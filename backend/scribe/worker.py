"""후처리(원본 만들기) 작업 프로세스.

서버가 `python -m scribe.worker <job_id>`로 별도 프로세스를 띄운다. MLX/Metal 메모리를 서버와
분리하고, 3~5시간 파일을 처리하는 동안에도 실시간 녹음이 영향을 덜 받게 하기 위함이다.
진행률은 SQLite jobs 테이블에 기록한다.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import signal
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from .audio import convert_to_wav, read_audio, wav_info
from .config import SAMPLE_RATE, settings
from .db import Store
from .engines import make_diarizer, make_engine, repos_for, resolve_diarizer_kind
from .engines.download import DownloadWatch
from .engines.base import split_windows
from .live_speakers import live_to_final_names
from .merge import build_utterances
from .storage import M4A

# 긴 녹음은 10분 안팎 창으로 나눠 받아쓴다. 메모리를 덜 쓰고, 창마다 결과를 저장해 두므로
# 중간에 멈추거나(취소, 이어서 녹음) 녹음이 뒤에 더 붙어도 끝난 창은 다시 하지 않는다.
WINDOW_S = float(os.environ.get("SCRIBE_WINDOW_S", "600"))
CKPT_VERSION = 2
# 이보다 긴 녹음은 화자 구분을 별도 프로세스에서 받아쓰기와 동시에 돌린다 (초)
PARALLEL_DIARIZE_MIN_S = float(os.environ.get("SCRIBE_PARALLEL_DIARIZE_MIN_S", "600"))

# 단계별 진행률 가중치
PHASES = {"convert": 0.05, "load": 0.03, "transcribe": 0.72, "diarize": 0.17, "merge": 0.03}
ORDER = ["convert", "load", "transcribe", "diarize", "merge"]


class Progress:
    def __init__(self, store: Store, job_id: str):
        self.store = store
        self.job_id = job_id
        self.started = time.time()
        self._last = 0.0
        self.notes: list[str] = []

    def set(self, phase: str, frac: float, message: str | None = None, force: bool = False) -> None:
        base = sum(PHASES[p] for p in ORDER[: ORDER.index(phase)])
        total = min(0.999, base + PHASES[phase] * max(0.0, min(1.0, frac)))
        now = time.time()
        if not force and now - self._last < 0.7:
            return
        self._last = now
        elapsed = now - self.started
        eta = elapsed / total * (1 - total) if total > 0.03 else None
        fields = {"phase": phase, "progress": round(total, 4), "eta": eta}
        if message is not None:
            fields["message"] = message
        self.store.update_job(self.job_id, **fields)


class Checkpoints:
    """창별 받아쓰기 결과와 화자 구분 결과를 meetings/<id>/ckpt/ 에 저장한다.
    모델·언어·용어집이 바뀌면 key가 달라져 모두 지운다."""

    def __init__(self, mdir: Path, key: str):
        self.dir = mdir / "ckpt"
        self.dir.mkdir(parents=True, exist_ok=True)
        meta = self.dir / "meta.json"
        try:
            old = json.loads(meta.read_text())["key"]
        except (OSError, ValueError, KeyError):
            old = None
        if old != key:
            for f in self.dir.iterdir():
                f.unlink()
            meta.write_text(json.dumps({"key": key}))

    def load(self, name: str):
        try:
            return json.loads((self.dir / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None

    def save(self, name: str, data) -> None:
        tmp = self.dir / f"{name}.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(self.dir / f"{name}.json")

    def drop_prefix(self, prefix: str, keep: str) -> None:
        for f in self.dir.glob(f"{prefix}*.json"):
            if f.stem != keep:
                f.unlink()


class _LazyAudio:
    """WAV를 통째로 읽지 않고 필요한 구간만 float로 꺼낸다 (창 나누기용)."""

    def __init__(self, wav: Path):
        info = wav_info(wav)
        self._mm = np.memmap(wav, dtype="<i2", mode="r", offset=info.data_offset, shape=(info.num_samples,))

    def __len__(self) -> int:
        return len(self._mm)

    def __getitem__(self, sl):
        return np.asarray(self._mm[sl], dtype=np.float32) / 32768.0


def plan_windows(wav: Path) -> list[tuple[int, int]]:
    # 앞부분이 같으면 같은 자리에서 자르므로, 녹음이 뒤에 붙어도 앞 창들은 그대로 쓸 수 있다
    return split_windows(_LazyAudio(wav), target_s=WINDOW_S, search_s=min(20.0, WINDOW_S / 10))  # type: ignore[arg-type]


def _shift(segs: list[dict], offset: float) -> list[dict]:
    for seg in segs:
        seg["start"] += offset
        seg["end"] += offset
        for w in seg.get("words") or []:
            w["start"] += offset
            w["end"] += offset
    return segs


def _diarize(wav: str, kind: str, num_speakers, on_progress=None) -> dict:
    diarizer = make_diarizer(kind)
    audio = read_audio(Path(wav))
    turns = diarizer.diarize(Path(wav), audio, num_speakers=num_speakers, on_progress=on_progress)
    return {"turns": turns, "embeddings": diarizer.embeddings}


def _diarize_child(conn, wav: str, kind: str, num_speakers) -> None:
    """별도 프로세스: 화자 구분 결과를 파이프로 돌려준다."""
    try:
        conn.send({"ok": True, **_diarize(wav, kind, num_speakers)})
    except Exception as exc:  # noqa: BLE001
        conn.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        conn.close()


class ParallelDiarize:
    """받아쓰기(GPU)와 화자 구분을 동시에 돌린다. 메모리도 프로세스별로 나뉜다."""

    def __init__(self, wav: Path, kind: str, num_speakers):
        ctx = multiprocessing.get_context("spawn")
        self.conn, child = ctx.Pipe(duplex=False)
        self.proc = ctx.Process(target=_diarize_child, args=(child, str(wav), kind, num_speakers), daemon=True)
        self.proc.start()
        child.close()

    def result(self, wait_cb=None) -> dict:
        while not self.conn.poll(1.0):
            if not self.proc.is_alive():
                raise RuntimeError(f"화자 구분 프로세스가 끝났어요 (코드 {self.proc.exitcode})")
            if wait_cb:
                wait_cb()
        res = self.conn.recv()
        self.proc.join(5)
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "화자 구분 실패")
        return res

    def stop(self) -> None:
        if self.proc.is_alive():
            self.proc.terminate()


def find_source(mdir: Path) -> Path | None:
    for p in sorted(mdir.glob("source.*")):
        return p
    return None


def run(job_id: str) -> int:
    settings.ensure_dirs()
    store = Store(settings.db_path)
    job = store.get_job(job_id)
    if job is None:
        print(f"job {job_id} not found", file=sys.stderr)
        return 2
    mid = job["meeting_id"]
    meeting = store.get_meeting(mid)
    if meeting is None:
        store.update_job(job_id, status="error", error="회의를 찾을 수 없습니다", finished_at=time.time())
        return 2
    store.update_job(job_id, status="running", started_at=time.time(), pid=os.getpid(), phase="convert", progress=0.0)
    store.update_meeting(mid, status="processing")
    prog = Progress(store, job_id)
    mdir = settings.meeting_dir(mid)
    wav = mdir / "audio.wav"
    work: Path | None = None  # m4a로 줄여 둔 회의를 다시 만들 때 쓰는 임시 WAV
    try:
        if not wav.exists() and (mdir / M4A).exists():
            # 줄여 둔 m4a가 기준이다. 다시 줄이며 음질이 떨어지지 않게 임시 WAV로만 푼다.
            work = mdir / "work.wav"
            prog.set("convert", 0.0, "오디오 여는 중", force=True)
            duration = convert_to_wav(mdir / M4A, work, lambda f: prog.set("convert", f))
            wav = work
        elif not wav.exists():
            src = find_source(mdir)
            if src is None:
                raise RuntimeError("처리할 오디오가 없습니다")
            prog.set("convert", 0.0, "오디오 변환 중", force=True)
            duration = convert_to_wav(src, wav, lambda f: prog.set("convert", f))
        else:
            duration = wav_info(wav).duration
        store.update_meeting(mid, duration=duration)

        context = store.get_setting("vocabulary", "") or ""
        key = hashlib.sha1(f"{CKPT_VERSION}|{describe_final()}|{settings.language}|{context}|{WINDOW_S}".encode()).hexdigest()
        ck = Checkpoints(mdir, key)
        windows = plan_windows(wav)
        total = max(1, windows[-1][1] if windows else 1)
        segments: list[dict] = []
        todo: list[tuple[int, int]] = []
        done = 0
        for a, b in windows:
            saved = ck.load(f"asr_{a}_{b}")
            if saved is None:
                todo.append((a, b))
            else:
                segments.extend(saved)
                done += b - a

        # 화자 구분: 저장해 둔 결과가 있으면 쓰고, 긴 녹음이면 받아쓰기와 동시에 시작한다
        diar_kind = resolve_diarizer_kind()
        diar_name = make_diarizer(diar_kind).describe()
        num_speakers = meeting.get("num_speakers")
        diar_key = f"diar_{diar_name}_{total}_{num_speakers or 0}".replace("/", "_").replace(":", "_")
        diar_saved = ck.load(diar_key)
        parallel = None
        if diar_saved is None and diar_kind != "none" and duration >= PARALLEL_DIARIZE_MIN_S:
            parallel = ParallelDiarize(wav, diar_kind, num_speakers)

        if todo:
            prog.set("load", 0.0, "모델 준비 중", force=True)
            watch = DownloadWatch(repos_for("final"))
            if watch.needed:
                # 처음 한 번은 모델을 내려받는다: 받은 양을 보여 준다
                def on_dl(p: dict | None) -> None:
                    if p:
                        prog.set("load", p["done"] / p["total"], f"모델 내려받는 중 ({p['done'] / 1024 ** 3:.1f}/{p['total'] / 1024 ** 3:.1f}GB, 처음 한 번만)", force=True)

                watch.start(on_dl, every=2.0)
            engine = make_engine("final")
            try:
                engine.load()
            finally:
                watch.stop()
            prog.set("load", 1.0, force=True)
            resumed = done > 0
            for i, (a, b) in enumerate(todo):
                msg = f"받아쓰는 중 ({i + 1}/{len(todo)}구간{', 이전 진행분 이어서' if resumed else ''})" if len(windows) > 1 else "받아쓰는 중"
                prog.set("transcribe", done / total, msg, force=True)
                audio = read_audio(wav, a / SAMPLE_RATE, b / SAMPLE_RATE)
                if hasattr(engine, "base_offset"):
                    engine.base_offset = a / SAMPLE_RATE  # 시연용 엔진이 대본 위치를 맞추는 데 쓴다
                segs = engine.transcribe_long(
                    audio, language=settings.language, context=context,
                    on_progress=lambda f, a=a, b=b: prog.set("transcribe", (done + f * (b - a)) / total),
                )
                del audio
                segs = _shift(segs, a / SAMPLE_RATE)
                ck.save(f"asr_{a}_{b}", segs)
                segments.extend(segs)
                done += b - a
            del engine
        print(f"windows={len(windows)} reused={len(windows) - len(todo)}", flush=True)
        segments.sort(key=lambda s: s["start"])
        # 녹음이 늘어 나뉜 자리가 바뀐 옛 창 결과는 지운다
        valid = {f"asr_{a}_{b}" for a, b in windows}
        for f in ck.dir.glob("asr_*.json"):
            if f.stem not in valid:
                f.unlink()

        turns: list[dict] = []
        embeddings = None
        if diar_saved is not None:
            turns, embeddings = diar_saved["turns"], diar_saved.get("embeddings")
        elif diar_kind != "none":
            try:
                if parallel is not None:
                    prog.set("diarize", 0.3, "화자 구분 마무리 중", force=True)
                    res = parallel.result()
                else:
                    prog.set("diarize", 0.0, "화자 구분 중", force=True)
                    res = _diarize(str(wav), diar_kind, num_speakers, on_progress=lambda f: prog.set("diarize", f))
                turns, embeddings = res["turns"], res.get("embeddings")
                ck.drop_prefix("diar_", keep=diar_key)
                ck.save(diar_key, {"turns": turns, "embeddings": embeddings})
            except Exception as exc:  # noqa: BLE001 - 화자 구분 실패해도 전사는 살린다
                diar_name = f"{diar_name} (실패: {type(exc).__name__})"
                prog.notes.append(f"화자 구분을 건너뛰었어요: {exc}")
            finally:
                if parallel is not None:
                    parallel.stop()

        prog.set("merge", 0.0, "정리 중", force=True)
        boundaries = [float(a["anchor"]["t"]) for a in store.list_annotations(mid) if a["kind"] == "divider" and "t" in a["anchor"]]
        data = build_utterances(segments, turns, boundaries)
        data["duration"] = duration
        data["language"] = settings.language
        mapping = data.pop("speaker_map", {})
        if embeddings:
            # 화자별 목소리 특징: 이름을 붙이면 다음 회의에서 같은 목소리에 이름을 제안한다
            data["speaker_embeddings"] = {mapping[raw]: vec for raw, vec in embeddings.items() if raw in mapping}
        parts = {p["id"]: p["name"] for p in meeting.get("participants") or []}
        from_live = live_to_final_names(data["utterances"], store.list_live_speakers(mid), parts) if parts else {}
        if from_live:
            data["speaker_from_live"] = from_live
        version = store.save_final_version(mid, data, engine=describe_final(), diarizer=diar_name)

        speakers = dict(meeting.get("speakers") or {})
        for label in data["speakers"]:
            speakers.setdefault(label, "")
        # 회의 중 고른 화자를 원본 화자 이름으로 (사용자가 원본에서 직접 붙인 이름은 그대로)
        named = {v for v in speakers.values() if v}
        for label, hit in from_live.items():
            if hit["auto"] and not speakers.get(label) and hit["name"] not in named:
                speakers[label] = hit["name"]
                named.add(hit["name"])
        store.update_meeting(mid, status="ready", speakers=speakers)
        msg = "완료" + (" · " + " ".join(prog.notes) if prog.notes else "")
        store.update_job(job_id, status="done", phase="done", progress=1.0, message=msg, finished_at=time.time(), eta=0)
        if meeting.get("source") == "upload":
            # 올린 파일의 사본은 지운다 (원래 파일은 사용자에게 있고, 재생용 WAV는 남는다)
            for src in mdir.glob("source.*"):
                src.unlink(missing_ok=True)
        if work is not None:
            work.unlink(missing_ok=True)
        print(f"done meeting={mid} version={version} utterances={len(data['utterances'])}")
        return 0
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        if work is not None:
            work.unlink(missing_ok=True)
        store.update_job(job_id, status="error", error=f"{type(exc).__name__}: {exc}", message=tb[-2000:], finished_at=time.time())
        has_final = store.get_final(mid) is not None
        store.update_meeting(mid, status="ready" if has_final else "error")
        print(tb, file=sys.stderr)
        return 1


def describe_final() -> str:
    from .engines import model_for

    kind, model = model_for("final")
    return f"{kind}:{model}"


if __name__ == "__main__":
    # 취소(SIGTERM)되면 정상 종료 경로로 빠져나가 화자 구분 프로세스도 함께 끝낸다
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(143))
    sys.exit(run(sys.argv[1]))

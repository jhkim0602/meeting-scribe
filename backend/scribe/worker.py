"""후처리(원본 만들기) 작업 프로세스.

서버가 `python -m scribe.worker <job_id>`로 별도 프로세스를 띄운다. MLX/Metal 메모리를 서버와
분리하고, 3~5시간 파일을 처리하는 동안에도 실시간 녹음이 영향을 덜 받게 하기 위함이다.
진행률은 SQLite jobs 테이블에 기록한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from .audio import convert_to_wav, read_audio, wav_info
from .config import SAMPLE_RATE, settings
from .db import Store
from .engines import make_diarizer, make_engine
from .engines.base import split_windows
from .merge import build_utterances

# 긴 녹음은 10분 안팎 창으로 나눠 받아쓴다. 메모리를 덜 쓰고, 창마다 결과를 저장해 두므로
# 중간에 멈추거나(취소, 이어서 녹음) 녹음이 뒤에 더 붙어도 끝난 창은 다시 하지 않는다.
WINDOW_S = float(os.environ.get("SCRIBE_WINDOW_S", "600"))
CKPT_VERSION = 1

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
    try:
        if not wav.exists():
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

        if todo:
            prog.set("load", 0.0, "모델 준비 중 (처음에는 내려받기 때문에 오래 걸릴 수 있어요)", force=True)
            engine = make_engine("final")
            engine.load()
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

        prog.set("diarize", 0.0, "화자 구분 중", force=True)
        diarizer = make_diarizer()
        turns: list[dict] = []
        diar_name = diarizer.describe()
        diar_key = f"diar_{diar_name}_{total}_{meeting.get('num_speakers') or 0}".replace("/", "_").replace(":", "_")
        saved_turns = ck.load(diar_key)
        if saved_turns is not None:
            turns = saved_turns
        else:
            try:
                audio = read_audio(wav)
                turns = diarizer.diarize(wav, audio, num_speakers=meeting.get("num_speakers"), on_progress=lambda f: prog.set("diarize", f))
                del audio
                ck.drop_prefix("diar_", keep=diar_key)
                ck.save(diar_key, turns)
            except Exception as exc:  # noqa: BLE001 - 화자 구분 실패해도 전사는 살린다
                diar_name = f"{diar_name} (실패: {type(exc).__name__})"
                prog.notes.append(f"화자 구분을 건너뛰었어요: {exc}")

        prog.set("merge", 0.0, "정리 중", force=True)
        boundaries = [float(a["anchor"]["t"]) for a in store.list_annotations(mid) if a["kind"] == "divider" and "t" in a["anchor"]]
        data = build_utterances(segments, turns, boundaries)
        data["duration"] = duration
        data["language"] = settings.language
        version = store.save_final_version(mid, data, engine=describe_final(), diarizer=diar_name)

        speakers = dict(meeting.get("speakers") or {})
        for label in data["speakers"]:
            speakers.setdefault(label, "")
        store.update_meeting(mid, status="ready", speakers=speakers)
        msg = "완료" + (" · " + " ".join(prog.notes) if prog.notes else "")
        store.update_job(job_id, status="done", phase="done", progress=1.0, message=msg, finished_at=time.time(), eta=0)
        print(f"done meeting={mid} version={version} utterances={len(data['utterances'])}")
        return 0
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        store.update_job(job_id, status="error", error=f"{type(exc).__name__}: {exc}", message=tb[-2000:], finished_at=time.time())
        has_final = store.get_final(mid) is not None
        store.update_meeting(mid, status="ready" if has_final else "error")
        print(tb, file=sys.stderr)
        return 1


def describe_final() -> str:
    from .engines import DEFAULT_MODELS, resolve_engine_kind

    kind = resolve_engine_kind(settings.final_engine)
    return f"{kind}:{settings.final_model or DEFAULT_MODELS.get(kind, {}).get('final', '')}"


if __name__ == "__main__":
    sys.exit(run(sys.argv[1]))

"""후처리(원본 만들기) 작업 프로세스.

서버가 `python -m scribe.worker <job_id>`로 별도 프로세스를 띄운다. MLX/Metal 메모리를 서버와
분리하고, 3~5시간 파일을 처리하는 동안에도 실시간 녹음이 영향을 덜 받게 하기 위함이다.
진행률은 SQLite jobs 테이블에 기록한다.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from .audio import convert_to_wav, read_audio, wav_info
from .config import settings
from .db import Store
from .engines import make_diarizer, make_engine
from .merge import build_utterances

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

        prog.set("load", 0.0, "모델 준비 중 (처음에는 내려받기 때문에 오래 걸릴 수 있어요)", force=True)
        engine = make_engine("final")
        engine.load()
        diarizer = make_diarizer()
        prog.set("load", 1.0, force=True)

        audio = read_audio(wav)
        context = store.get_setting("vocabulary", "") or ""
        prog.set("transcribe", 0.0, "받아쓰는 중", force=True)
        segments = engine.transcribe_long(audio, language=settings.language, context=context, on_progress=lambda f: prog.set("transcribe", f))
        del engine

        prog.set("diarize", 0.0, "화자 구분 중", force=True)
        turns: list[dict] = []
        diar_name = diarizer.describe()
        try:
            turns = diarizer.diarize(wav, audio, num_speakers=meeting.get("num_speakers"), on_progress=lambda f: prog.set("diarize", f))
        except Exception as exc:  # noqa: BLE001 - 화자 구분 실패해도 전사는 살린다
            diar_name = f"{diar_name} (실패: {type(exc).__name__})"
            prog.notes.append(f"화자 구분을 건너뛰었어요: {exc}")

        prog.set("merge", 0.0, "정리 중", force=True)
        data = build_utterances(segments, turns)
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

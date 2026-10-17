"""엔진 선택. SCRIBE_*_ENGINE=auto면 설치된 패키지를 보고 가장 좋은 것을 고른다."""

from __future__ import annotations

import importlib.util
import os
import threading
from typing import Optional

from ..config import is_apple_silicon, settings
from .base import AsrEngine, Diarizer, NoDiarizer

DEFAULT_MODELS = {
    "qwen3": {"live": "Qwen/Qwen3-ASR-0.6B", "final": "Qwen/Qwen3-ASR-1.7B"},
    "whisper": {"live": "mlx-community/whisper-large-v3-turbo", "final": "mlx-community/whisper-large-v3-turbo"},
    "faster-whisper": {"live": "small", "final": "large-v3-turbo"},
    "mock": {"live": "script", "final": "script"},
}


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def resolve_engine_kind(requested: str) -> str:
    if requested and requested != "auto":
        return requested
    if is_apple_silicon() and _has("mlx_qwen3_asr"):
        return "qwen3"
    if is_apple_silicon() and _has("mlx_whisper"):
        return "whisper"
    if _has("faster_whisper"):
        return "faster-whisper"
    return "mock"


def make_engine(role: str) -> AsrEngine:
    """role: 'live' | 'final'"""
    requested = settings.live_engine if role == "live" else settings.final_engine
    kind = resolve_engine_kind(requested)
    model = (settings.live_model if role == "live" else settings.final_model) or DEFAULT_MODELS.get(kind, {}).get(role, "")
    if kind == "qwen3":
        from .qwen3_mlx import Qwen3MlxEngine

        return Qwen3MlxEngine(model)
    if kind == "whisper":
        from .whisper_mlx import WhisperMlxEngine

        return WhisperMlxEngine(model)
    if kind == "faster-whisper":
        from .faster_whisper_engine import FasterWhisperEngine

        return FasterWhisperEngine(model)
    if kind == "mock":
        from .mock import MockAsr

        return MockAsr(model, final=(role == "final"))
    raise ValueError(f"알 수 없는 엔진: {kind}")


def resolve_diarizer_kind(requested: Optional[str] = None) -> str:
    requested = requested or settings.diarizer
    if requested and requested != "auto":
        return requested
    if _has("speakrs"):
        return "speakrs"
    if (_has("pyannote.audio") or _has("pyannote")) and (settings.hf_token or os.environ.get("HF_TOKEN") or _pyannote_cached()):
        return "pyannote"
    return "none"


def _pyannote_cached() -> bool:
    from pathlib import Path

    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return (hub / "models--pyannote--speaker-diarization-community-1").exists()


def make_diarizer(kind: Optional[str] = None) -> Diarizer:
    kind = resolve_diarizer_kind(kind)
    if kind == "speakrs":
        from .diarizers import SpeakrsDiarizer

        return SpeakrsDiarizer()
    if kind == "pyannote":
        from .diarizers import PyannoteDiarizer

        return PyannoteDiarizer()
    if kind == "mock":
        from .mock import MockDiarizer

        return MockDiarizer()
    return NoDiarizer()


class LiveEngineHolder:
    """서버 프로세스에서 실시간 엔진을 한 번만 올리고 상태를 알려준다."""

    def __init__(self) -> None:
        self.engine: Optional[AsrEngine] = None
        self.state = "idle"  # idle | loading | ready | error
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        self._ready = threading.Event()

    def start_loading(self) -> None:
        with self._lock:
            if self.state in ("loading", "ready"):
                return
            self.state = "loading"
            self.error = None
        threading.Thread(target=self._load, name="live-engine-load", daemon=True).start()

    def _load(self) -> None:
        try:
            engine = make_engine("live")
            engine.load()
            self.engine = engine
            self.state = "ready"
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "error"
        finally:
            self._ready.set()

    def wait(self, timeout: Optional[float] = None) -> Optional[AsrEngine]:
        self._ready.wait(timeout)
        return self.engine

    def info(self) -> dict:
        kind = resolve_engine_kind(settings.live_engine)
        model = settings.live_model or DEFAULT_MODELS.get(kind, {}).get("live", "")
        return {"kind": kind, "model": model, "state": self.state, "error": self.error}


def engines_info() -> dict:
    fk = resolve_engine_kind(settings.final_engine)
    return {
        "final": {"kind": fk, "model": settings.final_model or DEFAULT_MODELS.get(fk, {}).get("final", "")},
        "diarizer": resolve_diarizer_kind(),
        "apple_silicon": is_apple_silicon(),
        "installed": {m: _has(m) for m in ("mlx_qwen3_asr", "mlx_whisper", "faster_whisper", "speakrs", "pyannote.audio")},
    }

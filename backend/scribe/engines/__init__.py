"""엔진 선택. SCRIBE_*_ENGINE=auto면 설치된 패키지를 보고 가장 좋은 것을 고른다."""

from __future__ import annotations

import gc
import importlib.util
import os
import threading
import time
from typing import Optional

from ..config import is_apple_silicon, settings
from .base import AsrEngine, Diarizer, NoDiarizer
from .download import DownloadWatch

# Qwen3-ASR: 8bit는 mlx-qwen3-asr가 검증해 올린 양자화 모델(fp16과 같은 결과, 크기 절반 이하)
QWEN3_MODELS = {
    "8bit": {"live": "moona3k/mlx-qwen3-asr-0.6b-8bit", "final": "moona3k/mlx-qwen3-asr-1.7b-8bit"},
    "fp16": {"live": "Qwen/Qwen3-ASR-0.6B", "final": "Qwen/Qwen3-ASR-1.7B"},
}
DEFAULT_MODELS = {
    "qwen3": QWEN3_MODELS["fp16"],
    "whisper": {"live": "mlx-community/whisper-large-v3-turbo", "final": "mlx-community/whisper-large-v3-turbo"},
    "faster-whisper": {"live": "small", "final": "large-v3-turbo"},
    "mock": {"live": "script", "final": "script"},
}
ALIGNER_ID = "Qwen/Qwen3-ForcedAligner-0.6B"


def default_model(kind: str, role: str) -> str:
    if kind == "qwen3":
        return QWEN3_MODELS.get(settings.precision, QWEN3_MODELS["8bit"])[role]
    return DEFAULT_MODELS.get(kind, {}).get(role, "")


def model_for(role: str) -> tuple[str, str]:
    """(엔진 종류, 모델 id)"""
    kind = resolve_engine_kind(settings.live_engine if role == "live" else settings.final_engine)
    model = (settings.live_model if role == "live" else settings.final_model) or default_model(kind, role)
    return kind, model


def repos_for(role: str) -> list[str]:
    """이 역할을 위해 내려받는 Hugging Face 저장소들 (진행률 표시용)"""
    kind, model = model_for(role)
    if kind not in ("qwen3", "whisper") or not model or os.path.isdir(model):
        return []
    return [model, ALIGNER_ID] if kind == "qwen3" and role == "final" else [model]


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
    kind, model = model_for(role)
    if kind == "qwen3":
        from .qwen3_mlx import Qwen3MlxEngine

        # 원본 스크립트용은 단어 정렬기까지 미리 올려 내려받기 진행률에 함께 잡히게 한다
        return Qwen3MlxEngine(model, with_aligner=(role == "final"))
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
    """서버 프로세스에서 실시간 엔진을 한 번만 올리고 상태를 알려준다.
    녹음이 없는 채로 오래 지나면 메모리에서 내리고, 다음 녹음이나 앱을 다시 볼 때 올린다."""

    def __init__(self) -> None:
        self.engine: Optional[AsrEngine] = None
        self.state = "idle"  # idle | loading | ready | error
        self.error: Optional[str] = None
        self.last_used = time.time()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._watch: Optional[DownloadWatch] = None

    def start_loading(self) -> None:
        with self._lock:
            self.last_used = time.time()
            if self.state in ("loading", "ready"):
                return
            self.state = "loading"
            self.error = None
            self._ready.clear()
        threading.Thread(target=self._load, name="live-engine-load", daemon=True).start()

    def _load(self) -> None:
        watch = DownloadWatch(repos_for("live"))
        self._watch = watch if watch.needed else None
        try:
            engine = make_engine("live")
            if self._watch:
                self._watch.start()
            engine.load()
            self.engine = engine
            self.state = "ready"
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "error"
        finally:
            if self._watch:
                self._watch.stop()
            self._watch = None
            self._ready.set()

    def mark_used(self) -> None:
        self.last_used = time.time()

    def unload_if_idle(self, busy: bool) -> bool:
        """녹음이 없고 오래 쓰지 않았으면 모델을 내린다."""
        limit = settings.live_idle_unload_min * 60
        with self._lock:
            if busy or limit <= 0 or self.state != "ready" or time.time() - self.last_used < limit:
                return False
            self.engine = None
            self.state = "idle"
            self._ready.clear()
        gc.collect()
        try:
            import mlx.core as mx  # type: ignore

            (getattr(mx, "clear_cache", None) or mx.metal.clear_cache)()
        except Exception:  # noqa: BLE001 - MLX가 없거나 이전 버전
            pass
        return True

    def wait(self, timeout: Optional[float] = None) -> Optional[AsrEngine]:
        self._ready.wait(timeout)
        return self.engine

    def info(self) -> dict:
        kind, model = model_for("live")
        out = {"kind": kind, "model": model, "state": self.state, "error": self.error}
        if self._watch:
            out["download"] = self._watch.progress()
        return out


def engines_info() -> dict:
    fk, fmodel = model_for("final")
    return {
        "final": {"kind": fk, "model": fmodel},
        "precision": settings.precision,
        "diarizer": resolve_diarizer_kind(),
        "apple_silicon": is_apple_silicon(),
        "installed": {m: _has(m) for m in ("mlx_qwen3_asr", "mlx_whisper", "faster_whisper", "speakrs", "pyannote.audio")},
    }

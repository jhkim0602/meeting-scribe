"""Whisper (Apple Silicon, MLX) — mlx-whisper 패키지 사용. 대안 엔진."""

from __future__ import annotations

import threading

import numpy as np

from ..config import SAMPLE_RATE
from .base import AsrEngine, looks_like_hallucination


class WhisperMlxEngine(AsrEngine):
    name = "whisper-mlx"

    def __init__(self, model: str):
        super().__init__(model)
        self._lock = threading.Lock()

    def load(self) -> None:
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="ko")

    def _run(self, audio: np.ndarray, language: str, context: str, words: bool) -> dict:
        import mlx_whisper

        with self._lock:
            return mlx_whisper.transcribe(
                audio.astype(np.float32),
                path_or_hf_repo=self.model,
                language=language or None,
                initial_prompt=context or None,
                word_timestamps=words,
                condition_on_previous_text=False,
                temperature=(0.0, 0.2, 0.4),
                no_speech_threshold=0.6,
                hallucination_silence_threshold=2.0 if words else None,
                verbose=None,
            )

    def transcribe(self, audio: np.ndarray, *, language: str, context: str = "") -> str:
        if len(audio) == 0:
            return ""
        text = (self._run(audio, language, context, False).get("text") or "").strip()
        return "" if looks_like_hallucination(text) else text

    def _transcribe_window(self, audio: np.ndarray, *, language: str, context: str) -> list[dict]:
        result = self._run(audio, language, context, True)
        out = []
        for seg in result.get("segments") or []:
            text = (seg.get("text") or "").strip()
            if not text or looks_like_hallucination(text):
                continue
            words = [
                {"text": (w.get("word") or "").strip(), "start": float(w["start"]), "end": float(w["end"])}
                for w in seg.get("words") or []
            ]
            out.append({"text": text, "start": float(seg["start"]), "end": float(seg["end"]), "words": words})
        return out

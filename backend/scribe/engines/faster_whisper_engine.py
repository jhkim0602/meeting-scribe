"""faster-whisper (CPU/CUDA). 맥이 아닌 환경에서 쓰는 대안 엔진."""

from __future__ import annotations

import threading

import numpy as np

from .base import AsrEngine, looks_like_hallucination


class FasterWhisperEngine(AsrEngine):
    name = "faster-whisper"

    def __init__(self, model: str):
        super().__init__(model)
        self._model = None
        self._lock = threading.Lock()

    def load(self) -> None:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model, device="auto", compute_type="int8")

    def transcribe(self, audio: np.ndarray, *, language: str, context: str = "") -> str:
        self.load()
        with self._lock:
            segments, _ = self._model.transcribe(
                audio.astype(np.float32), language=language or None, initial_prompt=context or None,
                condition_on_previous_text=False, beam_size=1,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        return "" if looks_like_hallucination(text) else text

    def _transcribe_window(self, audio: np.ndarray, *, language: str, context: str) -> list[dict]:
        self.load()
        out = []
        with self._lock:
            segments, _ = self._model.transcribe(
                audio.astype(np.float32), language=language or None, initial_prompt=context or None,
                word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
            )
            for s in segments:
                text = s.text.strip()
                if not text or looks_like_hallucination(text):
                    continue
                words = [{"text": w.word.strip(), "start": float(w.start), "end": float(w.end)} for w in (s.words or [])]
                out.append({"text": text, "start": float(s.start), "end": float(s.end), "words": words})
        return out

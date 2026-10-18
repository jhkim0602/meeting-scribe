"""Qwen3-ASR (Apple Silicon, MLX) — mlx-qwen3-asr 패키지 사용.

실시간: Qwen/Qwen3-ASR-0.6B, 원본 스크립트: Qwen/Qwen3-ASR-1.7B + Qwen3-ForcedAligner(단어 타임스탬프).
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from .base import AsrEngine, ProgressFn

ALIGNER_ID = "Qwen/Qwen3-ForcedAligner-0.6B"


class Qwen3MlxEngine(AsrEngine):
    name = "qwen3-mlx"

    def __init__(self, model: str, aligner: str = ALIGNER_ID, with_aligner: bool = False):
        super().__init__(model)
        self.aligner_id = aligner
        self.with_aligner = with_aligner
        self._aligner = None
        self._lock = threading.Lock()  # MLX 호출은 한 번에 하나씩

    def load(self) -> None:
        from mlx_qwen3_asr import ForcedAligner, transcribe

        with self._lock:
            # 모델/토크나이저를 내려받고 캐시에 올린다 (1초 무음으로 워밍업)
            transcribe(np.zeros(16000, dtype=np.float32), model=self.model, language="ko")
            if self.with_aligner and self._aligner is None:
                self._aligner = ForcedAligner(self.aligner_id)

    def transcribe(self, audio: np.ndarray, *, language: str, context: str = "") -> str:
        from mlx_qwen3_asr import transcribe

        if len(audio) == 0:
            return ""
        with self._lock:
            result = transcribe(audio.astype(np.float32), model=self.model, language=language or None, context=context or "")
        return (result.text or "").strip()

    def transcribe_long(self, audio: np.ndarray, *, language: str, context: str = "", on_progress: Optional[ProgressFn] = None) -> list[dict]:
        from mlx_qwen3_asr import ForcedAligner, transcribe

        if self._aligner is None:
            self._aligner = ForcedAligner(self.aligner_id)

        def _cb(event: dict) -> None:
            if on_progress and "progress" in event:
                on_progress(float(event["progress"]))

        with self._lock:
            result = transcribe(
                audio.astype(np.float32),
                model=self.model,
                language=language or None,
                context=context or "",
                return_timestamps=True,
                return_chunks=True,
                forced_aligner=self._aligner,
                on_progress=_cb,
            )
        chunks = result.chunks or [{"text": result.text, "start": 0.0, "end": len(audio) / 16000}]
        words = result.segments or []
        out: list[dict] = []
        wi = 0
        for ci, ch in enumerate(chunks):
            start, end = float(ch["start"]), float(ch["end"])
            last = ci == len(chunks) - 1
            cw = []
            while wi < len(words) and (last or float(words[wi]["start"]) < end - 1e-3):
                w = words[wi]
                cw.append({"text": str(w["text"]), "start": float(w["start"]), "end": float(w["end"])})
                wi += 1
            out.append({"text": (ch.get("text") or "").strip(), "start": start, "end": end, "words": cw})
        return out

"""화자 분리 구현: speakrs(CoreML, 가장 빠름) → pyannote community-1 → 없음."""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..config import SAMPLE_RATE, is_apple_silicon, settings
from .base import Diarizer, ProgressFn


def parse_rttm(text: str) -> list[dict]:
    turns = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[0] == "SPEAKER":
            start, dur = float(parts[3]), float(parts[4])
            turns.append({"speaker": parts[7], "start": start, "end": start + dur})
    return turns


class SpeakrsDiarizer(Diarizer):
    name = "speakrs"
    needs_audio = False  # 파일을 직접 읽는다

    def diarize(self, wav_path, audio: np.ndarray, *, num_speakers: Optional[int] = None, on_progress: Optional[ProgressFn] = None) -> list[dict]:
        import speakrs  # type: ignore

        backend = "coreml" if is_apple_silicon() else "cpu"
        pipeline = speakrs.Pipeline.from_pretrained(backend)
        result = pipeline.diarize_file(str(wav_path))
        rttm = result.rttm() if hasattr(result, "rttm") else str(result)
        if on_progress:
            on_progress(1.0)
        return parse_rttm(rttm)


class PyannoteDiarizer(Diarizer):
    name = "pyannote-community-1"

    def diarize(self, wav_path, audio: np.ndarray, *, num_speakers: Optional[int] = None, on_progress: Optional[ProgressFn] = None) -> list[dict]:
        if settings.hf_token:
            import os

            os.environ.setdefault("HF_TOKEN", settings.hf_token)
        try:
            from mlx_qwen3_asr.diarization import DiarizationConfig, infer_speaker_turns

            cfg = DiarizationConfig(num_speakers=num_speakers, min_speakers=1, max_speakers=10, device="auto")
            turns = infer_speaker_turns(np.asarray(audio, dtype=np.float32), sr=SAMPLE_RATE, config=cfg)  # 이미 float32면 복사하지 않는다
        except ImportError:
            turns = self._direct(audio, num_speakers)
        if on_progress:
            on_progress(1.0)
        return [{"speaker": str(t["speaker"]), "start": float(t["start"]), "end": float(t["end"])} for t in turns]

    def _direct(self, audio: np.ndarray, num_speakers: Optional[int]) -> list[dict]:
        import torch
        from pyannote.audio import Pipeline

        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=settings.hf_token or None)
        if torch.backends.mps.is_available():
            pipeline.to(torch.device("mps"))
        kwargs = {"num_speakers": num_speakers} if num_speakers else {}
        output = pipeline({"waveform": torch.from_numpy(audio[None, :]), "sample_rate": SAMPLE_RATE}, **kwargs)
        annotation = getattr(output, "exclusive_speaker_diarization", None) or getattr(output, "speaker_diarization", output)
        return [{"speaker": spk, "start": seg.start, "end": seg.end} for seg, _, spk in annotation.itertracks(yield_label=True)]



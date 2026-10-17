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
        self.embeddings = None
        try:
            from mlx_qwen3_asr.diarization import DiarizationConfig, infer_speaker_turns

            cfg = DiarizationConfig(num_speakers=num_speakers, min_speakers=1, max_speakers=10, device="auto")
            capture = _capturing_pipeline(cfg.device)
            turns = None
            if capture is not None:
                try:
                    turns = infer_speaker_turns(audio.astype(np.float32), sr=SAMPLE_RATE, config=cfg, _pipeline=capture)
                    self.embeddings = _embeddings_by_turn_label(capture.output)
                except Exception:  # noqa: BLE001 - 가속기 문제 등: 아래에서 패키지 기본 경로(CPU 재시도 포함)로
                    turns = None
            if turns is None:
                turns = infer_speaker_turns(audio.astype(np.float32), sr=SAMPLE_RATE, config=cfg)
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
        self.embeddings = _embeddings_by_turn_label(output, rename=False)
        return [{"speaker": spk, "start": seg.start, "end": seg.end} for seg, _, spk in annotation.itertracks(yield_label=True)]


class _Capture:
    """pyannote 파이프라인을 감싸 결과(목소리 특징 포함)를 붙잡아 둔다."""

    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.output = None
        self.device = getattr(pipeline, "device", "cpu")

    def __call__(self, *args, **kwargs):
        self.output = self.pipeline(*args, **kwargs)
        return self.output


def _capturing_pipeline(device: str):
    try:
        from mlx_qwen3_asr.diarization import _load_pyannote_pipeline  # type: ignore[attr-defined]

        return _Capture(_load_pyannote_pipeline(device=device))
    except Exception:  # noqa: BLE001 - 내부 함수가 바뀌었으면 목소리 특징 없이 진행
        return None


def _embeddings_by_turn_label(output, rename: bool = True) -> Optional[dict]:
    """pyannote 4 출력의 speaker_embeddings를 화자 라벨별로 묶는다.
    rename=True면 mlx-qwen3-asr처럼 처음 말한 순서대로 SPEAKER_00, 01 …로 바꾼 라벨을 쓴다."""
    emb = getattr(output, "speaker_embeddings", None)
    regular = getattr(output, "speaker_diarization", None)
    if emb is None or regular is None:
        return None
    labels = list(regular.labels())
    raw: list = []
    for cand in (getattr(output, "exclusive_speaker_diarization", None), regular):
        if cand is not None:
            raw = sorted(((seg.start, seg.end, lab) for seg, _, lab in cand.itertracks(yield_label=True)), key=lambda x: (x[0], x[1]))
            if raw:
                break
    order: dict = {}
    for _, _, lab in raw:
        if lab not in order:
            order[lab] = f"SPEAKER_{len(order):02d}" if rename else str(lab)
    out = {}
    for lab, name in order.items():
        if lab in labels:
            v = np.asarray(emb[labels.index(lab)], dtype=float)
            if v.size and np.all(np.isfinite(v)):
                out[name] = [round(float(x), 5) for x in v]
    return out or None


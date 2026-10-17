"""엔진 인터페이스.

- AsrEngine.transcribe(): 짧은 구간(실시간용) → 텍스트
- AsrEngine.transcribe_long(): 긴 오디오(후처리용) → [{text, start, end, words:[{text,start,end}]}]
- Diarizer.diarize(): → [{speaker, start, end}]
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np

from ..config import SAMPLE_RATE

ProgressFn = Callable[[float], None]


class AsrEngine:
    name = "base"

    def __init__(self, model: str):
        self.model = model

    def describe(self) -> str:
        return f"{self.name}:{self.model}"

    def load(self) -> None:
        """모델을 미리 메모리에 올린다 (처음에는 내려받기 포함)."""

    def transcribe(self, audio: np.ndarray, *, language: str, context: str = "") -> str:
        raise NotImplementedError

    def transcribe_long(self, audio: np.ndarray, *, language: str, context: str = "", on_progress: Optional[ProgressFn] = None) -> list[dict]:
        """기본 구현: 10분 안팎 창으로 잘라 _transcribe_window()를 반복 호출한다."""
        windows = split_windows(audio, target_s=600.0)
        total = max(1, len(audio))
        out: list[dict] = []
        done = 0
        for start, end in windows:
            segs = self._transcribe_window(audio[start:end], language=language, context=context)
            offset = start / SAMPLE_RATE
            for seg in segs:
                seg["start"] = seg["start"] + offset
                seg["end"] = seg["end"] + offset
                for w in seg.get("words") or []:
                    w["start"] = w["start"] + offset
                    w["end"] = w["end"] + offset
                out.append(seg)
            done += end - start
            if on_progress:
                on_progress(done / total)
        return out

    def _transcribe_window(self, audio: np.ndarray, *, language: str, context: str) -> list[dict]:
        raise NotImplementedError


class Diarizer:
    name = "none"
    # diarize() 뒤에 채워지는 화자별 목소리 특징 {라벨: 벡터} (지원할 때만)
    embeddings: Optional[dict] = None

    def describe(self) -> str:
        return self.name

    def diarize(self, wav_path, audio: np.ndarray, *, num_speakers: Optional[int] = None, on_progress: Optional[ProgressFn] = None) -> list[dict]:
        return []


class NoDiarizer(Diarizer):
    name = "none"


def frame_db(audio: np.ndarray, frame: int = 480) -> np.ndarray:
    n = len(audio) // frame
    if n == 0:
        return np.zeros(0)
    x = audio[: n * frame].reshape(n, frame)
    rms = np.sqrt(np.mean(x * x, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def split_windows(audio: np.ndarray, target_s: float = 600.0, search_s: float = 20.0) -> list[tuple[int, int]]:
    """target_s 근처의 가장 조용한 지점에서 자른 (start, end) 표본 구간 목록."""
    n = len(audio)
    target = int(target_s * SAMPLE_RATE)
    if n <= target * 1.2:
        return [(0, n)] if n else []
    out = []
    pos = 0
    search = int(search_s * SAMPLE_RATE)
    frame = 480
    while pos < n:
        if n - pos <= target * 1.2:
            out.append((pos, n))
            break
        lo = pos + target - search
        hi = min(n, pos + target + search)
        db = frame_db(audio[lo:hi], frame)
        cut = lo + int(np.argmin(db)) * frame + frame // 2 if len(db) else pos + target
        out.append((pos, cut))
        pos = cut
    return out


KO_HALLUCINATIONS = (
    "시청해주셔서감사합니다",
    "구독과좋아요",
    "구독좋아요",
    "mbc뉴스",
    "자막제공",
    "다음영상에서",
    "끝까지시청",
)


def looks_like_hallucination(text: str) -> bool:
    from ..textutil import normalize

    t = normalize(text)
    return any(h in t for h in KO_HALLUCINATIONS)

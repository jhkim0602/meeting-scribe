"""개발/시연용 가짜 엔진. 실제 모델 없이 전체 흐름(실시간 → 원본 → 첨삭 재배치)을 확인할 수 있다.

같은 오디오 시간에는 실시간과 원본이 같은 대본 위치를 가리키되, 원본은 숫자/띄어쓰기/문장부호가
조금 다르게 나오도록 만들어 실제 ASR 버전 차이를 흉내 낸다.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..config import SAMPLE_RATE
from .base import AsrEngine, Diarizer, frame_db

SCRIPT = (
    "오늘 회의는 삼분기 예산 검토부터 시작하겠습니다 지난달 마케팅 비용이 계획보다 이십 퍼센트 넘게 나왔어요 "
    "광고 채널별로 보면 검색 광고 효율이 제일 좋았고 영상 광고는 기대보다 낮았습니다 "
    "그러면 영상 광고 예산을 줄이고 검색 쪽으로 옮기는 건 어떨까요 "
    "좋은 생각인데 브랜드 인지도 지표도 같이 봐야 할 것 같아요 다음 주까지 채널별 데이터를 정리해 주실 수 있을까요 "
    "네 김 팀장님이 정리해서 공유하겠습니다 그리고 신규 서비스 출시 일정도 확인해야 합니다 "
    "개발 쪽에서는 십일월 중순 베타를 목표로 하고 있고 결제 모듈 테스트가 아직 남아 있어요 "
    "결제 테스트는 보안 검토랑 같이 진행하면 일정이 줄어들 것 같습니다 "
    "알겠습니다 그럼 이번 주 금요일에 보안팀과 미팅을 잡겠습니다 마지막으로 채용 건인데요 "
    "디자이너 한 명과 백엔드 개발자 두 명을 연말까지 뽑는 걸로 정리하겠습니다 "
)
CHARS_PER_SEC = 7.0

FINAL_VARIANTS = [
    ("삼분기", "3분기"), ("이십 퍼센트", "20%"), ("김 팀장님이", "김팀장님이"), ("십일월", "11월"),
    ("시작하겠습니다", "시작하겠습니다."), ("낮았습니다", "낮았습니다."), ("어떨까요", "어떨까요?"),
    ("있을까요", "있을까요?"), ("공유하겠습니다", "공유하겠습니다."), ("남아 있어요", "남아 있어요."),
    ("같습니다", "같습니다."), ("잡겠습니다", "잡겠습니다."), ("정리하겠습니다", "정리하겠습니다."),
]


def _script_slice(t0: float, t1: float) -> str:
    n = len(SCRIPT)
    a = int(t0 * CHARS_PER_SEC)
    b = int(t1 * CHARS_PER_SEC)
    text = "".join(SCRIPT[i % n] for i in range(a, max(a, b)))
    # 단어 경계로 정리
    words = text.split(" ")
    if len(words) > 2:
        words = words[1:-1] if a % n != 0 else words[:-1]
    return " ".join(w for w in words if w).strip()


def _has_sound(audio: np.ndarray) -> bool:
    db = frame_db(audio)
    return bool(len(db)) and float(np.percentile(db, 90)) > -55


class MockAsr(AsrEngine):
    name = "mock"

    def __init__(self, model: str = "script", final: bool = False):
        super().__init__(model)
        self.final = final
        self.offset_hint = 0.0  # 실시간 엔진은 절대 시간을 알아야 대본 위치를 맞출 수 있다
        self.base_offset = 0.0  # 원본을 창별로 만들 때 이 창이 파일의 몇 초부터인지
        self._window_offset = 0.0

    def transcribe(self, audio: np.ndarray, *, language: str, context: str = "", t0: Optional[float] = None) -> str:
        if not _has_sound(audio):
            return ""
        start = self.offset_hint if t0 is None else t0
        return _script_slice(start, start + len(audio) / SAMPLE_RATE)

    def _transcribe_window(self, audio: np.ndarray, *, language: str, context: str) -> list[dict]:
        dur = len(audio) / SAMPLE_RATE
        segs = []
        t = 0.0
        while t < dur - 0.5:
            e = min(dur, t + 9.0)
            text = _script_slice(t + self._window_offset, e + self._window_offset)
            for a, b in FINAL_VARIANTS:
                text = text.replace(a, b)
            words = []
            toks = [w for w in text.split(" ") if w]
            for i, tok in enumerate(toks):
                ws = t + (e - t) * i / max(1, len(toks))
                we = t + (e - t) * (i + 1) / max(1, len(toks))
                words.append({"text": tok.strip(".?%"), "start": ws, "end": we})
            if text:
                segs.append({"text": text, "start": t, "end": e, "words": words})
            t = e
        return segs

    def transcribe_long(self, audio, *, language, context="", on_progress=None):
        from .base import split_windows

        out = []
        total = max(1, len(audio))
        for start, end in split_windows(audio, target_s=600.0):
            rel = start / SAMPLE_RATE
            self._window_offset = self.base_offset + rel
            for seg in self._transcribe_window(audio[start:end], language=language, context=context):
                seg["start"] += rel
                seg["end"] += rel
                for w in seg["words"]:
                    w["start"] += rel
                    w["end"] += rel
                out.append(seg)
            if on_progress:
                on_progress(end / total)
        return out


class MockDiarizer(Diarizer):
    name = "mock"

    def diarize(self, wav_path, audio, *, num_speakers=None, on_progress=None):
        dur = len(audio) / SAMPLE_RATE
        pattern = [("A", 9.5), ("B", 5.0), ("A", 7.0), ("C", 6.0), ("B", 8.0)]
        turns, t, i = [], 0.0, 0
        while t < dur:
            sp, length = pattern[i % len(pattern)]
            turns.append({"speaker": sp, "start": t, "end": min(dur, t + length)})
            t += length
            i += 1
        if on_progress:
            on_progress(1.0)
        # 같은 사람(A/B/C)은 회의가 달라도 비슷한 목소리 특징을 낸다
        basis = {"A": [1.0, 0.1, 0.0, 0.0], "B": [0.0, 1.0, 0.1, 0.0], "C": [0.1, 0.0, 1.0, 0.0]}
        self.embeddings = {sp: basis[sp] for sp in {t["speaker"] for t in turns}}
        return turns

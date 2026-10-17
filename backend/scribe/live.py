"""실시간 전사: 들어오는 오디오를 말 끊김(무음) 기준으로 잘라 확정 문장을 만들고,
아직 말하는 중인 부분은 1초마다 다시 받아 적어 '진행 중' 텍스트로 보여준다.

시간은 모두 녹음 파일 기준 절대 시간(초)이다. 메모/댓글 위치를 이 시간에 묶는다.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .config import SAMPLE_RATE
from .engines.base import AsrEngine, frame_db, looks_like_hallucination

FRAME = 480  # 30 ms
FRAME_S = FRAME / SAMPLE_RATE


@dataclass
class LiveConfig:
    min_segment_s: float = 1.2
    silence_s: float = 0.55
    max_segment_s: float = 14.0
    partial_every_s: float = 1.0
    min_partial_s: float = 0.8
    min_speech_s: float = 0.3
    margin_db: float = 9.0
    floor_db: float = -52.0


@dataclass
class LiveEvent:
    type: str  # "segment" | "partial"
    t0: float
    t1: float
    text: str
    extra: dict = field(default_factory=dict)


class LiveSegmenter:
    def __init__(self, engine: Optional[AsrEngine], *, language: str, context: str = "", start_s: float = 0.0, config: Optional[LiveConfig] = None):
        self.engine = engine
        self.language = language
        self.context = context
        self.cfg = config or LiveConfig()
        self.buf = np.zeros(0, dtype=np.float32)
        self.buf_start = int(round(start_s * SAMPLE_RATE))  # buf[0]의 절대 표본 번호
        self._history: deque[float] = deque(maxlen=int(12 / FRAME_S))
        self._last_partial = 0.0
        self._partial_shown = False
        self._incoming: list[np.ndarray] = []
        self._in_lock = threading.Lock()
        self._last_db = np.zeros(0)

    @property
    def pending_s(self) -> float:
        return len(self.buf) / SAMPLE_RATE

    def feed(self, audio: np.ndarray) -> None:
        """어느 스레드에서든 호출 가능. 실제 버퍼 반영은 step()에서 한다."""
        if len(audio):
            with self._in_lock:
                self._incoming.append(audio.astype(np.float32))

    def _drain(self) -> None:
        with self._in_lock:
            pieces, self._incoming = self._incoming, []
        if pieces:
            self.buf = np.concatenate([self.buf, *pieces])

    def _t(self, sample_idx: int) -> float:
        return (self.buf_start + sample_idx) / SAMPLE_RATE

    def _speech_mask(self, db: np.ndarray) -> np.ndarray:
        self._history.extend(db.tolist()[-len(db):])
        hist = np.fromiter(self._history, dtype=float) if self._history else db
        noise = float(np.percentile(hist, 15)) if len(hist) else -70.0
        thresh = max(noise + self.cfg.margin_db, self.cfg.floor_db)
        return db > thresh

    def _find_cut(self, speech: np.ndarray) -> Optional[int]:
        """확정할 지점(프레임 번호). 말이 있은 뒤 처음 나오는 충분히 긴 무음의 가운데에서 자른다.
        밀린 오디오가 길어도 max_segment_s 안에서 자르므로 문장이 지나치게 길어지지 않는다."""
        n = len(speech)
        min_f = int(self.cfg.min_segment_s / FRAME_S)
        need = int(self.cfg.silence_s / FRAME_S)
        max_f = int(self.cfg.max_segment_s / FRAME_S)
        limit = min(n, max_f)
        run = 0
        seen_speech = False
        for i in range(limit):
            if not speech[i]:
                run += 1
                continue
            if run >= need and seen_speech and i - run >= min_f:
                return i - run // 2
            run = 0
            seen_speech = True
        if limit == n and run >= need and seen_speech and n - run >= min_f:
            return n - run // 2
        if n >= max_f:
            lo = max(min_f, max_f - int(4.0 / FRAME_S))
            return lo + int(np.argmin(self._last_db[lo:max_f]))
        return None

    def _asr(self, audio: np.ndarray, t0: float) -> str:
        if self.engine is None:
            return ""
        if hasattr(self.engine, "offset_hint"):
            self.engine.offset_hint = t0  # mock 엔진용
        return self.engine.transcribe(audio, language=self.language, context=self.context).strip()

    def step(self, *, force: bool = False) -> list[LiveEvent]:
        """버퍼를 살펴 확정/진행 중 이벤트를 만든다. (작업 스레드에서 호출)"""
        events: list[LiveEvent] = []
        self._drain()
        if len(self.buf) < FRAME and not force:
            return events
        db = frame_db(self.buf, FRAME)
        self._last_db = db
        speech = self._speech_mask(db) if len(db) else np.zeros(0, dtype=bool)

        # 말소리 없이 무음만 3초 넘게 쌓이면 앞부분을 버린다 (0.5초는 남김)
        if not force and len(speech) * FRAME_S > 3.0 and not speech.any():
            drop = (len(speech) - int(0.5 / FRAME_S)) * FRAME
            self.buf = self.buf[drop:]
            self.buf_start += drop
            return events

        cut_f = len(speech) if force else self._find_cut(speech)
        while cut_f:
            cut = len(self.buf) if force else cut_f * FRAME
            seg_audio = self.buf[:cut]
            seg_speech = speech[:cut_f]
            speech_s = float(seg_speech.sum()) * FRAME_S
            if speech_s >= self.cfg.min_speech_s and self.engine is not None:
                idx = np.nonzero(seg_speech)[0]
                s0 = max(0, int(idx[0]) - 3) * FRAME
                s1 = min(cut, (int(idx[-1]) + 4) * FRAME)
                t0, t1 = self._t(s0), self._t(s1)
                text = self._asr(seg_audio[s0:s1], t0)
                ratio = speech_s / max(1e-6, (s1 - s0) / SAMPLE_RATE)
                if text and not (ratio < 0.5 and looks_like_hallucination(text)):
                    events.append(LiveEvent("segment", round(t0, 3), round(t1, 3), text))
            self.buf = self.buf[cut:]
            self.buf_start += cut
            speech = speech[cut_f:]
            self._last_db = self._last_db[cut_f:]
            if self._partial_shown:
                events.append(LiveEvent("partial", self._t(0), self._t(0), ""))
                self._partial_shown = False
            if force:
                break
            cut_f = self._find_cut(speech) if len(speech) else None

        now = time.monotonic()
        if (
            not force
            and self.engine is not None
            and self.pending_s >= self.cfg.min_partial_s
            and len(speech)
            and float(speech.sum()) * FRAME_S >= self.cfg.min_speech_s
            and now - self._last_partial >= self.cfg.partial_every_s
        ):
            self._last_partial = now
            text = self._asr(self.buf, self._t(0))
            if text:
                events.append(LiveEvent("partial", self._t(0), self._t(len(self.buf)), text))
                self._partial_shown = True
        return events

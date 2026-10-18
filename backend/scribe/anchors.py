"""메모·댓글·강조의 '위치'.

기록은 하나(실시간 기록, 올린 파일이면 한 번 받아 적은 기록)이고, 위치는 그 기록의
문장 id + 글자 위치로 저장한다. 사람이 문장을 고치면 글자 차이를 따라 위치를 정확히 옮긴다
(다른 기록으로 옮겨 붙이는 일은 하지 않는다).

unit 형식: {"id", "t0", "t1", "text"}
"""

from __future__ import annotations

import bisect
import difflib
from typing import Any, Optional

import numpy as np


def unit_char_times(unit: dict) -> np.ndarray:
    """문장 안 글자별 시각 (문장 시작~끝을 글자 수로 고르게 나눈다)"""
    n = len(unit.get("text") or "")
    if n == 0:
        return np.zeros(0)
    t0, t1 = float(unit["t0"]), float(unit["t1"])
    return t0 + (t1 - t0) * (np.arange(n) + 0.5) / n


class TextIndex:
    """여러 unit을 이어 붙인 전체 텍스트와 글자별 시간."""

    def __init__(self, units: list[dict]):
        self.units = units
        self.id_to_idx = {u["id"]: i for i, u in enumerate(units)}
        self.starts: list[int] = []
        parts: list[str] = []
        pos = 0
        for u in units:
            self.starts.append(pos)
            parts.append(u.get("text") or "")
            pos += len(u.get("text") or "") + 1
        self.text = "\n".join(parts)
        times = np.zeros(len(self.text))
        for i, u in enumerate(units):
            base = self.starts[i]
            n = len(u.get("text") or "")
            if n:
                times[base : base + n] = unit_char_times(u)
            if base + n < len(times):
                times[base + n] = float(u["t1"])
        self.times = times
        self.unit_t0 = [float(u["t0"]) for u in units]

    # --- 좌표 변환 ---
    def to_global(self, unit_id: str, offset: int) -> Optional[int]:
        idx = self.id_to_idx.get(unit_id)
        if idx is None:
            return None
        n = len(self.units[idx].get("text") or "")
        return self.starts[idx] + max(0, min(int(offset), n))

    def from_global(self, g: int) -> tuple[int, int]:
        idx = max(0, bisect.bisect_right(self.starts, g) - 1)
        n = len(self.units[idx].get("text") or "")
        return idx, max(0, min(g - self.starts[idx], n))

    def end_from_global(self, ge: int) -> tuple[int, int]:
        """구간 끝(배타적)을 unit 좌표로. 다음 unit 시작이 아니라 이전 unit의 끝으로 붙인다."""
        if ge <= 0:
            return 0, 0
        idx, off = self.from_global(ge - 1)
        n = len(self.units[idx].get("text") or "")
        return idx, min(off + 1, n)

    def time_at(self, g: int) -> float:
        if len(self.times) == 0:
            return 0.0
        return float(self.times[max(0, min(g, len(self.times) - 1))])

    def unit_after_time(self, t: float) -> Optional[str]:
        """시간 t를 포함하는(또는 직전의) unit. 메모는 이 unit 뒤에 표시된다."""
        if not self.units:
            return None
        i = bisect.bisect_right(self.unit_t0, t) - 1
        return self.units[i]["id"] if i >= 0 else None

    def slice(self, gs: int, ge: int) -> str:
        return self.text[gs:ge].replace("\n", " ")


def compute_anchor(index: TextIndex, start: dict, end: dict) -> Optional[dict]:
    """선택한 범위의 위치: 문장 id + 글자 위치, 그리고 보여 주기·내보내기용 인용과 시각."""
    gs = index.to_global(start["unit"], start["offset"])
    ge = index.to_global(end["unit"], end["offset"])
    if gs is None or ge is None:
        return None
    if ge < gs:
        gs, ge = ge, gs
    quote = index.slice(gs, ge)
    if not quote.strip():
        return None
    t0 = index.time_at(gs)
    t1 = index.time_at(max(gs, ge - 1))
    su, so = index.from_global(gs)
    eu, eo = index.end_from_global(ge)
    return {
        "t0": round(t0, 3),
        "t1": round(max(t0, t1), 3),
        "quote": quote,
        "start": {"unit": index.units[su]["id"], "offset": so},
        "end": {"unit": index.units[eu]["id"], "offset": eo},
    }


def memo_anchor(index: TextIndex, t: float, after_unit: Optional[str] = None) -> dict:
    if after_unit is None:
        after_unit = index.unit_after_time(t)
    return {"t": round(float(t), 3), "after": after_unit}


def resolve(anchor: dict, index: TextIndex) -> Optional[dict]:
    """앵커를 기록 위의 위치로.

    반환: 범위형 {"start":{unit,offset}, "end":{unit,offset}, "text"}
          메모형 {"after": unit_id | None}
    """
    if "t" in anchor and "start" not in anchor:
        after = anchor.get("after")
        if after is not None and after in index.id_to_idx:
            return {"after": after}
        return {"after": index.unit_after_time(float(anchor["t"]))}
    start, end = anchor.get("start"), anchor.get("end")
    if not start or not end:
        return None
    gs = index.to_global(start["unit"], start["offset"])
    ge = index.to_global(end["unit"], end["offset"])
    if gs is None or ge is None or ge <= gs:
        return None
    su, so = index.from_global(gs)
    eu, eo = index.end_from_global(ge)
    return {"start": {"unit": index.units[su]["id"], "offset": so}, "end": {"unit": index.units[eu]["id"], "offset": eo}, "text": index.slice(gs, ge)}


def resolve_annotations(annotations: list[dict], index: TextIndex) -> list[dict]:
    """각 주석에 기록 위의 위치(pos)를 붙여 돌려준다."""
    return [dict(ann, pos=resolve(ann["anchor"], index)) for ann in annotations]


def anchor_from_request(index: TextIndex, payload: dict[str, Any]) -> Optional[dict]:
    if payload.get("kind") in ("memo", "divider"):
        return memo_anchor(index, float(payload.get("t") or 0.0), payload.get("after"))
    return compute_anchor(index, payload["start"], payload["end"])


def remap_start(opcodes: list[tuple], offset: int) -> int:
    """문장을 고친 뒤 범위 시작 옮기기: 남은 글자는 그대로 따라가고, 바뀐 글자 안이면 바뀐 부분의 처음으로.
    시작 바로 앞에 끼워 넣은 글자는 범위에 넣지 않는다."""
    for tag, i1, i2, j1, j2 in opcodes:
        if tag != "insert" and i1 <= offset < i2:
            return j1 + (offset - i1) if tag == "equal" else j1
    return opcodes[-1][4] if opcodes else 0


def remap_end(opcodes: list[tuple], offset: int) -> int:
    """범위 끝(배타적) 옮기기: 마지막 글자를 따라가고, 바뀐 글자 안이면 바뀐 부분의 끝까지.
    끝 바로 뒤에 끼워 넣은 글자는 범위에 넣지 않는다."""
    if offset <= 0:
        return 0
    c = offset - 1
    for tag, i1, i2, j1, j2 in opcodes:
        if tag != "insert" and i1 <= c < i2:
            return j1 + (c - i1) + 1 if tag == "equal" else j2
    return opcodes[-1][4] if opcodes else 0


def edit_opcodes(old: str, new: str) -> list[tuple]:
    return difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()

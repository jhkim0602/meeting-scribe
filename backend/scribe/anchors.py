"""메모/댓글/수정의 '위치'를 다루는 핵심 모듈.

실시간 기록과 후처리 원본은 문장이 서로 다르다. 그래서 위치를 글자 번호가 아니라
(1) 오디오 시간 구간과 (2) 사용자가 고른 원문 인용으로 저장해 두고,
다른 버전의 기록에서는 시간 창 안에서 인용문과 가장 비슷한 부분을 찾아 다시 붙인다.

unit 형식: {"id", "t0", "t1", "text", "words"?: [[offset, length, start, end], ...]}
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from .textutil import is_kept, normalize

try:  # rapidfuzz가 없으면 difflib로 대체
    from rapidfuzz import fuzz as _fuzz
except ImportError:  # pragma: no cover
    _fuzz = None

MATCH_THRESHOLD = 55.0
CONTEXT_CHARS = 24


def unit_char_times(unit: dict) -> np.ndarray:
    text = unit.get("text") or ""
    n = len(text)
    t0, t1 = float(unit["t0"]), float(unit["t1"])
    if n == 0:
        return np.zeros(0)
    words = unit.get("words") or []
    if words:
        known_idx: list[float] = []
        known_val: list[float] = []
        for o, length, s, e in words:
            if length <= 0 or o >= n:
                continue
            length = min(length, n - o)
            for k in range(length):
                known_idx.append(o + k)
                known_val.append(s + (e - s) * (k / length))
        if known_idx:
            order = np.argsort(known_idx, kind="stable")
            xi = np.asarray(known_idx)[order]
            yi = np.maximum.accumulate(np.asarray(known_val)[order])
            return np.interp(np.arange(n), xi, yi)
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
        norm_chars: list[str] = []
        norm_map: list[int] = []
        for gi, ch in enumerate(self.text):
            if is_kept(ch):
                norm_chars.append(ch.lower())
                norm_map.append(gi)
        self.norm = "".join(norm_chars)
        self.norm_map = np.asarray(norm_map, dtype=np.int64)
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

    def nearest_char(self, t: float) -> int:
        if len(self.times) == 0:
            return 0
        return int(np.argmin(np.abs(self.times - t)))

    def unit_after_time(self, t: float) -> Optional[str]:
        """시간 t를 포함하는(또는 직전의) unit. 메모는 이 unit 뒤에 표시된다."""
        if not self.units:
            return None
        i = bisect.bisect_right(self.unit_t0, t) - 1
        return self.units[i]["id"] if i >= 0 else None

    def slice(self, gs: int, ge: int) -> str:
        return self.text[gs:ge].replace("\n", " ")


def compute_anchor(index: TextIndex, start: dict, end: dict) -> Optional[dict]:
    """선택한 범위로부터 버전에 독립적인 앵커를 만든다."""
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
        "prefix": index.slice(max(0, gs - CONTEXT_CHARS), gs),
        "suffix": index.slice(ge, ge + CONTEXT_CHARS),
        "start": {"unit": index.units[su]["id"], "offset": so},
        "end": {"unit": index.units[eu]["id"], "offset": eo},
    }


def memo_anchor(index: TextIndex, t: float, after_unit: Optional[str] = None) -> dict:
    if after_unit is None:
        after_unit = index.unit_after_time(t)
    return {"t": round(float(t), 3), "after": after_unit}


@dataclass
class Resolved:
    gs: int
    ge: int
    approx: bool
    score: float


def _best_short_match(qn: str, wn: str, center: int) -> Optional[int]:
    best, best_d = None, None
    pos = wn.find(qn)
    while pos >= 0:
        d = abs(pos - center)
        if best_d is None or d < best_d:
            best, best_d = pos, d
        pos = wn.find(qn, pos + 1)
    return best


def _fuzzy(qn: str, wn: str) -> tuple[float, int, int]:
    """wn 안에서 qn과 가장 비슷한 부분 문자열 (점수 0~100, 시작, 끝)."""
    if _fuzz is not None:
        al = _fuzz.partial_ratio_alignment(qn, wn)
        if al is None:
            return 0.0, 0, 0
        # rapidfuzz는 src=qn, dest=wn 좌표로 돌려준다
        return float(al.score), int(al.dest_start), int(al.dest_end)
    import difflib  # pragma: no cover

    best = (0.0, 0, 0)
    n = len(qn)
    for i in range(0, max(1, len(wn) - n + 1)):
        for length in {n, max(1, int(n * 0.85)), int(n * 1.15) + 1}:
            cand = wn[i : i + length]
            r = difflib.SequenceMatcher(None, qn, cand).ratio() * 100
            if r > best[0]:
                best = (r, i, i + len(cand))
    return best


def _snap_to_words(index: TextIndex, gs: int, ge: int, limit: int = 6) -> tuple[int, int]:
    """다른 버전에 다시 붙일 때 단어 중간에서 시작/끝나지 않게 어절 경계로 넓힌다."""
    text = index.text
    k = 0
    while gs > 0 and k < limit and is_kept(text[gs - 1]):
        gs -= 1
        k += 1
    k = 0
    while ge < len(text) and k < limit and is_kept(text[ge]):
        ge += 1
        k += 1
    return gs, ge


def _resolve_range(anchor: dict, index: TextIndex) -> Optional[Resolved]:
    if not index.units or len(index.text) == 0:
        return None
    t0 = float(anchor.get("t0", 0.0))
    t1 = float(anchor.get("t1", t0))
    quote = anchor.get("quote") or ""
    qn = normalize(quote)

    pad = max(4.0, 0.5 * (t1 - t0))
    mask = (index.times >= t0 - pad) & (index.times <= t1 + pad)
    hits = np.nonzero(mask)[0]
    if len(hits):
        w0, w1 = int(hits.min()), int(hits.max()) + 1
    else:
        c = index.nearest_char((t0 + t1) / 2)
        w0, w1 = c, c + 1
    need = int(len(quote) * 1.5) + 8
    if w1 - w0 < need:
        grow = (need - (w1 - w0)) // 2 + 1
        w0, w1 = max(0, w0 - grow), min(len(index.text), w1 + grow)

    if qn:
        n0 = int(np.searchsorted(index.norm_map, w0))
        n1 = int(np.searchsorted(index.norm_map, w1))
        wn = index.norm[n0:n1]
        center_g = index.nearest_char((t0 + t1) / 2)
        center_n = int(np.searchsorted(index.norm_map, center_g)) - n0 - len(qn) // 2
        if wn:
            if len(qn) <= 3:
                pos = _best_short_match(qn, wn, center_n)
                if pos is not None:
                    gs = int(index.norm_map[n0 + pos])
                    ge = int(index.norm_map[n0 + pos + len(qn) - 1]) + 1
                    return Resolved(gs, ge, False, 100.0)
            elif len(qn) <= len(wn):
                score, ds, de = _fuzzy(qn, wn)
                if score >= MATCH_THRESHOLD and de > ds:
                    gs = int(index.norm_map[n0 + ds])
                    ge = int(index.norm_map[n0 + de - 1]) + 1
                    gs, ge = _snap_to_words(index, gs, ge)
                    return Resolved(gs, ge, False, score)

    # 시간만으로 추정
    mask2 = (index.times >= t0) & (index.times <= max(t1, t0 + 0.05))
    hits2 = np.nonzero(mask2)[0]
    if len(hits2):
        gs, ge = int(hits2.min()), int(hits2.max()) + 1
    else:
        gs = index.nearest_char(t0)
        ge = gs + 1
    # 줄바꿈 구분자 위에 걸치지 않게 정리
    while gs < ge and index.text[gs] == "\n":
        gs += 1
    while ge > gs and index.text[ge - 1] == "\n":
        ge -= 1
    if ge <= gs:
        return None
    return Resolved(gs, ge, True, 0.0)


def resolve(anchor: dict, index: TextIndex, *, same_source: bool = False) -> Optional[dict]:
    """앵커를 주어진 기록(index) 위의 위치로 바꾼다.

    반환: 범위형 {"start":{unit,offset}, "end":{unit,offset}, "approx", "score", "text"}
          메모형 {"after": unit_id | None}
    """
    if "t" in anchor and "quote" not in anchor:
        after = anchor.get("after") if same_source else None
        if after is not None and after in index.id_to_idx:
            return {"after": after}
        return {"after": index.unit_after_time(float(anchor["t"]))}

    if same_source and anchor.get("start") and anchor.get("end"):
        gs = index.to_global(anchor["start"]["unit"], anchor["start"]["offset"])
        ge = index.to_global(anchor["end"]["unit"], anchor["end"]["offset"])
        if gs is not None and ge is not None and ge > gs:
            if normalize(index.slice(gs, ge)) == normalize(anchor.get("quote") or ""):
                return _pack(index, Resolved(gs, ge, False, 100.0))

    r = _resolve_range(anchor, index)
    return _pack(index, r) if r else None


def _pack(index: TextIndex, r: Resolved) -> dict:
    su, so = index.from_global(r.gs)
    eu, eo = index.end_from_global(r.ge)
    return {
        "start": {"unit": index.units[su]["id"], "offset": so},
        "end": {"unit": index.units[eu]["id"], "offset": eo},
        "approx": r.approx,
        "score": round(r.score, 1),
        "text": index.slice(r.gs, r.ge),
    }


def resolve_annotations(annotations: list[dict], index: TextIndex, view: str, version: int) -> list[dict]:
    """각 주석에 이 화면에서의 위치(pos)를 붙여 돌려준다. 위치를 못 찾으면 pos=None."""
    out = []
    for ann in annotations:
        same = ann.get("origin") == view and (view == "live" or int(ann.get("origin_version") or 0) == version)
        pos = resolve(ann["anchor"], index, same_source=same)
        item = dict(ann)
        item["pos"] = pos
        if ann["kind"] == "edit" and pos and "text" in pos:
            item["match"] = "same" if normalize(pos["text"]) == normalize(ann.get("replacement") or "") else "differs"
        out.append(item)
    return out


def anchor_from_request(index: TextIndex, payload: dict[str, Any]) -> Optional[dict]:
    if payload.get("kind") == "memo":
        return memo_anchor(index, float(payload.get("t") or 0.0), payload.get("after"))
    return compute_anchor(index, payload["start"], payload["end"])

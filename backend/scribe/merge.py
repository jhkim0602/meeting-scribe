"""ASR 단어 타임스탬프 + 화자 구간 → 화자별 발화(utterance) 목록."""

from __future__ import annotations

import bisect
from typing import Optional

from .textutil import SENTENCE_END, is_kept

SPEAKER_GAP_S = 1.5
LONG_PIECE_S = 25.0
MERGE_GAP_S = 1.0
MERGE_MAX_S = 45.0


def _match_at(text: str, i: int, wt: str) -> int:
    k = 0
    n = len(text)
    while k < len(wt) and i < n:
        ch = text[i]
        if not is_kept(ch):
            if k == 0:
                return -1
            i += 1
            continue
        if ch.lower() != wt[k]:
            return -1
        k += 1
        i += 1
    return i if k == len(wt) else -1


def locate_words(text: str, words: list[dict], lookahead: int = 80) -> list[list[float]]:
    """정렬기 단어(문장부호 없음, 분절 다름)를 원문 문자열 위치에 붙인다 → [offset, length, start, end]."""
    out: list[list[float]] = []
    cursor = 0
    for w in words:
        wt = "".join(ch.lower() for ch in str(w.get("text", "")) if is_kept(ch))
        if not wt:
            continue
        limit = min(len(text), cursor + lookahead)
        for i in range(cursor, limit):
            if text[i].lower() == wt[0]:
                e = _match_at(text, i, wt)
                if e >= 0:
                    out.append([i, e - i, float(w["start"]), float(w["end"])])
                    cursor = e
                    break
    return out


class TurnIndex:
    def __init__(self, turns: list[dict]):
        self.turns = sorted(
            ({"start": float(t["start"]), "end": float(t["end"]), "speaker": str(t["speaker"])} for t in turns),
            key=lambda t: t["start"],
        )
        self.starts = [t["start"] for t in self.turns]
        self.max_len = max((t["end"] - t["start"] for t in self.turns), default=0.0)

    def speaker_for(self, s: float, e: float, near: float = 2.0) -> Optional[str]:
        if not self.turns:
            return None
        if e <= s:
            e = s + 0.01
        best, best_ov = None, 0.0
        j = max(0, bisect.bisect_right(self.starts, e) - 1)
        # 겹치는 turn을 뒤에서부터 훑는다
        k = j
        while k >= 0:
            t = self.turns[k]
            ov = min(e, t["end"]) - max(s, t["start"])
            if ov > best_ov:
                best, best_ov = t["speaker"], ov
            if t["start"] + self.max_len < s:  # 이보다 앞선 turn은 겹칠 수 없음
                break
            k -= 1
        if best is not None:
            return best
        mid = (s + e) / 2
        nearest, nd = None, None
        for t in self.turns[max(0, j - 2) : j + 3]:
            d = 0.0 if t["start"] <= mid <= t["end"] else min(abs(mid - t["start"]), abs(mid - t["end"]))
            if nd is None or d < nd:
                nearest, nd = t["speaker"], d
        return nearest if nd is not None and nd <= near else None


def _piece(text: str, p0: int, p1: int, words: list[list[float]], speaker: Optional[str], fallback: tuple[float, float]) -> Optional[dict]:
    raw = text[p0:p1]
    stripped = raw.strip()
    if not stripped:
        return None
    shift = p0 + (len(raw) - len(raw.lstrip()))
    local = [[w[0] - shift, w[1], w[2], w[3]] for w in words]
    local = [w for w in local if 0 <= w[0] < len(stripped)]
    t0 = local[0][2] if local else fallback[0]
    t1 = local[-1][3] if local else fallback[1]
    return {"t0": round(t0, 3), "t1": round(max(t0, t1), 3), "speaker": speaker, "text": stripped, "words": [[int(a), int(b), round(c, 3), round(d, 3)] for a, b, c, d in local]}


def _majority(speakers: list[Optional[str]], durations: list[float]) -> Optional[str]:
    acc: dict[str, float] = {}
    for sp, d in zip(speakers, durations):
        if sp is not None:
            acc[sp] = acc.get(sp, 0.0) + max(d, 0.01)
    return max(acc, key=acc.get) if acc else None


def _crosses(boundaries: list[float], a: float, b: float) -> bool:
    """a와 b 사이(a < x <= b)에 구분선이 있는가."""
    if not boundaries or b <= a:
        return False
    i = bisect.bisect_right(boundaries, a)
    return i < len(boundaries) and boundaries[i] <= b


def split_segment(seg: dict, turns: TurnIndex, boundaries: list[float] | None = None) -> list[dict]:
    text = seg.get("text") or ""
    if not text.strip():
        return []
    words = locate_words(text, seg.get("words") or [])
    s0, s1 = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
    if not words:
        return [p for p in [_piece(text, 0, len(text), [], turns.speaker_for(s0, s1), (s0, s1))] if p]

    speakers = [turns.speaker_for(w[2], w[3]) for w in words]
    # 화자 정보가 비는 단어는 앞 단어의 화자를 이어받는다
    last = None
    for i, sp in enumerate(speakers):
        if sp is None:
            speakers[i] = last
        else:
            last = sp

    cuts = [0]
    piece_start_t = words[0][2]
    for i in range(1, len(words)):
        prev, cur = words[i - 1], words[i]
        between = text[int(prev[0] + prev[1]) : int(cur[0])]
        sentence_break = any(ch in SENTENCE_END for ch in between)
        if (
            speakers[i] != speakers[i - 1]
            or _crosses(boundaries or [], (prev[2] + prev[3]) / 2, (cur[2] + cur[3]) / 2)
            or cur[2] - prev[3] > SPEAKER_GAP_S
            or (sentence_break and cur[2] - piece_start_t > LONG_PIECE_S)
        ):
            cuts.append(int(cur[0]))
            piece_start_t = cur[2]
    cuts.append(len(text))

    pieces = []
    wi = 0
    for k in range(len(cuts) - 1):
        p0, p1 = cuts[k], cuts[k + 1]
        group, group_sp, group_d = [], [], []
        while wi < len(words) and words[wi][0] < p1:
            group.append(words[wi])
            group_sp.append(speakers[wi])
            group_d.append(words[wi][3] - words[wi][2])
            wi += 1
        p = _piece(text, p0, p1, group, _majority(group_sp, group_d), (s0, s1))
        if p:
            pieces.append(p)
    return pieces


def _merge_adjacent(utts: list[dict], boundaries: list[float] | None = None) -> list[dict]:
    out: list[dict] = []
    for u in utts:
        if out:
            prev = out[-1]
            if (
                prev["speaker"] == u["speaker"]
                and not _crosses(boundaries or [], prev["t1"] - 0.3, u["t0"] + 0.3)
                and u["t0"] - prev["t1"] <= MERGE_GAP_S
                and not prev["text"].endswith(SENTENCE_END)
                and u["t1"] - prev["t0"] <= MERGE_MAX_S
            ):
                shift = len(prev["text"]) + 1
                prev["text"] = prev["text"] + " " + u["text"]
                prev["words"] = prev["words"] + [[w[0] + shift, w[1], w[2], w[3]] for w in u["words"]]
                prev["t1"] = max(prev["t1"], u["t1"])
                continue
        out.append(dict(u))
    return out


def build_utterances(segments: list[dict], turns: list[dict], boundaries: list[float] | None = None) -> dict:
    """segments: [{text, start, end, words:[{text,start,end}]}], turns: [{speaker,start,end}].
    boundaries: 쉬는 시간/구분선 시각. 이 시각을 넘어 발화를 합치지 않는다."""
    ti = TurnIndex(turns)
    bounds = sorted(float(b) for b in (boundaries or []))
    utts: list[dict] = []
    for seg in segments:
        utts.extend(split_segment(seg, ti, bounds))
    utts.sort(key=lambda u: (u["t0"], u["t1"]))
    utts = _merge_adjacent(utts, bounds)

    # 화자 라벨을 등장 순서대로 S1, S2 ...로 바꾼다
    mapping: dict[str, str] = {}
    for u in utts:
        sp = u["speaker"]
        if sp is not None and sp not in mapping:
            mapping[sp] = f"S{len(mapping) + 1}"
        u["speaker"] = mapping.get(sp) if sp is not None else None
    for i, u in enumerate(utts):
        u["id"] = f"u{i}"
    return {"utterances": utts, "speakers": list(mapping.values())}

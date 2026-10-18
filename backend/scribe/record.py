"""회의 기록 고치기.

기록은 하나다. 사람이 문장 글자를 직접 고치면(STT가 틀렸을 때), 그 문장에 걸린 댓글·강조의
위치를 글자 차이대로 옮긴다. 처음 받아 적은 글자는 남겨 두어 언제든 원래대로 돌릴 수 있다.
"""

from __future__ import annotations

import re
from typing import Optional

from .anchors import TextIndex, compute_anchor, edit_opcodes, remap_end, remap_start, resolve_annotations
from .db import Store
from .live_speakers import LETTERS
from .merge import TurnIndex, sentence_rows
from .views import Views


class EditError(ValueError):
    pass


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def edit_segment(store: Store, views: Views, meeting_id: str, segment_id: str, text: str) -> dict:
    """문장 글자를 바꾸고, 바뀐 문장 정보와 위치가 옮겨진 댓글·강조를 돌려준다.
    반환: {"unit": {...}, "annotations": [바뀐 항목], "deleted": [지운 항목 id]}
    긴 회의에서도 빠르게: 이 문장에 걸린 항목만 읽고, 그 항목이 덮는 문장들로만 작은 색인을 만든다."""
    seg = store.get_live_segment(segment_id)
    if seg is None or seg["meeting_id"] != meeting_id:
        raise KeyError(segment_id)
    new = clean_text(text)
    if not new:
        raise EditError("문장을 비울 수는 없어요")
    old = seg["text"]
    changed: list[dict] = []
    deleted: list[str] = []
    if new != old:
        ops = edit_opcodes(old, new)
        store.set_live_text(segment_id, new)
        local: dict[tuple, Optional[TextIndex]] = {}
        for ann in store.annotations_on_segment(meeting_id, segment_id):
            st, en = ann["anchor"]["start"], ann["anchor"]["end"]
            ns, ne = dict(st), dict(en)
            if st["unit"] == segment_id:
                ns["offset"] = remap_start(ops, int(st["offset"]))
            if en["unit"] == segment_id:
                ne["offset"] = remap_end(ops, int(en["offset"]))
            key = (st["unit"], en["unit"])
            if key not in local:
                local[key] = views.local_index(meeting_id, list(key))
            idx = local[key]
            if idx is None:
                continue
            anchor = compute_anchor(idx, ns, ne) if _span(idx, ns, ne) else None
            if anchor is None:
                if ann["kind"] == "highlight":  # 강조한 글자를 모두 지웠다
                    store.delete_annotation(ann["id"])
                    deleted.append(ann["id"])
                    continue
                # 댓글 단 글자를 모두 지웠으면 문장 전체에 단다
                anchor = compute_anchor(idx, {"unit": segment_id, "offset": 0}, {"unit": segment_id, "offset": len(new)})
            updated = store.update_annotation(ann["id"], anchor=anchor)
            if updated is not None:
                changed.append(resolve_annotations([updated], idx)[0])
    seg = store.get_live_segment(segment_id) or seg
    unit = {"id": seg["id"], "t0": seg["t0"], "t1": seg["t1"], "text": seg["text"]}
    if seg.get("text_orig") is not None:
        unit["text_orig"] = seg["text_orig"]
    return {"unit": unit, "annotations": changed, "deleted": deleted}


def _span(idx, start: dict, end: dict) -> bool:
    gs, ge = idx.to_global(start["unit"], start["offset"]), idx.to_global(end["unit"], end["offset"])
    return gs is not None and ge is not None and ge > gs


def apply_legacy_edits(store: Store, views: Views) -> int:
    """예전의 "수정 제안"을 정리한다 (한 번만): 실시간 기록에 단 것은 그 문장을 직접 고친 것으로 바꾸고,
    무시한 것과 원본(후처리)에 단 것은 지운다. 바꾼 개수를 돌려준다."""
    if store.get_setting("legacy_edits_applied"):
        return 0
    n = 0
    for m in store.list_meetings():
        for ann in sorted((a for a in store.list_annotations(m["id"]) if a["kind"] == "edit"), key=lambda a: a["created_at"]):
            cur = store.get_annotation(ann["id"])  # 앞의 수정으로 위치가 옮겨졌을 수 있다
            if cur is None:
                continue
            store.delete_annotation(cur["id"])
            st, en = cur["anchor"].get("start"), cur["anchor"].get("end")
            if cur.get("origin") != "live" or cur.get("status") == "dismissed" or not st or not en or st["unit"] != en["unit"] or cur.get("replacement") is None:
                continue
            seg = store.get_live_segment(st["unit"])
            if seg is None or seg["text"][st["offset"]:en["offset"]] != cur["anchor"].get("quote"):
                continue
            text = seg["text"][: st["offset"]] + cur["replacement"] + seg["text"][en["offset"]:]
            if clean_text(text):
                edit_segment(store, views, m["id"], seg["id"], text)
                n += 1
    store.set_setting("legacy_edits_applied", True)
    return n


def add_window_rows(store: Store, meeting_id: str, segments: list[dict], t0: float, t1: float) -> int:
    """올린 파일: 받아 적은 구간(10분 안팎)의 문장을 바로 회의 기록에 넣는다 (다 끝날 때까지 기다리지 않게).
    그 구간에 이미 기록이 있으면(다시 받아 적기, 멈췄다 이어 하기) 건드리지 않는다. 넣은 문장 수를 돌려준다."""
    if store.has_live_between(meeting_id, t0, t1):
        return 0
    rows = [{"t0": r["t0"], "t1": r["t1"], "text": clean_text(r["text"]), "speaker_auto": None, "speaker_conf": None}
            for r in sentence_rows(segments) if clean_text(r["text"])]
    return store.add_live_segments(meeting_id, rows) if rows else 0


def label_speakers(store: Store, meeting_id: str, turns: list[dict]) -> int:
    """올린 파일: 다 받아 적은 뒤 화자 구분 결과로 문장마다 목소리 묶음(A, B…)을 붙인다.
    이미 묶음이 있는 문장은 그대로 두고, 새 문장은 같은 목소리면 같은 글자를 쓴다. 고른 참여자도 이어받는다."""
    if not turns:
        return 0
    ti = TurnIndex(turns)
    rows = store.list_live_speakers(meeting_id)
    voice = {r["id"]: ti.speaker_for(r["t0"], r["t1"]) for r in rows}
    votes: dict[str, dict[str, float]] = {}
    for r in rows:
        sp = voice[r["id"]]
        if sp is not None and r.get("speaker_auto"):
            acc = votes.setdefault(sp, {})
            acc[r["speaker_auto"]] = acc.get(r["speaker_auto"], 0.0) + max(0.01, r["t1"] - r["t0"])
    letters = {sp: max(acc, key=acc.get) for sp, acc in votes.items()}
    used = {r["speaker_auto"] for r in rows if r.get("speaker_auto")} | set(letters.values())
    speaker_map = (store.get_meeting(meeting_id) or {}).get("speaker_map") or {}
    changed = []
    for r in rows:
        sp = voice[r["id"]]
        if r.get("speaker_auto") or sp is None:
            continue
        if sp not in letters:
            letters[sp] = next((ch for ch in LETTERS if ch not in used), LETTERS[-1])
            used.add(letters[sp])
        auto = letters[sp]
        out = {"id": r["id"], "speaker": r.get("speaker"), "speaker_auto": auto, "speaker_src": r.get("speaker_src"), "speaker_conf": r.get("speaker_conf")}
        if not out["speaker"] and speaker_map.get(auto):
            out.update(speaker=speaker_map[auto], speaker_src="auto")
        changed.append(out)
    store.update_live_speakers(changed)
    return len(changed)

"""회의 기록 고치기.

기록은 하나다. 사람이 문장 글자를 직접 고치면(STT가 틀렸을 때), 그 문장에 걸린 댓글·강조의
위치를 글자 차이대로 옮긴다. 처음 받아 적은 글자는 남겨 두어 언제든 원래대로 돌릴 수 있다.
"""

from __future__ import annotations

import re
from typing import Optional

from .anchors import compute_anchor, edit_opcodes, remap_end, remap_start
from .db import Store
from .live_speakers import LETTERS
from .views import Views


class EditError(ValueError):
    pass


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def edit_segment(store: Store, views: Views, meeting_id: str, segment_id: str, text: str) -> dict:
    """문장 글자를 바꾸고, 바뀐 문장 정보와 위치가 옮겨진 댓글·강조를 돌려준다.
    반환: {"unit": {...}, "annotations": [바뀐 항목], "deleted": [지운 항목 id]}"""
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
        idx = views.index(meeting_id)
        for ann in store.list_annotations(meeting_id):
            a = ann["anchor"]
            st, en = a.get("start"), a.get("end")
            if not st or not en or segment_id not in (st["unit"], en["unit"]):
                continue
            ns, ne = dict(st), dict(en)
            if st["unit"] == segment_id:
                ns["offset"] = remap_start(ops, int(st["offset"]))
            if en["unit"] == segment_id:
                ne["offset"] = remap_end(ops, int(en["offset"]))
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
                changed.append(views.resolve_one(updated))
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


def record_from_utterances(store: Store, meeting_id: str, utterances: list[dict]) -> int:
    """올린 파일: 한 번 받아 적은 결과를 회의 기록으로. 화자는 목소리 묶음(A, B…)으로 넣어
    회의 중과 똑같이 칩이나 숫자키로 참석자를 고를 수 있게 한다. 기록이 이미 있으면 건드리지 않는다."""
    if store.list_live_segments(meeting_id):
        return 0
    letters: dict[str, str] = {}
    rows = []
    for u in utterances:
        if not clean_text(u.get("text", "")):
            continue
        label: Optional[str] = u.get("speaker")
        auto = None
        if label:
            auto = letters.setdefault(label, LETTERS[len(letters)] if len(letters) < len(LETTERS) else LETTERS[-1])
        rows.append({"t0": u["t0"], "t1": u["t1"], "text": clean_text(u["text"]), "speaker_auto": auto, "speaker_conf": None})
    return store.add_live_segments(meeting_id, rows)

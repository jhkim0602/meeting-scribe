"""저장소 데이터를 화면에 맞게 조합한다. 회의의 기록은 하나다 (실시간 기록, 올린 파일이면 한 번 받아 적은 기록)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional

from .anchors import TextIndex, resolve_annotations
from .db import Store


def speaker_name(speakers: dict, label: Optional[str]) -> Optional[str]:
    if label is None:
        return None
    if label in speakers and speakers[label]:
        return speakers[label]
    if label.startswith("S") and label[1:].isdigit():
        return f"화자 {label[1:]}"
    return label


class Views:
    def __init__(self, store: Store, cache_size: int = 16):
        self.store = store
        self._cache: OrderedDict[tuple, TextIndex] = OrderedDict()
        self._lock = threading.Lock()
        self._size = cache_size

    def units(self, meeting_id: str) -> list[dict]:
        out = []
        for s in self.store.list_live_segments(meeting_id):
            u = {"id": s["id"], "t0": s["t0"], "t1": s["t1"], "text": s["text"]}
            if s.get("text_orig") is not None:
                u["text_orig"] = s["text_orig"]  # 직접 고친 문장: 처음 받아 적은 글자
            out.append(u)
        return out

    def index(self, meeting_id: str) -> TextIndex:
        """기록의 글자 색인. 문장이 늘거나 고쳐지면 새로 만든다."""
        units = self.units(meeting_id)
        key = (meeting_id, len(units), units[-1]["id"] if units else "", hash(tuple(u["text"] for u in units if "text_orig" in u)))
        with self._lock:
            idx = self._cache.get(key)
            if idx is not None:
                self._cache.move_to_end(key)
                return idx
        idx = TextIndex(units)
        with self._lock:
            self._cache[key] = idx
            while len(self._cache) > self._size:
                self._cache.popitem(last=False)
        return idx

    def live_view(self, meeting_id: str) -> dict:
        idx = self.index(meeting_id)
        anns = resolve_annotations(self.store.list_annotations(meeting_id), idx)
        # 임시 화자는 자주 바뀌므로 색인과 따로 붙인다
        spk = {r["id"]: r for r in self.store.list_live_speakers(meeting_id)}
        units = []
        for u in idx.units:
            r = spk.get(u["id"]) or {}
            units.append(dict(u, speaker=r.get("speaker"), speaker_auto=r.get("speaker_auto"), speaker_src=r.get("speaker_src"), speaker_conf=r.get("speaker_conf")))
        return {"units": units, "annotations": anns}

    def resolve_one(self, ann: dict) -> dict:
        return resolve_annotations([ann], self.index(ann["meeting_id"]))[0]

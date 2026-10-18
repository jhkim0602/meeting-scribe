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

    @staticmethod
    def _unit(s: dict) -> dict:
        u = {"id": s["id"], "t0": s["t0"], "t1": s["t1"], "text": s["text"]}
        if s.get("text_orig") is not None:
            u["text_orig"] = s["text_orig"]  # 직접 고친 문장: 처음 받아 적은 글자
        return u

    def units(self, meeting_id: str) -> list[dict]:
        return [self._unit(s) for s in self.store.list_live_segments(meeting_id)]

    def index(self, meeting_id: str) -> TextIndex:
        """기록의 글자 색인. 문장이 늘거나 고쳐지면(기록이 바뀐 횟수가 오르면) 새로 만든다."""
        key = (meeting_id, self.store.record_rev(meeting_id))  # 횟수를 먼저 읽어야 낡은 기록이 새 횟수로 남지 않는다
        with self._lock:
            idx = self._cache.get(key)
            if idx is not None:
                self._cache.move_to_end(key)
                return idx
        idx = TextIndex(self.units(meeting_id))
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

    def local_index(self, meeting_id: str, unit_ids: list[str]) -> Optional[TextIndex]:
        """주어진 문장들만 덮는 작은 색인 (메모·댓글 하나를 다룰 때 기록 전체를 읽지 않는다). 없는 문장이 있으면 None."""
        span = self.store.live_segment_span(meeting_id, unit_ids)
        return TextIndex([self._unit(s) for s in span]) if span else None

    def resolve_one(self, ann: dict) -> dict:
        a = ann["anchor"]
        ids = [a["start"]["unit"], a["end"]["unit"]] if a.get("start") and a.get("end") else [a["after"]] if a.get("after") else []
        idx = self.local_index(ann["meeting_id"], ids) if ids else None
        return resolve_annotations([ann], idx or self.index(ann["meeting_id"]))[0]

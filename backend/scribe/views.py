"""저장소 데이터를 화면(실시간 기록/원본)에 맞게 조합한다."""

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

    def _cached(self, key: tuple, units: list[dict]) -> TextIndex:
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

    # ---------- 실시간 기록 ----------
    def live_units(self, meeting_id: str) -> list[dict]:
        return [{"id": s["id"], "t0": s["t0"], "t1": s["t1"], "text": s["text"]} for s in self.store.list_live_segments(meeting_id)]

    def live_index(self, meeting_id: str) -> TextIndex:
        units = self.live_units(meeting_id)
        key = ("live", meeting_id, len(units), units[-1]["id"] if units else "")
        return self._cached(key, units)

    # ---------- 원본 ----------
    def final_index(self, meeting_id: str) -> tuple[Optional[TextIndex], int, Optional[dict]]:
        final = self.store.get_final(meeting_id)
        if final is None:
            return None, 0, None
        version = int(final["version"])
        units = final["data"].get("utterances", [])
        return self._cached(("final", meeting_id, version), units), version, final

    def index_for(self, meeting_id: str, view: str) -> tuple[Optional[TextIndex], int]:
        if view == "final":
            idx, version, _ = self.final_index(meeting_id)
            return idx, version
        return self.live_index(meeting_id), 0

    def live_view(self, meeting_id: str) -> dict:
        idx = self.live_index(meeting_id)
        anns = resolve_annotations(self.store.list_annotations(meeting_id), idx, "live", 0)
        return {"units": idx.units, "annotations": anns}

    def final_view(self, meeting_id: str) -> Optional[dict]:
        idx, version, final = self.final_index(meeting_id)
        if idx is None or final is None:
            return None
        anns = resolve_annotations(self.store.list_annotations(meeting_id), idx, "final", version)
        data = final["data"]
        return {
            "version": version,
            "created_at": final["created_at"],
            "engine": final.get("engine"),
            "diarizer": final.get("diarizer"),
            "units": idx.units,
            "speakers": data.get("speakers", []),
            "duration": data.get("duration"),
            "annotations": anns,
        }

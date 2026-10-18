"""제목/회의 기록/노트 전체 검색."""

from __future__ import annotations

from .views import Views


def _snippet(text: str, q: str, width: int = 60) -> str:
    i = text.lower().find(q.lower())
    if i < 0:
        return text[:width * 2]
    a = max(0, i - width)
    b = min(len(text), i + len(q) + width)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


KIND_ORDER = {"title": 0, "live": 1, "notes": 2}


def search_all(views: Views, q: str, limit: int = 30) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    if views.store.fts:
        return _search_index(views, q, limit)
    return _search_scan(views, q, limit)


def _search_index(views: Views, q: str, limit: int) -> list[dict]:
    """FTS5 색인으로 찾는다 (제목, 회의 기록, 노트)."""
    store = views.store
    meetings = store.list_meetings()
    rank = {m["id"]: i for i, m in enumerate(meetings)}
    by_id = {m["id"]: m for m in meetings}
    rows = [r for r in store.search_rows(q, limit=max(300, limit * 10)) if r["meeting_id"] in by_id]
    rows.sort(key=lambda r: (rank[r["meeting_id"]], KIND_ORDER.get(r["kind"], 3), r["t"] if r["t"] is not None else 0))
    out = []
    for r in rows[:limit]:
        title = by_id[r["meeting_id"]]["title"] or "제목 없는 회의"
        item = {"meeting_id": r["meeting_id"], "title": title, "kind": r["kind"], "t": r["t"],
                "snippet": title if r["kind"] == "title" else _snippet(r["text"], q)}
        if r["ref"]:
            item["unit"] = r["ref"]
        out.append(item)
    return out


def _search_scan(views: Views, q: str, limit: int) -> list[dict]:
    store = views.store
    out: list[dict] = []
    ql = q.lower()
    for m in store.list_meetings():
        mid, title = m["id"], m["title"] or "제목 없는 회의"
        if ql in (m["title"] or "").lower():
            out.append({"meeting_id": mid, "title": title, "kind": "title", "t": None, "snippet": title})
        for s in store.list_live_segments(mid):
            if ql in s["text"].lower():
                out.append({"meeting_id": mid, "title": title, "kind": "live", "t": s["t0"], "unit": s["id"], "snippet": _snippet(s["text"], q)})
        full = store.get_meeting(mid, with_notes=True) or {}
        notes = full.get("notes_md") or ""
        if ql in notes.lower():
            out.append({"meeting_id": mid, "title": title, "kind": "notes", "t": None, "snippet": _snippet(notes, q)})
        if len(out) >= limit:
            break
    return out[:limit]

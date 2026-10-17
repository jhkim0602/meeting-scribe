"""제목/실시간 기록/원본/노트 전체 검색."""

from __future__ import annotations

from .views import Views


def _snippet(text: str, q: str, width: int = 60) -> str:
    i = text.lower().find(q.lower())
    if i < 0:
        return text[:width * 2]
    a = max(0, i - width)
    b = min(len(text), i + len(q) + width)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


def search_all(views: Views, q: str, limit: int = 30) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    store = views.store
    out: list[dict] = []
    ql = q.lower()
    for m in store.list_meetings():
        mid, title = m["id"], m["title"] or "제목 없는 회의"
        if ql in (m["title"] or "").lower():
            out.append({"meeting_id": mid, "title": title, "kind": "title", "t": None, "snippet": title})
        final = store.get_final(mid)
        if final:
            for u in final["data"].get("utterances", []):
                if ql in u["text"].lower():
                    out.append({"meeting_id": mid, "title": title, "kind": "final", "t": u["t0"], "unit": u["id"], "snippet": _snippet(u["text"], q)})
        else:
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

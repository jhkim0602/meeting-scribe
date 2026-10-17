"""MCP 서버 (stdio). Claude Desktop/Claude Code 등 사용자가 구독한 AI가 회의 기록을 읽고 요약을 저장한다.

실행: python -m scribe.mcp_server   (scripts/mcp.sh 참고)
"""

from __future__ import annotations

import datetime as _dt
import time
from typing import Optional

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore

from .config import settings
from .db import Store
from .export import LEGEND, meeting_markdown, transcript_markdown
from .search import search_all
from .textutil import fmt_time
from .views import Views, speaker_name
from .voices import learn_from_meeting

INSTRUCTIONS = """\
로컬 회의 기록 앱(Meeting Scribe)의 데이터에 접근합니다.
- 회의를 요약할 때는 get_meeting_bundle로 노트·전사·첨삭을 한 번에 읽으세요.
- 사용자가 회의 중 남긴 메모, 댓글, 수정, 강조는 사용자가 중요하다고 표시한 부분이므로 요약에 우선 반영하세요.
- 원본(후처리)이 있으면 화자 구분이 된 원본을, 없으면 실시간 기록을 사용합니다.
- 결과 요약은 save_summary로 저장하면 앱의 'AI 요약' 탭에 표시됩니다.
- meeting_id 자리에 'latest'를 넣으면 가장 최근 회의를 뜻합니다.
"""

settings.ensure_dirs()
_store = Store(settings.db_path)
_views = Views(_store)
server = _Server("meeting-scribe", instructions=INSTRUCTIONS)

STATUS_KO = {"empty": "빈 회의", "recording": "녹음 중", "recorded": "녹음됨(원본 전)", "processing": "원본 만드는 중", "ready": "원본 완료", "error": "오류"}


def _resolve_id(meeting_id: str) -> Optional[str]:
    if meeting_id in ("latest", "최근", "last", ""):
        ms = _store.list_meetings(limit=1)
        return ms[0]["id"] if ms else None
    return meeting_id if _store.get_meeting(meeting_id) else None


def _cut(text: str, max_chars: int, units: Optional[list[dict]] = None) -> str:
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    cut = cut if cut > 0 else max_chars
    head = text[:cut]
    hint = ""
    last_line = head.rsplit("\n", 1)[-1]
    if units:
        import re

        m = re.match(r"\[(\d+):(\d+)(?::(\d+))?\]", last_line)
        if m:
            parts = [int(x) for x in m.groups() if x is not None]
            secs = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
            nxt = next((u["t0"] for u in units if u["t0"] > secs), None)
            if nxt is not None:
                hint = f" 이어서 읽으려면 start_seconds={int(nxt)}로 다시 호출하세요."
    return head + f"\n\n… (길이 제한으로 잘림, 전체 {len(text):,}자.{hint})"


@server.tool()
def list_meetings(query: str = "", limit: int = 20) -> str:
    """회의 목록을 최신순으로 보여줍니다. query로 제목 검색."""
    rows = _store.list_meetings(query or None, limit=limit)
    if not rows:
        return "회의가 없습니다."
    lines = ["| 회의 ID | 제목 | 일시 | 길이 | 상태 | 요약 |", "|---|---|---|---|---|---|"]
    for m in rows:
        when = _dt.datetime.fromtimestamp(m["created_at"]).strftime("%Y-%m-%d %H:%M")
        summary = "있음" if m.get("summary_md") else "-"
        lines.append(f"| {m['id']} | {m['title'] or '제목 없는 회의'} | {when} | {fmt_time(m['duration'] or 0, True)} | {STATUS_KO.get(m['status'], m['status'])} | {summary} |")
    return "\n".join(lines)


@server.tool()
def get_meeting_bundle(meeting_id: str = "latest", version: str = "auto", start_seconds: Optional[float] = None, end_seconds: Optional[float] = None, max_chars: int = 80000) -> str:
    """회의 하나의 전체 자료(메타데이터, 내 노트, 첨삭이 표시된 전사)를 Markdown으로 돌려줍니다.
    요약/회의록 작성에 가장 먼저 쓰세요. version: auto|final|live. 길면 start_seconds로 나눠 읽으세요."""
    mid = _resolve_id(meeting_id)
    if mid is None:
        return f"회의를 찾을 수 없습니다: {meeting_id}"
    md = meeting_markdown(_views, mid, version=version, include_notes=start_seconds is None, start=start_seconds, end=end_seconds)
    units = (_views.final_view(mid) or {}).get("units") or _views.live_units(mid)
    return _cut(md or "", max_chars, units)


@server.tool()
def get_transcript(meeting_id: str = "latest", version: str = "auto", include_annotations: bool = True, start_seconds: Optional[float] = None, end_seconds: Optional[float] = None, max_chars: int = 80000) -> str:
    """전사만 돌려줍니다. 각 줄: [시각] 화자: 내용. include_annotations=True면 사용자 첨삭 표시 포함."""
    mid = _resolve_id(meeting_id)
    if mid is None:
        return f"회의를 찾을 수 없습니다: {meeting_id}"
    meeting = _store.get_meeting(mid) or {}
    final = _views.final_view(mid) if version != "live" else None
    if final:
        units, anns = final["units"], final["annotations"]
    else:
        lv = _views.live_view(mid)
        units, anns = lv["units"], lv["annotations"]
    body = transcript_markdown(units, anns, meeting.get("speakers") or {}, include_annotations=include_annotations, start=start_seconds, end=end_seconds)
    head = (LEGEND + "\n\n") if include_annotations else ""
    return _cut(head + (body or "(기록 없음)"), max_chars, units)


@server.tool()
def get_notes(meeting_id: str = "latest") -> str:
    """사용자가 오른쪽 노트 영역에 직접 쓴 필기를 Markdown으로 돌려줍니다. [mm:ss]는 회의 시각입니다."""
    mid = _resolve_id(meeting_id)
    if mid is None:
        return f"회의를 찾을 수 없습니다: {meeting_id}"
    m = _store.get_meeting(mid, with_notes=True) or {}
    return m.get("notes_md") or "(작성한 노트 없음)"


@server.tool()
def get_annotations(meeting_id: str = "latest") -> str:
    """사용자가 기록 위에 남긴 메모/댓글/수정/강조를 시간순 목록으로 돌려줍니다."""
    mid = _resolve_id(meeting_id)
    if mid is None:
        return f"회의를 찾을 수 없습니다: {meeting_id}"
    anns = sorted(_store.list_annotations(mid), key=lambda a: a["anchor"].get("t0", a["anchor"].get("t", 0)))
    if not anns:
        return "(첨삭 없음)"
    label = {"comment": "댓글", "memo": "메모", "edit": "수정", "highlight": "강조", "divider": "구분선"}
    lines = []
    for a in anns:
        anc = a["anchor"]
        t = anc.get("t0", anc.get("t", 0))
        quote = f' "{anc["quote"]}"' if anc.get("quote") else ""
        if a["kind"] == "divider":
            from .export import divider_text

            lines.append(f"- [{fmt_time(t)}] ──── {divider_text(a)} ────")
            continue
        if a["kind"] == "edit":
            detail = f' → "{a.get("replacement") or ""}" ({ {"accepted": "적용됨", "dismissed": "무시됨"}.get(a["status"], "제안") })'
        else:
            detail = f": {a['body']}" if a.get("body") else ""
        replies = "".join(f"\n    - 답글: {r.get('text', '')}" for r in a.get("replies") or [])
        lines.append(f"- [{fmt_time(t)}] {label.get(a['kind'], a['kind'])}{quote}{detail}{replies}")
    return "\n".join(lines)


@server.tool()
def search_meetings(query: str, limit: int = 20) -> str:
    """모든 회의의 제목, 기록, 노트에서 검색합니다."""
    hits = search_all(_views, query, limit=limit)
    if not hits:
        return "검색 결과가 없습니다."
    kind = {"title": "제목", "final": "원본", "live": "실시간", "notes": "노트"}
    return "\n".join(
        f"- {h['title']} ({h['meeting_id']}) · {kind.get(h['kind'], h['kind'])}" + (f" [{fmt_time(h['t'])}]" if h.get("t") is not None else "") + f": {h['snippet']}"
        for h in hits
    )


@server.tool()
def save_summary(meeting_id: str, markdown: str) -> str:
    """회의 요약(Markdown)을 저장합니다. 앱의 'AI 요약' 탭에 바로 표시됩니다. 기존 요약은 덮어씁니다."""
    mid = _resolve_id(meeting_id)
    if mid is None:
        return f"회의를 찾을 수 없습니다: {meeting_id}"
    _store.update_meeting(mid, summary_md=markdown, summary_updated_at=time.time())
    m = _store.get_meeting(mid) or {}
    return f"저장했습니다: {m.get('title') or mid} ({len(markdown):,}자)"


@server.tool()
def rename_speakers(meeting_id: str, names: dict[str, str]) -> str:
    """화자 라벨(S1, S2 …)에 실제 이름을 붙입니다. 예: {"S1": "김민지", "S2": "박준호"}"""
    mid = _resolve_id(meeting_id)
    if mid is None:
        return f"회의를 찾을 수 없습니다: {meeting_id}"
    m = _store.get_meeting(mid) or {}
    speakers = dict(m.get("speakers") or {})
    speakers.update({k: v for k, v in names.items() if k})
    _store.update_meeting(mid, speakers=speakers)
    learn_from_meeting(_store, mid)
    return "화자 이름: " + ", ".join(f"{k}={speaker_name(speakers, k)}" for k in sorted(speakers))


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()

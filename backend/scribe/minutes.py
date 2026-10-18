"""회의록 HTML: 회의 정보, 결정 사항, 할 일, 논의, 내 노트, 회의 중 메모·댓글을 한 장의 문서로.

파일 하나로 열리고(글꼴은 인터넷이 있으면 Pretendard, 없으면 시스템 글꼴), 인쇄하면 A4에 맞는다.
같은 폴더에 녹음 파일이 있으면 시각을 눌러 그 부분을 들을 수 있다.
원본 전사는 넣지 않는다 (요약과 사람이 쓴 것만).
"""

from __future__ import annotations

import datetime as _dt
import html
import re
from typing import Any, Optional

from .db import Store
from .textutil import fmt_time
from .views import Views

WEEKDAYS = "월화수목금토일"
# 글꼴은 기다리지 않는다: 시스템 글꼴로 먼저 보여 주고, 받아지면 바뀐다 (긴 문서도 바로 뜨게, 인터넷이 없어도)
FONT_CSS = "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"
MARKS_ALL_MAX = 40  # 회의록에 메모·댓글을 모두 싣는 최대 개수 (넘으면 메모만, 댓글은 전사 기록에서)
TIME_RE = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def _secs(label: str) -> float:
    parts = [int(p) for p in label.split(":")]
    s = 0
    for p in parts:
        s = s * 60 + p
    return float(s)


def _chip(t: float) -> str:
    return f'<button class="t" data-t="{t:.1f}" type="button">{_esc(fmt_time(t))}</button>'


# ---------------- 인라인 (요약 Markdown) ----------------

def _inline_md(text: str) -> str:
    """요약 Markdown 한 줄: **굵게**, *기울임*, `코드`, [링크](url), [mm:ss] 시각"""
    s = _esc(text)
    s = TIME_RE.sub(lambda m: _chip(_secs(m.group(1))), s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    return s


def _split_times(text: str) -> tuple[str, list[float]]:
    """줄 안의 [mm:ss]를 빼서 따로 돌려준다 (오른쪽 열에 모아 보여 주려고)"""
    times = [_secs(m.group(1)) for m in TIME_RE.finditer(text)]
    rest = TIME_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", rest).strip(" ,·"), times


def _times_cell(times: list[float]) -> str:
    return "".join(_chip(t) for t in times[:2])


def _topic_line(text: str) -> str:
    """'주제: 내용' 꼴이면 주제를 굵게 (훑어보기 쉽게)"""
    m = re.match(r"^\s*([^:：\[]{1,24})[:：]\s*(.+)$", text)
    if m and "**" not in m.group(1):
        return f'<span class="topic">{_esc(m.group(1).strip())}</span>{_inline_md(m.group(2))}'
    return _inline_md(text)


# ---------------- 요약 Markdown → 구역 ----------------

def parse_summary(md: str) -> dict[str, list[str]]:
    """'## 제목' 단위로 나눈다. {제목: [줄, …]}"""
    out: dict[str, list[str]] = {}
    cur = None
    for line in (md or "").splitlines():
        h = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if h:
            cur = h.group(1).strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
        elif line.strip():
            out.setdefault("", []).append(line)
    return out


def _find(sections: dict, *names: str) -> Optional[list[str]]:
    for key, lines in sections.items():
        k = key.replace(" ", "")
        if any(n.replace(" ", "") in k for n in names):
            return lines
    return None


def _md_blocks(lines: list[str], topic: bool = False) -> str:
    """요약 구역의 본문: 목록(들여쓰기), 표, 문단"""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                head, body = rows[0], rows[1:]
                out.append('<table class="grid"><thead><tr>' + "".join(f"<th>{_inline_md(c)}</th>" for c in head) + "</tr></thead><tbody>"
                           + "".join("<tr>" + "".join(f"<td>{_inline_md(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")
            continue
        m = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if m:
            items: list[tuple[int, str]] = []
            while i < len(lines):
                mm = re.match(r"^(\s*)[-*]\s+(.*)$", lines[i])
                if not mm:
                    break
                items.append((len(mm.group(1)) // 2, mm.group(2)))
                i += 1
            out.append(_nested_list(items, topic))
            continue
        out.append(f"<p>{_inline_md(line.strip())}</p>")
        i += 1
    return "".join(out)


def _nested_list(items: list[tuple[int, str]], topic: bool) -> str:
    html_out, depth = [], -1
    for level, text in items:
        level = min(level, depth + 1)
        while depth < level:
            html_out.append("<ul>")
            depth += 1
        while depth > level:
            html_out.append("</li></ul>")
            depth -= 1
        if html_out and html_out[-1] not in ("<ul>",):
            html_out.append("</li>")
        box = re.match(r"^\[( |x|X)\]\s+(.*)$", text)
        if box:
            done = box.group(1).lower() == "x"
            body = f'<span class="box{" on" if done else ""}"></span>{_inline_md(box.group(2))}'
        else:
            body = _topic_line(text) if topic and level == 0 else _inline_md(text)
        html_out.append(f"<li>{body}")
    while depth >= 0:
        html_out.append("</li></ul>")
        depth -= 1
    return "".join(html_out)


def _items(lines: Optional[list[str]]) -> list[str]:
    return [m.group(1).strip() for m in (re.match(r"^\s*[-*]\s+(.*)$", ln) for ln in lines or []) if m and m.group(1).strip()]


def _table_rows(lines: Optional[list[str]]) -> list[list[str]]:
    rows = []
    for ln in lines or []:
        if ln.lstrip().startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                rows.append(cells)
    return rows


# ---------------- 노트 (BlockNote) → HTML ----------------

def _inline_blocks(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return _esc(content)
    out = []
    for it in content:
        kind = it.get("type")
        if kind == "text":
            t = _esc(it.get("text", ""))
            st = it.get("styles") or {}
            if st.get("code"):
                t = f"<code>{t}</code>"
            if st.get("bold"):
                t = f"<strong>{t}</strong>"
            if st.get("italic"):
                t = f"<em>{t}</em>"
            if st.get("underline"):
                t = f"<u>{t}</u>"
            if st.get("strike"):
                t = f"<s>{t}</s>"
            out.append(t)
        elif kind == "link":
            out.append(f'<a href="{_esc(it.get("href"))}">{_inline_blocks(it.get("content"))}</a>')
        elif kind == "timestamp":
            out.append(_chip(float((it.get("props") or {}).get("t", 0))))
        elif kind == "mention":
            out.append(f'<span class="mention">@{_esc((it.get("props") or {}).get("label"))}</span>')
        else:
            out.append(_inline_blocks(it.get("content")))
    return "".join(out)


def _plain(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(it.get("text", "") if it.get("type") == "text" else _plain(it.get("content") or []) for it in content or [])


def notes_html(blocks: list[dict]) -> str:
    out: list[str] = []
    i = 0
    while i < len(blocks or []):
        b = blocks[i]
        kind = b.get("type")
        if kind in ("bulletListItem", "numberedListItem", "checkListItem"):
            tag = "ol" if kind == "numberedListItem" else "ul"
            cls = ' class="checks"' if kind == "checkListItem" else ""
            items = []
            while i < len(blocks) and blocks[i].get("type") == kind:
                items.append(_li(blocks[i]))
                i += 1
            out.append(f"<{tag}{cls}>{''.join(items)}</{tag}>")
            continue
        out.append(_block(b))
        i += 1
    return "".join(x for x in out if x)


def _li(b: dict) -> str:
    inner = _inline_blocks(b.get("content"))
    if b.get("type") == "checkListItem":
        done = (b.get("props") or {}).get("checked")
        inner = f'<span class="box{" on" if done else ""}"></span><span class="{"done" if done else ""}">{inner}</span>'
    kids = notes_html(b.get("children") or [])
    return f"<li>{inner}{kids}</li>"


def _block(b: dict) -> str:
    kind = b.get("type")
    inner = _inline_blocks(b.get("content")) if not isinstance(b.get("content"), dict) else ""
    kids = notes_html(b.get("children") or [])
    if kind == "heading":
        level = min(3, max(1, int((b.get("props") or {}).get("level", 2))))
        return f'<h{level + 2} class="nh">{inner}</h{level + 2}>{kids}' if inner else kids
    if kind == "quote":
        return f"<blockquote>{inner}{kids}</blockquote>"
    if kind == "codeBlock":
        return f"<pre><code>{_esc(_plain(b.get('content')))}</code></pre>"
    if kind == "table":
        rows = (b.get("content") or {}).get("rows") or []
        trs = []
        for r_i, row in enumerate(rows):
            cells = [c.get("content") if isinstance(c, dict) and c.get("type") == "tableCell" else c for c in row.get("cells") or []]
            tag = "th" if r_i == 0 else "td"
            trs.append("<tr>" + "".join(f"<{tag}>{_inline_blocks(c)}</{tag}>" for c in cells) + "</tr>")
        return f'<table class="grid">{"".join(trs)}</table>'
    if kind == "image":
        url = (b.get("props") or {}).get("url")
        return f'<figure><img src="{_esc(url)}" alt=""></figure>' if url else ""
    if inner or kids:
        return f"<p>{inner}</p>{kids}" if inner else kids
    return ""


def _note_checks(blocks: list[dict]) -> list[dict]:
    """노트의 체크리스트 → 할 일 (요약이 없을 때). 시각은 따로 뺀다."""
    out = []
    for b in blocks or []:
        if b.get("type") == "checkListItem":
            content = b.get("content") or []
            times = [float((it.get("props") or {}).get("t", 0)) for it in content if isinstance(it, dict) and it.get("type") == "timestamp"]
            rest = [it for it in content if not (isinstance(it, dict) and it.get("type") == "timestamp")] if isinstance(content, list) else content
            out.append({"text_html": _inline_blocks(rest), "plain": _plain(rest), "times": times, "done": bool((b.get("props") or {}).get("checked"))})
        out.extend(_note_checks(b.get("children") or []))
    return out


# ---------------- 데이터 모으기 ----------------

def _initials(name: str) -> str:
    name = name.strip()
    return name[-2:] if re.fullmatch(r"[가-힣]{2,4}", name) else (name[:1].upper() or "?")


def collect(store: Store, views: Views, meeting_id: str) -> Optional[dict]:
    m = store.get_meeting(meeting_id, with_notes=True)
    if m is None:
        return None
    clock = m.get("clock") or []
    start = float(m.get("held_at") or (clock[0][1] if clock else m["created_at"]))
    duration = float(m.get("duration") or 0)
    people = people_of(m)
    if not people:
        people = [{"name": v} for v in (m.get("speakers") or {}).values() if v]
    participants = [p["name"] for p in people]
    final = views.final_view(meeting_id)
    view = final if final else views.live_view(meeting_id)
    marks = []
    for a in sorted(view["annotations"], key=lambda a: a["anchor"].get("t0", a["anchor"].get("t", 0))):
        if a["kind"] not in ("comment", "memo") or not (a.get("body") or "").strip():
            continue
        pos = a.get("pos") or {}
        quote = pos.get("text") if isinstance(pos, dict) and "text" in pos else a["anchor"].get("quote")
        marks.append({
            "kind": a["kind"], "t": float(a["anchor"].get("t0", a["anchor"].get("t", 0)) or 0),
            "body": a["body"], "quote": quote if a["kind"] == "comment" else None,
            "replies": [r["text"] for r in a.get("replies") or []], "resolved": a.get("status") == "resolved",
        })
    return {
        "id": m["id"], "title": m.get("title") or "제목 없는 회의", "start": start, "duration": duration,
        "place": m.get("place") or "", "participants": participants, "people": people, "summary": m.get("summary_md") or "",
        "notes": m.get("notes") or [], "marks": marks, "source": m.get("source"),
    }


# ---------------- 그리기 ----------------

def _facts(start: float, duration: float, place: str, extra: Optional[list[tuple[str, str]]] = None) -> str:
    a = _dt.datetime.fromtimestamp(start)
    b = a + _dt.timedelta(seconds=duration)
    mins = round(duration / 60)
    length = f"{mins // 60}시간 {mins % 60}분" if mins >= 60 else f"{mins}분"
    cells = [("일시", f"{a.year}. {a.month}. {a.day} ({WEEKDAYS[a.weekday()]})")]
    if duration >= 60:
        cells.append(("시간", f'{a:%H:%M} – {b:%H:%M}<span class="sub">{length}</span>'))
    else:
        cells.append(("시작", f"{a:%H:%M}"))
    if place:
        cells.append(("장소", _esc(place)))
    cells += extra or []
    return "".join(f'<div class="fact"><dt>{k}</dt><dd>{v}</dd></div>' for k, v in cells)


def _person(name: str, idx: int) -> str:
    return f'<span class="person p{idx % 6}"><span class="av">{_esc(_initials(name))}</span>{_esc(name)}</span>'


def _assignee(cell: str, people: list[str]) -> str:
    c = TIME_RE.sub("", cell).strip()
    if not c or c in ("미정", "-", "—"):
        return '<span class="tbd">미정</span>'
    for i, p in enumerate(people):
        if p and p in c:
            return _person(p, i)
    return _esc(c)


def _due(cell: str) -> str:
    c = cell.strip()
    return '<span class="tbd">미정</span>' if not c or c in ("미정", "-", "—") else _esc(c)


def _discussion(lines: list[str]) -> Optional[list[dict]]:
    """'- 주제: 내용 [mm:ss]' 목록 → 줄 [{topic, body, times, subs}]. 목록 꼴이 아니면 None."""
    rows: list[dict] = []
    for ln in lines:
        m = re.match(r"^(\s*)[-*]\s+(.*)$", ln)
        if not m:
            if ln.strip():
                return None
            continue
        if len(m.group(1)) < 2 or not rows:
            text, times = _split_times(m.group(2))
            tm = re.match(r"^\s*([^:：]{1,24})[:：]\s*(.+)$", text)
            topic, body = (tm.group(1).strip().strip("*"), tm.group(2)) if tm else ("", text)
            rows.append({"topic": topic, "body": body, "times": times, "subs": []})
        else:
            rows[-1]["subs"].append(m.group(2))
    return rows or None


ICON = {
    "check": '<svg viewBox="0 0 16 16"><path d="m3.5 8.3 2.8 2.8 6.2-6.4"/></svg>',
    "play": '<svg viewBox="0 0 16 16"><path d="M5 3.5v9l7-4.5-7-4.5Z"/></svg>',
}


def render(data: dict, audio_src: Optional[str] = None, generated: Optional[float] = None, tools: str = "") -> str:
    sections = parse_summary(data["summary"])
    people = data["participants"]
    lead_lines = _find(sections, "한 줄 요약", "한줄요약", "요약")
    lead = " ".join(ln.strip() for ln in lead_lines or [] if ln.strip() and not ln.strip().startswith("|"))
    lead = re.sub(r"^[-*]\s+", "", lead)
    decisions = _items(_find(sections, "결정"))
    discussion = _find(sections, "주요 논의", "논의")
    questions = _items(_find(sections, "남은 질문", "확인 필요", "질문"))
    action_rows = _table_rows(_find(sections, "할 일", "실행", "액션"))

    body: list[str] = []
    n = 0

    def section(title: str, inner: str, count: str = "", cls: str = "") -> None:
        nonlocal n
        n += 1
        cnt = f'<span class="cnt">{count}</span>' if count else ""
        body.append(f'<section class="sec {cls}"><h2><span class="no">{n:02d}</span><span class="ttl">{_esc(title)}</span>{cnt}</h2>{inner}</section>')

    def ref(times: list[float]) -> str:
        return f'<span class="ref">{_times_cell(times)}</span>'

    if decisions:
        lis = []
        for d in decisions:
            text, times = _split_times(d)
            lis.append(f'<li><span class="tick">{ICON["check"]}</span><span class="what">{_inline_md(text)}</span>{ref(times)}</li>')
        section("결정 사항", f'<ol class="decisions">{"".join(lis)}</ol>', f"{len(decisions)}건")

    # 할 일: 요약의 표, 없으면 노트의 체크리스트
    actions = []
    if action_rows:
        head = [h.replace(" ", "") for h in action_rows[0]]

        def col(*names: str) -> int:
            for i, h in enumerate(head):
                if any(nm in h for nm in names):
                    return i
            return -1

        ci, ca, cd, cr = col("할일", "내용", "항목"), col("담당"), col("기한", "마감"), col("근거", "시각")
        for r in action_rows[1:]:
            get = lambda k: r[k] if 0 <= k < len(r) else ""  # noqa: E731
            text, times = _split_times(get(ci if ci >= 0 else 0))
            times += _split_times(get(cr))[1] if cr >= 0 else []
            actions.append({"text": _inline_md(text), "times": times, "who": _assignee(get(ca), people), "due": _due(get(cd)), "done": False})
    else:
        for c in _note_checks(data["notes"]):
            who = next((i for i, p in enumerate(people) if p and p in c["plain"]), None)
            due = re.search(r"(\d{1,2}[/.]\d{1,2}|\d{1,2}월\s*\d{1,2}일|오늘|내일|이번\s*주|다음\s*주\s*[월화수목금]?요일?)", c["plain"])
            actions.append({"text": c["text_html"], "times": c["times"], "who": _person(people[who], who) if who is not None else '<span class="tbd">미정</span>',
                            "due": _esc(due.group(1)) if due else '<span class="tbd">미정</span>', "done": c["done"]})
    if actions:
        open_n = sum(1 for a in actions if not a["done"])
        rows = "".join(
            f'<tr class="{"done" if a["done"] else ""}"><td class="st"><span class="box{" on" if a["done"] else ""}"></span></td>'
            f'<td class="what">{a["text"]}</td><td class="who">{a["who"]}</td><td class="due">{a["due"]}</td><td class="rf">{_times_cell(a["times"])}</td></tr>'
            for a in actions
        )
        count = f"{len(actions)}건" if open_n == len(actions) else f"남은 일 {open_n}건 / {len(actions)}건"
        section("실행 항목", '<table class="actions"><colgroup><col class="c-st"><col><col class="c-who"><col class="c-due"><col class="c-rf"></colgroup>'
                f'<thead><tr><th></th><th>할 일</th><th>담당</th><th>기한</th><th class="rf">근거</th></tr></thead><tbody>{rows}</tbody></table>', count)

    if discussion and any(ln.strip() for ln in discussion):
        rows = _discussion(discussion)
        if rows:
            has_topic = any(r["topic"] for r in rows)
            lis = []
            for r in rows:
                subs = "".join(f"<li>{_inline_md(x)}</li>" for x in r["subs"])
                topic = f'<span class="dt">{_esc(r["topic"])}</span>' if has_topic else ""
                lis.append(f'<li>{topic}<div class="dd">{_inline_md(r["body"])}{f"<ul>{subs}</ul>" if subs else ""}</div>{ref(r["times"])}</li>')
            section("주요 논의", f'<ul class="discussion{"" if has_topic else " plain"}">{"".join(lis)}</ul>', f"{len(rows)}건")
        else:
            section("주요 논의", f'<div class="prose">{_md_blocks(discussion, topic=True)}</div>')

    notes = notes_html(data["notes"])
    if notes:
        section("노트", f'<div class="prose notes">{notes}</div>')

    if data["marks"]:
        # 거의 문장마다 댓글을 단 회의는 회의록에 다 실으면 전사 기록만큼 길어진다: 메모만 싣고 댓글은 전사 기록으로
        shown = data["marks"]
        n_cmt = sum(1 for mk in shown if mk["kind"] == "comment")
        hint = ""
        if len(shown) > MARKS_ALL_MAX and n_cmt:
            shown = [mk for mk in shown if mk["kind"] == "memo"]
            hint = f'<p class="count">댓글 {n_cmt:,}개는 전사 기록에서 그 문장과 함께 볼 수 있어요.</p>'
        items = []
        for mk in shown:
            quote = f'<p class="q">{_esc(mk["quote"])}</p>' if mk.get("quote") else ""
            replies = "".join(f'<p class="reply">{_esc(r)}</p>' for r in mk["replies"])
            tag = '<span class="tag memo">메모</span>' if mk["kind"] == "memo" else ('<span class="tag ok">해결</span>' if mk["resolved"] else '<span class="tag">댓글</span>')
            items.append(f'<li><span class="at">{_chip(mk["t"])}</span><div class="mk">{quote}<p class="c">{_esc(mk["body"])}{tag}</p>{replies}</div></li>')
        if shown is data["marks"]:
            section("회의 중 메모·댓글", f'<ul class="marks">{"".join(items)}</ul>', f"{len(shown):,}건")
        else:
            section("회의 중 메모", f'{hint}<ul class="marks">{"".join(items)}</ul>', f"{len(shown):,}건")

    if questions:
        lis = []
        for i, q in enumerate(questions, 1):
            text, times = _split_times(q)
            lis.append(f'<li><span class="qn">Q{i}</span><span class="what">{_inline_md(text)}</span>{ref(times)}</li>')
        section("남은 질문", f'<ol class="questions">{"".join(lis)}</ol>', f"{len(questions)}건")

    if not body:
        body.append('<p class="empty">아직 정리된 내용이 없어요. 노트를 쓰거나 AI 요약을 만들면 여기에 담겨요.</p>')

    summary = f'<div class="summary"><span class="lbl">핵심 요약</span><p>{_inline_md(lead)}</p></div>' if lead else ""
    return page(
        data, brand=("회의록", "Meeting Minutes", "MN"), facts=_facts(data["start"], data["duration"], data["place"]),
        body=summary + "".join(body), audio_src=audio_src, generated=generated, tools=tools,
    )


def people_of(m: dict) -> list[dict]:
    """참석자와 세부 정보 (소속·직무·메모)"""
    return [{k: p.get(k) or "" for k in ("name", "org", "role", "note")} for p in m.get("participants") or []]


def attendees_html(people: list[dict], label: str = "참석자") -> str:
    """참석자: 소속·직무·메모가 하나라도 있으면 표로, 없으면 이름만 한 줄로"""
    if not people:
        return ""
    head = f'<span class="lbl">{label}<b>{len(people)}</b></span>'
    if not any(p.get("org") or p.get("role") or p.get("note") for p in people):
        return f'<div class="attendees">{head}<span class="people">{"".join(_person(p["name"], i) for i, p in enumerate(people))}</span></div>'

    def cell(v: str) -> str:
        return _esc(v) if v else '<span class="tbd">—</span>'

    rows = "".join(
        f'<tr><td class="pn">{_person(p["name"], i)}</td><td>{cell(p.get("org", ""))}</td><td>{cell(p.get("role", ""))}</td>'
        f'<td class="pm">{cell(p.get("note", ""))}</td></tr>'
        for i, p in enumerate(people)
    )
    return (f'<div class="attendees detail">{head}<table class="people-table"><colgroup><col class="c-pn"><col class="c-org"><col class="c-role"><col></colgroup>'
            f'<thead><tr><th>이름</th><th>소속</th><th>직무</th><th>메모</th></tr></thead><tbody>{rows}</tbody></table></div>')


def tools_html(meeting_id: str, active: str) -> str:
    """앱에서 열었을 때만 보이는 위쪽 줄: 전사 기록 ↔ 회의록, 인쇄, 파일로 받기 (받은 파일·인쇄에는 없음)"""
    tabs = [("transcript", "전사 기록"), ("minutes", "회의록")]
    seg = "".join(f'<a href="/api/meetings/{_esc(meeting_id)}/{k}.html" class="{"on" if k == active else ""}">{v}</a>' for k, v in tabs)
    return (f'<nav class="apptools"><div class="seg">{seg}</div><span class="sp"></span>'
            f'<button type="button" onclick="window.print()">인쇄 · PDF</button>'
            f'<a class="btn" href="/api/meetings/{_esc(meeting_id)}/{active}.html?download=1">파일로 받기</a></nav>')


def page(data: dict, *, brand: tuple[str, str, str], facts: str, body: str, audio_src: Optional[str] = None,
         generated: Optional[float] = None, tools: str = "", people_label: str = "참석자", extra_css: str = "",
         extra_js: str = "", wide: bool = False) -> str:
    """회의록·전사 기록이 함께 쓰는 문서 틀: 문서 머리, 제목, 정보 띠, 참석자, 본문, 바닥글, 듣기 막대"""
    ko, en, prefix = brand
    gen = _dt.datetime.fromtimestamp(generated or _dt.datetime.now().timestamp())
    doc_no = f"{prefix}-{_dt.datetime.fromtimestamp(data['start']):%Y%m%d}-{data['id'][-4:].upper()}"
    player = ""
    if audio_src:
        player = (f'<div class="player" id="player" hidden><button type="button" class="pp" id="pp" aria-label="재생">{ICON["play"]}</button>'
                  f'<span class="pl-label">녹음</span><span class="pl-time" id="pt">00:00</span>'
                  f'<audio id="au" preload="metadata" src="{_esc(audio_src)}"></audio></div>')
    cls = " ".join(c for c in ("paper", "has-audio" if audio_src else "", "wide" if wide else "") if c)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(data["title"])} · {ko}</title>
<link rel="stylesheet" media="print" onload="this.media='all'" href="{FONT_CSS}">
<noscript><link rel="stylesheet" href="{FONT_CSS}"></noscript>
<style>{CSS}{extra_css}</style>
</head>
<body{' class="in-app"' if tools else ""}>
{tools}
<main class="{cls}">
  <div class="doc-top"><span class="brand">{ko}<span class="en">{en}</span></span><span class="doc-no">{doc_no}</span></div>
  <header class="head">
    <h1>{_esc(data["title"])}</h1>
    <dl class="facts">{facts}</dl>
    {attendees_html(data.get("people") or [{"name": n} for n in data["participants"]], people_label)}
  </header>
  {body}
  <footer class="foot"><span>회의 노트로 작성</span><span>{gen.year}. {gen.month}. {gen.day}. {gen:%H:%M} 생성</span></footer>
  {player}
</main>
<script>{JS}{extra_js}</script>
</body>
</html>
"""


CSS = r"""
:root {
  --ink: #0f172a; --text: #2a303b; --muted: #687080; --faint: #a0a6b0; --line: #e4e7eb; --line-2: #eff1f4;
  --bg: #eef0f3; --paper: #ffffff; --soft: #f6f7f9; --accent: #2f45b8; --accent-soft: #eef1fb; --ok: #17803f; --ok-soft: #e9f6ee;
  --mono: "SF Mono", ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: "Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", "Segoe UI", sans-serif;
  font-size: 14.5px; line-height: 1.7; letter-spacing: -0.006em; word-break: keep-all; overflow-wrap: anywhere;
  -webkit-font-smoothing: antialiased; font-feature-settings: "tnum" 0;
}
.paper {
  position: relative; max-width: 860px; margin: 48px auto; padding: 40px 68px 36px; background: var(--paper);
  border-radius: 4px; box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.04), 0 2px 4px rgba(15, 23, 42, 0.03), 0 12px 32px rgba(15, 23, 42, 0.06);
}
.paper.has-audio { padding-bottom: 92px; }
.paper.wide { max-width: 1100px; }

/* 앱에서 열었을 때 위쪽 줄 */
.apptools { position: sticky; top: 0; z-index: 20; display: flex; align-items: center; gap: 8px; height: 52px; padding: 0 20px; background: rgba(238, 240, 243, 0.86); backdrop-filter: saturate(1.4) blur(10px); -webkit-backdrop-filter: saturate(1.4) blur(10px); }
.apptools .sp { flex: 1; }
.apptools .seg { display: inline-flex; padding: 3px; background: #e2e5ea; border-radius: 8px; }
.apptools .seg a { padding: 4px 14px; border: 0; border-radius: 6px; font-size: 13px; font-weight: 500; color: var(--muted); }
.apptools .seg a.on { background: var(--paper); color: var(--ink); box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08); }
.apptools button, .apptools .btn { display: inline-flex; align-items: center; height: 32px; padding: 0 12px; border: 1px solid var(--line); border-radius: 7px; background: var(--paper); color: var(--ink); font-family: inherit; font-size: 13px; font-weight: 500; line-height: 1; cursor: pointer; }
.apptools button:hover, .apptools .btn:hover { border-color: #c9ced6; }
.in-app .paper { margin-top: 8px; }

/* 문서 머리 */
.doc-top { display: flex; justify-content: space-between; align-items: center; padding-bottom: 12px; border-bottom: 2px solid var(--ink); font-size: 12px; }
.brand { font-weight: 700; color: var(--ink); letter-spacing: 0.02em; }
.brand .en { margin-left: 10px; padding-left: 10px; border-left: 1px solid var(--line); font-weight: 500; color: var(--faint); letter-spacing: 0.14em; text-transform: uppercase; font-size: 10.5px; }
.doc-no { color: var(--faint); font-family: var(--mono); font-size: 11px; letter-spacing: 0.02em; }
.head { padding: 30px 0 0; }
h1 { margin: 0 0 22px; font-size: 27px; line-height: 1.32; font-weight: 700; letter-spacing: -0.03em; color: var(--ink); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin: 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.fact { padding: 12px 18px 13px; border-left: 1px solid var(--line-2); }
.fact:first-child { padding-left: 0; border-left: 0; }
.fact dt { margin-bottom: 3px; font-size: 11.5px; font-weight: 500; color: var(--muted); }
.fact dd { margin: 0; font-size: 14.5px; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.sub { margin-left: 8px; color: var(--muted); font-weight: 400; font-size: 13px; }
.attendees { display: flex; align-items: baseline; gap: 18px; padding: 12px 0 13px; border-bottom: 1px solid var(--line); }
.lbl { flex: none; font-size: 11.5px; font-weight: 500; color: var(--muted); }
.attendees .lbl b { margin-left: 5px; color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.attendees.detail { display: block; padding-bottom: 4px; }
.people-table { margin-top: 8px; font-size: 13.5px; table-layout: fixed; }
.people-table .c-pn { width: 132px; } .people-table .c-org { width: 150px; } .people-table .c-role { width: 130px; }
.people-table th { padding: 6px 12px 6px 0; text-align: left; font-size: 11.5px; font-weight: 500; color: var(--faint); border-bottom: 1px solid var(--line-2); }
.people-table td { padding: 8px 12px 8px 0; vertical-align: top; border-bottom: 1px solid var(--line-2); color: var(--ink); }
.people-table tr:last-child td { border-bottom: 0; }
.people-table .pm { color: var(--text); }
.people { display: flex; flex-wrap: wrap; gap: 6px 16px; }
.person { display: inline-flex; align-items: center; gap: 7px; font-weight: 500; color: var(--ink); white-space: nowrap; }
.av { display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; font-size: 9.5px; font-weight: 700; letter-spacing: -0.04em; color: #fff; }
.p0 .av, .av.p0 { background: #2f45b8; } .p1 .av, .av.p1 { background: #b4532a; } .p2 .av, .av.p2 { background: #1f7a4d; }
.p3 .av, .av.p3 { background: #6d4bc4; } .p4 .av, .av.p4 { background: #a8395f; } .p5 .av, .av.p5 { background: #1f6f86; }

/* 핵심 요약 */
.summary { margin: 26px 0 0; padding: 18px 22px 19px; background: var(--soft); border-radius: 6px; }
.summary .lbl { display: block; margin-bottom: 6px; color: var(--accent); font-weight: 600; letter-spacing: 0.02em; }
.summary p { margin: 0; font-size: 15.5px; line-height: 1.7; color: var(--ink); letter-spacing: -0.012em; }

/* 구역 */
.sec { margin-top: 40px; }
.sec h2 { display: flex; align-items: baseline; gap: 10px; margin: 0 0 6px; padding-bottom: 10px; border-bottom: 1px solid var(--ink); font-size: 15.5px; font-weight: 700; color: var(--ink); letter-spacing: -0.015em; break-after: avoid; }
.sec h2 .no { font-family: var(--mono); font-size: 11.5px; font-weight: 600; color: var(--accent); letter-spacing: 0.02em; }
.sec h2 .cnt { margin-left: auto; font-size: 12px; font-weight: 500; color: var(--muted); font-variant-numeric: tabular-nums; }

/* 줄 목록 공통: [표시] 내용 [근거] */
.decisions, .discussion, .questions, .marks { list-style: none; margin: 0; padding: 0; }
.decisions > li, .discussion > li, .questions > li { display: flex; align-items: flex-start; gap: 14px; padding: 12px 0; border-bottom: 1px solid var(--line-2); break-inside: avoid; }
.what, .dd { flex: 1; min-width: 0; }
.ref { flex: none; display: flex; gap: 4px; justify-content: flex-end; min-width: 50px; padding-top: 2px; }
.tick { flex: none; display: grid; place-items: center; width: 20px; height: 20px; margin-top: 2px; border-radius: 50%; background: var(--ok-soft); }
.tick svg { width: 12px; height: 12px; fill: none; stroke: var(--ok); stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; }
.decisions .what { color: var(--ink); font-weight: 500; }
.discussion .dt { flex: none; width: 112px; font-weight: 650; color: var(--ink); }
.discussion .dd ul { margin: 6px 0 0; padding-left: 16px; color: var(--muted); font-size: 13.5px; }
.discussion .dd li { margin: 2px 0; } .discussion .dd li::marker { color: var(--faint); }
.qn { flex: none; width: 28px; margin-top: 1px; font-family: var(--mono); font-size: 12px; font-weight: 600; color: var(--accent); }

/* 실행 항목 */
table { border-collapse: collapse; width: 100%; }
.actions { font-size: 14px; table-layout: fixed; }
.actions .c-st { width: 30px; } .actions .c-who { width: 116px; } .actions .c-due { width: 112px; } .actions .c-rf { width: 66px; }
.actions th { padding: 9px 10px 8px; text-align: left; font-size: 11.5px; font-weight: 500; color: var(--muted); border-bottom: 1px solid var(--line); }
.actions th:first-child, .actions td:first-child { padding-left: 0; }
.actions td { padding: 12px 10px; vertical-align: top; border-bottom: 1px solid var(--line-2); }
.actions tr { break-inside: avoid; }
.actions .what { color: var(--ink); }
.actions .who { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.actions .due { white-space: nowrap; color: var(--ink); font-variant-numeric: tabular-nums; }
.actions .rf { text-align: right; padding-right: 0; }
.actions tr.done .what { color: var(--faint); text-decoration: line-through; text-decoration-color: #c9cdd3; }
.box { display: inline-block; flex: none; width: 15px; height: 15px; border: 1.5px solid #c3c8cf; border-radius: 4px; }
.actions .box { margin-top: 4px; }
.box.on { border-color: var(--ok); background: var(--ok) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='m3.5 8.3 2.8 2.8 6.2-6.4' fill='none' stroke='white' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") center/11px no-repeat; }
.tbd { color: var(--faint); font-weight: 400; }

/* 노트 */
.prose { padding-top: 10px; }
.prose p { margin: 0 0 10px; }
.prose ul, .prose ol { margin: 0 0 12px; padding-left: 20px; }
.prose li { margin: 3px 0; }
.prose li::marker { color: #b0b6be; }
.prose ul.checks { list-style: none; padding-left: 0; }
.prose ul.checks li { display: flex; align-items: flex-start; gap: 9px; }
.prose ul.checks .box { margin-top: 5px; }
.prose .done { color: var(--faint); text-decoration: line-through; text-decoration-color: #c9cdd3; }
.nh { margin: 24px 0 8px; color: var(--ink); letter-spacing: -0.015em; }
h3.nh { font-size: 16.5px; } h4.nh { font-size: 15px; } h5.nh { font-size: 14.5px; }
.notes > .nh:first-child { margin-top: 4px; }
blockquote { margin: 12px 0 14px; padding: 2px 0 2px 16px; border-left: 2px solid var(--ink); color: #444b57; }
pre { margin: 0 0 12px; padding: 12px 14px; background: var(--soft); border-radius: 6px; font-size: 12.5px; overflow-x: auto; }
code { font-family: var(--mono); font-size: 0.86em; padding: 1px 5px; background: var(--soft); border-radius: 4px; }
pre code { padding: 0; background: none; }
.grid { margin: 6px 0 16px; font-size: 13.5px; }
.grid th, .grid td { padding: 8px 12px; border-bottom: 1px solid var(--line-2); text-align: left; vertical-align: top; font-variant-numeric: tabular-nums; }
.grid th, .grid tr:first-child th { font-size: 12px; font-weight: 500; color: var(--muted); border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.grid th:first-child, .grid td:first-child { padding-left: 0; }
figure { margin: 12px 0; } figure img { max-width: 100%; border-radius: 6px; }
a { color: var(--accent); text-decoration: none; border-bottom: 1px solid rgba(47, 69, 184, 0.25); }
.mention { color: var(--accent); font-weight: 500; }

/* 시각 */
.t {
  display: inline-flex; align-items: center; gap: 4px; height: 20px; margin: 0 1px; padding: 0 6px; vertical-align: 1px;
  border: 1px solid var(--line); border-radius: 4px; background: var(--paper); color: var(--muted);
  font: 500 11px/1 var(--mono); letter-spacing: 0; cursor: default; white-space: nowrap;
}
.can-play .t { cursor: pointer; }
.can-play .t::before { content: ""; border-left: 5px solid currentColor; border-top: 3.5px solid transparent; border-bottom: 3.5px solid transparent; opacity: 0.7; }
.can-play .t:hover { border-color: var(--accent); background: var(--accent); color: #fff; }
.ref .t { margin: 0; }

/* 회의 중 메모·댓글 */
.marks > li { display: flex; gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--line-2); break-inside: avoid; }
.marks .at { flex: none; width: 58px; padding-top: 2px; }
.marks .at .t { margin: 0; }
.mk { flex: 1; min-width: 0; }
.marks .q { margin: 0 0 4px; color: var(--muted); font-size: 13.5px; }
.marks .q::before { content: "“"; } .marks .q::after { content: "”"; }
.marks .c { margin: 0; color: var(--ink); font-weight: 500; }
.tag { display: inline-block; margin-left: 8px; padding: 0 6px; border-radius: 3px; font-size: 11px; font-weight: 600; line-height: 18px; vertical-align: 1px; color: var(--muted); background: var(--soft); }
.tag.memo { color: #8a5a00; background: #fbf1dc; }
.tag.ok { color: var(--ok); background: var(--ok-soft); }
.marks .reply { margin: 5px 0 0; padding-left: 12px; border-left: 2px solid var(--line); color: #4b5260; font-size: 13.5px; }

.empty { color: var(--muted); margin-top: 32px; }
.foot { display: flex; justify-content: space-between; margin-top: 52px; padding-top: 12px; border-top: 1px solid var(--line); font-size: 11px; color: var(--faint); }

/* 듣기 */
.player {
  position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 10;
  display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 18px 0 6px;
  background: var(--ink); color: #fff; border-radius: 22px; box-shadow: 0 10px 28px rgba(15, 23, 42, 0.25); font-size: 13px;
}
.player[hidden] { display: none; }
.pp { display: grid; place-items: center; width: 32px; height: 32px; border: 0; border-radius: 50%; background: #fff; cursor: pointer; }
.pp svg { width: 13px; height: 13px; fill: var(--ink); margin-left: 2px; }
.pp.on svg { margin-left: 0; }
.pl-label { color: #aab1bd; } .pl-time { font-family: var(--mono); font-size: 12.5px; font-weight: 600; }

@media (max-width: 720px) {
  body { font-size: 14px; }
  .paper { margin: 0; padding: 22px 18px 40px; border-radius: 0; box-shadow: none; }
  .brand .en { display: none; }
  h1 { font-size: 22px; margin-bottom: 16px; }
  .facts { grid-template-columns: 1fr 1fr; }
  .fact { padding: 10px 12px; } .fact:nth-child(odd) { padding-left: 0; border-left: 0; } .fact:nth-child(n+3) { border-top: 1px solid var(--line-2); }
  .attendees { flex-direction: column; gap: 6px; }
  .people-table, .people-table tbody, .people-table tr, .people-table td { display: block; width: auto; }
  .people-table colgroup, .people-table thead { display: none; }
  .people-table tr { padding: 8px 0; border-bottom: 1px solid var(--line-2); }
  .people-table td { padding: 0; border: 0; } .people-table td:nth-child(2), .people-table td:nth-child(3) { display: inline; margin-left: 29px; color: var(--muted); font-size: 12.5px; }
  .people-table td:nth-child(3) { margin-left: 6px; } .people-table td:nth-child(3)::before { content: "· "; }
  .people-table td.pm { margin: 3px 0 0 29px; font-size: 13px; }
  .people-table .tbd { display: none; }
  .summary { padding: 15px 16px; } .summary p { font-size: 15px; }
  .discussion > li { flex-wrap: wrap; row-gap: 2px; } .discussion .dt { width: auto; flex: 1 0 100%; }
  .discussion > li .dd { flex: 1; }
  .actions, .actions tbody, .actions tr, .actions td { display: block; width: auto; }
  .actions colgroup, .actions thead { display: none; }
  .actions tr { position: relative; padding: 12px 0 12px 26px; border-bottom: 1px solid var(--line-2); }
  .actions td { padding: 0; border: 0; } .actions td.st { position: absolute; left: 0; top: 12px; }
  .actions td.who, .actions td.due, .actions td.rf { display: inline-block; margin: 6px 12px 0 0; font-size: 13px; }
  .actions td.rf { float: right; margin-right: 0; }
}
@page { size: A4; margin: 16mm 16mm 18mm; @bottom-right { content: counter(page) " / " counter(pages); font: 8.5pt "Pretendard Variable", sans-serif; color: #a0a6b0; } }
@media print {
  body { background: #fff; font-size: 10pt; }
  .av, .tick, .box.on, .tag { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .paper { max-width: none; margin: 0; padding: 0; box-shadow: none; border-radius: 0; }
  .player, .apptools { display: none !important; }
  .sec { margin-top: 26px; }
  .summary { background: none; border: 1px solid var(--line); }
  .t { border: 0; padding: 0 2px; height: auto; color: var(--faint); }
  a { border: 0; }
}
"""

JS = r"""
(function () {
  var au = document.getElementById('au'); if (!au) return;
  var bar = document.getElementById('player'), pp = document.getElementById('pp'), pt = document.getElementById('pt');
  var play = '<svg viewBox="0 0 16 16"><path d="M5 3.5v9l7-4.5-7-4.5Z"/></svg>';
  var pause = '<svg viewBox="0 0 16 16"><path d="M4.5 3h2.5v10H4.5zM9 3h2.5v10H9z"/></svg>';
  function fmt(s) { s = Math.max(0, Math.floor(s)); var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(r).padStart(2, '0'); }
  function ready() { bar.hidden = false; document.body.classList.add('can-play'); }
  au.addEventListener('loadedmetadata', ready);
  if (au.readyState >= 1) ready();  // 글꼴을 기다리는 사이에 이미 읽혔을 수 있다
  au.addEventListener('timeupdate', function () { pt.textContent = fmt(au.currentTime); });
  au.addEventListener('play', function () { pp.innerHTML = pause; pp.classList.add('on'); });
  au.addEventListener('pause', function () { pp.innerHTML = play; pp.classList.remove('on'); });
  pp.addEventListener('click', function () { au.paused ? au.play() : au.pause(); });
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.t'); if (!b || bar.hidden) return;
    au.currentTime = parseFloat(b.dataset.t || '0'); au.play();
  });
})();
"""

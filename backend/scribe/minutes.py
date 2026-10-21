"""회의록 HTML: 회의 정보, 결정 사항, 할 일, 논의, 내 노트, 회의 중 메모·댓글을 한 장의 문서로.

파일 하나로 열리고(글꼴은 인터넷이 있으면 Pretendard, 없으면 시스템 글꼴), 인쇄하면 A4에 맞는다.
같은 폴더에 녹음 파일이 있으면 시각을 눌러 그 부분을 들을 수 있다.
받아 적은 기록 자체는 넣지 않는다 (요약과 사람이 쓴 것만, 기록은 전사 기록 문서에).
"""

from __future__ import annotations

import datetime as _dt
import html
import json
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
    participants = [p["name"] for p in people]
    view = views.live_view(meeting_id)
    marks = []
    for a in sorted(view["annotations"], key=lambda a: a["anchor"].get("t0", a["anchor"].get("t", 0))):
        if a["kind"] not in ("comment", "memo") or not (a.get("body") or "").strip():
            continue
        pos = a.get("pos") or {}
        quote = pos.get("text") if isinstance(pos, dict) and "text" in pos else a["anchor"].get("quote")
        marks.append({
            "id": a["id"], "kind": a["kind"], "t": float(a["anchor"].get("t0", a["anchor"].get("t", 0)) or 0),
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
            items.append(f'<li data-ann="{_esc(mk.get("id", ""))}"><span class="at">{_chip(mk["t"])}</span><div class="mk">{quote}<p class="c"><span class="ann-body">{_esc(mk["body"])}</span>{tag}</p>{replies}</div></li>')
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
    """참석자: 소속·직무·메모가 하나라도 있으면 표로, 없으면 이름만 한 줄로.
    받은 파일에서도 참석자를 고칠 수 있게 원래 값을 data-people에 함께 넣는다 (FILE_EDIT_HTML이 같은 모양으로 다시 그린다)."""
    plain = [{k: p.get(k) or "" for k in ("name", "org", "role", "note")} for p in people]
    attrs = f' data-label="{_esc(label)}" data-people="{_esc(json.dumps(plain, ensure_ascii=False))}"'
    if not people:
        return f'<div class="attendees"{attrs} hidden></div>'
    head = f'<span class="lbl">{label}<b>{len(people)}</b></span>'
    if not any(p.get("org") or p.get("role") or p.get("note") for p in people):
        return f'<div class="attendees"{attrs}>{head}<span class="people">{"".join(_person(p["name"], i) for i, p in enumerate(people))}</span></div>'

    def cell(v: str) -> str:
        return _esc(v) if v else '<span class="tbd">—</span>'

    rows = "".join(
        f'<tr><td class="pn">{_person(p["name"], i)}</td><td>{cell(p.get("org", ""))}</td><td>{cell(p.get("role", ""))}</td>'
        f'<td class="pm">{cell(p.get("note", ""))}</td></tr>'
        for i, p in enumerate(people)
    )
    return (f'<div class="attendees detail"{attrs}>{head}<table class="people-table"><colgroup><col class="c-pn"><col class="c-org"><col class="c-role"><col></colgroup>'
            f'<thead><tr><th>이름</th><th>소속</th><th>직무</th><th>메모</th></tr></thead><tbody>{rows}</tbody></table></div>')


def _edit_scripts(data: dict) -> str:
    """앱에서 열었을 때만: [고치기]를 켜면 문장·메모·댓글·참석자를 그 자리에서 고쳐 앱에 바로 저장한다"""
    people = json.dumps(data.get("people") or [], ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" id="people-data">{people}</script><script>{EDIT_JS}</script>'


def tools_html(meeting_id: str, active: str) -> str:
    """앱에서 열었을 때만 보이는 위쪽 줄: 전사 기록 ↔ 회의록, 인쇄, 파일로 받기 (받은 파일·인쇄에는 없음)"""
    tabs = [("transcript", "전사 기록"), ("minutes", "회의록")]
    seg = "".join(f'<a href="/api/meetings/{_esc(meeting_id)}/{k}.html" class="{"on" if k == active else ""}">{v}</a>' for k, v in tabs)
    return (f'<nav class="apptools"><div class="seg">{seg}</div><span class="sp"></span>'
            f'<span class="edit-hint">문장·메모·댓글·참석자를 눌러 고치고 Enter (메모·댓글은 다 지우고 Enter면 삭제)</span>'
            f'<button type="button" id="edit-toggle" aria-pressed="false">고치기</button>'
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
<style>{CSS}{extra_css}{EDIT_CSS if tools else FILE_EDIT_CSS}</style>
</head>
<body{f' class="in-app" data-meeting="{_esc(data["id"])}"' if tools else ""}>
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
{_edit_scripts(data) if tools else FILE_EDIT_HTML}
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
.attendees[hidden] { display: none; }
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


# 받은 파일(앱 밖에서 여는 HTML)의 [고치기]: 더블클릭해서 그 자리에서 고치고 [저장]으로 고친 파일을 새로 저장한다.
# 앱의 회의 기록에는 반영되지 않는다 (파일만 바뀐다). 인쇄에는 나오지 않는다.
FILE_EDIT_CSS = r"""
.fedit { position: fixed; right: 16px; bottom: 16px; z-index: 50; display: flex; gap: 6px; align-items: center; padding: 6px; border-radius: 10px;
  background: #fff; border: 1px solid var(--line); box-shadow: 0 6px 24px rgba(15,23,42,.12); font-size: 13px; }
.fedit button { font: inherit; padding: 5px 11px; border-radius: 7px; border: 1px solid var(--line); background: #fff; color: var(--ink); cursor: pointer; }
.fedit button.on { background: var(--ink); color: #fff; border-color: var(--ink); }
.fedit button.save { background: var(--accent); color: #fff; border-color: var(--accent); }
.fedit button[hidden] { display: none; }
.fedit .hint { max-width: 300px; color: var(--muted); font-size: 12px; line-height: 1.45; padding: 0 4px; }
.fedit .hint[hidden] { display: none; }
.fedit .state { max-width: 260px; color: var(--muted); font-size: 12px; line-height: 1.4; }
.fedit .state:empty { display: none; }
body.fediting main :is(.s, .ann-body, h1, h2, h3, p, li, td, dd) { cursor: text; }
body.fediting main :is(.s, .ann-body, h1, h2, h3, p, li, td, dd):hover { outline: 1px dashed #b8c2d1; outline-offset: 2px; border-radius: 3px; }
[contenteditable="true"] { outline: 2px solid #2383e2 !important; background: #f3f8fe; border-radius: 3px; }
body.fediting main .attendees { cursor: pointer; border-radius: 3px; }
body.fediting main .attendees:hover { outline: 1px dashed #b8c2d1; outline-offset: 2px; }
body.fediting main .attendees :is(td, p):hover { outline: none; }
body.fediting main .attendees[hidden] { display: flex; }
body.fediting main .attendees[hidden]::after { content: "참석자 없음 · 더블클릭해서 추가"; font-size: 12.5px; color: var(--muted); }
/* 참석자 고치기: 문서 속 표처럼 담백하게. 칸은 글자처럼 보이다가 올리거나 고칠 때만 옅은 바탕 */
.pe { padding: 12px 0 10px; border-bottom: 1px solid var(--line); }
.pe table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 13.5px; }
.pe th { padding: 0 6px 6px; text-align: left; font-size: 11.5px; font-weight: 500; color: var(--faint); border-bottom: 1px solid var(--line-2); }
.pe td { padding: 1px 0; border-bottom: 1px solid var(--line-2); }
.pe th:last-child, .pe td:last-child { width: 28px; padding: 0; text-align: center; }
.pe input { width: 100%; box-sizing: border-box; padding: 6px; border: 0; border-radius: 4px; outline: 0; font: inherit; color: var(--ink); background: transparent; }
.pe input::placeholder { color: var(--faint); }
.pe input:hover { background: rgba(55, 53, 47, 0.04); }
.pe input:focus { background: rgba(35, 131, 226, 0.07); box-shadow: inset 0 0 0 1px rgba(35, 131, 226, 0.35); }
.pe button { font: inherit; font-size: 12.5px; white-space: nowrap; border: 0; border-radius: 4px; background: none; color: var(--muted); cursor: pointer; padding: 4px 8px; }
.pe button:hover { background: rgba(55, 53, 47, 0.06); color: var(--ink); }
.pe .del { width: 22px; height: 22px; padding: 0; font-size: 15px; line-height: 1; color: var(--faint); opacity: 0; }
.pe tr:hover .del, .pe .del:focus { opacity: 1; }
.pe .pe-bar { display: flex; gap: 2px; align-items: center; margin-top: 6px; }
.pe .pe-bar .sp { flex: 1; }
.pe button.ok { color: #fff; background: var(--ink); padding: 4px 11px; }
.pe button.ok:hover { background: var(--ink); opacity: 0.88; color: #fff; }
@media print { .fedit, .pe, .attendees[hidden] { display: none !important; } }
"""

FILE_EDIT_HTML = r"""<div class="fedit" id="fedit">
<span class="hint" id="fe-hint" hidden>글자를 <b>더블클릭</b>해서 고치고 Enter (Esc는 취소). 참석자를 더블클릭하면 이름·소속·직무·메모를 고치고 더하거나 뺄 수 있어요. 고칠 때마다 저절로 저장돼요. <b>이 파일만 바뀌고 회의 노트 앱의 기록에는 반영되지 않아요.</b></span>
<span class="state" id="fe-state"></span>
<button type="button" id="fe-toggle" title="이 파일을 고쳐요. 회의 노트 앱의 기록은 바뀌지 않아요">고치기</button>
<button type="button" class="save" id="fe-save" hidden>저장</button>
</div>
<script>
(function () {
  // 고칠 때마다 저절로 저장한다:
  // 1) 이 브라우저 안에 바로 (같은 파일을 다시 열면 되살린다)
  // 2) 크롬·엣지: [저장]으로 저장할 파일을 한 번 고르면(원래 파일에 덮어써도 된다) 그 뒤로는 고칠 때마다 그 파일에 바로
  //    (브라우저는 보안 때문에 파일을 처음 한 번은 사람이 골라야 쓸 수 있다)
  // 3) 그 밖의 브라우저: [저장]을 누르면 고친 파일을 내려받기 폴더에
  var bar = document.getElementById('fedit'), tg = document.getElementById('fe-toggle'), sv = document.getElementById('fe-save'),
      hint = document.getElementById('fe-hint'), st = document.getElementById('fe-state'), main = document.querySelector('main');
  var SEL = '.s, .ann-body, h1, h2, h3, p, li, td, dd';
  var KEY = 'scribe-file-edit:' + location.pathname;
  var picker = !!window.showSaveFilePicker, handle = null, writing = Promise.resolve();
  function state(t) { st.textContent = t; }
  function store(get, set) { try { return set === undefined ? localStorage.getItem(get) : (set === null ? localStorage.removeItem(get) : localStorage.setItem(get, set)); } catch (e) { return null; } }
  function on(v) {
    document.body.classList.toggle('fediting', v);
    tg.classList.toggle('on', v); tg.textContent = v ? '고치기 끝내기' : '고치기';
    hint.hidden = !v; sv.hidden = !v && !store(KEY);
    sv.textContent = handle ? '다른 이름으로 저장' : (picker ? '저장할 파일 정하기' : '고친 파일 내려받기');
  }
  // 예전에 이 브라우저에서 고친 것이 있으면 되살린다 (파일에 저장하지 않고 닫았어도)
  var draft = store(KEY);
  if (draft && draft !== main.innerHTML) { main.innerHTML = draft; state('이 브라우저에 저장해 둔 고친 내용을 불러왔어요'); sv.hidden = false; }
  tg.addEventListener('click', function () {
    if (closePeople && !closePeople(true)) return;  // 참석자를 고치던 중이면 고친 대로 넣고 끝낸다
    on(!document.body.classList.contains('fediting'));
  });
  function html() {
    var doc = document.documentElement.cloneNode(true);
    var b = doc.querySelector('body'); if (b) b.classList.remove('fediting');
    doc.querySelectorAll('[contenteditable]').forEach(function (n) { n.removeAttribute('contenteditable'); });
    ['#fe-hint', '#fe-save'].forEach(function (q) { var n = doc.querySelector(q); if (n) n.hidden = true; });
    var t = doc.querySelector('#fe-toggle'); if (t) { t.classList.remove('on'); t.textContent = '고치기'; }
    var s = doc.querySelector('#fe-state'); if (s) s.textContent = '';
    return '<!doctype html>\n' + doc.outerHTML;
  }
  function blob() { return new Blob([html()], { type: 'text/html;charset=utf-8' }); }
  function writeFile() {
    if (!handle) return Promise.resolve(false);
    writing = writing.then(function () {
      return handle.createWritable().then(function (w) { return w.write(blob()).then(function () { return w.close(); }); });
    }).then(function () { state('저장됨 · ' + handle.name); return true; },
            function (e) { state('파일에 저장하지 못했어요 (이 브라우저에는 저장해 뒀어요): ' + e.message); handle = null; on(true); return false; });
    return writing;
  }
  function saved() {
    // 고칠 때마다: 브라우저에 먼저, 고른 파일이 있으면 그 파일에도
    store(KEY, main.innerHTML);
    sv.hidden = false;
    if (handle) { state('저장하는 중…'); writeFile(); }
    else state(picker ? '이 브라우저에 저장됨 · 파일에도 저장하려면 [저장할 파일 정하기]를 한 번 눌러 주세요' : '이 브라우저에 저장됨 · 파일로 남기려면 [고친 파일 내려받기]');
  }
  document.addEventListener('dblclick', function (e) {
    if (!document.body.classList.contains('fediting') || !e.target.closest || e.target.closest('.pe')) return;
    var at = e.target.closest('.attendees');
    if (at && main.contains(at)) { people(at); return; }
    var el = e.target.closest(SEL);
    if (!el || bar.contains(el) || !el.closest('main') || el.isContentEditable) return;
    var orig = el.innerHTML;
    el.setAttribute('contenteditable', 'true');
    el.focus();
    function done(keep) {
      el.removeEventListener('keydown', key); el.removeEventListener('blur', blur);
      el.removeAttribute('contenteditable');
      if (!keep) el.innerHTML = orig;
      else if (el.innerHTML !== orig) saved();
    }
    function key(k) {
      if (k.key === 'Enter' && !k.shiftKey && !k.isComposing) { k.preventDefault(); el.blur(); }
      if (k.key === 'Escape') { done(false); }
    }
    function blur() { done(true); }
    el.addEventListener('keydown', key); el.addEventListener('blur', blur);
  });
  // 참석자: 더블클릭하면 표로 고치고 [완료](또는 Enter)로 문서에 넣는다. Esc·[취소]는 그대로 둔다.
  // 참석자 칸은 attendees_html(파이썬)과 같은 모양으로 다시 그리고, 이름을 바꾸면 본문의 화자 이름·담당자도 함께 바꾼다.
  var closePeople = null;
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function initials(n) { n = n.trim(); return /^[가-힣]{2,4}$/.test(n) ? n.slice(-2) : (n.slice(0, 1).toUpperCase() || '?'); }
  function chip(n, i) { return '<span class="person p' + (i % 6) + '"><span class="av">' + esc(initials(n)) + '</span>' + esc(n) + '</span>'; }
  function attendees(ps, label) {
    var a = ' data-label="' + esc(label) + '" data-people="' + esc(JSON.stringify(ps)) + '"';
    if (!ps.length) return '<div class="attendees"' + a + ' hidden></div>';
    var head = '<span class="lbl">' + esc(label) + '<b>' + ps.length + '</b></span>';
    if (!ps.some(function (p) { return p.org || p.role || p.note; }))
      return '<div class="attendees"' + a + '>' + head + '<span class="people">' + ps.map(function (p, i) { return chip(p.name, i); }).join('') + '</span></div>';
    function cell(v) { return v ? esc(v) : '<span class="tbd">—</span>'; }
    return '<div class="attendees detail"' + a + '>' + head + '<table class="people-table"><colgroup><col class="c-pn"><col class="c-org"><col class="c-role"><col></colgroup>'
      + '<thead><tr><th>이름</th><th>소속</th><th>직무</th><th>메모</th></tr></thead><tbody>'
      + ps.map(function (p, i) {
        return '<tr><td class="pn">' + chip(p.name, i) + '</td><td>' + cell(p.org) + '</td><td>' + cell(p.role) + '</td><td class="pm">' + cell(p.note) + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }
  function rename(ren, ps) {
    var by = {}; ps.forEach(function (p) { by[p.name] = p; });
    // 회의록의 담당자 칩 (참석자 칸은 이미 새로 그렸다)
    main.querySelectorAll('.person').forEach(function (s) {
      var t = s.lastChild, to = t && t.nodeType === 3 && ren[t.nodeValue];
      if (!to || s.closest('.attendees')) return;
      t.nodeValue = to;
      var av = s.querySelector('.av'); if (av) av.textContent = initials(to);
    });
    // 전사 기록의 발언자 줄: 이름·머리글자, 이름 옆 소속·직무
    main.querySelectorAll('.who .name').forEach(function (n) {
      var who = n.parentNode, to = ren[n.textContent];
      if (to) {
        n.textContent = to;
        var av = who.querySelector('.av'); if (av && !av.classList.contains('anon')) av.textContent = initials(to);
      }
      var p = by[n.textContent], info = p ? [p.org, p.role].filter(Boolean).join(' · ') : '', r = who.querySelector('.role');
      if (info && !r) { r = document.createElement('span'); r.className = 'role'; n.after(r); }
      if (info) r.textContent = info; else if (r) r.remove();
    });
  }
  function people(at) {
    if (closePeople) return;
    var label = at.getAttribute('data-label') || '참석자', orig = [];
    try { orig = JSON.parse(at.getAttribute('data-people') || '[]'); } catch (e) { orig = []; }
    var rows = orig.map(function (p) { return { name: p.name || '', org: p.org || '', role: p.role || '', note: p.note || '', from: p.name || '' }; });
    if (!rows.length) rows.push({ name: '', org: '', role: '', note: '', from: '' });
    var box = document.createElement('div');
    box.className = 'pe';
    function draw() {
      box.innerHTML = '<table><thead><tr><th>이름</th><th>소속</th><th>직무</th><th>메모</th><th></th></tr></thead><tbody>'
        + rows.map(function (p, i) {
          return '<tr data-i="' + i + '">' + ['name', 'org', 'role', 'note'].map(function (k) {
            return '<td><input data-k="' + k + '" value="' + esc(p[k]) + '" placeholder="' + (k === 'name' ? '이름' : '비어 있음') + '"></td>';
          }).join('') + '<td><button type="button" class="del" data-del="' + i + '" title="빼기" aria-label="빼기">×</button></td></tr>';
        }).join('')
        + '</tbody></table><div class="pe-bar"><button type="button" data-act="add">+ 참석자 추가</button><span class="sp"></span>'
        + '<button type="button" data-act="cancel">취소</button><button type="button" class="ok" data-act="ok">완료</button></div>';
    }
    function close(keep) {
      if (keep) {
        var seen = {}, out = [], ren = {};
        for (var i = 0; i < rows.length; i++) {
          var r = rows[i], n = r.name.trim();
          if (!n) continue;
          if (seen[n]) { alert("'" + n + "' 참석자가 이미 있어요. 다른 이름으로 적어 주세요."); return false; }
          seen[n] = 1;
          out.push({ name: n, org: r.org.trim(), role: r.role.trim(), note: r.note.trim() });
          if (r.from && r.from !== n) ren[r.from] = n;
        }
        if (JSON.stringify(out) !== JSON.stringify(orig)) {
          var tmp = document.createElement('div');
          tmp.innerHTML = attendees(out, label);
          box.replaceWith(tmp.firstChild);
          rename(ren, out);
          closePeople = null;
          saved();
          return true;
        }
      }
      box.replaceWith(at);
      closePeople = null;
      return true;
    }
    box.addEventListener('input', function (e) {
      var k = e.target.getAttribute('data-k'), tr = e.target.closest('tr');
      if (k && tr) rows[+tr.getAttribute('data-i')][k] = e.target.value;
    });
    box.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.preventDefault(); close(false); }
      else if (e.key === 'Enter' && !e.isComposing && e.target.tagName === 'INPUT') { e.preventDefault(); close(true); }
    });
    box.addEventListener('click', function (e) {
      var del = e.target.getAttribute('data-del'), act = e.target.getAttribute('data-act');
      if (del !== null) { rows.splice(+del, 1); draw(); }
      else if (act === 'add') {
        rows.push({ name: '', org: '', role: '', note: '', from: '' }); draw();
        var ins = box.querySelectorAll('input[data-k="name"]'); ins[ins.length - 1].focus();
      }
      else if (act === 'ok') close(true);
      else if (act === 'cancel') close(false);
    });
    draw();
    at.replaceWith(box);
    closePeople = close;
    var first = box.querySelector('input'); if (first) first.focus();
  }
  function fileName() {
    var name = decodeURIComponent((location.pathname.split('/').pop() || 'meeting.html'));
    return /\.html?$/i.test(name) ? name : name + '.html';
  }
  sv.addEventListener('click', function () {
    if (picker) {
      window.showSaveFilePicker({ suggestedName: fileName(), types: [{ description: 'HTML', accept: { 'text/html': ['.html'] } }] })
        .then(function (h) { handle = h; return writeFile(); })
        .then(function (ok) { if (ok) { store(KEY, null); on(document.body.classList.contains('fediting')); } })
        .catch(function (e) { if (e && e.name !== 'AbortError') download(); });
    } else download();
  });
  function download() {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob()); a.download = fileName().replace(/(\.html?)$/i, ' (고침)$1');
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 5000);
    state('내려받기 폴더에 저장했어요. 이름이 download처럼 보이면 끝에 .html을 붙여 주세요');
  }
})();
</script>"""


# 앱에서 연 문서의 [고치기]: 문장·메모·댓글은 그 자리에서, 참석자는 표에서 고쳐 앱에 저장하고 문서를 새로 그린다
EDIT_CSS = r"""
.apptools .edit-hint { display: none; font-size: 12px; color: var(--muted); }
body.editing .apptools .edit-hint { display: inline; }
#edit-toggle[aria-pressed="true"] { background: var(--ink); color: #fff; border-color: var(--ink); }
body.editing .ed { outline: 1px dashed #b8c2d1; outline-offset: 2px; border-radius: 3px; cursor: text; }
body.editing .ed:focus { outline: 2px solid #2383e2; background: #f3f8fe; }
.pe { margin: 12px 0 4px; }
.pe table { width: 100%; border-collapse: collapse; font-size: 13px; }
.pe th { text-align: left; font-weight: 500; color: var(--muted); padding: 4px 6px; }
.pe td { padding: 2px 4px; }
.pe input { width: 100%; box-sizing: border-box; padding: 5px 6px; border: 1px solid #d7dce3; border-radius: 5px; font: inherit; }
.pe button { font: inherit; font-size: 12px; padding: 4px 8px; border: 1px solid #d7dce3; border-radius: 5px; background: #fff; cursor: pointer; }
.pe .add { margin-top: 6px; }
@media print { .pe, .apptools { display: none !important; } }
"""

EDIT_JS = r"""
(function () {
  var mid = document.body.getAttribute('data-meeting');
  var btn = document.getElementById('edit-toggle');
  if (!mid || !btn) return;
  var KEY = 'scribe-edit-' + mid;
  // 고친 뒤 새로 그려도 보던 자리 그대로
  var y = sessionStorage.getItem(KEY + '-y');
  if (y) { sessionStorage.removeItem(KEY + '-y'); window.scrollTo(0, +y); }
  function req(method, url, body) {
    return fetch(url, { method: method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) {
        if (r.ok) return r.json().catch(function () { return {}; });
        return r.json().catch(function () { return {}; }).then(function (j) { throw new Error(j.detail || ('저장하지 못했어요 (' + r.status + ')')); });
      });
  }
  function redraw() { sessionStorage.setItem(KEY + '-y', String(window.scrollY)); location.reload(); }
  function fail(e) { alert(e.message); }
  function editable(el, save) {
    el.setAttribute('contenteditable', 'true');
    el.classList.add('ed');
    var orig = el.innerText;
    el.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); el.blur(); }
      if (e.key === 'Escape') { el.innerText = orig; el.blur(); }
    });
    el.addEventListener('blur', function () {
      var v = el.innerText.replace(/\s+/g, ' ').trim();
      if (v === orig.replace(/\s+/g, ' ').trim()) return;
      save(v).then(redraw, function (e) { el.innerText = orig; fail(e); });
    });
  }
  function people() {
    var data = JSON.parse(document.getElementById('people-data').textContent || '[]');
    var head = document.querySelector('header.head');
    var old = head.querySelector('.attendees');
    var box = document.createElement('div');
    box.className = 'pe';
    var rows = data.map(function (p) { return Object.assign({}, p); });
    function draw() {
      var h = '<table><thead><tr><th>이름</th><th>소속</th><th>직무</th><th>메모</th><th></th></tr></thead><tbody>';
      rows.forEach(function (p, i) {
        h += '<tr data-i="' + i + '">' + ['name', 'org', 'role', 'note'].map(function (k) {
          return '<td><input data-k="' + k + '" value="' + String(p[k] || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;') + '"></td>';
        }).join('') + '<td><button type="button" data-del="' + i + '">빼기</button></td></tr>';
      });
      box.innerHTML = h + '</tbody></table><button type="button" class="add">+ 참석자 추가</button>';
    }
    function save() {
      var names = {};
      for (var i = 0; i < rows.length; i++) {
        var n = (rows[i].name || '').trim();
        if (!n) continue;
        if (names[n]) { alert("'" + n + "' 참석자가 이미 있어요. 다른 이름으로 적어 주세요."); redraw(); return; }
        names[n] = 1;
      }
      req('PUT', '/api/meetings/' + mid + '/participants', { participants: rows.filter(function (p) { return (p.name || '').trim(); }) }).then(redraw, fail);
    }
    box.addEventListener('change', function (e) {
      var inp = e.target, tr = inp.closest('tr');
      if (!tr || !inp.getAttribute('data-k')) return;
      rows[+tr.getAttribute('data-i')][inp.getAttribute('data-k')] = inp.value.trim();
      save();
    });
    box.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.isComposing && e.target.tagName === 'INPUT') e.target.blur(); });
    box.addEventListener('click', function (e) {
      var del = e.target.getAttribute('data-del');
      if (del !== null) { rows.splice(+del, 1); save(); }
      if (e.target.classList.contains('add')) { rows.push({ name: '' }); draw(); var ins = box.querySelectorAll('input[data-k="name"]'); ins[ins.length - 1].focus(); }
    });
    draw();
    if (old) old.replaceWith(box); else head.appendChild(box);
  }
  function on() {
    document.body.classList.add('editing');
    btn.setAttribute('aria-pressed', 'true');
    btn.textContent = '고치기 끝내기';
    document.querySelectorAll('.s[id^="u-"]').forEach(function (s) {
      var id = s.id.slice(2);
      editable(s, function (v) { return req('PATCH', '/api/meetings/' + mid + '/segments/' + encodeURIComponent(id), { text: v }); });
    });
    document.querySelectorAll('[data-ann] .ann-body').forEach(function (el) {
      var id = el.closest('[data-ann]').getAttribute('data-ann');
      if (!id) return;
      editable(el, function (v) {
        return v ? req('PATCH', '/api/annotations/' + encodeURIComponent(id), { body: v }) : req('DELETE', '/api/annotations/' + encodeURIComponent(id));
      });
    });
    people();
  }
  btn.addEventListener('click', function () {
    var next = !document.body.classList.contains('editing');
    sessionStorage.setItem(KEY, next ? '1' : '0');
    if (next) on(); else redraw();
  });
  if (sessionStorage.getItem(KEY) === '1') on();
})();
"""


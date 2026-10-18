"""전사 기록 HTML: 통화 기록처럼 화자별 발언이 시간순으로 이어지고, 회의 중 남긴 메모·댓글·수정·강조가 제자리에 붙는다.

- 메모는 그 순간의 흐름 안에 쪽지로
- 댓글은 글자에 번호를 달고 그 문장 바로 아래에 (메모·댓글을 거의 문장마다 달아도 어느 말에 단 것인지 헷갈리지 않게)
- 수정은 변경 내용 추적처럼 (반영 전: 원문 줄긋기 + 고친 말, 반영 후: 고친 말)
- 쉬는 시간·구분선은 가로줄
원본(화자 구분)이 있으면 원본, 없으면 실시간 기록과 회의 중 고른 화자를 쓴다.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import re
from typing import Any, Optional

from .db import Store
from .export import _local_ranges, divider_text, record_source
from .minutes import _chip, _esc, _facts, _initials, page, people_of
from .textutil import fmt_time, normalize
from .views import Views, speaker_name

HL_COLORS = ("yellow", "green", "blue", "pink", "purple")
TURN_GAP_S = 60.0  # 같은 사람이라도 이만큼 조용했으면 새 발언으로
PLAIN_GAP_S = 2.0  # 화자 구분이 없을 때는 이 정도 쉬면 문단을 나눈다
PLAIN_MAX = 8
INDEX_OPEN_MAX = 20  # 모아 보기를 처음부터 펼쳐 두는 최대 개수


def collect(store: Store, views: Views, meeting_id: str) -> Optional[dict]:
    m = store.get_meeting(meeting_id, with_notes=True)
    if m is None:
        return None
    src = record_source(views, m)
    clock = m.get("clock") or []
    start = float(m.get("held_at") or (clock[0][1] if clock else m["created_at"]))
    people = people_of(m)
    if not people and src["final"] is not None:
        people = [{"name": n} for n in (speaker_name(src["speakers"], s) for s in src["final"]["speakers"]) if n]
    participants = [p["name"] for p in people]
    return {
        "id": m["id"], "title": m.get("title") or "제목 없는 회의", "start": start, "duration": float(m.get("duration") or 0),
        "place": m.get("place") or "", "participants": participants, "people": people, "clock": clock,
        "units": src["units"], "annotations": src["annotations"], "speakers": src["speakers"],
        "is_final": src["final"] is not None,
    }


def _wall(clock: list, t: float) -> Optional[float]:
    """녹음 시각 → 실제 시각 (녹음을 멈췄다 이어도 맞게)"""
    if not clock:
        return None
    i = bisect.bisect_right([c[0] for c in clock], t) - 1
    t0, w0 = clock[max(i, 0)]
    return w0 + (t - t0)


def _wall_label(clock: list, t: float) -> str:
    w = _wall(clock, t)
    return _dt.datetime.fromtimestamp(w).strftime("%H:%M") if w else ""


class _Marks:
    """한 문장 안의 강조·댓글·수정을 HTML로. 댓글 번호는 처음 나온 순서대로."""

    def __init__(self) -> None:
        self.numbers: dict[str, int] = {}

    def number(self, ann: dict) -> int:
        if ann["id"] not in self.numbers:
            self.numbers[ann["id"]] = len(self.numbers) + 1
        return self.numbers[ann["id"]]

    def unit(self, text: str, ranges: list[tuple[int, int, dict, bool]], comments: list[dict]) -> str:
        """겹친 표시도 함께 보이도록 경계마다 잘라서 감싼다: 댓글(바깥) > 강조 > 수정(원문 줄긋기 + 고친 말)"""
        edits: list[tuple[int, int, dict, bool]] = []
        others: list[tuple[int, int, dict, bool]] = []
        for r in sorted(ranges, key=lambda r: (r[0], -r[1])):
            s, e, ann, _ = r
            if ann["kind"] != "edit":
                others.append(r)
            elif ann.get("status") != "dismissed" and ann.get("match") != "same" and not any(s < e2 and s2 < e for s2, e2, _, _ in edits):
                edits.append(r)
        cuts = sorted({0, len(text)} | {x for r in edits + others for x in r[:2]})
        out: list[str] = []
        for a, b in zip(cuts, cuts[1:]):
            inside = lambda r: r[0] <= a and b <= r[1]  # noqa: E731
            ed = next((r for r in edits if inside(r)), None)
            hl = next((r for r in others if r[2]["kind"] == "highlight" and inside(r)), None)
            cm = next((r for r in others if r[2]["kind"] == "comment" and inside(r)), None)
            if ed is None:
                piece = _esc(text[a:b])
            else:
                repl = _esc(ed[2].get("replacement") or "")
                if ed[2].get("status") == "accepted":
                    piece = f'<span class="ed" title="고치기 전: {_esc(text[ed[0]:ed[1]])}">{repl}</span>' if a == ed[0] else ""
                else:
                    piece = f"<del>{_esc(text[a:b])}</del>" + (f"<ins>{repl}</ins>" if b == ed[1] else "")
            if piece and hl:
                color = hl[2].get("color") if hl[2].get("color") in HL_COLORS else "yellow"
                piece = f'<mark class="hl hl-{color}">{piece}</mark>'
            if piece and cm:
                piece = f'<mark class="cm" data-c="{self.number(cm[2])}">{piece}</mark>'
            out.append(piece)
            for s, e, ann, last in others:
                if ann["kind"] == "comment" and e == b and last:
                    n = self.number(ann)
                    out.append(self._sup(n))
                    comments.append(dict(ann, n=n))
        return "".join(out)

    @staticmethod
    def _sup(n: int) -> str:
        return f'<a class="cn" href="#c-{n}">{n}</a>'


def _memo_html(m: dict) -> str:
    t = float(m["anchor"].get("t", 0) or 0)
    return f'<div class="memo" id="a-{_esc(m["id"])}"><span class="memo-h"><span class="memo-tag">메모</span>{_chip(t)}</span><p>{_esc(m["body"])}</p></div>'


def _divider_html(d: dict) -> str:
    return f'<div class="divider"><span>{_esc(divider_text(d))}</span></div>'


def _comment_html(c: dict) -> str:
    replies = "".join(f'<p class="reply">{_esc(r.get("text", ""))}</p>' for r in c.get("replies") or [])
    done = '<span class="tag ok">해결</span>' if c.get("status") == "resolved" else ""
    # 후처리 원본에서 말이 바뀌었으면 회의 중에 단 글자도 함께 (자리를 시간으로만 찾았으면 "추정")
    pos, quote = c.get("pos") or {}, (c.get("anchor") or {}).get("quote") or ""
    was = ""
    if quote and (pos.get("approx") or normalize(pos.get("text") or "") != normalize(quote)):
        was = f'<p class="was">{"자리 추정 · " if pos.get("approx") else ""}회의 중 기록: “{_esc(quote)}”</p>'
    return (f'<div class="cmt" id="c-{c["n"]}"><span class="n">{c["n"]}</span>'
            f'<div class="cb"><p class="body">{_esc(c.get("body") or "")}{done}</p>{was}{replies}</div></div>')


def _ann_t(a: dict) -> float:
    return float(a["anchor"].get("t", a["anchor"].get("t0", 0)) or 0)


def _orphan_html(a: dict) -> str:
    label = {"memo": "메모", "comment": "댓글", "edit": "수정"}[a["kind"]]
    quote = a["anchor"].get("quote")
    quote_html = f' <span class="q">“{_esc(quote)}”</span>' if quote else ""
    return f'<li>{_chip(_ann_t(a))}<span class="k">{label}</span>{_esc(a.get("body") or a.get("replacement") or "")}{quote_html}</li>'


def _person_index(data: dict) -> dict[str, int]:
    """화자 색: 참석자 순서를 먼저 따르고, 나머지는 처음 말한 순서"""
    order: dict[str, int] = {}
    for p in data["participants"]:
        order.setdefault(p, len(order))
    for u in data["units"]:
        name = speaker_name(data["speakers"], u.get("speaker"))
        if name:
            order.setdefault(name, len(order))
    return order


def render(data: dict, audio_src: Optional[str] = None, generated: Optional[float] = None, tools: str = "") -> str:
    units, anns, speakers, clock = data["units"], data["annotations"], data["speakers"], data["clock"]
    per = _local_ranges(units, [a for a in anns if a["kind"] in ("comment", "edit", "highlight")])
    after: dict[Optional[str], list[dict]] = {}
    orphans: list[dict] = []
    for a in anns:
        pos = a.get("pos")
        if a["kind"] in ("memo", "divider"):
            if pos is not None and "after" in pos:
                after.setdefault(pos["after"], []).append(a)
            elif a["kind"] == "memo" and (a.get("body") or "").strip():
                orphans.append(a)
        elif pos is None and a["kind"] in ("comment", "edit") and (a.get("body") or a.get("replacement") or "").strip():
            orphans.append(a)
    for lst in after.values():
        lst.sort(key=lambda a: a["anchor"].get("t", 0))
    colors = _person_index(data)
    titles = {p["name"]: p for p in data.get("people") or []}
    marks = _Marks()

    # 발언 묶기: 같은 사람이 이어서 말하면 한 발언. 구분선이 있으면 끊는다.
    blocks: list[str] = []
    turn: Optional[dict[str, Any]] = None

    def flush() -> None:
        """이어진 문장을 한 문단으로 (메모나 댓글이 끼면 거기서 나눈다)"""
        if turn is not None and turn["para"]:
            turn["flow"].append("<p>" + " ".join(turn["para"]) + "</p>")
            turn["para"] = []

    def close() -> None:
        nonlocal turn
        if turn is None:
            return
        flush()
        blocks.append(f'<article class="turn">{turn["head"]}<div class="lines">{"".join(turn["flow"])}</div></article>')
        turn = None

    def head(u: dict, key: Optional[str]) -> str:
        wall = _wall_label(clock, u["t0"])
        wall_html = f'<span class="wall">{wall}</span>' if wall else ""
        if key is None:
            return f'<div class="who plain">{_chip(u["t0"])}{wall_html}</div>'
        full = speaker_name(speakers, key) or ""
        guess = full.endswith("(추정)")
        name = full.removesuffix("(추정)")
        anon = re.fullmatch(r"화자\s*(\S{1,3})", name)  # 이름을 아직 못 붙인 화자는 회색에 번호만
        av_cls = "anon" if anon else f"p{colors.get(full, 0) % 6}"
        initials = anon.group(1) if anon else _initials(name)
        info = " · ".join(x for x in (titles.get(name, {}).get("org"), titles.get(name, {}).get("role")) if x)
        return (f'<div class="who"><span class="av {av_cls}{" guess" if guess else ""}">{_esc(initials)}</span>'
                f'<span class="name">{_esc(name)}</span>{f"<span class=role>{_esc(info)}</span>" if info else ""}'
                f'{"<span class=est>추정</span>" if guess else ""}{_chip(u["t0"])}{wall_html}</div>')

    for m in after.get(None, []):
        blocks.append(_divider_html(m) if m["kind"] == "divider" else f'<div class="free">{_memo_html(m)}</div>')
    prev_end = None
    for u in units:
        key = u.get("speaker")
        gap = u["t0"] - prev_end if prev_end is not None else 0
        same = turn is not None and turn["key"] == key and (
            gap <= TURN_GAP_S if key is not None else (gap <= PLAIN_GAP_S and turn["n"] < PLAIN_MAX))
        if not same:
            close()
            turn = {"key": key, "head": head(u, key), "flow": [], "para": [], "n": 0}
        assert turn is not None
        comments: list[dict] = []
        body = marks.unit(u["text"], per.get(u["id"], []), comments)
        turn["para"].append(f'<span class="s" id="u-{_esc(u["id"])}" data-t="{u["t0"]:.2f}">{body}</span>')
        turn["n"] += 1
        prev_end = u["t1"]
        # 댓글은 그 문장 바로 아래, 메모는 그다음 (남긴 순간)
        if comments:
            flush()
            turn["flow"].append("".join(_comment_html(c) for c in comments))
        for m in after.get(u["id"], []):
            if m["kind"] == "divider":
                close()
                blocks.append(_divider_html(m))
            else:
                turn = turn or {"key": key, "head": head(u, key), "flow": [], "para": [], "n": 0}
                flush()
                turn["flow"].append(_memo_html(m))
    close()

    if not units:
        blocks.append('<p class="empty">아직 기록이 없어요.</p>')

    # 모아 보기: 메모와 댓글을 시간순으로, 누르면 그 자리로
    index_items = []
    for a in anns:
        if a["kind"] == "memo" and (a.get("body") or "").strip() and a.get("pos") is not None:
            index_items.append((float(a["anchor"].get("t", 0) or 0), "memo", f"#a-{a['id']}", a["body"]))
        elif a["kind"] == "comment" and a["id"] in marks.numbers:
            index_items.append((float(a["anchor"].get("t0", 0) or 0), "comment", f"#c-{marks.numbers[a['id']]}", a.get("body") or ""))
    index_items.sort(key=lambda x: x[0])
    counts = {k: sum(1 for a in anns if a["kind"] == k and (k != "memo" or (a.get("body") or "").strip())) for k in ("memo", "comment", "edit", "highlight")}
    turns_n = sum(1 for b in blocks if b.startswith("<article"))

    top = []
    stats = (f'<div class="stats"><span>발언 <b>{turns_n:,}</b></span><span>메모 <b>{counts["memo"]:,}</b></span><span>댓글 <b>{counts["comment"]:,}</b></span>'
             f'<span>수정 <b>{counts["edit"]:,}</b></span><span>강조 <b>{counts["highlight"]:,}</b></span>'
             '<span class="legend"><mark class="hl hl-yellow">강조</mark><mark class="cm">댓글</mark><a class="cn">1</a>'
             '<del>원문</del><ins>고친 말</ins><span class="memo-tag">메모</span></span></div>')
    top.append(stats)
    if index_items:
        lis = "".join(
            f'<li><a class="jump" href="{href}"><span class="tm">{_esc(fmt_time(t))}</span>'
            f'<span class="k k-{kind}">{"메모" if kind == "memo" else "댓글"}</span><span class="tx">{_esc(text)}</span></a></li>'
            for t, kind, href, text in index_items
        )
        # 많으면 접어 둔다 (펼친 목록이 문서 앞을 다 차지하고 여는 속도도 느려진다)
        n_memo = sum(1 for x in index_items if x[1] == "memo")
        detail = f' <span class="mix">메모 {n_memo:,} · 댓글 {len(index_items) - n_memo:,}</span>' if len(index_items) > INDEX_OPEN_MAX else ""
        top.append(f'<details class="index"{" open" if len(index_items) <= INDEX_OPEN_MAX else ""}><summary>메모·댓글 모아 보기 <b>{len(index_items):,}</b>{detail}</summary><ol>{lis}</ol></details>')

    tail = ""
    if orphans:
        lis = "".join(_orphan_html(a) for a in sorted(orphans, key=_ann_t))
        tail = ('<section class="orphans"><h2>자리를 찾지 못한 메모·댓글</h2>'
                '<p class="hint">원본을 다시 만들면서 말이 바뀌어 원래 자리를 찾지 못했어요.</p>'
                f"<ul>{lis}</ul></section>")

    kind = "원본 · 화자 구분" if data["is_final"] else "실시간 기록"
    body = (f'<div class="rec-top">{"".join(top)}</div>'
            f'<div class="transcript">{"".join(blocks)}</div>{tail}')
    return page(
        data, brand=("전사 기록", "Transcript", "TR"), facts=_facts(data["start"], data["duration"], data["place"], [("기록", kind)]),
        body=body, audio_src=audio_src, generated=generated, tools=tools, extra_css=CSS, extra_js=JS,
    )


CSS = r"""
/* ---------- 전사 기록 ---------- */
.rec-top { margin-top: 22px; }
.stats { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; padding: 0 0 12px; font-size: 12.5px; color: var(--muted); border-bottom: 1px solid var(--line); }
.stats b { margin-left: 3px; color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
.legend { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; font-size: 12px; color: var(--text); }
.legend ins { margin-left: -4px; }
.index { margin: 0; border-bottom: 1px solid var(--line); }
.index summary { padding: 11px 0; font-size: 13px; font-weight: 600; color: var(--ink); cursor: pointer; list-style: none; }
.index summary::-webkit-details-marker { display: none; }
.index summary::before { content: ""; display: inline-block; width: 6px; height: 6px; margin: 0 9px 2px 2px; border-right: 1.5px solid var(--muted); border-bottom: 1.5px solid var(--muted); transform: rotate(-45deg); transition: transform .15s; }
.index[open] summary::before { transform: rotate(45deg); margin-bottom: 4px; }
.index summary b { margin-left: 4px; color: var(--muted); font-weight: 500; }
.index summary .mix { margin-left: 8px; font-size: 12px; font-weight: 400; color: var(--faint); }
.index ol { list-style: none; margin: 0 0 12px; padding: 0; columns: 2; column-gap: 28px; }
.index li { break-inside: avoid; }
.index .jump { display: flex; align-items: baseline; gap: 8px; padding: 4px 0; border: 0; color: var(--text); font-size: 13px; }
.index .jump:hover .tx { color: var(--accent); }
.index .tm { flex: none; width: 48px; font-family: var(--mono); font-size: 11.5px; color: var(--muted); }
.index .tx { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.k { flex: none; display: inline-block; padding: 0 5px; border-radius: 3px; font-size: 10.5px; font-weight: 600; line-height: 17px; color: var(--accent); background: var(--accent-soft); }
.k-memo { color: #9a6c14; background: #fbf3e0; }

.transcript { margin-top: 8px; }
.turn { padding: 16px 0 6px; }
.who { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.who .av { width: 24px; height: 24px; font-size: 10px; }
.who .av.guess { opacity: 0.55; }
.av.anon { background: #9aa1ad; }
.who .name { font-weight: 650; color: var(--ink); font-size: 14px; }
.who .role { font-size: 12px; color: var(--muted); }
.who .est { font-size: 11px; color: var(--muted); padding: 0 5px; border: 1px dashed #c9ced6; border-radius: 3px; line-height: 16px; }
.who .t { margin-left: 2px; }
.who .wall { font-size: 11.5px; color: var(--faint); font-variant-numeric: tabular-nums; }
.who.plain { margin-bottom: 2px; }
.lines { padding-left: 32px; }
.who.plain + .lines { padding-left: 0; }
.lines p { margin: 0 0 8px; font-size: 15px; line-height: 1.8; color: var(--text); }
.s { border-radius: 3px; transition: background .2s; }
.s.now { background: var(--accent-soft); box-shadow: 0 0 0 2px var(--accent-soft); }

mark { color: inherit; padding: 1px 0; border-radius: 2px; }
.hl-yellow { background: #fcf3d4; } .hl-green { background: #e4f3e8; } .hl-blue { background: #e4f0fa; }
.hl-pink { background: #f8e1ec; } .hl-purple { background: #ebe3f5; }
mark.cm { background: rgba(47, 69, 184, 0.045); border-bottom: 1.5px solid rgba(47, 69, 184, 0.28); border-radius: 0; }
.cn { display: inline-grid; place-items: center; min-width: 15px; height: 15px; margin: 0 1px 0 2px; padding: 0 3px; border: 0; border-radius: 8px; background: #e6e9f8; color: #4152b4 !important; font: 600 9.5px/1 var(--mono); vertical-align: 4px; }
del { color: #b45454; text-decoration-color: rgba(180, 84, 84, 0.5); background: #fdf1f1; border-radius: 2px; }
ins { margin-left: 2px; color: #237347; text-decoration: none; background: #ebf6ef; border-radius: 2px; padding: 0 1px; }
.ed { border-bottom: 1px dotted #4b9b6c; }

.memo { display: flex; align-items: baseline; gap: 10px; margin: 2px 0 10px; padding: 7px 12px 8px; background: #fffcf5; border: 1px solid #f4ecd6; border-radius: 6px; }
.memo-h { flex: none; display: inline-flex; align-items: center; gap: 6px; transform: translateY(-1px); }
.memo-tag { display: inline-block; padding: 0 6px; border-radius: 3px; font-size: 11px; font-weight: 600; line-height: 18px; color: #9a6c14; background: #faf0d7; }
.memo .t { margin: 0; background: transparent; border-color: #efe4c4; color: #a07a2c; }
.memo p { margin: 0 !important; font-size: 14px !important; line-height: 1.6 !important; color: #3a3f48 !important; }
.free { padding: 14px 0 0 32px; }

.cmt { display: flex; gap: 9px; margin: -2px 0 6px; padding: 6px 12px 7px; background: #f9fafe; border-left: 2px solid #ccd3f0; border-radius: 0 6px 6px 0; font-size: 14px; line-height: 1.6; }
.cmt + p, .cmt + .memo { margin-top: 10px; }
.cmt .n { flex: none; display: grid; place-items: center; min-width: 18px; height: 18px; padding: 0 4px; margin-top: 2px; border-radius: 9px; background: #e6e9f8; color: #4152b4; font: 600 10px/1 var(--mono); }
.cmt .cb { min-width: 0; }
.cmt .body { margin: 0; color: #3a3f48; }
.cmt .was { margin: 2px 0 0; font-size: 12.5px; color: var(--muted); }
.cmt .reply { margin: 4px 0 0; padding-left: 10px; border-left: 2px solid var(--line); color: #4b5260; }
.cmt .tag { margin-left: 6px; }
.cmt:target { animation: flash 1.6s ease-out; }
@keyframes flash { from { background: var(--accent-soft); } to { background: transparent; } }

.divider { display: flex; align-items: center; gap: 14px; margin: 22px 0 10px; color: var(--muted); font-size: 12.5px; font-weight: 500; }
.divider::before, .divider::after { content: ""; flex: 1; height: 1px; background: var(--line); }

.orphans { margin-top: 36px; padding-top: 14px; border-top: 1px solid var(--line); }
.orphans h2 { margin: 0 0 2px; font-size: 14px; color: var(--ink); }
.orphans .hint { margin: 0 0 8px; font-size: 12.5px; color: var(--muted); }
.orphans ul { list-style: none; margin: 0; padding: 0; }
.orphans li { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; padding: 6px 0; font-size: 13.5px; border-bottom: 1px solid var(--line-2); }
.orphans .q { color: var(--muted); }

@media screen { .turn { content-visibility: auto; contain-intrinsic-size: auto 120px; } }
@media (max-width: 860px) {
  .legend { margin-left: 0; width: 100%; }
  .index ol { columns: 1; }
}
@media (max-width: 720px) {
  .lines, .free { padding-left: 0; }
  .cmt { font-size: 13.5px; }
  .memo { flex-direction: column; gap: 3px; }
  .lines p { font-size: 14.5px; }
}
@media print {
  .turn { padding-top: 10px; }
  .who { break-after: avoid; }
  .lines p, .cmt, .memo { break-inside: avoid; }
  .lines p { font-size: 10pt; }
  .cmt { font-size: 9pt; }
  .memo p { font-size: 9.5pt !important; }
  .index { display: none; }
  mark, del, ins, .memo, .memo-tag, .cn, .cmt, .cmt .n { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
"""

JS = r"""
(function () {
  var au = document.getElementById('au'); if (!au) return;
  var spans = Array.prototype.slice.call(document.querySelectorAll('.s[data-t]'));
  var times = spans.map(function (s) { return parseFloat(s.dataset.t); });
  var cur = null;
  au.addEventListener('timeupdate', function () {
    var t = au.currentTime, lo = 0, hi = times.length - 1, i = -1;
    while (lo <= hi) { var mid = (lo + hi) >> 1; if (times[mid] <= t + 0.05) { i = mid; lo = mid + 1; } else hi = mid - 1; }
    var el = i >= 0 ? spans[i] : null;
    if (el === cur) return;
    if (cur) cur.classList.remove('now');
    if (el) el.classList.add('now');
    cur = el;
  });
})();
"""

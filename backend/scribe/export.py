"""회의 전체(기록 + 메모·댓글 + 노트 + 요약)를 Markdown으로 만든다. AI(MCP)와 내보내기에서 사용.
원본 스크립트(후처리 결과)는 참고용으로 따로 만든다 (script_markdown)."""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from .textutil import fmt_time
from .views import Views, speaker_name

KIND_LABEL = {"comment": "댓글", "memo": "메모", "highlight": "강조", "divider": "구분선"}


def divider_text(ann: dict) -> str:
    """'쉬는 시간 · 14:02–14:15 · 13분' 형식."""
    label = (ann.get("body") or "").strip() or "구분선"
    meta = ann.get("meta") or {}
    bs, be = meta.get("break_start"), meta.get("break_end")
    if bs and be:
        a = _dt.datetime.fromtimestamp(bs).strftime("%H:%M")
        b = _dt.datetime.fromtimestamp(be).strftime("%H:%M")
        mins = max(1, round((be - bs) / 60))
        return f"{label} · {a}–{b} · {mins}분"
    return label


def _local_ranges(units: list[dict], annotations: list[dict]) -> dict[str, list[tuple[int, int, dict, bool]]]:
    """unit별 (start, end, ann, is_last_unit) 목록."""
    order = {u["id"]: i for i, u in enumerate(units)}
    per: dict[str, list[tuple[int, int, dict, bool]]] = {}
    for ann in annotations:
        pos = ann.get("pos")
        if not pos or "start" not in pos:
            continue
        su, eu = order.get(pos["start"]["unit"]), order.get(pos["end"]["unit"])
        if su is None or eu is None:
            continue
        for i in range(su, eu + 1):
            u = units[i]
            s = pos["start"]["offset"] if i == su else 0
            e = pos["end"]["offset"] if i == eu else len(u["text"])
            if e > s:
                per.setdefault(u["id"], []).append((s, e, ann, i == eu))
    return per


def _render_unit_text(text: str, ranges: list[tuple[int, int, dict, bool]], notes: list[str], counter: list[int]) -> str:
    ranges = sorted(ranges, key=lambda r: (r[0], -r[1]))
    out: list[str] = []
    cur = 0
    for s, e, ann, last in ranges:
        if s < cur:  # 겹치는 범위는 본문 표시는 건너뛰고 설명만 남긴다
            if last and ann["kind"] == "comment":
                counter[0] += 1
                notes.append(f"  - [댓글 c{counter[0]}] {ann['body']}")
            continue
        out.append(text[cur:s])
        piece = text[s:e]
        kind = ann["kind"]
        if kind == "highlight":
            out.append(f"=={piece}==")
        elif kind == "comment":
            if last:
                counter[0] += 1
                out.append(f"=={piece}==[c{counter[0]}]")
                replies = "".join(f" / 답글: {r.get('text', '')}" for r in ann.get("replies") or [])
                resolved = " (해결됨)" if ann.get("status") == "resolved" else ""
                notes.append(f"  - [댓글 c{counter[0]}] {ann['body']}{replies}{resolved}")
            else:
                out.append(f"=={piece}==")
        else:
            out.append(piece)
        cur = e
    out.append(text[cur:])
    return "".join(out)


def transcript_markdown(
    units: list[dict],
    annotations: list[dict],
    speakers: dict,
    *,
    include_annotations: bool = True,
    start: Optional[float] = None,
    end: Optional[float] = None,
) -> str:
    anns = annotations if include_annotations else [a for a in annotations if a["kind"] == "divider"]
    per = _local_ranges(units, anns)
    memos_after: dict[Optional[str], list[dict]] = {}
    for ann in anns:
        pos = ann.get("pos")
        if ann["kind"] in ("memo", "divider") and pos is not None and "after" in pos:
            memos_after.setdefault(pos["after"], []).append(ann)
    counter = [0]
    lines: list[str] = []

    def memo_lines(key: Optional[str]) -> None:
        for m in sorted(memos_after.get(key, []), key=lambda a: a["anchor"].get("t", 0)):
            if m["kind"] == "divider":
                lines.append(f"──── {divider_text(m)} ────")
            else:
                lines.append(f"> [메모 {fmt_time(m['anchor'].get('t', 0))}] {m['body']}")

    if start is None:
        memo_lines(None)
    for u in units:
        if start is not None and u["t1"] < start:
            continue
        if end is not None and u["t0"] > end:
            break
        notes: list[str] = []
        body = _render_unit_text(u["text"], per.get(u["id"], []), notes, counter)
        who = speaker_name(speakers, u.get("speaker"))
        prefix = f"[{fmt_time(u['t0'])}]" + (f" {who}:" if who else "")
        lines.append(f"{prefix} {body}")
        lines.extend(notes)
        memo_lines(u["id"])
    return "\n".join(lines)


LEGEND = (
    "표기: ==강조== / ==댓글 단 부분==[c1] 아래에 댓글 / > [메모 시각] 사용자가 회의 중 남긴 메모 / "
    "──── 구분선 ──── 쉬는 시간이나 회의 단락. 기록의 글자는 사용자가 직접 고친 것이 반영되어 있다. "
    "사용자의 메모·댓글은 중요한 신호이므로 우선 반영할 것."
)


def record_source(views: Views, meeting: dict) -> dict:
    """회의 기록과 메모·댓글, 화자 이름표. 회의 중 고른(또는 목소리로 추정한) 화자를 쓰고,
    이름이 없는 목소리 묶음은 "화자 A(추정)"."""
    lv = views.live_view(meeting["id"])
    speakers = {p["id"]: p["name"] for p in meeting.get("participants") or []}
    units = []
    for u in lv["units"]:
        who = u.get("speaker") if u.get("speaker") in speakers else (f"?{u['speaker_auto']}" if u.get("speaker_auto") else None)
        if who and who.startswith("?"):
            speakers[who] = f"화자 {who[1:]}(추정)"
        units.append(dict(u, speaker=who))
    upload = meeting.get("source") == "upload"
    return {"units": units, "annotations": lv["annotations"], "speakers": speakers,
            "kind": "파일을 받아 적은 기록" if upload else "회의 중 받아 적은 기록"}


def meeting_markdown(
    views: Views,
    meeting_id: str,
    *,
    include_notes: bool = True,
    include_annotations: bool = True,
    include_summary: bool = False,
    start: Optional[float] = None,
    end: Optional[float] = None,
) -> Optional[str]:
    meeting = views.store.get_meeting(meeting_id, with_notes=True)
    if meeting is None:
        return None
    src = record_source(views, meeting)
    units, anns, speakers, kind = src["units"], src["annotations"], src["speakers"], src["kind"]

    created = _dt.datetime.fromtimestamp(meeting["created_at"]).strftime("%Y-%m-%d %H:%M")
    lines = [f"# {meeting['title'] or '제목 없는 회의'}", ""]
    lines.append(f"- 회의 ID: {meeting['id']}")
    lines.append(f"- 일시: {created}")
    lines.append(f"- 길이: {fmt_time(meeting.get('duration') or 0, always_hours=True)}")
    people = meeting.get("participants") or []
    if people:
        lines.append("- 참석자:")
        for p in people:
            info = " · ".join(x for x in (p.get("org"), p.get("role")) if x)
            lines.append(f"  - {p['name']}" + (f" ({info})" if info else "") + (f" — {p['note']}" if p.get("note") else ""))
    edited = sum(1 for u in units if u.get("text_orig") is not None)
    lines.append(f"- 기록 종류: {kind}" + (f" (사용자가 {edited}문장을 직접 고침)" if edited else ""))
    lines.append("")
    if include_summary and meeting.get("summary_md"):
        lines += ["## 저장된 AI 요약", "", meeting["summary_md"].strip(), ""]
    if include_notes:
        notes_md = (meeting.get("notes_md") or "").strip()
        lines += ["## 내 노트", "", notes_md or "(작성한 노트 없음)", ""]
    lines += ["## 회의 기록", ""]
    if include_annotations:
        lines += [LEGEND, ""]
    body = transcript_markdown(units, anns, speakers, include_annotations=include_annotations, start=start, end=end)
    lines.append(body or "(기록 없음)")
    return "\n".join(lines).rstrip() + "\n"


def script_markdown(store, meeting_id: str) -> Optional[str]:
    """원본 스크립트: 녹음을 마친 뒤 큰 모델로 다시 받아 적고 화자를 나눈 결과 (참고용, 기록과 섞지 않는다)"""
    meeting = store.get_meeting(meeting_id)
    final = store.get_final(meeting_id) if meeting else None
    if final is None:
        return None
    data = final["data"]
    utt = data.get("utterances") or []
    made = _dt.datetime.fromtimestamp(final["created_at"]).strftime("%Y-%m-%d %H:%M")
    lines = [f"# {meeting['title'] or '제목 없는 회의'} — 원본 스크립트", "",
             "> 녹음을 마친 뒤 더 큰 모델로 처음부터 다시 받아 적고 화자를 나눈 **참고용** 기록입니다.",
             "> 회의 기록(회의 중 받아 적고 사람이 고친 글, 메모·댓글)과는 따로 두며 서로 섞지 않습니다.", "",
             f"- 만든 시각: {made}", f"- 길이: {fmt_time(data.get('duration') or meeting.get('duration') or 0, always_hours=True)}",
             f"- 받아쓰기: {final.get('engine') or '-'}", f"- 화자 구분: {final.get('diarizer') or '-'}", ""]
    speakers = meeting.get("speakers") or {}
    prev = object()
    para: list[str] = []

    def flush() -> None:
        if para:
            lines.append(" ".join(para))
            lines.append("")
            para.clear()

    for u in utt:
        who = speaker_name(speakers, u.get("speaker"))
        if who != prev:
            flush()
            lines.append(f"**{who}** · {fmt_time(u['t0'], always_hours=True)}" if who else f"`{fmt_time(u['t0'], always_hours=True)}`")
            prev = who
        para.append(u["text"].strip())
    flush()
    if not utt:
        lines.append("(받아 적은 내용 없음)")
    return "\n".join(lines).rstrip() + "\n"

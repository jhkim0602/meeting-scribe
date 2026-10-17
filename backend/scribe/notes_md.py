"""BlockNote 문서(JSON) → Markdown. MCP와 내보내기에서 사용한다."""

from __future__ import annotations

from typing import Any

from .textutil import fmt_time


def _inline(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    out: list[str] = []
    for item in content:
        kind = item.get("type")
        if kind == "text":
            text = item.get("text", "")
            styles = item.get("styles") or {}
            if not text:
                continue
            core = text.strip()
            if not core:
                out.append(text)
                continue
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()) :]
            text = core
            if styles.get("code"):
                text = f"`{text}`"
            if styles.get("bold"):
                text = f"**{text}**"
            if styles.get("italic"):
                text = f"*{text}*"
            if styles.get("strike"):
                text = f"~~{text}~~"
            out.append(lead + text + trail)
        elif kind == "link":
            out.append(f"[{_inline(item.get('content'))}]({item.get('href', '')})")
        elif kind == "timestamp":
            t = float((item.get("props") or {}).get("t", 0))
            out.append(f"[{fmt_time(t)}]")
        elif kind == "mention":
            out.append(f"@{(item.get('props') or {}).get('label', '')}")
        else:
            out.append(_inline(item.get("content")))
    return "".join(out)


def _table(content: dict) -> list[str]:
    rows = content.get("rows") or []
    lines: list[str] = []
    for r_i, row in enumerate(rows):
        cells = []
        for cell in row.get("cells") or []:
            if isinstance(cell, dict) and cell.get("type") == "tableCell":
                cells.append(_inline(cell.get("content")).replace("|", "\\|"))
            else:
                cells.append(_inline(cell).replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
        if r_i == 0:
            lines.append("|" + "|".join([" --- "] * max(1, len(cells))) + "|")
    return lines


def _blocks(blocks: list[dict], depth: int, lines: list[str]) -> None:
    indent = "  " * depth
    num = 0
    for b in blocks or []:
        kind = b.get("type")
        props = b.get("props") or {}
        content = b.get("content")
        text = _inline(content) if not isinstance(content, dict) else ""
        if kind != "numberedListItem":
            num = 0
        if kind == "heading":
            level = int(props.get("level", 1))
            lines.append(f"{'#' * min(6, level + 1)} {text}")
        elif kind == "bulletListItem":
            lines.append(f"{indent}- {text}")
        elif kind == "numberedListItem":
            num += 1
            lines.append(f"{indent}{num}. {text}")
        elif kind == "checkListItem":
            lines.append(f"{indent}- [{'x' if props.get('checked') else ' '}] {text}")
        elif kind == "toggleListItem":
            lines.append(f"{indent}- ▸ {text}")
        elif kind == "quote":
            lines.append(f"{indent}> {text}")
        elif kind == "codeBlock":
            lines.append(f"```{props.get('language', '')}")
            lines.append(text)
            lines.append("```")
        elif kind == "table" and isinstance(content, dict):
            lines.extend(_table(content))
        elif kind == "image":
            lines.append(f"![{props.get('caption', '')}]({props.get('url', '')})")
        elif kind == "divider":
            lines.append("---")
        else:
            lines.append(f"{indent}{text}" if text else "")
        children = b.get("children") or []
        if children:
            _blocks(children, depth + 1, lines)


LIST_TYPES = {"bulletListItem", "numberedListItem", "checkListItem", "toggleListItem"}


def blocks_to_markdown(blocks: Any) -> str:
    """최상위 블록 사이에는 빈 줄을 넣는다 (같은 종류의 목록이 이어질 때는 제외)."""
    if not blocks:
        return ""
    lines: list[str] = []
    prev_type = None
    for b in blocks:
        kind = b.get("type")
        if lines and not (kind in LIST_TYPES and prev_type in LIST_TYPES):
            lines.append("")
        _blocks([b], 0, lines)
        prev_type = kind
    # 연속된 빈 줄 정리
    out: list[str] = []
    for line in lines:
        if line == "" and out and out[-1] == "":
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"

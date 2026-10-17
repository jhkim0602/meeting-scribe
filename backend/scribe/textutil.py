"""문자 정규화 공용 함수."""

from __future__ import annotations

import unicodedata


def is_kept(ch: str) -> bool:
    """글자/숫자만 비교 대상으로 남긴다 (공백, 문장부호 무시)."""
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "N")


def normalize(text: str) -> str:
    return "".join(ch.lower() for ch in text if is_kept(ch))


SENTENCE_END = tuple(".?!。…？！")


def fmt_time(seconds: float, always_hours: bool = False) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h or always_hours:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

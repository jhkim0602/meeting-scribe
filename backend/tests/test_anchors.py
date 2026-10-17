from scribe.anchors import TextIndex, compute_anchor, memo_anchor, resolve, resolve_annotations

# 실시간 기록: 문장 경계와 글자가 원본과 다르다 (오인식 포함)
LIVE = [
    {"id": "L0", "t0": 0.0, "t1": 6.0, "text": "오늘 회의는 삼분기 예산 검토부터 시작하겠습니다"},
    {"id": "L1", "t0": 6.5, "t1": 12.0, "text": "마케팅 비용을 이십 퍼센트 줄이는 안이 올라와 있어요"},
    {"id": "L2", "t0": 12.5, "t1": 18.0, "text": "네 그러면 다음 주까지 김 팀장님이 정리해 주세요"},
]


def _words(text, t0, t1):
    toks = text.split(" ")
    out, pos = [], 0
    dur = (t1 - t0) / len(toks)
    for i, tok in enumerate(toks):
        o = text.index(tok, pos)
        out.append([o, len(tok), round(t0 + i * dur, 3), round(t0 + (i + 1) * dur, 3)])
        pos = o + len(tok)
    return out


# 원본: 화자별로 나뉘고 띄어쓰기/문장부호/일부 단어가 다르다
FINAL_TEXTS = [
    ("u0", 0.0, 6.2, "S1", "오늘 회의는 3분기 예산 검토부터 시작하겠습니다."),
    ("u1", 6.4, 12.1, "S1", "마케팅 비용을 20% 줄이는 안이 올라와 있어요."),
    ("u2", 12.4, 14.0, "S2", "네."),
    ("u3", 14.1, 18.2, "S1", "그러면 다음 주까지 김팀장님이 정리해 주세요."),
]
FINAL = [{"id": i, "t0": a, "t1": b, "speaker": sp, "text": t, "words": _words(t, a, b)} for i, a, b, sp, t in FINAL_TEXTS]


def test_compute_anchor_basic():
    idx = TextIndex(LIVE)
    s = LIVE[1]["text"].index("마케팅")
    e = s + len("마케팅 비용을")
    a = compute_anchor(idx, {"unit": "L1", "offset": s}, {"unit": "L1", "offset": e})
    assert a["quote"] == "마케팅 비용을"
    assert 6.5 <= a["t0"] < a["t1"] <= 12.0
    assert a["start"] == {"unit": "L1", "offset": s}


def test_resolve_on_final_finds_same_words():
    live = TextIndex(LIVE)
    final = TextIndex(FINAL)
    s = LIVE[2]["text"].index("김 팀장님이")
    a = compute_anchor(live, {"unit": "L2", "offset": s}, {"unit": "L2", "offset": s + len("김 팀장님이 정리해")})
    pos = resolve(a, final)
    assert pos is not None and not pos["approx"]
    assert pos["start"]["unit"] == "u3"
    assert pos["text"] == "김팀장님이 정리해"


def test_resolve_with_number_difference_falls_back_reasonably():
    live = TextIndex(LIVE)
    final = TextIndex(FINAL)
    s = LIVE[1]["text"].index("이십 퍼센트")
    a = compute_anchor(live, {"unit": "L1", "offset": s}, {"unit": "L1", "offset": s + len("이십 퍼센트 줄이는")})
    pos = resolve(a, final)
    assert pos is not None
    assert pos["start"]["unit"] == "u1"
    assert "줄이는" in pos["text"]


def test_cross_unit_selection_resolves_across_speakers():
    live = TextIndex(LIVE)
    final = TextIndex(FINAL)
    s = LIVE[1]["text"].index("올라와")
    a = compute_anchor(live, {"unit": "L1", "offset": s}, {"unit": "L2", "offset": len("네 그러면")})
    assert "올라와 있어요 네 그러면" == a["quote"]
    pos = resolve(a, final)
    assert pos["start"]["unit"] == "u1"
    assert pos["end"]["unit"] in ("u2", "u3")


def test_same_source_fast_path_is_exact():
    live = TextIndex(LIVE)
    a = compute_anchor(live, {"unit": "L0", "offset": 0}, {"unit": "L0", "offset": 2})
    pos = resolve(a, live, same_source=True)
    assert pos["start"] == {"unit": "L0", "offset": 0} and pos["end"] == {"unit": "L0", "offset": 2}
    assert pos["score"] == 100.0


def test_short_quote_prefers_time():
    units = [
        {"id": "a", "t0": 0, "t1": 2, "text": "네 네"},
        {"id": "b", "t0": 30, "t1": 32, "text": "네 맞아요"},
    ]
    idx = TextIndex(units)
    pos = resolve({"t0": 30.2, "t1": 30.5, "quote": "네"}, idx)
    assert pos["start"]["unit"] == "b"


def test_memo_maps_to_unit_by_time():
    live = TextIndex(LIVE)
    final = TextIndex(FINAL)
    m = memo_anchor(live, 12.2)  # L1 직후
    assert m["after"] == "L1"
    assert resolve(m, live, same_source=True) == {"after": "L1"}
    assert resolve(m, final) == {"after": "u1"}
    assert resolve({"t": -1.0}, final) == {"after": None}


def test_edit_match_status():
    live = TextIndex(LIVE)
    final = TextIndex(FINAL)
    s = LIVE[2]["text"].index("김 팀장님이")
    a = compute_anchor(live, {"unit": "L2", "offset": s}, {"unit": "L2", "offset": s + len("김 팀장님이")})
    anns = [
        {"id": "e1", "kind": "edit", "origin": "live", "origin_version": 0, "anchor": a, "replacement": "김팀장님이"},
        {"id": "e2", "kind": "edit", "origin": "live", "origin_version": 0, "anchor": a, "replacement": "이 팀장님이"},
    ]
    out = resolve_annotations(anns, final, "final", 1)
    assert out[0]["match"] == "same"
    assert out[1]["match"] == "differs"


def test_time_fallback_when_text_unrecognizable():
    final = TextIndex(FINAL)
    pos = resolve({"t0": 7.0, "t1": 9.0, "quote": "전혀 다른 말입니다 블라블라"}, final)
    assert pos is not None and pos["approx"] is True
    assert pos["start"]["unit"] == "u1"


def test_empty_index():
    assert resolve({"t0": 1, "t1": 2, "quote": "x"}, TextIndex([])) is None

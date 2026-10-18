"""위치: 기록 하나 안에서 문장 id + 글자 위치. 사람이 문장을 고치면 글자 차이대로 정확히 옮긴다."""

from scribe.anchors import TextIndex, compute_anchor, edit_opcodes, memo_anchor, remap_end, remap_start, resolve, resolve_annotations

LIVE = [
    {"id": "L0", "t0": 0.0, "t1": 6.0, "text": "오늘 회의는 삼분기 예산 검토부터 시작하겠습니다"},
    {"id": "L1", "t0": 6.5, "t1": 12.0, "text": "마케팅 비용을 이십 퍼센트 줄이는 안이 올라와 있어요"},
    {"id": "L2", "t0": 12.5, "t1": 18.0, "text": "네 그러면 다음 주까지 김 팀장님이 정리해 주세요"},
]


def test_compute_anchor_and_resolve_are_exact():
    idx = TextIndex(LIVE)
    s = LIVE[1]["text"].index("이십 퍼센트")
    a = compute_anchor(idx, {"unit": "L1", "offset": s}, {"unit": "L1", "offset": s + len("이십 퍼센트")})
    assert a["quote"] == "이십 퍼센트" and 6.5 <= a["t0"] <= a["t1"] <= 12.0
    assert "prefix" not in a  # 다른 기록에서 다시 찾지 않으므로 앞뒤 글자는 필요 없다
    pos = resolve(a, idx)
    assert pos == {"start": {"unit": "L1", "offset": s}, "end": {"unit": "L1", "offset": s + 6}, "text": "이십 퍼센트"}


def test_cross_unit_selection():
    idx = TextIndex(LIVE)
    a = compute_anchor(idx, {"unit": "L0", "offset": 20}, {"unit": "L1", "offset": 3})
    assert a["start"]["unit"] == "L0" and a["end"]["unit"] == "L1"
    assert resolve(a, idx)["text"] == a["quote"]


def test_memo_after_unit_or_by_time():
    idx = TextIndex(LIVE)
    assert resolve(memo_anchor(idx, 7.0, "L0"), idx) == {"after": "L0"}
    assert resolve(memo_anchor(idx, 13.0), idx) == {"after": "L2"}
    assert resolve({"t": 13.0, "after": "gone"}, idx) == {"after": "L2"}  # 문장이 없으면 시각으로


def test_missing_unit_or_empty_range_has_no_position():
    idx = TextIndex(LIVE)
    assert resolve({"start": {"unit": "X", "offset": 0}, "end": {"unit": "X", "offset": 2}}, idx) is None
    assert resolve({"start": {"unit": "L0", "offset": 3}, "end": {"unit": "L0", "offset": 3}}, idx) is None
    anns = resolve_annotations([{"id": "a", "anchor": {"start": {"unit": "L0", "offset": 0}, "end": {"unit": "L0", "offset": 2}}}], idx)
    assert anns[0]["pos"]["text"] == "오늘"


def _moved(old, new, s, e):
    ops = edit_opcodes(old, new)
    return new[remap_start(ops, s):remap_end(ops, e)]


def test_edit_moves_positions_by_the_exact_difference():
    old = LIVE[1]["text"]
    s, e = old.index("이십 퍼센트"), old.index("이십 퍼센트") + 6
    assert _moved(old, old.replace("이십 퍼센트", "20%"), s, e) == "20%"  # 고친 글자 자체
    assert _moved(old, "올해 " + old, s, e) == "이십 퍼센트"  # 앞에 글자를 넣어도 그대로
    assert _moved(old, old.replace("마케팅", "광고"), s, e) == "이십 퍼센트"
    assert _moved(old, old.replace("이십", "삼십"), s, e) == "삼십 퍼센트"  # 일부만 고침
    assert _moved("abc", "abXc", 2, 3) == "c"  # 시작 바로 앞에 끼운 글자는 넣지 않는다
    assert _moved("abc", "abcX", 0, 3) == "abc"  # 끝 바로 뒤에 끼운 글자도
    assert _moved(old, old.replace("이십 퍼센트 ", ""), s, e) == ""  # 다 지우면 빈 범위

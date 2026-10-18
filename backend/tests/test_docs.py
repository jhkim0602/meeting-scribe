"""회의 문서: 메모·댓글이 아주 많은 긴 회의에서도 짧고 빠르게, 고친 문장은 고친 대로."""

from scribe import minutes, transcript_html


def _units(n):
    return [{"id": f"u{i}", "t0": i * 5.0, "t1": i * 5.0 + 4, "text": f"문장 {i}번 지난달 비용이 이십 퍼센트 늘었습니다", "speaker": "S1"} for i in range(n)]


def _comment(i, quote="지난달"):
    s = len(f"문장 {i}번 ")
    return {"id": f"c{i}", "kind": "comment", "body": f"생각 {i}", "status": "open", "replies": [],
            "anchor": {"t0": i * 5.0, "quote": quote},
            "pos": {"start": {"unit": f"u{i}", "offset": s}, "end": {"unit": f"u{i}", "offset": s + 3}, "text": quote}}


def _data(units, anns, **kw):
    return {"id": "m1234", "title": "긴 회의", "start": 1_760_000_000.0, "duration": 10800.0, "place": "", "participants": ["김민지"],
            "people": [{"name": "김민지"}], "clock": [], "units": units, "annotations": anns, "speakers": {"S1": "김민지"}, "kind": "회의 중 받아 적은 기록", **kw}


def test_dense_transcript_folds_the_index():
    few = transcript_html.render(_data(_units(5), [_comment(i) for i in range(5)]))
    assert '<details class="index" open>' in few
    many = transcript_html.render(_data(_units(30), [_comment(i) for i in range(30)]))
    assert '<details class="index">' in many and "메모 0 · 댓글 30" in many  # 많으면 접고 개수만
    assert many.count('class="cmt"') == 30  # 댓글은 모두 그 문장 아래에


def test_edited_sentences_show_as_edited_text_only():
    units = _units(3)
    units[1] = dict(units[1], text="문장 1번 지난달 비용이 20% 늘었습니다", text_orig=units[1]["text"])
    h = transcript_html.render(_data(units, [_comment(0)]))
    span = h[h.index('id="u-u1"'):]
    assert span[span.index(">") + 1:span.index("</span>")] == "문장 1번 지난달 비용이 20% 늘었습니다"
    assert "고친 문장 <b>1</b>" in h and "<del>" not in h


def test_dense_minutes_keep_memos_and_point_to_transcript_for_comments():
    marks = [{"kind": "comment", "t": i, "body": f"생각 {i}", "quote": "q", "replies": [], "resolved": False} for i in range(50)]
    marks += [{"kind": "memo", "t": 100 + i, "body": f"메모 {i}", "quote": None, "replies": [], "resolved": False} for i in range(3)]
    data = {"id": "m1234", "title": "긴 회의", "start": 1_760_000_000.0, "duration": 10800.0, "place": "", "participants": [],
            "people": [], "summary": "", "notes": [], "marks": marks}
    h = minutes.render(data)
    assert '<span class="ttl">회의 중 메모</span>' in h and "댓글 50개는 전사 기록에서" in h
    assert "메모 2" in h and "생각 7" not in h
    few = minutes.render(dict(data, marks=marks[:5] + marks[-1:]))
    assert '<span class="ttl">회의 중 메모·댓글</span>' in few and "생각 3" in few


def test_legacy_edit_suggestions_become_direct_edits_once(tmp_path):
    """예전 "수정 제안": 실시간 기록에 단 것은 그 문장을 직접 고친 것으로, 무시한 것·원본에 단 것은 지운다 (한 번만)."""
    from scribe.anchors import TextIndex, compute_anchor
    from scribe.db import Store
    from scribe.record import apply_legacy_edits
    from scribe.views import Views

    store = Store(tmp_path / "scribe.db")
    views = Views(store)
    mid = store.create_meeting("예전 회의")["id"]
    s0 = store.add_live_segment(mid, 0, 4, "지난달 비용이 이십 퍼센트 늘었습니다")
    s1 = store.add_live_segment(mid, 5, 9, "다음 주까지 정리해 주세요")
    idx = TextIndex(views.units(mid))
    t = s0["text"]

    def rng(seg, word):
        o = seg["text"].index(word)
        return compute_anchor(idx, {"unit": seg["id"], "offset": o}, {"unit": seg["id"], "offset": o + len(word)})

    store.add_annotation(mid, kind="comment", origin="live", origin_version=0, anchor=rng(s0, "늘었습니다"), body="확인")
    store.add_annotation(mid, kind="edit", origin="live", origin_version=0, anchor=rng(s0, "이십 퍼센트"), replacement="20%", status="open")
    store.add_annotation(mid, kind="edit", origin="live", origin_version=0, anchor=rng(s1, "다음 주"), replacement="이번 주", status="dismissed")
    store.add_annotation(mid, kind="edit", origin="final", origin_version=1, anchor=rng(s1, "정리해"), replacement="정리", status="open")

    assert apply_legacy_edits(store, views) == 1
    units = {u["id"]: u for u in views.units(mid)}
    assert units[s0["id"]]["text"] == t.replace("이십 퍼센트", "20%") and units[s0["id"]]["text_orig"] == t
    assert units[s1["id"]]["text"] == s1["text"] and "text_orig" not in units[s1["id"]]
    anns = views.live_view(mid)["annotations"]
    assert [a["kind"] for a in anns] == ["comment"] and anns[0]["pos"]["text"] == "늘었습니다"
    assert apply_legacy_edits(store, views) == 0

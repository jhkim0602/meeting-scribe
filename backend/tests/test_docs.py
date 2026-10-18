"""회의 문서: 메모·댓글이 아주 많은 긴 회의에서도 짧고 빠르게, 후처리 원본으로 옮겨진 댓글은 회의 중 글자도 함께."""

from scribe import minutes, transcript_html


def _units(n):
    return [{"id": f"u{i}", "t0": i * 5.0, "t1": i * 5.0 + 4, "text": f"문장 {i}번 지난달 비용이 이십 퍼센트 늘었습니다", "speaker": "S1"} for i in range(n)]


def _comment(i, quote="지난달", text=None, approx=False):
    s = len(f"문장 {i}번 ")
    return {"id": f"c{i}", "kind": "comment", "body": f"생각 {i}", "status": "open", "replies": [],
            "anchor": {"t0": i * 5.0, "quote": quote},
            "pos": {"start": {"unit": f"u{i}", "offset": s}, "end": {"unit": f"u{i}", "offset": s + 3}, "approx": approx, "text": text or quote}}


def _data(units, anns, **kw):
    return {"id": "m1234", "title": "긴 회의", "start": 1_760_000_000.0, "duration": 10800.0, "place": "", "participants": ["김민지"],
            "people": [{"name": "김민지"}], "clock": [], "units": units, "annotations": anns, "speakers": {"S1": "김민지"}, "is_final": True, **kw}


def test_dense_transcript_folds_the_index():
    few = transcript_html.render(_data(_units(5), [_comment(i) for i in range(5)]))
    assert '<details class="index" open>' in few
    many = transcript_html.render(_data(_units(30), [_comment(i) for i in range(30)]))
    assert '<details class="index">' in many and "메모 0 · 댓글 30" in many  # 많으면 접고 개수만
    assert many.count('class="cmt"') == 30  # 댓글은 모두 그 문장 아래에


def test_comment_moved_to_final_shows_what_was_written_during_the_meeting():
    anns = [_comment(0), _comment(1, quote="이십 퍼센트", text="20%"), _comment(2, quote="이십 퍼센트", approx=True)]
    h = transcript_html.render(_data(_units(3), anns))
    assert h.count('class="was"') == 2
    assert "회의 중 기록: “이십 퍼센트”" in h and "자리 추정 · 회의 중 기록" in h


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

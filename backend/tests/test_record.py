"""기록 고치기와 색인: 긴 회의에서도 필요한 문장만 다루고, 다른 프로세스가 쓴 것도 바로 보인다."""

from scribe.anchors import compute_anchor
from scribe.db import Store
from scribe.record import edit_segment
from scribe.views import Views


def _meeting(tmp_path, texts):
    store = Store(tmp_path / "scribe.db")
    mid = store.create_meeting("회의")["id"]
    segs = [store.add_live_segment(mid, i * 4.0, i * 4.0 + 3.0, t) for i, t in enumerate(texts)]
    return store, Views(store), mid, segs


def _comment(store, views, mid, start, end, body="c"):
    anchor = compute_anchor(views.index(mid), start, end)
    return store.add_annotation(mid, kind="comment", origin="live", anchor=anchor, body=body)


def test_index_follows_writes_from_another_connection(tmp_path):
    store, views, mid, segs = _meeting(tmp_path, ["첫 문장입니다", "둘째 문장입니다"])
    first = views.index(mid)
    assert views.index(mid) is first  # 바뀐 게 없으면 다시 만들지 않는다
    other = Store(tmp_path / "scribe.db")  # 워커처럼 다른 연결
    other.add_live_segment(mid, 9.0, 11.0, "셋째 문장")
    grown = views.index(mid)
    assert grown is not first and [u["text"] for u in grown.units][-1] == "셋째 문장"
    other.set_live_text(segs[0]["id"], "첫 문장을 고쳤어요")
    assert views.index(mid).units[0]["text"] == "첫 문장을 고쳤어요"
    assert views.index(mid).units[0]["text_orig"] == "첫 문장입니다"


def test_edit_moves_a_comment_that_spans_sentences(tmp_path):
    store, views, mid, segs = _meeting(tmp_path, ["가나다 라마바", "사아자 차카타", "파하 끝"])
    a, b, c = (s["id"] for s in segs)
    span = _comment(store, views, mid, {"unit": a, "offset": 4}, {"unit": c, "offset": 2})  # "라마바 … 파하"
    other = _comment(store, views, mid, {"unit": b, "offset": 0}, {"unit": b, "offset": 3})
    r = edit_segment(store, views, mid, a, "가나다 아주 라마바")
    assert [x["id"] for x in r["annotations"]] == [span["id"]]  # 다른 문장에만 걸린 댓글은 건드리지 않는다
    moved = r["annotations"][0]
    assert moved["anchor"]["start"] == {"unit": a, "offset": 7} and moved["anchor"]["end"] == {"unit": c, "offset": 2}
    assert moved["anchor"]["quote"] == "라마바 사아자 차카타 파하"
    assert moved["pos"]["text"] == "라마바 사아자 차카타 파하"
    # 가운데 문장을 고치면 위치(문장 + 글자)는 그대로다
    assert edit_segment(store, views, mid, b, "사아자 고친 차카타")["annotations"] == [
        dict(store.get_annotation(other["id"]), pos={"start": {"unit": b, "offset": 0}, "end": {"unit": b, "offset": 3}, "text": "사아자"})
    ]
    assert store.get_annotation(span["id"])["anchor"]["start"]["offset"] == 7


def test_resolve_one_reads_only_the_needed_sentences(tmp_path, monkeypatch):
    store, views, mid, segs = _meeting(tmp_path, [f"문장 {i}번입니다" for i in range(50)])
    ann = _comment(store, views, mid, {"unit": segs[20]["id"], "offset": 0}, {"unit": segs[21]["id"], "offset": 2})
    memo = store.add_annotation(mid, kind="memo", origin="live", anchor={"t": 30.0, "after": segs[7]["id"]}, body="m")
    monkeypatch.setattr(views, "index", lambda m: (_ for _ in ()).throw(AssertionError("기록 전체를 읽었어요")))
    assert views.resolve_one(ann)["pos"]["text"] == "문장 20번입니다 문장"
    assert views.resolve_one(memo)["pos"] == {"after": segs[7]["id"]}


def test_upload_windows_go_in_time_order_once(tmp_path):
    from scribe.record import add_window_rows

    store = Store(tmp_path / "scribe.db")
    mid = store.create_meeting("올린 파일", source="upload")["id"]
    w1 = [{"text": "첫 구간 첫 문장. 첫 구간 둘째 문장.", "start": 1.0, "end": 8.0, "words": []}]
    w2 = [{"text": "둘째 구간 문장입니다.", "start": 21.0, "end": 25.0, "words": []}]
    assert add_window_rows(store, mid, w2, 20.0, 40.0) == 1  # 멈췄다 이어 하면 뒤 구간이 먼저 들어갈 수 있다
    assert add_window_rows(store, mid, w1, 0.0, 20.0) == 1
    assert add_window_rows(store, mid, w1, 0.0, 20.0) == 0  # 이미 있는 구간은 그대로
    segs = store.list_live_segments(mid)
    assert [s["t0"] for s in segs] == [1.0, 21.0] and [s["idx"] for s in segs] == [0, 1]


def test_sentence_rows_split_at_sentence_ends_and_pauses():
    from scribe.merge import sentence_rows

    text = "안녕하세요. 오늘 회의를 시작하겠습니다 첫 안건은 예산입니다"
    words = [{"text": w, "start": s, "end": e} for w, s, e in [
        ("안녕하세요", 0.0, 0.8), ("오늘", 1.0, 1.3), ("회의를", 1.4, 1.8), ("시작하겠습니다", 1.9, 2.6),
        ("첫", 4.0, 4.2), ("안건은", 4.3, 4.7), ("예산입니다", 4.8, 5.5)]]
    rows = sentence_rows([{"text": text, "start": 0.0, "end": 5.5, "words": words}])
    assert [r["text"] for r in rows] == ["안녕하세요.", "오늘 회의를 시작하겠습니다", "첫 안건은 예산입니다"]
    assert rows[2]["t0"] == 4.0 and rows[2]["t1"] == 5.5


def test_label_speakers_keeps_letters_and_chosen_people(tmp_path):
    from scribe.record import label_speakers

    store = Store(tmp_path / "scribe.db")
    mid = store.create_meeting("올린 파일", source="upload")["id"]
    segs = [store.add_live_segment(mid, t, t + 3.0, f"문장 {t}") for t in (0.0, 4.0, 8.0, 12.0)]
    # 첫 두 문장은 이미 묶여 있고(지난번), 사용자가 B를 '오세린'으로 골라 두었다
    store.update_live_speakers([
        {"id": segs[0]["id"], "speaker": None, "speaker_auto": "A", "speaker_src": None, "speaker_conf": None},
        {"id": segs[1]["id"], "speaker": "p1", "speaker_auto": "B", "speaker_src": "manual", "speaker_conf": 1.0},
    ])
    store.update_meeting(mid, participants=[{"id": "p1", "name": "오세린"}], speaker_map={"B": "p1"})
    turns = [{"speaker": "x", "start": 0.0, "end": 3.5}, {"speaker": "y", "start": 3.5, "end": 7.5},
             {"speaker": "y", "start": 7.5, "end": 11.5}, {"speaker": "z", "start": 11.5, "end": 16.0}]
    assert label_speakers(store, mid, turns) == 2
    rows = {r["id"]: r for r in store.list_live_speakers(mid)}
    assert rows[segs[2]["id"]]["speaker_auto"] == "B" and rows[segs[2]["id"]]["speaker"] == "p1"  # 같은 목소리 → 같은 글자, 고른 사람
    assert rows[segs[3]["id"]]["speaker_auto"] == "C" and rows[segs[3]["id"]]["speaker"] is None
    assert rows[segs[1]["id"]]["speaker_src"] == "manual"

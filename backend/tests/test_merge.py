from scribe.merge import build_utterances, locate_words


def test_locate_words_ignores_punctuation_and_spacing():
    text = "안녕하세요, 김 팀장님. 3분기 예산은요?"
    words = [
        {"text": "안녕하세요", "start": 0.0, "end": 0.8},
        {"text": "김", "start": 0.9, "end": 1.0},
        {"text": "팀장님", "start": 1.0, "end": 1.5},
        {"text": "3분기", "start": 2.0, "end": 2.5},
        {"text": "예산은요", "start": 2.5, "end": 3.2},
    ]
    loc = locate_words(text, words)
    assert [text[int(o):int(o + l)] for o, l, _, _ in loc] == ["안녕하세요", "김", "팀장님", "3분기", "예산은요"]


def test_split_by_speaker_and_merge_across_chunks():
    seg1 = {
        "text": "그럼 시작할게요. 네 좋습니다",
        "start": 0.0,
        "end": 5.0,
        "words": [
            {"text": "그럼", "start": 0.0, "end": 0.4},
            {"text": "시작할게요", "start": 0.5, "end": 1.4},
            {"text": "네", "start": 2.0, "end": 2.2},
            {"text": "좋습니다", "start": 2.3, "end": 3.0},
        ],
    }
    seg2 = {
        "text": "그리고 예산은",
        "start": 5.0,
        "end": 8.0,
        "words": [{"text": "그리고", "start": 3.4, "end": 3.9}, {"text": "예산은", "start": 4.0, "end": 4.6}],
    }
    turns = [
        {"speaker": "SPEAKER_01", "start": 0.0, "end": 1.6},
        {"speaker": "SPEAKER_00", "start": 1.9, "end": 5.0},
    ]
    out = build_utterances([seg1, seg2], turns)
    utts = out["utterances"]
    assert [u["text"] for u in utts] == ["그럼 시작할게요.", "네 좋습니다 그리고 예산은"]
    assert [u["speaker"] for u in utts] == ["S1", "S2"]
    assert out["speakers"] == ["S1", "S2"]
    u = utts[1]
    for o, l, s, e in u["words"]:
        assert u["text"][o:o + l] in ("네", "좋습니다", "그리고", "예산은")


def test_no_words_no_turns():
    out = build_utterances([{"text": " 안녕하세요 ", "start": 1.0, "end": 2.0, "words": []}], [])
    assert out["utterances"][0]["text"] == "안녕하세요"
    assert out["utterances"][0]["speaker"] is None

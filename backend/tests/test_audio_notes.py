import numpy as np

from scribe.audio import WavAppender, read_audio, wav_info
from scribe.notes_md import blocks_to_markdown


def test_wav_append_and_resume(tmp_path):
    p = tmp_path / "a.wav"
    w = WavAppender(p)
    tone = (np.sin(np.arange(16000) / 10) * 10000).astype("<i2")
    w.append(tone.tobytes())
    w.close()
    assert abs(wav_info(p).duration - 1.0) < 1e-6
    w2 = WavAppender(p)
    assert w2.samples == 16000
    w2.append(tone[:8000].tobytes())
    w2.close()
    info = wav_info(p)
    assert info.num_samples == 24000
    x = read_audio(p, 1.0, 1.5)
    assert len(x) == 8000 and x.dtype == np.float32


def test_blocks_to_markdown():
    blocks = [
        {"type": "heading", "props": {"level": 1}, "content": [{"type": "text", "text": "결정 사항", "styles": {}}], "children": []},
        {"type": "checkListItem", "props": {"checked": True}, "content": [{"type": "text", "text": "예산 ", "styles": {"bold": True}}, {"type": "timestamp", "props": {"t": 75}}], "children": []},
        {"type": "bulletListItem", "props": {}, "content": [{"type": "text", "text": "상위", "styles": {}}], "children": [
            {"type": "bulletListItem", "props": {}, "content": [{"type": "text", "text": "하위", "styles": {}}], "children": []}
        ]},
    ]
    md = blocks_to_markdown(blocks)
    assert "## 결정 사항" in md
    assert "- [x] **예산** [01:15]" in md
    assert "  - 하위" in md


def test_comment_note_block_markdown():
    """노트로 내보낸 댓글 블록(시각 + 댓글, 아래에 인용과 답글)의 Markdown."""
    blocks = [
        {"type": "bulletListItem", "props": {}, "content": [
            {"type": "timestamp", "props": {"t": 125}}, {"type": "text", "text": " 재무팀 확인 필요", "styles": {"bold": True}},
        ], "children": [
            {"type": "quote", "props": {}, "content": [{"type": "text", "text": "마케팅 비용이 20% 넘게", "styles": {}}], "children": []},
            {"type": "bulletListItem", "props": {}, "content": [{"type": "text", "text": "답글: 다음 주 확인", "styles": {}}], "children": []},
        ]},
    ]
    md = blocks_to_markdown(blocks)
    assert md.splitlines() == ["- [02:05] **재무팀 확인 필요**", "  > 마케팅 비용이 20% 넘게", "  - 답글: 다음 주 확인"]

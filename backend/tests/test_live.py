import numpy as np

from scribe.engines.mock import MockAsr
from scribe.live import LiveSegmenter

SR = 16000


def _speech(seconds, amp=0.3, freq=220):
    t = np.arange(int(seconds * SR)) / SR
    # 음절 같은 진폭 변조
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)
    return (amp * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds):
    return (np.random.default_rng(0).normal(0, 0.002, int(seconds * SR))).astype(np.float32)


def test_segments_cut_at_silence_with_absolute_times():
    seg = LiveSegmenter(MockAsr(), language="ko", start_s=100.0)
    audio = np.concatenate([_silence(1.0), _speech(3.0), _silence(1.0), _speech(2.5), _silence(1.2)])
    events = []
    for i in range(0, len(audio), 4000):  # 250 ms씩
        seg.feed(audio[i:i + 4000])
        events += seg.step()
    events += seg.step(force=True)
    finals = [e for e in events if e.type == "segment"]
    assert len(finals) == 2, finals
    assert 100.8 <= finals[0].t0 <= 101.1
    assert 103.8 <= finals[0].t1 <= 104.3
    assert 104.8 <= finals[1].t0 <= 105.2  # 앞쪽 0.1초 여유 포함
    assert all(e.text for e in finals)


def test_long_speech_is_force_cut():
    seg = LiveSegmenter(MockAsr(), language="ko")
    audio = _speech(20.0)
    events = []
    for i in range(0, len(audio), 8000):
        seg.feed(audio[i:i + 8000])
        events += seg.step()
    finals = [e for e in events if e.type == "segment"]
    assert finals and finals[0].t1 - finals[0].t0 <= 15.0


def test_silence_only_produces_nothing():
    seg = LiveSegmenter(MockAsr(), language="ko")
    seg.feed(_silence(5.0))
    assert [e for e in seg.step(force=True) if e.type == "segment"] == []


def test_backlog_is_split_into_short_segments():
    """모델 준비가 늦어 오디오가 몰려 들어와도 문장 단위로 나눠 확정한다."""
    seg = LiveSegmenter(MockAsr(), language="ko")
    parts = []
    for _ in range(6):
        parts += [_speech(2.5), _silence(0.9)]
    seg.feed(np.concatenate(parts))
    events = seg.step()
    finals = [e for e in events if e.type == "segment"]
    assert len(finals) >= 5
    assert all(e.t1 - e.t0 < 4.0 for e in finals)


def test_feed_from_other_thread_is_safe():
    import threading

    seg = LiveSegmenter(MockAsr(), language="ko")
    audio = np.concatenate([_speech(2.0), _silence(1.0)] * 4)

    def producer():
        for i in range(0, len(audio), 1600):
            seg.feed(audio[i:i + 1600])

    th = threading.Thread(target=producer)
    th.start()
    events = []
    while th.is_alive():
        events += seg.step()
    th.join()
    events += seg.step(force=True)
    total = sum(e.t1 - e.t0 for e in events if e.type == "segment")
    assert total > 6.0


def test_forced_flush_of_backlog_still_cuts_at_silences():
    # 엔진이 늦게 준비되어 밀린 오디오를 한꺼번에 확정해도 말 끊김마다 나뉜다
    seg = LiveSegmenter(MockAsr(), language="ko")
    audio = np.concatenate([_speech(3.0), _silence(1.0), _speech(4.0), _silence(1.0), _speech(3.5), _silence(1.2), _speech(20.0)])
    seg.feed(audio)
    finals = [e for e in seg.step(force=True) if e.type == "segment"]
    assert len(finals) >= 4, finals
    assert all(e.t1 - e.t0 <= 15.0 for e in finals)
    assert finals[-1].t1 >= 33.0

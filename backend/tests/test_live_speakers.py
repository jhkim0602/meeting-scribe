import sys
import types

import numpy as np

from scribe import live_speakers
from scribe.live_speakers import PyannoteEmbedder


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _setup(monkeypatch, outcomes):
    """outcomes: 'fail' 이면 모델 불러오기 실패, 아니면 성공. 차례대로 쓴다."""
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(from_numpy=lambda a: a))
    clock = Clock()
    monkeypatch.setattr(live_speakers.time, "monotonic", clock)
    emb = PyannoteEmbedder()
    loads = []

    def load():
        loads.append(1)
        if outcomes.pop(0) == "fail":
            raise RuntimeError("MPS backend out of memory")
        return lambda x: np.ones(4)

    monkeypatch.setattr(emb, "_load", load)
    return emb, clock, loads


AUDIO = np.zeros(16000, dtype=np.float32)


def test_embedder_retries_quietly_and_recovers(monkeypatch, capsys):
    emb, clock, loads = _setup(monkeypatch, ["fail", "fail", "ok"])

    assert emb.embed(AUDIO, 0, 1) is None
    err = capsys.readouterr().err
    assert "실패 #1" in err and "MPS backend out of memory" in err and "Traceback" in err

    # 기다리는 동안에는 모델을 건드리지 않고 조용히 None
    clock.now += 29
    assert emb.embed(AUDIO, 1, 2) is None
    assert len(loads) == 1 and capsys.readouterr().err == ""

    # 30초 뒤 다시 시도: 같은 에러는 한 줄만, 간격은 두 배
    clock.now += 1
    assert emb.embed(AUDIO, 2, 3) is None
    err = capsys.readouterr().err
    assert "실패 #2" in err and "60초 뒤" in err and "Traceback" not in err

    clock.now += 60
    assert emb.embed(AUDIO, 3, 4) == [1.0] * 4
    assert "복구됨" in capsys.readouterr().err
    assert emb.error is None and emb.failures == 0 and len(loads) == 3


def test_embedder_retry_delay_is_capped(monkeypatch, capsys):
    emb, clock, _ = _setup(monkeypatch, ["fail"] * 10)
    for _ in range(10):
        emb.embed(AUDIO, 0, 1)
        clock.now += emb.RETRY_MAX_S
    assert f"{emb.RETRY_MAX_S:.0f}초 뒤" in capsys.readouterr().err.splitlines()[-1]

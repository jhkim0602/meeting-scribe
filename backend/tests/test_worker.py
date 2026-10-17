import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from scribe.audio import WavAppender
from scribe.db import Store

BACKEND = Path(__file__).resolve().parents[1]


def _make_wav(path: Path, seconds: float) -> None:
    sr = 16000
    t = np.arange(int(seconds * sr)) / sr
    env = (np.sin(2 * np.pi * 0.25 * t) > -0.3).astype(np.float32)  # 말/쉼 반복
    x = (0.3 * env * np.sin(2 * np.pi * 200 * t) * 32767).astype("<i2")
    w = WavAppender(path)
    w.append(x.tobytes())
    w.close()


def test_worker_builds_final_with_speakers(tmp_path):
    data_dir = tmp_path / "data"
    store = Store(data_dir / "scribe.db")
    m = store.create_meeting("테스트 회의")
    mdir = data_dir / "meetings" / m["id"]
    mdir.mkdir(parents=True)
    _make_wav(mdir / "audio.wav", 70.0)
    job = store.create_job(m["id"])
    env = dict(os.environ, SCRIBE_DATA_DIR=str(data_dir), SCRIBE_FINAL_ENGINE="mock", SCRIBE_DIARIZER="mock", PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-m", "scribe.worker", job["id"]], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    final = store.get_final(m["id"])
    utts = final["data"]["utterances"]
    assert len(utts) >= 5
    assert {u["speaker"] for u in utts} == {"S1", "S2", "S3"}
    assert all(u["words"] for u in utts)
    j = store.get_job(job["id"])
    assert j["status"] == "done" and j["progress"] == 1.0
    assert store.get_meeting(m["id"])["status"] == "ready"
    assert abs(store.get_meeting(m["id"])["duration"] - 70.0) < 0.01

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


def test_worker_resumes_from_window_checkpoints(tmp_path):
    """창별 결과를 저장해 두므로, 녹음이 뒤에 더 붙으면 앞 창은 다시 받아쓰지 않는다."""
    data_dir = tmp_path / "data"
    store = Store(data_dir / "scribe.db")
    m = store.create_meeting("긴 회의")
    mdir = data_dir / "meetings" / m["id"]
    mdir.mkdir(parents=True)
    _make_wav(mdir / "audio.wav", 70.0)
    env = dict(os.environ, SCRIBE_DATA_DIR=str(data_dir), SCRIBE_FINAL_ENGINE="mock", SCRIBE_DIARIZER="mock",
               SCRIBE_WINDOW_S="20", PYTHONPATH=str(BACKEND))

    def run_job():
        job = store.create_job(m["id"])
        r = subprocess.run([sys.executable, "-m", "scribe.worker", job["id"]], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        line = next(x for x in r.stdout.splitlines() if x.startswith("windows="))
        return dict(kv.split("=") for kv in line.split())

    first = run_job()
    assert int(first["windows"]) >= 3 and first["reused"] == "0"
    v1 = store.get_final(m["id"])["data"]["utterances"]
    again = run_job()
    assert again["reused"] == again["windows"]  # 그대로 다시 만들면 받아쓰기는 모두 재사용
    assert store.get_final(m["id"])["data"]["utterances"] == v1

    # 이어서 녹음: 같은 파일 뒤에 30초가 붙는다
    sr = 16000
    t = np.arange(int(30 * sr)) / sr
    extra = (0.3 * (np.sin(2 * np.pi * 0.25 * t) > -0.3) * np.sin(2 * np.pi * 200 * t) * 32767).astype("<i2")
    w = WavAppender(mdir / "audio.wav")
    w.append(extra.tobytes())
    w.close()
    grown = run_job()
    assert int(grown["windows"]) > int(first["windows"])
    assert int(grown["reused"]) >= int(first["windows"]) - 1  # 마지막 창만 다시
    final = store.get_final(m["id"])["data"]
    assert final["utterances"][-1]["t1"] > 95.0
    # 다시 쓸 수 없게 된 옛 창 결과는 지워진다
    names = sorted(f.name for f in (mdir / "ckpt").glob("asr_*.json"))
    assert len(names) == int(grown["windows"])


def test_worker_parallel_diarization_and_voice_features(tmp_path):
    """긴 녹음처럼 화자 구분을 별도 프로세스에서 동시에 돌려도 결과가 같고, 화자별 목소리 특징이 남는다."""
    data_dir = tmp_path / "data"
    store = Store(data_dir / "scribe.db")
    m = store.create_meeting("동시 처리")
    mdir = data_dir / "meetings" / m["id"]
    mdir.mkdir(parents=True)
    _make_wav(mdir / "audio.wav", 70.0)
    job = store.create_job(m["id"])
    env = dict(os.environ, SCRIBE_DATA_DIR=str(data_dir), SCRIBE_FINAL_ENGINE="mock", SCRIBE_DIARIZER="mock",
               SCRIBE_PARALLEL_DIARIZE_MIN_S="0", PYTHONPATH=str(BACKEND))
    r = subprocess.run([sys.executable, "-m", "scribe.worker", job["id"]], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    data = store.get_final(m["id"])["data"]
    assert {u["speaker"] for u in data["utterances"]} == {"S1", "S2", "S3"}
    assert set(data["speaker_embeddings"]) == {"S1", "S2", "S3"} and "speaker_map" not in data
    assert store.get_job(job["id"])["status"] == "done"


"""원본 스크립트 작업: 사용자가 시킬 때만, 고른 모델·화자 구분으로, 녹음 중에는 멈춘다."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribe import config, jobs
from scribe.db import Store
from test_worker import BACKEND, _make_wav


class FakeProc:
    def __init__(self, *a, **k):
        self.args = a
        self.kwargs = k
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    started = []

    def fake_popen(*a, **k):
        p = FakeProc(*a, **k)
        started.append(p)
        return p

    # 작업 관리자만 가짜 프로세스를 띄우게 한다 (파일 줄이기의 ffmpeg는 진짜로)
    monkeypatch.setattr(jobs, "subprocess", SimpleNamespace(Popen=fake_popen, STDOUT=subprocess.STDOUT))
    store = Store(tmp_path / "scribe.db")
    jm = jobs.JobManager(store)
    jm.started = started
    return jm


def _meeting(jm, status="recorded", source="live"):
    m = jm.store.create_meeting("회의", source=source)
    jm.store.update_meeting(m["id"], status=status)
    return m["id"]


def test_job_waits_while_recording_then_starts(manager):
    mid = _meeting(manager)
    recording = [True]
    manager.recording_any = lambda: recording[0]
    job = manager.enqueue(mid)
    assert not manager.started and job["status"] == "queued"
    assert "녹음 중" in manager.store.get_job(job["id"])["waiting"]
    assert manager.store.get_meeting(mid)["status"] == "processing"
    recording[0] = False
    manager._kick()
    assert len(manager.started) == 1 and manager.current == job["id"]
    assert manager.store.get_job(job["id"])["waiting"] is None


def test_chosen_model_and_speakers_go_to_the_worker(manager):
    """고른 모델·화자 구분은 작업 프로세스 환경 변수로 (설정 파일보다 먼저)"""
    mid = _meeting(manager, source="upload")
    manager.enqueue(mid, {"final": "qwen3-0.6b-8bit", "speakers": False})
    env = manager.started[0].kwargs["env"]
    assert env["SCRIBE_FINAL_ENGINE"] == "qwen3" and env["SCRIBE_FINAL_MODEL"] == "moona3k/mlx-qwen3-asr-0.6b-8bit"
    assert env["SCRIBE_DIARIZER"] == "none"
    assert jobs.job_env({"final": "fw-large-v3-turbo"}) == {"SCRIBE_FINAL_ENGINE": "faster-whisper", "SCRIBE_FINAL_MODEL": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}
    assert jobs.job_env({}) == {}


def test_running_job_is_paused_while_recording(manager):
    mid = _meeting(manager)
    recording = [False]
    manager.recording_any = lambda: recording[0]
    job = manager.enqueue(mid)
    manager.store.update_job(job["id"], status="running")
    pause = jobs.pause_path(mid)
    manager._steer(job["id"])
    assert not pause.exists()
    recording[0] = True
    manager._steer(job["id"])
    assert pause.read_text(encoding="utf-8").startswith("녹음 중이라 잠시 멈췄어요")
    recording[0] = False
    manager._steer(job["id"])
    assert not pause.exists()


def test_housekeeping_shrinks_recorded_meetings_but_not_while_recording(manager, monkeypatch):
    from scribe import storage

    monkeypatch.setattr(storage, "COMPRESS_AFTER_H", 0.0)
    mid = _meeting(manager)  # 원본 스크립트 없이 녹음만 마친 회의
    mdir = config.settings.meeting_path(mid)
    mdir.mkdir(parents=True, exist_ok=True)
    _make_wav(mdir / "audio.wav", 3.0)
    manager.recording_any = lambda: True
    assert manager.housekeep_once() == []
    manager.recording_any = lambda: False
    assert manager.housekeep_once() == [mid]
    assert (mdir / "audio.m4a").exists() and not (mdir / "audio.wav").exists()


def _children(pid: int) -> list[int]:
    path = Path(f"/proc/{pid}/task/{pid}/children")
    return [int(x) for x in path.read_text().split()] if path.exists() else []


def _state(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1].split()[0]
    except OSError:
        return "gone"


def _wait(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def _paused_worker(tmp_path):
    data_dir = tmp_path / "data"
    store = Store(data_dir / "scribe.db")
    m = store.create_meeting("멈춤")
    # 회의 폴더는 '날짜_제목', 멈춤 표시는 그 안의 숨김 작업 폴더(.work)에
    mdir = data_dir / "meetings" / store.meeting_folder(m["id"])
    (mdir / ".work").mkdir(parents=True, exist_ok=True)
    _make_wav(mdir / "audio.wav", 70.0)
    (mdir / ".work" / "pause").write_text("녹음 중이라 잠시 멈췄어요", encoding="utf-8")
    job = store.create_job(m["id"])
    env = dict(os.environ, SCRIBE_DATA_DIR=str(data_dir), SCRIBE_FINAL_ENGINE="mock", SCRIBE_DIARIZER="mock",
               SCRIBE_PARALLEL_DIARIZE_MIN_S="0", PYTHONPATH=str(BACKEND))
    proc = subprocess.Popen([sys.executable, "-m", "scribe.worker", job["id"]], cwd=BACKEND, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert _wait(lambda: (store.get_job(job["id"]) or {}).get("waiting") == "녹음 중이라 잠시 멈췄어요"), proc.stderr
    return store, job, mdir, proc


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc로 멈춘 프로세스를 확인한다")
def test_worker_waits_while_paused_then_finishes(tmp_path):
    store, job, mdir, proc = _paused_worker(tmp_path)
    kids = _children(proc.pid)
    assert kids and _wait(lambda: any(_state(k) == "T" for k in kids), 5)  # 같이 돌던 화자 구분도 멈춘다 (다른 하나는 multiprocessing 도우미)
    progress = store.get_job(job["id"])["progress"]
    time.sleep(1.5)
    assert store.get_job(job["id"])["progress"] == progress and proc.poll() is None
    (mdir / ".work" / "pause").unlink()
    assert proc.wait(timeout=120) == 0
    j = store.get_job(job["id"])
    assert j["status"] == "done" and j["waiting"] is None
    assert {u["speaker"] for u in store.get_final(j["meeting_id"])["data"]["utterances"]} == {"S1", "S2", "S3"}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc로 멈춘 프로세스를 확인한다")
def test_paused_worker_can_be_cancelled(tmp_path):
    store, job, mdir, proc = _paused_worker(tmp_path)
    kids = _children(proc.pid)
    assert kids and _wait(lambda: any(_state(k) == "T" for k in kids), 5)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=20) != 0
    assert _wait(lambda: all(_state(k) in ("gone", "Z") for k in kids), 10)

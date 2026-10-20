"""후처리 작업 대기열. 한 번에 하나씩 별도 프로세스로 실행한다.

작업은 사용자가 직접 시킬 때만 만든다 (녹음을 마쳐도 저절로 만들지 않는다). 모델·화자 구분은 작업마다 고른 것을 쓴다.
회의를 방해하지 않게, 어떤 회의든 녹음하는 동안에는 시작하지 않고 하던 작업도 잠시 멈춘다 (실시간 받아쓰기가 먼저다).
멈추기는 워커가 안전한 곳(진행률을 알리는 때)에서 스스로 멈추게 한다: 회의 폴더의 `pause` 파일이 있는 동안 기다린다.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from . import catalog
from .config import settings
from .db import Store
from .folders import work_dir
from .storage import audio_file, compress_audio, is_settled

HOUSEKEEP_EVERY_S = 60.0
PAUSE_FILE = "pause"


def pause_path(meeting_id: str) -> Path:
    """워커에게 잠시 멈추라고 알리는 파일 (내용은 기다리는 까닭)"""
    return work_dir(meeting_id) / PAUSE_FILE

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def job_env(options: dict) -> dict[str, str]:
    """사용자가 고른 모델·화자 구분을 작업 프로세스의 환경 변수로 (설정 파일보다 먼저 쓴다)"""
    env: dict[str, str] = {}
    c = catalog.choice("final", options.get("final"))
    if c:
        env.update(SCRIBE_FINAL_ENGINE=c["engine"], SCRIBE_FINAL_MODEL=c["model"])
    if options.get("speakers") is False:
        env["SCRIBE_DIARIZER"] = "none"
    return env


class JobManager:
    def __init__(self, store: Store):
        self.store = store
        self.proc: Optional[subprocess.Popen] = None
        self.current: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self.is_recording: Callable[[str], bool] = lambda mid: False
        self.recording_any: Callable[[], bool] = lambda: False
        # 작업 프로세스가 끝났을 때 (서버가 들고 있는 회의 상태를 새로 읽게 한다)
        self.on_job_end: Callable[[str], None] = lambda mid: None
        self._last_housekeep = 0.0

    def recover(self) -> None:
        """서버가 꺼졌다 켜졌을 때 끊긴 작업 정리."""
        for job in self.store.active_jobs():
            if job["status"] == "running" and not _alive(job.get("pid")):
                self.store.update_job(job["id"], status="error", error="서버가 종료되어 작업이 중단되었습니다", finished_at=time.time())
                m = self.store.get_meeting(job["meeting_id"])
                if m and m["status"] == "processing":
                    self.store.update_meeting(m["id"], status=self.store.status_after_job(m["id"]))

    def enqueue(self, meeting_id: str, options: Optional[dict] = None) -> dict:
        """options: {final: 모델 id, speakers: 화자 구분 여부} (없으면 설정의 기본값)"""
        latest = self.store.latest_job(meeting_id)
        if latest and latest["status"] in ("queued", "running"):
            return latest
        job = self.store.create_job(meeting_id, options=options)
        self.store.update_meeting(meeting_id, status="processing")
        self._kick()
        return self.store.get_job(job["id"]) or job

    def hold_reason(self, job: dict) -> Optional[str]:
        """이 작업을 지금 하지 말아야 하면 그 까닭 (화면에 그대로 보여 준다)."""
        upload = (self.store.get_meeting(job["meeting_id"]) or {}).get("source") == "upload"
        if self.recording_any():
            return "녹음 중이라 잠시 멈췄어요. 녹음을 마치면 이어서 " + ("받아 적어요" if upload else "만들어요")
        return None

    def cancel(self, meeting_id: str) -> None:
        latest = self.store.latest_job(meeting_id)
        if not latest or latest["status"] not in ("queued", "running"):
            return
        if latest["id"] == self.current and self.proc and self.proc.poll() is None:
            self.proc.terminate()  # 멈춰 기다리는 중이어도 워커는 바로 끝난다
        self.store.update_job(latest["id"], status="cancelled", finished_at=time.time(), message="사용자가 취소했어요")
        status = self.store.status_after_job(meeting_id, failed=False)
        self.store.update_meeting(meeting_id, status="paused" if status == "empty" and self._has_audio(meeting_id) else status)

    @staticmethod
    def _has_audio(meeting_id: str) -> bool:
        return audio_file(settings.meeting_path(meeting_id)) is not None

    def housekeep_once(self) -> list[str]:
        """녹음을 마치고 한동안 손대지 않은 회의의 WAV를 m4a로 줄인다. 줄인 회의 id 목록."""
        if self.recording_any():
            return []  # 녹음 중에는 파일 줄이기(ffmpeg)도 쉬어 받아쓰기를 방해하지 않는다
        busy = {j["meeting_id"] for j in self.store.active_jobs()}
        done = []
        for m in self.store.list_meetings():
            mid = m["id"]
            # 원본 스크립트를 만들었는지와 상관없이, 녹음을 마치고 한동안 손대지 않았으면 줄인다
            if m["status"] not in ("ready", "recorded") or mid in busy or self.is_recording(mid):
                continue
            mdir = settings.meeting_path(mid)
            if is_settled(mdir) and compress_audio(mdir):
                done.append(mid)
        return done

    def _kick(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        queued = [j for j in self.store.active_jobs() if j["status"] == "queued"]
        held = [(j, self.hold_reason(j)) for j in queued]
        job = next((j for j, reason in held if reason is None), None)
        for j, reason in held:
            if j is not job and j.get("waiting") != reason:
                self.store.update_job(j["id"], waiting=reason)
        if job is None:
            self.proc, self.current = None, None
            return
        self.store.update_job(job["id"], waiting=None)
        pause_path(job["meeting_id"]).unlink(missing_ok=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        env.update(job_env(json.loads(job.get("options") or "null") or {}))
        log = open(work_dir(job["meeting_id"]) / "worker.log", "ab")
        self.proc = subprocess.Popen([sys.executable, "-m", "scribe.worker", job["id"]], cwd=str(BACKEND_DIR), env=env, stdout=log, stderr=subprocess.STDOUT)
        self.current = job["id"]

    async def run_forever(self) -> None:
        while True:
            try:
                if self.proc is not None and self.proc.poll() is not None:
                    job = self.store.get_job(self.current) if self.current else None
                    if job and job["status"] in ("queued", "running"):
                        self.store.update_job(job["id"], status="error", error=f"작업 프로세스가 비정상 종료되었습니다 (코드 {self.proc.returncode})", finished_at=time.time())
                        m = self.store.get_meeting(job["meeting_id"])
                        if m and m["status"] == "processing":
                            self.store.update_meeting(m["id"], status=self.store.status_after_job(m["id"]))
                    if job:
                        pause_path(job["meeting_id"]).unlink(missing_ok=True)
                        self.on_job_end(job["meeting_id"])
                    self.proc, self.current = None, None
                elif self.proc is not None and self.current:
                    self._steer(self.current)
                self._kick()
                # 원본 스크립트 작업이 없을 때만 조용히 파일을 줄인다
                if self.proc is None and time.time() - self._last_housekeep > HOUSEKEEP_EVERY_S:
                    self._last_housekeep = time.time()
                    await asyncio.to_thread(self.housekeep_once)
            except Exception as exc:  # noqa: BLE001
                print(f"[jobs] {exc}", file=sys.stderr)
            await asyncio.sleep(1.0)

    def _steer(self, job_id: str) -> None:
        """하는 중인 작업을 멈추거나 다시 하게 한다 (pause 파일)."""
        job = self.store.get_job(job_id)
        if job is None or job["status"] != "running":
            return
        reason = self.hold_reason(job)
        path = pause_path(job["meeting_id"])
        if reason is None:
            path.unlink(missing_ok=True)
        elif not path.exists() or path.read_text(encoding="utf-8") != reason:
            path.write_text(reason, encoding="utf-8")

    def start(self) -> None:
        self.recover()
        self._task = asyncio.get_event_loop().create_task(self.run_forever())

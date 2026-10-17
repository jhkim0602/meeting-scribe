"""후처리 작업 대기열. 한 번에 하나씩 별도 프로세스로 실행한다."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .config import settings
from .db import Store

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class JobManager:
    def __init__(self, store: Store):
        self.store = store
        self.proc: Optional[subprocess.Popen] = None
        self.current: Optional[str] = None
        self._task: Optional[asyncio.Task] = None

    def recover(self) -> None:
        """서버가 꺼졌다 켜졌을 때 끊긴 작업 정리."""
        for job in self.store.active_jobs():
            if job["status"] == "running" and not _alive(job.get("pid")):
                self.store.update_job(job["id"], status="error", error="서버가 종료되어 작업이 중단되었습니다", finished_at=time.time())
                m = self.store.get_meeting(job["meeting_id"])
                if m and m["status"] == "processing":
                    self.store.update_meeting(m["id"], status="ready" if self.store.get_final(m["id"]) else "error")

    def enqueue(self, meeting_id: str) -> dict:
        latest = self.store.latest_job(meeting_id)
        if latest and latest["status"] in ("queued", "running"):
            return latest
        job = self.store.create_job(meeting_id)
        self.store.update_meeting(meeting_id, status="processing")
        self._kick()
        return job

    def cancel(self, meeting_id: str) -> None:
        latest = self.store.latest_job(meeting_id)
        if not latest or latest["status"] not in ("queued", "running"):
            return
        if latest["id"] == self.current and self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.store.update_job(latest["id"], status="cancelled", finished_at=time.time(), message="사용자가 취소했어요")
        self.store.update_meeting(meeting_id, status="ready" if self.store.get_final(meeting_id) else ("paused" if self._has_audio(meeting_id) else "empty"))

    @staticmethod
    def _has_audio(meeting_id: str) -> bool:
        return (settings.meetings_dir / meeting_id / "audio.wav").exists()

    def _kick(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        queued = [j for j in self.store.active_jobs() if j["status"] == "queued"]
        if not queued:
            self.proc, self.current = None, None
            return
        job = queued[0]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        log = open(settings.meeting_dir(job["meeting_id"]) / "worker.log", "ab")
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
                            self.store.update_meeting(m["id"], status="ready" if self.store.get_final(m["id"]) else "error")
                    self.proc, self.current = None, None
                self._kick()
            except Exception as exc:  # noqa: BLE001
                print(f"[jobs] {exc}", file=sys.stderr)
            await asyncio.sleep(1.0)

    def start(self) -> None:
        self.recover()
        self._task = asyncio.get_event_loop().create_task(self.run_forever())

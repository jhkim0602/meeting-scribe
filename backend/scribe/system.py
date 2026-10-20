"""녹음을 지키는 시스템 도우미: 남은 저장 공간, 녹음 중 잠자기 방지."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .config import SAMPLE_RATE

BYTES_PER_SECOND = SAMPLE_RATE * 2
# DB, 원본 스크립트, 로그를 위해 남겨 두는 공간
DISK_RESERVE = 300 * 1024 * 1024


def disk_free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0


def disk_free_seconds(path: Path) -> float:
    """앞으로 녹음할 수 있는 시간(초)."""
    return max(0.0, (disk_free_bytes(path) - DISK_RESERVE) / BYTES_PER_SECOND)


class KeepAwake:
    """녹음하는 동안 컴퓨터가 저절로 잠들지 않게 한다 (화면은 꺼져도 된다).
    맥: caffeinate -i. 윈도우: SetThreadExecutionState (그 상태를 쥐고 있는 스레드가 살아 있는 동안만 유지된다).

    덮개를 닫아 재우는 것은 막지 않는다. 그 경우는 브라우저가 깨어난 뒤 쉬는 시간으로 남긴다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._proc: Optional[subprocess.Popen] = None
        self._win_stop: Optional[threading.Event] = None

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32" or shutil.which("caffeinate") is not None

    @staticmethod
    def _win_hold(stop: threading.Event) -> None:
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetThreadExecutionState(es_continuous | es_system_required)
        stop.wait()
        kernel32.SetThreadExecutionState(es_continuous)

    def acquire(self) -> None:
        with self._lock:
            self._count += 1
            if sys.platform == "win32":
                if self._win_stop is None:
                    self._win_stop = threading.Event()
                    threading.Thread(target=self._win_hold, args=(self._win_stop,), name="keep-awake", daemon=True).start()
                return
            if self._proc is None and self.available():
                try:
                    # -w: 서버가 죽으면 같이 끝난다
                    self._proc = subprocess.Popen(
                        ["caffeinate", "-i", "-w", str(os.getpid())],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except OSError:
                    self._proc = None

    def release(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)
            if self._count == 0 and self._win_stop is not None:
                self._win_stop.set()
                self._win_stop = None
            if self._count == 0 and self._proc is not None:
                self._proc.terminate()
                self._proc = None


keep_awake = KeepAwake()


_power: tuple[float, Optional[str]] = (0.0, None)


def power_source() -> Optional[str]:
    """'ac' | 'battery' | None(알 수 없음). 1분 동안 기억한다."""
    global _power
    forced = os.environ.get("SCRIBE_POWER")
    if forced in ("ac", "battery"):
        return forced
    now = time.time()
    if now - _power[0] < 60:
        return _power[1]
    src: Optional[str] = None
    try:
        if sys.platform == "win32":
            src = _win_power()
        elif shutil.which("pmset"):
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3).stdout
            src = "battery" if "Battery Power" in out else "ac" if "AC Power" in out else None
        else:
            for d in Path("/sys/class/power_supply").glob("*"):
                if (d / "type").read_text().strip() == "Mains":
                    src = "ac" if (d / "online").read_text().strip() == "1" else "battery"
                    break
    except (OSError, subprocess.SubprocessError):
        src = None
    _power = (now, src)
    return src


def _win_power() -> Optional[str]:
    """윈도우 GetSystemPowerStatus: 충전기에 꽂혔는지 (배터리가 없는 PC는 늘 'ac')"""
    import ctypes

    class _Status(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    st = _Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):  # type: ignore[attr-defined]
        return None
    if st.BatteryFlag == 128:  # 배터리 없음
        return "ac"
    return {0: "battery", 1: "ac"}.get(st.ACLineStatus)

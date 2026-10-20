"""메모리 사용량: 이 컴퓨터 전체와 프로세스 하나 (패키지 없이 운영체제 기능으로).

- 맥: 프로세스는 proc_pid_rusage의 phys_footprint (활동 상태 보기의 '메모리'와 같은 값, GPU 메모리 포함)
- 윈도우: 프로세스는 GetProcessMemoryInfo의 PrivateUsage, 전체는 GlobalMemoryStatusEx
- 그 밖(리눅스): /proc
못 읽으면 None.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path
from typing import Optional


def process_bytes(pid: int) -> Optional[int]:
    try:
        if sys.platform == "darwin":
            buf = (ctypes.c_uint64 * 40)()
            if ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(pid, 2, ctypes.byref(buf)) != 0:  # RUSAGE_INFO_V2
                return None
            return int((ctypes.c_uint64 * 38).from_buffer(buf, 16)[7])  # uuid 16바이트 뒤 여덟째 값: ri_phys_footprint
        if sys.platform == "win32":
            return _win_process(pid)
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, AttributeError, ValueError):
        pass
    return None


def system() -> dict:
    """{total, available, swap_used} 바이트"""
    out: dict = {"total": None, "available": None, "swap_used": None}
    try:
        if sys.platform == "darwin":
            out["total"] = int(_sh(["sysctl", "-n", "hw.memsize"]))
            stats = dict(line.split(":", 1) for line in _sh(["vm_stat"]).splitlines()[1:] if ":" in line)
            page = int(_sh(["sysctl", "-n", "hw.pagesize"]))
            pages = sum(int(stats.get(k, "0").strip().rstrip(".")) for k in ("Pages free", "Pages inactive", "Pages speculative"))
            out["available"] = pages * page
            used = _sh(["sysctl", "-n", "vm.swapusage"]).split("used = ")[1].split()[0]  # '3659.81M'
            out["swap_used"] = int(float(used.rstrip("M")) * 1024 * 1024)
        elif sys.platform == "win32":
            out.update(_win_system())
        else:
            info = {k: int(v.split()[0]) * 1024 for k, v in (ln.split(":", 1) for ln in Path("/proc/meminfo").read_text().splitlines())}
            out.update(total=info.get("MemTotal"), available=info.get("MemAvailable"),
                       swap_used=info.get("SwapTotal", 0) - info.get("SwapFree", 0))
    except (OSError, ValueError, IndexError, AttributeError, subprocess.SubprocessError):
        pass
    return out


def _sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()


def _win_process(pid: int) -> Optional[int]:
    from ctypes import wintypes

    class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                           "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]

    kernel32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
    if not handle:
        return None
    try:
        c = Counters()
        c.cb = ctypes.sizeof(c)
        return int(c.PrivateUsage) if psapi.GetProcessMemoryInfo(handle, ctypes.byref(c), c.cb) else None
    finally:
        kernel32.CloseHandle(handle)


def _win_system() -> dict:
    class Status(ctypes.Structure):  # MEMORYSTATUSEX
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong) for n in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                                              "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")]

    st = Status()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
        return {}
    # 페이지 파일 사용량에서 실제 메모리 사용량을 빼면 디스크로 밀려난 양에 가깝다
    commit = st.ullTotalPageFile - st.ullAvailPageFile
    phys_used = st.ullTotalPhys - st.ullAvailPhys
    return {"total": st.ullTotalPhys, "available": st.ullAvailPhys, "swap_used": max(0, commit - phys_used)}

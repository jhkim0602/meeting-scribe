"""기기마다 다른 기능을 실제로 불러 본다 (윈도우 시험 기계에서 돌 때 윈도우 코드가 실제로 실행된다).

메모리 읽기, 잠자기 방지, 배터리 상태, 폴더 열기 명령.
"""

import os
import sys
import time

import pytest

from scribe import memstat, system


def test_memory_numbers_on_this_machine():
    assert (memstat.process_bytes(os.getpid()) or 0) > 10 * 1024 * 1024  # 이 파이썬 프로세스
    s = memstat.system()
    assert s["total"] and s["total"] > s["available"] > 0


def test_keep_awake_can_be_taken_and_released():
    k = system.KeepAwake()
    if not k.available():
        pytest.skip("이 기기에는 잠자기 방지가 없어요 (리눅스)")
    k.acquire()
    k.acquire()  # 녹음 둘
    time.sleep(0.3)
    if sys.platform == "win32":
        assert k._win_stop is not None  # 잠자기를 막는 스레드가 쥐고 있다
    k.release()
    k.release()
    if sys.platform == "win32":
        assert k._win_stop is None


def test_power_source_is_read_without_error():
    system._power = (0.0, None)  # 1분 기억을 비운다
    assert system.power_source() in ("ac", "battery", None)


@pytest.mark.skipif(sys.platform != "win32", reason="윈도우에서만")
def test_windows_power_status_call():
    # 시험 기계는 배터리가 없다: '충전 중(ac)'으로 읽혀야 한다
    assert system._win_power() == "ac"


@pytest.mark.skipif(sys.platform != "win32", reason="윈도우에서만")
def test_windows_can_open_a_folder(tmp_path):
    """폴더 열기(os.startfile)가 오류 없이 불린다"""
    os.startfile(str(tmp_path))  # type: ignore[attr-defined]

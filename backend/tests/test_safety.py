"""기록을 지키는 일과 예외 처리: DB 되살리기·사본, 동기화 폴더 경고, 화면 알림, 화자 모델 GPU→CPU·멈춤, 오래된 CPU."""

import asyncio
import sqlite3
import sys
import time
import types
from contextlib import closing
from pathlib import Path

import numpy as np

from scribe import live_speakers, notices, recording, safety


def _db(path: Path, rows: int = 3) -> None:
    # 꼭 닫는다 (윈도우는 열린 파일을 옮기지 못한다)
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("CREATE TABLE t(x)")
        c.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(rows)])


def test_broken_db_is_kept_aside_and_restored_from_latest_copy(tmp_path):
    db = tmp_path / "scribe.db"
    _db(db)
    assert safety.check_and_recover(db) is None  # 멀쩡하면 아무것도 안 한다
    assert safety.backup_daily(db) is not None and safety.backup_daily(db) is None  # 하루 한 번
    db.write_bytes(b"this is not a database" * 100)  # 깨짐
    msg = safety.check_and_recover(db)
    assert msg and "사본으로 되돌렸어요" in msg
    assert list(tmp_path.glob("scribe.broken-*.db"))  # 깨진 파일은 남긴다
    with closing(sqlite3.connect(db)) as c:
        assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 3

    # 사본이 없으면 새로 시작 (깨진 파일은 남긴다)
    other = tmp_path / "b" / "scribe.db"
    other.parent.mkdir()
    other.write_bytes(b"garbage" * 100)
    assert "새로 시작" in safety.check_and_recover(other) and not other.exists()


def test_daily_copies_keep_the_last_seven(tmp_path):
    db = tmp_path / "scribe.db"
    _db(db)
    folder = safety.backups_dir(db)
    folder.mkdir()
    for d in range(1, 10):
        (folder / f"scribe-2026-01-{d:02d}.db").write_bytes(b"x")
    safety.backup_daily(db)
    kept = sorted(p.name for p in folder.glob("scribe-*.db"))
    assert len(kept) == 7 and kept[-1] == f"scribe-{time.strftime('%Y-%m-%d')}.db"


def test_sync_folder_warning(tmp_path, monkeypatch):
    assert "OneDrive" in safety.sync_folder_warning(tmp_path / "OneDrive - 회사" / "MeetingScribe")
    assert safety.sync_folder_warning(tmp_path / "MeetingScribe") is None


def test_notices_are_numbered_and_deduplicated():
    start = notices.since(0)[-1]["id"] if notices.since(0) else 0
    notices.add("하나", key="t-once")
    notices.add("하나", key="t-once")  # 같은 일은 한 번만
    notices.add("둘")
    got = notices.since(start)
    assert [n["text"] for n in got] == ["하나", "둘"] and got[1]["id"] == got[0]["id"] + 1
    assert notices.since(got[-1]["id"]) == []


def test_speaker_model_switches_to_cpu_and_says_so(monkeypatch):
    """GPU(MPS)에서 오류가 나면 조용히 끄지 않고, 알린 뒤 CPU로 바꿔 다음 문장부터 다시 한다."""
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(from_numpy=lambda a: a))
    emb = live_speakers.PyannoteEmbedder()
    devices = []

    def load():
        devices.append("cpu" if emb.cpu else "mps")
        emb._device = devices[-1]

        def run(x):
            if emb._device == "mps":
                raise RuntimeError("MPS backend error")
            return np.ones(4)
        return run

    monkeypatch.setattr(emb, "_load", load)
    start = notices.since(0)[-1]["id"] if notices.since(0) else 0
    audio = np.zeros(16000, dtype=np.float32)
    assert emb.embed(audio, 0, 1) is None and emb.cpu and emb.error is None  # 기다리지 않고 바로 CPU로
    assert any("CPU로 바꿔서" in n["text"] for n in notices.since(start))
    assert emb.embed(audio, 1, 2) == [1.0] * 4 and devices == ["mps", "cpu"]


def test_hung_speaker_model_is_restarted(monkeypatch):
    """문장 하나의 화자를 오래 못 정하면 멈춘 것으로 보고 새 작업자·새 모델로 다음 문장부터 한다."""
    monkeypatch.setattr(recording, "SPEAKER_TIMEOUT_S", 0.2)
    restarted = []
    monkeypatch.setattr(recording, "restart_embedder", lambda: restarted.append(1))
    monkeypatch.setattr(recording, "get_embedder", lambda: "new-embedder")
    sent = []

    class Stub:
        embedder = "old"

        def _speaker_for(self, seg):
            time.sleep(1.0)  # 멈춘 모델

        async def send(self, payload):
            sent.append(payload)

    s = Stub()
    old_pool = recording._spk_executor
    start = notices.since(0)[-1]["id"] if notices.since(0) else 0
    asyncio.run(recording.RecordingSession._label(s, {"id": "a", "t0": 0.0, "t1": 1.0}))
    assert restarted and s.embedder == "new-embedder" and recording._spk_executor is not old_pool
    assert sent == [] and any("멈춰서 새로 시작" in n["text"] for n in notices.since(start))


def test_old_cpu_falls_back_from_int8(monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, model, device, compute_type):
            calls.append(compute_type)
            if compute_type == "int8":
                raise ValueError("Requested int8 compute type, but the target device or backend do not support efficient int8 computation.")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    from scribe.engines.faster_whisper_engine import FasterWhisperEngine

    start = notices.since(0)[-1]["id"] if notices.since(0) else 0
    FasterWhisperEngine("small").load()
    assert calls == ["int8", "default"] and any("int8" in n["text"] for n in notices.since(start))


def test_backup_failure_never_crashes_startup(tmp_path, monkeypatch):
    """윈도우처럼 사본 파일을 옮기지 못해도 서버가 멈추지 않는다 (로그만 남긴다)."""
    db = tmp_path / "scribe.db"
    _db(db)

    def locked(self, target):
        raise PermissionError("[WinError 32] The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: (_ for _ in ()).throw(PermissionError("locked")))
    assert safety.backup_daily(db) is None  # 예외를 던지지 않는다


def test_connections_are_closed_after_backup_and_check(tmp_path):
    """연결을 닫아야 윈도우에서 사본을 옮기고 깨진 DB를 옆으로 옮길 수 있다."""
    db = tmp_path / "scribe.db"
    _db(db)
    opened = []
    real = sqlite3.connect

    def tracking(*a, **k):
        c = real(*a, **k)
        opened.append(c)
        return c

    import scribe.safety as s
    s.sqlite3.connect = tracking
    try:
        s.check_and_recover(db)
        s.backup_daily(db)
    finally:
        s.sqlite3.connect = real
    for c in opened:
        try:
            c.execute("SELECT 1")
            raise AssertionError("열린 연결이 남았어요")
        except sqlite3.ProgrammingError:
            pass  # 닫힘

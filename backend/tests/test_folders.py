"""회의 폴더 이름: '날짜_제목'으로 보이고, 제목을 바꾸면 따라 바뀌고, 예전 id 폴더는 옮긴다."""

import time

import pytest

from scribe import config, folders
from scribe.db import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    config.settings.ensure_dirs()
    return Store(tmp_path / "scribe.db")


def _day(m):
    return time.strftime("%Y-%m-%d", time.localtime(m["created_at"]))


def test_new_meeting_folder_is_date_and_title(store):
    m = store.create_meeting("주간 회의: 기획/개발")
    path = config.settings.meeting_path(m["id"])
    assert path.name == f"{_day(m)}_주간 회의 기획 개발" and path.is_dir()
    # 제목 없는 회의 둘이 같은 날 만들어져도 이름이 겹치지 않는다
    a, b = store.create_meeting(""), store.create_meeting("")
    names = {config.settings.meeting_path(x["id"]).name for x in (a, b)}
    assert names == {f"{_day(a)}_회의", f"{_day(a)}_회의 (2)"}


def test_title_change_renames_folder_and_keeps_files(store):
    m = store.create_meeting("")
    mdir = config.settings.meeting_dir(m["id"])
    (mdir / "audio.wav").write_bytes(b"x" * 10)
    store.update_meeting(m["id"], title="학적파트 인터뷰")
    assert folders.sync(store, m["id"]) == f"{_day(m)}_학적파트 인터뷰"
    new = config.settings.meeting_path(m["id"])
    assert new.name == f"{_day(m)}_학적파트 인터뷰" and (new / "audio.wav").read_bytes() == b"x" * 10
    assert not mdir.exists()
    assert folders.sync(store, m["id"]) is None  # 이미 맞으면 그대로


def test_old_id_folders_are_migrated_and_work_files_hidden(store):
    m = store.create_meeting("예전 회의")
    # 예전 구조: 폴더 이름이 id, 작업 파일이 그대로 보인다
    config.settings.meeting_path(m["id"]).rmdir()
    store.set_folder(m["id"], None)
    old = config.settings.meetings_dir / m["id"]
    (old / "ckpt").mkdir(parents=True)
    (old / "ckpt" / "asr_0_10.json").write_text("{}")
    (old / "worker.log").write_text("log")
    (old / "audio.m4a").write_bytes(b"a")
    assert config.settings.meeting_path(m["id"]) == old  # 옮기기 전에는 id 폴더를 그대로 찾는다
    assert folders.migrate_all(store, busy=set()) == 1
    new = config.settings.meeting_path(m["id"])
    assert new.name == f"{_day(m)}_예전 회의" and (new / "audio.m4a").exists()
    assert (new / ".work" / "ckpt" / "asr_0_10.json").exists() and (new / ".work" / "worker.log").exists()
    assert not (new / "ckpt").exists() and not old.exists()


def test_busy_meetings_are_left_alone(store):
    m = store.create_meeting("")
    store.update_meeting(m["id"], title="녹음 중")
    before = config.settings.meeting_path(m["id"]).name
    assert folders.migrate_all(store, busy={m["id"]}) == 0
    assert config.settings.meeting_path(m["id"]).name == before

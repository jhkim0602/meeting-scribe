"""왼쪽 목록의 폴더: 앱 안에서만 정리한다. 디스크의 회의 폴더(meetings/날짜_제목)는 옮기지 않는다."""

import os
import sys

import pytest

from scribe import config, folders


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    if "scribe.main" not in sys.modules:  # 다른 시험이 이미 서버를 불렀으면 그 저장 폴더를 같이 쓴다 (DB가 그쪽에 열려 있다)
        data = tmp_path_factory.mktemp("data")
        os.environ.update(SCRIBE_DATA_DIR=str(data), SCRIBE_LIVE_ENGINE="mock", SCRIBE_FINAL_ENGINE="mock", SCRIBE_DIARIZER="mock")
        config.settings.data_dir = data
    config.settings.live_engine = config.settings.final_engine = "mock"
    config.settings.diarizer = "mock"
    config.settings.extra_hosts = ("testserver",)
    from fastapi.testclient import TestClient

    from scribe.main import app, store

    # 다른 시험이 만든 DB가 전역 '회의 폴더 찾기'를 바꿔 두었을 수 있다 → 서버의 DB로 되돌린다
    config.folder_of = store.meeting_folder
    config.settings.data_dir = store.path.parent
    with TestClient(app) as c:
        yield c


def _meeting(client, title):
    m = client.post("/api/meetings", json={"title": title}).json()
    path = config.settings.meeting_dir(m["id"])
    (path / "audio.wav").write_bytes(b"RIFF....")
    return m["id"], path


def _rel(mid):
    return config.settings.meeting_path(mid).relative_to(config.settings.meetings_dir).as_posix()


def test_folders_live_in_the_app_and_files_stay_put(client):
    mid, path = _meeting(client, "주간 회의")
    a = client.post("/api/groups", json={"name": "프로젝트 A"}).json()["group"]
    sub = client.post("/api/groups", json={"name": "디자인", "parent_id": a["id"]}).json()["group"]

    client.put(f"/api/meetings/{mid}/group", json={"group_id": sub["id"]})
    item = next(m for m in client.get("/api/meetings").json() if m["id"] == mid)
    assert item["group_id"] == sub["id"]
    counts = {g["name"]: g["count"] for g in client.get("/api/groups").json()["groups"]}
    assert counts == {"프로젝트 A": 0, "디자인": 1}
    # 디스크는 그대로: meetings/날짜_제목
    assert config.settings.meeting_path(mid) == path and "/" not in _rel(mid)
    assert not (config.settings.meetings_dir / "프로젝트 A").exists()

    client.patch(f"/api/groups/{a['id']}", json={"name": "프로젝트 B"})
    client.patch(f"/api/groups/{sub['id']}", json={"parent_id": ""})  # 맨 바깥으로
    groups = {g["name"]: g for g in client.get("/api/groups").json()["groups"]}
    assert groups["디자인"]["parent_id"] is None and "프로젝트 B" in groups
    assert config.settings.meeting_path(mid) == path

    # 폴더를 지우면 회의는 지우지 않고 한 칸 위(맨 바깥)로 나온다
    client.delete(f"/api/groups/{sub['id']}")
    item = next(m for m in client.get("/api/meetings").json() if m["id"] == mid)
    assert item["group_id"] is None and (path / "audio.wav").exists()


def test_rules_names_depth_and_cycles(client):
    top = client.post("/api/groups", json={"name": "Sales"}).json()["group"]
    assert client.post("/api/groups", json={"name": "sales"}).status_code == 409  # 대소문자만 다른 이름
    assert client.post("/api/groups", json={"name": "   "}).status_code == 400
    b = client.post("/api/groups", json={"name": "b", "parent_id": top["id"]}).json()["group"]
    c = client.post("/api/groups", json={"name": "c", "parent_id": b["id"]}).json()["group"]
    assert client.post("/api/groups", json={"name": "d", "parent_id": c["id"]}).status_code == 400  # 3단계까지
    assert client.patch(f"/api/groups/{top['id']}", json={"parent_id": c["id"]}).status_code == 400  # 자기 안으로
    assert client.put("/api/meetings/없는회의/group", json={"group_id": top["id"]}).status_code == 404
    # 디스크에 못 쓰는 글자도 앱 안 폴더 이름으로는 괜찮다
    assert client.post("/api/groups", json={"name": '보고서: 1/2 "최종"?'}).status_code == 200


def test_meeting_folders_moved_by_the_test_build_come_back(client):
    """예전 시험판이 meetings/폴더/날짜_제목으로 옮겨 둔 회의는 켤 때 meetings/날짜_제목으로 되돌리고 빈 폴더는 지운다"""
    from scribe.main import store

    mid, path = _meeting(client, "되돌릴 회의")
    nested = config.settings.meetings_dir / "프로젝트 A" / path.name
    nested.parent.mkdir(parents=True)
    path.rename(nested)
    (nested.parent / ".DS_Store").write_bytes(b"")
    store.set_folder(mid, f"프로젝트 A/{path.name}")
    assert config.settings.meeting_path(mid) == nested

    folders.migrate_all(store, busy=set())
    assert _rel(mid) == path.name and (config.settings.meeting_path(mid) / "audio.wav").exists()
    assert not (config.settings.meetings_dir / "프로젝트 A").exists()


def test_title_rename_waits_when_windows_locks_files(client, monkeypatch):
    """제목을 바꾸면 폴더 이름도 바꾸는데, 윈도우에서 다른 프로그램이 파일을 열고 있으면 나중에 다시 한다"""
    from scribe.main import store

    mid, path = _meeting(client, "잠긴 회의")
    real = type(config.settings.meetings_dir).rename

    def locked(self, target):
        raise PermissionError(32, "다른 프로세스가 파일을 사용 중이기 때문에 프로세스가 액세스 할 수 없습니다")

    monkeypatch.setattr(type(config.settings.meetings_dir), "rename", locked)
    client.patch(f"/api/meetings/{mid}", json={"title": "새 제목"})
    assert config.settings.meeting_path(mid) == path
    monkeypatch.setattr(type(config.settings.meetings_dir), "rename", real)
    folders.migrate_all(store, busy=set())  # 서버가 1분마다 다시 해 보는 것
    assert _rel(mid).endswith("_새 제목")

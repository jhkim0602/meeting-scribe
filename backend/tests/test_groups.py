"""왼쪽 목록의 폴더: 만들기·이름 바꾸기·옮기기·지우기가 디스크(저장 폴더/meetings/폴더/날짜_제목)에서도 맞는지,
윈도우·맥에서 생길 수 있는 경우(못 쓰는 글자, 예약 이름, 대소문자, 긴 경로, 열려 있는 파일)를 어떻게 다루는지."""

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


def test_folder_tree_follows_on_disk(client):
    mid, _ = _meeting(client, "주간 회의")
    a = client.post("/api/groups", json={"name": "프로젝트 A"}).json()["group"]
    sub = client.post("/api/groups", json={"name": "디자인", "parent_id": a["id"]}).json()["group"]

    r = client.put(f"/api/meetings/{mid}/group", json={"group_id": sub["id"]}).json()
    assert r["pending"] == []
    assert _rel(mid).startswith("프로젝트 A/디자인/") and _rel(mid).endswith("_주간 회의")
    assert (config.settings.meeting_path(mid) / "audio.wav").exists()
    item = next(m for m in client.get("/api/meetings").json() if m["id"] == mid)
    assert item["group_id"] == sub["id"]
    counts = {g["name"]: g["count"] for g in client.get("/api/groups").json()["groups"]}
    assert counts == {"프로젝트 A": 0, "디자인": 1}

    # 폴더 이름을 바꾸면 안의 회의도 따라간다. 빈 예전 폴더는 지운다
    client.patch(f"/api/groups/{a['id']}", json={"name": "프로젝트 B"})
    assert _rel(mid).startswith("프로젝트 B/디자인/")
    assert not (config.settings.meetings_dir / "프로젝트 A").exists()

    # 폴더를 맨 바깥으로 옮기기
    client.patch(f"/api/groups/{sub['id']}", json={"parent_id": ""})
    assert _rel(mid).startswith("디자인/")
    assert not (config.settings.meetings_dir / "프로젝트 B").exists()  # 비어서 지웠다

    # 폴더를 지우면 회의는 지우지 않고 한 칸 위(맨 바깥)로 나온다
    r = client.delete(f"/api/groups/{sub['id']}").json()
    assert "/" not in _rel(mid) and (config.settings.meeting_path(mid) / "audio.wav").exists()
    assert [g["name"] for g in r["groups"]] == ["프로젝트 B"]

    # 회의를 지우면 그 폴더가 비었을 때 폴더도 지운다
    mid2, _ = _meeting(client, "지울 회의")
    client.put(f"/api/meetings/{mid2}/group", json={"group_id": a["id"]})
    gdir = config.settings.meeting_path(mid2).parent
    assert gdir.name == "프로젝트 B"
    client.delete(f"/api/meetings/{mid2}")
    assert not gdir.exists()


def test_rules_names_depth_and_cycles(client):
    top = client.post("/api/groups", json={"name": "Sales"}).json()["group"]
    # 같은 곳에 같은 이름(대소문자만 다른 것 포함)은 안 된다: 윈도우·맥은 대소문자를 구분하지 않는다
    assert client.post("/api/groups", json={"name": "sales"}).status_code == 409
    assert client.post("/api/groups", json={"name": "   "}).status_code == 400
    b = client.post("/api/groups", json={"name": "b", "parent_id": top["id"]}).json()["group"]
    c = client.post("/api/groups", json={"name": "c", "parent_id": b["id"]}).json()["group"]
    assert client.post("/api/groups", json={"name": "d", "parent_id": c["id"]}).status_code == 400  # 3단계까지
    assert client.patch(f"/api/groups/{top['id']}", json={"parent_id": c["id"]}).status_code == 400  # 자기 안으로
    assert client.put("/api/meetings/없는회의/group", json={"group_id": top["id"]}).status_code == 404


def test_disk_names_are_safe_on_windows_and_mac():
    assert folders.dir_name('보고서: 1/2 "최종"?') == "보고서 1 2 최종"
    assert folders.dir_name("CON") == "CON_" and folders.dir_name("nul.txt") == "nul.txt_" and folders.dir_name("COM1") == "COM1_"
    assert folders.dir_name("끝에 점...  ") == "끝에 점"
    assert folders.dir_name("2026-10-20_회의") == "폴더 2026-10-20_회의"  # 회의 폴더와 헷갈리지 않게
    assert folders.dir_name("...") == "폴더"
    assert len(folders.dir_name("가" * 100)) == 40
    # 맥이 돌려줄 수 있는 자모 분리형(NFD)도 같은 이름으로
    import unicodedata

    assert folders.dir_name(unicodedata.normalize("NFD", "회의록")) == "회의록"


def test_locked_files_or_long_paths_wait_and_retry(client, monkeypatch):
    """윈도우에서 다른 프로그램이 파일을 열고 있어 못 옮기면: 목록은 바로 바뀌고 디스크는 나중에 다시 한다"""
    mid, _ = _meeting(client, "잠긴 회의")
    g = client.post("/api/groups", json={"name": "보관"}).json()["group"]
    real = type(config.settings.meetings_dir).rename

    def locked(self, target):
        raise PermissionError(32, "다른 프로세스가 파일을 사용 중이기 때문에 프로세스가 액세스 할 수 없습니다")

    monkeypatch.setattr(type(config.settings.meetings_dir), "rename", locked)
    r = client.put(f"/api/meetings/{mid}/group", json={"group_id": g["id"]}).json()
    assert r["pending"] == [mid] and "/" not in _rel(mid)
    item = next(m for m in client.get("/api/meetings").json() if m["id"] == mid)
    assert item["group_id"] == g["id"]  # 목록은 이미 폴더 안

    monkeypatch.setattr(type(config.settings.meetings_dir), "rename", real)
    from scribe.main import store

    folders.migrate_all(store, busy=set())  # 서버가 1분마다 다시 해 보는 것
    assert _rel(mid).startswith("보관/")

    # 윈도우의 260자 제한: 넘으면 옮기지 않고 그대로 둔다
    monkeypatch.setattr(sys, "platform", "win32")
    deep = client.post("/api/groups", json={"name": "가" * 40}).json()["group"]
    monkeypatch.setattr(folders, "_INNER", 400)  # 경로가 아주 긴 저장 위치를 흉내
    r = client.put(f"/api/meetings/{mid}/group", json={"group_id": deep["id"]}).json()
    assert r["pending"] == [mid] and _rel(mid).startswith("보관/")


def test_case_only_rename_keeps_folder_on_case_insensitive_disks(client, monkeypatch):
    """맥·윈도우: 폴더 이름의 대소문자만 바꾸면 같은 폴더이므로 회의 폴더에 ' (2)'가 붙지 않는다"""
    monkeypatch.setattr(sys, "platform", "darwin")
    mid, _ = _meeting(client, "대소문자")
    g = client.post("/api/groups", json={"name": "abc"}).json()["group"]
    client.put(f"/api/meetings/{mid}/group", json={"group_id": g["id"]})
    before = _rel(mid)
    client.patch(f"/api/groups/{g['id']}", json={"name": "ABC"})
    assert _rel(mid) == before and "(2)" not in _rel(mid)

"""API 통합 테스트: 녹음 → 기록 위 메모·댓글, 문장 직접 고치기 → 원본 스크립트(참고용) → 내보내기."""

import json
import os
import sys
import subprocess
import time

import numpy as np
import pytest

SR = 16000


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    os.environ.update(SCRIBE_DATA_DIR=str(data), SCRIBE_LIVE_ENGINE="mock", SCRIBE_FINAL_ENGINE="mock", SCRIBE_DIARIZER="mock")
    from scribe import config

    config.settings.data_dir = data
    config.settings.live_engine = config.settings.final_engine = "mock"
    config.settings.diarizer = "mock"
    config.settings.extra_hosts = ("testserver",)  # TestClient의 Host
    from fastapi.testclient import TestClient

    from scribe import recording
    from scribe.main import app

    recording.MIN_BREAK_S = 1.0  # 테스트에서는 1초 이상 멈추면 쉬는 시간
    with TestClient(app) as c:
        yield c


def _speech_pcm(pattern):
    parts = []
    rng = np.random.default_rng(1)
    for kind, sec in pattern:
        n = int(sec * SR)
        if kind == "s":
            t = np.arange(n) / SR
            x = 0.3 * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) * np.sin(2 * np.pi * 210 * t)
        else:
            x = rng.normal(0, 0.002, n)
        parts.append(x)
    x = np.concatenate(parts)
    return (x * 32767).astype("<i2").tobytes()


def _wait_job(client, mid, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"/api/meetings/{mid}/job").json()["job"]
        if job and job["status"] in ("done", "error", "cancelled"):
            return job
        time.sleep(0.3)
    raise AssertionError("job timeout")


def test_full_flow(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["live"]["kind"] == "mock"
    assert h["app_install"] is False and h["mcp"] is None  # 터미널로 설치: scripts/mcp.sh

    m = client.post("/api/meetings", json={"title": "주간 회의"}).json()
    mid = m["id"]
    # 쉼은 1.5초: 시연용 글자는 말끝과 상관없어 짧은 쉼(1.1초 미만)은 이어질 수 있다
    pcm = _speech_pcm([("n", 0.5), ("s", 3.0), ("n", 1.5), ("s", 4.0), ("n", 1.5), ("s", 3.5), ("n", 1.5)])
    events = []
    with client.websocket_connect(f"/api/meetings/{mid}/record") as ws:
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready" and ready["offset"] == 0
        step = SR // 4 * 2
        for i in range(0, len(pcm), step):
            ws.send_bytes(pcm[i:i + step])
        ws.send_text(json.dumps({"type": "stop"}))
        while True:
            msg = json.loads(ws.receive_text())
            events.append(msg)
            if msg["type"] == "stopped":
                break
    segs = [e["segment"] for e in events if e["type"] == "segment"]
    assert len(segs) == 3, events
    assert events[-1]["job"]["status"] in ("queued", "running")

    live = client.get(f"/api/meetings/{mid}/live").json()
    units = live["units"]
    assert [u["id"] for u in units] == [s["id"] for s in segs]

    u0 = units[0]
    w = u0["text"].split(" ")
    c = client.post(f"/api/meetings/{mid}/annotations", json={
        "kind": "comment", "body": "이 숫자 확인 필요",
        "start": {"unit": u0["id"], "offset": 0}, "end": {"unit": u0["id"], "offset": len(w[0]) + 1 + len(w[1])},
    }).json()
    assert c["pos"]["start"]["unit"] == u0["id"] and c["pos"]["text"] == f"{w[0]} {w[1]}"
    assert client.post(f"/api/meetings/{mid}/annotations", json={"kind": "edit", "start": c["pos"]["start"], "end": c["pos"]["end"], "body": "x"}).status_code == 400
    memo = client.post(f"/api/meetings/{mid}/annotations", json={"kind": "memo", "t": u0["t1"] + 0.01, "after": u0["id"], "body": "여기 중요"}).json()
    assert memo["pos"] == {"after": u0["id"]}
    r = client.post(f"/api/annotations/{c['id']}/replies", json={"text": "다음 회의에서 확인"}).json()
    assert r["replies"][0]["text"] == "다음 회의에서 확인"

    # 틀린 글자를 직접 고친다: 앞에 글자를 넣어도 댓글은 같은 자리에, 댓글 단 글자를 고치면 고친 글자에
    fixed = "확인 " + u0["text"].replace(w[1], "(고침)", 1)
    ed = client.patch(f"/api/meetings/{mid}/segments/{u0['id']}", json={"text": fixed}).json()
    assert ed["unit"]["text"] == fixed and ed["unit"]["text_orig"] == u0["text"]
    moved = {a["id"]: a for a in ed["annotations"]}
    assert moved[c["id"]]["pos"]["text"] == f"{w[0]} (고침)"
    assert client.patch(f"/api/meetings/{mid}/segments/{u0['id']}", json={"text": "   "}).status_code == 400

    job = _wait_job(client, mid)
    assert job["status"] == "done", job
    # 후처리 뒤에도 기록은 그대로 (고친 글자, 메모·댓글 위치 모두)
    live = client.get(f"/api/meetings/{mid}/live").json()
    assert live["units"][0]["text"] == fixed and [u["id"] for u in live["units"]] == [s["id"] for s in segs]
    anns = {a["id"]: a for a in live["annotations"]}
    assert anns[c["id"]]["pos"]["text"] == f"{w[0]} (고침)" and anns[memo["id"]]["pos"] == {"after": u0["id"]}
    # 원본 스크립트는 참고용 Markdown으로 따로
    script = client.get(f"/api/meetings/{mid}/script.md").text
    assert "원본 스크립트" in script and "참고용" in script and "**화자 1**" in script
    assert client.get(f"/api/meetings/{mid}/final").status_code == 404

    md = client.get(f"/api/meetings/{mid}/export.md").text
    assert "[댓글 c1] 이 숫자 확인 필요" in md
    assert "[메모" in md and "여기 중요" in md
    assert "(고침)" in md and "1문장을 직접 고침" in md

    notes = [{"type": "paragraph", "props": {}, "content": [{"type": "timestamp", "props": {"t": 3.2}}, {"type": "text", "text": " 예산 결정", "styles": {}}], "children": []}]
    assert client.put(f"/api/meetings/{mid}/notes", json={"blocks": notes}).json()["ok"]
    assert client.get(f"/api/meetings/{mid}/notes").json()["markdown"].startswith("[00:03] 예산 결정")

    a = client.get(f"/api/meetings/{mid}/audio", headers={"Range": "bytes=0-99"})
    assert a.status_code == 206 and len(a.content) == 100 and a.content[:4] == b"RIFF"

    assert client.get("/api/search", params={"q": "예산"}).json()
    meetings = client.get("/api/meetings").json()
    assert meetings[0]["id"] == mid and meetings[0]["status"] == "ready"


def test_resume_recording_appends(client):
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.0), ("n", 1.0)])
    for expected_offset in (0.0, 3.0):
        with client.websocket_connect(f"/api/meetings/{mid}/record") as ws:
            ready = json.loads(ws.receive_text())
            assert ready["type"] == "ready", ready
            assert abs(ready["offset"] - expected_offset) < 0.01
            ws.send_bytes(pcm)
            ws.send_text(json.dumps({"type": "pause"}))
            while json.loads(ws.receive_text())["type"] != "paused":
                pass
    segs = client.get(f"/api/meetings/{mid}/live").json()["units"]
    assert len(segs) == 2 and segs[1]["t0"] >= 3.0
    client.post(f"/api/meetings/{mid}/cancel")


def test_upload_file(client, tmp_path):
    wav = tmp_path / "in.wav"
    pcm = _speech_pcm([("s", 3.0), ("n", 1.0)] * 5)
    from scribe.audio import wav_header

    wav.write_bytes(wav_header(len(pcm)) + pcm)
    mp3 = tmp_path / "회의 녹음.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "64k", str(mp3)], check=True)
    with open(mp3, "rb") as f:
        m = client.post("/api/upload", files={"file": ("회의 녹음.m4a", f, "audio/mp4")}, data={"title": ""}).json()
    assert m["title"] == "회의 녹음" and m["source"] == "upload"
    job = _wait_job(client, m["id"])
    assert job["status"] == "done", job
    # 올린 파일은 받아 적은 결과가 곧 회의 기록 (목소리 묶음 A, B…로 화자를 고를 수 있고, 직접 고칠 수 있다)
    units = client.get(f"/api/meetings/{m['id']}/live").json()["units"]
    assert units and all(u["speaker_auto"] for u in units) and units[0]["speaker_auto"] == "A"
    assert abs(client.get(f"/api/meetings/{m['id']}").json()["duration"] - 20.0) < 0.2
    pid = client.put(f"/api/meetings/{m['id']}/participants", json={"participants": [{"name": "오세린"}]}).json()["participants"][0]["id"]
    ch = client.post(f"/api/meetings/{m['id']}/live/speaker", json={"segment_ids": [units[0]["id"]], "participant_id": pid}).json()["changes"]
    assert {c["id"] for c in ch} == {u["id"] for u in units if u["speaker_auto"] == "A"}
    ed = client.patch(f"/api/meetings/{m['id']}/segments/{units[0]['id']}", json={"text": "고친 첫 문장"}).json()
    assert ed["unit"]["text"] == "고친 첫 문장"
    # 다시 처리해도 사람이 고친 기록은 그대로
    client.post(f"/api/meetings/{m['id']}/process")
    assert _wait_job(client, m["id"])["status"] == "done"
    again = client.get(f"/api/meetings/{m['id']}/live").json()["units"]
    assert again[0]["text"] == "고친 첫 문장" and len(again) == len(units)


def test_comment_export_flag_and_whole_sentence_comment(client):
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.5), ("n", 1.0), ("s", 2.5), ("n", 1.0)])
    with client.websocket_connect(f"/api/meetings/{mid}/record") as ws:
        json.loads(ws.receive_text())
        ws.send_bytes(pcm)
        ws.send_text(json.dumps({"type": "pause"}))
        while json.loads(ws.receive_text())["type"] != "paused":
            pass
    units = client.get(f"/api/meetings/{mid}/live").json()["units"]
    u = units[-1]
    # 문장 전체에 댓글 (선택 없이)
    c = client.post(f"/api/meetings/{mid}/annotations", json={
        "kind": "comment", "view": "live", "body": "전체 문장 댓글",
        "start": {"unit": u["id"], "offset": 0}, "end": {"unit": u["id"], "offset": len(u["text"])},
    }).json()
    assert c["anchor"]["quote"] == u["text"] and c["exported_at"] is None
    r = client.post(f"/api/meetings/{mid}/annotations/exported", json={"ids": [c["id"]]}).json()
    assert r["updated"] == 1
    ann = client.get(f"/api/meetings/{mid}/live").json()["annotations"][0]
    assert ann["exported_at"] is not None
    client.post(f"/api/meetings/{mid}/annotations/exported", json={"ids": [c["id"]], "exported": False})
    assert client.get(f"/api/meetings/{mid}/live").json()["annotations"][0]["exported_at"] is None
    client.post(f"/api/meetings/{mid}/cancel")



def _record(client, mid, pcm, query="", messages=(), until="paused"):
    """녹음 연결을 열어 소리를 보내고 메시지를 차례로 보낸 뒤, until 메시지를 받으면 닫는다."""
    got = []
    with client.websocket_connect(f"/api/meetings/{mid}/record{query}") as ws:
        ready = json.loads(ws.receive_text())
        got.append(ready)
        if pcm:
            ws.send_bytes(pcm)
        for m in messages:
            if isinstance(m, (int, float)):
                time.sleep(m)
                continue
            ws.send_text(json.dumps(m))
            want = {"pause": "paused", "resume": "resumed", "stop": "stopped"}[m["type"]]
            while True:
                msg = json.loads(ws.receive_text())
                got.append(msg)
                if msg["type"] == want:
                    break
    return got


def test_pause_resume_creates_break_divider(client):
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.5), ("n", 1.0)])
    got = _record(client, mid, pcm, messages=[{"type": "pause"}, 1.3, {"type": "resume"}, {"type": "pause"}, 0.2, {"type": "resume"}])
    resumed = [m for m in got if m["type"] == "resumed"]
    assert resumed[0]["divider"] is not None and resumed[1]["divider"] is None  # 0.2초 멈춤은 무시
    d = resumed[0]["divider"]
    assert d["kind"] == "divider" and d["body"] == "쉬는 시간" and d["meta"]["auto"] is True
    assert d["meta"]["break_end"] - d["meta"]["break_start"] >= 1.3
    live = client.get(f"/api/meetings/{mid}/live").json()
    div = [a for a in live["annotations"] if a["kind"] == "divider"][0]
    assert div["pos"]["after"] == live["units"][-1]["id"]
    md = client.get(f"/api/meetings/{mid}/export.md").text
    assert "──── 쉬는 시간 · " in md and "분 ────" in md
    client.post(f"/api/meetings/{mid}/cancel")


def test_resume_after_stop_cancels_processing_and_marks_break(client):
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.5), ("n", 1.0)] * 3)
    got = _record(client, mid, pcm, messages=[{"type": "stop"}], until="stopped")
    assert got[-1]["job"]["status"] in ("queued", "running")
    # 실수로 종료한 직후 바로 이어서 녹음: 원본 작업을 취소하고, 몇 초 차이는 쉬는 시간이 아니다
    got2 = _record(client, mid, b"", messages=[{"type": "pause"}])
    ready = got2[0]
    assert ready["type"] == "ready" and ready["cancelled_job"] is True
    assert ready["divider"] is None
    job = client.get(f"/api/meetings/{mid}/job").json()["job"]
    assert job["status"] == "cancelled"
    client.post(f"/api/meetings/{mid}/cancel")


def test_continuous_reconnect_is_not_a_break(client):
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.0), ("n", 1.0)])
    _record(client, mid, pcm, messages=[{"type": "pause"}])
    time.sleep(1.2)
    got = _record(client, mid, pcm, query="?continuous=1", messages=[{"type": "pause"}])
    assert got[0]["divider"] is None
    # 브라우저가 일시정지 상태로 다시 연결하면 재개할 때 구분선을 만든다
    m = client.get(f"/api/meetings/{mid}").json()
    assert m["paused_wall"] is not None
    time.sleep(1.2)
    got = _record(client, mid, b"", query="?continuous=1&paused=1", messages=[{"type": "resume"}, {"type": "pause"}])
    resumed = [x for x in got if x["type"] == "resumed"][0]
    assert resumed["divider"] is not None
    # 잠자기 등으로 소리가 끊긴 재연결: since 시각부터 쉬는 시간
    since = time.time() - 120
    got = _record(client, mid, b"", query=f"?since={since}", messages=[{"type": "pause"}])
    d = got[0]["divider"]
    assert d is not None and abs(d["meta"]["break_start"] - since) < 2
    client.post(f"/api/meetings/{mid}/cancel")


def test_manual_divider_and_settings(client):
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.0), ("n", 1.0)])
    _record(client, mid, pcm, messages=[{"type": "pause"}])
    u = client.get(f"/api/meetings/{mid}/live").json()["units"][0]
    d = client.post(f"/api/meetings/{mid}/annotations", json={"kind": "divider", "view": "live", "t": u["t1"] + 0.01, "after": u["id"], "body": "2부"}).json()
    assert d["pos"] == {"after": u["id"]} and d["meta"] is None
    assert client.patch(f"/api/annotations/{d['id']}", json={"body": "안건 2"}).json()["body"] == "안건 2"
    assert "──── 안건 2 ────" in client.get(f"/api/meetings/{mid}/export.md").text
    s = client.put("/api/settings", json={"break_prompt_minutes": 5}).json()
    assert s["break_prompt_minutes"] == 5 and s["auto_on_battery"] is False
    assert client.put("/api/settings", json={"auto_on_battery": True}).json()["auto_on_battery"] is True
    client.post(f"/api/meetings/{mid}/cancel")


def test_app_install_shows_mcp_command_with_app_python(client, tmp_path, monkeypatch):
    cfg = tmp_path / "설정 폴더" / "config.json"
    monkeypatch.setenv("SCRIBE_CONFIG", str(cfg))
    h = client.get("/api/health").json()
    assert h["app_install"] is True
    assert h["mcp"]["command"] == sys.executable and h["mcp"]["args"] == ["-m", "scribe.mcp_server"]
    assert h["mcp"]["env"]["SCRIBE_CONFIG"] == str(cfg) and h["mcp"]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_local_only_blocks_other_sites(client):
    from starlette.websockets import WebSocketDisconnect

    from scribe.security import hostname

    assert hostname("[::1]:8765") == "::1" and hostname("LocalHost:8765") == "localhost" and hostname("127.0.0.1") == "127.0.0.1"
    # DNS 리바인딩: Host가 다른 이름이면 거절
    assert client.get("/api/meetings", headers={"host": "evil.example:8765"}).status_code == 403
    # 다른 사이트 페이지에서 온 요청은 거절, 같은 출처와 개발 서버는 통과
    assert client.get("/api/meetings", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/meetings", headers={"host": "127.0.0.1:8765", "origin": "http://localhost:3000"}).status_code == 403
    assert client.get("/api/meetings", headers={"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"}).status_code == 200
    assert client.get("/api/meetings", headers={"host": "localhost:8765", "origin": "http://localhost:5173"}).status_code == 200
    assert client.post("/api/meetings", json={}, headers={"origin": "null"}).status_code == 403
    mid = client.post("/api/meetings", json={}).json()["id"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/meetings/{mid}/record", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert client.get(f"/api/meetings/{mid}").json()["status"] == "empty"


def test_disk_space_guard(client, monkeypatch):
    from scribe import recording

    mid = client.post("/api/meetings", json={}).json()["id"]
    # 시작할 때 공간이 거의 없으면 녹음하지 않는다
    monkeypatch.setattr(recording, "disk_free_seconds", lambda path: 10.0)
    with client.websocket_connect(f"/api/meetings/{mid}/record") as ws:
        msg = json.loads(ws.receive_text())
    assert msg["type"] == "error" and "저장 공간" in msg["message"]

    # 녹음 중에 공간이 줄면 알리고, 바닥나면 여기까지 저장하고 멈춘다
    free = iter([5000.0, 300.0, 5.0])
    monkeypatch.setattr(recording, "disk_free_seconds", lambda path: next(free, 5.0))
    got = []
    chunk = _speech_pcm([("n", 1.0)])
    with client.websocket_connect(f"/api/meetings/{mid}/record") as ws:
        ready = json.loads(ws.receive_text())
        assert ready["disk_free_s"] == 5000
        try:
            for _ in range(70):
                ws.send_bytes(chunk)
            while True:
                m = json.loads(ws.receive_text())
                got.append(m)
                if m["type"] == "disk-full":
                    break
        except Exception:  # noqa: BLE001 - 서버가 연결을 닫음
            pass
    kinds = [m["type"] for m in got]
    assert "disk" in kinds and kinds[-1] == "disk-full", kinds
    warn = next(m for m in got if m["type"] == "disk")
    assert warn["free_s"] == 300
    full = got[-1]
    assert 59.0 <= full["duration"] <= 61.0
    time.sleep(0.3)
    m = client.get(f"/api/meetings/{mid}").json()
    assert m["status"] == "recorded" and abs(m["duration"] - full["duration"]) < 0.01


def test_wav_appender_survives_disk_full(tmp_path):
    from scribe.audio import WavAppender, wav_info

    w = WavAppender(tmp_path / "a.wav")
    w.append(b"\x01\x00" * 1000)

    class Half:
        def __init__(self, f):
            self.f = f

        def write(self, b):
            return self.f.write(b[: len(b) // 2])  # 공간이 모자라 절반만 써짐

        def __getattr__(self, name):
            return getattr(self.f, name)

    w._f = Half(w._f)
    with pytest.raises(OSError):
        w.append(b"\x02\x00" * 1000)
    w._f = w._f.f
    w.close()
    info = wav_info(tmp_path / "a.wav")
    assert info.num_samples == 1000 and (tmp_path / "a.wav").stat().st_size == 44 + 2000



def test_mutations_return_the_changed_item_with_its_position(client):
    """화면이 전체 기록을 다시 받지 않도록, 바뀐 항목을 위치와 함께 돌려준다."""
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.5), ("n", 1.0), ("s", 3.0), ("n", 1.0)])
    _record(client, mid, pcm, messages=[{"type": "stop"}], until="stopped")
    assert _wait_job(client, mid)["status"] == "done"
    u = client.get(f"/api/meetings/{mid}/live").json()["units"][0]
    c = client.post(f"/api/meetings/{mid}/annotations", json={
        "kind": "comment", "body": "확인", "start": {"unit": u["id"], "offset": 0}, "end": {"unit": u["id"], "offset": 4},
    }).json()
    assert c["pos"]["start"]["unit"] == u["id"] and "resolved" not in c
    r = client.post(f"/api/annotations/{c['id']}/replies", json={"text": "네"}).json()
    assert r["replies"][0]["text"] == "네" and r["pos"] == c["pos"]
    ex = client.post(f"/api/meetings/{mid}/annotations/exported", json={"ids": [c["id"]]}).json()
    assert ex["updated"] == 1 and ex["exported_at"][c["id"]] > 0
    got = client.get(f"/api/meetings/{mid}/live").json()["annotations"][0]
    assert got["replies"][0]["text"] == "네" and got["exported_at"] and got["pos"] == c["pos"]


def test_clock_maps_recording_time_to_wall_time(client):
    """녹음 시간 → 실제 시각: 시작·재개할 때마다 기준점을 남긴다 (끊김 없는 재연결은 제외)."""
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.0), ("n", 1.0)])
    t0 = time.time()
    _record(client, mid, pcm, messages=[{"type": "pause"}, 1.2, {"type": "resume"}, {"type": "pause"}])
    _record(client, mid, pcm, query="?continuous=1", messages=[{"type": "pause"}])
    clock = client.get(f"/api/meetings/{mid}").json()["clock"]
    assert [p[0] for p in clock] == [0.0, 3.0]
    assert abs(clock[0][1] - t0) < 1.0 and clock[1][1] - clock[0][1] >= 1.2
    st = client.get("/api/storage").json()
    assert st["meetings"] >= 1 and st["audio_bytes"] > 0 and st["free_bytes"] > 0



def test_settled_audio_is_compressed_and_still_usable(client, monkeypatch):
    """다 만든 회의의 WAV는 m4a로 줄이고, 재생·다시 만들기·이어서 녹음은 그대로 된다."""
    from scribe import config, storage
    from scribe import main as appmain

    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.5), ("n", 1.0)] * 2)
    _record(client, mid, pcm, messages=[{"type": "stop"}], until="stopped")
    assert _wait_job(client, mid)["status"] == "done"
    mdir = config.settings.meetings_dir / mid
    duration = client.get(f"/api/meetings/{mid}").json()["duration"]

    monkeypatch.setattr(storage, "COMPRESS_AFTER_H", 0.0)
    assert mid in appmain.jobs.housekeep_once()
    assert (mdir / "audio.m4a").exists() and not (mdir / "audio.wav").exists()
    assert client.get(f"/api/meetings/{mid}").json()["has_audio"] is True
    r = client.get(f"/api/meetings/{mid}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["content-type"] == "audio/mp4" and len(r.content) == 100

    # 다시 만들기: m4a를 임시 WAV로 풀어 처리하고, 끝나면 임시 파일은 지운다. 원본 스크립트는 마지막 것만 남긴다.
    for _ in range(2):
        client.post(f"/api/meetings/{mid}/process")
        assert _wait_job(client, mid)["status"] == "done"
    assert (mdir / "audio.m4a").exists() and not (mdir / "audio.wav").exists() and not (mdir / "work.wav").exists()
    with appmain.store.conn() as c:
        versions = [r["version"] for r in c.execute("SELECT version FROM final_versions WHERE meeting_id=?", (mid,))]
    assert versions == [3]

    # 이어서 녹음: 먼저 WAV로 풀고(preparing) 같은 길이에서 이어 붙인다
    with client.websocket_connect(f"/api/meetings/{mid}/record") as ws:
        first = json.loads(ws.receive_text())
        ready = json.loads(ws.receive_text())
        assert first["type"] == "preparing" and ready["type"] == "ready"
        assert abs(ready["offset"] - duration) < 0.05
        ws.send_bytes(pcm)
        ws.send_text(json.dumps({"type": "pause"}))
        while json.loads(ws.receive_text())["type"] != "paused":
            pass
    assert (mdir / "audio.wav").exists() and not (mdir / "audio.m4a").exists()
    client.post(f"/api/meetings/{mid}/cancel")


def test_upload_copy_is_removed_after_processing(client, tmp_path):
    from scribe import config
    from scribe.audio import wav_header

    pcm = _speech_pcm([("s", 2.0), ("n", 1.0)] * 2)
    f = tmp_path / "회의.wav"
    f.write_bytes(wav_header(len(pcm)) + pcm)
    with open(f, "rb") as fh:
        m = client.post("/api/upload", files={"file": ("회의.wav", fh, "audio/wav")}, data={"title": ""}).json()
    assert _wait_job(client, m["id"])["status"] == "done"
    mdir = config.settings.meetings_dir / m["id"]
    assert not list(mdir.glob("source.*")) and (mdir / "audio.wav").exists()


def test_search_index_follows_writes(client):
    """검색 색인: 회의 기록(고친 글자 포함), 노트, 제목이 바뀌면 바로 찾아지고, 지우면 사라진다. 원본 스크립트는 찾지 않는다."""
    from scribe import main as appmain

    mid = client.post("/api/meetings", json={"title": "색인 시험 회의"}).json()["id"]
    pcm = _speech_pcm([("s", 2.5), ("n", 1.0)] * 2)
    _record(client, mid, pcm, messages=[{"type": "pause"}])
    live_text = client.get(f"/api/meetings/{mid}/live").json()["units"][0]["text"]
    word = live_text.replace(" ", "")[:4]
    hits = [h for h in client.get("/api/search", params={"q": live_text[:5]}).json() if h["meeting_id"] == mid]
    assert hits and hits[0]["kind"] == "live" and hits[0]["unit"]
    client.put(f"/api/meetings/{mid}/notes", json={"blocks": [{"type": "paragraph", "content": [{"type": "text", "text": "다음 주 화요일까지 견적서 보내기", "styles": {}}], "children": []}]})
    assert any(h["kind"] == "notes" and "견적서" in h["snippet"] for h in client.get("/api/search", params={"q": "견적서"}).json())
    client.patch(f"/api/meetings/{mid}", json={"title": "분기 결산 회의"})
    assert any(h["kind"] == "title" for h in client.get("/api/search", params={"q": "결산"}).json() if h["meeting_id"] == mid)
    assert not any(h["kind"] == "title" for h in client.get("/api/search", params={"q": "색인 시험"}).json() if h["meeting_id"] == mid)
    # 원본 스크립트를 만들어도 회의 기록에서 찾는다
    client.post(f"/api/meetings/{mid}/process")
    assert _wait_job(client, mid)["status"] == "done"
    kinds = {h["kind"] for h in client.get("/api/search", params={"q": live_text[:5]}).json() if h["meeting_id"] == mid}
    assert kinds == {"live"}
    # 고친 글자로 바로 찾아진다
    u0 = client.get(f"/api/meetings/{mid}/live").json()["units"][0]
    client.patch(f"/api/meetings/{mid}/segments/{u0['id']}", json={"text": u0["text"] + " 견본고침"})
    assert any(h["kind"] == "live" and h["unit"] == u0["id"] for h in client.get("/api/search", params={"q": "견본고침"}).json())
    # 색인을 처음부터 다시 만들어도 같은 결과
    before = client.get("/api/search", params={"q": "견적서"}).json()
    appmain.store.rebuild_search_index()
    assert client.get("/api/search", params={"q": "견적서"}).json() == before
    assert word
    client.delete(f"/api/meetings/{mid}")
    assert not any(h["meeting_id"] == mid for h in client.get("/api/search", params={"q": "견적서"}).json())


def test_backup_zip_contains_everything(client, tmp_path):
    import io
    import sqlite3
    import zipfile

    mid = client.post("/api/meetings", json={"title": "백업/시험: 회의"}).json()["id"]
    _record(client, mid, _speech_pcm([("s", 2.0), ("n", 1.0)]), messages=[{"type": "pause"}])
    r = client.get("/api/backup.zip")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "scribe.db" in names and "복원 방법.txt" in names
    assert f"meetings/{mid}/audio.wav" in names
    md = [n for n in names if n.startswith("회의록/") and "백업_시험_ 회의" in n]
    assert md and "백업/시험" in zf.read(md[0]).decode()
    db = tmp_path / "restored.db"
    db.write_bytes(zf.read("scribe.db"))
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT title FROM meetings WHERE id=?", (mid,)).fetchone()[0] == "백업/시험: 회의"
    client.post(f"/api/meetings/{mid}/cancel")



def test_live_temporary_speakers_follow_one_click(client):
    """실시간 임시 화자: 목소리로 묶고(A, B…), 한 문장에 참여자를 고르면 같은 목소리 문장 전체와
    앞으로 올 문장에 반영된다. 다음 회의의 목소리 기억으로도 이어진다."""
    mid = client.post("/api/meetings", json={}).json()["id"]
    parts = client.put(f"/api/meetings/{mid}/participants", json={"participants": [{"name": "김민지"}, {"name": " 박준호 "}, {"name": "김민지"}]}).json()["participants"]
    assert [p["name"] for p in parts] == ["김민지", "박준호"]
    minji, junho = parts[0]["id"], parts[1]["id"]
    # 시연용 목소리 시간표: A 0~9.5초, B 9.5~14.5, A 14.5~21.5, C 21.5~27.5, B 27.5~35.5
    _record(client, mid, _speech_pcm([("s", 3.0), ("n", 1.5)] * 8), messages=[{"type": "pause"}])
    units = client.get(f"/api/meetings/{mid}/live").json()["units"]
    autos = [u["speaker_auto"] for u in units]
    assert len(units) == 8 and autos[0] == "A" and autos[2] != "A" and autos[3] == "A" and len(set(autos)) == 3, autos
    assert all(u["speaker"] is None for u in units)

    # 한 번 고르면 같은 목소리 문장 전체에
    ch = client.post(f"/api/meetings/{mid}/live/speaker", json={"segment_ids": [units[0]["id"]], "participant_id": minji}).json()["changes"]
    assert {c["id"] for c in ch} == {u["id"] for u in units if u["speaker_auto"] == "A"}
    units = client.get(f"/api/meetings/{mid}/live").json()["units"]
    assert units[0]["speaker_src"] == "manual" and units[1]["speaker"] == minji and units[1]["speaker_src"] == "auto"
    # 되돌리기: 한 문장만 김민지로 두고 나머지는 예전대로
    r = client.post(f"/api/meetings/{mid}/live/speaker", json={"segment_ids": [units[4]["id"]], "participant_id": junho})
    assert r.status_code == 200 and r.json()["previous_map"] == {"A": minji}
    prev = [dict(u) for u in units if u["speaker_auto"] == "A" and u["id"] != units[4]["id"]]
    client.post(f"/api/meetings/{mid}/live/speaker/restore", json={"rows": prev, "speaker_map": r.json()["previous_map"]})
    after = {u["id"]: u for u in client.get(f"/api/meetings/{mid}/live").json()["units"]}
    assert after[units[4]["id"]]["speaker"] == junho and after[units[1]["id"]]["speaker"] == minji and after[units[0]["id"]]["speaker_src"] == "manual"
    client.post(f"/api/meetings/{mid}/live/speaker", json={"segment_ids": [units[4]["id"]], "participant_id": minji, "scope": "one"})
    # "이 문장만"
    ch = client.post(f"/api/meetings/{mid}/live/speaker", json={"segment_ids": [units[2]["id"]], "participant_id": junho, "scope": "one"}).json()["changes"]
    assert [c["id"] for c in ch] == [units[2]["id"]]
    client.post(f"/api/meetings/{mid}/live/speaker", json={"segment_ids": [units[3]["id"]], "participant_id": minji, "scope": "one"})
    assert client.post(f"/api/meetings/{mid}/live/speaker", json={"segment_ids": [units[0]["id"]], "participant_id": "nobody"}).status_code == 400

    # 앞으로 오는 같은 목소리 문장은 처음부터 김민지
    got = _record(client, mid, _speech_pcm([("s", 3.0), ("n", 1.5)] * 2), messages=[{"type": "pause"}])
    labels = [m["segment"] for m in got if m["type"] == "speaker"]
    assert labels and all(l["speaker"] == minji and l["speaker_src"] == "auto" for l in labels), labels
    md = client.get(f"/api/meetings/{mid}/export.md").text
    assert "김민지:" in md and "박준호:" in md and "화자 C(추정):" in md

    # 후처리(원본 스크립트)를 만들어도 회의 중 고른 화자는 기록에 그대로
    before = [(u["id"], u["speaker"]) for u in client.get(f"/api/meetings/{mid}/live").json()["units"]]
    client.post(f"/api/meetings/{mid}/process")
    assert _wait_job(client, mid)["status"] == "done"
    assert [(u["id"], u["speaker"]) for u in client.get(f"/api/meetings/{mid}/live").json()["units"]] == before

    # 다음 회의: 같은 이름의 참여자를 넣으면 기억한 목소리로 처음부터 알아본다
    m2 = client.post("/api/meetings", json={}).json()["id"]
    p2 = client.put(f"/api/meetings/{m2}/participants", json={"participants": [{"name": "김민지"}]}).json()["participants"]
    got = _record(client, m2, _speech_pcm([("s", 3.0), ("n", 1.5)]), messages=[{"type": "pause"}])
    first = [m["segment"] for m in got if m["type"] == "speaker"][0]
    assert first["speaker"] == p2[0]["id"] and first["speaker_src"] == "auto"
    sug = {s["name"]: s for s in client.get("/api/participants/suggest").json()}
    assert sug["김민지"]["voice"] is True and "박준호" in sug
    for x in (mid, m2):
        client.post(f"/api/meetings/{x}/cancel")


def test_minutes_html_has_summary_notes_and_marks_but_no_transcript(client):
    """회의록: 정보·요약·할 일·노트·메모를 담고, 받아 적은 문장 자체는 넣지 않는다. 글은 모두 이스케이프."""
    mid = client.post("/api/meetings", json={"title": "주간 <회의>"}).json()["id"]
    client.put(f"/api/meetings/{mid}/participants", json={"participants": [{"name": "김민지"}, {"name": "박준호"}]})
    _record(client, mid, _speech_pcm([("s", 2.5), ("n", 1.0), ("s", 2.5), ("n", 1.0)]), messages=[{"type": "pause"}])
    units = client.get(f"/api/meetings/{mid}/live").json()["units"]
    u = units[-1]
    client.post(f"/api/meetings/{mid}/annotations", json={
        "kind": "comment", "view": "live", "body": "숫자 <확인>",
        "start": {"unit": u["id"], "offset": 0}, "end": {"unit": u["id"], "offset": len(u["text"])},
    })
    client.post(f"/api/meetings/{mid}/annotations", json={"kind": "memo", "view": "live", "t": u["t1"] + 0.01, "after": u["id"], "body": "여기 중요"})
    client.put(f"/api/meetings/{mid}/summary", json={"markdown": (
        "## 한 줄 요약\n출시일을 **11월 18일**로 정했다.\n\n## 결정 사항\n- 베타는 18일 [01:30]\n\n"
        "## 할 일\n| 할 일 | 담당 | 기한 | 근거 |\n|---|---|---|---|\n| 공고 올리기 | 박준호 | 10/24 | [02:05] |\n\n"
        "## 주요 논의\n- 일정: QA가 길어짐 [00:40]\n  - 캠페인 다시 짜기\n\n## 남은 질문\n- 데이터 이관?\n")})
    client.put(f"/api/meetings/{mid}/notes", json={"blocks": [
        {"type": "heading", "props": {"level": 2}, "content": [{"type": "text", "text": "배경", "styles": {}}]},
        {"type": "checkListItem", "props": {"checked": True}, "content": [{"type": "text", "text": "<script>alert(1)</script>", "styles": {}}]},
    ]})

    r = client.get(f"/api/meetings/{mid}/minutes.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    h = r.text
    assert "주간 &lt;회의&gt;" in h and "<strong>11월 18일</strong>" in h
    for title in ("결정 사항", "실행 항목", "주요 논의", "노트", "회의 중 메모·댓글", "남은 질문"):
        assert f'<span class="ttl">{title}</span>' in h, title
    assert '<span class="dt">일정</span>' in h and 'data-t="40.0"' in h and 'data-t="125.0"' in h  # 시각은 오른쪽 열로
    assert "박준호</span></td>" in h and "김민지" in h  # 담당자는 참석자로 알아본다
    assert "숫자 &lt;확인&gt;" in h and "여기 중요" in h
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in h and "<script>alert" not in h
    assert units[0]["text"] not in h and h.count(u["text"]) == 1  # 댓글 인용 말고는 받아 적은 문장이 없다
    assert f"/api/meetings/{mid}/audio" in h and 'class="apptools"' in h  # 앱에서 열면 전환·인쇄·받기 줄

    d = client.get(f"/api/meetings/{mid}/minutes.html?download=1")
    assert 'class="apptools"' not in d.text
    assert "attachment" in d.headers["content-disposition"] and "%ED%9A%8C%EC%9D%98%EB%A1%9D.html" in d.headers["content-disposition"]
    assert 'src="녹음.wav"' in d.text  # 받은 파일은 같은 폴더의 녹음 파일을 쓴다
    assert client.get("/api/meetings/nope/minutes.html").status_code == 404
    client.post(f"/api/meetings/{mid}/cancel")


def test_transcript_html_is_a_call_log_with_marks_in_place(client):
    """전사 기록: 화자별 발언이 이어지고, 메모는 그 순간에, 댓글은 번호와 그 문장 아래에, 고친 글자는 고친 대로."""
    mid = client.post("/api/meetings", json={"title": "통화 기록"}).json()["id"]
    minji = client.put(f"/api/meetings/{mid}/participants", json={"participants": [{"name": "김민지"}]}).json()["participants"][0]["id"]
    _record(client, mid, _speech_pcm([("s", 2.5), ("n", 1.0), ("s", 2.5), ("n", 1.0)]), messages=[{"type": "pause"}])
    units = client.get(f"/api/meetings/{mid}/live").json()["units"]
    u0, u1 = units[0], units[1]
    client.post(f"/api/meetings/{mid}/live/speaker", json={"segment_ids": [u0["id"]], "participant_id": minji})
    w0 = u0["text"].split(" ")[0]
    client.patch(f"/api/meetings/{mid}/segments/{u0['id']}", json={"text": "고친<말>" + u0["text"][len(w0):]})
    client.post(f"/api/meetings/{mid}/annotations", json={
        "kind": "comment", "body": "확인 <필요>",
        "start": {"unit": u1["id"], "offset": 0}, "end": {"unit": u1["id"], "offset": len(u1["text"])}})
    client.post(f"/api/meetings/{mid}/annotations", json={"kind": "memo", "t": u0["t1"] + 0.01, "after": u0["id"], "body": "여기서 결론"})

    r = client.get(f"/api/meetings/{mid}/transcript.html")
    assert r.status_code == 200
    h = r.text
    rest0 = u0["text"][len(w0):].strip()
    assert rest0[:6] in h and u1["text"][:6] in h  # 받아 적은 문장이 모두 나온다
    assert '<span class="name">김민지</span>' in h
    assert "고친&lt;말&gt;" in h and f">{w0} " not in h and "<del>" not in h  # 고친 글자만, 줄긋기 없이
    assert "고친 문장 <b>1</b>" in h
    body = h[h.index('<div class="transcript'):]
    assert body.index(f'id="u-{u0["id"]}"') < body.index("여기서 결론") < body.index(f'id="u-{u1["id"]}"')  # 메모는 그 순간에
    assert '<mark class="cm" data-c="1">' in h and 'id="c-1"' in h and "확인 &lt;필요&gt;" in h
    assert "메모·댓글 모아 보기" in h and 'class="apptools"' in h
    d = client.get(f"/api/meetings/{mid}/transcript.html?download=1")
    assert "%EC%A0%84%EC%82%AC%20%EA%B8%B0%EB%A1%9D.html" in d.headers["content-disposition"]  # "전사 기록.html"
    assert 'class="apptools"' not in d.text and 'src="녹음.wav"' in d.text
    client.post(f"/api/meetings/{mid}/cancel")


def test_participant_details_are_kept_carried_over_and_shown(client):
    """참석자 소속·직무·메모: 이름만 다시 보내도 지워지지 않고, 빈 값을 보내면 지워진다.
    다음 회의에 같은 이름을 넣으면 소속·직무를 이어받고(메모는 아님), 문서와 Markdown에도 나온다."""
    mid = client.post("/api/meetings", json={"title": "세부 정보"}).json()["id"]
    url = f"/api/meetings/{mid}/participants"
    parts = client.put(url, json={"participants": [
        {"name": "한지우", "org": " 전략기획팀 ", "role": "팀장", "note": "예산 <승인> 권한"}, {"name": "오세린"}]}).json()["participants"]
    assert parts[0] == {"id": parts[0]["id"], "name": "한지우", "org": "전략기획팀", "role": "팀장", "note": "예산 <승인> 권한"}
    assert parts[1] == {"id": parts[1]["id"], "name": "오세린"}
    # 이름만 다시 보내도 (예: 참여자 추가) 세부 정보는 그대로, 빈 값은 지운다
    parts = client.put(url, json={"participants": [{"name": "한지우"}, {"id": parts[1]["id"], "name": "오세린", "role": "디자이너"}, {"name": "류도윤"}]}).json()["participants"]
    assert parts[0]["org"] == "전략기획팀" and parts[1]["role"] == "디자이너" and "org" not in parts[2]
    parts = client.put(url, json={"participants": [dict(parts[0], note=""), parts[1], parts[2]]}).json()["participants"]
    assert "note" not in parts[0] and parts[0]["role"] == "팀장"

    md = client.get(f"/api/meetings/{mid}/export.md").text
    assert "  - 한지우 (전략기획팀 · 팀장)" in md and "  - 오세린 (디자이너)" in md
    client.put(url, json={"participants": [dict(parts[0], note="예산 <승인> 권한"), parts[1], parts[2]]})
    for doc in ("transcript", "minutes"):
        h = client.get(f"/api/meetings/{mid}/{doc}.html").text
        assert 'class="people-table"' in h and "<td>전략기획팀</td><td>팀장</td>" in h and "예산 &lt;승인&gt; 권한" in h, doc

    # 다음 회의: 같은 이름이면 소속·직무를 이어받는다 (메모는 회의마다)
    m2 = client.post("/api/meetings", json={}).json()["id"]
    p2 = client.put(f"/api/meetings/{m2}/participants", json={"participants": [{"name": "한지우"}]}).json()["participants"][0]
    assert p2["org"] == "전략기획팀" and p2["role"] == "팀장" and "note" not in p2
    sug = {s["name"]: s for s in client.get("/api/participants/suggest").json()}
    assert sug["한지우"]["org"] == "전략기획팀" and sug["오세린"]["role"] == "디자이너"


def test_marks_and_edits_stay_on_the_record_after_postprocess(client):
    """후처리는 원본 스크립트로만 남고, 회의 기록(직접 고친 글자, 회의 중·끝난 뒤 단 댓글)은 그대로다."""
    mid = client.post("/api/meetings", json={"title": "그대로"}).json()["id"]
    pcm = _speech_pcm([("s", 2.5), ("n", 1.5), ("s", 2.5), ("n", 1.5), ("s", 2.5), ("n", 1.5)])
    with client.websocket_connect(f"/api/meetings/{mid}/record") as ws:
        json.loads(ws.receive_text())
        ws.send_bytes(pcm)
        live = []
        while len(live) < 3:
            ev = json.loads(ws.receive_text())
            if ev["type"] == "segment":
                live.append(ev["segment"])
        # 회의 중: 댓글을 달고, 틀린 글자를 고친다
        u = live[0]
        c1 = client.post(f"/api/meetings/{mid}/annotations", json={"kind": "comment", "body": "회의 중 댓글",
                         "start": {"unit": u["id"], "offset": 0}, "end": {"unit": u["id"], "offset": len(u["text"].split(" ")[0])}}).json()
        client.patch(f"/api/meetings/{mid}/segments/{live[1]['id']}", json={"text": "회의 중에 고친 문장"})
        ws.send_text(json.dumps({"type": "stop"}))
        while json.loads(ws.receive_text())["type"] != "stopped":
            pass
    assert _wait_job(client, mid)["status"] == "done"
    # 끝난 뒤에도 같은 기록에 댓글
    u = live[2]
    c2 = client.post(f"/api/meetings/{mid}/annotations", json={"kind": "comment", "body": "끝난 뒤 댓글",
                     "start": {"unit": u["id"], "offset": 0}, "end": {"unit": u["id"], "offset": 2}}).json()
    view = client.get(f"/api/meetings/{mid}/live").json()
    assert [x["id"] for x in view["units"]] == [x["id"] for x in live] and view["units"][1]["text"] == "회의 중에 고친 문장"
    pos = {a["id"]: a["pos"] for a in view["annotations"]}
    assert pos[c1["id"]] == c1["pos"] and pos[c2["id"]] == c2["pos"]
    h = client.get(f"/api/meetings/{mid}/transcript.html").text
    assert "회의 중에 고친 문장" in h and "회의 중 댓글" in h and "끝난 뒤 댓글" in h and "회의 중 기록" in h
    script = client.get(f"/api/meetings/{mid}/script.md?download=1")
    assert script.status_code == 200 and "%EC%9B%90%EB%B3%B8%20%EC%8A%A4%ED%81%AC%EB%A6%BD%ED%8A%B8.md" in script.headers["content-disposition"]
    assert "회의 중에 고친 문장" not in script.text  # 원본 스크립트와 기록은 섞지 않는다

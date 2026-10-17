"""API 통합 테스트: 녹음 → 실시간 첨삭 → 원본 생성 → 첨삭 재배치 → 내보내기."""

import json
import os
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

    m = client.post("/api/meetings", json={"title": "주간 회의"}).json()
    mid = m["id"]
    pcm = _speech_pcm([("n", 0.5), ("s", 3.0), ("n", 1.0), ("s", 4.0), ("n", 1.0), ("s", 3.5), ("n", 1.2)])
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

    u0, u1 = units[0], units[1]
    w = u0["text"].split(" ")
    c = client.post(f"/api/meetings/{mid}/annotations", json={
        "kind": "comment", "view": "live", "body": "이 숫자 확인 필요",
        "start": {"unit": u0["id"], "offset": 0}, "end": {"unit": u0["id"], "offset": len(w[0]) + 1 + len(w[1])},
    }).json()
    assert c["pos"]["start"]["unit"] == u0["id"] and not c["pos"]["approx"]
    e_word = u1["text"].split(" ")[0]
    e = client.post(f"/api/meetings/{mid}/annotations", json={
        "kind": "edit", "view": "live", "replacement": e_word + "(수정)",
        "start": {"unit": u1["id"], "offset": 0}, "end": {"unit": u1["id"], "offset": len(e_word)},
    }).json()
    assert e["replacement"].endswith("(수정)")
    memo = client.post(f"/api/meetings/{mid}/annotations", json={"kind": "memo", "view": "live", "t": u0["t1"] + 0.01, "after": u0["id"], "body": "여기 중요"}).json()
    assert memo["pos"] == {"after": u0["id"]}
    r = client.post(f"/api/annotations/{c['id']}/replies", json={"text": "다음 회의에서 확인"}).json()
    assert r["replies"][0]["text"] == "다음 회의에서 확인"

    job = _wait_job(client, mid)
    assert job["status"] == "done", job
    fin = client.get(f"/api/meetings/{mid}/final").json()["final"]
    assert fin and fin["version"] == 1
    assert {u["speaker"] for u in fin["units"]} >= {"S1"}
    anns = {a["id"]: a for a in fin["annotations"]}
    assert anns[c["id"]]["pos"] is not None
    assert anns[memo["id"]]["pos"]["after"] is not None
    assert anns[e["id"]]["match"] == "differs"

    # 원본에서 수정 제안 '적용'
    p = client.patch(f"/api/annotations/{e['id']}?view=final", json={"status": "accepted"}).json()
    assert p["status"] == "accepted"

    md = client.get(f"/api/meetings/{mid}/export.md").text
    assert "[댓글 c1] 이 숫자 확인 필요" in md
    assert "[메모" in md and "여기 중요" in md
    assert "(수정)" in md
    assert "화자" in md

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
    fin = client.get(f"/api/meetings/{m['id']}/final").json()["final"]
    assert fin["units"]
    assert abs(client.get(f"/api/meetings/{m['id']}").json()["duration"] - 20.0) < 0.2


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
    assert s["break_prompt_minutes"] == 5
    client.post(f"/api/meetings/{mid}/cancel")


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



def test_mutations_return_positions_for_both_views(client):
    """화면이 전체 기록을 다시 받지 않도록, 바뀐 항목의 두 화면 위치를 함께 돌려준다."""
    mid = client.post("/api/meetings", json={}).json()["id"]
    pcm = _speech_pcm([("s", 2.5), ("n", 1.0), ("s", 3.0), ("n", 1.0)])
    _record(client, mid, pcm, messages=[{"type": "stop"}], until="stopped")
    assert _wait_job(client, mid)["status"] == "done"
    u = client.get(f"/api/meetings/{mid}/live").json()["units"][0]
    c = client.post(f"/api/meetings/{mid}/annotations", json={
        "kind": "comment", "view": "live", "body": "확인", "start": {"unit": u["id"], "offset": 0}, "end": {"unit": u["id"], "offset": 4},
    }).json()
    assert c["resolved"]["live"]["pos"]["start"]["unit"] == u["id"]
    assert c["resolved"]["final"]["pos"]["start"]["unit"].startswith("u")
    r = client.post(f"/api/annotations/{c['id']}/replies", json={"text": "네"}).json()
    assert r["replies"][0]["text"] == "네" and r["resolved"]["final"]["pos"] == c["resolved"]["final"]["pos"]
    ex = client.post(f"/api/meetings/{mid}/annotations/exported", json={"ids": [c["id"]]}).json()
    assert ex["updated"] == 1 and ex["exported_at"][c["id"]] > 0
    # 위치 캐시: 답글/내보내기 표시는 바뀌어도 같은 위치, 새 값은 그대로 보인다
    fin = client.get(f"/api/meetings/{mid}/final").json()["final"]["annotations"][0]
    assert fin["replies"][0]["text"] == "네" and fin["exported_at"] and fin["pos"] == c["resolved"]["final"]["pos"]


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


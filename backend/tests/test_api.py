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
    from fastapi.testclient import TestClient

    from scribe.main import app

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

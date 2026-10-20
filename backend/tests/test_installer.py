"""설치 도우미가 부르는 명령: 설정 파일 → 서버 설정, 모델 내려받기 진행률, 토큰 확인, Claude 연결, ffmpeg 없이 길이 읽기"""

import io
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

from scribe import catalog, installer

BACKEND = Path(__file__).resolve().parents[1]


def test_config_file_becomes_server_settings(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"data_dir": str(tmp_path / "회의"), "port": 9911, "hf_token": " hf_abc ",
                               "live": "qwen3-0.6b-8bit", "final": "qwen3-1.7b-fp16",
                               "env": {"SCRIBE_LIVE_SPK_SAME": 0.6, "SCRIBE_LIVE_MODEL": "덮어쓰지 않음", "PATH": "/무시"}}), encoding="utf-8")
    code = ("import os; from scribe.config import settings as s; from scribe.engines import model_for;"
            "print(s.data_dir, s.port, s.hf_token, model_for('live'), model_for('final'), os.environ['SCRIBE_LIVE_SPK_SAME'], os.environ['PATH'] != '/무시', sep='|')")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCRIBE_") and k != "HF_TOKEN"}
    env.update(SCRIBE_CONFIG=str(cfg), PYTHONPATH=str(BACKEND), SCRIBE_PORT="9922")  # 직접 준 환경 변수가 이긴다
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.strip().split("|")
    assert out[0] == str(tmp_path / "회의") and out[1] == "9922" and out[2] == "hf_abc"
    assert out[3] == "('qwen3', 'moona3k/mlx-qwen3-asr-0.6b-8bit')" and out[4] == "('qwen3', 'Qwen/Qwen3-ASR-1.7B')"
    assert out[5] == "0.6" and out[6] == "True"  # 세부 설정은 SCRIBE_만


def test_catalog_choices_sizes_and_recommendation():
    assert catalog.recommend(8.0, "macos") == {"live": "qwen3-0.6b-8bit", "final": "qwen3-0.6b-8bit"}
    assert catalog.recommend(18.0, "macos") == {"live": "qwen3-0.6b-8bit", "final": "qwen3-1.7b-8bit"}
    assert catalog.recommend(18.0, "windows") == {"live": "fw-small", "final": "fw-large-v3-turbo"}
    # 추천 모델은 그 기기에서 보이는 모델이어야 한다
    for os_name in ("macos", "windows", "linux"):
        rec = catalog.recommend(16.0, os_name)
        for role in ("live", "final"):
            assert catalog.for_os(catalog.choice(role, rec[role]), os_name), (os_name, role)
    repos = catalog.repos_for("qwen3-0.6b-8bit", "qwen3-0.6b-8bit")
    assert repos == ["moona3k/mlx-qwen3-asr-0.6b-8bit", "Qwen/Qwen3-ForcedAligner-0.6B"]  # 같은 모델은 한 번만
    assert catalog.total_gb(repos) == 2.51
    for role in ("live", "final"):
        for c in catalog.CATALOG[role]:
            assert all(r in catalog.CATALOG["sizes_gb"] for r in c["repos"]), c["id"]


def test_download_reports_progress_and_finishes(tmp_path, monkeypatch):
    from scribe.engines import download as dl

    monkeypatch.setattr(dl, "hub_cache", lambda: tmp_path)
    monkeypatch.setattr(installer, "download_targets", lambda token: (["moona3k/mlx-qwen3-asr-0.6b-8bit"], []))
    monkeypatch.setattr(dl, "MODEL_SIZES", {"moona3k/mlx-qwen3-asr-0.6b-8bit": 4000})

    def fake_snapshot(repo, token=None):
        blobs = tmp_path / ("models--" + repo.replace("/", "--")) / "blobs"
        blobs.mkdir(parents=True)
        for i in range(4):
            (blobs / f"b{i}").write_bytes(b"x" * 1000)
            import time
            time.sleep(0.05)

    events = []
    assert installer.download(events.append, snapshot=fake_snapshot, every=0.02) == 0
    assert events[0] == {"event": "start", "repos": ["moona3k/mlx-qwen3-asr-0.6b-8bit"], "total": 4000}
    prog = [e for e in events if e["event"] == "progress"]
    assert len(prog) >= 2 and prog[-1]["done"] == 4000
    assert events[-1] == {"event": "done", "ok": True, "failed": []}


def test_download_speaker_failure_is_not_fatal(tmp_path, monkeypatch):
    from scribe.engines import download as dl

    monkeypatch.setattr(dl, "hub_cache", lambda: tmp_path)
    monkeypatch.setattr(installer, "download_targets", lambda token: (["a/model"], ["pyannote/speaker-diarization-community-1"]))

    def fake_snapshot(repo, token=None):
        if repo.startswith("pyannote"):
            raise RuntimeError("401 gated")

    events = []
    assert installer.download(events.append, snapshot=fake_snapshot, every=10) == 0
    done = events[-1]
    assert done["ok"] is True and done["failed"][0]["required"] is False and "약관" in done["failed"][0]["hint"]


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(whoami_code=200, head_code=200):
    def open_(url, token, method="GET", timeout=15):
        code = whoami_code if "whoami" in url else head_code
        if code != 200:
            raise urllib.error.HTTPError(url, code, "x", {}, None)
        return _Resp(json.dumps({"name": "minji"}).encode())
    return open_


def test_check_hf_token_and_terms():
    assert installer.check_hf("")["message"] == "토큰이 비어 있어요"
    assert installer.check_hf("hf_x", _opener(401))["valid"] is False
    r = installer.check_hf("hf_x", _opener(200, 403))
    assert r["valid"] is True and r["access"] is False and "약관" in r["message"] and r["terms_url"].startswith("https://")
    r = installer.check_hf("hf_x", _opener())
    assert r["ok"] and r["user"] == "minji"


def test_claude_connect_keeps_other_servers(tmp_path, monkeypatch):
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}), encoding="utf-8")
    monkeypatch.setenv("SCRIBE_CONFIG", "/Users/me/Library/Application Support/MeetingScribe/config.json")
    assert installer.claude("status", path)["connected"] is False
    r = installer.claude("connect", path)
    assert r["connected"] and r["changed"]
    data = json.loads(path.read_text())
    assert data["theme"] == "dark" and data["mcpServers"]["other"] == {"command": "x"}
    entry = data["mcpServers"]["meeting-scribe"]
    assert entry["command"] == sys.executable and entry["args"] == ["-m", "scribe.mcp_server"]
    assert entry["env"]["SCRIBE_CONFIG"].endswith("config.json") and entry["env"]["PYTHONPATH"] == str(BACKEND)
    assert path.with_suffix(".json.bak").exists()
    assert installer.claude("connect", path)["changed"] is False  # 이미 같으면 그대로
    assert installer.claude("disconnect", path)["connected"] is False
    assert "meeting-scribe" not in json.loads(path.read_text())["mcpServers"]


def test_duration_without_ffprobe(tmp_path, monkeypatch):
    import numpy as np

    from scribe import audio

    wav = tmp_path / "a.wav"
    w = audio.WavAppender(wav)
    w.append((np.zeros(16000 * 3, dtype="<i2")).tobytes())
    w.close()
    real = audio.shutil.which
    monkeypatch.setattr(audio.shutil, "which", lambda name: None if name == "ffprobe" else real(name))
    assert abs(audio.probe_duration(wav) - 3.0) < 0.05


def test_doctor_and_catalog_commands_print_json(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(BACKEND), SCRIBE_DATA_DIR=str(tmp_path / "d"), HOME=str(tmp_path))
    out = subprocess.run([sys.executable, "-m", "scribe.installer", "doctor"], env=env, capture_output=True, text=True, check=True).stdout
    items = {i["id"]: i for i in json.loads(out)["items"]}
    assert items["python"]["ok"] and items["data"]["ok"] and items["claude"]["ok"] is None
    out = subprocess.run([sys.executable, "-m", "scribe.installer", "catalog"], env=env, capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    # 기기마다 추천이 다르다 (맥: Qwen3, 윈도우·리눅스: Whisper)
    assert d["machine"]["recommend"] == catalog.recommend(d["machine"]["ram_gb"]) and d["catalog"]["live"]

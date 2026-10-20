import time

from scribe import config
from scribe.engines import LiveEngineHolder, default_model, repos_for


def test_idle_live_model_is_unloaded_and_reloaded(monkeypatch):
    monkeypatch.setattr(config.settings, "live_engine", "mock")
    monkeypatch.setattr(config.settings, "live_idle_unload_min", 30.0)
    h = LiveEngineHolder()
    h.start_loading()
    assert h.wait(10) is not None and h.state == "ready"
    assert not h.unload_if_idle(busy=False)  # 방금 썼다
    h.last_used = time.time() - 31 * 60
    assert not h.unload_if_idle(busy=True)  # 녹음 중이면 내리지 않는다
    assert h.unload_if_idle(busy=False) and h.state == "idle" and h.engine is None
    h.start_loading()  # 다음 녹음/앱을 다시 볼 때 올린다
    assert h.wait(10) is not None and h.state == "ready"


def test_precision_picks_models(monkeypatch):
    monkeypatch.setattr(config.settings, "precision", "8bit")
    assert default_model("qwen3", "final").endswith("1.7b-8bit")
    monkeypatch.setattr(config.settings, "precision", "fp16")
    assert default_model("qwen3", "live") == "Qwen/Qwen3-ASR-0.6B"
    monkeypatch.setattr(config.settings, "final_engine", "qwen3")
    monkeypatch.setattr(config.settings, "final_model", "")
    assert repos_for("final") == ["Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B"]


def test_faster_whisper_models_are_downloaded_at_install(monkeypatch):
    """윈도우: 설치할 때 받아 두지 않으면 첫 녹음 때 진행률 없이 '모델 준비 중'으로 오래 머문다."""
    monkeypatch.setattr(config.settings, "live_engine", "faster-whisper")
    monkeypatch.setattr(config.settings, "live_model", "Systran/faster-whisper-small")
    assert repos_for("live") == ["Systran/faster-whisper-small"]
    monkeypatch.setattr(config.settings, "live_model", "small")  # 짧은 이름은 엔진이 받는다
    assert repos_for("live") == []


def test_selftest_runs_with_a_given_file(tmp_path, monkeypatch, capsys):
    """맥이 아니어도 점검 스크립트가 끝까지 돈다 (시연용 엔진, 직접 준 파일)."""
    import numpy as np

    from scribe import selftest
    from scribe.audio import WavAppender

    monkeypatch.setattr(config.settings, "live_engine", "mock")
    monkeypatch.setattr(config.settings, "final_engine", "mock")
    monkeypatch.setattr(config.settings, "diarizer", "mock")
    t = np.arange(16000 * 4) / 16000
    w = WavAppender(tmp_path / "a.wav")
    w.append((0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2").tobytes())
    w.close()
    assert selftest.main(["--audio", str(tmp_path / "a.wav"), "--text", "오늘 회의는"]) == 0
    out = capsys.readouterr().out
    assert "■ 환경" in out and "CER" in out and "화자 구분" in out
    assert selftest.cer("오늘 회의는", "오늘 회의는") == 0.0 and selftest.cer("가나다", "가나라") > 0


def test_downloaded_bytes_counts_both_cache_layouts(tmp_path, monkeypatch):
    """맥·리눅스(snapshots는 blobs를 가리키는 링크)와 윈도우(링크 없이 snapshots에 진짜 파일) 둘 다 받은 양을 센다."""
    from scribe.engines import download

    monkeypatch.setattr(download, "hub_cache", lambda: tmp_path)
    root = tmp_path / "models--a--linked"
    (root / "blobs").mkdir(parents=True)
    (root / "snapshots" / "rev").mkdir(parents=True)
    (root / "blobs" / "abc").write_bytes(b"x" * 100)
    (root / "snapshots" / "rev" / "model.bin").symlink_to(root / "blobs" / "abc")
    assert download.downloaded_bytes("a/linked") == 100  # 링크는 두 번 세지 않는다

    root = tmp_path / "models--a--copied"
    (root / "blobs").mkdir(parents=True)
    (root / "snapshots" / "rev" / "sub").mkdir(parents=True)
    (root / "blobs" / "part.incomplete").write_bytes(b"x" * 30)  # 받는 중
    (root / "snapshots" / "rev" / "model.bin").write_bytes(b"x" * 100)
    (root / "snapshots" / "rev" / "sub" / "vocab.json").write_bytes(b"x" * 5)
    assert download.downloaded_bytes("a/copied") == 135
    assert download.downloaded_bytes("a/missing") == 0

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

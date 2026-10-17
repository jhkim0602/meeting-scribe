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

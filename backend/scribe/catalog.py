"""받아쓰기 모델 목록 (models.json). 설치 도우미(launcher)와 서버가 같이 쓴다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

GB = 1024 ** 3
CATALOG: dict = json.loads(Path(__file__).with_name("models.json").read_text(encoding="utf-8"))


def choice(role: str, cid: Optional[str]) -> Optional[dict]:
    """role: 'live' | 'final'"""
    return next((c for c in CATALOG[role] if c["id"] == cid), None)


def sizes_bytes() -> dict[str, int]:
    return {repo: int(gb * GB) for repo, gb in CATALOG["sizes_gb"].items()}


def repos_for(live: Optional[str], final: Optional[str], speaker: bool = False) -> list[str]:
    """이 선택으로 내려받는 저장소들 (겹치는 것은 한 번)"""
    out: list[str] = []
    for role, cid in (("live", live), ("final", final)):
        for repo in (choice(role, cid) or {}).get("repos", []):
            if repo not in out:
                out.append(repo)
    if speaker:
        out += [r for r in CATALOG["speaker"]["repos"] if r not in out]
    return out


def total_gb(repos: list[str]) -> float:
    return round(sum(CATALOG["sizes_gb"].get(r, 0.0) for r in repos), 2)


def recommend(ram_gb: Optional[float]) -> dict:
    """메모리에 맞는 기본 선택 {live, final}"""
    for rule in CATALOG["recommend"]:
        if "max_ram_gb" not in rule or (ram_gb is not None and ram_gb <= rule["max_ram_gb"]):
            return {"live": rule["live"], "final": rule["final"]}
    return {"live": CATALOG["recommend"][-1]["live"], "final": CATALOG["recommend"][-1]["final"]}


def config_env(cfg: dict) -> dict[str, str]:
    """설치 도우미가 저장한 설정(config.json) → 서버 환경 변수"""
    env: dict[str, str] = {}
    if cfg.get("data_dir"):
        env["SCRIBE_DATA_DIR"] = str(cfg["data_dir"])
    if cfg.get("port"):
        env["SCRIBE_PORT"] = str(int(cfg["port"]))
    if cfg.get("hf_token"):
        env["HF_TOKEN"] = str(cfg["hf_token"]).strip()
    if cfg.get("diarizer"):
        env["SCRIBE_DIARIZER"] = str(cfg["diarizer"])
    for role, prefix in (("live", "SCRIBE_LIVE"), ("final", "SCRIBE_FINAL")):
        c = choice(role, cfg.get(role))
        if c:
            env[f"{prefix}_ENGINE"] = c["engine"]
            env[f"{prefix}_MODEL"] = c["model"]
    return env

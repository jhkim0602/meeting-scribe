"""받아 적기·원본 스크립트를 시작하기 전에 고를 것과 예상 시간.

예상 시간 = 녹음 길이 × 속도(녹음 1초를 처리하는 데 걸리는 초).
속도는 이 컴퓨터에서 같은 설정으로 끝낸 최근 작업이 있으면 그것(가장 정확), 없으면 models.json의 어림값(rtf)을 쓴다.
"""

from __future__ import annotations

import json
import statistics
from typing import Optional

from . import catalog
from .db import Store
from .engines import model_for, resolve_diarizer_kind
from .engines.download import downloaded_bytes


def _downloaded(repos: list[str]) -> bool:
    sizes = catalog.sizes_bytes()
    return all(downloaded_bytes(r) >= sizes.get(r, 0) * 0.97 for r in repos)


def measured_rates(store: Store) -> dict[tuple[str, bool], float]:
    """(모델 id, 화자 구분) → 이 컴퓨터에서 잰 속도 (최근 3개 작업의 가운데 값)"""
    with store.conn() as c:
        rows = c.execute(
            "SELECT j.options, j.started_at, j.finished_at, m.duration FROM jobs j JOIN meetings m ON m.id = j.meeting_id"
            " WHERE j.status='done' AND j.options IS NOT NULL AND j.started_at IS NOT NULL AND m.duration > 60"
            " ORDER BY j.finished_at DESC LIMIT 60"
        ).fetchall()
    seen: dict[tuple[str, bool], list[float]] = {}
    for r in rows:
        opts = json.loads(r["options"] or "{}")
        key = (opts.get("final"), opts.get("speakers", True))
        rate = (r["finished_at"] - r["started_at"]) / r["duration"]
        # 녹음 때문에 오래 멈춰 있던 작업은 빼고 센다 (실제 속도보다 훨씬 느리게 보이므로)
        if key[0] and rate < 1.5 and len(seen.setdefault(key, [])) < 3:
            seen[key].append(rate)
    return {k: statistics.median(v) for k, v in seen.items() if v}


def plan(store: Store, duration: float) -> dict:
    """고를 수 있는 모델(이 기기용), 기본값, 화자 구분을 쓸 수 있는지, 각각의 예상 시간(초)"""
    kind, model = model_for("final")
    current = next((c["id"] for c in catalog.CATALOG["final"] if c["engine"] == kind and c["model"] == model and catalog.for_os(c)), None)
    default = current or catalog.recommend(None)["final"]
    speaker_ok = resolve_diarizer_kind() != "none"
    speaker_rtf = catalog.CATALOG["speaker"].get("rtf", {}).get(catalog.this_os(), 0.15)
    rates = measured_rates(store)
    choices = []
    for c in catalog.CATALOG["final"]:
        if not catalog.for_os(c):
            continue
        rtf = c.get("rtf", 0.5)
        eta = {}
        for speakers in (True, False):
            measured = rates.get((c["id"], speakers))
            eta["with_speakers" if speakers else "without_speakers"] = round(duration * (measured or rtf + (speaker_rtf if speakers else 0)))
        choices.append({
            "id": c["id"], "label": c["label"], "tag": c["tag"], "detail": c["detail"], "memory_gb": c["memory_gb"],
            "download_gb": catalog.total_gb(c["repos"]), "downloaded": _downloaded(c["repos"]),
            "eta_s": eta, "measured": any((c["id"], s) in rates for s in (True, False)),
        })
    return {
        "duration": duration,
        "choices": choices,
        "default": default,
        "speakers": {"available": speaker_ok,
                     "reason": None if speaker_ok else "Hugging Face 토큰을 넣으면 쓸 수 있어요 (설치·설정 창 → 화자 구분)"},
    }


def validate(final: Optional[str]) -> Optional[str]:
    """이 기기에서 쓸 수 있는 모델이면 그대로, 아니면 오류 문구"""
    if final is None:
        return None
    c = catalog.choice("final", final)
    return None if c and catalog.for_os(c) else "이 기기에서 쓸 수 없는 모델이에요"

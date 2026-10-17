"""화자 목소리 기억: 원본의 화자에 이름을 붙이면 그 목소리 특징을 기억했다가,
다음 회의에서 비슷한 목소리의 화자에게 그 이름을 제안한다 (확정은 사용자가 한다).

목소리 특징은 화자 구분(pyannote)이 내는 화자별 벡터다. 회의별·화자별로 한 줄씩 저장하고,
회의를 지우면 그 회의에서 배운 목소리도 지운다.
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from .db import Store

# 이만큼 비슷하면 같은 사람일 수 있다고 제안한다 (코사인 유사도)
MATCH = float(os.environ.get("SCRIBE_VOICE_MATCH", "0.55"))


def _unit(v) -> Optional[np.ndarray]:
    a = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(a))
    return a / n if a.size and n > 0 else None


def learn_from_meeting(store: Store, meeting_id: str) -> int:
    """이 회의에서 이름 붙은 화자의 목소리를 기억한다 (이름을 지우면 잊는다). 기억한 수."""
    m = store.get_meeting(meeting_id) or {}
    final = store.get_final(meeting_id)
    embs = (final or {}).get("data", {}).get("speaker_embeddings") or {}
    names = m.get("speakers") or {}
    n = 0
    for label, vec in embs.items():
        name = (names.get(label) or "").strip()
        if name:
            store.set_voice_sample(meeting_id, label, name, vec, space="diar")
            n += 1
        else:
            store.delete_voice_sample(meeting_id, label)
    return n


def suggest(store: Store, meeting_id: str, embeddings: dict, names: dict) -> dict:
    """이름 없는 화자마다 가장 비슷한 기억된 목소리. {라벨: {name, score}}"""
    samples = [s for s in store.list_voice_samples() if s["meeting_id"] != meeting_id and s.get("space", "diar") == "diar"]
    if not samples or not embeddings:
        return {}
    # 같은 이름의 여러 회의 목소리는 평균을 낸다
    by_name: dict[str, list[np.ndarray]] = {}
    for s in samples:
        u = _unit(s["embedding"])
        if u is not None:
            by_name.setdefault(s["name"], []).append(u)
    centroids = {name: _unit(np.mean(vs, axis=0)) for name, vs in by_name.items()}
    taken = {v.strip() for v in names.values() if v and v.strip()}
    cands = []
    for label, vec in embeddings.items():
        if (names.get(label) or "").strip():
            continue
        u = _unit(vec)
        if u is None:
            continue
        for name, c in centroids.items():
            if c is None or name in taken or c.shape != u.shape:
                continue
            score = float(np.dot(u, c))
            if score >= MATCH:
                cands.append((score, label, name))
    # 점수가 높은 것부터: 한 화자에 한 이름, 한 이름은 한 화자에게만
    out: dict[str, dict] = {}
    used: set[str] = set()
    for score, label, name in sorted(cands, reverse=True):
        if label in out or name in used:
            continue
        out[label] = {"name": name, "score": round(score, 3)}
        used.add(name)
    return out

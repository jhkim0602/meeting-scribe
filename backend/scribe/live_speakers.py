"""실시간 임시 화자.

문장이 하나 확정될 때마다 목소리 특징을 뽑아 비슷한 목소리끼리 묶는다(화자 A, B …).
사용자가 어떤 문장에 참여자를 고르면 그 문장의 목소리 묶음 전체(지난 문장과 앞으로 올 문장)에
바로 반영한다. 참여자 목소리는 지난 회의에서 기억해 둔 것으로 시작할 수도 있다.
여기서 정한 화자가 곧 회의 기록의 화자다 (후처리 원본 스크립트의 화자 구분과 섞지 않는다).

목소리 특징을 뽑을 수 없는 환경(pyannote 없음)에서는 바로 앞 문장의 화자를 이어받는 것만 한다.
"""

from __future__ import annotations

import os
import secrets
import sys
import threading
import time
import traceback
from typing import Optional

import numpy as np

from . import notices
from .config import SAMPLE_RATE, settings
from .db import Store

# 이보다 짧은 문장은 목소리 특징이 불안정해 앞 문장의 화자를 이어받는다
MIN_EMBED_S = 1.0
INHERIT_GAP_S = 1.5
# 코사인 유사도: SAME 이상이면 같은 목소리, NEW 미만이면 새 목소리
SAME = float(os.environ.get("SCRIBE_LIVE_SPK_SAME", "0.5"))
NEW = float(os.environ.get("SCRIBE_LIVE_SPK_NEW", "0.35"))
MAX_CLUSTERS = 10
# 화자 모델을 GPU에서 CPU로 바꿀 때 화면에 알리는 문장
CPU_NOTICE = '화자 모델이 GPU(MPS)에서 오류가 나서 CPU로 바꿔서 계속할게요. 회의 중 화자 표시가 조금 늦어지고 CPU를 조금 더 써요. 녹음과 받아쓰기는 그대로예요.'
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _unit(v) -> Optional[np.ndarray]:
    a = np.asarray(v, dtype=float).reshape(-1)
    n = float(np.linalg.norm(a))
    return a / n if a.size and n > 0 else None


# ---------------- 목소리 특징 ----------------

class Embedder:
    space = "none"

    def embed(self, audio: np.ndarray, t0: float, t1: float) -> Optional[list]:
        raise NotImplementedError


class MockEmbedder(Embedder):
    """시연용: 시연용 화자 구분과 같은 시간표(A 9.5초, B 5초, A 7초, C 6초, B 8초 …)를 따른다."""

    space = "live:mock"
    PATTERN = [("A", 9.5), ("B", 5.0), ("A", 7.0), ("C", 6.0), ("B", 8.0)]
    BASIS = {"A": [1.0, 0.1, 0.0, 0.0], "B": [0.0, 1.0, 0.1, 0.0], "C": [0.1, 0.0, 1.0, 0.0]}

    def speaker_at(self, t: float) -> str:
        total = sum(d for _, d in self.PATTERN)
        t = t % total
        for sp, d in self.PATTERN:
            if t < d:
                return sp
            t -= d
        return "A"

    def embed(self, audio, t0, t1):
        base = np.asarray(self.BASIS[self.speaker_at((t0 + t1) / 2)])
        jitter = np.random.default_rng(int(t0 * 1000)).normal(0, 0.05, base.size)
        return (base + jitter).round(4).tolist()


class PyannoteEmbedder(Embedder):
    """pyannote의 화자 특징 모델(WeSpeaker ResNet34)로 문장 하나의 목소리 특징을 뽑는다."""

    space = "live:wespeaker"
    REPO = "pyannote/wespeaker-voxceleb-resnet34-LM"

    # 실패하면 이 간격(초)을 두 배씩 늘려 가며 조용히 다시 시도한다 (회의 기록은 그대로 이어진다)
    RETRY_MIN_S = 30.0
    RETRY_MAX_S = 300.0

    def __init__(self) -> None:
        self._inf = None
        self._lock = threading.Lock()
        self.error: Optional[str] = None
        self.failures = 0
        self._retry_at = 0.0
        self.cpu = False  # GPU(MPS)에서 오류가 나면 CPU로 바꾼다 (알리고)
        self._device = "cpu"

    def _load(self):
        import torch
        from pyannote.audio import Inference, Model

        token = settings.hf_token or os.environ.get("HF_TOKEN") or None
        try:
            model = Model.from_pretrained(self.REPO, token=token)
        except TypeError:  # pyannote.audio 3.x
            model = Model.from_pretrained(self.REPO, use_auth_token=token)
        inf = Inference(model, window="whole")
        self._device = "mps" if torch.backends.mps.is_available() and not self.cpu else "cpu"
        if self._device == "mps":
            inf.to(torch.device("mps"))
        else:
            # CPU뿐인 PC(윈도우): 같은 서버의 실시간 받아쓰기가 코어를 쓰도록 2개만 쓴다 (문장 하나라 충분하다)
            torch.set_num_threads(min(2, os.cpu_count() or 2))
        return inf

    def embed(self, audio, t0, t1):
        if self.error and time.monotonic() < self._retry_at:
            return None
        with self._lock:
            try:
                if self._inf is None:
                    self._inf = self._load()
                import torch

                v = self._inf({"waveform": torch.from_numpy(audio[None, :].astype(np.float32)), "sample_rate": SAMPLE_RATE})
            except Exception as exc:  # noqa: BLE001 - 목소리 특징 없이 진행(앞 문장 이어받기만), 나중에 다시 시도
                if self._device == "mps" and self._inf is not None:
                    # GPU에서 난 오류: 알리고 CPU로 바꿔 다음 문장부터 바로 다시 한다
                    _log(f"화자 특징 모델 GPU(MPS) 오류 ({t0:.1f}s 문장): {type(exc).__name__}: {exc} → CPU로 바꿈")
                    self.cpu, self._inf = True, None
                    notices.add(CPU_NOTICE, key="speaker-cpu")
                    return None
                self._fail(exc, t0, t1)
                return None
            if self.error:
                _log(f"화자 특징 모델 복구됨 (실패 {self.failures}회 후, {t0:.1f}s 문장)")
                self.error, self.failures = None, 0
        v = np.asarray(v, dtype=float).reshape(-1)
        return [round(float(x), 5) for x in v] if np.all(np.isfinite(v)) else None

    def _fail(self, exc: Exception, t0: float, t1: float) -> None:
        """실패를 로그에 남기고, 모델을 버린 뒤 다음 시도 시각을 정한다."""
        msg = f"{type(exc).__name__}: {exc}"
        self.failures += 1
        delay = min(self.RETRY_MIN_S * 2 ** (self.failures - 1), self.RETRY_MAX_S)
        self._retry_at = time.monotonic() + delay
        self._inf = None  # 다음 시도 때 모델을 새로 불러온다
        # 같은 에러가 반복되면 한 줄만, 처음 보는 에러면 traceback까지 남긴다
        tb = "" if msg == self.error else "\n" + traceback.format_exc().rstrip()
        _log(f"화자 특징 모델 실패 #{self.failures} ({t0:.1f}-{t1:.1f}s 문장): {msg} → {delay:.0f}초 뒤 다시 시도{tb}")
        self.error = msg


def _log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] live_speakers: {msg}", file=sys.stderr, flush=True)


_embedder: Optional[Embedder] = None
_embedder_ready = False


def get_embedder() -> Optional[Embedder]:
    """목소리 특징 모델. 끌 수 있고(SCRIBE_LIVE_SPEAKERS=0), 없으면 None."""
    global _embedder, _embedder_ready
    if _embedder_ready:
        return _embedder
    _embedder_ready = True
    if os.environ.get("SCRIBE_LIVE_SPEAKERS", "1") == "0":
        return None
    from .engines import _has, resolve_diarizer_kind

    kind = resolve_diarizer_kind()
    if kind == "mock":
        _embedder = MockEmbedder()
    elif _has("pyannote.audio") and kind != "none":
        _embedder = PyannoteEmbedder()
    return _embedder


# ---------------- 참여자 ----------------

PARTICIPANT_FIELDS = {"org": 60, "role": 60, "note": 500}  # 소속, 직무, 메모


def normalize_participants(items: list[dict], old: list[dict], known: Optional[dict[str, dict]] = None) -> list[dict]:
    """이름이 같은 참여자는 하나로, 기존 id는 유지, 새 참여자에는 id를 붙인다.
    소속·직무·메모는 보낸 값이 있으면 그 값, 보내지 않았으면 기존 값을 둔다.
    처음 넣는 사람은 지난 회의에서 적어 둔 소속·직무를 이어받는다 (known: {이름: {org, role}})."""
    by_id = {p["id"]: p for p in old}
    by_name = {p["name"]: p for p in old}
    out, seen = [], set()
    for it in items:
        name = (it.get("name") or "").strip()[:40]
        if not name or name in seen:
            continue
        seen.add(name)
        prev = (by_id.get(it["id"]) if it.get("id") else None) or by_name.get(name) or {}
        pid = it.get("id") or prev.get("id") or f"p{secrets.token_hex(3)}"
        person = {"id": pid, "name": name}
        for key, cap in PARTICIPANT_FIELDS.items():
            if key in it:
                value = str(it.get(key) or "").strip()[:cap]
            elif prev:
                value = prev.get(key) or ""
            else:
                value = ((known or {}).get(name) or {}).get(key, "") if key != "note" else ""
            if value:
                person[key] = value
        out.append(person)
    return out


# ---------------- 화자 추적 ----------------

class SpeakerTracker:
    """회의 하나의 임시 화자 상태. 녹음 세션과 화면(API)이 같은 것을 쓴다."""

    def __init__(self, store: Store, meeting_id: str, space: str):
        self.store = store
        self.meeting_id = meeting_id
        self.space = space
        self.lock = threading.RLock()
        self.reload()

    # 상태는 DB(문장별 화자, 목소리 특징, 대응표)에서 다시 만들 수 있다
    def reload(self) -> None:
        with self.lock:
            m = self.store.get_meeting(self.meeting_id) or {}
            self.participants = {p["id"]: p["name"] for p in m.get("participants") or []}
            self.speaker_map: dict[str, str] = {k: v for k, v in (m.get("speaker_map") or {}).items() if v in self.participants}
            self.rows = {r["id"]: r for r in self.store.list_live_speakers(self.meeting_id)}
            self.order = list(self.rows)
            self.embs = {k: _unit(v) for k, v in self.store.live_embeddings(self.meeting_id).items()}
            self.clusters: dict[str, list] = {}  # 묶음 → [합, 개수]
            self.profiles: dict[str, list] = {}  # 참여자 → [합, 개수]
            for sid, r in self.rows.items():
                u = self.embs.get(sid)
                if u is None:
                    continue
                if r.get("speaker_auto"):
                    self._add(self.clusters, r["speaker_auto"], u)
                if r.get("speaker_src") == "manual" and r.get("speaker") in self.participants:
                    self._add(self.profiles, r["speaker"], u)
            self._seed_from_memory()
            self.last_id = self.order[-1] if self.order else None

    def _seed_from_memory(self) -> None:
        """지난 회의에서 기억한 같은 이름의 목소리로 참여자 목소리를 시작한다."""
        names = {v: k for k, v in self.participants.items()}
        for s in self.store.list_voice_samples():
            if s.get("space") != self.space or s["meeting_id"] == self.meeting_id or s["name"] not in names:
                continue
            u = _unit(s["embedding"])
            if u is not None:
                self._add(self.profiles, names[s["name"]], u, weight=3)

    @staticmethod
    def _add(table: dict, key: str, u: np.ndarray, weight: int = 1) -> None:
        if key in table:
            if table[key][0].shape != u.shape:
                return
            table[key][0] = table[key][0] + u * weight
            table[key][1] += weight
        else:
            table[key] = [u * weight, weight]

    @staticmethod
    def _best(table: dict, u: np.ndarray) -> tuple[Optional[str], float]:
        best, score = None, -1.0
        for k, (total, _) in table.items():
            c = _unit(total)
            if c is None or c.shape != u.shape:
                continue
            sc = float(np.dot(u, c))
            if sc > score:
                best, score = k, sc
        return best, score

    def _new_letter(self) -> str:
        for ch in LETTERS:
            if ch not in self.clusters:
                return ch
        return "Z"

    @staticmethod
    def _out(r: dict) -> dict:
        return {k: r.get(k) for k in ("id", "speaker", "speaker_auto", "speaker_src", "speaker_conf")}

    def classify(self, seg: dict, embedding: Optional[list]) -> dict:
        """새 문장의 임시 화자를 정해 저장하고 돌려준다."""
        with self.lock:
            r = {"id": seg["id"], "t0": seg["t0"], "t1": seg["t1"], "speaker": None, "speaker_auto": None, "speaker_src": None, "speaker_conf": None}
            prev = self.rows.get(self.last_id) if self.last_id else None
            u = _unit(embedding) if embedding is not None else None
            if u is None:
                # 짧은 문장, 목소리 특징 없음: 곧바로 이어진 앞 문장의 화자를 이어받는다
                if prev and seg["t0"] - prev["t1"] < INHERIT_GAP_S and (prev.get("speaker") or prev.get("speaker_auto")):
                    r.update(speaker=prev.get("speaker"), speaker_auto=prev.get("speaker_auto"),
                             speaker_src="auto" if prev.get("speaker") else None, speaker_conf=round(float(prev.get("speaker_conf") or 0.5) * 0.9, 3))
            else:
                self.embs[seg["id"]] = u
                self.store.set_live_embedding(self.meeting_id, seg["id"], [round(float(x), 5) for x in u])
                limit = len(self.participants) + 1 if self.participants else MAX_CLUSTERS
                auto, sc = self._best(self.clusters, u)
                if auto is None or (sc < NEW and len(self.clusters) < limit):
                    auto, sc = self._new_letter(), 0.5
                self._add(self.clusters, auto, u)
                r["speaker_auto"] = auto
                r["speaker_conf"] = round(max(sc, 0.0), 3)
                pid, sp = self._best(self.profiles, u)
                if pid and sp >= SAME:
                    r.update(speaker=pid, speaker_src="auto", speaker_conf=round(sp, 3))
                elif self.speaker_map.get(auto):
                    r.update(speaker=self.speaker_map[auto], speaker_src="auto")
            self.rows[r["id"]] = r
            self.order.append(r["id"])
            self.last_id = r["id"]
            self.store.update_live_speakers([self._out(r)])
            return self._out(r)

    def assign(self, segment_ids: list[str], pid: Optional[str], scope: str = "voice") -> list[dict]:
        """사용자가 고른 화자. scope='voice'면 같은 목소리 묶음의 (직접 고르지 않은) 문장에도 반영한다."""
        with self.lock:
            if pid is not None and pid not in self.participants:
                raise KeyError(pid)
            if any(sid not in self.rows for sid in segment_ids):
                self.reload()  # 방금 생긴 문장
            changed: dict[str, dict] = {}
            autos = set()
            for sid in segment_ids:
                r = self.rows.get(sid)
                if r is None:
                    continue
                r.update(speaker=pid, speaker_src="manual" if pid else None, speaker_conf=1.0 if pid else None)
                changed[sid] = r
                u = self.embs.get(sid)
                if pid and u is not None:
                    self._add(self.profiles, pid, u)
                if r.get("speaker_auto"):
                    autos.add(r["speaker_auto"])
            if scope == "voice":
                for a in autos:
                    if pid:
                        self.speaker_map[a] = pid
                    else:
                        self.speaker_map.pop(a, None)
                    for sid, r in self.rows.items():
                        if r.get("speaker_auto") == a and r.get("speaker_src") != "manual":
                            r.update(speaker=pid, speaker_src="auto" if pid else None)
                            changed[sid] = r
                self.store.update_meeting(self.meeting_id, speaker_map=self.speaker_map)
            out = [self._out(r) for r in changed.values()]
            self.store.update_live_speakers(out)
            return out

    def snapshot_map(self) -> dict:
        with self.lock:
            return dict(self.speaker_map)

    def restore(self, rows: list[dict], speaker_map: Optional[dict]) -> list[dict]:
        """되돌리기: 화면이 기억해 둔 예전 화자와 목소리 대응표로 돌려놓는다."""
        with self.lock:
            out = []
            for prev in rows:
                r = self.rows.get(prev.get("id"))
                if r is None:
                    continue
                pid = prev.get("speaker")
                r.update(speaker=pid if pid in self.participants else None, speaker_src=prev.get("speaker_src") if pid in self.participants else None,
                         speaker_conf=prev.get("speaker_conf"))
                out.append(self._out(r))
            if speaker_map is not None:
                self.speaker_map = {k: v for k, v in speaker_map.items() if v in self.participants}
                self.store.update_meeting(self.meeting_id, speaker_map=self.speaker_map)
            self.store.update_live_speakers(out)
            return out

    def learn_voices(self) -> int:
        """참여자별 목소리를 기억해 다음 회의에서 처음부터 알아보게 한다."""
        with self.lock:
            n = 0
            for pid in list(self.participants):
                # 직접 고른 문장 + 직접 이어 준 목소리 묶음(같은 목소리 문장도 함께)의 문장
                confirmed = [
                    self.embs[s] for s, r in self.rows.items()
                    if r.get("speaker") == pid and self.embs.get(s) is not None
                    and (r.get("speaker_src") == "manual" or self.speaker_map.get(r.get("speaker_auto") or "") == pid)
                ]
                if len(confirmed) < 2:
                    continue
                c = _unit(np.mean(confirmed, axis=0))
                if c is not None:
                    self.store.set_voice_sample(self.meeting_id, f"P:{pid}", self.participants[pid], [round(float(x), 5) for x in c], space=self.space)
                    n += 1
            return n


_trackers: dict[str, SpeakerTracker] = {}
_reg_lock = threading.Lock()


def tracker_for(store: Store, meeting_id: str) -> SpeakerTracker:
    emb = get_embedder()
    with _reg_lock:
        t = _trackers.get(meeting_id)
        if t is None or t.store is not store:
            t = _trackers[meeting_id] = SpeakerTracker(store, meeting_id, emb.space if emb else "none")
        return t


def restart_embedder() -> None:
    """멈춘 화자 모델을 버리고 새로 만든다 (멈춘 스레드가 쥔 잠금을 다음 문장이 기다리지 않게)"""
    global _embedder
    if isinstance(_embedder, PyannoteEmbedder):
        cpu = _embedder.cpu
        _embedder = PyannoteEmbedder()
        _embedder.cpu = cpu



def status() -> dict:
    """설정 → 고급의 메모리 보기용: 화자 특징 모델이 올라와 있는지, 실패 횟수, 화자 정보를 들고 있는 회의 수"""
    e = _embedder
    return {"loaded": getattr(e, "_inf", None) is not None, "error": getattr(e, "error", None),
            "failures": getattr(e, "failures", 0), "meetings": len(_trackers)}


def forget_tracker(meeting_id: str) -> None:
    with _reg_lock:
        _trackers.pop(meeting_id, None)

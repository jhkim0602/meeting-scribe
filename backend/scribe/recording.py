"""녹음 WebSocket 세션: 브라우저가 보내는 16 kHz PCM을 파일에 저장하고 실시간 전사 결과를 돌려준다."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from fastapi import WebSocket, WebSocketDisconnect

from . import folders, notices
from .audio import WavAppender, pcm16_to_float, read_audio
from .config import settings
from .db import Store
from .engines import LiveEngineHolder
from .live import LiveEvent, LiveSegmenter
from .live_speakers import MIN_EMBED_S, get_embedder, restart_embedder, tracker_for
from .storage import M4A, WAV, restore_wav
from .system import disk_free_seconds, keep_awake, power_source

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-asr")
# 임시 화자(목소리 특징)는 받아쓰기를 늦추지 않게 따로, 문장 순서대로 처리한다
_spk_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-speaker")
# 문장 하나의 화자를 이보다 오래 못 정하면 화자 모델이 멈춘 것으로 보고 새로 시작한다
SPEAKER_TIMEOUT_S = float(os.environ.get("SCRIBE_SPEAKER_TIMEOUT_S", "30"))
HANG_NOTICE = ("화자 모델이 멈춰서 새로 시작했어요. 그사이 문장은 화자가 비어 있을 수 있어요 (문장마다 직접 고를 수 있어요). "
               "녹음과 받아쓰기는 그대로예요.")

# 이보다 짧게 멈춘 것은 구분선을 만들지 않는다 (실수로 누른 일시정지, 짧은 연결 끊김)
MIN_BREAK_S = float(os.environ.get("SCRIBE_MIN_BREAK_S", "20"))
BREAK_LABEL = "쉬는 시간"

# 저장 공간: 녹음 시간 30초마다 확인, 10분 분량 아래면 알리고 20초 분량 아래면 녹음을 멈춘다
DISK_CHECK_EVERY_S = 30.0
DISK_WARN_S = 600.0
DISK_STOP_S = 20.0
DISK_START_MIN_S = 60.0

# 배터리로 쓸 때는 진행 중 문장을 덜 자주 다시 받아 적는다 (초)
PARTIAL_EVERY_AC_S = 1.0
PARTIAL_EVERY_BATTERY_S = 2.0


def make_break_divider(store: Store, meeting_id: str, t: float, wall_from: float, wall_to: float) -> Optional[dict]:
    """녹음이 멈췄던 구간을 '쉬는 시간' 구분선으로 남긴다. 녹음 시간 t 지점(멈춘 곳)에 놓인다."""
    if wall_to - wall_from < MIN_BREAK_S:
        return None
    after = None
    for seg in store.list_live_segments(meeting_id):
        if seg["t0"] <= t:
            after = seg["id"]
        else:
            break
    return store.add_annotation(
        meeting_id,
        kind="divider",
        origin="live",
        origin_version=0,
        anchor={"t": round(float(t), 3), "after": after},
        body=BREAK_LABEL,
        meta={"break_start": wall_from, "break_end": wall_to, "auto": True},
    )


def mark_paused(store: Store, meeting_id: str, t: float, wall: float) -> None:
    """멈춘 시각을 기억한다. 이미 멈춘 상태면 처음 멈춘 시각을 유지한다."""
    m = store.get_meeting(meeting_id) or {}
    if not m.get("paused_wall"):
        store.update_meeting(meeting_id, paused_wall=wall, paused_t=t)


def close_break(store: Store, meeting_id: str, fallback_t: float) -> Optional[dict]:
    """다시 녹음을 시작할 때: 멈춰 있던 동안을 구분선으로 만들고 기록을 지운다."""
    m = store.get_meeting(meeting_id) or {}
    pw, pt = m.get("paused_wall"), m.get("paused_t")
    if not pw:
        return None
    store.update_meeting(meeting_id, paused_wall=None, paused_t=None)
    return make_break_divider(store, meeting_id, pt if pt is not None else fallback_t, pw, time.time())


def begin_session_break(store: Store, meeting_id: str, start_s: float, params: dict) -> Optional[dict]:
    """새 연결이 열렸을 때 쉬는 시간 처리.

    - continuous=1: 브라우저가 끊긴 동안의 소리를 모아 두었다가 보내는 재연결. 끊김은 쉬는 시간이 아니다.
    - paused=1: 브라우저가 아직 일시정지 상태. 구분선은 재개할 때 만든다.
    - since=<epoch>: 마지막으로 소리를 보낸 시각 (노트북 잠자기 등).
    """
    m = store.get_meeting(meeting_id) or {}
    client_paused = params.get("paused") == "1"
    if params.get("continuous") == "1":
        if not client_paused:
            store.update_meeting(meeting_id, paused_wall=None, paused_t=None)
        return None
    try:
        since = float(params["since"]) if params.get("since") else None
    except ValueError:
        since = None
    pw = m.get("paused_wall")
    if since is not None and (not pw or since < pw):
        store.update_meeting(meeting_id, paused_wall=since, paused_t=m.get("paused_t") if pw else start_s)
    if client_paused or start_s <= 0:
        return None
    return close_break(store, meeting_id, start_s)


class RecordingSession:
    def __init__(self, store: Store, holder: LiveEngineHolder, meeting_id: str, ws: WebSocket):
        self.store = store
        self.holder = holder
        self.meeting_id = meeting_id
        self.ws = ws
        self.writer = WavAppender(settings.meeting_dir(meeting_id) / "audio.wav")
        self.start_s = self.writer.duration
        context = store.get_setting("vocabulary", "") or ""
        self.seg = LiveSegmenter(holder.engine, language=settings.language, context=context, start_s=self.start_s)
        self._task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()
        self.closed = False
        self.disk_full = False
        self._next_disk_check = self.start_s + DISK_CHECK_EVERY_S
        self.tracker = tracker_for(store, meeting_id)
        self.embedder = get_embedder()
        self._labels: list[asyncio.Task] = []

    async def send(self, payload: dict) -> None:
        if self.closed:
            return
        async with self._send_lock:
            try:
                await asyncio.wait_for(self.ws.send_text(json.dumps(payload, ensure_ascii=False)), timeout=5)
            except Exception:  # noqa: BLE001 - 연결이 끊긴 경우
                self.closed = True

    def _engine_ready(self) -> bool:
        if self.seg.engine is None and self.holder.state == "ready":
            self.seg.engine = self.holder.engine
        return self.seg.engine is not None

    def on_audio(self, pcm: bytes) -> None:
        if self.disk_full:
            return
        try:
            self.writer.append(pcm)
        except OSError:
            self.disk_full = True
            return
        if self.writer.duration >= self._next_disk_check:
            self._next_disk_check = self.writer.duration + DISK_CHECK_EVERY_S
            free = disk_free_seconds(settings.data_dir)
            if free < DISK_STOP_S:
                self.disk_full = True
            elif free < DISK_WARN_S:
                asyncio.create_task(self.send({"type": "disk", "free_s": round(free)}))
        self.seg.feed(pcm16_to_float(pcm))
        if self._engine_ready() and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._step())

    async def _step(self, force: bool = False) -> None:
        self.seg.cfg.partial_every_s = PARTIAL_EVERY_BATTERY_S if power_source() == "battery" else PARTIAL_EVERY_AC_S
        loop = asyncio.get_running_loop()
        events: list[LiveEvent] = await loop.run_in_executor(_executor, lambda: self.seg.step(force=force))
        for ev in events:
            if ev.type == "segment":
                saved = self.store.add_live_segment(self.meeting_id, ev.t0, ev.t1, ev.text)
                await self.send({"type": "segment", "segment": {k: saved[k] for k in ("id", "idx", "t0", "t1", "text")}})
                self._labels = [t for t in self._labels if not t.done()]
                self._labels.append(asyncio.create_task(self._label(saved)))
            else:
                await self.send({"type": "partial", "t0": ev.t0, "t1": ev.t1, "text": ev.text})

    def _speaker_for(self, seg: dict) -> dict:
        emb = None
        if self.embedder is not None and seg["t1"] - seg["t0"] >= MIN_EMBED_S:
            audio = read_audio(self.writer.path, seg["t0"], seg["t1"])
            emb = self.embedder.embed(audio, seg["t0"], seg["t1"])
        return self.tracker.classify(seg, emb)

    async def _label(self, seg: dict) -> None:
        """문장의 임시 화자를 정해 알려 준다 (문장은 먼저 보내고 화자는 조금 뒤에)."""
        global _spk_executor
        pool = _spk_executor
        try:
            labels = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(pool, self._speaker_for, seg), SPEAKER_TIMEOUT_S)
        except asyncio.TimeoutError:
            # 멈춘 스레드는 그대로 두고(파이썬은 강제로 끝낼 수 없다) 새 작업자와 새 모델로 다음 문장부터 한다
            if pool is _spk_executor:
                pool.shutdown(wait=False, cancel_futures=True)
                _spk_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-speaker")
                restart_embedder()
                self.embedder = get_embedder()
                notices.add(HANG_NOTICE)
            return
        except Exception:  # noqa: BLE001 - 화자를 못 정해도 기록은 그대로 (로그에 남긴다)
            print(f"화자를 정하지 못했어요 ({seg['t0']:.1f}s 문장):\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            return
        await self.send({"type": "speaker", "segment": labels})

    async def flush(self) -> None:
        """남은 오디오를 모두 확정한다 (일시정지/종료 시)."""
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        if not self._engine_ready():
            # 엔진이 아직 준비 중이면 최대 10분까지 기다렸다가 처리
            await asyncio.get_running_loop().run_in_executor(None, lambda: self.holder.wait(600))
        if self._engine_ready():
            self._task = asyncio.create_task(self._step(force=True))
            await self._task
        if self._labels:
            await asyncio.gather(*self._labels, return_exceptions=True)

    def close(self) -> float:
        self.writer.close()
        return self.writer.duration


async def handle_recording(
    ws: WebSocket,
    store: Store,
    holder: LiveEngineHolder,
    sessions: dict,
    on_resume: Optional[Callable[[str], bool]] = None,
) -> None:
    meeting_id = ws.path_params["meeting_id"]
    await ws.accept()
    meeting = store.get_meeting(meeting_id)
    if meeting is None:
        await ws.send_text(json.dumps({"type": "error", "message": "회의를 찾을 수 없습니다"}))
        await ws.close()
        return
    # 방금 끊긴 이전 연결이 정리될 때까지 잠시 기다린다 (재연결/이어서 녹음)
    for _ in range(80):
        if meeting_id not in sessions:
            break
        await asyncio.sleep(0.1)
    if meeting_id in sessions:
        await ws.send_text(json.dumps({"type": "error", "message": "이미 다른 창에서 녹음 중입니다"}))
        await ws.close()
        return
    free_s = disk_free_seconds(settings.data_dir)
    if free_s < DISK_START_MIN_S:
        await ws.send_text(json.dumps({"type": "error", "message": "저장 공간이 거의 없어 녹음할 수 없어요. 공간을 비운 뒤 다시 시도해 주세요."}))
        await ws.close()
        return
    # 원본 스크립트를 만드는 중이거나 기다리는 중이면(예: 실수로 녹음 종료) 그 작업을 멈추고 이어서 녹음한다
    cancelled_job = bool(on_resume(meeting_id)) if on_resume else False
    holder.start_loading()
    mdir = settings.meeting_dir(meeting_id)
    if not (mdir / WAV).exists() and (mdir / M4A).exists():
        # 다 만든 뒤 줄여 둔 회의: 이어 붙이려고 먼저 WAV로 푼다 (5시간이면 수십 초)
        await ws.send_text(json.dumps({"type": "preparing", "message": "이전 녹음을 여는 중이에요"}))
        try:
            await asyncio.to_thread(restore_wav, mdir)
        except Exception as exc:  # noqa: BLE001
            await ws.send_text(json.dumps({"type": "error", "message": f"이전 녹음을 열지 못했어요: {exc}"}))
            await ws.close()
            return
    session = RecordingSession(store, holder, meeting_id, ws)
    sessions[meeting_id] = session
    keep_awake.acquire()
    store.update_meeting(meeting_id, status="recording")
    params = dict(ws.query_params)
    divider = begin_session_break(store, meeting_id, session.start_s, params)
    if params.get("continuous") != "1":
        # 끊김 없이 이어 보내는 재연결이 아니면, 여기서부터는 지금 시각에 녹음된 소리
        store.add_clock_point(meeting_id, session.start_s, time.time())
    await session.send({
        "type": "ready", "offset": session.start_s, "engine": holder.info(), "divider": divider,
        "cancelled_job": cancelled_job, "disk_free_s": round(free_s),
    })
    stop = False
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                session.closed = True  # 끊긴 연결로는 보내지 않고 저장만 한다
                break
            if msg.get("bytes"):
                session.on_audio(msg["bytes"])
                if session.disk_full:
                    # 여기까지 저장하고 녹음을 멈춘다. 공간을 비우면 이어서 녹음할 수 있다.
                    await session.send({"type": "disk-full", "duration": session.writer.duration})
                    break
            elif msg.get("text"):
                data = json.loads(msg["text"])
                kind = data.get("type")
                if kind == "pause":
                    await session.flush()
                    store.update_meeting(meeting_id, duration=session.writer.duration)
                    # from_t/from_wall: 오래 조용했던 경우 조용해진 시점부터 쉬는 시간으로 본다
                    t = data.get("from_t")
                    t = float(t) if isinstance(t, (int, float)) and 0 <= t <= session.writer.duration else session.writer.duration
                    wall = data.get("from_wall")
                    wall = float(wall) if isinstance(wall, (int, float)) and wall <= time.time() else time.time()
                    mark_paused(store, meeting_id, t, wall)
                    await session.send({"type": "paused", "duration": session.writer.duration})
                elif kind == "resume":
                    divider = close_break(store, meeting_id, session.writer.duration)
                    store.add_clock_point(meeting_id, session.writer.duration, time.time())
                    await session.send({"type": "resumed", "divider": divider})
                elif kind == "stop":
                    await session.flush()
                    stop = True
                    break
                elif kind == "ping":
                    await session.send({"type": "pong", "engine": holder.info()})
    except WebSocketDisconnect:
        session.closed = True
    finally:
        try:
            if not stop:
                # 취소되더라도 남은 오디오 처리는 백그라운드에서 끝까지 진행한다
                await asyncio.shield(session.flush())
        except BaseException:  # noqa: BLE001 - 취소/연결 끊김
            pass
        finally:
            duration = session.close()
            sessions.pop(meeting_id, None)
            keep_awake.release()
            holder.mark_used()
            try:
                session.tracker.learn_voices()  # 참여자 목소리를 기억해 다음 회의에서 알아본다
            except Exception:  # noqa: BLE001 - 기억하지 못해도 기록은 그대로 (로그에 남긴다)
                print(f"참여자 목소리를 기억하지 못했어요:\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            store.update_meeting(meeting_id, duration=duration, status="recorded")
            # 녹음이 끝나거나 끊긴 시각 → 다시 이어서 녹음하면 그 사이를 쉬는 시간으로 표시
            mark_paused(store, meeting_id, duration, time.time())
            # 녹음 중에 제목을 바꿨으면 이제 폴더 이름도 '날짜_제목'으로 (녹음 파일을 닫은 뒤라 바꿀 수 있다)
            latest = store.latest_job(meeting_id)
            if not (latest and latest["status"] in ("queued", "running")):
                folders.sync(store, meeting_id)
            if stop:
                try:
                    await session.send({"type": "stopped", "duration": duration})
                    await ws.close()
                except BaseException:  # noqa: BLE001
                    pass
            elif session.disk_full and not session.closed:
                try:
                    await ws.close()
                except BaseException:  # noqa: BLE001
                    pass

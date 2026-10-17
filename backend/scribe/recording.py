"""녹음 WebSocket 세션: 브라우저가 보내는 16 kHz PCM을 파일에 저장하고 실시간 전사 결과를 돌려준다."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from .audio import WavAppender, pcm16_to_float
from .config import settings
from .db import Store
from .engines import LiveEngineHolder
from .live import LiveEvent, LiveSegmenter

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-asr")


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
        self.writer.append(pcm)
        self.seg.feed(pcm16_to_float(pcm))
        if self._engine_ready() and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._step())

    async def _step(self, force: bool = False) -> None:
        loop = asyncio.get_running_loop()
        events: list[LiveEvent] = await loop.run_in_executor(_executor, lambda: self.seg.step(force=force))
        for ev in events:
            if ev.type == "segment":
                saved = self.store.add_live_segment(self.meeting_id, ev.t0, ev.t1, ev.text)
                await self.send({"type": "segment", "segment": {k: saved[k] for k in ("id", "idx", "t0", "t1", "text")}})
            else:
                await self.send({"type": "partial", "t0": ev.t0, "t1": ev.t1, "text": ev.text})

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

    def close(self) -> float:
        self.writer.close()
        return self.writer.duration


async def handle_recording(ws: WebSocket, store: Store, holder: LiveEngineHolder, sessions: dict, on_stop) -> None:
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
    if meeting["status"] == "processing":
        await ws.send_text(json.dumps({"type": "error", "message": "원본을 만드는 중에는 녹음할 수 없습니다"}))
        await ws.close()
        return
    holder.start_loading()
    session = RecordingSession(store, holder, meeting_id, ws)
    sessions[meeting_id] = session
    store.update_meeting(meeting_id, status="recording")
    await session.send({"type": "ready", "offset": session.start_s, "engine": holder.info()})
    stop = False
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                session.closed = True  # 끊긴 연결로는 보내지 않고 저장만 한다
                break
            if msg.get("bytes"):
                session.on_audio(msg["bytes"])
            elif msg.get("text"):
                data = json.loads(msg["text"])
                kind = data.get("type")
                if kind == "pause":
                    await session.flush()
                    store.update_meeting(meeting_id, duration=session.writer.duration)
                    await session.send({"type": "paused", "duration": session.writer.duration})
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
            store.update_meeting(meeting_id, duration=duration, status="recorded")
            if stop:
                job = on_stop(meeting_id)
                try:
                    await session.send({"type": "stopped", "duration": duration, "job": job})
                    await ws.close()
                except BaseException:  # noqa: BLE001
                    pass

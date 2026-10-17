"""FastAPI 앱: REST API + 녹음 WebSocket + 프론트엔드 정적 파일."""

from __future__ import annotations

import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .anchors import anchor_from_request, resolve_annotations
from .audio import ffmpeg_path, wav_info
from .config import settings
from .db import Store
from .engines import LiveEngineHolder, engines_info
from .export import meeting_markdown
from .jobs import JobManager
from .notes_md import blocks_to_markdown
from .recording import handle_recording
from .search import search_all
from .views import Views

settings.ensure_dirs()
store = Store(settings.db_path)
views = Views(store)
jobs = JobManager(store)
holder = LiveEngineHolder()
sessions: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버가 꺼질 때 녹음 중이던 회의 상태 정리
    for m in store.list_meetings():
        if m["status"] == "recording":
            store.update_meeting(m["id"], status="recorded")
    jobs.start()
    if os.environ.get("SCRIBE_PRELOAD", "1") == "1":
        holder.start_loading()
    yield


app = FastAPI(title="Meeting Scribe", version=__version__, lifespan=lifespan)


# ---------------- 공통 ----------------

def _meeting_or_404(meeting_id: str) -> dict:
    m = store.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(404, "회의를 찾을 수 없습니다")
    return m


def _audio_path(meeting_id: str) -> Path:
    return settings.meetings_dir / meeting_id / "audio.wav"


def _meeting_detail(m: dict) -> dict:
    out = dict(m)
    job = store.latest_job(m["id"])
    out["job"] = job
    out["has_audio"] = _audio_path(m["id"]).exists()
    out["recording"] = m["id"] in sessions
    out["has_summary"] = bool(m.get("summary_md"))
    return out


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "live": holder.info(),
        **engines_info(),
        "ffmpeg": bool(ffmpeg_path()),
        "data_dir": str(settings.data_dir),
        "project_root": str(Path(__file__).resolve().parents[2]),
        "language": settings.language,
    }


class SettingsIn(BaseModel):
    vocabulary: Optional[str] = None
    auto_process: Optional[bool] = None
    break_prompt_minutes: Optional[float] = None


@app.get("/api/settings")
def get_settings() -> dict:
    return {
        "vocabulary": store.get_setting("vocabulary", ""),
        "auto_process": store.get_setting("auto_process", True),
        # 이만큼 조용하면 '쉬는 시간인가요?'를 묻는다 (0이면 묻지 않음)
        "break_prompt_minutes": store.get_setting("break_prompt_minutes", 3),
    }


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict:
    if body.vocabulary is not None:
        store.set_setting("vocabulary", body.vocabulary.strip())
    if body.auto_process is not None:
        store.set_setting("auto_process", bool(body.auto_process))
    if body.break_prompt_minutes is not None:
        store.set_setting("break_prompt_minutes", max(0.0, min(60.0, float(body.break_prompt_minutes))))
    return get_settings()


# ---------------- 회의 ----------------

class MeetingIn(BaseModel):
    title: Optional[str] = None


class MeetingPatch(BaseModel):
    title: Optional[str] = None
    speakers: Optional[dict[str, str]] = None
    num_speakers: Optional[int] = None


@app.get("/api/meetings")
def list_meetings(q: Optional[str] = None) -> list[dict]:
    out = []
    for m in store.list_meetings(q):
        item = {k: m[k] for k in ("id", "title", "created_at", "updated_at", "status", "source", "duration")}
        item["recording"] = m["id"] in sessions
        if m["status"] == "processing":
            job = store.latest_job(m["id"])
            item["progress"] = job["progress"] if job else None
        out.append(item)
    return out


@app.post("/api/meetings")
def create_meeting(body: MeetingIn) -> dict:
    m = store.create_meeting((body.title or "").strip())
    return _meeting_detail(m)


@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: str) -> dict:
    return _meeting_detail(_meeting_or_404(meeting_id))


@app.patch("/api/meetings/{meeting_id}")
def patch_meeting(meeting_id: str, body: MeetingPatch) -> dict:
    _meeting_or_404(meeting_id)
    fields: dict[str, Any] = {}
    if body.title is not None:
        fields["title"] = body.title.strip()
    if body.speakers is not None:
        fields["speakers"] = body.speakers
    if body.num_speakers is not None:
        fields["num_speakers"] = body.num_speakers or None
    return _meeting_detail(store.update_meeting(meeting_id, **fields))  # type: ignore[arg-type]


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: str) -> dict:
    _meeting_or_404(meeting_id)
    if meeting_id in sessions:
        raise HTTPException(409, "녹음 중인 회의는 삭제할 수 없습니다")
    jobs.cancel(meeting_id)
    store.delete_meeting(meeting_id)
    shutil.rmtree(settings.meetings_dir / meeting_id, ignore_errors=True)
    return {"ok": True}


SAFE_EXT = re.compile(r"^\.[A-Za-z0-9]{1,6}$")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), title: str = Form("")) -> dict:
    name = file.filename or "audio"
    ext = Path(name).suffix.lower()
    if not SAFE_EXT.match(ext or ""):
        ext = ".bin"
    m = store.create_meeting(title.strip() or Path(name).stem, source="upload", original_name=name)
    dst = settings.meeting_dir(m["id"]) / f"source{ext}"
    with open(dst, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    store.update_meeting(m["id"], status="recorded")
    jobs.enqueue(m["id"])
    return _meeting_detail(store.get_meeting(m["id"]))  # type: ignore[arg-type]


# ---------------- 기록 ----------------

@app.get("/api/meetings/{meeting_id}/live")
def live_view(meeting_id: str) -> dict:
    _meeting_or_404(meeting_id)
    return views.live_view(meeting_id)


@app.get("/api/meetings/{meeting_id}/final")
def final_view(meeting_id: str) -> dict:
    _meeting_or_404(meeting_id)
    return {"final": views.final_view(meeting_id), "job": store.latest_job(meeting_id)}


@app.post("/api/meetings/{meeting_id}/process")
def process(meeting_id: str) -> dict:
    m = _meeting_or_404(meeting_id)
    if meeting_id in sessions:
        raise HTTPException(409, "녹음을 먼저 종료해 주세요")
    mdir = settings.meetings_dir / meeting_id
    if not _audio_path(meeting_id).exists() and not any(mdir.glob("source.*")):
        raise HTTPException(400, "처리할 오디오가 없습니다")
    return {"job": jobs.enqueue(m["id"])}


@app.post("/api/meetings/{meeting_id}/cancel")
def cancel(meeting_id: str) -> dict:
    _meeting_or_404(meeting_id)
    jobs.cancel(meeting_id)
    return {"ok": True}


@app.get("/api/meetings/{meeting_id}/job")
def job(meeting_id: str) -> dict:
    _meeting_or_404(meeting_id)
    return {"job": store.latest_job(meeting_id)}


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


@app.get("/api/meetings/{meeting_id}/audio")
def audio(meeting_id: str, request: Request) -> Response:
    path = _audio_path(meeting_id)
    if not path.exists():
        raise HTTPException(404, "오디오가 아직 없습니다")
    size = path.stat().st_size
    rng = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    if not rng:
        return FileResponse(path, media_type="audio/wav", headers=headers)
    m = RANGE_RE.match(rng)
    if not m:
        raise HTTPException(416, "잘못된 Range")
    start = int(m.group(1)) if m.group(1) else 0
    end = int(m.group(2)) if m.group(2) else size - 1
    if not m.group(1) and m.group(2):  # 끝에서부터 N바이트
        start, end = max(0, size - int(m.group(2))), size - 1
    end = min(end, size - 1)
    if start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    length = end - start + 1

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 16, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(length)})
    return StreamingResponse(body(), status_code=206, media_type="audio/wav", headers=headers)


# ---------------- 노트 / 요약 ----------------

class NotesIn(BaseModel):
    blocks: list[dict]


@app.get("/api/meetings/{meeting_id}/notes")
def get_notes(meeting_id: str) -> dict:
    m = store.get_meeting(meeting_id, with_notes=True)
    if m is None:
        raise HTTPException(404, "회의를 찾을 수 없습니다")
    return {"blocks": m.get("notes"), "markdown": m.get("notes_md") or "", "updated_at": m.get("notes_updated_at")}


@app.put("/api/meetings/{meeting_id}/notes")
def put_notes(meeting_id: str, body: NotesIn) -> dict:
    import json

    _meeting_or_404(meeting_id)
    md = blocks_to_markdown(body.blocks)
    now = time.time()
    store.update_meeting(meeting_id, notes_json=json.dumps(body.blocks, ensure_ascii=False), notes_md=md, notes_updated_at=now)
    return {"ok": True, "updated_at": now}


class SummaryIn(BaseModel):
    markdown: str


@app.get("/api/meetings/{meeting_id}/summary")
def get_summary(meeting_id: str) -> dict:
    m = _meeting_or_404(meeting_id)
    return {"markdown": m.get("summary_md") or "", "updated_at": m.get("summary_updated_at")}


@app.put("/api/meetings/{meeting_id}/summary")
def put_summary(meeting_id: str, body: SummaryIn) -> dict:
    _meeting_or_404(meeting_id)
    store.update_meeting(meeting_id, summary_md=body.markdown, summary_updated_at=time.time())
    return get_summary(meeting_id)


# ---------------- 첨삭(메모/댓글/수정/강조) ----------------

class AnnotationIn(BaseModel):
    kind: str  # comment | edit | memo | highlight
    view: str = "live"  # live | final
    start: Optional[dict] = None
    end: Optional[dict] = None
    t: Optional[float] = None
    after: Optional[str] = None
    body: str = ""
    replacement: Optional[str] = None
    color: Optional[str] = None


class AnnotationPatch(BaseModel):
    body: Optional[str] = None
    replacement: Optional[str] = None
    status: Optional[str] = None
    color: Optional[str] = None


class ReplyIn(BaseModel):
    text: str


class ExportedIn(BaseModel):
    ids: list[str]
    exported: bool = True


KINDS = {"comment", "edit", "memo", "highlight", "divider"}


def _resolved_one(ann: dict, view: str) -> dict:
    idx, version = views.index_for(ann["meeting_id"], view)
    if idx is None:
        return dict(ann, pos=None)
    return resolve_annotations([ann], idx, view, version)[0]


@app.post("/api/meetings/{meeting_id}/annotations")
def add_annotation(meeting_id: str, body: AnnotationIn) -> dict:
    _meeting_or_404(meeting_id)
    if body.kind not in KINDS:
        raise HTTPException(400, "알 수 없는 종류")
    view = "final" if body.view == "final" else "live"
    idx, version = views.index_for(meeting_id, view)
    if idx is None:
        raise HTTPException(400, "원본이 아직 없습니다")
    payload = body.model_dump()
    if body.kind in ("memo", "divider"):
        if body.t is None:
            raise HTTPException(400, "시간이 필요합니다")
    elif not (body.start and body.end):
        raise HTTPException(400, "선택 범위가 필요합니다")
    anchor = anchor_from_request(idx, payload)
    if anchor is None:
        raise HTTPException(400, "선택한 범위를 찾을 수 없습니다")
    if body.kind == "edit" and body.replacement is None:
        raise HTTPException(400, "수정할 내용이 필요합니다")
    ann = store.add_annotation(
        meeting_id,
        kind=body.kind,
        origin=view,
        origin_version=version,
        anchor=anchor,
        body=body.body,
        replacement=body.replacement,
        color=body.color,
        status="open",
    )
    return _resolved_one(ann, view)


def _ann_or_404(ann_id: str) -> dict:
    ann = store.get_annotation(ann_id)
    if ann is None:
        raise HTTPException(404, "항목을 찾을 수 없습니다")
    return ann


@app.patch("/api/annotations/{ann_id}")
def patch_annotation(ann_id: str, body: AnnotationPatch, view: str = "live") -> dict:
    _ann_or_404(ann_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    ann = store.update_annotation(ann_id, **fields)
    return _resolved_one(ann, view)  # type: ignore[arg-type]


@app.post("/api/annotations/{ann_id}/replies")
def add_reply(ann_id: str, body: ReplyIn, view: str = "live") -> dict:
    ann = _ann_or_404(ann_id)
    replies = list(ann.get("replies") or [])
    replies.append({"text": body.text.strip(), "at": time.time()})
    ann = store.update_annotation(ann_id, replies=replies)
    return _resolved_one(ann, view)  # type: ignore[arg-type]


@app.post("/api/meetings/{meeting_id}/annotations/exported")
def mark_exported(meeting_id: str, body: ExportedIn) -> dict:
    """댓글/메모를 노트로 내보냈다고 표시한다."""
    _meeting_or_404(meeting_id)
    return {"updated": store.mark_exported(meeting_id, body.ids, body.exported)}


@app.delete("/api/annotations/{ann_id}")
def delete_annotation(ann_id: str) -> dict:
    _ann_or_404(ann_id)
    store.delete_annotation(ann_id)
    return {"ok": True}


# ---------------- 내보내기 / 검색 ----------------

@app.get("/api/meetings/{meeting_id}/export.md")
def export_md(meeting_id: str, version: str = "auto", download: int = 0) -> Response:
    md = meeting_markdown(views, meeting_id, version=version, include_summary=True)
    if md is None:
        raise HTTPException(404, "회의를 찾을 수 없습니다")
    headers = {}
    if download:
        from urllib.parse import quote

        title = (store.get_meeting(meeting_id) or {}).get("title") or "meeting"
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(title)}.md"
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8", headers=headers)


@app.get("/api/search")
def search(q: str, limit: int = 30) -> list[dict]:
    return search_all(views, q, limit=limit)


# ---------------- 녹음 WebSocket ----------------

def _after_stop(meeting_id: str) -> Optional[dict]:
    if store.get_setting("auto_process", True):
        return jobs.enqueue(meeting_id)
    return None


def _before_resume(meeting_id: str) -> bool:
    """원본을 만드는 중에 다시 녹음하면 그 작업을 취소한다. 취소했으면 True."""
    latest = store.latest_job(meeting_id)
    if latest and latest["status"] in ("queued", "running"):
        jobs.cancel(meeting_id)
        return True
    return False


@app.websocket("/api/meetings/{meeting_id}/record")
async def record(ws: WebSocket) -> None:
    await handle_recording(ws, store, holder, sessions, _after_stop, _before_resume)


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


# ---------------- 프론트엔드 ----------------

if settings.frontend_dist.exists():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
else:
    @app.get("/")
    def _no_frontend() -> PlainTextResponse:
        return PlainTextResponse("프론트엔드 빌드가 없습니다. frontend 폴더에서 'npm install && npm run build'를 실행해 주세요.")

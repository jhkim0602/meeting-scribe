"""FastAPI 앱: REST API + 녹음 WebSocket + 프론트엔드 정적 파일."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, catalog
from .anchors import anchor_from_request
from .audio import ffmpeg_path
from .config import settings
from .db import Store
from .engines import LiveEngineHolder, engines_info, model_for
from .export import meeting_markdown, script_markdown
from .jobs import JobManager
from .notes_md import blocks_to_markdown
from .record import EditError, apply_legacy_edits, edit_segment
from .recording import handle_recording
from .search import search_all
from .security import LocalOnlyMiddleware
from . import folders, notices
from .safety import backup_daily, check_and_recover, sync_folder_warning
from .storage import audio_file, media_type
from .system import KeepAwake, disk_free_bytes, disk_free_seconds, power_source
from .views import Views
from .live_speakers import forget_tracker, normalize_participants, tracker_for

settings.ensure_dirs()
# 켤 때 DB 점검: 깨졌으면 깨진 파일은 옆에 두고 최근 사본으로 되돌린다
_recovered = check_and_recover(settings.db_path)
store = Store(settings.db_path)
views = Views(store)
jobs = JobManager(store)
jobs.is_recording = lambda mid: mid in sessions
jobs.recording_any = lambda: bool(sessions)
def _sync_folder(meeting_id: str) -> None:
    """회의 폴더 이름을 '날짜_제목'에 맞춘다. 녹음·원본 스크립트 작업 중이면 파일을 쓰고 있으니 끝난 뒤에."""
    latest = store.latest_job(meeting_id)
    if meeting_id not in sessions and not (latest and latest["status"] in ("queued", "running")):
        folders.sync(store, meeting_id)


def _on_job_end(meeting_id: str) -> None:
    forget_tracker(meeting_id)  # 올린 파일: 워커가 정한 화자 묶음을 새로 읽는다
    _sync_folder(meeting_id)  # 작업 중에 제목을 바꿨으면 이제 폴더 이름도


jobs.on_job_end = _on_job_end
holder = LiveEngineHolder()
sessions: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버가 꺼질 때 녹음 중이던 회의 상태 정리
    for m in store.list_meetings():
        if m["status"] == "recording":
            store.update_meeting(m["id"], status="recorded")
    apply_legacy_edits(store, views)  # 예전 "수정 제안"을 직접 고친 글자로 (한 번만)
    if _recovered:
        notices.add(_recovered, level="error", key="db-recovered")
    if warning := sync_folder_warning(settings.data_dir):
        notices.add(warning, key="sync-folder")
    backup_daily(settings.db_path)
    folders.migrate_all(store, busy=set())  # 예전 id 폴더를 '날짜_제목'으로 (켤 때는 녹음·작업이 없다)
    jobs.start()
    if os.environ.get("SCRIBE_PRELOAD", "1") == "1":
        holder.start_loading()
    idle = asyncio.get_event_loop().create_task(_unload_idle_model())
    yield
    idle.cancel()


async def _unload_idle_model() -> None:
    """녹음이 없는 채로 오래(기본 30분) 지나면 실시간 모델을 메모리에서 내린다. 하루 한 번 DB 사본도 (녹음 중이 아닐 때)."""
    while True:
        await asyncio.sleep(60)
        holder.unload_if_idle(busy=bool(sessions))
        if not sessions:
            await asyncio.to_thread(backup_daily, settings.db_path)


app = FastAPI(title="Meeting Scribe", version=__version__, lifespan=lifespan)
app.add_middleware(LocalOnlyMiddleware)


# ---------------- 공통 ----------------

def _meeting_or_404(meeting_id: str) -> dict:
    m = store.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(404, "회의를 찾을 수 없습니다")
    return m


def _audio_path(meeting_id: str) -> Optional[Path]:
    """재생할 녹음 파일 (WAV, 다 만든 뒤 줄였으면 m4a)"""
    return audio_file(settings.meeting_path(meeting_id))


def _meeting_detail(m: dict) -> dict:
    out = dict(m)
    job = store.latest_job(m["id"])
    out["job"] = job
    out["has_audio"] = _audio_path(m["id"]) is not None
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
        "disk_free_bytes": disk_free_bytes(settings.data_dir),
        "disk_free_s": round(disk_free_seconds(settings.data_dir)),
        # 서버가 녹음 중 잠자기를 막는지 (맥: caffeinate). 아니면 브라우저가 화면 꺼짐을 막는다.
        "keep_awake": KeepAwake.available(),
        "power": power_source(),
        "project_root": str(Path(__file__).resolve().parents[2]),
        "language": settings.language,
        # 맥 앱(실행기)으로 설치했으면 모델은 메뉴 막대 앱에서 바꾸고, Claude는 앱의 파이썬으로 MCP 서버를 띄운다
        "app_install": bool(os.environ.get("SCRIBE_CONFIG")),
        # 저장 폴더가 iCloud·OneDrive 안이면 경고 (설정 → 저장 공간에 보여 준다)
        "storage_warning": sync_folder_warning(settings.data_dir),
        "mcp": _app_mcp_entry(),
    }


@app.get("/api/notices")
def get_notices(since: int = 0) -> list[dict]:
    """서버가 스스로 한 일 (화면이 알림으로 보여 준다)"""
    return notices.since(since)


@app.get("/api/system/memory")
def memory() -> dict:
    """설정 → 고급: 이 컴퓨터와 회의 노트가 쓰는 메모리, 모델 상태, 녹음 중 밀린 소리"""
    from . import live_speakers, memstat

    return {
        "system": memstat.system(),
        "server": memstat.process_bytes(os.getpid()),
        "jobs": [{"meeting_id": j["meeting_id"], "bytes": memstat.process_bytes(j["pid"])} for j in store.active_jobs() if j.get("pid")],
        "live_model": holder.info(),
        "speaker_model": live_speakers.status(),
        "recording": [{"meeting_id": mid, "backlog_s": round(s.seg.pending_s, 1)} for mid, s in list(sessions.items())],
    }


@app.get("/api/logs/server")
def server_log(lines: int = 300) -> dict:
    """서버 로그 끝부분 (앱으로 설치했을 때 실행기가 SCRIBE_SERVER_LOG로 위치를 알려 준다)"""
    path = os.environ.get("SCRIBE_SERVER_LOG")
    if not path or not os.path.exists(path):
        return {"path": path, "available": False, "lines": []}
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 256 * 1024))
        text = f.read().decode("utf-8", errors="replace")
    # 진행 막대(\r로 덮어쓰는 줄)는 마지막 모습만
    out = [ln.rstrip("\r").split("\r")[-1] for ln in text.rstrip("\n").split("\n")]
    return {"path": path, "available": True, "lines": out[-max(1, min(lines, 2000)):]}


def _app_mcp_entry() -> Optional[dict]:
    if not os.environ.get("SCRIBE_CONFIG"):
        return None  # 터미널로 설치: scripts/mcp.sh
    from .installer import mcp_entry

    return mcp_entry()


@app.post("/api/warmup")
def warmup() -> dict:
    """앱을 다시 보면 실시간 모델을 미리 올려 둔다 (녹음 시작을 기다리지 않게)."""
    holder.start_loading()
    return holder.info()


@app.get("/api/storage")
def storage() -> dict:
    """앱이 쓰는 저장 공간과 남은 공간 (설정 화면)."""
    audio = other = 0
    for mdir in settings.meetings_dir.iterdir() if settings.meetings_dir.exists() else []:
        for f in mdir.rglob("*"):
            if f.is_file():
                size = f.stat().st_size
                if f.suffix.lower() in (".wav", ".m4a", ".mp3", ".mp4", ".aac", ".flac", ".ogg", ".webm") or f.stem == "source":
                    audio += size
                else:
                    other += size
    db = sum(p.stat().st_size for p in settings.data_dir.glob("scribe.db*") if p.is_file())
    return {
        "data_dir": str(settings.data_dir),
        "meetings": len(store.list_meetings()),
        "audio_bytes": audio,
        "other_bytes": other + db,
        "free_bytes": disk_free_bytes(settings.data_dir),
        "free_s": round(disk_free_seconds(settings.data_dir)),
    }


class SettingsIn(BaseModel):
    vocabulary: Optional[str] = None
    break_prompt_minutes: Optional[float] = None
    live_model: Optional[str] = None  # 실시간 받아쓰기 모델 id (models.json live)


def _live_choice_id() -> Optional[str]:
    kind, model = model_for("live")
    return next((c["id"] for c in catalog.CATALOG["live"] if c["engine"] == kind and c["model"] == model), None)


def _save_app_config(key: str, value: Any) -> None:
    """앱으로 설치했으면 config.json에도 적어 다음에 켤 때와 설치·설정 창이 같은 값을 보게 한다"""
    path = os.environ.get("SCRIBE_CONFIG")
    if not path:
        return
    import json

    p = Path(path)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        cfg[key] = value
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name != "nt":
            os.chmod(tmp, 0o600)  # Hugging Face 토큰이 들어 있다
        tmp.replace(p)
    except (OSError, ValueError) as exc:
        print(f"설정 파일에 적지 못했어요: {exc}", file=sys.stderr, flush=True)


@app.get("/api/settings")
def get_settings() -> dict:
    return {
        "vocabulary": store.get_setting("vocabulary", ""),
        # 이만큼 조용하면 '쉬는 시간인가요?'를 묻는다 (0이면 묻지 않음)
        "break_prompt_minutes": store.get_setting("break_prompt_minutes", 3),
        # 실시간 받아쓰기 모델: 이 기기에서 고를 수 있는 것과 지금 것 (왼쪽 아래 모델 표시에서 바꾼다)
        "live_model": _live_choice_id(),
        "live_choices": [{k: c.get(k) for k in ("id", "label", "tag", "detail", "memory_gb")}
                         for c in catalog.CATALOG["live"] if catalog.for_os(c)],
    }


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> dict:
    if body.vocabulary is not None:
        store.set_setting("vocabulary", body.vocabulary.strip())
    if body.break_prompt_minutes is not None:
        store.set_setting("break_prompt_minutes", max(0.0, min(60.0, float(body.break_prompt_minutes))))
    if body.live_model and body.live_model != _live_choice_id():
        c = catalog.choice("live", body.live_model)
        if c is None or not catalog.for_os(c):
            raise HTTPException(400, "이 기기에서 쓸 수 없는 모델이에요")
        if sessions:
            raise HTTPException(409, "녹음 중에는 모델을 바꿀 수 없어요. 녹음을 마친 뒤 바꿔 주세요")
        old = settings.live_engine, settings.live_model
        settings.live_engine, settings.live_model = c["engine"], c["model"]
        if not holder.reload():
            settings.live_engine, settings.live_model = old
            raise HTTPException(409, "모델을 올리는 중이에요. 잠시 뒤 다시 바꿔 주세요")
        _save_app_config("live", c["id"])
    return get_settings()


# ---------------- 회의 ----------------

class MeetingIn(BaseModel):
    title: Optional[str] = None


class MeetingPatch(BaseModel):
    title: Optional[str] = None
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
    if body.num_speakers is not None:
        fields["num_speakers"] = body.num_speakers or None
    m = store.update_meeting(meeting_id, **fields)
    if "title" in fields:
        _sync_folder(meeting_id)
    return _meeting_detail(m)  # type: ignore[arg-type]


class ParticipantsIn(BaseModel):
    participants: list[dict]


@app.put("/api/meetings/{meeting_id}/participants")
def put_participants(meeting_id: str, body: ParticipantsIn) -> dict:
    """회의 참여자 [{id?, name, org?, role?, note?}] (소속·직무·메모). 녹음 전에 미리, 또는 녹음 중에 언제든."""
    m = _meeting_or_404(meeting_id)
    parts = normalize_participants(body.participants, m.get("participants") or [], _known_people(exclude=meeting_id))
    store.update_meeting(meeting_id, participants=parts)
    tracker_for(store, meeting_id).reload()  # 같은 이름의 기억된 목소리로 시작
    return {"participants": parts}


def _known_people(exclude: Optional[str] = None) -> dict[str, dict]:
    """지난 회의들에서 적어 둔 사람별 소속·직무 (가장 최근 것)"""
    known: dict[str, dict] = {}
    for m in store.list_meetings():
        if m["id"] == exclude:
            continue
        for p in m.get("participants") or []:
            cur = known.setdefault(p["name"], {})
            for key in ("org", "role"):
                if p.get(key) and key not in cur:
                    cur[key] = p[key]
    return known


@app.get("/api/participants/suggest")
def suggest_participants() -> list[dict]:
    """참여자 이름 자동 완성: 최근 회의 참여자(소속·직무와 함께) + 목소리를 기억하는 사람"""
    voices = {s["name"] for s in store.list_voice_samples() if (s.get("space") or "").startswith("live")}
    seen: dict[str, dict] = {}
    for name, info in _known_people().items():
        seen.setdefault(name, {"name": name, "voice": name in voices, **info})
    for name in sorted(voices):
        seen.setdefault(name, {"name": name, "voice": True})
    return list(seen.values())[:50]


class SpeakerAssignIn(BaseModel):
    segment_ids: list[str]
    participant_id: Optional[str] = None
    scope: str = "voice"  # voice: 같은 목소리 문장도 | one: 이 문장만


@app.post("/api/meetings/{meeting_id}/live/speaker")
def assign_speaker(meeting_id: str, body: SpeakerAssignIn) -> dict:
    """실시간 기록 문장의 화자를 고른다. 같은 목소리의 다른 문장에도 바로 반영한다."""
    _meeting_or_404(meeting_id)
    tracker = tracker_for(store, meeting_id)
    before_map = tracker.snapshot_map()
    try:
        changes = tracker.assign(body.segment_ids, body.participant_id, "one" if body.scope == "one" else "voice")
    except KeyError:
        raise HTTPException(400, "참여자를 찾을 수 없습니다")
    # 화면이 "이 문장만"으로 되돌릴 수 있게 바꾸기 전 대응표도 돌려준다
    return {"changes": changes, "previous_map": before_map}


class SpeakerRestoreIn(BaseModel):
    rows: list[dict]
    speaker_map: Optional[dict] = None


@app.post("/api/meetings/{meeting_id}/live/speaker/restore")
def restore_speakers(meeting_id: str, body: SpeakerRestoreIn) -> dict:
    """되돌리기: 예전 화자와 목소리 대응표로"""
    _meeting_or_404(meeting_id)
    return {"changes": tracker_for(store, meeting_id).restore(body.rows, body.speaker_map)}


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: str) -> dict:
    _meeting_or_404(meeting_id)
    if meeting_id in sessions:
        raise HTTPException(409, "녹음 중인 회의는 삭제할 수 없습니다")
    jobs.cancel(meeting_id)
    forget_tracker(meeting_id)
    store.delete_meeting(meeting_id)
    shutil.rmtree(settings.meeting_path(meeting_id), ignore_errors=True)
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
    # 바로 받아 적지 않는다: 사용자가 모델·화자 구분을 고르고(예상 시간을 보고) 시작한다
    from .audio import probe_duration

    store.update_meeting(m["id"], status="recorded", duration=probe_duration(dst) or 0.0)
    return _meeting_detail(store.get_meeting(m["id"]))  # type: ignore[arg-type]


# ---------------- 기록 ----------------

_ANN_DROP = ("origin", "origin_version", "replacement")  # 예전 "원본에 옮겨 붙이기"·"수정 제안"의 칸


def api_ann(a: dict) -> dict:
    """화면에 보내는 메모·댓글: 화면이 쓰는 칸만 (위치는 pos에 있으므로 anchor의 문장 위치는 뺀다)."""
    out = {k: v for k, v in a.items() if k not in _ANN_DROP}
    out["anchor"] = {k: v for k, v in a["anchor"].items() if k not in ("start", "end")}
    return out


def _json(data: Any) -> Response:
    """큰 응답은 FastAPI의 변환(jsonable_encoder)을 거치지 않고 바로 보낸다 (3시간 회의에서 수십 ms)."""
    import json

    return Response(json.dumps(data, ensure_ascii=False, separators=(",", ":")), media_type="application/json")


@app.get("/api/meetings/{meeting_id}/live")
def live_view(meeting_id: str) -> Response:
    _meeting_or_404(meeting_id)
    view = views.live_view(meeting_id)
    return _json({"units": view["units"], "annotations": [api_ann(a) for a in view["annotations"]]})


class SegmentEdit(BaseModel):
    text: str


@app.patch("/api/meetings/{meeting_id}/segments/{segment_id}")
def patch_segment(meeting_id: str, segment_id: str, body: SegmentEdit) -> dict:
    """기록의 문장 글자를 직접 고친다 (녹음 중에도). 처음 받아 적은 글자로 보내면 원래대로 돌아간다.
    그 문장에 걸린 댓글·강조는 글자 차이대로 위치를 옮겨 함께 돌려준다."""
    _meeting_or_404(meeting_id)
    try:
        r = edit_segment(store, views, meeting_id, segment_id, body.text)
        return dict(r, annotations=[api_ann(a) for a in r["annotations"]])
    except KeyError:
        raise HTTPException(404, "문장을 찾을 수 없습니다")
    except EditError as exc:
        raise HTTPException(400, str(exc))


class ProcessIn(BaseModel):
    final: Optional[str] = None  # 모델 id (models.json final)
    speakers: Optional[bool] = None  # 화자 구분
    num_speakers: Optional[int] = None  # 0이면 자동


@app.get("/api/meetings/{meeting_id}/process/options")
def process_options(meeting_id: str) -> dict:
    """시작하기 전에 고를 것: 이 기기의 모델들(받았는지, 예상 시간), 화자 구분, 화자 수"""
    from .process_plan import plan

    m = _meeting_or_404(meeting_id)
    return dict(plan(store, float(m.get("duration") or 0)), num_speakers=m.get("num_speakers"))


@app.post("/api/meetings/{meeting_id}/process")
def process(meeting_id: str, body: Optional[ProcessIn] = None) -> dict:
    """사용자가 직접 시작한다 (모델·화자 구분을 고른 대로)"""
    from .process_plan import plan, validate

    m = _meeting_or_404(meeting_id)
    if meeting_id in sessions:
        raise HTTPException(409, "녹음을 먼저 종료해 주세요")
    mdir = settings.meeting_path(meeting_id)
    if _audio_path(meeting_id) is None and not any(mdir.glob("source.*")):
        raise HTTPException(400, "처리할 오디오가 없습니다")
    body = body or ProcessIn()
    if (err := validate(body.final)) is not None:
        raise HTTPException(400, err)
    # 모델을 고르지 않았으면 설정의 모델 그대로 (고른 것만 작업에 적는다)
    options: dict = {"speakers": bool(plan(store, 0)["speakers"]["available"] and body.speakers is not False)}
    if body.final:
        options["final"] = body.final
    if body.num_speakers is not None:
        store.update_meeting(meeting_id, num_speakers=max(0, min(20, body.num_speakers)) or None)
    return {"job": jobs.enqueue(m["id"], options)}


@app.post("/api/meetings/{meeting_id}/cancel")
def cancel(meeting_id: str) -> dict:
    _meeting_or_404(meeting_id)
    jobs.cancel(meeting_id)
    return {"ok": True}


@app.get("/api/meetings/{meeting_id}/job")
def job(meeting_id: str) -> dict:
    """작업 진행률. record_rev: 기록이 바뀐 횟수 (올린 파일은 받아 적는 동안 기록이 구간마다 늘어난다)"""
    _meeting_or_404(meeting_id)
    return {"job": store.latest_job(meeting_id), "record_rev": store.record_rev(meeting_id)}


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


@app.get("/api/meetings/{meeting_id}/audio")
def audio(meeting_id: str, request: Request) -> Response:
    path = _audio_path(meeting_id)
    if path is None:
        raise HTTPException(404, "오디오가 아직 없습니다")
    mtype = media_type(path)
    size = path.stat().st_size
    rng = request.headers.get("range")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    if not rng:
        return FileResponse(path, media_type=mtype, headers=headers)
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
    return StreamingResponse(body(), status_code=206, media_type=mtype, headers=headers)


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


# ---------------- 메모·댓글·강조·구분선 ----------------

class AnnotationIn(BaseModel):
    kind: str  # comment | memo | highlight | divider
    start: Optional[dict] = None
    end: Optional[dict] = None
    t: Optional[float] = None
    after: Optional[str] = None
    body: str = ""
    color: Optional[str] = None


class AnnotationPatch(BaseModel):
    body: Optional[str] = None
    status: Optional[str] = None
    color: Optional[str] = None


class ReplyIn(BaseModel):
    text: str


class ExportedIn(BaseModel):
    ids: list[str]
    exported: bool = True


KINDS = {"comment", "memo", "highlight", "divider"}


@app.post("/api/meetings/{meeting_id}/annotations")
def add_annotation(meeting_id: str, body: AnnotationIn) -> dict:
    _meeting_or_404(meeting_id)
    if body.kind not in KINDS:
        raise HTTPException(400, "알 수 없는 종류")
    payload = body.model_dump()
    # 긴 회의에서도 빠르게: 고른 문장(또는 메모를 붙일 문장)만 덮는 작은 색인으로 위치를 정한다
    if body.kind in ("memo", "divider"):
        if body.t is None:
            raise HTTPException(400, "시간이 필요합니다")
        idx = (views.local_index(meeting_id, [body.after]) if body.after else None) or views.index(meeting_id)
    elif not (body.start and body.end):
        raise HTTPException(400, "선택 범위가 필요합니다")
    else:
        idx = views.local_index(meeting_id, [str(body.start.get("unit") or ""), str(body.end.get("unit") or "")])
        if idx is None:
            raise HTTPException(400, "선택한 범위를 찾을 수 없습니다")
    anchor = anchor_from_request(idx, payload)
    if anchor is None:
        raise HTTPException(400, "선택한 범위를 찾을 수 없습니다")
    ann = store.add_annotation(
        meeting_id,
        kind=body.kind,
        origin="live",
        origin_version=0,
        anchor=anchor,
        body=body.body,
        color=body.color,
        status="open",
    )
    return api_ann(views.resolve_one(ann))


def _ann_or_404(ann_id: str) -> dict:
    ann = store.get_annotation(ann_id)
    if ann is None:
        raise HTTPException(404, "항목을 찾을 수 없습니다")
    return ann


@app.patch("/api/annotations/{ann_id}")
def patch_annotation(ann_id: str, body: AnnotationPatch) -> dict:
    _ann_or_404(ann_id)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    ann = store.update_annotation(ann_id, **fields)
    return api_ann(views.resolve_one(ann))  # type: ignore[arg-type]


@app.post("/api/annotations/{ann_id}/replies")
def add_reply(ann_id: str, body: ReplyIn) -> dict:
    ann = _ann_or_404(ann_id)
    replies = list(ann.get("replies") or [])
    replies.append({"text": body.text.strip(), "at": time.time()})
    ann = store.update_annotation(ann_id, replies=replies)
    return api_ann(views.resolve_one(ann))  # type: ignore[arg-type]


@app.post("/api/meetings/{meeting_id}/annotations/exported")
def mark_exported(meeting_id: str, body: ExportedIn) -> dict:
    """댓글/메모를 노트로 내보냈다고 표시한다."""
    _meeting_or_404(meeting_id)
    updated = store.mark_exported(meeting_id, body.ids, body.exported)
    wanted = set(body.ids)
    stamps = {a["id"]: a["exported_at"] for a in store.list_annotations(meeting_id) if a["id"] in wanted}
    return {"updated": updated, "exported_at": stamps}


@app.delete("/api/annotations/{ann_id}")
def delete_annotation(ann_id: str) -> dict:
    _ann_or_404(ann_id)
    store.delete_annotation(ann_id)
    return {"ok": True}


# ---------------- 내보내기 / 검색 ----------------

def _attachment(name: str, ascii_name: str) -> str:
    """받을 파일 이름. 한글 이름(filename*)을 못 읽는 브라우저를 위해 영문 이름도 함께 준다."""
    from urllib.parse import quote

    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


@app.get("/api/meetings/{meeting_id}/export.md")
def export_md(meeting_id: str, download: int = 0) -> Response:
    md = meeting_markdown(views, meeting_id, include_summary=True)
    if md is None:
        raise HTTPException(404, "회의를 찾을 수 없습니다")
    headers = {}
    if download:
        title = (store.get_meeting(meeting_id) or {}).get("title") or "meeting"
        headers["Content-Disposition"] = _attachment(f"{title}.md", "meeting.md")
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8", headers=headers)


@app.get("/api/meetings/{meeting_id}/script.md")
def script_md(meeting_id: str, download: int = 0) -> Response:
    """원본 스크립트: 녹음을 마친 뒤 큰 모델로 다시 받아 적고 화자를 나눈 참고용 기록"""
    import datetime as _dt

    m = _meeting_or_404(meeting_id)
    md = script_markdown(store, meeting_id)
    if md is None:
        raise HTTPException(404, "원본 스크립트가 아직 없습니다")
    headers = {}
    if download:
        day = _dt.datetime.fromtimestamp(m["created_at"]).strftime("%Y-%m-%d")
        name = re.sub(r'[\\/:*?"<>|]+', " ", f"{day} {m['title'] or '회의'} 원본 스크립트.md").strip()
        headers["Content-Disposition"] = _attachment(name, f"{day} script.md")
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8", headers=headers)


def _doc_response(meeting_id: str, kind: str, download: int) -> Response:
    """회의 문서(전사 기록·회의록) HTML. 앱에서 열면 위쪽에 전환·인쇄·받기 줄이 붙고,
    받은 파일은 같은 폴더의 녹음 파일로 시각을 눌러 들을 수 있다."""
    import datetime as _dt

    from . import minutes, transcript_html

    mod = transcript_html if kind == "transcript" else minutes
    data = mod.collect(store, views, meeting_id)
    if data is None:
        raise HTTPException(404, "회의를 찾을 수 없습니다")
    path = _audio_path(meeting_id)
    if not download:
        src = f"/api/meetings/{meeting_id}/audio" if path else None
        return HTMLResponse(mod.render(data, audio_src=src, tools=minutes.tools_html(meeting_id, kind)))
    day = _dt.datetime.fromtimestamp(data["start"]).strftime("%Y-%m-%d")
    label, ascii_label = ("전사 기록", "transcript") if kind == "transcript" else ("회의록", "minutes")
    name = re.sub(r'[\\/:*?"<>|]+', " ", f"{day} {data['title']} {label}.html").strip()
    headers = {"Content-Disposition": _attachment(name, f"{day} {ascii_label}.html")}
    return HTMLResponse(mod.render(data, audio_src=f"녹음{path.suffix}" if path else None), headers=headers)


@app.post("/api/meetings/{meeting_id}/reveal")
def reveal_meeting(meeting_id: str) -> dict:
    """회의 폴더 열기: 전사 기록·회의록 HTML을 새로 만들어 넣고 Finder·파일 탐색기로 연다 (이 컴퓨터에서만)"""
    import subprocess
    import sys

    from . import minutes, transcript_html

    _meeting_or_404(meeting_id)
    mdir = settings.meeting_dir(meeting_id)
    audio = _audio_path(meeting_id)
    for mod, name in ((transcript_html, "전사 기록.html"), (minutes, "회의록.html")):
        data = mod.collect(store, views, meeting_id)
        if data is not None:
            (mdir / name).write_text(mod.render(data, audio_src=audio.name if audio else None), encoding="utf-8")
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(mdir)])
        elif sys.platform == "win32":
            os.startfile(str(mdir))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(mdir)])
    except OSError as exc:
        raise HTTPException(500, f"폴더를 열지 못했어요: {exc}")
    return {"path": str(mdir)}


@app.get("/api/meetings/{meeting_id}/transcript.html")
def transcript_doc(meeting_id: str, download: int = 0) -> Response:
    """전사 기록: 화자별 발언 위에 회의 중 메모·댓글·수정·강조가 제자리에"""
    return _doc_response(meeting_id, "transcript", download)


@app.get("/api/meetings/{meeting_id}/minutes.html")
def minutes_doc(meeting_id: str, download: int = 0) -> Response:
    """회의록: 정보·요약·할 일·노트·메모를 한 장으로"""
    return _doc_response(meeting_id, "minutes", download)


@app.get("/api/backup.zip")
def backup() -> StreamingResponse:
    """전체 백업 (DB + 녹음 + 회의록 .md)"""
    from .backup import backup_name, iter_backup

    headers = {"Content-Disposition": _attachment(backup_name(), "meeting-scribe-backup.zip")}
    return StreamingResponse(iter_backup(store, views), media_type="application/zip", headers=headers)


@app.get("/api/meetings/{meeting_id}/export.zip")
def export_meeting(meeting_id: str) -> StreamingResponse:
    """회의 하나를 '날짜_제목' 폴더(HTML + 녹음)로"""
    from .meeting_file import base_name, iter_zip

    name = base_name(_meeting_or_404(meeting_id))
    day = name.split("_", 1)[0]
    headers = {"Content-Disposition": _attachment(f"{name}.zip", f"{day}_meeting.zip")}
    return StreamingResponse(iter_zip(store, views, meeting_id, name), media_type="application/zip", headers=headers)


@app.post("/api/import")
def import_meeting_file(files: list[UploadFile] = File(...)) -> dict:
    """내보낸 회의(zip, 또는 HTML + 녹음)를 새 회의로 불러온다"""
    from .meeting_file import MeetingFileError, import_meeting, read_files

    try:
        data, audio = read_files([(f.filename or "", f.file) for f in files])
        return _meeting_detail(import_meeting(store, data, audio))
    except MeetingFileError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/search")
def search(q: str, limit: int = 30) -> list[dict]:
    return search_all(views, q, limit=limit)


# ---------------- 녹음 WebSocket ----------------

def _before_resume(meeting_id: str) -> bool:
    """원본 스크립트를 만드는 중에 다시 녹음하면 그 작업을 취소한다. 취소했으면 True."""
    latest = store.latest_job(meeting_id)
    if latest and latest["status"] in ("queued", "running"):
        jobs.cancel(meeting_id)
        return True
    return False


@app.websocket("/api/meetings/{meeting_id}/record")
async def record(ws: WebSocket) -> None:
    await handle_recording(ws, store, holder, sessions, _before_resume)


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

"""SQLite 저장소. 서버 프로세스와 후처리 워커 프로세스가 같이 쓰므로 WAL 모드를 쓴다."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings(
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'empty',
  source TEXT NOT NULL DEFAULT 'live',
  duration REAL NOT NULL DEFAULT 0,
  original_name TEXT,
  speakers TEXT NOT NULL DEFAULT '{}',
  num_speakers INTEGER,
  final_version INTEGER NOT NULL DEFAULT 0,
  summary_md TEXT,
  summary_updated_at REAL,
  notes_json TEXT,
  notes_md TEXT,
  notes_updated_at REAL
);
CREATE TABLE IF NOT EXISTS live_segments(
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  t0 REAL NOT NULL,
  t1 REAL NOT NULL,
  text TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_meeting ON live_segments(meeting_id, idx);
CREATE TABLE IF NOT EXISTS final_versions(
  meeting_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  created_at REAL NOT NULL,
  engine TEXT,
  diarizer TEXT,
  data TEXT NOT NULL,
  PRIMARY KEY(meeting_id, version)
);
CREATE TABLE IF NOT EXISTS annotations(
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  origin TEXT NOT NULL,
  origin_version INTEGER NOT NULL DEFAULT 0,
  anchor TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  replacement TEXT,
  color TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  replies TEXT NOT NULL DEFAULT '[]',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ann_meeting ON annotations(meeting_id);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  phase TEXT,
  progress REAL NOT NULL DEFAULT 0,
  message TEXT,
  error TEXT,
  pid INTEGER,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  eta REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_meeting ON jobs(meeting_id, created_at);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

JSON_FIELDS_MEETING = ("speakers", "clock", "participants", "speaker_map")
KEEP_FINAL_VERSIONS = 1  # 원본 스크립트는 참고용이라 마지막 것만


def _note_rows(md: str) -> list[tuple]:
    """노트 마크다운을 문단 단위로 색인한다 (찾은 곳 주변만 보여 주려고)."""
    return [(None, None, part.strip()) for part in md.split("\n\n") if part.strip()]


def new_id() -> str:
    """시간순 정렬이 되는 짧은 ID."""
    return f"{int(time.time() * 1000):x}{secrets.token_hex(3)}"


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)
        self.fts = self._init_fts()

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """이전 버전 DB에 새 열을 더한다."""
        cols = {r["name"] for r in c.execute("PRAGMA table_info(annotations)").fetchall()}
        if "exported_at" not in cols:
            c.execute("ALTER TABLE annotations ADD COLUMN exported_at REAL")
        if "meta" not in cols:
            c.execute("ALTER TABLE annotations ADD COLUMN meta TEXT")
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(meetings)").fetchall()}
        # 일시정지/연결 끊김 시각: 다시 녹음할 때 쉬는 시간 구분선을 만든다
        if "paused_wall" not in mcols:
            c.execute("ALTER TABLE meetings ADD COLUMN paused_wall REAL")
        if "paused_t" not in mcols:
            c.execute("ALTER TABLE meetings ADD COLUMN paused_t REAL")
        # 실시간 임시 화자: 문장별 화자(참여자 id), 자동 묶음(A, B…), 누가 정했는지, 확신도
        lcols = {r["name"] for r in c.execute("PRAGMA table_info(live_segments)").fetchall()}
        # 직접 고친 문장: 처음 받아 적은 글자(text_orig)와 고친 시각
        for col, typ in (("speaker", "TEXT"), ("speaker_auto", "TEXT"), ("speaker_src", "TEXT"), ("speaker_conf", "REAL"),
                         ("text_orig", "TEXT"), ("edited_at", "REAL")):
            if col not in lcols:
                c.execute(f"ALTER TABLE live_segments ADD COLUMN {col} {typ}")
        c.execute(
            "CREATE TABLE IF NOT EXISTS live_embeddings (segment_id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, embedding TEXT NOT NULL)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_live_emb_meeting ON live_embeddings(meeting_id)")
        # 회의 참여자 [{id, name}]와 자동 묶음 → 참여자 대응표 {A: p1}
        if "participants" not in mcols:
            c.execute("ALTER TABLE meetings ADD COLUMN participants TEXT NOT NULL DEFAULT '[]'")
        if "speaker_map" not in mcols:
            c.execute("ALTER TABLE meetings ADD COLUMN speaker_map TEXT NOT NULL DEFAULT '{}'")
        # 화자 목소리 기억 (이름 붙인 화자의 목소리 특징)
        c.execute(
            "CREATE TABLE IF NOT EXISTS voice_samples (meeting_id TEXT NOT NULL, label TEXT NOT NULL, name TEXT NOT NULL,"
            " embedding TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (meeting_id, label))"
        )
        # 목소리 특징의 종류(live:…): 같은 종류끼리만 비교한다 (예전 diar 항목은 쓰지 않는다)
        vcols = {r["name"] for r in c.execute("PRAGMA table_info(voice_samples)").fetchall()}
        if "space" not in vcols:
            c.execute("ALTER TABLE voice_samples ADD COLUMN space TEXT NOT NULL DEFAULT 'diar'")
        # 녹음 시간 → 실제 시각: [[녹음 시간(초), 그때의 실제 시각(epoch)], ...]
        if "clock" not in mcols:
            c.execute("ALTER TABLE meetings ADD COLUMN clock TEXT NOT NULL DEFAULT '[]'")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        yield conn

    # ---------- meetings ----------
    # ---------- 검색 색인 (FTS5 trigram: 글자 3개 이상은 색인, 더 짧으면 LIKE) ----------
    FTS_VERSION = 2  # 2: 원본 스크립트는 찾지 않는다

    def _init_fts(self) -> bool:
        try:
            with self.conn() as c:
                c.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
                    "meeting_id UNINDEXED, kind UNINDEXED, ref UNINDEXED, t UNINDEXED, text, tokenize='trigram')"
                )
        except sqlite3.OperationalError:
            return False  # FTS5가 없는 SQLite: 파이썬으로 훑어 찾는다
        if self.get_setting("fts_version") != self.FTS_VERSION:
            self.rebuild_search_index()
            self.set_setting("fts_version", self.FTS_VERSION)
        return True

    def rebuild_search_index(self) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM search_fts")
        for m in self.list_meetings():
            mid = m["id"]
            self._fts_replace(mid, "title", [(None, None, m.get("title") or "")])
            self._fts_replace(mid, "live", [(s["id"], s["t0"], s["text"]) for s in self.list_live_segments(mid)])
            full = self.get_meeting(mid, with_notes=True) or {}
            self._fts_replace(mid, "notes", _note_rows(full.get("notes_md") or ""))

    def _fts_replace(self, meeting_id: str, kind: str, rows: list[tuple]) -> None:
        if getattr(self, "fts", True) is False:
            return
        with self.conn() as c:
            c.execute("DELETE FROM search_fts WHERE meeting_id=? AND kind=?", (meeting_id, kind))
            c.executemany(
                "INSERT INTO search_fts(meeting_id,kind,ref,t,text) VALUES(?,?,?,?,?)",
                [(meeting_id, kind, ref, t, text) for ref, t, text in rows if text],
            )

    def search_rows(self, q: str, limit: int = 300) -> list[dict]:
        """색인에서 찾은 줄들. 3글자 이상이면 색인(MATCH), 짧으면 LIKE로 훑는다."""
        with self.conn() as c:
            if len(q) >= 3:
                rows = c.execute(
                    "SELECT meeting_id, kind, ref, t, text FROM search_fts WHERE search_fts MATCH ? LIMIT ?",
                    ('"' + q.replace('"', '""') + '"', limit),
                ).fetchall()
            else:
                esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = c.execute(
                    "SELECT meeting_id, kind, ref, t, text FROM search_fts WHERE text LIKE ? ESCAPE '\\' LIMIT ?",
                    (f"%{esc}%", limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def create_meeting(self, title: str, source: str = "live", original_name: Optional[str] = None) -> dict:
        now = time.time()
        mid = new_id()
        with self.conn() as c:
            c.execute(
                "INSERT INTO meetings(id,title,created_at,updated_at,status,source,original_name) VALUES(?,?,?,?,?,?,?)",
                (mid, title, now, now, "empty", source, original_name),
            )
        self._fts_replace(mid, "title", [(None, None, title or "")])
        return self.get_meeting(mid)  # type: ignore[return-value]

    def get_meeting(self, meeting_id: str, *, with_notes: bool = False) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if row is None:
            return None
        return self._meeting_row(row, with_notes=with_notes)

    def list_meetings(self, query: Optional[str] = None, limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM meetings"
        args: list[Any] = []
        if query:
            sql += " WHERE title LIKE ?"
            args.append(f"%{query}%")
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [self._meeting_row(r) for r in rows]

    def update_meeting(self, meeting_id: str, **fields: Any) -> Optional[dict]:
        if not fields:
            return self.get_meeting(meeting_id)
        fields["updated_at"] = time.time()
        cols = []
        vals: list[Any] = []
        for key, value in fields.items():
            if key in JSON_FIELDS_MEETING and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            cols.append(f"{key}=?")
            vals.append(value)
        vals.append(meeting_id)
        with self.conn() as c:
            c.execute(f"UPDATE meetings SET {', '.join(cols)} WHERE id=?", vals)
        if "title" in fields:
            self._fts_replace(meeting_id, "title", [(None, None, fields["title"] or "")])
        if "notes_md" in fields:
            self._fts_replace(meeting_id, "notes", _note_rows(fields["notes_md"] or ""))
        return self.get_meeting(meeting_id)

    def delete_meeting(self, meeting_id: str) -> None:
        with self.conn() as c:
            c.execute("BEGIN")
            for table in ("live_segments", "live_embeddings", "final_versions", "annotations", "jobs", "voice_samples"):
                c.execute(f"DELETE FROM {table} WHERE meeting_id=?", (meeting_id,))
            c.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
            if self.fts:
                c.execute("DELETE FROM search_fts WHERE meeting_id=?", (meeting_id,))
            c.execute("COMMIT")

    def _meeting_row(self, row: sqlite3.Row, *, with_notes: bool = False) -> dict:
        d = dict(row)
        d["speakers"] = json.loads(d.get("speakers") or "{}")
        d["clock"] = json.loads(d.get("clock") or "[]")
        d["participants"] = json.loads(d.get("participants") or "[]")
        d["speaker_map"] = json.loads(d.get("speaker_map") or "{}")
        if not with_notes:
            d.pop("notes_json", None)
            d.pop("notes_md", None)
        else:
            d["notes"] = json.loads(d.pop("notes_json") or "null")
        return d

    # ---------- live segments ----------
    def add_live_segment(self, meeting_id: str, t0: float, t1: float, text: str) -> dict:
        with self.conn() as c:
            row = c.execute("SELECT COALESCE(MAX(idx), -1) AS m FROM live_segments WHERE meeting_id=?", (meeting_id,)).fetchone()
            idx = int(row["m"]) + 1
            seg = {"id": new_id(), "meeting_id": meeting_id, "idx": idx, "t0": float(t0), "t1": float(t1), "text": text, "created_at": time.time()}
            c.execute(
                "INSERT INTO live_segments(id,meeting_id,idx,t0,t1,text,created_at) VALUES(:id,:meeting_id,:idx,:t0,:t1,:text,:created_at)",
                seg,
            )
            if getattr(self, "fts", False) and text:
                c.execute("INSERT INTO search_fts(meeting_id,kind,ref,t,text) VALUES(?,?,?,?,?)", (meeting_id, "live", seg["id"], seg["t0"], text))
        return seg

    def add_live_segments(self, meeting_id: str, rows: list[dict]) -> int:
        """올린 파일을 받아 적은 문장을 한꺼번에 기록으로 (화자 묶음과 함께)"""
        now = time.time()
        with self.conn() as c:
            row = c.execute("SELECT COALESCE(MAX(idx), -1) AS m FROM live_segments WHERE meeting_id=?", (meeting_id,)).fetchone()
            start = int(row["m"]) + 1
            segs = [{"id": new_id(), "meeting_id": meeting_id, "idx": start + i, "t0": float(r["t0"]), "t1": float(r["t1"]), "text": r["text"],
                     "created_at": now, "speaker_auto": r.get("speaker_auto"), "speaker_conf": r.get("speaker_conf")} for i, r in enumerate(rows)]
            c.executemany(
                "INSERT INTO live_segments(id,meeting_id,idx,t0,t1,text,created_at,speaker_auto,speaker_conf)"
                " VALUES(:id,:meeting_id,:idx,:t0,:t1,:text,:created_at,:speaker_auto,:speaker_conf)",
                segs,
            )
        self._fts_replace(meeting_id, "live", [(s["id"], s["t0"], s["text"]) for s in self.list_live_segments(meeting_id)])
        return len(segs)

    def list_live_segments(self, meeting_id: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT id,idx,t0,t1,text,text_orig,edited_at FROM live_segments WHERE meeting_id=? ORDER BY idx", (meeting_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_live_segment(self, segment_id: str) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT id,meeting_id,idx,t0,t1,text,text_orig,edited_at FROM live_segments WHERE id=?", (segment_id,)).fetchone()
        return dict(row) if row else None

    def set_live_text(self, segment_id: str, text: str) -> Optional[dict]:
        """문장 글자를 직접 고친다. 처음 받아 적은 글자는 text_orig에 남기고, 원래대로 돌리면 고친 표시를 지운다."""
        seg = self.get_live_segment(segment_id)
        if seg is None:
            return None
        orig = seg["text_orig"] if seg["text_orig"] is not None else seg["text"]
        edited = text != orig
        with self.conn() as c:
            c.execute(
                "UPDATE live_segments SET text=?, text_orig=?, edited_at=? WHERE id=?",
                (text, orig if edited else None, time.time() if edited else None, segment_id),
            )
            if getattr(self, "fts", False):
                c.execute("DELETE FROM search_fts WHERE kind='live' AND ref=?", (segment_id,))
                c.execute("INSERT INTO search_fts(meeting_id,kind,ref,t,text) VALUES(?,?,?,?,?)", (seg["meeting_id"], "live", segment_id, seg["t0"], text))
        return self.get_live_segment(segment_id)

    def list_live_speakers(self, meeting_id: str) -> list[dict]:
        """문장별 임시 화자 (실시간 기록 순서대로)"""
        with self.conn() as c:
            rows = c.execute(
                "SELECT id,t0,t1,speaker,speaker_auto,speaker_src,speaker_conf FROM live_segments WHERE meeting_id=? ORDER BY idx",
                (meeting_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_live_speakers(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.conn() as c:
            c.executemany(
                "UPDATE live_segments SET speaker=:speaker, speaker_auto=:speaker_auto, speaker_src=:speaker_src, speaker_conf=:speaker_conf WHERE id=:id",
                rows,
            )

    def set_live_embedding(self, meeting_id: str, segment_id: str, embedding: list) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO live_embeddings(segment_id,meeting_id,embedding) VALUES(?,?,?)",
                (segment_id, meeting_id, json.dumps(embedding)),
            )

    def live_embeddings(self, meeting_id: str) -> dict:
        with self.conn() as c:
            rows = c.execute("SELECT segment_id, embedding FROM live_embeddings WHERE meeting_id=?", (meeting_id,)).fetchall()
        return {r["segment_id"]: json.loads(r["embedding"]) for r in rows}

    # ---------- final transcript ----------
    def save_final_version(self, meeting_id: str, data: dict, engine: str, diarizer: str) -> int:
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT COALESCE(MAX(version), 0) AS v FROM final_versions WHERE meeting_id=?", (meeting_id,)).fetchone()
            version = int(row["v"]) + 1
            c.execute(
                "INSERT INTO final_versions(meeting_id,version,created_at,engine,diarizer,data) VALUES(?,?,?,?,?,?)",
                (meeting_id, version, time.time(), engine, diarizer, json.dumps(data, ensure_ascii=False)),
            )
            c.execute("UPDATE meetings SET final_version=?, updated_at=? WHERE id=?", (version, time.time(), meeting_id))
            # 원본 스크립트는 참고용이라 마지막 것만 남긴다 (회의 기록과 메모·댓글은 여기에 묶여 있지 않다)
            c.execute("DELETE FROM final_versions WHERE meeting_id=? AND version<=?", (meeting_id, version - KEEP_FINAL_VERSIONS))
            c.execute("COMMIT")
        return version

    def status_after_job(self, meeting_id: str, failed: bool = True) -> str:
        """원본 스크립트 작업이 끝나지 못했을 때의 회의 상태: 기록이 있으면 회의는 멀쩡하다."""
        if self.get_final(meeting_id):
            return "ready"
        with self.conn() as c:
            has_record = c.execute("SELECT 1 FROM live_segments WHERE meeting_id=? LIMIT 1", (meeting_id,)).fetchone() is not None
        if has_record:
            return "recorded"
        return "error" if failed else "empty"

    def get_final(self, meeting_id: str, version: Optional[int] = None) -> Optional[dict]:
        with self.conn() as c:
            if version is None:
                row = c.execute(
                    "SELECT * FROM final_versions WHERE meeting_id=? ORDER BY version DESC LIMIT 1", (meeting_id,)
                ).fetchone()
            else:
                row = c.execute("SELECT * FROM final_versions WHERE meeting_id=? AND version=?", (meeting_id, version)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["data"] = json.loads(d["data"])
        return d

    # ---------- annotations ----------
    def add_annotation(self, meeting_id: str, **fields: Any) -> dict:
        now = time.time()
        ann = {
            "id": new_id(),
            "meeting_id": meeting_id,
            "kind": fields["kind"],
            "origin": fields.get("origin", "live"),
            "origin_version": int(fields.get("origin_version") or 0),
            "anchor": json.dumps(fields["anchor"], ensure_ascii=False),
            "body": fields.get("body") or "",
            "replacement": fields.get("replacement"),
            "color": fields.get("color"),
            "status": fields.get("status") or "open",
            "replies": json.dumps(fields.get("replies") or [], ensure_ascii=False),
            "meta": json.dumps(fields["meta"], ensure_ascii=False) if fields.get("meta") else None,
            "created_at": now,
            "updated_at": now,
        }
        with self.conn() as c:
            c.execute(
                "INSERT INTO annotations(id,meeting_id,kind,origin,origin_version,anchor,body,replacement,color,status,replies,meta,created_at,updated_at)"
                " VALUES(:id,:meeting_id,:kind,:origin,:origin_version,:anchor,:body,:replacement,:color,:status,:replies,:meta,:created_at,:updated_at)",
                ann,
            )
        return self.get_annotation(ann["id"])  # type: ignore[return-value]

    def get_annotation(self, ann_id: str) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM annotations WHERE id=?", (ann_id,)).fetchone()
        return self._ann_row(row) if row else None

    def list_annotations(self, meeting_id: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM annotations WHERE meeting_id=? ORDER BY created_at", (meeting_id,)).fetchall()
        return [self._ann_row(r) for r in rows]

    def update_annotation(self, ann_id: str, **fields: Any) -> Optional[dict]:
        allowed = {"body", "replacement", "color", "status", "replies", "anchor", "exported_at", "meta"}
        cols, vals = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("replies", "anchor", "meta") and value is not None and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            cols.append(f"{key}=?")
            vals.append(value)
        if cols:
            cols.append("updated_at=?")
            vals.append(time.time())
            vals.append(ann_id)
            with self.conn() as c:
                c.execute(f"UPDATE annotations SET {', '.join(cols)} WHERE id=?", vals)
        return self.get_annotation(ann_id)

    def mark_exported(self, meeting_id: str, ids: list[str], exported: bool = True) -> int:
        """노트로 내보낸 댓글/메모 표시 (중복 내보내기 방지용)."""
        if not ids:
            return 0
        value = time.time() if exported else None
        marks = ",".join("?" * len(ids))
        with self.conn() as c:
            cur = c.execute(
                f"UPDATE annotations SET exported_at=?, updated_at=? WHERE meeting_id=? AND id IN ({marks})",
                [value, time.time(), meeting_id, *ids],
            )
        return cur.rowcount

    def delete_annotation(self, ann_id: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM annotations WHERE id=?", (ann_id,))

    @staticmethod
    def _ann_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["anchor"] = json.loads(d["anchor"])
        d["replies"] = json.loads(d["replies"] or "[]")
        d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
        return d

    # ---------- jobs ----------
    def create_job(self, meeting_id: str, kind: str = "postprocess") -> dict:
        job = {"id": new_id(), "meeting_id": meeting_id, "kind": kind, "status": "queued", "phase": "queued", "progress": 0.0, "created_at": time.time()}
        with self.conn() as c:
            c.execute(
                "INSERT INTO jobs(id,meeting_id,kind,status,phase,progress,created_at) VALUES(:id,:meeting_id,:kind,:status,:phase,:progress,:created_at)",
                job,
            )
        return self.get_job(job["id"])  # type: ignore[return-value]

    def get_job(self, job_id: str) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def latest_job(self, meeting_id: str) -> Optional[dict]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE meeting_id=? ORDER BY created_at DESC LIMIT 1", (meeting_id,)).fetchone()
        return dict(row) if row else None

    def active_jobs(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = [f"{k}=?" for k in fields]
        with self.conn() as c:
            c.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id=?", [*fields.values(), job_id])

    # ---------- settings ----------
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def add_clock_point(self, meeting_id: str, t: float, wall: float) -> None:
        """이 녹음 시간 t부터는 실제 시각 wall에 녹음된 소리다 (녹음 시작, 재개할 때마다)."""
        m = self.get_meeting(meeting_id)
        if m is None:
            return
        points = [p for p in m.get("clock") or [] if p[0] < t - 1e-3]
        points.append([round(float(t), 3), round(float(wall), 3)])
        self.update_meeting(meeting_id, clock=points)

    # ---------- 화자 목소리 기억 ----------
    def set_voice_sample(self, meeting_id: str, label: str, name: str, embedding: list, space: str = "diar") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO voice_samples(meeting_id,label,name,embedding,updated_at,space) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(meeting_id,label) DO UPDATE SET name=excluded.name, embedding=excluded.embedding, updated_at=excluded.updated_at, space=excluded.space",
                (meeting_id, label, name, json.dumps(embedding), time.time(), space),
            )

    def delete_voice_sample(self, meeting_id: str, label: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM voice_samples WHERE meeting_id=? AND label=?", (meeting_id, label))

    def list_voice_samples(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT meeting_id, label, name, embedding, space FROM voice_samples").fetchall()
        return [{**dict(r), "embedding": json.loads(r["embedding"])} for r in rows]


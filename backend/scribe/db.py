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

JSON_FIELDS_MEETING = ("speakers",)


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

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """이전 버전 DB에 새 열을 더한다."""
        cols = {r["name"] for r in c.execute("PRAGMA table_info(annotations)").fetchall()}
        if "exported_at" not in cols:
            c.execute("ALTER TABLE annotations ADD COLUMN exported_at REAL")

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
    def create_meeting(self, title: str, source: str = "live", original_name: Optional[str] = None) -> dict:
        now = time.time()
        mid = new_id()
        with self.conn() as c:
            c.execute(
                "INSERT INTO meetings(id,title,created_at,updated_at,status,source,original_name) VALUES(?,?,?,?,?,?,?)",
                (mid, title, now, now, "empty", source, original_name),
            )
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
        return self.get_meeting(meeting_id)

    def delete_meeting(self, meeting_id: str) -> None:
        with self.conn() as c:
            c.execute("BEGIN")
            for table in ("live_segments", "final_versions", "annotations", "jobs"):
                c.execute(f"DELETE FROM {table} WHERE meeting_id=?", (meeting_id,))
            c.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
            c.execute("COMMIT")

    def _meeting_row(self, row: sqlite3.Row, *, with_notes: bool = False) -> dict:
        d = dict(row)
        d["speakers"] = json.loads(d.get("speakers") or "{}")
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
        return seg

    def list_live_segments(self, meeting_id: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT id,idx,t0,t1,text FROM live_segments WHERE meeting_id=? ORDER BY idx", (meeting_id,)).fetchall()
        return [dict(r) for r in rows]

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
            c.execute("COMMIT")
        return version

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
            "created_at": now,
            "updated_at": now,
        }
        with self.conn() as c:
            c.execute(
                "INSERT INTO annotations(id,meeting_id,kind,origin,origin_version,anchor,body,replacement,color,status,replies,created_at,updated_at)"
                " VALUES(:id,:meeting_id,:kind,:origin,:origin_version,:anchor,:body,:replacement,:color,:status,:replies,:created_at,:updated_at)",
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
        allowed = {"body", "replacement", "color", "status", "replies", "anchor", "exported_at"}
        cols, vals = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("replies", "anchor") and not isinstance(value, str):
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

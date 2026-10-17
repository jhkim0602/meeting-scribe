"""전체 백업: DB(회의·노트·첨삭·요약), 녹음 파일, 사람이 읽을 회의록(.md)을 zip 하나로 내려받는다.

몇 GB가 될 수 있어 임시 파일 없이 바로 흘려보낸다(스트리밍). DB는 SQLite 백업 기능으로
쓰는 중에도 일관된 사본을 떠서 넣는다.
"""

from __future__ import annotations

import io
import re
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Iterator

from .config import settings
from .db import Store
from .export import meeting_markdown
from .storage import audio_file
from .views import Views

RESTORE = """회의 노트 백업

복원하는 법
1. 회의 노트 서버를 끕니다 (./run.sh 창에서 Ctrl+C, 자동 실행을 켰다면 scripts/launchagent.sh uninstall).
2. 이 압축 안의 scribe.db 와 meetings 폴더를 데이터 폴더(기본 ~/MeetingScribe)에 덮어씁니다.
3. 서버를 다시 켭니다.

회의록 폴더의 .md 파일은 앱 없이도 읽을 수 있는 회의 기록입니다.
"""

_UNSAFE = re.compile(r'[\\/:*?"<>|\n\r\t]+')


class _Sink(io.RawIOBase):
    """zip이 쓰는 바이트를 모았다가 조금씩 내보낸다 (되감기 없는 스트림)."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.pos = 0

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self.buf += b
        self.pos += len(b)
        return len(b)

    def tell(self) -> int:
        return self.pos

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _add_file(zf: zipfile.ZipFile, sink: _Sink, path: Path, arcname: str, compress: bool) -> Iterator[bytes]:
    info = zipfile.ZipInfo(arcname, date_time=time.localtime(path.stat().st_mtime)[:6])
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            dst.write(chunk)
            yield sink.take()
    yield sink.take()


def backup_name() -> str:
    return time.strftime("회의노트-백업-%Y%m%d-%H%M.zip")


def iter_backup(store: Store, views: Views) -> Iterator[bytes]:
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", allowZip64=True) as zf:
        snap = settings.data_dir / ".backup-snapshot.db"
        try:
            with sqlite3.connect(store.path) as src, sqlite3.connect(snap) as dst:
                src.backup(dst)
            yield from _add_file(zf, sink, snap, "scribe.db", compress=True)
        finally:
            snap.unlink(missing_ok=True)
        used: set[str] = set()
        for m in store.list_meetings():
            day = time.strftime("%Y-%m-%d", time.localtime(m["created_at"]))
            name = f"{day} {_UNSAFE.sub('_', (m['title'] or '제목 없는 회의').strip())[:60]}"
            while name in used:
                name += "_"
            used.add(name)
            md = meeting_markdown(views, m["id"], version="auto", include_summary=True)
            if md:
                zf.writestr(zipfile.ZipInfo(f"회의록/{name}.md", date_time=time.localtime()[:6]), md.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
                yield sink.take()
            audio = audio_file(settings.meetings_dir / m["id"])
            if audio is not None:
                yield from _add_file(zf, sink, audio, f"meetings/{m['id']}/{audio.name}", compress=False)
        zf.writestr("복원 방법.txt", RESTORE.encode("utf-8"))
    yield sink.take()

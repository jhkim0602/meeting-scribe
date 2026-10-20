"""기록을 지키는 일: DB 점검과 되살리기, 하루 한 번 DB 사본, 동기화 폴더(iCloud·OneDrive) 경고.

- 켤 때 DB를 점검해 깨졌으면 깨진 파일은 옆에 남기고 가장 최근 사본으로 되돌린다 (사본이 없으면 새로 시작)
- 하루 한 번 저장 폴더의 .backups/ 에 사본을 남긴다 (최근 7개). 녹음 중에는 하지 않는다
- 저장 폴더가 iCloud(맥 '데스크탑 및 문서')나 OneDrive 안이면 알린다: 쓰는 중인 DB를 동기화가 복사하다 깨뜨릴 수 있다
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

KEEP = 7


def backups_dir(db: Path) -> Path:
    return db.parent / ".backups"


def check_and_recover(db: Path) -> Optional[str]:
    """깨졌으면 되돌리고 알릴 문장을 돌려준다. 괜찮으면 None."""
    if not db.exists():
        return None
    try:
        with sqlite3.connect(db) as c:
            ok = c.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        ok = False
    if ok:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    broken = db.with_name(f"scribe.broken-{stamp}.db")
    db.rename(broken)
    for ext in ("-wal", "-shm"):
        side = db.with_name(db.name + ext)
        if side.exists():
            side.rename(broken.with_name(broken.name + ext))
    copies = sorted(backups_dir(db).glob("scribe-*.db"))
    if copies:
        shutil.copy2(copies[-1], db)
        day = copies[-1].stem.removeprefix("scribe-")
        return f"기록 DB가 깨져서 {day} 사본으로 되돌렸어요. 그 뒤에 쓴 내용은 없을 수 있어요. 깨진 파일은 {broken.name}로 남겨 뒀어요."
    return f"기록 DB가 깨져서 새로 시작했어요 (사본이 없었어요). 깨진 파일은 {broken.name}로 남겨 뒀어요. 녹음 파일은 그대로예요."


def backup_daily(db: Path) -> Optional[Path]:
    """오늘 사본이 없으면 만든다 (SQLite 백업: 쓰는 중에도 일관된 사본). 만든 파일."""
    folder = backups_dir(db)
    target = folder / f"scribe-{time.strftime('%Y-%m-%d')}.db"
    if target.exists() or not db.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        with sqlite3.connect(db) as src, sqlite3.connect(tmp) as dst:
            src.backup(dst)
        tmp.replace(target)
    except (sqlite3.Error, OSError) as exc:
        tmp.unlink(missing_ok=True)
        print(f"DB 사본을 만들지 못했어요: {exc}", file=sys.stderr, flush=True)
        return None
    for old in sorted(folder.glob("scribe-*.db"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return target


def sync_folder_warning(data_dir: Path) -> Optional[str]:
    """저장 폴더가 동기화 폴더 안이면 알릴 문장"""
    p = data_dir.expanduser().resolve()
    home = Path.home()
    if sys.platform == "darwin":
        icloud = home / "Library" / "Mobile Documents"
        synced = [icloud] + [home / name for name in ("Desktop", "Documents") if (icloud / "com~apple~CloudDocs" / name).exists()]
        if any(p == s or s in p.parents for s in synced):
            return "저장 폴더가 iCloud와 동기화되는 곳에 있어요. 쓰는 중인 기록 DB가 동기화 중에 깨질 수 있어요. 녹음이 없을 때 설치·설정 창에서 저장 위치를 iCloud 밖(예: 홈 폴더의 MeetingScribe)으로 옮겨 주세요."
    if any(part.lower().startswith("onedrive") for part in p.parts):
        return "저장 폴더가 OneDrive와 동기화되는 곳에 있어요. 쓰는 중인 기록 DB가 동기화 중에 깨질 수 있어요. 녹음이 없을 때 설치·설정 창에서 저장 위치를 OneDrive 밖으로 옮겨 주세요."
    return None

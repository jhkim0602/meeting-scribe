"""회의 폴더 이름: 저장 폴더 안에서 '날짜_제목'으로 보이게 한다 (예전에는 회의 id).

  회의록/
    2026-10-20_주간회의/
      audio.m4a            녹음
      전사 기록.html, 회의록.html   ('폴더 열기'를 누를 때 새로 만든다)
      .work/                작업 파일 (원본 스크립트 중간 결과, 로그) — 숨김
    scribe.db              기록 DB

왼쪽 목록의 폴더(정리용)는 앱 안에서만 쓴다 (DB의 group_id). 디스크의 회의 폴더는 언제나 meetings/ 바로 아래에 둔다.

폴더 이름은 DB(meetings.folder)에 적어 두고, 모든 곳이 settings.meeting_path(id)로 찾는다.
제목을 바꾸면 폴더 이름도 바꾸되, 녹음·원본 스크립트 작업 중에는 파일을 쓰고 있으므로 끝난 뒤에 바꾼다.
"""

from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from .config import settings

WORK = ".work"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_OS_JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}


def base_name(m: dict) -> str:
    """2026-10-20_주간회의 (파일 이름에 못 쓰는 글자는 빼고)"""
    day = time.strftime("%Y-%m-%d", time.localtime(m["created_at"]))
    title = " ".join(_UNSAFE.sub(" ", m.get("title") or "").split()).strip(".")[:80] or "회의"
    return f"{day}_{title}"


def work_dir(meeting_id: str) -> Path:
    """작업 파일 자리 (원본 스크립트 중간 결과, 작업 로그, 멈춤 표시)"""
    path = settings.meeting_path(meeting_id) / WORK
    path.mkdir(parents=True, exist_ok=True)
    return path


def unique_name(want: str, own: Optional[str] = None) -> str:
    """같은 이름의 폴더가 있으면 ' (2)', ' (3)' … (맥·윈도우는 대소문자를 구분하지 않으므로 exists()로 본다)"""
    name, n = want, 2
    while name != own and (settings.meetings_dir / name).exists():
        name, n = f"{want} ({n})", n + 1
    return name


def _rel(path: Path) -> str:
    return path.relative_to(settings.meetings_dir).as_posix()


def sync(store, meeting_id: str) -> Optional[str]:
    """폴더 이름을 '날짜_제목'에 맞춘다 (부르는 쪽이 녹음·작업 중이 아닐 때만 부른다). 바뀐 이름.
    meetings/ 아래 다른 폴더 안에 있으면(예전 시험판에서 옮긴 것) 바로 아래로 되돌린다.
    옮기지 못하면(윈도우: 다른 프로그램이 파일을 열고 있음) 그대로 두고 다음에 다시 한다 (서버가 1분마다)."""
    m = store.get_meeting(meeting_id)
    if m is None:
        return None
    current = settings.meeting_path(meeting_id)
    cur_parent, _, cur_base = _rel(current).rpartition("/")
    want = base_name(m)
    if not cur_parent and (cur_base == want or (cur_base.startswith(want + " (") and cur_base.endswith(")"))):
        if m.get("folder") != cur_base and current.exists():
            store.set_folder(meeting_id, cur_base)
        return None
    name = unique_name(want, None if cur_parent else cur_base)
    target = settings.meetings_dir / name
    try:
        if current.exists():
            current.rename(target)
        _tidy(target)
    except OSError as exc:  # 윈도우: 다른 프로그램이 폴더 안 파일을 열고 있으면 못 바꾼다 → 다음에 다시
        _log(f"회의 폴더 이름을 바꾸지 못했어요 ({_rel(current)} → {name}): {exc}")
        return None
    store.set_folder(meeting_id, name)
    if cur_parent:
        prune_empty(current.parent)
    return name


def prune_empty(folder: Path) -> None:
    """회의를 옮긴 뒤 비어 버린 정리용 폴더를 지운다 (위로 올라가며, meetings 폴더 자체는 남긴다).
    운영체제가 저절로 만드는 파일(.DS_Store, Thumbs.db, desktop.ini)만 있으면 빈 폴더로 본다."""
    root = settings.meetings_dir.resolve()
    try:
        folder = folder.resolve()
    except OSError:
        return
    while folder != root and root in folder.parents:
        try:
            names = [p.name for p in folder.iterdir()]
            if any(n not in _OS_JUNK for n in names):
                return
            for n in names:
                (folder / n).unlink()
            folder.rmdir()
        except OSError:
            return  # 윈도우: 탐색기가 그 폴더를 보고 있으면 못 지운다. 남아도 문제없다
        folder = folder.parent


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _tidy(mdir: Path) -> None:
    """예전 구조의 작업 파일(ckpt/, worker.log, pause)을 .work/ 로 옮긴다"""
    if not mdir.exists():
        return
    work = mdir / WORK
    for name in ("ckpt", "worker.log", "pause"):
        src = mdir / name
        if src.exists():
            work.mkdir(exist_ok=True)
            shutil.move(str(src), str(work / name))


def migrate_all(store, busy: set[str]) -> int:
    """켤 때 한 번: 모든 회의 폴더를 '날짜_제목'으로 (busy: 녹음·작업 중인 회의는 건너뛴다). 바꾼 수."""
    done = 0
    for m in store.list_meetings():
        if m["id"] not in busy and sync(store, m["id"]):
            done += 1
    return done

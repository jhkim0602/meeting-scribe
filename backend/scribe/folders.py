"""회의 폴더 이름: 저장 폴더 안에서 '날짜_제목'으로 보이게 한다 (예전에는 회의 id).

  회의록/
    2026-10-20_주간회의/
      audio.m4a            녹음
      전사 기록.html, 회의록.html   ('폴더 열기'를 누를 때 새로 만든다)
      .work/                작업 파일 (원본 스크립트 중간 결과, 로그) — 숨김
    scribe.db              기록 DB

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
    """같은 이름의 폴더가 있으면 ' (2)', ' (3)' …"""
    name, n = want, 2
    while name != own and (settings.meetings_dir / name).exists():
        name, n = f"{want} ({n})", n + 1
    return name


def sync(store, meeting_id: str) -> Optional[str]:
    """폴더 이름을 '날짜_제목'에 맞춘다 (부르는 쪽이 녹음·작업 중이 아닐 때만 부른다). 바뀐 이름."""
    m = store.get_meeting(meeting_id)
    if m is None:
        return None
    current = settings.meeting_path(meeting_id)
    want = base_name(m)
    if current.name == want or (current.name.startswith(want + " (") and current.name.endswith(")")):
        if m.get("folder") != current.name and current.exists():
            store.set_folder(meeting_id, current.name)
        return None
    name = unique_name(want, current.name)
    try:
        if current.exists():
            current.rename(settings.meetings_dir / name)
        _tidy(settings.meetings_dir / name)
    except OSError as exc:  # 윈도우: 다른 프로그램이 폴더 안 파일을 열고 있으면 못 바꾼다 → 다음에 다시
        print(f"회의 폴더 이름을 바꾸지 못했어요 ({current.name} → {name}): {exc}", file=sys.stderr, flush=True)
        return None
    store.set_folder(meeting_id, name)
    return name


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

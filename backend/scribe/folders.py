"""회의 폴더 이름: 저장 폴더 안에서 '날짜_제목'으로 보이게 한다 (예전에는 회의 id).

  회의록/
    2026-10-20_주간회의/
      audio.m4a            녹음
      전사 기록.html, 회의록.html   ('폴더 열기'를 누를 때 새로 만든다)
      .work/                작업 파일 (원본 스크립트 중간 결과, 로그) — 숨김
    scribe.db              기록 DB

왼쪽 목록에서 만든 폴더에 넣으면 디스크에서도 그 폴더 안으로 옮긴다: meetings/프로젝트 A/2026-10-20_주간회의/

폴더 경로는 DB(meetings.folder, meetings 아래 상대 경로)에 적어 두고, 모든 곳이 settings.meeting_path(id)로 찾는다.
제목을 바꾸면 폴더 이름도 바꾸되, 녹음·원본 스크립트 작업 중에는 파일을 쓰고 있으므로 끝난 뒤에 바꾼다.
"""

from __future__ import annotations

import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import Optional

from .config import settings

WORK = ".work"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_MEETING_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}_")
_OS_JUNK = {".DS_Store", "Thumbs.db", "desktop.ini"}
_INNER = 70  # 회의 폴더 안 가장 긴 파일 이름 여유 (.work/ckpt/… 등)


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


def dir_name(name: str) -> str:
    """사용자가 지은 폴더 이름 → 디스크 폴더 이름 (윈도우·맥 어디서나 쓸 수 있게).
    못 쓰는 글자(\\ / : * ? " < > |)와 끝의 점·띄어쓰기는 빼고, 윈도우 예약 이름(CON, NUL, COM1 …)은 피한다.
    맥은 한글 파일 이름을 자모로 나눠 돌려줄 수 있어(NFD) 비교할 때 NFC로 맞춘다."""
    n = unicodedata.normalize("NFC", name)
    n = " ".join(_UNSAFE.sub(" ", n).split())[:40].strip(". ") or "폴더"
    if n.split(".")[0].upper() in _RESERVED:
        n += "_"
    if _MEETING_DIR.match(n):  # 회의 폴더('날짜_제목')와 헷갈리지 않게
        n = "폴더 " + n
    return n


def group_rel(store, group_id: Optional[str]) -> str:
    """폴더의 디스크 경로 (저장 폴더/meetings 아래, '/'로 이어 붙임). 폴더 밖이면 ''."""
    parts: list[str] = []
    seen: set[str] = set()
    while group_id and group_id not in seen:
        seen.add(group_id)
        g = store.get_group(group_id)
        if g is None:
            break
        parts.append(dir_name(g["name"]))
        group_id = g["parent_id"]
    return "/".join(reversed(parts))


def unique_name(want: str, own: Optional[str] = None, parent: Optional[Path] = None) -> str:
    """같은 이름의 폴더가 있으면 ' (2)', ' (3)' … (맥·윈도우는 대소문자를 구분하지 않으므로 exists()로 본다)"""
    base = parent or settings.meetings_dir
    name, n = want, 2
    while name != own and (base / name).exists():
        name, n = f"{want} ({n})", n + 1
    return name


def too_long(path: Path) -> bool:
    """윈도우는 경로가 260자를 넘으면 파일을 못 다루는 경우가 많다 (회의 폴더 안 파일 이름 여유를 두고 본다)"""
    return sys.platform == "win32" and len(str(path)) + _INNER > 259


def _nfc(s: str) -> str:
    """경로 비교용: 맥의 자모 분리형(NFD)을 맞추고, 맥·윈도우는 대소문자를 구분하지 않으므로 그것도 맞춘다"""
    s = unicodedata.normalize("NFC", s)
    return s.casefold() if sys.platform in ("darwin", "win32") else s


def _rel(path: Path) -> str:
    return path.relative_to(settings.meetings_dir).as_posix()


def sync(store, meeting_id: str) -> Optional[str]:
    """폴더를 '폴더 경로/날짜_제목'에 맞춘다 (부르는 쪽이 녹음·작업 중이 아닐 때만 부른다). 바뀐 경로.
    옮기지 못하면(윈도우: 다른 프로그램이 파일을 열고 있음, 경로가 너무 김) 그대로 두고 다음에 다시 한다.
    앱의 목록은 DB(group_id)를 따르므로 디스크가 늦어도 화면은 바로 바뀐다."""
    m = store.get_meeting(meeting_id)
    if m is None:
        return None
    current = settings.meeting_path(meeting_id)
    cur_parent, _, cur_base = _rel(current).rpartition("/")
    want_parent = group_rel(store, m.get("group_id"))
    want = base_name(m)
    same_parent = _nfc(cur_parent) == _nfc(want_parent)
    if same_parent and (cur_base == want or (cur_base.startswith(want + " (") and cur_base.endswith(")"))):
        if m.get("folder") != _rel(current) and current.exists():
            store.set_folder(meeting_id, _rel(current))
        return None
    parent = settings.meetings_dir / want_parent if want_parent else settings.meetings_dir
    name = unique_name(want, cur_base if same_parent else None, parent)
    target = parent / name
    if too_long(target):
        _log(f"경로가 너무 길어 회의 폴더를 옮기지 않았어요 ({len(str(target))}자): {target}. 폴더 이름을 짧게 하거나 저장 위치를 바꿔 주세요.")
        return None
    try:
        if current.exists():
            parent.mkdir(parents=True, exist_ok=True)
            current.rename(target)  # 같은 저장 폴더 안이라 복사 없이 바로 옮겨진다
        _tidy(target)
    except OSError as exc:  # 윈도우: 다른 프로그램(탐색기 미리 보기, 재생 중인 녹음 등)이 파일을 열고 있으면 못 옮긴다 → 다음에 다시
        _log(f"회의 폴더를 옮기지 못했어요 ({_rel(current)} → {_rel(target)}): {exc}")
        return None
    store.set_folder(meeting_id, _rel(target))
    prune_empty(current.parent)
    return _rel(target)


def needs_sync(store, m: dict) -> bool:
    """디스크의 회의 폴더가 아직 목록의 폴더와 다르면 True (녹음·작업이 끝나거나 파일이 풀리면 맞춘다)"""
    cur_parent = _rel(settings.meeting_path(m["id"])).rpartition("/")[0]
    return _nfc(cur_parent) != _nfc(group_rel(store, m.get("group_id")))


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

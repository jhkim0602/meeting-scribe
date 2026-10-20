"""화면에 알릴 일: 서버가 스스로 판단해 한 일 (화자 모델을 CPU로 바꿈, DB를 사본으로 되돌림, 저장 위치 경고 …).

화면이 /api/notices?since=마지막 번호 로 가져가 알림으로 보여 준다. 서버 로그에도 같은 문장을 남긴다.
같은 key의 알림은 한 번만 (같은 일로 계속 알리지 않게).
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

_items: list[dict] = []
_keys: set[str] = set()
_last = 0
_lock = threading.Lock()


def add(text: str, level: str = "warn", key: Optional[str] = None) -> None:
    global _last
    with _lock:
        if key is not None:
            if key in _keys:
                return
            _keys.add(key)
        _last += 1
        _items.append({"id": _last, "level": level, "text": text, "at": time.time()})
        del _items[:-50]
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 알림: {text}", file=sys.stderr, flush=True)


def since(last_id: int) -> list[dict]:
    with _lock:
        return [n for n in _items if n["id"] > last_id]

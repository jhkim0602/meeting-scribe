"""로컬 전용 보호: 내 브라우저의 다른 사이트가 이 서버를 쓰지 못하게 한다.

- Host 검사: DNS 리바인딩(evil.example → 127.0.0.1)으로 API를 읽는 것을 막는다.
- Origin 검사: 다른 사이트 페이지가 브라우저를 통해 API나 녹음 WebSocket을 여는 것을 막는다.
  브라우저는 다른 출처의 요청과 모든 WebSocket에 Origin을 붙이므로 같은 출처만 통과시킨다.
Origin이 없는 요청(curl, 테스트, MCP 등 브라우저 밖)은 Host만 확인한다.
"""

from __future__ import annotations

from typing import Optional

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# 개발 서버(vite)에서 프록시로 들어오는 요청
DEV_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


def hostname(hostport: str) -> str:
    """'127.0.0.1:8765', '[::1]:8765', 'localhost' → 포트를 뺀 호스트 이름."""
    h = hostport.strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    if h.count(":") == 1:
        return h.split(":", 1)[0]
    return h


def allowed_hosts() -> set[str]:
    return LOCAL_HOSTS | set(settings.extra_hosts)


def check(host: str, origin: Optional[str], scheme: str = "http") -> Optional[str]:
    """막아야 하면 이유를, 괜찮으면 None을 돌려준다."""
    if hostname(host) not in allowed_hosts():
        return "이 서버는 이 컴퓨터(127.0.0.1)에서만 열 수 있어요"
    if origin is None:
        return None
    origin = origin.strip().lower().rstrip("/")
    same = {f"http://{host.strip().lower()}", f"https://{host.strip().lower()}"}
    if origin in same or origin in DEV_ORIGINS or origin in settings.extra_origins:
        return None
    return "다른 사이트에서 온 요청은 받지 않아요"


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        reason = check(headers.get("host", ""), headers.get("origin"))
        if reason is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            await PlainTextResponse(reason, status_code=403)(scope, receive, send)
        else:
            # accept 전에 닫으면 서버가 403으로 거절한다
            await send({"type": "websocket.close", "code": 1008, "reason": ""})

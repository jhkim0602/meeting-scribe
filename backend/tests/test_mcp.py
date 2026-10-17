"""MCP 서버를 실제 stdio로 띄워 도구 목록과 호출을 확인한다."""

import asyncio
import os
import sys
from pathlib import Path

from scribe.db import Store

BACKEND = Path(__file__).resolve().parents[1]


def test_mcp_tools_roundtrip(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    data = tmp_path / "d"
    store = Store(data / "scribe.db")
    m = store.create_meeting("MCP 테스트 회의")
    store.add_live_segment(m["id"], 0.0, 3.0, "예산은 다음 주까지 확정합니다")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "scribe.mcp_server"],
        env={**os.environ, "SCRIBE_DATA_DIR": str(data), "PYTHONPATH": str(BACKEND)},
        cwd=str(BACKEND),
    )

    async def run():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                assert {"list_meetings", "get_meeting_bundle", "save_summary", "get_notes", "search_meetings"} <= tools
                out = await s.call_tool("get_meeting_bundle", {"meeting_id": "latest"})
                text = out.content[0].text
                assert "MCP 테스트 회의" in text and "예산은 다음 주까지" in text
                saved = await s.call_tool("save_summary", {"meeting_id": m["id"], "markdown": "## 요약\n- 예산 확정"})
                assert "저장했습니다" in saved.content[0].text

    asyncio.run(run())
    assert store.get_meeting(m["id"])["summary_md"].startswith("## 요약")

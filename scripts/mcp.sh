#!/usr/bin/env bash
# Claude Desktop / Claude Code에서 부르는 MCP 서버 실행 스크립트 (stdio)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then set -a; source "$ROOT/.env"; set +a; fi
cd "$ROOT/backend"
export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "$ROOT/backend/.venv/bin/python" -m scribe.mcp_server

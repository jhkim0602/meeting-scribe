#!/usr/bin/env bash
# 서버 실행 후 브라우저 열기
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$ROOT/.env" ]]; then set -a; source "$ROOT/.env"; set +a; fi
PORT="${SCRIBE_PORT:-8765}"
cd "$ROOT/backend"
if [[ ! -x .venv/bin/python ]]; then
  echo "먼저 ./setup_mac.sh 를 실행해 주세요."
  exit 1
fi
URL="http://127.0.0.1:${PORT}"
# --no-open: 로그인할 때 자동 실행(LaunchAgent)처럼 브라우저를 열지 않는다
if [[ "${1:-}" != "--no-open" ]]; then
  ( sleep 2; command -v open >/dev/null 2>&1 && open "$URL" ) &
fi
echo "회의 노트: $URL  (종료: Ctrl+C)"
exec .venv/bin/python -m uvicorn scribe.main:app --host 127.0.0.1 --port "$PORT" --log-level warning

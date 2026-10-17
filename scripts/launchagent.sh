#!/usr/bin/env bash
# 로그인할 때 회의 노트 서버를 자동으로 켠다 (터미널을 열어 둘 필요 없음).
#   scripts/launchagent.sh install     켜기
#   scripts/launchagent.sh uninstall   끄기
#   scripts/launchagent.sh status      상태
# 서버 로그: ~/MeetingScribe/server.log
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then set -a; source "$ROOT/.env"; set +a; fi
LABEL="com.meeting-scribe.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DATA="${SCRIBE_DATA_DIR:-$HOME/MeetingScribe}"
PORT="${SCRIBE_PORT:-8765}"
DOMAIN="gui/$(id -u)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS에서만 쓸 수 있어요."
  exit 1
fi

xml_escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }

install() {
  mkdir -p "$HOME/Library/LaunchAgents" "$DATA"
  local root log
  root="$(xml_escape "$ROOT")"
  log="$(xml_escape "$DATA/server.log")"
  cat >"$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$root/run.sh</string>
    <string>--no-open</string>
  </array>
  <key>WorkingDirectory</key><string>$root</string>
  <key>RunAtLoad</key><true/>
  <!-- 비정상 종료되면 다시 켠다 (직접 끈 경우는 그대로) -->
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <!-- 녹음 중 실시간 받아쓰기가 느려지지 않게 일반 앱처럼 대우받는다 -->
  <key>ProcessType</key><string>Interactive</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
</dict>
</plist>
PLIST_EOF
  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  echo "켰어요. 이제 로그인하면 회의 노트 서버가 저절로 켜집니다."
  echo "브라우저: http://127.0.0.1:$PORT"
  echo "팁: Chrome에서 이 주소를 연 뒤 메뉴 → 저장 및 공유 → 바로가기 만들기 → '창으로 열기'를 고르면 앱처럼 Dock에서 열 수 있어요."
}

uninstall() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "자동 실행을 껐어요."
}

status() {
  if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    echo "자동 실행: 켜짐"
    curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && echo "서버: 응답함 (http://127.0.0.1:$PORT)" || echo "서버: 응답 없음 (로그: $DATA/server.log)"
  else
    echo "자동 실행: 꺼짐"
  fi
}

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  status) status ;;
  *) echo "사용법: $0 install | uninstall | status"; exit 1 ;;
esac

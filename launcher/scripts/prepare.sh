#!/usr/bin/env bash
# 앱에 넣을 것을 launcher/src-tauri/resources에 모은다: 서버 코드, 회의 노트 화면, 설치 도구(uv)
#   launcher/scripts/prepare.sh            이 기기용
#   launcher/scripts/prepare.sh aarch64-apple-darwin
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RES="$ROOT/launcher/src-tauri/resources"
UV_VERSION="${UV_VERSION:-0.12.21}"

case "${1:-auto}" in
  auto)
    case "$(uname -s)-$(uname -m)" in
      Darwin-arm64) TARGET=aarch64-apple-darwin ;;
      Darwin-x86_64) TARGET=x86_64-apple-darwin ;;
      Linux-x86_64) TARGET=x86_64-unknown-linux-gnu ;;
      Linux-aarch64) TARGET=aarch64-unknown-linux-gnu ;;
      *) echo "이 기기는 아직 지원하지 않아요: $(uname -s)-$(uname -m)"; exit 1 ;;
    esac ;;
  *) TARGET="$1" ;;
esac

echo "· 서버 코드"
rm -rf "$RES/app/backend" "$RES/app/frontend" "$RES/app/claude"
mkdir -p "$RES/app/backend" "$RES/bin"
cp "$ROOT"/backend/requirements*.txt "$RES/app/backend/"
cp -R "$ROOT/backend/scribe" "$RES/app/backend/scribe"
find "$RES/app/backend" -name "__pycache__" -type d -prune -exec rm -rf {} +

echo "· 회의록 스킬 (Claude Code에 복사해 쓰는 것)"
mkdir -p "$RES/app/claude"
cp -R "$ROOT/claude/skills" "$RES/app/claude/skills"

echo "· 회의 노트 화면"
if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  (cd "$ROOT/frontend" && npm ci && npm run build)
fi
cp -R "$ROOT/frontend/dist" "$RES/app/frontend"

echo "· 설치 도구 uv $UV_VERSION ($TARGET)"
if [[ ! -x "$RES/bin/uv" || "$("$RES/bin/uv" --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]]; then
  tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$TARGET.tar.gz" -o "$tmp/uv.tar.gz"
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  cp "$tmp/uv-$TARGET/uv" "$RES/bin/uv"
  chmod +x "$RES/bin/uv"
  rm -rf "$tmp"
fi
du -sh "$RES/app" "$RES/bin" | sed 's/^/  /'

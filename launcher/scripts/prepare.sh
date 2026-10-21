#!/usr/bin/env bash
# 앱에 넣을 것을 launcher/src-tauri/resources에 모은다: 서버 코드, 회의 노트 화면, 설치 도구(uv)
#   launcher/scripts/prepare.sh            이 기기용
#   launcher/scripts/prepare.sh aarch64-apple-darwin
#   launcher/scripts/prepare.sh x86_64-pc-windows-msvc   (윈도우: Git Bash에서)
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
      MINGW*-x86_64|MSYS*-x86_64) TARGET=x86_64-pc-windows-msvc ;;
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
UV="uv"; [[ "$TARGET" == *windows* ]] && UV="uv.exe"
if [[ ! -x "$RES/bin/$UV" || "$("$RES/bin/$UV" --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]]; then
  tmp="$(mktemp -d)"
  if [[ "$TARGET" == *windows* ]]; then
    # 윈도우용은 zip 안에 uv.exe만 들어 있다
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$TARGET.zip" -o "$tmp/uv.zip"
    unzip -q "$tmp/uv.zip" -d "$tmp"
    cp "$tmp/uv.exe" "$RES/bin/uv.exe"
  else
    curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$TARGET.tar.gz" -o "$tmp/uv.tar.gz"
    tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
    cp "$tmp/uv-$TARGET/uv" "$RES/bin/uv"
  fi
  chmod +x "$RES/bin/$UV"
  rm -rf "$tmp"
fi

if [[ "$TARGET" == *windows* ]]; then
  # 앱의 파이썬 옆에 둘 C++ 런타임 한 벌 (src-tauri/src/vcrt.rs). 시스템 런타임이 오래된 PC에서도 PyTorch가 켜지게.
  # 윈도우 앱을 빌드하는 Visual Studio(Build Tools)의 재배포용 폴더에서 가장 새 것을 그대로 가져온다.
  echo "· C++ 런타임 (msvcp140.dll 등)"
  vswhere="/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
  vs=""; [[ -x "$vswhere" ]] && vs="$("$vswhere" -latest -products '*' -property installationPath | tr -d '\r')"
  crt=""; [[ -n "$vs" ]] && crt="$(ls -d "$(cygpath -u "$vs")"/VC/Redist/MSVC/[0-9]*/x64/Microsoft.VC*.CRT 2>/dev/null | sort -V | tail -1 || true)"
  if [[ ! -f "$crt/msvcp140.dll" ]]; then
    echo "C++ 런타임을 찾지 못했어요. Visual Studio Build Tools의 'MSVC v143 - VS 2022 C++ x64/x86 빌드 도구'를 설치해 주세요."
    exit 1
  fi
  rm -rf "$RES/bin/vcrt"
  mkdir -p "$RES/bin/vcrt"
  cp "$crt"/*.dll "$RES/bin/vcrt/"
  echo "  $crt"
fi
du -sh "$RES/app" "$RES/bin" | sed 's/^/  /'

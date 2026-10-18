#!/usr/bin/env bash
# Apple Silicon 맥 설치 스크립트: Python 3.12 가상환경(ffmpeg 포함), MLX 음성인식 패키지, (선택) 모델 미리 받기
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

say() { printf "\n\033[1m%s\033[0m\n" "$*"; }

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "이 스크립트는 Apple Silicon(M1~M4) 맥용입니다. 다른 환경에서는 README의 '맥이 아닌 환경'을 참고하세요."
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew가 필요합니다. https://brew.sh 의 설치 명령을 먼저 실행해 주세요."
  exit 1
fi

say "1/5 uv 확인 (ffmpeg는 파이썬 패키지에 들어 있어요)"
command -v uv >/dev/null 2>&1 || brew install uv

say "2/5 Python 3.12 가상환경"
cd "$ROOT/backend"
[[ -d .venv ]] || uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-mac.txt

say "3/5 설정 파일"
cd "$ROOT"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ".env 파일을 만들었어요. 화자 분리를 쓰려면 HF_TOKEN을 채워 주세요."
fi

say "4/5 화면(프론트엔드)"
if [[ -f frontend/dist/index.html && "${1:-}" != "--build-frontend" ]]; then
  echo "미리 빌드된 화면을 사용합니다. (수정했다면 ./setup_mac.sh --build-frontend)"
else
  command -v npm >/dev/null 2>&1 || brew install node
  (cd frontend && npm ci && npm run build)
fi

say "5/5 모델 미리 받기 (Qwen3-ASR 8비트 0.6B·1.7B + 단어 정렬기, 약 4.7GB)"
read -r -p "지금 받을까요? 나중에 첫 녹음 때 받아도 됩니다(화면에 진행률이 보여요). [Y/n] " yn
if [[ "${yn:-Y}" =~ ^[Yy]$ ]]; then
  set -a; source .env; set +a
  (cd backend && .venv/bin/python -m scribe.download_models) || true
fi

say "설치 완료"
read -r -p "로그인할 때 서버를 자동으로 켤까요? (터미널을 열어 둘 필요가 없어요) [y/N] " auto
if [[ "${auto:-N}" =~ ^[Yy]$ ]]; then
  "$ROOT/scripts/launchagent.sh" install || true
fi
echo "실행: ./run.sh   (브라우저에서 http://127.0.0.1:8765 이 열려요)"
echo "맥 실기기 점검: (cd backend && .venv/bin/python -m scribe.selftest)"
echo "Claude 연결: claude mcp add meeting-scribe -- $ROOT/scripts/mcp.sh"

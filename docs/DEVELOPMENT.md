# 개발 문서

쓰는 법은 [README](../README.md), 무엇을 왜 만들었는지는 [사용자 시나리오](USER_SCENARIOS.md)에 있습니다.

## 어떻게 동작하나

```
브라우저 마이크 ──16kHz PCM──▶ FastAPI WebSocket ──▶ audio.wav (계속 이어 쓰기)
                                    │
                                    └─▶ 무음 기준 분할 → Qwen3-ASR-0.6B → 회의 기록의 문장 (+진행 중 문장)
                                                       └▶ 목소리 특징 → 임시 화자 A, B … (참석자를 고르면 같은 목소리 전체에)
사람이 고친 글자 ─▶ 같은 문장의 text를 바꾸고 처음 받아 적은 글은 text_orig에 남김
메모·댓글·강조 ─▶ 문장 id + 글자 위치. 문장을 고치면 글자 차이(difflib)대로 위치를 옮김
녹음 종료 ─▶ (녹음 없을 때, 기본은 전원 연결 시) 별도 프로세스: Qwen3-ASR-1.7B + 단어 정렬 → 화자 구분 → 원본 스크립트 (참고용 .md)
올린 파일 ─▶ 같은 프로세스로 받아 적으며 10분 구간마다 회의 기록에 바로 넣고, 끝나면 목소리 묶음 A, B …
```

**기록은 하나입니다.** 회의 중 받아 적은 문장(올린 파일이면 한 번 받아 적은 문장)에 사람이 고친 글자가 그대로 들어가고, 메모·댓글·강조는 그 문장 위에 문장 id와 글자 위치로 붙습니다. 다른 기록으로 옮겨 붙이는 일이 없으니, 받아쓰기가 크게 달라져도 자리가 무너지지 않습니다.

- **문장 고치기** (`record.edit_segment`): 고치기 전후 글자를 `difflib.SequenceMatcher`로 비교해, 그 문장에 걸린 범위의 시작은 남은 글자를 따라가고(바뀐 글자 안이면 바뀐 부분의 처음), 끝은 마지막 글자를 따라갑니다(바뀐 글자 안이면 바뀐 부분의 끝). 댓글 단 글자를 모두 지우면 댓글은 문장 전체로, 강조는 지웁니다.
- **원본 스크립트** (`export.script_markdown`): 후처리 결과는 `final_versions`에 마지막 것만 두고 Markdown으로만 내보냅니다. 화면, 문서, 검색, AI 요약의 기록은 모두 회의 기록을 씁니다. AI는 `get_reference_script`로 참고만 할 수 있습니다.
- **원본 스크립트는 회의를 방해하지 않습니다** (`jobs.JobManager`): 어떤 회의든 녹음하는 동안에는 시작하지 않고, 하던 작업은 잠시 멈춥니다(실시간 받아쓰기가 GPU를 먼저 씀). 녹음을 마쳐 저절로 시작한 작업은 전원이 연결돼 있을 때만 하고(설정의 "배터리로 쓸 때도 만들기", `auto_on_battery`를 켜면 배터리여도), 사용자가 직접 시킨 작업(만들기, 파일 올리기)은 배터리여도 합니다. 멈추기는 서버가 회의 폴더에 `pause` 파일을 두면 워커가 진행률을 알리는 때 스스로 기다리는 방식이라(동시에 돌던 화자 구분 프로세스는 SIGSTOP) DB를 잡은 채 멈추는 일이 없습니다. 기다리는 까닭은 `jobs.waiting`으로 화면에 보입니다.
- **올린 파일** (`record.add_window_rows`, `record.label_speakers`): 10분 구간을 받아 적을 때마다 문장 단위(문장 끝·1초 넘는 쉼, 20초 이하)로 기록에 넣고, 끝나면 화자 구분 결과를 문장마다 겹치는 시간으로 붙입니다. 이미 있는 구간은 건드리지 않아 다시 받아 적거나 멈췄다 이어 해도 사람이 고친 기록은 그대로입니다.
- **긴 회의에서도 가볍게**: 기록이 바뀐 횟수(`meetings.record_rev`, 문장이 늘거나 고쳐질 때 오름)로 글자 색인을 캐시하고, 문장 고치기와 메모·댓글 하나를 다룰 때는 그 항목이 덮는 문장만 읽어 작은 색인을 만듭니다(`Views.local_index`).
- **예전 데이터**: 서버를 켤 때 한 번, 예전 "수정 제안"을 실시간 기록의 직접 고친 글자로 바꿉니다 (`record.apply_legacy_edits`).

| 구성 | 기본값 | 바꾸는 법 (`.env`) |
|---|---|---|
| 실시간 받아쓰기 | Qwen3-ASR-0.6B (MLX, 8bit) | `SCRIBE_LIVE_MODEL` |
| 원본 스크립트 받아쓰기 | Qwen3-ASR-1.7B + ForcedAligner (MLX, 8bit) | `SCRIBE_FINAL_MODEL` |
| 대안 엔진 | whisper-large-v3-turbo (mlx-whisper) | `SCRIBE_LIVE_ENGINE=whisper` |
| 화자 구분 (원본 스크립트·올린 파일) | speakrs(설치 시) → pyannote community-1 | `SCRIBE_DIARIZER` |
| 회의 중 화자 묶기 | pyannote wespeaker 목소리 특징 | `SCRIBE_LIVE_SPK_SAME` (0.5) |
| 데이터 | `~/MeetingScribe` (SQLite + WAV/m4a) | `SCRIBE_DATA_DIR` |

용어 목록을 받아쓰기 모델에 힌트로 넣는 기능은 없앴습니다 (말이 없는 짧은 소리에서 목록을 그대로 받아 적는 일이 있었다).

## 긴 회의 (3~5시간)

- **보이는 문장만 그립니다.** 5시간 기록(3,000문장 이상)도 화면에는 수십 개만 그려서 스크롤과 탭 전환이 가볍습니다.
- **⌘F는 기록 안에서 찾기**로 바뀝니다. 띄어쓰기는 무시하고 찾고(“예산숫자”로 “예산 숫자”도 찾음), Enter/Shift+Enter로 이동합니다.
- **구간 목차:** 구분선(쉬는 시간, 2부 등)이 있으면 탭 오른쪽에 목차 버튼이 생깁니다. 재생 막대에도 댓글(노랑)과 구분선(회색) 눈금이 옅게 보입니다.
- **바뀐 것만 다시 그립니다.** 댓글을 달거나 문장을 고치면 서버가 바뀐 항목만 돌려주고 화면은 그것만 바꿔 끼웁니다. 고친 글자는 저장을 기다리지 않고 바로 보입니다.
- **녹음 중에도 조용합니다.** 소리 크기(1초에 10번 넘게 바뀜)는 마이크 아이콘의 CSS 변수만 바꾸고, 기록·제목줄·사이드바는 녹음 상태가 바뀔 때만 다시 그립니다.
- **노트 편집기는 따로 불러옵니다.** 첫 화면 JS가 약 0.45MB(이전 1.5MB)이고, 노트 편집기(BlockNote)는 첫 화면 뒤 쉬는 틈에 받아 둡니다.
- **원본 스크립트는 10분 단위로 만듭니다.** 구간마다 결과를 저장해 두므로, 만드는 중에 취소하거나 이어서 녹음해도 끝난 구간은 다시 하지 않습니다.
- **문서:** 전사 기록은 화면 밖 발언을 그리지 않고(`content-visibility`), 메모·댓글 모아 보기는 20개가 넘으면 접어 둡니다. 글꼴은 기다리지 않고 시스템 글꼴로 먼저 보여 줍니다.

3시간 회의, 문장 2,103개, 메모·댓글·강조 약 2,000개 (이 개발 서버, 시연용 엔진, 브라우저 기준):

| 측정 | 결과 |
|---|---|
| 앱 첫 화면 | 0.2초 (스크립트 실행 0.05초, 이전 0.3초 / 0.11초) |
| 기록 첫 표시 | 0.45초 (화면 요소 약 900개, 이전 0.57초) |
| 스크롤 | 61fps |
| 탭 전환 (AI 요약 → 기록) | 0.19초 |
| 문장 고치기 Enter → 화면 반영 / 서버 저장 끝 | 0.02~0.05초 / 0.03~0.05초 (서버 처리 5ms, 이전 84ms) |
| 메모·댓글 달기 (서버) | 2ms (이전 15ms) |
| 녹음 중 화면 다시 그리기 | 1초에 3번, 스크립트 13ms/초 (이전 12.6번, 36ms/초) |
| ⌘F 찾기 | 0.15초 |
| 가장 긴 멈춤 | 0.09초 |
| 전사 기록 문서: 만들기 / 첫 화면 / 가장 긴 멈춤 | 0.1초 / 0.24초 / 0.08초 |

예전 표의 "문장 고치기 0.01초"는 잘못 잰 값이었습니다(화면에 이미 고친 문장이 있어 바로 통과). 고치기 전 실제 값은 0.10~0.21초였습니다.

## M3 Pro 18GB 기준 예상

| 항목 | 예상 |
|---|---|
| 실시간 자막 지연 | 1~2초 |
| 모델 내려받기 | 약 4.7GB (8bit 기본) · fp16이면 약 8.4GB |
| 메모리 사용 | 약 4GB (8bit 실시간 + 원본 스크립트 동시) · fp16이면 약 7GB. 화자 구분은 녹음을 메모리에 한 번만 올립니다(3시간 약 0.7GB, speakrs는 파일을 직접 읽어 0) |
| 원본 스크립트 만들기 | 1시간 녹음당 약 15분 (받아쓰기와 화자 구분을 동시에). 녹음 없을 때만(기본은 전원 연결 시), 녹음하면 멈췄다 이어 함 |
| 녹음 파일 | 녹음 중 WAV 1시간 약 115MB → 다 만든 뒤 m4a 1시간 약 29MB (5시간 약 0.14GB) |

M4 Pro 공개 수치를 M3 Pro 메모리 대역폭 비율로 환산한 추정치입니다. `python -m scribe.selftest`로 직접 확인해 보세요.

## 가볍게 운영하기

| 기능 | 하는 일 | 바꾸는 법 (`.env`) |
|---|---|---|
| 8bit 모델 | mlx-qwen3-asr가 검증해 올린 8bit 모델을 기본으로 씁니다 (영어 기준 fp16과 같은 결과, 크기 절반 이하). 한국어는 `selftest --compare`로 확인하세요 | `SCRIBE_PRECISION=fp16` |
| 녹음 파일 줄이기 | 녹음을 마치고 6시간 손대지 않은 회의는 WAV를 m4a로 줄입니다(원본 스크립트를 만들었는지와 상관없이, 녹음 중에는 쉼). 재생·다시 만들기·이어서 녹음은 그대로 됩니다 | `SCRIBE_COMPRESS_AFTER_H` (음수면 끔) |
| 올린 파일 사본 | 다 받아 적으면 앱 안의 사본은 지웁니다 (원래 파일은 그대로) | - |
| 원본 스크립트 버전 | 마지막 것만 남깁니다 | - |
| 원본 스크립트 시점 | 전원이 연결돼 있고 녹음하지 않을 때 저절로. 녹음을 시작하면 하던 작업도 멈췄다가 끝나면 이어 합니다 | 설정 → 저절로 만들기 끄기 (⋯ → 만들 때만), 배터리로 쓸 때도 만들기 |
| 쉬는 모델 내리기 | 녹음 없이 30분이 지나면 실시간 모델을 메모리에서 내리고, 앱을 다시 보면 미리 올립니다 | `SCRIBE_LIVE_IDLE_UNLOAD_MIN` (0이면 안 내림) |
| 배터리 절약 | 배터리로 쓸 때는 진행 중 문장을 2초마다 갱신합니다. 받아 적기가 느리면 간격을 스스로 늘립니다 | - |
| 동시 처리 | 10분이 넘는 녹음은 화자 구분을 받아쓰기와 동시에 돌립니다 | `SCRIBE_PARALLEL_DIARIZE_MIN_S` |
| 검색 | 모든 회의의 기록(고친 글자 포함)·노트·제목을 SQLite 색인(FTS5)으로 찾습니다 | - |
| 목소리 기억 | 회의 중 참석자를 고르면 그 목소리를 기억해, 다음 회의에서 같은 이름을 넣으면 처음부터 알아봅니다 | - |
| 백업 | 설정 → "전체 백업 내려받기": DB, 녹음, 사람이 읽는 회의록(.md)을 zip 하나로 | - |
| Docker | 쓰지 않습니다. 맥의 Docker에서는 GPU(Metal)를 쓸 수 없어 받아쓰기가 크게 느려집니다 | - |

## 맥 앱 (launcher/)

터미널 없이 쓰는 작은 앱입니다(Tauri 2: Rust + React). 앱에는 서버 코드, 회의 화면, 설치 도구 [uv](https://github.com/astral-sh/uv)만 들어 있고, 처음 열 때 파이썬·패키지는 앱 전용 폴더(`~/Library/Application Support/com.meetingscribe.app`)에, 모델은 `~/.cache/huggingface`에 받습니다. 회의 화면은 지금처럼 브라우저에서 열고, 앱은 메뉴 막대에서 서버를 켜 두는 제어판입니다.

```
앱 열기 ─▶ config.json에 setup_done이 없으면 설치 도우미, 있으면 제어판 (메뉴 막대 아이콘, 창을 닫아도 남음)
설치    ─▶ uv를 앱 전용 폴더에 새로 써서(격리 표시 피하기) → uv venv (Python 3.12, uv가 받음)
           → uv pip install -r requirements-mac.txt → python -m scribe.installer download (JSON 줄 진행률) → doctor
           앱이 새 버전이라 requirements가 바뀌면(packages_rev) 패키지 단계만 다시
서버    ─▶ 앱 전용 파이썬으로 uvicorn 127.0.0.1:8765. 이미 회의 노트 서버가 응답하면(launchagent 등) 그것을 씀
           SCRIBE_CONFIG=config.json → 서버가 모델·저장 위치·토큰을 환경변수 기본값으로 씀 (catalog.config_env, 직접 준 환경변수가 이김)
           PYTHONPYCACHEPREFIX로 서명된 앱 안에 .pyc를 쓰지 않음
끄기    ─▶ 녹음 중이면 묻고, SIGTERM → 서버가 녹음 파일을 마무리하고 끝남 (8초 뒤에도 살아 있으면 SIGKILL)
```

- 모델 목록은 `backend/scribe/models.json` 하나를 설치 도우미와 서버가 같이 읽습니다(크기, 권장 메모리, 메모리별 기본 선택).
- 설치 도우미의 Claude 연결은 `scribe.installer claude connect`가 `claude_desktop_config.json`에 MCP 서버를 더합니다(원래 파일은 `.bak`으로 남김).
- 서명: 애드혹(`signingIdentity: "-"`). Apple Silicon에서 실행에 필요한 최소 서명이고, 공증이 없어 처음 한 번 "그래도 열기"가 필요합니다. 디스크 이미지나 다운로드 폴더에서 바로 열면(App Translocation) 앱이 옮기라고 알립니다.
- 빌드: `.github/workflows/mac-app.yml` (macos-14). 앱 코드가 바뀐 main 푸시마다 .dmg를 만들고, 만든 앱으로 설치(모델 없이)·서버 켜기·점검을 돌린 뒤 서명이 그대로인지 확인합니다. `v0.1.0` 같은 태그를 올리면 릴리스에 붙습니다.

```bash
# 화면만 (Tauri 없이 브라우저에서, 가짜 응답): http://localhost:1420/?ram=18  ?installed=1  ?fail=packages  ?misplaced=1
cd launcher && npm install && npx vite --port 1420

# 앱에 넣을 것 모으기 (서버 코드, frontend/dist, uv) → launcher/src-tauri/resources
launcher/scripts/prepare.sh                 # 이 기기용, 맥 빌드는 aarch64-apple-darwin
cd launcher/src-tauri && cargo test

# 창 없이 설치·서버·점검 (CI와 같은 시험, 맥이 아니어도 됨)
SCRIBE_LIVE_ENGINE=mock SCRIBE_FINAL_ENGINE=mock SCRIBE_DIARIZER=mock \
  ./target/debug/meeting-scribe --headless install --support /tmp/ms --resources $(pwd)/resources --set data_dir=/tmp/ms-data
./target/debug/meeting-scribe --headless serve  --support /tmp/ms --resources $(pwd)/resources
./target/debug/meeting-scribe --headless doctor --support /tmp/ms --resources $(pwd)/resources

# 앱 창으로 (개발): MEETING_SCRIBE_RESOURCES로 resources 위치를 바꿀 수 있음
cd launcher && npx tauri dev
# 맥에서 .dmg 만들기
cd launcher && npm ci && npx tauri build --target aarch64-apple-darwin --bundles app,dmg
```

## 개발

```bash
# 백엔드 테스트 (모델 없이 시연용 엔진으로 전체 흐름 검증)
cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q

# 시연용 엔진으로 서버 실행 (맥이 아니어도 됨)
SCRIBE_LIVE_ENGINE=mock SCRIBE_FINAL_ENGINE=mock SCRIBE_DIARIZER=mock .venv/bin/python -m uvicorn scribe.main:app --port 8765

# 화면 개발 (Vite, /api는 8765로 프록시)
cd frontend && npm install && npm run dev
npx tsc -b && npm run build     # 타입 검사와 배포용 빌드 (frontend/dist)

# 브라우저 E2E (가짜 마이크)
python scripts/e2e/make_fake_audio.py scripts/e2e/out/speech.wav
python scripts/e2e/make_quiet_audio.py scripts/e2e/out/quiet.wav
python scripts/e2e/make_quiet_audio.py scripts/e2e/out/zero.wav zero
node scripts/e2e/run.cjs        # 댓글·노트 흐름, 문장 고치기(E, 고르고 고치기, 처음대로), 원본 스크립트, ⌘F
node scripts/e2e/ui.cjs         # 녹음 표시줄, 단축키, 되돌리기, 보기 설정
node scripts/e2e/speakers.cjs   # 임시 화자, 참석자, 숫자키, 녹음 뒤에도 그대로 (빈 데이터 폴더에서)
node scripts/e2e/docs.cjs       # 참석자 정보, 전사 기록·회의록 보기·받기, 메모 자리, 시각 눌러 듣기
SCRIBE_MIN_BREAK_S=2 로 서버를 띄운 뒤 RESTART_CMD="서버 재시작 명령" node scripts/e2e/breaks.cjs   # 예외 처리 흐름
SCRIBE_WINDOW_S=20 SCRIBE_MOCK_FINAL_DELAY_S=2 SCRIBE_POWER=battery SCRIBE_PARALLEL_DIARIZE_MIN_S=100000 로 띄운 뒤 node scripts/e2e/jobs.cjs   # 올린 파일이 구간마다 보임, 녹음 중 멈춤, 배터리면 기다림
```

맥이 아닌 환경에서는 `pip install faster-whisper`를 하면 `faster-whisper` 엔진이 자동으로 선택됩니다.

```
backend/scribe/
  main.py            REST API, 녹음 WebSocket, 화면 파일 제공
  recording.py       녹음 세션 (파일 저장 + 실시간 받아쓰기 + 임시 화자)
  live.py            무음 기준 문장 분할, 진행 중 문장
  live_speakers.py   임시 화자 (목소리 묶음 → 참석자), 목소리 기억
  record.py          문장 고치기와 위치 옮기기, 올린 파일 → 구간마다 기록 + 화자 묶음, 예전 수정 제안 정리
  anchors.py         메모·댓글 위치 (문장 id + 글자 위치), 글자 차이 계산
  views.py           기록 + 메모·댓글 + 화자를 화면용으로
  jobs.py            작업 대기열: 녹음 중·배터리면 기다리거나 멈추게 함, 녹음 파일 줄이기
  worker.py          원본 스크립트 만들기 / 올린 파일 받아 적기 (별도 프로세스, 진행률 기록, 멈춤 신호 따름)
  merge.py           단어 타임스탬프 + 화자 구간 → 화자별 발화 (원본 스크립트)
  export.py          Markdown 내보내기 (AI가 읽는 형식), 원본 스크립트
  transcript_html.py 전사 기록 문서
  minutes.py         회의록 문서, 두 문서의 공통 틀
  mcp_server.py      Claude용 MCP 서버
  installer.py       설치 도우미가 부르는 것: 모델 받기(JSON 진행률), 토큰 확인, Claude 연결, 점검
  catalog.py         모델 목록(models.json): 메모리별 기본 선택, 받을 크기, config.json → 환경변수
  search.py          전체 검색 (FTS5)
  engines/           qwen3_mlx, whisper_mlx, faster_whisper, diarizers, mock
frontend/src/
  components/        Sidebar, TranscriptView(기록·고치기·메모·댓글), NotesPane(BlockNote), Dock(녹음/재생) …
  lib/               api, store(zustand), model(표시 조각), turns(화자 단락), speakers …
launcher/
  src/               설치 도우미(Wizard), 설치 진행(Install), 제어판(Home)
  src-tauri/src/     main(창·메뉴 막대·명령), install(설치 단계), server(서버 켜기·끄기), config, paths, sys(칩·메모리·공간)
  scripts/prepare.sh 앱에 넣을 것 모으기
```

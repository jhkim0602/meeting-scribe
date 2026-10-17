# 회의 노트 (Meeting Scribe)

맥북 안에서만 돌아가는 **클로바노트 + 노션**입니다.

- 왼쪽에는 회의 소리가 실시간으로 글자가 되어 올라옵니다. 그 위에 바로 **댓글·수정·강조·메모**를 남깁니다.
- 오른쪽에는 노션처럼 자유롭게 **필기**합니다. 타임스탬프를 넣으면 눌러서 그 시점을 다시 들을 수 있습니다.
- 녹음을 마치면 더 큰 모델이 처음부터 다시 받아 적고 **화자를 나눈 원본**을 만듭니다. 회의 중에 남긴 첨삭은 원본의 **같은 자리에 빨간 펜 첨삭처럼** 다시 표시됩니다.
- 3~5시간짜리 녹음 파일도 끌어다 놓으면 같은 화면으로 처리합니다.
- 요약은 내가 구독한 **Claude가 MCP로 읽고** 만들어 앱의 "AI 요약" 탭에 저장합니다.

| 실시간 기록 위에 첨삭 + 노트 | 원본(화자 구분) 위에 옮겨진 첨삭 |
|---|---|
| ![실시간](docs/screenshots/live.png) | ![원본](docs/screenshots/final.png) |

## 빠른 시작 (Apple Silicon 맥)

```bash
./setup_mac.sh      # ffmpeg, Python 3.12, Qwen3-ASR(MLX) 설치 + 모델 미리 받기(선택)
./run.sh            # http://127.0.0.1:8765 이 열립니다
```

화자 구분까지 쓰려면 한 번만 준비합니다.

1. https://huggingface.co/pyannote/speaker-diarization-community-1 에서 약관에 동의합니다.
2. https://huggingface.co/settings/tokens 에서 Read 토큰을 만들어 `.env`의 `HF_TOKEN=`에 붙여 넣습니다.

토큰이 없어도 전사는 되고, 원본에서 화자만 나뉘지 않습니다.

## 쓰는 법

| 하고 싶은 것 | 방법 |
|---|---|
| 녹음 시작 | 왼쪽 "새 회의 녹음" 또는 아래 빨간 버튼. 마이크는 아래 바에서 고릅니다 |
| 지금 순간에 메모 | 녹음 중 화면 아래 입력창에 쓰고 Enter |
| 문장에 댓글·수정·강조 | 기록에서 글자를 드래그 → 떠오르는 버튼 |
| 문장 사이에 메모 | 문장에 마우스를 올리면 왼쪽 아래 "+ 메모" |
| 기록을 노트로 인용 | 드래그 → "노트로" |
| 노트에 시각 넣기 | 노트 위 "시각 넣기" 또는 `/타임스탬프` |
| 원본 보기 | "원본" 탭. 녹음을 끝내면 자동으로 만들어집니다 |
| 고친 내용을 원본에 반영 | 원본의 빨간 첨삭을 눌러 "원본에 적용" 또는 "무시" |
| 화자 이름 바꾸기 | 원본의 "화자 1"을 눌러 이름 입력 |
| 파일 처리 | 왼쪽 "녹음 파일 올리기" 또는 첫 화면에 끌어다 놓기 |
| 내보내기 | 오른쪽 위 ⋯ → Markdown으로 내보내기 |

일시정지 후 이어서 녹음하거나, 끝난 회의에 "이어서 녹음"을 눌러 같은 파일 뒤에 덧붙일 수 있습니다.

## Claude로 요약하기 (MCP + 스킬)

앱은 회의 데이터를 MCP 서버로 내어 줍니다. 내 Claude 구독으로 요약하고, 결과를 앱에 저장합니다.

**Claude Code**
```bash
claude mcp add meeting-scribe -- /경로/meeting-scribe/scripts/mcp.sh
mkdir -p ~/.claude/skills && cp -r claude/skills/meeting-minutes ~/.claude/skills/
```

**Claude Desktop**: 설정 → 개발자 → 구성 편집에서 아래를 추가하고 재시작합니다.
```json
{ "mcpServers": { "meeting-scribe": { "command": "/경로/meeting-scribe/scripts/mcp.sh" } } }
```
회의록 스킬은 `claude/skills/meeting-minutes` 폴더를 zip으로 묶어 설정 → 기능 → 스킬에서 올립니다.

그다음 Claude에게 "방금 회의 회의록으로 정리해줘"라고 말하면 됩니다.

| MCP 도구 | 하는 일 |
|---|---|
| `list_meetings` / `search_meetings` | 회의 목록, 전체 검색 |
| `get_meeting_bundle` | 노트 + 첨삭 + 전사를 한 번에 (요약용, 긴 회의는 이어 읽기 안내) |
| `get_transcript` / `get_notes` / `get_annotations` | 부분만 읽기 |
| `save_summary` | 요약을 앱의 "AI 요약" 탭에 저장 |
| `rename_speakers` | 화자 라벨에 이름 붙이기 |

`meeting_id` 자리에 `latest`를 쓰면 가장 최근 회의입니다.

## 어떻게 동작하나

```
브라우저 마이크 ──16kHz PCM──▶ FastAPI WebSocket ──▶ audio.wav (계속 이어 쓰기)
                                    │
                                    └─▶ 무음 기준 분할 → Qwen3-ASR-0.6B → 실시간 문장 (+진행 중 문장)
녹음 종료 ─▶ 별도 프로세스: Qwen3-ASR-1.7B + 단어 정렬 → pyannote 화자 구분 → 화자별 발화 = 원본
첨삭(메모/댓글/수정) ─▶ "오디오 시간 + 인용문"으로 저장 → 원본에서 시간 창 안의 가장 비슷한 문장에 다시 붙임
```

첨삭 위치를 글자 번호가 아니라 **오디오 시간과 인용문**으로 저장하는 것이 핵심입니다. 실시간 기록과 원본은 띄어쓰기, 숫자 표기, 문장 경계, 화자 분할이 모두 다르지만, 같은 시간대에서 인용문과 가장 비슷한 부분을 찾아 같은 자리에 표시합니다. 문장이 너무 달라 찾지 못하면 시간만으로 위치를 추정하고 "위치 추정" 표시를 붙입니다.

| 구성 | 기본값 | 바꾸는 법 (`.env`) |
|---|---|---|
| 실시간 받아쓰기 | Qwen3-ASR-0.6B (MLX) | `SCRIBE_LIVE_MODEL` |
| 원본 받아쓰기 | Qwen3-ASR-1.7B + ForcedAligner (MLX) | `SCRIBE_FINAL_MODEL` |
| 대안 엔진 | whisper-large-v3-turbo (mlx-whisper) | `SCRIBE_LIVE_ENGINE=whisper` |
| 화자 구분 | speakrs(설치 시) → pyannote community-1 | `SCRIBE_DIARIZER` |
| 데이터 | `~/MeetingScribe` (SQLite + WAV) | `SCRIBE_DATA_DIR` |

설정 → "자주 나오는 용어·이름"에 사람 이름이나 전문 용어를 적으면 받아쓰기에 힌트로 들어갑니다.

## M3 Pro 18GB 기준 예상

| 항목 | 예상 |
|---|---|
| 실시간 자막 지연 | 1~2초 |
| 메모리 사용 | 약 6GB (실시간 + 원본 모델 동시) |
| 5시간 파일 원본 만들기 | 약 35~45분 (받아쓰기) + 약 20분 (pyannote) |

M4 Pro 공개 수치를 M3 Pro 메모리 대역폭 비율로 환산한 추정치입니다. 첫 실행 때 10분짜리 녹음으로 직접 확인해 보세요.

## 개발

```bash
# 백엔드 테스트 (모델 없이 시연용 엔진으로 전체 흐름 검증)
cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q

# 시연용 엔진으로 서버 실행 (맥이 아니어도 됨)
SCRIBE_LIVE_ENGINE=mock SCRIBE_FINAL_ENGINE=mock SCRIBE_DIARIZER=mock .venv/bin/python -m uvicorn scribe.main:app --port 8765

# 화면 개발 (Vite, /api는 8765로 프록시)
cd frontend && npm install && npm run dev

# 브라우저 E2E (가짜 마이크)
python scripts/e2e/make_fake_audio.py scripts/e2e/out/speech.wav
node scripts/e2e/run.cjs
```

맥이 아닌 환경에서는 `pip install faster-whisper`를 하면 `faster-whisper` 엔진이 자동으로 선택됩니다.

```
backend/scribe/
  main.py          REST API, 녹음 WebSocket, 화면 파일 제공
  recording.py     녹음 세션 (파일 저장 + 실시간 전사)
  live.py          무음 기준 문장 분할, 진행 중 문장
  worker.py        원본 만들기 (별도 프로세스, 진행률 기록)
  anchors.py       첨삭 위치 저장/재배치 (시간 + 인용문)
  merge.py         단어 타임스탬프 + 화자 구간 → 화자별 발화
  export.py        Markdown 내보내기 (AI가 읽는 형식)
  mcp_server.py    Claude용 MCP 서버
  engines/         qwen3_mlx, whisper_mlx, faster_whisper, diarizers, mock
frontend/src/
  components/      Sidebar, TranscriptView(첨삭), NotesPane(BlockNote), Dock(녹음/재생) …
```

## 알려진 제한

- 실시간 기록에는 화자 구분이 없습니다. 화자는 원본에서 나뉩니다.
- 화자 구분은 5명 이상이거나 여러 명이 동시에 말할 때 정확도가 떨어집니다.
- 실제 MLX 엔진 코드는 패키지 API(mlx-qwen3-asr 0.4.4)에 맞춰 작성했지만, 개발 환경이 리눅스라 맥 실기기 실행은 아직 확인하지 않았습니다. 처음 실행 후 문제가 있으면 `~/MeetingScribe/meetings/<회의ID>/worker.log`를 확인해 주세요.
- 녹음 동의는 참석자에게 꼭 받아 주세요.

"""환경 변수 기반 설정. 모든 값은 SCRIBE_* 환경 변수로 덮어쓸 수 있다."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("SCRIBE_DATA_DIR", str(Path.home() / "MeetingScribe"))).expanduser())
    host: str = field(default_factory=lambda: _env("SCRIBE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("SCRIBE_PORT", "8765")))
    language: str = field(default_factory=lambda: _env("SCRIBE_LANGUAGE", "ko"))
    # 엔진 선택: auto | qwen3 | whisper | faster-whisper | mock
    live_engine: str = field(default_factory=lambda: _env("SCRIBE_LIVE_ENGINE", "auto"))
    final_engine: str = field(default_factory=lambda: _env("SCRIBE_FINAL_ENGINE", "auto"))
    # 모델 ID (엔진별 기본값은 engines/__init__.py 참고)
    live_model: str = field(default_factory=lambda: _env("SCRIBE_LIVE_MODEL", ""))
    final_model: str = field(default_factory=lambda: _env("SCRIBE_FINAL_MODEL", ""))
    # 화자 분리: auto | speakrs | pyannote | none | mock
    diarizer: str = field(default_factory=lambda: _env("SCRIBE_DIARIZER", "auto"))
    hf_token: str = field(default_factory=lambda: _env("HF_TOKEN", os.environ.get("HUGGINGFACE_TOKEN", "")))
    # 127.0.0.1/localhost 외에 허용할 Host, Origin (쉼표로 구분, 보통은 비워 둔다)
    extra_hosts: tuple = field(default_factory=lambda: tuple(h.strip().lower() for h in _env("SCRIBE_ALLOWED_HOSTS", "").split(",") if h.strip()))
    extra_origins: tuple = field(default_factory=lambda: tuple(o.strip().lower().rstrip("/") for o in _env("SCRIBE_ALLOWED_ORIGINS", "").split(",") if o.strip()))
    frontend_dist: Path = field(default_factory=lambda: Path(_env("SCRIBE_FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "scribe.db"

    @property
    def meetings_dir(self) -> Path:
        return self.data_dir / "meetings"

    def meeting_dir(self, meeting_id: str) -> Path:
        path = self.meetings_dir / meeting_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.meetings_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()

SAMPLE_RATE = 16000

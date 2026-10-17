"""모델 미리 내려받기: python -m scribe.download_models

처음 녹음할 때 기다리지 않도록 Qwen3-ASR(실시간 0.6B, 원본 1.7B), 단어 정렬기,
화자 분리 모델(pyannote community-1, Hugging Face 토큰 필요)을 받아 둔다.
"""

from __future__ import annotations

import os
import sys

from .config import settings
from .engines import DEFAULT_MODELS, resolve_diarizer_kind, resolve_engine_kind

ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"
PYANNOTE = "pyannote/speaker-diarization-community-1"


def main() -> int:
    from huggingface_hub import snapshot_download

    targets: list[str] = []
    for role in ("live", "final"):
        kind = resolve_engine_kind(settings.live_engine if role == "live" else settings.final_engine)
        model = (settings.live_model if role == "live" else settings.final_model) or DEFAULT_MODELS.get(kind, {}).get(role, "")
        if kind in ("qwen3", "whisper") and model and model not in targets:
            targets.append(model)
        if kind == "qwen3" and role == "final":
            targets.append(ALIGNER)
    ok = True
    for repo in targets:
        print(f"↓ {repo}")
        try:
            snapshot_download(repo)
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  실패: {exc}")
    token = settings.hf_token or os.environ.get("HF_TOKEN")
    if resolve_diarizer_kind() in ("pyannote", "none") and token:
        print(f"↓ {PYANNOTE} (화자 분리)")
        try:
            snapshot_download(PYANNOTE, token=token)
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  실패: {exc}\n  https://huggingface.co/{PYANNOTE} 에서 약관에 동의했는지 확인해 주세요.")
    elif not token:
        print("· HF_TOKEN이 없어 화자 분리 모델은 건너뜁니다 (.env에 HF_TOKEN을 넣으면 받을 수 있어요).")
    print("완료" if ok else "일부 실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

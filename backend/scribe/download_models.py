"""모델 미리 내려받기: python -m scribe.download_models

처음 녹음할 때 기다리지 않도록 Qwen3-ASR(실시간 0.6B, 원본 스크립트 1.7B), 단어 정렬기,
화자 분리 모델(pyannote community-1, Hugging Face 토큰 필요)을 받아 둔다.
"""

from __future__ import annotations

import os
import sys

from .config import settings
from .engines import repos_for, resolve_diarizer_kind
from .engines.download import MODEL_SIZES

PYANNOTE = "pyannote/speaker-diarization-community-1"


def main() -> int:
    from huggingface_hub import snapshot_download

    targets: list[str] = []
    for role in ("live", "final"):
        for repo in repos_for(role):
            if repo not in targets:
                targets.append(repo)
    total = sum(MODEL_SIZES.get(r, 0) for r in targets)
    print(f"받을 모델: {len(targets)}개, 약 {total / 1024 ** 3:.1f}GB (정밀도 {settings.precision})")
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
    try:
        import importlib.util

        if importlib.util.find_spec("pyannote.audio") is not None:
            from .live_speakers import PyannoteEmbedder

            print(f"↓ {PyannoteEmbedder.REPO} (실시간 임시 화자)")
            snapshot_download(PyannoteEmbedder.REPO, token=token or None)
    except Exception as exc:  # noqa: BLE001
        print(f"  실시간 임시 화자 모델은 건너뜁니다: {exc}")
    if not token:
        print("· HF_TOKEN이 없어 화자 분리 모델은 건너뜁니다 (.env에 HF_TOKEN을 넣으면 받을 수 있어요).")
    print("완료" if ok else "일부 실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""모델 내려받기 진행률: Hugging Face 캐시 폴더가 얼마나 찼는지로 잰다.

mlx-qwen3-asr는 모델을 처음 쓸 때 스스로 내려받는다. 그 과정을 건드리지 않고
캐시의 blobs 폴더(받는 중인 .incomplete 파일 포함) 크기를 예상 크기와 비교한다.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from ..catalog import sizes_bytes

# 알려진 모델의 대략적인 크기 (바이트, models.json)
GB = 1024 ** 3
MODEL_SIZES = sizes_bytes()


def hub_cache() -> Path:
    try:
        from huggingface_hub import constants  # type: ignore

        return Path(constants.HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
        return Path(os.environ.get("HF_HUB_CACHE", home / "hub"))


def repo_dir(repo: str) -> Path:
    return hub_cache() / ("models--" + repo.replace("/", "--"))


def downloaded_bytes(repo: str) -> int:
    """받은 양: blobs(받는 중인 것 포함) + snapshots의 진짜 파일.
    - blobs의 링크는 따라가서 센다: 큰 파일은 여러 모델이 같이 쓰는 hub/blobs/xx/ 에 한 번만 두고 링크로 가리킨다
    - snapshots의 링크는 blobs를 가리키므로 세지 않는다 (두 번 세지 않게)
    - 윈도우(개발자 모드가 아니면 링크를 못 만든다)에서는 다 받은 파일이 snapshots에 진짜 파일로 있다"""
    root = repo_dir(repo)
    total = 0
    for f in (root / "blobs").glob("*"):
        try:
            total += f.stat().st_size  # 링크면 가리키는 파일의 크기
        except OSError:
            pass  # 끊어진 링크
    for f in (root / "snapshots").glob("*/**/*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


class DownloadWatch:
    """여러 저장소의 내려받기 진행률. 이미 다 받았으면 needed=False."""

    def __init__(self, repos: list[str]):
        self.repos = [r for r in repos if r in MODEL_SIZES]
        self.total = sum(MODEL_SIZES[r] for r in self.repos)
        self.needed = self.total > 0 and self._done() < self.total * 0.97
        self._stop = threading.Event()
        self._last = self._done() if self.needed else self.total

    def _done(self) -> int:
        return sum(min(downloaded_bytes(r), MODEL_SIZES[r]) for r in self.repos)

    def start(self, on_update=None, every: float = 1.0) -> None:
        def loop() -> None:
            while not self._stop.wait(every):
                self._last = self._done()
                if on_update:
                    on_update(self.progress())

        threading.Thread(target=loop, name="download-watch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def progress(self) -> Optional[dict]:
        if not self.needed:
            return None
        return {"done": self._last, "total": self.total}

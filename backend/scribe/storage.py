"""녹음 파일 정리: 다 만든 회의의 WAV를 m4a로 줄이고, 필요할 때 다시 WAV로 푼다.

WAV(16 kHz 16-bit)는 1시간에 약 115MB, m4a(AAC 64kbps)는 약 29MB다.
녹음을 마치고 한동안(기본 6시간) 손대지 않은 회의만 줄인다 (원본 스크립트를 만들었는지와 상관없이).
그 전에는 WAV 그대로라 "이어서 녹음"이 바로 된다. 줄인 뒤에 이어서 녹음하면 먼저 WAV로 푼다.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from .audio import convert_to_wav, ffmpeg_path, probe_duration, wav_info

WAV = "audio.wav"
M4A = "audio.m4a"
# 녹음 파일을 이만큼 건드리지 않았으면 줄인다 (시간, 음수면 끔)
COMPRESS_AFTER_H = float(os.environ.get("SCRIBE_COMPRESS_AFTER_H", "6"))
AAC_BITRATE = "64k"


def audio_file(mdir: Path) -> Optional[Path]:
    """재생할 녹음 파일: WAV가 있으면 WAV, 아니면 m4a."""
    for name in (WAV, M4A):
        p = mdir / name
        if p.exists():
            return p
    return None


def media_type(path: Path) -> str:
    return "audio/mp4" if path.suffix == ".m4a" else "audio/wav"


def compress_audio(mdir: Path) -> bool:
    """audio.wav → audio.m4a. 길이가 맞는지 확인한 뒤에만 WAV를 지운다."""
    wav = mdir / WAV
    ff = ffmpeg_path()
    if not wav.exists() or not ff:
        return False
    tmp = mdir / "audio.tmp.m4a"
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(wav),
           "-c:a", "aac", "-b:a", AAC_BITRATE, "-movflags", "+faststart", str(tmp)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        return False
    want = wav_info(wav).duration
    got = probe_duration(tmp)
    if got is None or abs(got - want) > 0.3:
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(mdir / M4A)
    wav.unlink()
    return True


def restore_wav(mdir: Path) -> Optional[Path]:
    """이어서 녹음하려고 m4a를 WAV로 되돌린다. 성공하면 m4a는 지운다 (이제 WAV가 기준)."""
    m4a = mdir / M4A
    wav = mdir / WAV
    if wav.exists() or not m4a.exists():
        return wav if wav.exists() else None
    convert_to_wav(m4a, wav)
    m4a.unlink()
    return wav


def is_settled(mdir: Path, now: Optional[float] = None) -> bool:
    """녹음 파일을 줄여도 될 만큼 오래 손대지 않았는지."""
    if COMPRESS_AFTER_H < 0:
        return False
    wav = mdir / WAV
    if not wav.exists():
        return False
    return (now or time.time()) - wav.stat().st_mtime >= COMPRESS_AFTER_H * 3600

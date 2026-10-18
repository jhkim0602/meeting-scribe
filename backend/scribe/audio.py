"""오디오 저장/변환 유틸리티.

녹음은 16 kHz 모노 16-bit WAV 하나에 계속 이어 붙인다. 헤더를 주기적으로 갱신하므로
녹음 중에도 파일은 항상 재생 가능한 WAV이며, 일시정지 후 다시 녹음하면 같은 파일 끝에 붙는다.
업로드 파일은 ffmpeg로 같은 형식의 WAV로 변환한다.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import struct
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .config import SAMPLE_RATE

HEADER_SIZE = 44


def _header(data_size: int, sr: int = SAMPLE_RATE, channels: int = 1, bits: int = 16) -> bytes:
    byte_rate = sr * channels * bits // 8
    block_align = channels * bits // 8
    return b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE" + b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, sr, byte_rate, block_align, bits
    ) + b"data" + struct.pack("<I", data_size)


def wav_header(data_size: int) -> bytes:
    return _header(data_size)


class WavInfo:
    def __init__(self, data_offset: int, data_size: int, sample_rate: int, channels: int, bits: int):
        self.data_offset = data_offset
        self.data_size = data_size
        self.sample_rate = sample_rate
        self.channels = channels
        self.bits = bits

    @property
    def num_samples(self) -> int:
        return self.data_size // max(1, self.channels * self.bits // 8)

    @property
    def duration(self) -> float:
        return self.num_samples / float(self.sample_rate or SAMPLE_RATE)


def wav_info(path: Path) -> WavInfo:
    """WAV 청크를 읽어 data 위치를 찾는다. 녹음 중이라 헤더 크기가 낡았으면 파일 크기로 보정한다."""
    file_size = os.path.getsize(path)
    with open(path, "rb") as f:
        riff = f.read(12)
        if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise ValueError(f"WAV 파일이 아닙니다: {path}")
        sr, channels, bits = SAMPLE_RATE, 1, 16
        while True:
            head = f.read(8)
            if len(head) < 8:
                raise ValueError("data 청크를 찾지 못했습니다")
            cid, size = head[:4], struct.unpack("<I", head[4:])[0]
            if cid == b"fmt ":
                fmt = f.read(size)
                _, channels, sr, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
                if size % 2:
                    f.read(1)
            elif cid == b"data":
                offset = f.tell()
                actual = file_size - offset
                if size == 0 or size > actual or size == 0xFFFFFFFF:
                    size = actual
                return WavInfo(offset, size, sr, channels, bits)
            else:
                f.seek(size + (size % 2), 1)


class WavAppender:
    """녹음용 WAV 쓰기. 기존 파일이 있으면 끝에 이어 쓴다. 스레드 안전."""

    def __init__(self, path: Path, header_every_bytes: int = SAMPLE_RATE * 2 * 2):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._header_every = header_every_bytes
        self._since_header = 0
        if self.path.exists() and self.path.stat().st_size >= HEADER_SIZE:
            info = wav_info(self.path)
            if info.data_offset != HEADER_SIZE or info.sample_rate != SAMPLE_RATE or info.channels != 1:
                raise ValueError("이어 붙일 수 없는 WAV 형식입니다")
            # 버퍼 없이 써서 저장 공간이 모자랄 때 어디까지 써졌는지 정확히 안다
            self._f = open(self.path, "r+b", buffering=0)
            self._data_size = info.data_size - (info.data_size % 2)
            self._f.truncate(HEADER_SIZE + self._data_size)
            self._f.seek(HEADER_SIZE + self._data_size)
        else:
            self._f = open(self.path, "w+b", buffering=0)
            self._data_size = 0
            self._f.write(_header(0))
        self._write_header()

    @property
    def samples(self) -> int:
        return self._data_size // 2

    @property
    def duration(self) -> float:
        return self.samples / SAMPLE_RATE

    def append(self, pcm16: bytes) -> None:
        if not pcm16:
            return
        if len(pcm16) % 2:
            pcm16 = pcm16[:-1]
        with self._lock:
            try:
                written = self._f.write(pcm16) or 0
            except OSError:
                written = -1
            if written != len(pcm16):
                # 저장 공간 부족: 반쯤 쓴 조각을 버려 파일을 온전하게 둔다
                try:
                    self._f.truncate(HEADER_SIZE + self._data_size)
                    self._f.seek(HEADER_SIZE + self._data_size)
                    self._write_header()
                except OSError:
                    pass
                raise OSError(errno.ENOSPC, "저장 공간이 부족합니다")
            self._data_size += len(pcm16)
            self._since_header += len(pcm16)
            if self._since_header >= self._header_every:
                self._write_header()

    def _write_header(self) -> None:
        pos = self._f.tell()
        self._f.seek(0)
        self._f.write(_header(self._data_size))
        self._f.seek(pos)
        self._f.flush()
        self._since_header = 0

    def close(self) -> None:
        with self._lock:
            if self._f.closed:
                return
            self._write_header()
            os.fsync(self._f.fileno())
            self._f.close()


def read_audio(path: Path, start: float = 0.0, end: Optional[float] = None) -> np.ndarray:
    """WAV 구간을 float32 [-1, 1] 배열로 읽는다 (메모리 매핑 사용)."""
    info = wav_info(path)
    if info.bits != 16 or info.channels != 1:
        raise ValueError("16-bit 모노 WAV만 지원합니다")
    total = info.num_samples
    s0 = max(0, int(start * info.sample_rate))
    s1 = total if end is None else min(total, int(end * info.sample_rate))
    if s1 <= s0:
        return np.zeros(0, dtype=np.float32)
    mm = np.memmap(path, dtype="<i2", mode="r", offset=info.data_offset, shape=(total,))
    # 복사는 한 번만: 3시간이면 float 배열 하나가 약 0.7GB라 임시 배열을 하나 더 만들지 않는다
    out = mm[s0:s1].astype(np.float32)
    out *= 1.0 / 32768.0
    del mm
    return out


def pcm16_to_float(pcm16: bytes) -> np.ndarray:
    return np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0


# ---------------- ffmpeg ----------------

def ffmpeg_path() -> Optional[str]:
    """설치된 ffmpeg, 없으면 파이썬 패키지(imageio-ffmpeg)에 들어 있는 것 (앱으로 설치하면 Homebrew가 없어도 된다)"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe if exe and os.path.exists(exe) else None
    except Exception:  # noqa: BLE001 - 패키지가 없거나 이 기기용 ffmpeg가 없다
        return None


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def probe_duration(src: Path) -> Optional[float]:
    """파일 길이(초). ffprobe가 없으면 ffmpeg가 알려 주는 Duration을 읽는다."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(src)],
                capture_output=True, text=True, timeout=60,
            )
            return float(out.stdout.strip())
        except (ValueError, subprocess.SubprocessError):
            pass
    ff = ffmpeg_path()
    if not ff:
        return None
    try:
        err = subprocess.run([ff, "-hide_banner", "-nostdin", "-i", str(src)], capture_output=True, text=True, timeout=60).stderr
    except (OSError, subprocess.SubprocessError):
        return None
    m = _DURATION_RE.search(err or "")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


_TIME_RE = re.compile(r"out_time_(?:us|ms)=(\d+)")


def convert_to_wav(src: Path, dst: Path, on_progress: Optional[Callable[[float], None]] = None) -> float:
    """아무 오디오/영상 파일을 16 kHz 모노 16-bit WAV로 변환하고 길이(초)를 돌려준다."""
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg를 찾지 못했어요. 앱의 환경설정에서 '다시 설치'를 누르거나 'brew install ffmpeg'로 설치해 주세요.")
    total = probe_duration(src) or 0.0
    tmp = dst.with_suffix(".tmp.wav")
    cmd = [
        ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
        "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
        "-progress", "pipe:1", "-nostats", str(tmp),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        m = _TIME_RE.match(line.strip())
        if m and total > 0 and on_progress:
            seconds = int(m.group(1)) / 1_000_000
            on_progress(min(1.0, seconds / total))
    proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"오디오 변환 실패: {err.strip()[:500]}")
    tmp.replace(dst)
    if on_progress:
        on_progress(1.0)
    return wav_info(dst).duration

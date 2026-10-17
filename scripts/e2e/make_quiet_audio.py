"""E2E용 조용한 마이크 소리: python scripts/e2e/make_quiet_audio.py out/quiet.wav [zero]
zero를 주면 완전한 0(마이크가 소리를 주지 않는 상태), 아니면 아주 작은 잡음(조용한 회의실)."""
import struct
import sys

import numpy as np

sr = 48000
n = sr * 20
x = np.zeros(n) if len(sys.argv) > 2 and sys.argv[2] == "zero" else np.random.default_rng(5).normal(0, 0.002, n)
pcm = (x * 32767).astype("<i2").tobytes()
hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
open(sys.argv[1], "wb").write(hdr + pcm)

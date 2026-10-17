"""E2E용 가짜 마이크 소리(말/쉼 반복 48 kHz WAV)를 만든다: python scripts/e2e/make_fake_audio.py out/speech.wav"""
import struct
import sys

import numpy as np

sr = 48000
rng = np.random.default_rng(3)
parts = []
for i in range(8):
    n = int((2.6 + 0.6 * (i % 3)) * sr)
    t = np.arange(n) / sr
    f = 180 + 30 * (i % 4)
    parts.append(0.35 * (0.55 + 0.45 * np.sin(2 * np.pi * 3.3 * t)) * np.sin(2 * np.pi * f * t) + 0.08 * np.sin(2 * np.pi * 2 * f * t))
    parts.append(rng.normal(0, 0.003, int(0.9 * sr)))
pcm = (np.concatenate(parts) * 32767).astype("<i2").tobytes()
hdr = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
open(sys.argv[1], "wb").write(hdr + pcm)

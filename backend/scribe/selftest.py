"""맥 실기기 점검: python -m scribe.selftest [--compare] [--audio 파일 --text "정답 문장"]

1) 환경: 칩·메모리, ffmpeg, MLX 패키지, 모델을 받아 두었는지, 저장 공간, 잠자기 방지, 전원
2) 한국어 받아쓰기: macOS `say`의 한국어 목소리(Yuna)로 문장을 만들어 실시간(0.6B)·원본 스크립트(1.7B) 엔진으로
   받아 적고, 속도(실제 시간 대비 몇 배 빠른지)와 글자 오류율(CER)을 잰다.
   --compare면 원본 스크립트 엔진을 8bit와 fp16으로 나란히 돌려 8bit로 써도 되는지 알려 준다.
3) 화자 구분이 동작하는지 (HF_TOKEN 필요)

합성 목소리는 실제 회의보다 깨끗하므로 CER은 '이 정도 이하가 정상'이라는 기준선으로만 본다.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np

from .audio import WavAppender, convert_to_wav, ffmpeg_path, read_audio
from .config import SAMPLE_RATE, is_apple_silicon, settings
from .engines import engines_info, make_diarizer, make_engine, model_for, repos_for, resolve_diarizer_kind
from .engines.download import DownloadWatch
from .system import KeepAwake, disk_free_seconds, power_source
from .textutil import normalize

# 숫자는 받아쓰기가 "20%"처럼 적을 수 있어 말로 풀어 쓴다
SENTENCES = [
    "오늘 회의는 삼분기 마케팅 예산 검토부터 시작하겠습니다.",
    "지난달 광고비가 계획보다 많이 나와서 원인을 먼저 살펴보려고 해요.",
    "다음 주 화요일까지 채널별 데이터를 정리해서 공유해 주세요.",
    "결제 모듈 테스트는 보안 검토와 함께 진행하겠습니다.",
]
GAP_S = 0.8


def cer(ref: str, hyp: str) -> float:
    from rapidfuzz.distance import Levenshtein

    r, h = normalize(ref), normalize(hyp)
    return Levenshtein.distance(r, h) / max(1, len(r))


def _sh(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def report_environment() -> None:
    print("■ 환경")
    chip = _sh(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor() or platform.machine()
    mem = _sh(["sysctl", "-n", "hw.memsize"])
    mem_gb = f"{int(mem) / 1024 ** 3:.0f}GB" if mem.isdigit() else "?"
    print(f"  맥: macOS {platform.mac_ver()[0] or '-'} · {chip} · 메모리 {mem_gb} · Apple Silicon {'예' if is_apple_silicon() else '아니오'}")
    info = engines_info()
    print(f"  ffmpeg: {'있음' if ffmpeg_path() else '없음 (brew install ffmpeg)'}")
    print("  패키지: " + ", ".join(f"{k} {'O' if v else '-'}" for k, v in info["installed"].items()))
    for role in ("live", "final"):
        kind, model = model_for(role)
        repos = repos_for(role)
        cached = not DownloadWatch(repos).needed if repos else None
        state = "받아 둠" if cached else "아직 안 받음 (처음 쓸 때 받음)" if cached is False else "-"
        print(f"  {'실시간' if role == 'live' else '원본 스크립트'}: {kind} · {model} · {state}")
    print(f"  정밀도: {settings.precision} · 화자 구분: {resolve_diarizer_kind()}")
    print(f"  저장 공간: 약 {disk_free_seconds(settings.data_dir) / 3600:.0f}시간 녹음 가능 ({settings.data_dir})")
    print(f"  잠자기 방지: {'caffeinate' if KeepAwake.available() else '브라우저 화면 꺼짐 방지만'} · 전원: {power_source() or '알 수 없음'}")


def synthesize(workdir: Path) -> Optional[tuple[Path, list[tuple[float, float]], str]]:
    """say(Yuna)로 문장들을 만들어 하나의 16 kHz WAV로 잇는다. (wav, 문장 구간들, 정답)"""
    if not shutil.which("say"):
        return None
    pieces, spans, t = [], [], 0.0
    gap = np.zeros(int(GAP_S * SAMPLE_RATE), dtype=np.float32)
    for i, text in enumerate(SENTENCES):
        aiff = workdir / f"s{i}.aiff"
        r = subprocess.run(["say", "-v", "Yuna", "-o", str(aiff), text], capture_output=True, text=True)
        if r.returncode != 0 or not aiff.exists():
            print("  한국어 목소리(Yuna)가 없어요: 시스템 설정 → 손쉬운 사용 → 읽기 및 말하기 → 시스템 목소리 → 목소리 관리에서 '한국어 Yuna'를 받아 주세요.")
            return None
        wav = workdir / f"s{i}.wav"
        convert_to_wav(aiff, wav)
        x = read_audio(wav)
        pieces += [x, gap]
        spans.append((t, t + len(x) / SAMPLE_RATE))
        t += (len(x) + len(gap)) / SAMPLE_RATE
    out = workdir / "korean.wav"
    w = WavAppender(out)
    w.append((np.concatenate(pieces) * 32767).astype("<i2").tobytes())
    w.close()
    return out, spans, " ".join(SENTENCES)


def test_live(audio: np.ndarray, spans: list[tuple[float, float]], ref: str) -> None:
    print("■ 실시간 받아쓰기")
    engine = make_engine("live")
    t0 = time.time()
    engine.load()
    print(f"  모델 올리기 {time.time() - t0:.1f}초")
    texts, compute, per = [], 0.0, []
    for a, b in spans:
        clip = audio[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)]
        t = time.time()
        texts.append(engine.transcribe(clip, language=settings.language))
        dt = time.time() - t
        compute += dt
        per.append(dt)
    speech_s = sum(b - a for a, b in spans)
    print(f"  속도: 실제 시간의 {speech_s / max(compute, 1e-6):.1f}배 · 문장 하나 받아 적는 데 평균 {np.mean(per):.2f}초 (자막 지연의 대부분)")
    print(f"  글자 오류율(CER): {cer(ref, ' '.join(texts)) * 100:.1f}%")
    print(f"  예: {texts[0]}")


def test_final(wav: Path, ref: str, label: str = "") -> float:
    engine = make_engine("final")
    t0 = time.time()
    engine.load()
    load_s = time.time() - t0
    audio = read_audio(wav)
    t = time.time()
    segs = engine.transcribe_long(audio, language=settings.language)
    dt = time.time() - t
    text = " ".join(s["text"] for s in segs)
    e = cer(ref, text)
    dur = len(audio) / SAMPLE_RATE
    print(f"  {label}모델 올리기 {load_s:.1f}초 · 속도: 실제 시간의 {dur / max(dt, 1e-6):.1f}배 (1시간 녹음 → 약 {60 * dt / dur:.0f}분) · CER {e * 100:.1f}%")
    return e


def test_diarize(wav: Path) -> None:
    print("■ 화자 구분")
    kind = resolve_diarizer_kind()
    if kind == "none":
        print("  꺼져 있어요: .env에 HF_TOKEN을 넣고 pyannote 약관에 동의하면 켜집니다.")
        return
    d = make_diarizer(kind)
    t = time.time()
    turns = d.diarize(wav, read_audio(wav))
    print(f"  {d.describe()}: {len({x['speaker'] for x in turns})}명 (한 사람 목소리라 1명이 정상) · {time.time() - t:.1f}초")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="맥 실기기 점검")
    ap.add_argument("--compare", action="store_true", help="원본 스크립트 엔진을 8bit와 fp16으로 비교")
    ap.add_argument("--audio", help="직접 녹음한 파일로 점검 (--text와 함께)")
    ap.add_argument("--text", help="--audio 파일의 정답 문장")
    ap.add_argument("--skip-diarize", action="store_true")
    args = ap.parse_args(argv)

    report_environment()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if args.audio:
            if not args.text:
                print("--audio에는 --text(정답 문장)가 필요해요.")
                return 2
            wav = work / "input.wav"
            convert_to_wav(Path(args.audio), wav)
            audio = read_audio(wav)
            spans, ref = [(0.0, len(audio) / SAMPLE_RATE)], args.text
        else:
            made = synthesize(work)
            if made is None:
                print("■ 받아쓰기 점검은 건너뜀 (macOS say가 없음). --audio 파일 --text \"정답\"으로 직접 점검할 수 있어요.")
                return 0
            wav, spans, ref = made
            audio = read_audio(wav)
        test_live(audio, spans, ref)
        print("■ 원본 스크립트 받아쓰기")
        if args.compare:
            results = {}
            original = settings.precision
            for p in ("8bit", "fp16"):
                settings.precision = p
                results[p] = test_final(wav, ref, label=f"[{p}] ")
            settings.precision = original
            diff = (results["8bit"] - results["fp16"]) * 100
            verdict = "8bit를 그대로 쓰세요" if diff <= 0.5 else ".env에 SCRIBE_PRECISION=fp16을 넣는 것을 권해요"
            print(f"  → 8bit가 fp16보다 CER {diff:+.1f}%p · {verdict}")
        else:
            test_final(wav, ref)
        if not args.skip_diarize:
            test_diarize(wav)
    print("완료. 마이크·잠자기 같은 나머지 점검은 docs/MAC_CHECKLIST.md를 따라 해 보세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

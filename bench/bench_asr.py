"""faster-whisper CPU 측정: 앱과 같은 설정으로.
live: 문장 하나(정답 문장 단위로 자름)를 받아 적는 시간 / final: 10분 녹음 전체(단어 시각, VAD)."""
import json, re, sys, time
import numpy as np, psutil, soundfile as sf
from faster_whisper import WhisperModel
import jiwer

model_id, threads, mode = sys.argv[1], int(sys.argv[2]), sys.argv[3]
proc = psutil.Process()
peak = 0
def mem():
    global peak
    peak = max(peak, proc.memory_info().rss)

def norm(s): return re.sub(r"[^\w]", "", s)

t0 = time.time()
m = WhisperModel(model_id, device="cpu", compute_type="int8", cpu_threads=threads)
load = time.time() - t0; mem()
wav, sr = sf.read("data/meeting_600s.wav", dtype="float32")
truth = json.load(open("data/meeting_600s.json"))
out = {"model": model_id.split("/")[-1], "threads": threads, "mode": mode, "load_s": round(load, 1)}
if mode == "live":
    lat, hyp, ref = [], [], []
    for tr in truth[:20]:
        seg = wav[int(tr["start"] * sr): int(tr["end"] * sr)]
        t = time.time()
        s, _ = m.transcribe(seg, language="ko", condition_on_previous_text=False, beam_size=1)
        text = " ".join(x.text.strip() for x in s)
        lat.append(time.time() - t); mem()
        hyp.append(norm(text)); ref.append(norm(tr["text"]))
    dur = sum(tr["end"] - tr["start"] for tr in truth[:20])
    out.update(avg_sentence_s=round(sum(tr["end"] - tr["start"] for tr in truth[:20]) / 20, 1), latency_avg=round(np.mean(lat), 2), latency_max=round(max(lat), 2),
               rtf=round(sum(lat) / dur, 3), cer=round(jiwer.cer(" ".join(ref), " ".join(hyp)), 3))
else:
    t = time.time()
    s, _ = m.transcribe(wav, language="ko", word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    text = "".join(x.text for x in s)
    el = time.time() - t; mem()
    out.update(audio_s=round(len(wav) / sr), elapsed_s=round(el, 1), rtf=round(el / (len(wav) / sr), 3),
               cer=round(jiwer.cer(norm("".join(tr["text"] for tr in truth)), norm(text)), 3))
out["peak_rss_gb"] = round(peak / 1024**3, 2)
print(json.dumps(out, ensure_ascii=False), flush=True)

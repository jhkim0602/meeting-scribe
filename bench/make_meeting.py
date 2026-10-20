"""Zeroth Korean 시험 세트로 4명 회의를 만든다: 차례로 말하고(한 차례 1~2문장), 사이에 0.3~0.9초 쉼. 정답(누가 언제 무엇을)도 남긴다."""
import io, json, random, sys
import numpy as np, pyarrow.parquet as pq, soundfile as sf

SR = 16000
target = float(sys.argv[1]) if len(sys.argv) > 1 else 600
t = pq.read_table("data/zeroth_test.parquet").to_pylist()
speakers = [118, 104, 121, 147]
pool = {s: [r for r in t if r["speaker_id"] == s] for s in speakers}
rng = random.Random(7)
audio, turns, now = [], [], 0.0
i = 0
while now < target:
    spk = speakers[i % len(speakers)] if i % 5 else rng.choice(speakers)
    i += 1
    for _ in range(rng.choice([1, 1, 2])):
        if not pool[spk]:
            pool[spk] = [r for r in t if r["speaker_id"] == spk]
        r = pool[spk].pop(rng.randrange(len(pool[spk])))
        x, sr = sf.read(io.BytesIO(r["audio"]["bytes"]), dtype="float32")
        assert sr == SR
        turns.append({"speaker": f"S{speakers.index(spk) + 1}", "start": round(now, 3), "end": round(now + len(x) / SR, 3), "text": r["text"]})
        audio.append(x)
        now += len(x) / SR
        gap = rng.uniform(0.3, 0.9)
        audio.append(np.zeros(int(gap * SR), np.float32))
        now += gap
wav = np.concatenate(audio)
sf.write(f"data/meeting_{int(target)}s.wav", wav, SR)
json.dump(turns, open(f"data/meeting_{int(target)}s.json", "w"), ensure_ascii=False, indent=0)
print(f"{len(wav)/SR:.1f}s, {len(turns)} sentences, speakers {sorted({x['speaker'] for x in turns})}")

"""pyannote CPU 측정: 앱과 같은 파이프라인(community-1)으로 10분 회의의 화자 나누기 + 회의 중 화자 모델(wespeaker) 문장당 시간."""
import json, sys, time
import numpy as np, psutil, soundfile as sf, torch

threads, what = int(sys.argv[1]), sys.argv[2]
torch.set_num_threads(threads)
proc = psutil.Process()
wav, sr = sf.read("data/meeting_600s.wav", dtype="float32")
truth = json.load(open("data/meeting_600s.json"))
out = {"threads": threads, "what": what}
if what == "pipeline":
    from pyannote.audio import Pipeline
    t = time.time()
    p = Pipeline.from_pretrained("pyannote-community/speaker-diarization-community-1")
    out["load_s"] = round(time.time() - t, 1)
    for label, kw in (("auto", {}), ("known4", {"num_speakers": 4})):
        t = time.time()
        res = p({"waveform": torch.from_numpy(wav)[None], "sample_rate": sr}, **kw)
        el = time.time() - t
        ann = getattr(res, "exclusive_speaker_diarization", None) or getattr(res, "speaker_diarization", res)
        turns = [(s.start, s.end, spk) for s, _, spk in ann.itertracks(yield_label=True)]
        # 정답 문장마다 가장 많이 겹친 화자 → 문장 단위 화자 정확도 (라벨 이름은 가장 잘 맞는 짝으로)
        import itertools
        pred = []
        for tr in truth:
            ov = {}
            for a, b, spk in turns:
                o = min(b, tr["end"]) - max(a, tr["start"])
                if o > 0: ov[spk] = ov.get(spk, 0) + o
            pred.append(max(ov, key=ov.get) if ov else None)
        found = sorted({p_ for p_ in pred if p_})
        best = 0
        gold = [tr["speaker"] for tr in truth]
        golds = sorted(set(gold))
        for perm in itertools.permutations(found, min(len(found), len(golds))):
            m = dict(zip(perm, golds))
            best = max(best, sum(1 for g, p_ in zip(gold, pred) if p_ and m.get(p_) == g))
        out[label] = {"elapsed_s": round(el, 1), "rtf": round(el / (len(wav) / sr), 3), "speakers_found": len({s for _, _, s in turns}),
                      "sentence_speaker_acc": round(best / len(truth), 3)}
else:
    from pyannote.audio import Inference, Model
    model = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM")
    inf = Inference(model, window="whole")
    lat = []
    for tr in truth[:30]:
        seg = wav[int(tr["start"] * sr): int(tr["end"] * sr)]
        t = time.time()
        inf({"waveform": torch.from_numpy(seg)[None], "sample_rate": sr})
        lat.append(time.time() - t)
    out.update(avg_sentence_s=round(np.mean([tr["end"] - tr["start"] for tr in truth[:30]]), 1), latency_avg=round(float(np.mean(lat[1:])), 3), latency_max=round(max(lat[1:]), 3))
out["peak_rss_gb"] = round(proc.memory_info().rss / 1024**3, 2)
print(json.dumps(out, ensure_ascii=False), flush=True)

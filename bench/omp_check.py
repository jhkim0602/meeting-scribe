"""윈도우: 한 프로세스에서 torch(pyannote)와 CTranslate2(faster-whisper)를 같이 쓰면 OpenMP DLL이 부딪히는지 (앱의 서버가 이렇게 쓴다)"""
import numpy as np, torch
from pyannote.audio import Inference, Model
from faster_whisper import WhisperModel
inf = Inference(Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM"), window="whole")
wm = WhisperModel("Systran/faster-whisper-small", device="cpu", compute_type="int8")
x = (np.random.default_rng(0).standard_normal(16000 * 3) * 0.01).astype(np.float32)
for _ in range(2):
    inf({"waveform": torch.from_numpy(x)[None], "sample_rate": 16000})
    list(wm.transcribe(x, language="ko", beam_size=1)[0])
print("OMP OK: torch + ctranslate2 in one process")

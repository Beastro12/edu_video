import json, sys, soundfile as sf
from kokoro_onnx import Kokoro
k = Kokoro("kokoro.onnx", "voices.bin")
voice, speed = sys.argv[1], float(sys.argv[2])
out = {}
for s in json.load(open("script.json")):
    samples, sr = k.create(s["text"], voice=voice, speed=speed, lang="en-us")
    sf.write(f"audio/{s['id']}.wav", samples, sr)
    out[s["id"]] = len(samples) / sr
    print(s["id"], round(out[s["id"]], 1), flush=True)
json.dump(out, open("durations.json", "w"), indent=1)
print("total", round(sum(out.values()), 1))

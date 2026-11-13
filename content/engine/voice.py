"""Voice an episode: episode JSON -> WORK/voice.wav + WORK/timeline.json (sentence and estimated word timings, beats)."""
import json, os, re, sys
import numpy as np, soundfile as sf
from kokoro_onnx import Kokoro
CACHE = os.environ.get("CACHE", os.path.join(os.path.dirname(__file__), "..", ".cache"))
ep, WORK = json.load(open(sys.argv[1])), sys.argv[2]
k = Kokoro(os.path.join(CACHE, "kokoro.onnx"), os.path.join(CACHE, "voices.bin"))
SR = 24000
def syl(w): return max(1, len(re.findall(r"[aeiouy]+", w.lower())))
out, t, parts = [], 0.25, [np.zeros(int(SR * 0.25), np.float32)]
for s in ep["sentences"]:
    disp, spoken = [], []
    for tk in s["text"].split(" "):
        if "|" in tk:
            d, s_ = tk.split("|", 1); disp.append(d); spoken.append(s_.replace("-", " "))
        else:
            disp.append(tk); spoken.append(tk)
    a, _ = k.create(" ".join(spoken).replace("$", ""), voice=ep.get("voice", "af_heart"), speed=ep.get("speed", 1.2), lang="en-us")
    a = a.astype(np.float32); nz = np.where(np.abs(a) > 0.02)[0]
    if len(nz): a = a[max(0, nz[0] - 240): nz[-1] + 1200]
    d = len(a) / SR
    w = [syl(x) + (0.6 if re.search(r"[.,?:]$", dd) else 0) for x, dd in zip(spoken, disp)]
    tot, acc, words = sum(w), 0, []
    for dd, ww in zip(disp, w):
        words.append({"w": dd, "s": round(t + d * acc / tot, 3), "e": round(t + d * (acc + ww) / tot, 3)}); acc += ww
    out.append({"start": round(t, 3), "end": round(t + d, 3), "words": words, "beats": [dict(b, t=words[min(i, len(words) - 1)]["s"]) for i, b in s["beats"]]})
    gap = 0.22 if s["text"].rstrip().endswith((".", "?", ":", "!")) else 0.1
    parts += [a, np.zeros(int(SR * gap), np.float32)]; t += d + gap
parts.append(np.zeros(SR, np.float32))
sf.write(os.path.join(WORK, "voice.wav"), np.concatenate(parts), SR)
json.dump({"sentences": out, "dur": round(t + 1.0, 3), "date": ep.get("date", ""), "format": ep.get("format", "9:16"), "hook": ep.get("hook", True), "slug": ep.get("slug", ""), "music": ep.get("music")}, open(os.path.join(WORK, "timeline.json"), "w"), indent=1, ensure_ascii=False)
print("voice: duration", round(t + 1.0, 1), "s")

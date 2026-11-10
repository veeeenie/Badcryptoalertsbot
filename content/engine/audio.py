"""Sound: synthesized effects on each beat (whoosh, pop, ticks, ding, impact, riser) + a soft 104 bpm beat ducked under the voice. -> WORK/mix.wav"""
import json, os, sys, numpy as np, soundfile as sf
from scipy.signal import butter, sosfilt, resample_poly
SR = 48000; rng = np.random.default_rng(7)
WORK = sys.argv[1]; TL = json.load(open(os.path.join(WORK, "timeline.json"))); DUR = TL["dur"]; N = int(DUR * SR)
def env(n, a, d):  # attack/decay exponential envelope
    t = np.arange(n) / SR; e = np.minimum(1, t / max(a, 1e-4)) * np.exp(-np.maximum(0, t - a) / d); return e
def bp(x, lo, hi): return sosfilt(butter(2, [lo, hi], "bandpass", fs=SR, output="sos"), x)
def hp(x, f): return sosfilt(butter(2, f, "highpass", fs=SR, output="sos"), x)
def lp(x, f): return sosfilt(butter(2, f, "lowpass", fs=SR, output="sos"), x)
def whoosh():
    n = int(0.45 * SR); x = rng.standard_normal(n)
    # sweep: blend of filtered bands
    out = np.zeros(n); seg = n // 9
    for i in range(9):
        f0 = 300 + 500 * i if i < 5 else 300 + 500 * (9 - i); s = slice(i * seg, (i + 1) * seg + 400)
        out[s] += bp(x[s], f0, f0 * 3)[: len(out[s])]
    t = np.arange(n) / n; e = np.sin(np.pi * t) ** 2
    return 0.5 * out * e / (np.abs(out).max() + 1e-9)
def pop():
    n = int(0.12 * SR); t = np.arange(n) / SR; f = 900 * np.exp(-t * 18) + 380
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 38)
    s[:200] += rng.standard_normal(200) * 0.3 * np.linspace(1, 0, 200); return 0.55 * s
def tick():
    n = int(0.02 * SR); t = np.arange(n) / SR; return 0.25 * np.sin(2 * np.pi * 2200 * t) * np.exp(-t * 250)
def impact(soft=False):
    n = int(0.8 * SR); t = np.arange(n) / SR; f = 90 * np.exp(-t * 6) + 38
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5)
    nz = lp(rng.standard_normal(n), 1800) * np.exp(-t * 30) * 0.8
    return (0.45 if soft else 0.9) * (s + nz) / 1.6
def ding():
    n = int(0.9 * SR); t = np.arange(n) / SR
    s = (np.sin(2 * np.pi * 1568 * t) + 0.6 * np.sin(2 * np.pi * 2093 * t) + 0.3 * np.sin(2 * np.pi * 3136 * t)) * np.exp(-t * 5)
    return 0.22 * s * np.minimum(1, t / 0.004)
def riser(length=0.9):
    n = int(length * SR); t = np.arange(n) / SR; x = hp(rng.standard_normal(n), 1500) * (t / length) ** 2 * 0.35
    f = 200 + 700 * (t / length) ** 2; x += 0.15 * np.sin(2 * np.pi * np.cumsum(f) / SR) * (t / length); return x
mix_sfx = np.zeros(N + SR * 2)
def add(sig, at, g=1.0):
    i = int(at * SR); i = max(0, i); mix_sfx[i:i + len(sig)] += g * sig[: len(mix_sfx) - i]
seen_groups = set()
for s in TL["sentences"]:
    for b in s["beats"]:
        t, fx = b["t"], b.get("sfx")
        if fx == "whoosh": add(whoosh(), t - 0.18, 0.8)
        elif fx == "pop": add(pop(), t, 0.7)
        elif fx == "ding": add(ding(), t, 0.8)
        elif fx == "impact": add(impact(), t, 0.9)
        elif fx == "impact_soft": add(impact(True), t, 0.9)
        elif fx == "riser_short": add(riser(0.6), t - 0.1, 0.6)
        elif fx == "riser_hit": add(riser(0.9), t - 0.9, 0.7); add(impact(), t, 0.8)
        elif fx == "ticks":
            for k in range(16): add(tick(), t + k * 0.07, 0.8)
            add(ding(), t + 1.15, 0.5)
# music: 104 bpm, kick + hat + soft pad (Am - F - C - G)
bpm = 104; beat = 60 / bpm; music = np.zeros(N + SR * 2)
def kick():
    n = int(0.35 * SR); t = np.arange(n) / SR; f = 110 * np.exp(-t * 25) + 45; return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
def hat():
    n = int(0.05 * SR); t = np.arange(n) / SR; return hp(rng.standard_normal(n), 7000) * np.exp(-t * 90) * 0.35
K, Hh = kick(), hat()
chords = [(220, 261.6, 329.6), (174.6, 220, 261.6), (130.8, 164.8, 196), (196, 246.9, 293.7)]
nb = int(DUR / beat) + 2
for i in range(nb):
    at = int(i * beat * SR)
    if at >= len(music): break
    music[at:at + len(K)] += 0.8 * K[: len(music) - at]
    h = int((i + 0.5) * beat * SR); music[h:h + len(Hh)] += Hh[: max(0, len(music) - h)]
    if i % 4 == 0:
        c = chords[(i // 4) % 4]; n = int(4 * beat * SR); t = np.arange(n) / SR
        pad = sum(np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t) for f in c) * np.minimum(1, t / 0.4) * np.exp(-t / 3) * 0.12
        music[at:at + n] += pad[: len(music) - at]
        bass = np.sin(2 * np.pi * c[0] / 2 * t) * np.exp(-t * 1.2) * 0.35; music[at:at + n] += bass[: len(music) - at]
music = lp(music, 6000)
v, vsr = sf.read(os.path.join(WORK, "voice.wav")); v = resample_poly(v, SR, vsr)
voice = np.zeros(N + SR * 2); voice[: len(v)] += v[: len(voice)]
# duck music under voice
venv = np.convolve(np.abs(voice), np.ones(int(0.15 * SR)) / int(0.15 * SR), "same")
duck = 1 - 0.55 * np.clip(venv / (venv.max() * 0.25), 0, 1)
fade = np.ones(len(music)); fe = int(1.2 * SR); end = int(DUR * SR); fade[end - fe:end] = np.linspace(1, 0, fe); fade[end:] = 0
out = 1.0 * voice + 0.16 * music * duck * fade + 0.55 * mix_sfx
out = out[:N]; out = out / max(1.0, np.abs(out).max() / 0.95)
sf.write(os.path.join(WORK, "mix.wav"), out.astype(np.float32), SR)
print("mixed", round(len(out) / SR, 2), "peak", round(float(np.abs(out).max()), 3))

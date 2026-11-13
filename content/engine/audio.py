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
def swish():  # short airy swipe for a camera punch / cut
    n = int(0.16 * SR); t = np.arange(n) / n; x = bp(rng.standard_normal(n), 2500, 9000) * np.sin(np.pi * t) ** 1.5
    return 0.28 * x / (np.abs(x).max() + 1e-9)
def boom():  # low hit under a scene change
    n = int(0.5 * SR); t = np.arange(n) / SR; f = 70 * np.exp(-t * 10) + 34
    return 0.5 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
def snap():  # tight click for an auto pop
    n = int(0.05 * SR); t = np.arange(n) / SR; return 0.35 * hp(rng.standard_normal(n), 3000) * np.exp(-t * 120)
# camera events from the editorial engine (record.py events): punches, transitions, hits, counters
CAM = os.path.join(WORK, "cam.json")
if os.path.exists(CAM):
    for e in json.load(open(CAM)):
        if e["kind"] == "punch": add(swish(), e["t"] - 0.04, 0.55)
        elif e["kind"] == "trans": add(boom(), e["t"], 0.7); add(swish(), e["t"] - 0.12, 0.7)
        elif e["kind"] == "hit": add(boom(), e["t"], 0.45)
        elif e["kind"] == "count":
            for k in range(10): add(tick(), e["t"] + k * 0.07, 0.7)
    for s in TL["sentences"]:
        for b in s["beats"]:
            if b.get("kind") == "add" and not b.get("sfx"): add(snap(), b["t"], 0.6)
EDITORIAL = os.path.exists(CAM)
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
    if EDITORIAL:  # extra drive: hats on every 8th, a clap on 2 and 4
        music[at:at + len(Hh)] += 0.6 * Hh[: max(0, len(music) - at)]
        if i % 2: cp = hp(rng.standard_normal(int(0.12 * SR)), 1200) * np.exp(-np.arange(int(0.12 * SR)) / SR * 30) * 0.45; music[at:at + len(cp)] += cp[: max(0, len(music) - at)]
    if i % 4 == 0:
        c = chords[(i // 4) % 4]; n = int(4 * beat * SR); t = np.arange(n) / SR
        pad = sum(np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t) for f in c) * np.minimum(1, t / 0.4) * np.exp(-t / 3) * 0.12
        music[at:at + n] += pad[: len(music) - at]
        bass = np.sin(2 * np.pi * c[0] / 2 * t) * np.exp(-t * 1.2) * 0.35; music[at:at + n] += bass[: len(music) - at]
music = lp(music, 6000)
MOOD = TL.get("mood") or "upbeat"
if MOOD == "tense":  # generated tension bed for bad-news days: A minor, pulsing 8th-note bass, ticking 16ths, low drone, E-major turn for unease
    bpm = 108; beat = 60 / bpm; music = np.zeros(N + SR * 2)
    prog = [(110.0, (220, 261.6, 329.6)), (87.3, (174.6, 220, 261.6)), (73.4, (146.8, 174.6, 220)), (82.4, (164.8, 207.7, 246.9))]  # Am F Dm E
    def pluck(f, n, dec=9):
        t = np.arange(n) / SR; x = np.sign(np.sin(2 * np.pi * f * t)) * 0.5 + np.sin(2 * np.pi * f * t) * 0.5
        return lp(x, 900) * np.exp(-t * dec) * np.minimum(1, t / 0.004)
    tick = hp(rng.standard_normal(int(0.03 * SR)), 6000) * np.exp(-np.arange(int(0.03 * SR)) / SR * 160) * 0.22
    for i in range(int(DUR / beat) + 2):
        at = int(i * beat * SR)
        if at >= len(music): break
        root, ch = prog[(i // 8) % 4]
        if i % 2 == 0: music[at:at + len(K)] += 0.9 * K[: len(music) - at]
        for e in (0, 0.5):  # 8th-note bass pulse
            b0 = int((i + e) * beat * SR); bl = pluck(root, int(beat * 0.5 * SR)) * (0.5 if e else 0.62); music[b0:b0 + len(bl)] += bl[: max(0, len(music) - b0)]
        for q in range(4):  # ticking 16ths, accent on the beat
            t0 = int((i + q / 4) * beat * SR); music[t0:t0 + len(tick)] += (1.0 if q == 0 else 0.45) * tick[: max(0, len(music) - t0)]
        if i % 8 == 0:  # dark pad + drone per chord (2 bars)
            n = int(8 * beat * SR); t = np.arange(n) / SR
            pad = sum(np.sin(2 * np.pi * f * t + 0.3 * np.sin(2 * np.pi * 0.25 * t)) for f in ch) * np.minimum(1, t / 1.2) * np.minimum(1, (t[-1] - t) / 0.6 + 0.01) * 0.07
            drone = np.sin(2 * np.pi * root / 2 * t) * 0.18 * np.minimum(1, t / 0.5)
            music[at:at + n] += (lp(pad, 2500) + drone)[: len(music) - at]
    music = lp(music, 7000)
MUSIC_GAIN = 0.21 if EDITORIAL else 0.16
# Real tracks: any .mp3/.wav/.m4a in $MUSIC_DIR (default: CACHE/music, never committed: the repo is public and the
# Pixabay licence forbids redistributing the raw files). One track per episode, picked by slug (or "music" in the
# episode), started at the offset whose beat grid best lines up with the video's cuts, looped if short, and dropped
# to silence for a beat before each {"kind":"drop"} / rust catch frame. Falls back to the generated beat above.
import glob, hashlib, subprocess, tempfile
MDIR = os.environ.get("MUSIC_DIR") or os.path.join(os.environ.get("CACHE", os.path.join(os.path.dirname(__file__), "..", ".cache")), "music")
tracks = sorted(f for f in glob.glob(os.path.join(MDIR, "*")) if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg", ".flac")))
TRACK_INFO = None
if tracks and TL.get("music") != "generated":
    want = TL.get("music")
    pick = next((f for f in tracks if want and want.lower() in os.path.basename(f).lower()), None) or \
        tracks[int(hashlib.md5(TL.get("slug", WORK).encode()).hexdigest(), 16) % len(tracks)]
    tmp = os.path.join(WORK, "track.wav")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", pick, "-ac", "1", "-ar", str(SR), tmp], check=True)
    tr, _ = sf.read(tmp)
    try:
        import librosa
        y22 = resample_poly(tr, 22050, SR); tempo, bt = librosa.beat.beat_track(y=y22, sr=22050, units="time"); tempo = float(np.atleast_1d(tempo)[0])
    except Exception as ex:
        print("beat tracking failed:", ex); tempo, bt = 0.0, np.array([])
    cuts = [e["t"] for e in json.load(open(CAM))] if os.path.exists(CAM) else []
    tlen = len(tr) / SR; best, bo = -1, 0.0
    if len(bt) > 8 and cuts:
        cuts = np.array(cuts); hi = max(0.0, min(tlen - DUR - 0.5, tlen * 0.5))
        for o in np.arange(0, max(hi, 0.01), 0.01):
            b = bt[(bt >= o) & (bt <= o + DUR + 1)] - o
            if len(b) < 4: continue
            d = np.min(np.abs(cuts[:, None] - b[None, :]), axis=1); sc = float(np.exp(-(d / 0.05) ** 2 / 2).sum())
            if sc > best: best, bo = sc, float(o)
    seg = tr[int(bo * SR):]
    xf = int(1.5 * SR)  # loop short tracks with a 1.5 s crossfade so the seam doesn't jump
    while len(seg) < N + SR * 2:
        r = np.linspace(0, 1, xf); nxt = tr[int(min(8.0, len(tr) / 4) * SR):]  # re-enter after the intro
        seg = np.concatenate([seg[:-xf], seg[-xf:] * (1 - r) + nxt[:xf] * r, nxt[xf:]])
    seg = seg[: N + SR * 2]; seg = seg / (np.sqrt(np.mean(seg[: N] ** 2)) + 1e-9) * 0.25  # same loudness for every track
    seg[: int(0.25 * SR)] *= np.linspace(0, 1, int(0.25 * SR))
    music = seg; MUSIC_GAIN = 0.34
    TRACK_INFO = (os.path.basename(pick), round(tempo), round(bo, 2), round(best, 1), len(cuts))
# drop to silence for a beat before the catch
drops = [b["t"] for s in TL["sentences"] for b in s["beats"] if b.get("kind") == "drop" or (b.get("kind") == "bg" and b.get("mode") == "rust")]
for dt in drops:
    a0, a1, a2 = int((dt - 0.5) * SR), int((dt + 0.12) * SR), int((dt + 0.45) * SR)
    if a0 < 0: continue
    music[a0:a1] *= np.linspace(1, 0, a1 - a0) ** 0.3; music[a1:a2] *= np.linspace(0, 1, a2 - a1)
v, vsr = sf.read(os.path.join(WORK, "voice.wav")); v = resample_poly(v, SR, vsr)
voice = np.zeros(N + SR * 2); voice[: len(v)] += v[: len(voice)]
# duck music under voice
venv = np.convolve(np.abs(voice), np.ones(int(0.15 * SR)) / int(0.15 * SR), "same")
duck = 1 - 0.55 * np.clip(venv / (venv.max() * 0.25), 0, 1)
fade = np.ones(len(music)); fe = int(1.2 * SR); end = int(DUR * SR); fade[end - fe:end] = np.linspace(1, 0, fe); fade[end:] = 0
out = 1.0 * voice + MUSIC_GAIN * music * duck * fade + 0.55 * mix_sfx
out = out[:N]; out = out / max(1.0, np.abs(out).max() / 0.95)
sf.write(os.path.join(WORK, "mix.wav"), out.astype(np.float32), SR)
print("mixed", round(len(out) / SR, 2), "peak", round(float(np.abs(out).max()), 3), "track:", TRACK_INFO or "generated beat")

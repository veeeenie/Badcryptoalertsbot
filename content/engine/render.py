"""
Render an episode's frames: WORK/timeline.json -> WORK/part{i}.mp4 (1080x1920, 30 fps).
  python render.py stills 3.2 10.5 ...   key frames as JPEGs in WORK (look at them before the full render)
  python render.py <i> <n>               render chunk i of n (run n in parallel)
Beat kinds (all positions are the centre; x is an offset from the middle, y from the top; keep visuals between y 250 and 1200,
captions use 1250-1600): title, big (text, size, color, emoji), text, counter (n, label, emoji), stamp (text, size, color),
pill, card (text, sub, x), chip (text, x), ring (n, of), book (side, px), compare (rows [[label, a, b]], names), market
(title, left, right, mid), eq. Colors: white, muted, mint, red, amber. A new "group" clears the screen with a punch-in.
"""
import json, math, os, random, subprocess, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
CACHE = os.environ.get("CACHE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache"))
FD = os.path.join(CACHE, "inter/extras/ttf/")
_fc = {}
def font(w, s):
    key = (w, s)
    if key not in _fc:
        _fc[key] = ImageFont.truetype(FD + {"r": "Inter-Regular", "s": "Inter-SemiBold", "b": "Inter-Bold", "x": "InterDisplay-Black", "d": "InterDisplay-Bold"}[w] + ".ttf", s)
    return _fc[key]
EMO = ImageFont.truetype(next(p for p in ("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", os.path.join(CACHE, "NotoColorEmoji.ttf")) if os.path.exists(p)), 109)
BG = (9, 13, 24); GRID = (24, 33, 52)
COL = {"white": (245, 247, 250), "muted": (140, 152, 170), "mint": (62, 230, 184), "red": (255, 84, 104), "amber": (255, 196, 70)}
WORK = os.environ["WORK"]
TL = json.load(open(os.path.join(WORK, "timeline.json")))
DUR = TL["dur"]
BEATS = [b for s in TL["sentences"] for b in s["beats"]]
GROUP_START = {}
for b in BEATS: GROUP_START.setdefault(b["group"], b["t"])

def ease(x): x = max(0.0, min(1.0, x)); return 1 - (1 - x) ** 3
def back(x):  # overshoot pop
    x = max(0.0, min(1.0, x)); c = 1.9; return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2

# background: grid + vignette, rendered taller so it can drift
def make_bg():
    im = Image.new("RGB", (W, H + 90), BG); d = ImageDraw.Draw(im)
    for x in range(0, W + 1, 90): d.line((x, 0, x, H + 90), fill=GRID, width=2)
    for y in range(0, H + 91, 90): d.line((0, y, W, y), fill=GRID, width=2)
    vig = Image.new("L", (W, H + 90), 0); vd = ImageDraw.Draw(vig)
    vd.ellipse((-350, 50, W + 350, H + 40), fill=255); vig = vig.filter(ImageFilter.GaussianBlur(260))
    dark = Image.new("RGB", (W, H + 90), (4, 6, 12))
    return Image.composite(im, dark, vig)
BGI = make_bg()

def text_layer(txt, f, col, stroke=0, sc=(0, 0, 0)):
    bb = f.getbbox(txt, stroke_width=stroke)
    im = Image.new("RGBA", (bb[2] - bb[0] + 40, bb[3] - bb[1] + 40), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((20 - bb[0], 20 - bb[1]), txt, font=f, fill=col, stroke_width=stroke, stroke_fill=sc)
    return im

def glow(layer, col, r=18, k=0.9):
    a = layer.split()[-1].filter(ImageFilter.GaussianBlur(r))
    g = Image.new("RGBA", layer.size, col + (0,)); g.putalpha(a.point(lambda v: int(v * k)))
    pad = Image.new("RGBA", layer.size, (0, 0, 0, 0)); pad.alpha_composite(g); pad.alpha_composite(layer); return pad

def emoji(ch, size):
    im = Image.new("RGBA", (140, 140), (0, 0, 0, 0)); ImageDraw.Draw(im).text((8, 8), ch, font=EMO, embedded_color=True)
    im = im.crop(im.getbbox()); return im.resize((size, int(size * im.height / im.width)), Image.LANCZOS)

def place(canvas, layer, cx, cy, scale=1.0, alpha=1.0, rot=0):
    if scale <= 0.01 or alpha <= 0: return
    if rot: layer = layer.rotate(rot, resample=Image.BICUBIC, expand=True)
    if abs(scale - 1) > 0.01: layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BICUBIC)
    if alpha < 1: a = layer.split()[-1].point(lambda v: int(v * alpha)); layer = layer.copy(); layer.putalpha(a)
    canvas.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))

def rbox(w, h, fill, outline=None, r=34, ow=5):
    im = Image.new("RGBA", (w + 12, h + 12), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((6, 6, w + 6, h + 6), r, fill=fill, outline=outline, width=ow); return im

_cache = {}
def cached(key, fn):
    if key not in _cache: _cache[key] = fn()
    return _cache[key]

def draw_beat(cv, b, t):
    age = t - b["t"]
    if age < 0: return
    p = back(age / 0.28); a = ease(age / 0.15)
    cx = W // 2 + b.get("x", 0); y = b["y"]; col = COL.get(b.get("color", "white"), COL["white"])
    k = b["kind"]
    if k == "title":
        L = cached(("t", b["text"], b.get("size", 96), b.get("color")), lambda: text_layer(b["text"], font("x", b.get("size", 96)), col + (255,), 0))
        place(cv, L, cx, y, p, a)
    elif k in ("big",):
        sz = b.get("size", 110)
        L = cached(("b", b["text"], sz, b.get("color")), lambda: glow(text_layer(b["text"], font("x", sz), col + (255,), 8, (0, 0, 0)), col, 22, 0.55))
        if b.get("emoji"):
            E = cached(("e", b["emoji"], 170), lambda: emoji(b["emoji"], 170))
            place(cv, E, cx, y - 150, back((age - 0.08) / 0.3), a, rot=math.sin(t * 3) * 6)
            place(cv, L, cx, y + 40, p, a)
        else:
            place(cv, L, cx, y, p, a)
    elif k == "text":
        sz = b.get("size", 60)
        L = cached(("x", b["text"], sz, b.get("color")), lambda: text_layer(b["text"], font("b", sz), col + (255,)))
        place(cv, L, cx, y + (1 - ease(age / 0.3)) * 30, 1, a)
    elif k == "counter":
        n = int(b["n"] * ease(age / 1.1))
        L = text_layer(str(n), font("x", 300), COL["white"] + (255,), 8, (0, 0, 0))
        place(cv, glow(L, COL["mint"], 26, 0.45) if age > 1.1 else L, cx, y + 120, p, a)
        lab = cached(("cl", b["label"]), lambda: text_layer(b["label"], font("s", 58), COL["muted"] + (255,)))
        place(cv, lab, cx, y + 330, 1, ease((age - 0.3) / 0.3))
        if b.get("emoji"):
            E = cached(("e", b["emoji"], 150), lambda: emoji(b["emoji"], 150))
            place(cv, E, cx, y - 110, back((age - 0.1) / 0.3), a, rot=math.sin(t * 4) * 8)
    elif k == "stamp":
        sz = b.get("size", 180)
        def mk():
            L = text_layer(b["text"], font("x", sz), col + (255,))
            box = rbox(L.width + 60, L.height + 30, None, col + (255,), 30, 12)
            box.alpha_composite(L, ((box.width - L.width) // 2, (box.height - L.height) // 2)); return box
        S_ = cached(("s", b["text"], sz), mk)
        sc = 1.8 - 0.8 * ease(age / 0.18)
        place(cv, S_, cx, y, sc, ease(age / 0.1), rot=-6)
    elif k == "pill":
        def mk():
            L = text_layer(b["text"], font("b", 52), col + (255,))
            box = rbox(L.width + 70, 110, (20, 28, 44, 235), col + (255,), 55, 4)
            box.alpha_composite(L, ((box.width - L.width) // 2, (box.height - L.height) // 2)); return box
        place(cv, cached(("p", b["text"], b.get("color")), mk), cx, y, p, a)
    elif k == "card":
        def mk():
            box = rbox(400, 330, (18, 26, 42, 245), col + (255,), 36, 6)
            L1 = text_layer(b["text"], font("x", 120), col + (255,)); L2 = text_layer(b["sub"], font("d", 84), COL["white"] + (255,))
            box.alpha_composite(L1, ((box.width - L1.width) // 2, 30)); box.alpha_composite(L2, ((box.width - L2.width) // 2, 185)); return box
        place(cv, cached(("c", b["text"]), mk), cx, y + 170, p, a, rot=(-3 if b.get("x", 0) < 0 else 3))
    elif k == "chip":
        def mk():
            L = text_layer(b["text"], font("b", 56), COL["white"] + (255,))
            box = rbox(400, 120, (18, 26, 42, 245), COL["mint"] + (255,), 60, 4)
            box.alpha_composite(L, ((box.width - L.width) // 2, (box.height - L.height) // 2)); return box
        place(cv, cached(("ch", b["text"]), mk), cx, y, p, a)
    elif k == "ring":
        R = 300; im = Image.new("RGBA", (2 * R + 60, 2 * R + 60), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        bb = (30, 30, 30 + 2 * R, 30 + 2 * R); d.ellipse(bb, outline=(34, 44, 66, 255), width=48)
        ang = 360 * b["n"] / b["of"] * ease(age / 1.0)
        if ang > 1: d.arc(bb, -90, -90 + ang, fill=COL["red"] + (255,), width=48)
        n = int(b["n"] * ease(age / 1.0))
        L = text_layer(str(n), font("x", 230), COL["white"] + (255,))
        im.alpha_composite(L, ((im.width - L.width) // 2, R - 170))
        l2 = text_layer(f"of {b['of']} price markets", font("s", 44), COL["muted"] + (255,))
        im.alpha_composite(l2, ((im.width - l2.width) // 2, R + 90))
        place(cv, im, cx, y, p, a)
        l3 = cached("ringl", lambda: text_layer("have NO real quotes", font("x", 76), COL["red"] + (255,), 6, (0, 0, 0)))
        place(cv, l3, cx, y + 430, back((age - 0.5) / 0.3), ease((age - 0.5) / 0.15))
    elif k == "book":
        def mk():
            box = rbox(820, 230, (18, 26, 42, 245), col + (255,), 30, 6)
            L1 = text_layer(b["side"], font("b", 80), col + (255,)); L2 = text_layer(b["px"], font("x", 150), COL["white"] + (255,))
            box.alpha_composite(L1, (60, (box.height - L1.height) // 2)); box.alpha_composite(L2, (box.width - L2.width - 50, (box.height - L2.height) // 2)); return box
        place(cv, cached(("bk", b["side"]), mk), cx, y + 115, p, a)
    elif k == "compare":
        rows = b["rows"]   # [[label, value A, value B], ...] in %, max 3; legend names from b["names"]
        names = b.get("names", ["market", "model"])
        d = ImageDraw.Draw(cv)
        for i, (lab, mk_, md) in enumerate(rows):
            aa = ease((age - i * 0.35) / 0.25)
            if aa <= 0: continue
            yy = y + i * 220; g = ease((age - i * 0.35) / 0.8)
            L = cached(("cmp", lab), lambda: text_layer(lab, font("b", 50), COL["white"] + (255,)))
            place(cv, L, 100 + L.width / 2, yy + 20, 1, aa)
            for j, (v, c, nm) in enumerate(((mk_, COL["mint"], names[0]), (md, COL["muted"], names[1]))):
                y2 = yy + 75 + j * 60
                d.rounded_rectangle((100, y2, 780, y2 + 40), 20, fill=(28, 38, 58))
                ww = int(680 * v / 100 * g)
                if ww > 20: d.rounded_rectangle((100, y2, 100 + ww, y2 + 40), 20, fill=c)
                T_ = cached(("cv", v, nm), lambda: text_layer(f"{v}% {nm}", font("b", 40), c + (255,)))
                place(cv, T_, 810 + T_.width / 2, y2 + 20, 1, aa)
    elif k == "market":
        def mk():
            box = rbox(900, 250, (18, 26, 42, 245), (60, 74, 100, 255), 30, 3)
            for txt, f, c, xy in ((b["title"], font("s", 42), COL["muted"], (40, 28)),
                                  (b["left"], font("x", 80), COL["mint"], (40, 110)), (b["right"], font("x", 80), COL["red"], None),
                                  (b.get("mid", ""), font("b", 52), COL["amber"], (405, 125))):
                if not txt: continue
                L = text_layer(txt, f, c + (255,)); box.alpha_composite(L, xy or (box.width - L.width - 30, 110))
            return box
        place(cv, cached(("mkt", b["title"]), mk), cx, y + 125, p, a)
    elif k == "eq":
        L = cached(("eq", b["text"]), lambda: glow(text_layer(b["text"], font("x", 100), col + (255,), 6, (0, 0, 0)), col, 18, 0.4))
        place(cv, L, cx, y, p, a)

def captions(cv, t):
    s = next((s for s in TL["sentences"] if s["start"] - 0.05 <= t <= s["end"] + 0.25), None)
    if not s: return
    words = [w for w in s["words"] if w["s"] - 0.03 <= t]
    if not words: return
    f = font("x", 78); space = 22
    # lay out all sentence words (fixed positions), draw only spoken ones
    layers = [cached(("cw", w["w"].upper(), cur), (lambda w=w, cur=cur: text_layer(w["w"].upper(), f, (COL["amber"] if cur else COL["white"]) + (255,), 7, (0, 0, 0))))
              for w in s["words"] for cur in [w["s"] - 0.03 <= t < w["e"] + 0.02]]
    lines, cur, cw = [], [], 0
    for i, L in enumerate(layers):
        if cur and cw + L.width - 40 + space > 940: lines.append(cur); cur, cw = [], 0
        cur.append(i); cw += L.width - 40 + space
    lines.append(cur)
    y0 = 1430 - (len(lines) - 1) * 50
    for li, ln in enumerate(lines):
        tot = sum(layers[i].width - 40 for i in ln) + space * (len(ln) - 1); x = W / 2 - tot / 2
        for i in ln:
            L = layers[i]; w = s["words"][i]
            if w["s"] - 0.03 <= t:
                pp = back((t - w["s"] + 0.03) / 0.16)
                place(cv, L, x + (L.width - 40) / 2, y0 + li * 100, 0.6 + 0.4 * pp, ease((t - w["s"] + 0.03) / 0.08))
            x += L.width - 40 + space

def chrome(cv, t):
    d = ImageDraw.Draw(cv)
    d.rectangle((0, 0, W, 12), fill=(25, 32, 48)); d.rectangle((0, 0, int(W * t / DUR), 12), fill=COL["mint"])

def frame(t):
    off = int((t * 18) % 90)
    base = BGI.crop((0, off, W, off + H)).convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    active = None
    for b in BEATS:
        if b["t"] <= t: active = b["group"]
    if active:
        for b in BEATS:
            if b["group"] == active: draw_beat(layer, b, t)
    # camera: punch-in on each new group, shake on impacts
    g0 = GROUP_START.get(active, -9) if active else -9
    z = 1 + 0.07 * (1 - ease((t - g0) / 0.35)) if t - g0 < 0.35 else 1.0
    sx = sy = 0
    for b in BEATS:
        if b.get("sfx") in ("impact", "riser_hit") and 0 <= t - b["t"] < 0.3:
            amp = 18 * (1 - (t - b["t"]) / 0.3); sx = random.uniform(-amp, amp); sy = random.uniform(-amp, amp)
    if z != 1.0:
        zl = layer.resize((int(W * z), int(H * z)), Image.BICUBIC); layer = zl.crop(((zl.width - W) // 2, (zl.height - H) // 2, (zl.width - W) // 2 + W, (zl.height - H) // 2 + H))
    base.alpha_composite(layer, (int(sx), int(sy)))
    captions(base, t); chrome(base, t)
    return base.convert("RGB")

if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "stills":
        for tt in map(float, sys.argv[2:]):
            frame(tt).save(os.path.join(WORK, f"st_{tt:05.2f}.jpg"), quality=88)
        sys.exit()
    # render in 4 chunks in parallel, then join
    n = round(DUR * FPS); chunks = int(sys.argv[2]) if len(sys.argv) > 2 else 1; ci = int(sys.argv[1]) if sys.argv[1:] else 0
    a, b = n * ci // chunks, n * (ci + 1) // chunks
    tmp = os.path.join(WORK, f"part{ci}.tmp.mp4")
    p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                          "-c:v", "libx264", "-crf", "19", "-preset", "veryfast", "-pix_fmt", "yuv420p", tmp], stdin=subprocess.PIPE)
    random.seed(ci)
    for i in range(a, b): p.stdin.write(frame(i / FPS).tobytes())
    p.stdin.close()
    if p.wait() == 0: os.replace(tmp, os.path.join(WORK, f"part{ci}.mp4"))

"""Record the editorial page with headless Chrome.
Usage: record.py sheet out.png       -> style sheet with every prop
       record.py stills t1 t2 ...   -> WORK/st_<t>.jpg
       record.py <part> <parts>     -> WORK/part<part>.mp4 (30 fps, H.264)
Needs WORK and CACHE env vars (set by make.sh) and WORK/timeline.json from voice.py."""
import base64, json, os, shutil, subprocess, sys
from playwright.sync_api import sync_playwright

WORK, CACHE = os.environ["WORK"], os.environ["CACHE"]
HERE = os.path.dirname(os.path.abspath(__file__))
FPS = 30
TLP = os.path.join(WORK, "timeline.json")
TL = json.load(open(TLP)) if os.path.exists(TLP) else {"sentences": [], "dur": 1}
FONTS = {"Playfair": "Playfair.ttf", "Outfit": "Outfit.ttf"}
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


def open_page(p):
    b = p.chromium.launch(executable_path=CHROME, args=["--disable-gpu", "--force-color-profile=srgb"]) if CHROME else p.chromium.launch()
    pg = b.new_page(viewport={"width": 1080, "height": 1920})
    pg.goto("file://" + os.path.join(HERE, "page.html"))
    for fam, fn in FONTS.items():
        data = base64.b64encode(open(os.path.join(CACHE, "fonts", fn), "rb").read()).decode()
        pg.evaluate("""async ([f, d]) => { const ff = new FontFace(f, `url(data:font/ttf;base64,${d})`, {weight: '100 900'}); await ff.load(); document.fonts.add(ff); }""", [fam, data])
    pg.evaluate("tl => setup(tl)", TL)
    return b, pg


def frame(pg, t):
    url = pg.evaluate("t => { render(t); return document.getElementById('c').toDataURL('image/jpeg', 0.93); }", t)
    return base64.b64decode(url.split(",", 1)[1])


with sync_playwright() as p:
    b, pg = open_page(p)
    if sys.argv[1] == "sheet":  # record.py sheet <out.png>: every prop on one page
        open(sys.argv[2], "wb").write(base64.b64decode(pg.evaluate("styleSheet()").split(",", 1)[1]))
    elif sys.argv[1] == "stills":
        for s in sys.argv[2:]:
            open(os.path.join(WORK, f"st_{float(s):05.2f}.jpg"), "wb").write(frame(pg, float(s)))
    else:
        part, parts = int(sys.argv[1]), int(sys.argv[2])
        n = int(TL["dur"] * FPS); a, z = n * part // parts, n * (part + 1) // parts
        ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", os.path.join(WORK, f"part{part}.mp4")], stdin=subprocess.PIPE)
        for i in range(a, z):
            ff.stdin.write(frame(pg, i / FPS))
        ff.stdin.close(); ff.wait()
    b.close()

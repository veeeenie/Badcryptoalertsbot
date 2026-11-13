"""Flag on-screen text that repeats what the voice/subtitle says at the same moment."""
import json, re, sys
ep = json.load(open(sys.argv[1]))
STOP = set("the a an and of to in on at for is was be by it its that this with as or from are you your will can more than about into up down just one".split())
def toks(s):
    s = s.lower().replace('*', '')
    out = set()
    for w in re.findall(r"[a-z]+|\d[\d,.]*", s):
        w = w.replace(',', '').rstrip('.')
        if w in STOP or (w.isalpha() and len(w) < 3): continue
        out.add(w[:-1] if w.isalpha() and w.endswith('s') and len(w) > 4 else w)
    return out
FIELDS = ["text", "sub", "note", "tag", "value", "fee", "labLo", "endLab", "markLab", "hiLab", "from", "to"]
live = {}; bad = 0
for i, s in enumerate(ep["sentences"]):
    disp = " ".join(tk.split("|")[0] for tk in s["text"].split(" "))
    spoken = disp + " " + " ".join(tk.split("|")[1].replace("-", " ") for tk in s["text"].split(" ") if "|" in tk)
    for _, b in s["beats"]:
        k = b["kind"]
        if k == "clear": live = {}
        elif k == "add": live[b["id"]] = b
        elif k == "remove": live.pop(b["id"], None)
        elif k == "set" and b["id"] in live: live[b["id"]] = {**live[b["id"]], **b}
        elif k == "chapter": live["__chap"] = {"text": b["text"]}
    st = toks(spoken)
    for pid, p in live.items():
        txt = " ".join(str(p.get(f, "")) for f in FIELDS) + " " + " ".join(r[0] for r in p.get("rows", []))
        hit = toks(txt) & st
        if hit: bad += 1; print(f"#{i:02d} '{disp[:50]}'  <->  {pid}: {sorted(hit)}")
print("overlaps:", bad)

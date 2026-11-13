# Daily videos for @conxixent

One short vertical video a day (40–55 s) about Hyperliquid, trade.xyz, Metric and Flop: an independent aggregator, built from real data and news. The trader posts it on X himself.

## Build

```
content/engine/make.sh content/episodes/<slug>.json stills 3.2 11.5 20.1   # key frames first, look at them
content/engine/make.sh content/episodes/<slug>.json                        # full build -> content/out/<slug>.mp4 (~2 min)
```
First run downloads the free voice model (Kokoro, Apache-2.0), the Inter font and installs Python packages.

## Episode file

`content/episodes/YYYY-MM-DD-<topic>.json`, see `2026-11-10-hl-prediction-markets.json` (the approved look):

```
{"slug": "...", "date": "10 NOV", "voice": "af_heart", "speed": 1.2,
 "sentences": [{"text": "Hyperliquid has 300|three-hundred prediction markets.",
                "beats": [[0, {"kind": "title", "group": "A", "text": "HYPERLIQUID", "y": 330, "sfx": "whoosh"}], ...]}]}
```
- A token `display|spoken-words` shows `display` in the captions and says the spoken words (hyphens become spaces). Use it for numbers, $, ¢, tickers ("BTC|bitcoin", "75K|seventy-five-K", "trade.xyz,|trade-x-y-z").
- Beats fire at a word index of their sentence. A new `group` clears the screen (punch-in zoom). Beat kinds and fields are listed at the top of `engine/render.py`.
- Sound effects per beat (`sfx`): whoosh (new scene), pop (element), ticks (counter), ding (payoff), impact (big reveal, shakes the screen), impact_soft, riser_short (tease), riser_hit (build-up + hit).
- Visuals go between y 250 and 1200; captions use 1250–1600. Big text at most ~16 characters at size 94–110.

## Style (what the trader approved)
- Hook in the first second: the claim or number on screen at once. No intro, no logo.
- A new visual every 1–2 s; 18–26 short sentences; 40–55 s total.
- Open loop early ("…with one catch"), pay it off near the end.
- One takeaway per video: a single sentence the viewer can repeat or use (e.g. "HIP-3 is the stock market, HIP-4 is the betting market"). Decide it before writing; it is the catch's payoff, shown on screen as its own beat just before the follow line.
- Kinetic word captions, emoji icons, grid background, no header text (progress bar only).
- @conxixent is drawn small under the captions on every frame (render.py, HANDLE), so a clip or screenshot still carries it.
- End: "Follow for daily Hyperliquid, trade.xyz, Metric and Flop data." + "Not financial advice." line, @conxixent shown.
- No project logos or brand art, no impersonation: an independent account.
- Every number must come from a source checked that day (repo data files or a page actually opened). No hype, no "guaranteed airdrop", no price targets.

## Editorial style (default; 4:5 with characters from 13 Nov 2026)

**Read `DESIGN.md` first**: format, colours, characters, camera, text, music and copy rules. Reference episode: `episodes/2026-11-13-btc-liquidations.json` (4:5, bull and bear, close-ups, rust hook and catch frames, no repeated text, tense music). Engine: `engine/editorial/page.html` (canvas, every prop is a draw function) recorded by `engine/editorial/record.py` with headless Chrome. An 85-second build takes about 10 minutes.

Episode fields: `"style": "editorial"`, `"format": "4:5"`, `"mood": "tense"|"neutral"|"upbeat"`, `"music": "<part of a track file name>"` or `"generated"`, optional `"hook": false` (no rust opening frame).

Music: tracks are read from `content/.cache/music/` (or `$MUSIC_DIR`), never committed. Clone the private repo `veeeenie/conxixent-music` there when it's available. audio.py beat-tracks the track (librosa), starts it where its beats line up with the cuts, loops with a crossfade, and drops it out before the catch. Without tracks it uses a generated bed (`mood` tense = dark minor pulse).

Check before building: `python3 content/engine/editorial/checktext.py <episode.json>` (on-screen text must not repeat the spoken line; it must print `overlaps: 0`).

Beats (`[word index, {...}]`, fired at that word of the sentence; `sfx`: pop, whoosh, ding, impact, impact_soft, riser_short, ticks):
- `{"kind":"chapter","text":"Why it fell"}`: the section label at the top (4:5). Must not repeat words spoken while it shows.
- `{"kind":"clear"}`: ends the scene (a cut: wipe, zoom-through, slide or ink wipe, chosen automatically).
- `{"kind":"shot","z":2.0,"x":700,"y":555}`: hard cut to a close-up framing stage point x,y; `{"kind":"shot"}` cuts back to wide.
- `{"kind":"bg","mode":"rust"}`: rust frame for this scene (use on the catch; the first scene gets it automatically). `{"kind":"drop"}`: music drop at that word.
- `{"kind":"add","id":"g","prop":"gauge","x":540,"y":700, ...}`: add a prop (stage coordinates, y 430–1220). `"pop": false` skips the bounce.
- `{"kind":"move","id":..,"x":..,"y":..,"s":..,"dur":0.6}`, `{"kind":"set","id":..,<field>:<value>}` (numeric fields animate from their old value), `{"kind":"remove","id":..}`.
- 9:16 only: `{"kind":"head","text":"All *airdropped.*"}` headline.

Props and their fields:
- Characters: `bull` / `bear` s, mood (calm|happy|sad|shock|sweat), action (pay|cheer|hold), dir, tag, fall (set 1 to tip over)
- Money: `coin` r, label · `coinflow` dx, h, period, dir, r (coins arcing left→right) · `stack` n, r, value, sub · `pile` sub · `jar` fill (0–1, set to fill), drop, value
- Dials and charts: `gauge` v (set to swing), min, max, unit, sub, seq + dt (steps through values) · `gauges` cols, rows, hi (set: that one swings to max) · `path` vals (% moves), lo (dashed red line; set to move), labLo, endCol, endLab, note, dur · `hours` vals, hi, hiLab, from, to · `bars` rows [[label, pct, colour]], vals · `donut` pct, sub · `thermo` level (set to rise), mark, markLab, note
- Objects: `tollbooth` fee, lift, open · `clock` h, spin, label, sub · `arrow` dir, sym, len, col, note · `chain` · `agent` · `gpu` · `ticket` · `proof` · `parachute` · `halving` · `timeline` items, upto, note
- Text (use sparingly, never repeating the voice): `label` text (`*accent*`), size · `big` text, size, color, sub · `small` text, size · `stamp` text, size · `cross` w · `x` r

Add a new prop to `DRAW` in page.html when a story needs one (palette `P`, helpers `card()`, `solid()`, `ink()`, `lbl()`, `num()`, `note()`, tokens `DS`), add it to `styleSheet()`, and check the sheet.

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
- Kinetic word captions, emoji icons, grid background, no header text (progress bar only).
- @conxixent is drawn small under the captions on every frame (render.py, HANDLE), so a clip or screenshot still carries it.
- End: "Follow for daily Hyperliquid, trade.xyz, Metric and Flop data." + "Not financial advice." line, @conxixent shown.
- No project logos or brand art, no impersonation: an independent account.
- Every number must come from a source checked that day (repo data files or a page actually opened). No hype, no "guaranteed airdrop", no price targets.

## Editorial style (default from 11 Nov 2026)

Set `"style": "editorial"` in the episode. Approved look: `episodes/2026-11-10-flop-editorial.json` (warm cream background, Playfair serif headline with one rust accent word, drawn props that act out the idea, a small 3–4-word caption pill, @conxixent at the bottom). Engine: `engine/editorial/page.html` (canvas, every prop is a draw function) recorded by `engine/editorial/record.py` with headless Chrome. A full build takes about 8 minutes.

Beats (same `[word index, {...}]` timing as before; `sfx` works the same, keep it soft: pop, whoosh on clear, ding on a payoff, impact_soft for a stamp, riser_short for the catch):
- `{"kind":"head","text":"All *airdropped.*"}`: headline, 2–4 words, `*...*` = rust accent. Fades the old one out.
- `{"kind":"clear"}`: fades out the scene; props added at the same moment wait until it's gone. One scene per idea.
- `{"kind":"add","id":"g","prop":"gpu","x":760,"y":840, ...}`: add a prop (x is the centre, 540 = middle; keep props between y 430 and 1220). `"pop": false` skips the bounce.
- `{"kind":"move","id":"g","x":..,"y":..,"s":0.5,"dur":0.6}`, `{"kind":"set","id":"ch","hi":2}`, `{"kind":"remove","id":"g"}`.

Safe area: X's phone player covers the top ~12% and bottom ~28% of the video, so the page keeps everything between y 400 and 1370 (headline at 470, caption at 1272, handle at 1362). Prop coordinates are stage coordinates (y 430–1220, x 0–1080) and are scaled 0.8 around the centre automatically; keep side props between x 180 and 900, and keep any text a prop draws above stage y 1160 so it clears the caption pill.

Props and their fields:
- `coin` r, label · `nameplate` name, sub · `chain` n, size, hi (highlighted block) · `agent` s, label · `gpu` s, label, spin
- `ticket` title, fee · `proof` (check mark draws itself) · `label` text (`*accent*`), size, x · `cross` w (strike through a label) · `x` r (red X over something)
- `parachute` (drops a coin) · `stack` n coins, value, sub · `halving` values [96,48,...] · `bars` rows [[label, pct, "acc"|"ink"|"mut"|"gold"|"sage"|"red"]]
- `pile` sub · `donut` pct, sub · `timeline` items [[when, what]], upto (reveal more with set), note · `stamp` text, size · `big` text, size, color, sub · `small` text

Add a new prop to `DRAW` in page.html when a story needs one (simple shapes, the palette in `P`), and check it in stills.

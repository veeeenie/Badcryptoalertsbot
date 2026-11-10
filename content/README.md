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

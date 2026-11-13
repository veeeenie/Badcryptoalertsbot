# @conxixent design language (editorial style)

Every video should look like it came from the same account. This file is the rule book; the engine (`engine/editorial/page.html`) builds most of it in. Check new props against the style sheet: `WORK=/tmp/ds CACHE=content/.cache python3 content/engine/editorial/record.py sheet sheet.png`.

Adopted 10 Nov 2026; reworked 13 Nov 2026 after the trader's feedback (more happening on screen, real cuts, characters, 4:5, no repeated text, mood-matched music, more depth). Reference episode: `episodes/2026-11-13-btc-liquidations.json`.

## 1. Feel
Warm, clear, and busy in a good way: a story acted out by two characters and simple objects, cut like an explainer reel. Something moves or changes every 1.5–2 s. One idea per scene.

## 2. Format
- **4:5, 1080×1350** (`"format": "4:5"` in the episode). X's feed player covers almost nothing at this size.
- Top: the **chapter label** (small pill, beat kind `chapter`), changing only when the story moves on (3–6 chapters per video). There is **no headline** in 4:5.
- Middle: the scene (stage y 430–1220, centre 825, scale 1.0; keep side props between x 150 and 930).
- Bottom: the caption pill (66 px, whole phrase shown solid, current word rust) and @conxixent.

## 3. Colour (the palette `P`; no other colours)
| Role | Token | Use |
|---|---|---|
| Background | `bg` #EFE7DA | The cream room with the floor line. |
| Rust frame | `acc` #B5532B | Full rust background with a cream spotlight: automatic on the first scene (the hook), and on the catch (`{"kind":"bg","mode":"rust"}`). It stands out against X's white and black feeds. |
| Text | `ink` #2B1E16 | Numbers, labels. |
| Notes | `mut` #7F6C5A | Small print, sources. |
| Accent | `acc` | The current caption word, the hero element of a scene (one per scene), rust wipes. |
| Value | `gold` #D7A544 | Coins, money, fee meters. Gold means value. |
| Objects | `dark` #3B2F27 | Machines, booths, arrows. |
| Cards | `paper` #FBF7EF, edge `line` | Charts, dials, labels. |
| Characters | `bull` #C0643A, `bear` #6B5444, `ivory` | The bull and the bear only. |
| Success | `sage` | Check marks only. |
| Warning | `red` #B33A2C | Stamps, X marks, liquidation lines: risk and "no". |

## 4. Characters
- **The bull is the longs, the bear is the shorts.** They carry the story: they pay, catch, cheer, sweat, get shocked, tip over.
- Props `bull` / `bear`: `s` (0.3–0.6), `mood` (calm, happy, sad, shock, sweat), `action` (pay, cheer, hold), `dir`, `tag` (a small word under the feet, e.g. "20x"), `fall` (set to 1 to tip over; a row of bulls falling one by one is a cascade).
- Use them in most scenes. Several small bulls = a crowd of traders.

## 5. Camera and cuts
- The camera stays level (no tilts). Every scene is a new shot with a slow push, pull or pan.
- **Real angles:** within a scene, cut between a wide shot and close-ups with `{"kind":"shot","z":1.6–2.6,"x":..,"y":..}` (stage coordinates of what to frame) and back with `{"kind":"shot"}`. Typical scene: wide → close-up on the detail (the meter, the needle, the jar, a face) → wide or another detail. Keep characters inside the frame at the zoom you pick.
- Scenes without shot beats get automatic punch-ins on each new prop.
- Scene changes are cuts: rust wipe, zoom-through, slide, ink wipe (automatic). Payoff beats (`ding`, `impact`, …) add a short shake, flash and light rays.
- Props pop in; needles swing; numbers count (never past their value); bars, lines and checks draw themselves.

## 6. Text on screen: never repeat the voice
- The caption pill already shows what is said. **On-screen text must never repeat the words or numbers being spoken** (no "$31M" while the voice says "$31 million", no "The catch." while it says "Now the catch").
- Show numbers as things: piles, stacks, jars, gauges without numbers, bars, lines, toppling bulls.
- On-screen text only adds what isn't said: the source, the date or time window, a unit, "forecast", leverage tags.
- Check it: `python3 content/engine/editorial/checktext.py <episode.json>` must report `overlaps: 0` (chapter labels count too).

## 7. Sound and music
- Effects: soft pop/snap on props, swish on cuts, boom on scene changes, ticks on counting, `impact`/`impact_soft` for stamps and shocks, `riser_short` before the catch.
- **Music matches the story's mood.** Set `"mood"` and `"music"` (part of a track's file name) in the episode:
  - tense (liquidations, crashes, hacks, scams): `hip-hop-beat` (approved 13 Nov), `cyberpunk`, `viacheslavstarostin-background-music`, `synthwave`; or `"music": "generated"` with `"mood": "tense"`.
  - neutral (explainers, how-it-works): `lo-fi`, `chill-beats-instrumental`, `vlog`, `lnplusmusic-background-music`.
  - upbeat (launches, records, airdrops): `upbeat-electronic`, `upbeat-motivation`, `funky-house`, `upbeat-summer`, `future-bass`, `product-launch`.
  - The tracks live in the private repo `veeeenie/conxixent-music` (never in this public repo: the Pixabay licence forbids redistributing the files); copy them to `content/.cache/music/`. Without them, use `"music": "generated"`.
- The music drops out for a beat before the rust catch frame (automatic) and stays under the voice.

## 8. Voice and copy
- Kokoro `af_heart` at 1.2. Short, plain sentences.
- **Go into depth; don't cut context.** 25–35 sentences, 60–100 s. Structure: hook (what happened, the striking number) → "With one catch." → why it happened → how it works (with live numbers) → today's numbers → the catch (rust frame) → the takeaway → end card.
- No hype words, no price predictions, no "guaranteed". Uncertain facts stay out; approximations are said as approximations ("about", "roughly").
- Every number comes from a source checked that day.
- End: voice "Follow for daily Hyperliquid, trade.xyz, Metric and Flop data." + "Not financial advice."; screen shows @conxixent big, "new videos at 10:00 and 18:00 IST", the bull and bear cheering, and the data sources in small print (not the four names, which are spoken).

## 9. Before shipping
- `checktext.py` reports 0 overlaps.
- Stills, one per scene: nothing important cropped by a close-up, nothing under the caption pill, no prop overlapping another, characters inside the spotlight on rust frames.
- Only palette colours; gold only for value; red only for risk.
- New props sit naturally on the style sheet next to the others.

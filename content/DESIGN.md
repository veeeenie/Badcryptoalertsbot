# @conxixent design language (editorial style)

Every video should look like it came from the same account. This file is the rule book; the engine (`engine/editorial/page.html`) builds most of it in. Check new work against the style sheet: `WORK=/tmp/ds CACHE=content/.cache python3 content/engine/editorial/record.py sheet sheet.png`.

Adopted 10 Nov 2026. Reference: the instant-coffee explainer reel the trader liked (warm, calm, editorial).

## 1. Feel
Calm, warm, clear. A well-made magazine explainer, not a hype channel. One idea at a time, acted out by simple objects. If a frame needs explaining, it has too much in it.

## 2. Colour (the palette `P`; no other colours)
| Role | Token | Use |
|---|---|---|
| Background | `bg` #EFE7DA, floor `bg2` | Always the cream room with the table line. Never change the background. |
| Text | `ink` #2B1E16 | Headlines, labels, numbers. |
| Notes | `mut` #7F6C5A | Prop captions, small print. |
| Accent | `acc` #B5532B (rust) | The one accent word in a headline, the current caption word, and the single "hero" element of a scene (the highlighted block, the main bar). At most one rust thing per scene besides the headline. |
| Value | `gold` #D7A544, `goldD` | Coins and anything that is money or tokens. Gold means value, nothing else. |
| Objects | `dark` #3B2F27 | Machines, blocks, solid things. |
| Cards | `paper` #FBF7EF, edge `line` | Tickets, proofs, nameplates, labels. |
| Success | `sage` #6E8A62 | Check marks and "this worked" only. |
| Warning | `red` #B33A2C | Stamps, X marks, strike-throughs: scams, risks, "no". Never decoration. |

## 3. Type
- Headline: Playfair Display 800, 104 px, 2–4 words, one `*accent*` word, ends with a full stop. Sits at y 470.
- Big numbers: Playfair 800, 120 px (`num()`), up to 160–220 for a single hero number.
- Labels and captions: Outfit 600, 50 px (`lbl()`); notes Outfit 500, 42 px (`note()`); fine print 34 px.
- Caption pill: Outfit 600, 58 px, 3–4 words at a time, current word in rust.
- Sentence case everywhere except stamps (caps). No emoji on screen.

## 4. Layout
- Safe area: X's phone player covers the top ~12% and bottom ~28%, so everything lives between y 400 and 1370 (headline 470, scene, caption pill 1272, @conxixent 1362). Built in.
- Prop coordinates are stage coordinates (y 430–1220), scaled 0.8 around the centre automatically. Side props stay between x 180 and 900; any text a prop draws stays above stage y 1160.
- One idea per scene. At most 3 props on screen (a group like bars or a timeline counts as one). The main prop is centred, or two props sit left/right as a pair (x ≈ 290 / 790).
- Leave air: if a scene feels full, split it into two scenes.

## 5. Drawing props
- Flat shapes, rounded corners (`DS.radius` 22), one soft floor shadow (`shadow()`), front view. No gradients on objects, no 3D, no textures.
- Two families only: paper cards (`card()`: paper fill, thin edge) and solid objects (`solid()`: dark fill). Drawn icons use the ink outline (`ink()`, 7 px).
- Sizes: small ≈ 180 px, medium ≈ 300, large ≈ 440 wide (`DS.small/medium/large`).
- Only the agent has a face. No people, no logos, no brand art, no real product shapes.
- A new prop is built from `card()`, `solid()`, `ink()`, `lbl()`, `num()`, `note()` and palette colours, then checked on the style sheet next to the others before it ships.

## 6. Motion
- Props pop in with a small bounce (0.3 s). Moves ease out over ~0.6 s. Scenes fade out (0.22 s) before the next fades in. Headlines fade up.
- Numbers count up; bars, rings and check marks draw themselves.
- Nothing shakes, flashes, zooms or spins fast (the GPU fans are the exception, and speed up when it "works").
- A visible change every 1.5–2.5 s. A scene lasts 3–8 s.

## 7. Sound
Soft and sparse: pop when a prop appears, whoosh on a scene change, ding on a payoff, `impact_soft` only for a warning stamp, `riser_short` only for the "one catch" tease. The low beat stays under the voice.

## 8. Voice and copy
- Kokoro `af_heart` at 1.2. Short, plain sentences. 18–24 sentences, 45–55 s.
- Structure: hook in the first sentence → "With one catch." early → explain with scenes → the catch → end card.
- No hype words ("insane", "huge", "to the moon"), no price predictions, no "guaranteed". Uncertain facts stay out; targets and proposals are labelled as such.
- Every number on screen comes from a source checked that day.
- End card, always the same: "Follow for daily Hyperliquid, trade.xyz, Metric and Flop data." with the four labels and a big rust @conxixent, then "Not financial advice." as small print.

## 9. Before shipping, check the stills
- Headline 2–4 words, one rust word, not overlapping anything.
- Every prop inside the safe area; no text touching the caption pill.
- Only palette colours; at most one rust hero per scene; gold only for value; red only for warnings.
- Any new prop sits naturally on the style sheet next to the others.

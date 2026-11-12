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
| Accent | `acc` #B5532B (rust) | The one accent word in a headline, the current caption word, the single "hero" element of a scene (the highlighted block, the main bar), and the rust wipe between scenes. At most one rust thing per scene besides the headline. |
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

## 6. Motion (updated 12 Nov 2026: more cuts, more angles; the trader found the calm version too static)
Attention has to be re-earned every 1.5–2 s. The engine's camera director (`planCamera()` in page.html) does this automatically from the beats; episodes don't need extra fields.
- Every scene is a new shot with its own angle: push in, pull out, tilt left, pan right, tilt right (cycled). The camera drifts the whole time, so no frame is fully still.
- Each new prop, move or set-with-sfx inside a scene punches the camera toward it, alternating hard cuts and fast eases (0.22 s). If no beat lands for ~2 s, the camera reframes anyway.
- Scene changes are cuts, not fades. They cycle through a rust wipe, a zoom-through with blur, a whip pan, and an ink wipe.
- Payoff beats (`ding`, `impact`, `impact_soft`, `riser_hit`) get a short shake, a soft cream flash and light rays behind the subject.
- Headline words drop in one by one with a bounce; the accent word gets a faint rust marker. The caption pill pops on each new chunk. Big numbers (`big` with digits) count up.
- Props still pop in with a bounce (0.3 s); bars, rings and check marks draw themselves.
- The headline, caption pill and @conxixent are an overlay: the camera never moves them, so they stay readable.
- When writing beats: give each scene 2–4 beats (props, moves, sets) spread across its sentences, so the punches have targets. A scene with one prop and no other beats gets only the automatic reframes.

## 7. Sound
Punchy but under the voice: a swish on every camera punch, swish plus a low boom on every scene cut, a snap on every prop without its own sfx, ticks under counting numbers, a low hit under payoffs. Beat sfx work as before (pop, whoosh, ding, `impact_soft` for a warning stamp, `riser_short` for the "one catch" tease). The music bed has hats on every 8th and a clap on 2 and 4, ducked under the voice.

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

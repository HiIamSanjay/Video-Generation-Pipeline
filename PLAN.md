# Plan — API-driven daily ad generation

## Goal

The store API supplies one offer per day:

```json
{ "item": "vegetables", "percent": 20 }
```

Out comes a finished 10-second 16:9 ad: the relevant section of the store on screen,
`SALE` / `20% OFF ON VEGETABLES` rendered legibly, and a voiceover saying
"Get twenty percent off on vegetables". One command, no manual steps.

## Constraints (from the brief)

| Constraint | Consequence for the design |
|---|---|
| One product on sale per store per day | One render per day. No plate cache, no department library to pre-render. |
| A 10-minute render is acceptable | Generate fresh each time. Simplicity beats caching. |
| No separate video per department | Nothing is pre-built; the scene is derived from today's item. |
| LTX generates the audio itself | **No TTS engine.** The voiceover comes from the model. |

## Architecture

```
store API  →  { item: X, percent: N }
                     │
                     ▼
        1. resolve X → scene + grammar
                     │
                     ▼
        2. build prompt  (scene + VO line, NO on-screen text)
                     │
                     ▼
        3. POST /prompt → ComfyUI → poll /history   [~10 min]
                     │
                     ▼
              plate.mp4  (visuals + VO + music, no text)
                     │
                     ▼
        4. ffmpeg drawtext:  SALE
                             N% OFF ON X            [~5 s]
                     │
                     ▼
                out/<X>_<N>_<date>.mp4
```

### The one real design decision: text is burned in, not generated

Two renders have now produced broken on-screen text — `20%` duplicated, and `OFF`
rendered as `OF`. This is not a tuning problem. Diffusion models have no character-level
representation of text, and negative prompts (`duplicated text`, `misspelled words`,
`multiple 20s`) were already in place both times and did not prevent it.

So the render is asked for **no text at all**, and `SALE / N% OFF ON X` is composited
afterwards with ffmpeg. That yields:

- correct spelling, every time, for any item name and any percentage
- exact brand colours and font, crisp at any resolution
- a fully unattended pipeline — nothing that needs a human to proofread

Audio is untouched: LTX still generates the voiceover and music, so removing the on-screen
text costs nothing on the audio side. Removing text from the prompt also frees the model to
spend its capacity on the scene, which should improve the visuals.

An `-InModelText` flag will keep the current behaviour available for comparison.

## Phases

### Phase 0 — Prerequisites *(~30 min, no rendering)*

- Install ffmpeg: `winget install Gyan.FFmpeg` — not currently on this machine, required for the overlay.
- Export the **API-format** workflow from ComfyUI (`Workflow → Export (API)`) as `workflow_api.json`.
  The UI-format JSON we have been editing is *not* what `POST /prompt` accepts. This is the
  main gotcha in the build.
- `comfy_client.ps1` — submit a prompt, poll `/history/{id}`, return the output file path.
  Smoke-tested against the live server on `127.0.0.1:8188`.
- Confirm the voiceover in `output_D_00006.mp4` actually says the scripted line. If LTX's
  speech is unreliable, that reopens the TTS question — worth knowing before building on it.

### Phase 1 — Item → scene resolution *(no rendering)*

- `items.json` — for each known department: aliases, display name, grammar flags, and a tuned
  scene description. Example for `vegetables`: *"fresh produce section, crates of tomatoes,
  peppers and leafy greens, misted and glistening, bright market lighting"*.
- Resolver: item string → entry, via alias match, then fuzzy match, then a `general` store-aisle
  fallback so an unrecognised item never hard-fails.
- Grammar: mass nouns must not be pluralised — "20% off on rice", never "rices".
- Number-to-words for the voiceover line: `25` → "twenty five percent".

### Phase 2 — Prompt builder + renderer

- `generate_ad.ps1 -Item vegetables -Percent 20`
- Builds the positive prompt from the resolved scene, with the voiceover line embedded and an
  explicit instruction that no text, signage or lettering appears anywhere in the frame.
- Negative prompt gains `text, letters, words, numbers, signage, typography, watermark`.
- Patches `workflow_api.json`, queues it, waits, retrieves the result.
- Reuses the existing graph settings: 1280x704, 241 frames @ 24 fps, distilled branch only.

### Phase 3 — Text overlay

- Two-line composite via ffmpeg `drawtext`: `SALE` large in red with a gold outline,
  `N% OFF ON X` beneath it in white with a red outline.
- Auto-fit font size so long item names ("HOUSEHOLD ESSENTIALS") still fit the frame.
- Fade in over the first ~0.5 s so it does not pop.
- Audio stream copied through untouched.

### Phase 4 — Entry point and validation

- End-to-end: `generate_ad.ps1 -Item X -Percent N` → `out/<X>_<N>_<date>.mp4`
- Post-render validation: duration ≈ 10 s, resolution 1280x704, audio stream present and
  non-silent. Fail loudly rather than publish a broken ad.
- Thin adapter for the store API's real payload shape, plus a `-DryRun` that prints the
  generated prompt without queueing.

### Phase 5 — Documentation

- README: running a daily campaign, adding a department, tuning a scene description,
  troubleshooting a failed render.

## Open decisions

1. **Department list.** What sections does the store actually have — or a sample of real API
   payloads? The scene descriptions are hand-tuned per department, and that is what makes the
   produce aisle look like a produce aisle. A `general` fallback covers anything unlisted.
2. **Unknown items.** For an item with no entry, either use the generic fallback scene (free,
   instant, less specific) or call the Claude API to write a scene description on the fly
   (better visuals for long-tail items, needs an API key). The fallback is fine for v1.
3. **Overlay vs in-model text.** Recommending overlay as the default for the reasons above,
   with `-InModelText` retained as a flag. Confirm.

## Measured facts this plan is built on

| | |
|---|---|
| Render time, distilled branch only | **588 s (~10 min)** for 241 frames at 1280x704 |
| Render time with both branches | 4779 s (~80 min) — the Full branch alone cost ~70 min |
| GPU | RTX 5060 Ti, 16 GB — model needs ~20 GB, so ~11 GB offloads to RAM each run |
| Step rate | ~62 s/step, 8 steps (offloading is the bottleneck, not compute) |
| ComfyUI | 0.29.0, API confirmed live on `127.0.0.1:8188` |
| Python | Embedded 3.13.14 in the portable install (the PATH `python` is a broken WindowsApps stub) |
| ffmpeg | Not installed — Phase 0 |
| Output directory | `ComfyUI_windows_portable\ComfyUI\output\` |

Scripting in PowerShell throughout, consistent with `set_campaign.ps1`. No new runtime needed.

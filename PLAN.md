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
| LTX generates the audio itself | **No TTS engine.** The voiceover comes from the model — confirmed intelligible and accepted. |
| Video must be reachable over a Cloudflare Tunnel | A delivery phase: serve `out/` over local HTTP, front it with `cloudflared`, expose stable URLs. |
| Store API payload shape not yet known | Input is kept behind a thin adapter. The pipeline takes `-Item` / `-Percent` directly; the adapter is written once the payload is known. |

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
                     │
                     ▼
        5. local HTTP server  →  cloudflared tunnel
                     │
                     ▼
        https://<host>/latest.mp4   ← in-store display polls this
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

An `-in-model-text` flag keeps the current behaviour available for comparison.

**This is reversible at any time in ComfyUI.** The overlay approach only changes the positive
prompt (it asks for no text) and adds an ffmpeg step. To go back, restore the
`Line 1 reads: SALE` block in node `2483` and skip the overlay — or pass the flag. Nothing about
the graph is permanently committed to either approach.

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

Since the item list is not known in advance, scene descriptions are generated rather than
hand-maintained, with a curated layer in front for items we have already tuned.

1. **Curated first.** `items.json` — for departments we have dialled in: aliases, display name,
   grammar flags, and a tuned scene description. Example for `vegetables`: *"fresh produce
   section, crates of tomatoes, peppers and leafy greens, misted and glistening, bright market
   lighting"*. Deterministic and reproducible for anything that recurs.
2. **LLM expansion for anything unlisted.** Call the Claude API to write a scene description for
   an arbitrary item, then cache the result back into `items.json` so the same item is
   deterministic next time.
3. **Generic aisle fallback** if the API is unreachable, so a render never hard-fails.

**The LLM writes the scene description only** — not the whole prompt. The no-text instruction,
the voiceover line, the style block, and the negative prompt stay under our control and are
assembled around whatever it returns. Its system prompt must state explicitly that the frame
contains no text, signage or lettering; left unconstrained, an LLM will happily write "a neon
sign flashes 20% OFF TOYS" and reintroduce the exact defect the overlay exists to remove.

Also in this phase:
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

### Phase 5 — Delivery over Cloudflare Tunnel

- **Local server.** Serve the finished ads over HTTP on `127.0.0.1` using the portable
  install's embedded Python (`http.server`) — already present, no new dependency.
- **Stable endpoints**, so the display never needs its URL changed:
  - `GET /latest.mp4` — today's ad
  - `GET /manifest.json` — `{ item, percent, generated_at, duration, url, sha256 }`, so a
    client can detect a new ad without downloading the video
- **Serve only `out/`.** The server root is scoped to the output directory, read-only, with
  directory listing off. A tunnel pointed at a broader root would expose the machine.
- **Cache correctness.** Dated filenames (`/ads/vegetables_20_2026-08-03.mp4`) with
  `latest.mp4` as the pointer, and `Cache-Control: no-store` on the manifest — otherwise the
  display can keep playing yesterday's ad from cache.
- **`cloudflared`** (`winget install Cloudflare.cloudflared`) fronts the local server. Two modes:

  | | Quick tunnel | Named tunnel |
  |---|---|---|
  | Setup | none — `cloudflared tunnel --url http://127.0.0.1:8787` | Cloudflare account + a domain |
  | URL | random `*.trycloudflare.com` | stable hostname you control |
  | Survives restart | no, URL changes | yes |
  | Use | testing the pipeline | production — the display needs a fixed URL |

- **Note:** starting a tunnel publishes this machine's server to the public internet. I will not
  create one without an explicit go-ahead, and the quick tunnel is the right first step so
  nothing is bound to your domain while we are still testing.

> **Do not tunnel ComfyUI (port 8188) itself.**
>
> ComfyUI ships with no authentication. Anyone who obtains the URL can queue jobs on the GPU,
> browse and download everything in `output/`, and — via custom nodes and known path-traversal
> issues — read and write files on the host. A `trycloudflare.com` hostname is not a secret;
> such hostnames get scanned. "Secure tunnel" describes the transport, not the service behind it.
>
> The store system does not need to reach ComfyUI — it needs to submit an offer and collect a
> video. So the tunnel fronts a **read-only static server scoped to `out/`**, and ComfyUI stays
> bound to `127.0.0.1`.
>
> If remote *triggering* is needed later, the correct shape is a small API of our own behind a
> shared secret, which validates the payload and then talks to ComfyUI over localhost. Port 8188
> is never exposed.

### Phase 6 — Documentation

- README: running a daily campaign, adding a department, tuning a scene description,
  restarting the tunnel, troubleshooting a failed render.

## Resolved

- **Voiceover** — LTX's generated speech is intelligible and accepted. No TTS.
- **Departments** — not known yet, so Phase 1 ships a starter library for common grocery and
  general-store sections plus a `general` aisle fallback. Any unrecognised item still produces a
  working ad; entries get added and tuned as the real item list emerges. Not a blocker.
- **Double render** — fixed in `2c0eaf5` by muting `SaveVideo` #4823. Only
  `1.LTX_2.3_Workflow.json` carries the fix; the API export in Phase 0 must come from that file.

## Open decisions

1. **Overlay vs in-model text.** Recommending overlay as the default for the reasons above, with
   `-InModelText` retained as a flag for comparison. Confirm.
2. **Tunnel mode.** Quick tunnel for testing (zero setup, random URL), then a named tunnel for
   production. A named tunnel needs a Cloudflare account and a domain — say whether you have
   those, and whether the display can be pointed at a `trycloudflare.com` URL in the meantime.
3. **Trigger.** Does the pipeline run on a daily schedule (Task Scheduler at a set hour), or
   on-demand when the store API is polled and reports a new offer?

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

## Implementation language: Python

A migration to an Ubuntu partition is planned, so the pipeline is written in **Python**, not
PowerShell. It runs unchanged on both, and the portable install already bundles Python 3.13.14,
so there is nothing to install on Windows today. (The PATH `python` is a broken WindowsApps stub —
scripts must invoke `python_embeded\python.exe` explicitly, or a real interpreter once on Ubuntu.)

`set_campaign.ps1` stays as-is for manual one-off prompt edits; it is not part of the pipeline.

**Carry-forward for the Ubuntu move:** the LoRA paths in nodes #4922 and #4968 must switch from
backslashes to forward slashes, since ComfyUI builds that combo list with `os.sep`. This is the
exact reverse of the fix in `c8f70fa` — see the README.

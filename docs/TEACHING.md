# How this system works — a teaching guide

A complete walkthrough of the automated store-ad generator, starting from
first principles. No prior knowledge of AI video generation is assumed; you
should be comfortable reading code and using a terminal.

Read it in order the first time. Each part builds on the previous one.

If you only need to *operate* the system rather than understand it, use
[HOWTORUN.md](docs/HOWTORUN.md) instead.

---

## Table of contents

**Part 1 — Foundations**
1. [What problem does this solve?](#1-what-problem-does-this-solve)
2. [How AI generates images: diffusion](#2-how-ai-generates-images-diffusion)
3. [Latent space and the VAE](#3-latent-space-and-the-vae)
4. [How the model knows what to draw: text conditioning](#4-how-the-model-knows-what-to-draw-text-conditioning)
5. [Samplers, steps, schedulers and sigmas](#5-samplers-steps-schedulers-and-sigmas)
6. [Guidance and CFG](#6-guidance-and-cfg)
7. [LoRA: cheap model modification](#7-lora-cheap-model-modification)
8. [From images to video](#8-from-images-to-video)
9. [Why AI cannot spell](#9-why-ai-cannot-spell)
10. [Hardware: VRAM, offloading, quantisation](#10-hardware-vram-offloading-quantisation)

**Part 2 — The stack**
11. [ComfyUI: graphs as programs](#11-comfyui-graphs-as-programs)
12. [The LTX-2 model](#12-the-ltx-2-model)
13. [Our workflow, node by node](#13-our-workflow-node-by-node)
14. [The two sampler branches](#14-the-two-sampler-branches)

**Part 3 — The pipeline**
15. [Architecture](#15-architecture)
16. [Each script explained](#16-each-script-explained)
17. [The three decisions that shaped the design](#17-the-three-decisions-that-shaped-the-design)

**Part 4 — Practice**
18. [Running it](#18-running-it)
19. [Fifteen real bugs and what they teach](#19-fifteen-real-bugs-and-what-they-teach)
20. [Exercises](#20-exercises)
21. [Glossary](#21-glossary)

---

# Part 1 — Foundations

## 1. What problem does this solve?

A general store runs one promotion per day: *"20% off on vegetables"* today,
*"10% off on toys"* tomorrow. Each promotion needs a 10-second video for the
in-store display: footage of the relevant aisle, the offer written on screen,
and a voiceover announcing it.

Making these by hand — filming, editing, recording narration — costs hours per
ad. This system takes `{item: "vegetables", percent: 20}` and produces a
finished, validated video, served on a URL, with no human involvement.

The interesting engineering is not "call an AI model". It is deciding **which
parts of the job the AI should do at all.** That question drives every design
decision in Part 3.

## 2. How AI generates images: diffusion

Start with images; video is images with a time axis.

Imagine taking a photograph and adding a tiny bit of random static. Repeat 1000
times. You end up with pure noise — the photograph is gone. That is the
**forward diffusion process**, and it is trivial to do.

Now the clever part. Train a neural network on millions of images to answer one
narrow question:

> *Given a slightly noisy image, what did the noise look like?*

That is a learnable task, because noise has statistical structure the image does
not. Once a network can predict the noise, you can **subtract** it — recovering
a slightly cleaner image.

Generating a new image then works backwards:

```
start:  pure random noise
step 1: predict the noise, subtract some of it   -> vaguely structured blur
step 2: predict the noise, subtract some of it   -> shapes appear
...
step 8: predict the noise, subtract some of it   -> a photograph
```

Nothing is retrieved or copied. The network repeatedly asks "if this were a
noisy photo, what would the clean version look like?", and the answer converges
on an image that never existed.

**Key insight:** each step is one full forward pass of a very large neural
network. Our model has 22 billion parameters, so a step is expensive. Step
count is the main quality/time dial — 8 steps is fast and rough, 50 steps is
slow and refined.

## 3. Latent space and the VAE

Running diffusion directly on pixels is ruinously expensive. A 1280×704 frame
is 901,120 pixels × 3 colour channels. Multiply by 241 frames.

So real systems do diffusion in a **compressed** space. Two extra networks make
this possible, together called a **VAE** (Variational Autoencoder):

- an **encoder** that squeezes an image into a small tensor called a **latent**
- a **decoder** that expands a latent back into an image

The latent is not a smaller picture. It is a dense numerical summary — think of
it as the image's *meaning* rather than its pixels. Diffusion happens entirely
in that compressed space, and the decoder only runs once at the very end.

Our model compresses **32× in each spatial dimension** and **8× in time**:

```
1280 × 704 pixels, 241 frames
   ÷32      ÷32        ÷8
  40    ×   22    ×   31   = 27,280 latent positions
```

From 231 million pixel-values down to 27,280 positions. That is why a 10-second
video is generated in minutes rather than weeks.

**Remember the 32× number.** It explains, later, why the model cannot spell.

### Why dimensions must be divisible by 32

If the encoder divides by 32, the input has to divide cleanly by 32. This is why
**true 720p is impossible** here:

```
720 ÷ 32 = 22.5     ✗ not a whole number
704 ÷ 32 = 22       ✓
```

We render 1280×704. It is 1.82:1 rather than exactly 16:9 (1.778:1) — close
enough that fitting it to a 16:9 screen costs 16 pixels of letterboxing.

### Why frame count must be 8n+1

Temporal compression works on groups of 8 frames, plus one anchor frame at the
start:

```
241 = 8 × 30 + 1     ✓    (241 - 1) % 8 == 0
240                  ✗
```

At 24 fps, 241 frames = 10.04 seconds. Valid neighbours: 121 (5s), 361 (15s).

## 4. How the model knows what to draw: text conditioning

So far the model would generate *some* plausible video. To control it, the
prompt must reach the network.

A separate model, a **text encoder**, converts your prompt into numbers. Ours is
**Gemma 3 12B** — a language model used purely as a translator from text into a
list of vectors (an **embedding**). Each vector encodes meaning: "tomatoes" sits
near "vegetables" and far from "wrench".

Those embeddings are injected into the diffusion network at every step through
**cross-attention** — a mechanism letting each position in the image "look at"
the prompt and ask *which words are relevant to me?* The region becoming a crate
attends to "wooden crates"; the region becoming light attends to "bright
lighting".

This is called **conditioning**. Two conditionings are used at once:

- **positive** — what you want
- **negative** — what you do not want

The negative prompt is not a filter or a rule. It is a second, opposite
direction the model is pushed *away* from. That distinction matters: it explains
why `duplicated text` in a negative prompt does not reliably prevent duplicated
text. Nothing checks the output; it only biases the trajectory.

## 5. Samplers, steps, schedulers and sigmas

Diffusion needs a schedule: how much noise to remove at each step. This is
where four often-confused terms live.

**Sigmas** are the noise levels, from most noisy to clean. Our distilled branch
uses nine explicit values:

```
1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0
```

Nine sigmas = eight steps (you move *between* consecutive values). Note the
spacing: the first five barely move, then the last four take big leaps. This is
tuned — early steps establish composition, later steps must travel far to reach
a clean image.

**Scheduler** = the thing that generates a sigma list. `LTXVScheduler` computes
15 sigmas from parameters; `ManualSigmas` just hands over the nine above.

**Sampler** = the algorithm stepping from one sigma to the next. Options differ
in how they use the noise prediction:

- `euler_ancestral_cfg_pp` — Euler's method, cheap, one model call per step
- `res_2s` — a 2-stage method that evaluates the model **twice** per step for a
  more accurate move

**Steps** = how many sigmas minus one. More steps = more model calls = slower
and usually better.

> This is why our two branches differ so much in cost. 8 Euler steps = ~8 model
> evaluations. 15 `res_2s` steps = ~30 evaluations, plus guidance doubling it
> again. That is the difference between 10 minutes and 80.

## 6. Guidance and CFG

**Classifier-Free Guidance** is how prompt adherence is controlled.

At each step, run the model twice: once with the positive conditioning, once
with the negative. You now have two noise predictions. Extrapolate *beyond* the
positive one, away from the negative:

```
guided = negative + scale × (positive − negative)
```

- `scale = 1` → plain positive prediction, no extrapolation
- `scale = 7` → typical; strong prompt adherence
- very high → oversaturated, contorted images

The cost: CFG above 1 means **two model calls per step instead of one**, doubling
the work.

Our two branches take opposite approaches:

- **Distilled branch** — `CFGGuider` with `cfg = 1`. No extrapolation, one call
  per step. It can do this because the *distilled LoRA* baked prompt adherence
  into the weights.
- **Full branch** — `MultimodalGuider` with `cfg = 28`, plus `GuiderParameters`
  giving audio and video **separate** guidance strengths (audio 7, video 3).
  That separation is why this branch is better for a voiceover — you can push
  speech clarity without over-cooking the picture.

## 7. LoRA: cheap model modification

Fine-tuning 22 billion parameters is infeasible for most people. **LoRA**
(Low-Rank Adaptation) is the workaround.

Instead of changing the weights, train a small pair of matrices whose product is
*added* to them. A LoRA file is megabytes rather than tens of gigabytes, and can
be applied at a chosen **strength**:

```
effective weights = original + strength × lora_delta
```

Our LoRA is a **distillation** LoRA: it teaches the model to produce good
results in far fewer steps than it normally needs. Strength is a dial:

| Branch | Strength | Why |
|---|---|---|
| Distilled | 0.5 | Strong distillation, so 8 steps suffice |
| Full | 0.2 | Light touch; quality comes from 15 `res_2s` steps instead |

## 8. From images to video

Video adds constraints an image model does not have. Frame 5 must look like
frame 4 — same store, same crates, coherent camera motion. A model generating
frames independently produces a flickering mess.

Video diffusion models therefore denoise **the whole clip at once**, as a 3D
block (width × height × time), with attention operating across time as well as
space. Every frame is generated aware of every other frame.

This is why you cannot render "just the last second" — the clip is one object.
It is also why the memory cost is so high: all 27,280 latent positions are live
simultaneously.

### Audio in the same latent

LTX-2 is unusual: it generates **audio and video jointly**. Our graph shows this
plainly:

```
EmptyLTXVLatentVideo  ─┐
                        ├─→ LTXVConcatAVLatent → sampler → LTXVSeparateAVLatent ─→ video
LTXVEmptyLatentAudio  ─┘                                                        └─→ audio
```

Video and audio latents are concatenated into one tensor, denoised **together**,
then split apart and decoded by separate VAEs. Because they share the diffusion
process, the voice can be synchronised to the visuals — a genuine advantage over
generating them separately.

## 9. Why AI cannot spell

This is the single most important concept in the project, because it determines
the architecture.

Our first renders asked the model to display `20% OFF TOYS`. It produced:

```
        20%
   20% OF TOYS!
```

The number duplicated, and `OFF` lost a letter. Adding `duplicated text`,
`misspelled words` and `multiple 20s` to the negative prompt did not fix it.
Neither would more steps or higher resolution. Here is why:

**Reason 1 — the VAE destroys the evidence.** Text lives at 32× compression. A
letter occupies roughly one or two latent positions. The difference between
`OFF` and `OF` is a handful of pixels that do not survive encoding. The
information is gone before the diffusion model ever sees it.

**Reason 2 — no character-level conditioning.** Gemma encodes *tokens* (roughly
word fragments), not glyph shapes. Nothing in the architecture connects "the
letters O, F, F, in that order" to specific pixels. The model has learned that
retail ads contain *text-shaped things*, not what letters are.

**Reason 3 — negative prompts do not verify.** As covered in §4, a negative
prompt is a direction to move away from, not a check on the output. There is no
step at which anything reads the generated text and compares it to the intended
string.

**Reason 4 — temporal instability.** Even when a frame renders text correctly,
the next frame is denoised separately. Letters visibly breathe and morph.

### Therefore

Text is **deterministic content**. `20% OFF` has exactly one correct rendering.
A probabilistic system is the wrong tool, and this is not a matter of taste: a
wrong percentage on a shop display is a *pricing error*, potentially a
consumer-law problem, and in an unattended daily pipeline nobody is proofreading
it.

So we do not ask the model to write. We ask it for a **text-free plate** —
scene, camera motion, voiceover, music — and composite the text afterwards with
a real font. Correct spelling by construction.

This is the central lesson: **use generative models for what is genuinely
generative, and deterministic code for what has a single correct answer.**

## 10. Hardware: VRAM, offloading, quantisation

Our GPU is an RTX 5060 Ti with **16 GB** of VRAM. The model needs about 20 GB.
This gap explains all the performance behaviour.

**Quantisation** shrinks weights by storing them at lower precision. ComfyUI is
launched with:

```
--fp8_e4m3fn-text-enc --fp8_e4m3fn-unet
```

`fp8` = 8-bit floating point, half the size of the usual 16-bit. Quality loss is
small; the saving is large. Even so, 20 GB > 16 GB.

**Offloading** handles the remainder: layers that do not fit stay in system RAM
and are streamed to the GPU as needed. From our logs:

```
loaded partially; 8335 MB loaded, 11691 MB offloaded, lowvram patches: 1501
```

Two-thirds of the model lives in RAM. Every step it is shuttled across the PCIe
bus, which is dramatically slower than VRAM. Hence **~62 seconds per step** on a
card that would otherwise manage a few seconds.

The practical consequence: our bottleneck is **memory bandwidth, not compute**.
A GPU with 24 GB would be several times faster despite similar raw throughput.
Knowing which resource is scarce tells you which upgrade matters.

---

# Part 2 — The stack

## 11. ComfyUI: graphs as programs

**ComfyUI** is a node-based front end for diffusion models. Instead of calling a
library, you build a **graph**: nodes are operations, edges carry typed data
(`MODEL`, `LATENT`, `CONDITIONING`, `IMAGE`, `VAE`, `SIGMAS`…).

It is a dataflow program. And critically:

> **Execution is driven backwards from output nodes.**

ComfyUI looks at which output nodes (like `SaveVideo`) are active, walks
backwards to find everything they depend on, and runs only that. Nodes nothing
depends on are never executed.

This single fact explains two things in our project:

1. **Muting one `SaveVideo` removed an entire branch.** We did not disable eight
   nodes — we muted one output, and its exclusive upstream chain became
   unreachable. Render time went from 80 minutes to 10.
2. **The other sampler still sits in the exported file harmlessly.** It is
   orphaned, so it costs nothing.

### Node modes

Every node has a `mode`:

| mode | Name | Meaning |
|---|---|---|
| 0 | Normal | runs |
| 2 | Mute | never runs; downstream gets nothing |
| 4 | Bypass | passes input straight through, skipping its own work |

### Two file formats — the trap

ComfyUI has **two** JSON representations of the same graph, and confusing them
costs an afternoon.

**UI format** (`workflows/store_ad.json`) — what the editor saves. Contains
`nodes` (with canvas positions, sizes, colours, titles), `links`, `groups`,
`extra`. Human-editable, position-aware.

**API format** (`workflow_api.json`) — what the HTTP API accepts. A flat
dictionary keyed by node ID:

```json
{
  "2483": {
    "class_type": "CLIPTextEncode",
    "inputs": { "text": "A vibrant, high-energy...", "clip": ["4960", 0] }
  }
}
```

No positions, no links array — connections are inline as `["source_node", slot]`.
Muted nodes are dropped at export.

**You cannot POST the UI format.** It will be rejected. Export via
`Workflow → Export (API)`. Our client detects the mistake and says so:

```python
if "nodes" in wf:
    raise ComfyError("... is in UI format, not API format. Re-export with Workflow -> Export (API).")
```

### The HTTP API

ComfyUI is a web server on port **8188**. The endpoints we use:

| Endpoint | Purpose |
|---|---|
| `GET /system_stats` | version, VRAM, device |
| `POST /prompt` | queue a workflow; returns `prompt_id` |
| `GET /queue` | what is running and pending |
| `GET /history/{id}` | results once finished (empty until then) |
| `GET /view?filename=…` | download an output |

> **Security:** there is **no authentication**. Anyone who can reach port 8188
> can queue jobs, browse outputs and — through some custom nodes — touch files
> on the host. Never expose it to the internet. §15 covers what we expose
> instead.

## 12. The LTX-2 model

| Component | File | Role |
|---|---|---|
| Diffusion model | `ltx-2.3-22b-dev.safetensors` | 22B parameters, denoises the A/V latent |
| Text encoder | `gemma_3_12B_it.safetensors` | prompt → embeddings |
| Video VAE | in the checkpoint | latent ↔ frames, 32× spatial / 8× temporal |
| Audio VAE | in the checkpoint | latent ↔ waveform |
| Distill LoRA | `ltx-2.3-22b-distilled-lora-384-1.1.safetensors` | good output in few steps |

`.safetensors` is a weight format designed to be loaded without executing code —
unlike Python pickles, which can run arbitrary code on load. Prefer it always.

### Custom node packs

Base ComfyUI does not know about LTX-2. Three community packs add the nodes:

| Pack | Provides | Pinned commit |
|---|---|---|
| ComfyUI-LTXVideo | `MultimodalGuider`, `LTXVPreprocess`, `LTXVTiledVAEDecode`, `LTXAVTextEncoderLoader` | `3b9c5cd` |
| RES4LYF | `ClownSampler_Beta`, `GuiderParameters` | `022c873` |
| ComfyUI-KJNodes | `ResizeImageMaskNode` | `1.0.8` |

Commits are pinned in the README because custom nodes break across versions.
This is a real dependency-management problem: a graph is only reproducible if
the node implementations are too.

## 13. Our workflow, node by node

44 nodes. Here is the whole thing, grouped by job.

### Loading

| Node | Type | Does |
|---|---|---|
| 3940 | `CheckpointLoaderSimple` | loads the 22B model + video VAE |
| 4010 | `LTXVAudioVAELoader` | loads the audio VAE |
| 4960 | `LTXAVTextEncoderLoader` | loads Gemma 3 12B |
| 4922 | `LoraLoaderModelOnly` | distill LoRA @ 0.5 → distilled branch |
| 4968 | `LoraLoaderModelOnly` | distill LoRA @ 0.2 → full branch |

### Prompt

| Node | Type | Does |
|---|---|---|
| 2483 | `CLIPTextEncode` | **positive prompt** → conditioning |
| 2612 | `CLIPTextEncode` | **negative prompt** → conditioning |
| 1241 | `LTXVConditioning` | attaches frame rate (24) to both |

`LTXVConditioning` matters: the model is told the target frame rate, so it can
scale motion appropriately. The same prompt at 24 fps and 8 fps should not move
at the same speed.

### Latent setup

| Node | Type | Does |
|---|---|---|
| 3059 | `EmptyLTXVLatentVideo` | blank video latent, 1280×704, length ← 4979 |
| 3980 | `LTXVEmptyLatentAudio` | blank audio latent, same length, 24 fps |
| 4528 | `LTXVConcatAVLatent` | **joins them into one A/V latent** |
| 4979 | `PrimitiveInt` "number of frames" | **241** — feeds both video and audio |
| 4978 | `PrimitiveFloat` "fps" | **24** — feeds conditioning, audio, output |
| 4985 | `LTXFloatToInt` | fps as an integer where needed |

The primitives exist so one control drives every dependent value. Change 4979
and video length, audio length and duration all move together. This is good
graph design — a single source of truth.

### Optional image conditioning

| Node | Type | Does |
|---|---|---|
| 2004 | `LoadImage` | loads a first-frame image |
| 4981 | `ResizeImageMaskNode` | scales it |
| 3336 | `LTXVPreprocess` | prepares it |
| 3159 | `LTXVImgToVideoConditionOnly` | conditions the video on it |
| 4977 | `PrimitiveBoolean` | **`True` = bypass** → text-to-video |

This is the text-to-video / image-to-video switch. With 4977 set `True`, node
3159 ignores the image and we get pure text-to-video.

> **Subtlety:** the `LoadImage` chain still *executes* even when bypassed,
> because 3159 requires a connected `IMAGE` input to pass validation. Muting
> those nodes breaks the graph. The boolean is the correct switch; the nodes stay
> enabled. This is exactly the kind of thing that looks like dead code and is
> not.

### Sampling and decoding

Two parallel branches — see §14. Each ends:

```
SamplerCustomAdvanced → LTXVSeparateAVLatent → ┬→ LTXVTiledVAEDecode  → frames
                                               └→ LTXVAudioVAEDecode  → audio
                                                        ↓
                                              CreateVideo → SaveVideo
```

`LTXVTiledVAEDecode` decodes in **tiles** rather than all at once. Decoding a
full 1280×704×241 latent would exhaust VRAM, so it processes overlapping
sub-regions and blends them. You can see this in the logs:

```
Processing VAE decode tile at row 0, col 0:  Position: (0:15, 0:23)
Processing VAE decode tile at row 0, col 1:  Position: (0:15, 17:40)
```

Note `17:40` overlapping `0:23` — the overlap is blended to hide seams.

## 14. The two sampler branches

The template ships with two complete generation paths. Understanding them
explains our biggest performance win.

| | Distilled (`output_D`) | Full (`output_F`) |
|---|---|---|
| Sampler | `KSamplerSelect` / `euler_ancestral_cfg_pp` | `ClownSampler_Beta` / `res_2s` |
| Sigmas | `ManualSigmas` — 9 values, **8 steps** | `LTXVScheduler` — **15 steps** |
| Guider | `CFGGuider`, cfg **1** | `MultimodalGuider`, cfg **28**, separate audio/video |
| LoRA | 0.5 | 0.2 |
| Model evaluations | ~8 | ~30–60 |
| **Measured time** | **588 s** | **~4200 s** |

Both were active by default. Both `SaveVideo` nodes ran, so **every queue
produced two videos and took 80 minutes** — for two versions of the same ad.

Muting `SaveVideo` #4823 made the full branch unreachable. 80 minutes → 10.

**When to use which:** distilled for iteration (you cannot refine a prompt on an
80-minute loop), full for a final render where the audio-specific guidance
earns its cost. Swapping is two `mode` values.

---

# Part 3 — The pipeline

## 15. Architecture

```
  store API
  { item: "vegetables", percent: 20 }
        │
        ▼
  ┌───────────────┐   items.json: aliases, grammar, scene text
  │   scene.py    │   fuzzy match, number→words
  └───────┬───────┘
          │  scene description, "SALE", "20% OFF ON VEGETABLES",
          │  "Get twenty percent off on vegetables"
          ▼
  ┌───────────────┐   builds positive + negative prompts
  │ generate_ad.py│   patches workflow_api.json
  └───────┬───────┘   POST /prompt → poll /history → download
          │                                        ~10 min
          ▼
     renders/vegetables_20_2026-08-03.mp4   ← no text on screen
          │  + .json sidecar
          ▼
  ┌───────────────┐   Pillow draws animated RGBA text
  │  overlay.py   │   piped to ffmpeg, composited
  └───────┬───────┘                              ~1.5 s
          ▼
  ┌───────────────┐   duration / dimensions / audio-not-silent
  │   make_ad.py  │   refuses to publish on failure
  └───────┬───────┘   writes manifest.json, latest.mp4
          ▼
     out/  ──►  serve.py (127.0.0.1:8787)  ──►  cloudflared  ──►  display
```

## 16. Each script explained

### `comfy_client.py` — talking to ComfyUI

Standard library only, so nothing is installed into ComfyUI's Python.

Three things worth studying:

**Polling that detects silent failure.** Naively you poll `/history/{id}` until
it is non-empty. But if the job is cancelled or crashes, history stays empty
forever and you hang. So we also watch `/queue`: once we have seen the job in
the queue and it later disappears *without* appearing in history, something went
wrong and we say so.

**Surfacing validation errors verbatim.** A bad workflow returns HTTP 400 with a
JSON report naming the offending node and input. Swallowing that would be
throwing away the answer.

**Not assuming output keys.** `SaveVideo` reports its `.mp4` under `images`, not
`videos`. Rather than guess, we collect from every key:

```python
for key, items in out.items():
    for it in items:
        if isinstance(it, dict) and "filename" in it:
            found.append(...)
```

### `scene.py` — item → scene

The store sends arbitrary product strings. `items.json` holds 19 departments,
each with aliases, a display form, a spoken form, and a hand-tuned scene
description. Resolution is layered:

1. exact key (`vegetables`)
2. alias (`milk` → `dairy`, `chappals` → `footwear`)
3. token overlap (`Fresh Vegetables 1kg` → `vegetables`)
4. fuzzy match via `difflib` (`vegitables`, `stationary`)
5. Claude API for unknown items, cached back into `items.json`
6. `general` fallback — **never hard-fails**

Two details that look small and are not:

**`vo_noun` is stored, not derived.** Pluralisation is not mechanical. `dairy`
speaks as "dairy products"; `rice` must not become "rices". A rules engine would
be wrong more often than a table.

**`number_to_words`.** The voiceover must say "thirty five percent", not "35".

`--label` chooses whether the ad advertises the department (`OFF ON DAIRY`) or
the item (`OFF ON MILK`). Scene comes from the department either way — you
always want the dairy aisle.

### `generate_ad.py` — prompt and render

Assembles the prompt in a fixed order, which matters:

```
opening scene  →  no-text clause  →  style  →  audio (LAST, quoted)
```

Audio goes last and the spoken line is **quoted**, because the model otherwise
keeps reading past the script (see §19, bug 7).

Seeds derive from `sha256(item + percent)`. So re-running a campaign reproduces
its video, while different campaigns differ. Python's built-in `hash()` would
not work — it is salted per process and changes between runs.

Inputs are validated *before* submitting: `%32` on dimensions, `8n+1` on frames.
Failing in a millisecond beats failing ten minutes into a sampler.

### `overlay.py` — the compositor

Draws text with Pillow and pipes raw RGBA frames to ffmpeg's `overlay` filter.

**Sprites are rendered once and scaled**, not re-rasterised per frame. Redrawing
the font at each size would make the gold stroke visibly thicken and thin during
the scale animation.

**Sprites are cropped to their alpha bounding box.** My first version left the
drop-shadow padding in, and that transparent margin counted toward line
spacing — the two lines sat much too far apart.

**Easing functions** give the motion character:

```python
def ease_out_back(t, overshoot=1.9):   # scales past 1.0 then settles — a "punch"
    t -= 1.0
    return 1.0 + t * t * ((overshoot + 1.0) * t + overshoot)
```

No fade-out at the end, so the clip loops cleanly on a display. Audio is
stream-copied (`-c:a copy`), never re-encoded.

`--preview 2.0` flattens one frame over the real video and writes a PNG in about
a second — so layout is judged without a full encode.

### `make_ad.py` — orchestration and the gate

Runs everything, then **validates before publishing**:

- duration within 0.3 s of 241/24
- even pixel dimensions
- audio track exists, mean volume above −50 dB

The audio check is the interesting one. A silent track measures about −91 dB and
real audio around −16 dB, so the threshold is unambiguous. It is checked with
ffmpeg's `volumedetect`, parsed from the log.

On failure: nothing is published, the reason is printed, the file is kept, exit
code is non-zero. **This is the whole point of an unattended pipeline** — there
is no human to notice a silent ad, so the pipeline must notice for itself. It
was tested against a deliberately broken clip, not assumed to work.

On success: writes the dated file, copies it to `latest.mp4`, updates
`manifest.json` with a `sha256`.

### `serve.py` — delivery

A deliberately narrow HTTP server.

| Route | Cache | Why |
|---|---|---|
| `/manifest.json` | `no-store` | must always be fresh |
| `/latest.mp4` | `no-store` | **stable filename** — a cached copy would pin the display to the first ad it ever saw |
| `/ads/<name>.mp4` | `immutable, 1 year` | dated names never change |

That `latest.mp4` cache header is a correctness issue, not an optimisation. Get
it wrong and the display keeps replaying an old ad — a failure that looks
identical to the pipeline being broken.

**HTTP Range support is hand-written**, because `SimpleHTTPRequestHandler` does
not implement it and video players need it to seek and to begin playback before
the file is buffered. A range request gets `206 Partial Content` with a
`Content-Range` header; an unsatisfiable one gets `416`.

**Scope is enforced twice.** Filenames must match `^[A-Za-z0-9._-]+$` (which
excludes `/` and `..`), and the resolved path is then re-checked with
`relative_to(OUT)`. Only `.mp4` and `manifest.json` are served. Tested with
traversal attempts and a `.txt` planted inside `out/` — all 404.

### Why ComfyUI is not exposed

An earlier plan proposed tunnelling ComfyUI itself so the store could reach it.
That would have published an unauthenticated service that can queue GPU jobs,
list and download `output/`, and reach host files.

The store does not need ComfyUI. It needs to submit an offer and fetch a video.
So the tunnel points at the **read-only `out/` server**, and ComfyUI stays on
`127.0.0.1`. If remote triggering is needed later, the correct shape is a small
authenticated service of our own that validates input and talks to ComfyUI over
localhost.

**General principle:** expose the narrowest possible surface, and never let a
convenient tunnel decide your security boundary.

## 17. The three decisions that shaped the design

**1. Text is composited, not generated.** Because the model cannot spell (§9),
and because a percentage has exactly one correct value. Reversible with a flag —
but the default is correctness.

**2. Only the distilled branch runs.** Because measurement said the other cost
70 extra minutes per ad for a second copy nobody asked for. *We measured before
deciding* — the initial guess about which branch was slow was wrong, and the
user's own log corrected it.

**3. No caching layer.** An earlier design pre-rendered one reusable "plate" per
department. Correct for many ads per day; pointless at one ad per day, where it
would only add cache-invalidation bugs. **The requirement killed the
optimisation.** Ask about volume before designing for scale.

---

# Part 4 — Practice

## 18. Running it

### Setup, once per shell

```powershell
cd C:\Users\User\Desktop\video_gen
. .\activate.ps1          # the leading dot matters
```

Gives `comfy-py`, `ffmpeg`, `ffprobe`, `$ComfyRoot`, `$ComfyOutput`. Or use
`.\py <script>` with no setup at all.

> **Why not `python`?** The `python` on PATH is a broken Windows Store stub.
> The real interpreter is inside the ComfyUI portable install. And
> `python_embeded\python.exe` alone fails twice over — that path is relative to
> ComfyUI, not the project, and PowerShell reads a bare relative path as a
> *module* name, producing `The module 'python_embeded' could not be loaded`.

### Everyday commands

```powershell
.\py comfy_client.py --stats                 # is the server up? how much VRAM?
.\py comfy_client.py --show                  # workflow settings, SaveVideo count
.\py scene.py --selftest                     # resolver across sample inputs

.\py make_ad.py --item vegetables --percent 20            # full run, ~10 min
.\py make_ad.py --item milk --percent 30 --label item     # "OFF ON MILK"
.\py make_ad.py --item toys --percent 40 --dry-run        # prompt only, no GPU
.\py make_ad.py --item toys --percent 40 --skip-render    # re-composite, ~2 s

.\py overlay.py --meta renders\vegetables_20_2026-08-03.json --preview 2.0
.\py serve.py                                             # 127.0.0.1:8787
```

Public URL for testing:

```powershell
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --url http://127.0.0.1:8787
```

Both the server and the tunnel run in the foreground and stop when closed. The
`trycloudflare.com` hostname changes on every restart, and Cloudflare gives
account-less tunnels **no uptime guarantee** — production needs a named tunnel.

### Iteration speed — the thing to internalise

| Action | Cost |
|---|---|
| `--dry-run` | instant |
| `--preview` a frame | ~1 s |
| Re-composite (`--skip-render`) | ~2 s |
| Full render | ~10 min |
| Render with both branches | ~80 min |

Work at the cheapest tier that answers your question. Testing an animation
tweak with a full render wastes ten minutes to learn something a two-second
command would tell you.

## 19. Fifteen real bugs and what they teach

Every one of these actually happened while building this.

**1. Video was 5 s, prompt said 10 s.** Frame count was 121 (5.04 s at 24 fps).
→ *Check that config matches intent; the file said one thing, the prompt another.*

**2. Every run produced two videos and took 80 minutes.** Both `SaveVideo` nodes
were active. → *Understand your framework's execution model. Backwards-from-outputs
made one mute worth 70 minutes.*

**3. "720p" was actually 2.35:1 ultra-wide.** Width was raised to 1280, height
left at 544. And 720 is not divisible by 32 anyway. → *Changing one dimension
changes aspect ratio. Know your model's constraints.*

**4. Negative prompt began with the literal words "Negative Prompt:".** Those
tokens were encoded as content. → *A prompt field is not a form with labels.*

**5. I "fixed" LoRA backslashes and broke both nodes.** ComfyUI builds its file
list with `os.sep`; on Windows the value *must* be `ltxv\ltx2\...`. → *Do not
normalise what you have not verified. The original was right; my portability
instinct was wrong.*

**6. On-screen text came out `20%` / `20% OF TOYS!`.** → *The lesson of §9;
it reshaped the architecture.*

**7. The voiceover said "acampass".** The prompt read
`...off on vegetables! Accompanied by cheerful music.` — unquoted, so the model
kept reading the stage direction aloud. Fixed by quoting the line and putting it
last. → *The model cannot see your intended structure. Delimit explicitly.*

**8. Guessed which sampler branch was slow — and was wrong.** I extrapolated
"58 min at 80%" as the distilled branch; the log later showed distilled takes 9
minutes and the *other* branch was the expensive one. → *Measure. My estimate
was off by 8×.*

**9. `import comfy_client` failed.** ComfyUI's embedded Python pins `sys.path`
via `python313._pth` and omits the script's own directory. → *Embedded
interpreters are not normal interpreters.*

**10. `SaveVideo` outputs appear under `images`.** → *Do not assume API response
shapes; iterate over what is there.*

**11. Line spacing was far too wide.** Sprite drop-shadow padding counted toward
the gap. → *Crop to content before measuring.*

**12. `git commit -m` with double quotes in the message failed repeatedly.**
PowerShell's native-argument quoting mangled it. Fixed with `git commit -F file`.
→ *Shell quoting is a real hazard; use a file for anything non-trivial.*

**13. Manifest recorded `"seed": null`.** The `--skip-render` path never derived
the seed. → *Found only by reading the output. Inspect your artefacts.*

**14. Pillow lacks Raqm.** English is fine, but Hindi/Tamil would render
unjoined and mis-ordered. → *Know your limits before someone asks for
regional-language ads.*

**15. `SimpleHTTPRequestHandler` has no Range support.** Video players need it.
→ *Read what the stdlib actually does, not what you assume.*

## 20. Exercises

Roughly increasing difficulty.

1. **Trace the graph.** Open `workflows/store_ad.json` in ComfyUI. Starting at
   the active `SaveVideo`, follow the edges backwards and list every node that
   must execute. Which nodes are unreachable?
2. **Predict a duration.** What clip length does 481 frames at 24 fps give? Is
   481 legal? What about 480?
3. **Add a department.** Add `flowers` to `items.json` with aliases, display,
   `vo_noun` and a scene description. Verify with
   `.\py scene.py --item flowers --percent 15`. Remember: describe visuals only,
   never signage.
4. **Break the validator on purpose.** Make a clip that fails each of the three
   checks and confirm each fires.
5. **Change the animation.** Make `SALE` slide in from the left instead of
   scaling. Use `--preview` to iterate — you should not need a re-render.
6. **Compare the branches.** Run once with `--in-model-text` and once without.
   Compare text quality, and time both.
7. **Explain a cache header.** Why is `latest.mp4` `no-store` while
   `/ads/<name>.mp4` is immutable for a year? What breaks if you swap them?
8. **Estimate a cost.** Using the token maths in §3, how many latent positions
   does 1536×864 at 241 frames need? Roughly how much slower than 1280×704, and
   why is the answer not simply the ratio?
9. **Design the API adapter.** The store will send some JSON. Write the function
   mapping it to `--item` / `--percent`, and decide what happens on a malformed
   payload, an unknown item, and a percentage of 0 or 150.
10. **Threat-model the tunnel.** List everything an attacker could do with the
    public URL. Now list what changes if the tunnel pointed at port 8188 instead.

## 21. Glossary

| Term | Meaning |
|---|---|
| **Diffusion** | Generating data by repeatedly removing predicted noise from random noise |
| **Latent** | Compressed representation where diffusion happens; not a small image |
| **VAE** | Encoder/decoder pair between pixels and latents. Ours: 32× spatial, 8× temporal |
| **Denoising step** | One forward pass predicting and partly removing noise |
| **Sigma** | Noise level. A sigma list defines the schedule; N sigmas = N−1 steps |
| **Scheduler** | Produces the sigma list |
| **Sampler** | Algorithm that steps between sigmas (`euler`, `res_2s`, …) |
| **Conditioning** | Encoded prompt injected into the model via cross-attention |
| **Text encoder** | Model turning text into embeddings. Ours: Gemma 3 12B |
| **CFG** | Classifier-Free Guidance; extrapolate away from the negative prompt. Costs 2 model calls per step when > 1 |
| **Guider** | Node combining model, conditionings and CFG into what the sampler calls |
| **LoRA** | Small trainable delta added to frozen weights, applied at a strength |
| **Distillation** | Training a model to get good results in fewer steps |
| **fp8** | 8-bit float weights; half the memory of fp16, minor quality cost |
| **Offloading** | Keeping layers in system RAM and streaming them to the GPU. Slow but makes big models fit |
| **Tiled decode** | Decoding a latent in overlapping tiles to bound VRAM use |
| **Node mode** | 0 normal, 2 mute, 4 bypass |
| **UI vs API format** | Editor format vs the format `POST /prompt` accepts. Not interchangeable |
| **8n+1** | Legal frame counts, from 8× temporal compression plus an anchor frame |
| **Plate** | A render without text, ready for compositing |
| **Range request** | HTTP partial fetch (`206`) that lets players seek |
| **Manifest** | JSON describing the current ad, so clients detect changes cheaply |

---

## The one thing to take away

The hardest problem here was not making the AI work. It was **recognising which
half of the job the AI should not touch.**

The model is genuinely good at what is generative: inventing a produce aisle,
moving a camera through it, speaking a line of dialogue with intonation. It is
structurally incapable of writing `20% OFF` reliably, and no amount of prompting,
step count or resolution changes that.

Good engineering around a generative model means drawing that line accurately —
and then building deterministic, testable, verifiable machinery on the correct
side of it.

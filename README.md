# video_gen — LTX-2 retail ad workflow

ComfyUI workflow for **general-store** daily offer ads — 10 seconds, with generated voiceover
and music. Joint audio + video generation on LTX-2.3-22B.

One offer per store per day, and the product changes daily: vegetables today, toys tomorrow.
The graph currently holds a toys campaign, but that is just the offer loaded right now — the
department, the on-screen text and the voiceover all get regenerated per campaign. See
[PLAN.md](PLAN.md) for the pipeline that drives this from the store API.

> **[HOWTORUN.md](HOWTORUN.md)** — just want to make an ad? Start there. Step-by-step commands,
> expected output, and a troubleshooting table.
>
> **[TEACHING.md](TEACHING.md)** — how it all works, from first principles: diffusion, latents,
> why the model cannot spell, every node in the graph, and why the pipeline is shaped this way.
>
> This README is the reference in between.

- **[1.LTX_2.3_Workflow.json](1.LTX_2.3_Workflow.json)** — the working workflow. This is the one to load.
- **[LTX_Video_Workflow.json](LTX_Video_Workflow.json)** — pristine upstream template, kept for reference. Do not edit.
- **[prompt.txt](prompt.txt)** — positive + negative prompt text, mirrored from the graph for reference.
- **[set_campaign.ps1](set_campaign.ps1)** — retarget the offer without hand-editing JSON.

## Running the scripts

The pipeline runs on ComfyUI's embedded Python. There is no system Python — the `python` on
PATH is a broken WindowsApps stub. Two ways to invoke it, both from this directory:

**`.\py` — for one-off commands.** No setup, works in PowerShell and cmd:

```powershell
.\py scene.py --selftest                          # resolver across sample inputs
.\py scene.py --item milk --percent 30 --label item
.\py comfy_client.py --stats                      # live server, VRAM, queue
.\py comfy_client.py --show                       # workflow settings + SaveVideo count
```

**`. .\activate.ps1` — for a working session.** Adds `ffmpeg` and `ffprobe`, which winget
installed outside the PATH of already-open shells, plus `$ComfyRoot` / `$ComfyOutput`. Must be
**dot-sourced**, or the aliases vanish with the child scope:

```powershell
. .\activate.ps1
comfy-py scene.py --selftest
ffprobe -i "$ComfyOutput\output_D_00006_.mp4"
```

Both honour `COMFY_PYTHON` if the portable install lives somewhere else.

> Why not `python_embeded\python.exe` directly? That path resolves against the current
> directory, not the ComfyUI install, and PowerShell reads a bare relative path as a module
> name rather than a program — hence `The module 'python_embeded' could not be loaded`.

## Daily ad pipeline

One offer in, one finished ad out:

```powershell
.\py make_ad.py --item vegetables --percent 20
```

That resolves the item to a store section, renders a text-free video with a matching
voiceover (~10 min), composites the on-screen text, validates the result, and publishes to
`out/`. Add `--label item` to advertise the product rather than the department
(`20% OFF ON MILK` instead of `20% OFF ON DAIRY`).

| Script | Stage |
|---|---|
| [scene.py](scene.py) | item string → scene description, on-screen text, voiceover line |
| [generate_ad.py](generate_ad.py) | builds the prompt, renders via the ComfyUI API |
| [overlay.py](overlay.py) | composites the animated text with Pillow + ffmpeg |
| [make_ad.py](make_ad.py) | all of the above, plus validation and publishing |
| [serve.py](serve.py) | serves `out/` over HTTP for the display |
| [comfy_client.py](comfy_client.py) | ComfyUI API client |

Useful flags: `--dry-run` prints the prompt without touching the GPU, `--skip-render`
re-composites the newest existing render (a two-second loop instead of ten minutes), and
`--in-model-text` has the model draw the text instead of compositing it.

**Validation blocks publishing.** Duration, dimensions, and a non-silent audio track are
checked; on failure nothing is published, the reason is printed and the file is kept for
inspection. An unattended daily job has no human to catch a broken ad.

### Serving it

```powershell
.\py serve.py                                   # 127.0.0.1:8787, nothing exposed
```

| Route | Purpose |
|---|---|
| `/` | status page with an inline player |
| `/manifest.json` | current ad metadata incl. `sha256`, so a client can detect a new ad without downloading the video |
| `/latest.mp4` | current ad under a stable filename — the display's URL never changes |
| `/ads/<name>.mp4` | dated file, immutable, cached for a year |

The document root is `out/` and only `.mp4` plus `manifest.json` are served. HTTP Range is
implemented, which `SimpleHTTPRequestHandler` does not do and video players need in order to
seek.

### Cloudflare tunnel

```powershell
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --url http://127.0.0.1:8787
```

Prints a `https://<random>.trycloudflare.com` URL. Fine for testing; the hostname changes on
every restart, so production needs a named tunnel (Cloudflare account + domain).

> **Never tunnel ComfyUI itself (port 8188).** It has no authentication, so anyone with the
> URL could queue jobs, browse `output/`, and reach files on the host. Only the read-only
> `out/` server is exposed. If remote *triggering* is needed later, put a small authenticated
> shim in front and let it talk to ComfyUI over localhost.

## Retargeting the offer

The on-screen graphic is specified as two lines — `SALE` on top, `<N>% OFF ON <PRODUCT>` below. To change the campaign:

```powershell
.\set_campaign.ps1 -Percent 10 -Product VEGETABLES -Store "grocery store"
```

It rewrites both text nodes (positive and negative, including the `multiple <N>s` suppression term), spells the percentage out for the voiceover, and validates the JSON before finishing. Reload the workflow in ComfyUI afterwards.

## Requirements

### Custom node packs

| Pack | Nodes used | Pinned commit |
|---|---|---|
| [ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo) | `MultimodalGuider`, `LTXVPreprocess`, `LTXVImgToVideoConditionOnly`, `LTXFloatToInt`, `LTXVTiledVAEDecode`, `LTXAVTextEncoderLoader`, `GemmaAPITextEncode` | `3b9c5cde4700917074823d45e25401d81049f8fc` |
| [RES4LYF](https://github.com/ClownsharkBatwing/RES4LYF) | `ClownSampler_Beta`, `GuiderParameters` | `022c873c6d198c67b3312a1804336533a050df18` |
| ComfyUI-KJNodes | `ResizeImageMaskNode` | `1.0.8` |

Core nodes reference comfy-core up to `0.14.1` / frontend `1.47.10` — use a recent ComfyUI build.

### Models

| File | Location |
|---|---|
| `ltx-2.3-22b-dev.safetensors` | `models/checkpoints/` |
| `gemma_3_12B_it.safetensors` | `models/text_encoders/` |
| `ltxv/ltx2/ltx-2.3-22b-distilled-lora-384-1.1.safetensors` | `models/loras/ltxv/ltx2/` |

**LoRA path separators are host-specific — do not "normalize" them.** The `lora_name` widget is a combo that must match ComfyUI's file list *exactly*. That list is built with `os.sep`, so on Windows the value must use backslashes (`ltxv\ltx2\...`, escaped as `\\` in the JSON) and on Linux forward slashes. A mismatch shows up as a red-bordered `Load LoRA` node with an unresolved value. This repo targets **Windows**, so backslashes are correct here and should be left alone.

## How the graph is wired

```
CheckpointLoaderSimple (ltx-2.3-22b-dev)
  ├─ MODEL → LoraLoader #4968 @0.2 → MultimodalGuider → SamplerCustomAdvanced #4802 → output_F   [MUTED]
  ├─ MODEL → LoraLoader #4922 @0.5 → CFGGuider(cfg 1) → SamplerCustomAdvanced #4829 → output_D   [ACTIVE]
  └─ VAE  → image conditioning + 2x LTXVTiledVAEDecode

LTXAVTextEncoderLoader (gemma_3_12B_it) → CLIPTextEncode x2 → LTXVConditioning (24 fps)
EmptyLTXVLatentVideo (1280x704, length←241) ─┐
LTXVEmptyLatentAudio (length←241, rate←24) ─┴→ LTXVConcatAVLatent → sampler
   → LTXVSeparateAVLatent → TiledVAEDecode + AudioVAEDecode → CreateVideo → SaveVideo
```

### Key controls

| Node | Title / role | Value |
|---|---|---|
| `PrimitiveInt` #4979 | number of frames | **241** (= 10.04 s; must be 8n+1) |
| `PrimitiveFloat` #4978 | fps — feeds conditioning, audio rate, and CreateVideo | **24** |
| `EmptyLTXVLatentVideo` #3059 | output resolution | **1280 x 704** (both dims must be divisible by 32) |
| `PrimitiveBoolean` #4977 | **t2v / i2v switch** → `bypass` input of #3159 | `True` = text-to-video |
| `LTXVImgToVideoConditionOnly` #3159 | first-frame conditioning strength | 0.7 (inactive while bypassed) |

**Duration:** change frames in #4979 only — it drives both the video and audio latents. Frame count must satisfy `(frames - 1) % 8 == 0`. At 24 fps: 121 = 5 s, 241 = 10 s, 361 = 15 s.

**Text-to-video vs image-to-video:** flip `PrimitiveBoolean` #4977. `True` bypasses the image conditioning inside #3159 (current setting). Set `False` and load a first frame in `LoadImage` #2004 for i2v. Leave the `LoadImage` / `Resize` / `LTXVPreprocess` chain enabled either way — #3159 requires a connected `IMAGE` input even when bypassed, so muting those nodes fails validation.

**Resolution:** both dimensions must be divisible by 32, so true 720p (1280x720) is **not** a legal latent size — 720/32 = 22.5. Use `1280x704` (1.82:1, current) or `1536x864` / `1024x576` for exact 16:9. Watch the aspect ratio when changing one dimension: `1280x544` is 2.35:1 ultra-wide, not 16:9, and will letterbox or crop on a 16:9 display.

**Two sampler branches — iterate on Distilled, ship on Full.**

| | `output_D` — "Generate Distilled" | `output_F` — "Generate Full" |
|---|---|---|
| Sampler | 8-step `ManualSigmas`, `euler_ancestral_cfg_pp` | 15-step `LTXVScheduler` + `ClownSampler_Beta` on `res_2s` |
| Guider | `CFGGuider` cfg 1 (1 pass/step) | `MultimodalGuider` cfg 28, separate AUDIO/VIDEO guidance |
| Cost | baseline | roughly 5-7x — `res_2s` is a 2-stage solver and cfg 28 needs cond+uncond |
| State | **ACTIVE** (`SaveVideo` #4852) | **MUTED** (`SaveVideo` #4823, `mode: 2`) |

Muting a `SaveVideo` prunes its entire exclusive upstream chain, so only the active branch costs anything. Distilled is the default because at 1280x704 / 241 frames the Full branch runs for hours — too slow to iterate on prompt wording or text legibility. Swap the two `mode` values for the final render, where the Full branch's audio-specific guidance is worth the wait. **Never leave both active** — the template ships that way and silently doubles every queue.

## Known limitations

- **Resolution is 1280x704**, not the "4K UHD" the prompt asks for — that phrase is a style token only. Render at this size and upscale in post; pushing the latent higher on a 22B model costs VRAM and time superlinearly.
- **On-screen text is unreliable.** The prompt requests exact glyphs (`20% OFF TOYS`) and negative prompts do not reliably suppress duplicated or misspelled text. Compositing the price graphic over a clean generated plate is far more dependable than fighting the sampler.
- Seeds are fixed (42 / 43) — set them to `randomize` when exploring variations.

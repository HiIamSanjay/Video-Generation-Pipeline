# video_gen — LTX-2 retail ad workflow

ComfyUI workflow for a 10-second toy-store promo with generated voiceover and music.
Joint audio + video generation on LTX-2.3-22B.

- **[1.LTX_2.3_Workflow.json](1.LTX_2.3_Workflow.json)** — the working workflow. This is the one to load.
- **[LTX_Video_Workflow.json](LTX_Video_Workflow.json)** — pristine upstream template, kept for reference. Do not edit.
- **[prompt.txt](prompt.txt)** — positive + negative prompt text, mirrored from the graph for reference.
- **[set_campaign.ps1](set_campaign.ps1)** — retarget the offer without hand-editing JSON.

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

**LoRA path separators are host-specific — do not "normalize" them.** The `lora_name` widget is a combo that must match ComfyUI's file list *exactly*. That list is built with `os.sep`, so on Windows the value must use backslashes (`ltxv\ltx2\...`, escaped as `\\` in the JSON) and on Linux forward slashes. A mismatch shows up as a red-bordered `Load LoRA` node with an unresolved value. This repo is set up for **Windows**; if you move it to Linux, swap the separators in nodes #4922 and #4968.

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

# How to run it

Operational guide. Copy-pasteable, in order. For *why* any of it works, see
[TEACHING.md](docs/TEACHING.md); for reference detail, see [README.md](README.md).

---

## TL;DR

Three things, in three windows. These commands work in **both** PowerShell and
cmd.

Window 1 — start ComfyUI (or just double-click the `.bat`):

```
C:\Users\User\Downloads\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\run_nvidia_gpu.bat
```

Window 2 — make today's ad, about 10 minutes:

```
cd C:\Users\User\Desktop\video_gen
.\py make_ad.py --item vegetables --percent 20
```

Window 3 — serve it:

```
cd C:\Users\User\Desktop\video_gen
.\py serve.py
```

Then open <http://127.0.0.1:8787/> to watch it.

---

## Before you start

Everything below is already installed on this machine. Use this to verify after
a reinstall or on a new box.

| Requirement | Check | If missing |
|---|---|---|
| ComfyUI portable | folder exists in `Downloads\ComfyUI_windows_portable_nvidia` | reinstall ComfyUI portable |
| Models | see [README.md](README.md) model table | download to `ComfyUI\models\...` |
| Custom nodes | LTXVideo, RES4LYF, KJNodes | ComfyUI Manager, pins in README |
| ffmpeg | `.\py -c "import shutil"` then `ffmpeg -version` after activate | `winget install Gyan.FFmpeg` |
| cloudflared | `& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' --version` | `winget install Cloudflare.cloudflared` |
| `workflow_api.json` | file exists in `video_gen\` | ComfyUI → `Workflow → Export (API)` from `workflows/store_ad.json` |

You do **not** need to install Python. The pipeline uses the interpreter bundled
with ComfyUI.

---

## Step 1 — Start ComfyUI

Double-click:

```
C:\Users\User\Downloads\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable\run_nvidia_gpu.bat
```

Leave that window open — it is the server. Wait for:

```
Starting server
To see the GUI go to: http://127.0.0.1:8188
```

> ⚠️ **Use `run_nvidia_gpu.bat`, not `run_nvidia_gpu_fast_fp16_accumulation.bat`.**
> Only the first one passes `--fp8_e4m3fn-text-enc --fp8_e4m3fn-unet`, which
> halves the model's memory footprint. Without those flags a 22B model on a 16 GB
> card will crawl or run out of memory.

## Step 2 — Open a shell in the project

```
cd C:\Users\User\Desktop\video_gen
```

### Which shell are you in?

Look at your prompt:

| Prompt looks like | Shell |
|---|---|
| `PS C:\Users\User\Desktop\video_gen>` | **PowerShell** |
| `C:\Users\User\Desktop\video_gen>` | **cmd.exe** (Command Prompt) |

**`.\py` works in both.** Use it and you never have to think about this again:

```
.\py scene.py --selftest
```

**`activate.ps1` is PowerShell-only.** It is optional — it just saves typing and
adds `ffmpeg`/`ffprobe` to the session:

```powershell
. .\activate.ps1          # PowerShell ONLY. The leading dot is required.
comfy-py scene.py --selftest
```

In cmd that gives `'.' is not recognized as an internal or external command`.
Either switch to PowerShell (type `powershell` and press Enter) or just use
`.\py`.

The leading dot is not decoration — it dot-sources the script into your current
session. Without it the aliases are created in a child scope and vanish the
instant it exits.

> **Pasting note:** examples below are written for PowerShell, and several have
> trailing `# comments`. cmd does not understand `#` and will either error or
> pass it to the script as an argument. Paste the command only, not the comment.

> Never run `python_embeded\python.exe ...` — that path is relative to the
> ComfyUI install, not this folder, and PowerShell reads a bare relative path as
> a module name. See troubleshooting below.

## Step 3 — Check everything is healthy

```powershell
.\py comfy_client.py --stats
```

Expected:

```
ComfyUI   0.29.0  (local-portable)
python    3.13.14
torch     2.13.0+cu130
device    NVIDIA GeForce RTX 5060 Ti
vram      12.7 GiB free of 15.9 GiB
queue     0 running, 0 pending
```

Then confirm the workflow settings:

```powershell
.\py comfy_client.py --show
```

```
workflow_api.json: 40 nodes
1280x704  241 frames @ 24 fps = 10.04s  seed=43  prefix=output_D
SaveVideo nodes: ['4852']  (more than one means a double render)
```

> **Check the last line every time.** If it lists two `SaveVideo` nodes, every
> render will produce two videos and take ~80 minutes instead of ~10.

## Step 4 — Make the ad

```powershell
.\py make_ad.py --item vegetables --percent 20
```

Takes about **10 minutes**. Output:

```
vegetables @ 20%  ->  vegetables  (curated)
  1280x704  241 frames @ 24 fps = 10.04s  seed=1952917932  prefix=ad_vegetables_20
  on-screen: SALE / 20% OFF ON VEGETABLES
  prompt_id 42fb1ef5-55e5-4171-bbff-0b0c8a02aebf
running  557s elapsed
done in 559s
  -> renders\vegetables_20_2026-08-03.mp4
  composited SALE / 20% OFF ON VEGETABLES
  checks: 1280x704  10.04s  241 frames  audio mean -16.4 dB

  out\vegetables_20_2026-08-03.mp4
  latest.mp4  (stable pointer for the display)
  manifest.json updated
```

### Useful variations

```powershell
# Advertise the product instead of the department: "20% OFF ON MILK" not "ON DAIRY"
.\py make_ad.py --item milk --percent 30 --label item

# See the prompt without using the GPU at all — instant
.\py make_ad.py --item toys --percent 40 --dry-run

# Re-do only the text overlay on the newest render — about 2 seconds
.\py make_ad.py --item vegetables --percent 20 --skip-render

# Let the model draw the text instead of compositing (expect spelling errors)
.\py make_ad.py --item toys --percent 40 --in-model-text

# Prove the plumbing works in ~75s at tiny resolution
.\py make_ad.py --item milk --percent 30 --smoke
```

### Tuning the overlay without re-rendering

Check a single frame in about a second:

```powershell
.\py overlay.py --meta renders\vegetables_20_2026-08-03.json --preview 2.0
```

Writes `_preview_2s.png`. Adjust and repeat:

```powershell
.\py overlay.py --meta renders\vegetables_20_2026-08-03.json --preview 2.0 --pop-at 0.6 --scrim 150
```

| Flag | Does |
|---|---|
| `--pop-at 0.6` | seconds before `SALE` lands |
| `--scrim 150` | 0–255 darkness behind the text; `0` turns it off |
| `--font impact.ttf` | any font in `C:\Windows\Fonts` |
| `--size1` / `--size2` | starting sizes before auto-shrink |

When it looks right, apply it for real with `--skip-render`.

## Step 5 — Serve it

```powershell
.\py serve.py
```

```
serving C:\Users\User\Desktop\video_gen\out
  http://127.0.0.1:8787/
  http://127.0.0.1:8787/manifest.json
  http://127.0.0.1:8787/latest.mp4
ctrl-c to stop
```

Leave it running. Open <http://127.0.0.1:8787/> for a status page with the video
embedded.

| Route | For |
|---|---|
| `/` | status page, human check |
| `/latest.mp4` | **point the display here** — filename never changes |
| `/manifest.json` | metadata + `sha256`, so a client detects a new ad without downloading 6 MB |
| `/ads/<name>.mp4` | a specific dated ad |

## Step 6 — Public URL (optional)

```powershell
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --url http://127.0.0.1:8787
```

Prints, after ~30 seconds:

```
https://<random-words>.trycloudflare.com
```

Leave that window open too. Things to know:

- The hostname **changes every restart**. Nothing can be permanently pointed at it.
- Cloudflare gives account-less tunnels **no uptime guarantee**. Testing only.
- Production needs a **named tunnel** (Cloudflare account + your own domain).
- Anyone with the URL can watch the ads. That is fine — they are shop adverts.

> 🔒 **Never tunnel port 8188.** ComfyUI has no password. Exposing it lets
> anyone queue jobs on your GPU, download everything in `output\`, and reach
> files on this machine. Only ever tunnel `8787`, which serves `out\` read-only.

---

## The daily routine

Once ComfyUI and `serve.py` are running, a new campaign is one command:

```powershell
cd C:\Users\User\Desktop\video_gen
.\py make_ad.py --item <product> --percent <number>
```

The display picks it up automatically — `latest.mp4` is replaced in place, and
it is served `no-store` so nothing caches the old one.

### Automating it

Task Scheduler → Create Task → Daily at your chosen hour → Action:

| Field | Value |
|---|---|
| Program | `C:\Users\User\Desktop\video_gen\py.cmd` |
| Arguments | `make_ad.py --item vegetables --percent 20` |
| Start in | `C:\Users\User\Desktop\video_gen` |

`make_ad.py` exits non-zero when validation fails, so a scheduled task will
report a failure rather than silently publishing a broken ad.

For a real deployment you also want ComfyUI and `serve.py` to start on boot —
Task Scheduler with "At startup", or NSSM to run them as services.

---

## All commands

```powershell
# health
.\py comfy_client.py --stats                       # server, VRAM, queue
.\py comfy_client.py --show                         # settings + SaveVideo count
.\py comfy_client.py --smoke                        # 320x192 test render, ~25s

# resolution
.\py scene.py --selftest                            # 15 sample inputs
.\py scene.py --item chappals --percent 50 --label item
.\py scene.py --item milk --percent 30 --json

# render only
.\py generate_ad.py --item vegetables --percent 20
.\py generate_ad.py --item vegetables --percent 20 --dry-run

# overlay only
.\py overlay.py --meta renders\<name>.json
.\py overlay.py --meta renders\<name>.json --preview 2.0

# everything
.\py make_ad.py --item vegetables --percent 20
.\py make_ad.py --item vegetables --percent 20 --skip-render

# serve
.\py serve.py
.\py serve.py --port 9000

# manual prompt edit in the ComfyUI graph itself
.\set_campaign.ps1 -Percent 10 -Product VEGETABLES -Store "grocery store"
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `'.' is not recognized as an internal or external command` | you are in **cmd**, and `activate.ps1` is PowerShell-only | use `.\py <script>`, or type `powershell` first |
| `'comfy-py' is not recognized...` | `activate.ps1` never ran (or ran in cmd) | use `.\py <script>` instead — works in both shells |
| `unrecognized arguments: #` | pasted a trailing `# comment` into cmd | paste the command only |
| `The module 'python_embeded' could not be loaded` | ran `python_embeded\python.exe` from the project folder | use `.\py <script>` |
| `python` opens Microsoft Store | the PATH `python` is a broken stub | use `.\py` |
| `Is ComfyUI running on http://127.0.0.1:8188?` | server not started | run `run_nvidia_gpu.bat` |
| `workflow_api.json is in UI format` | exported with `Save`, not `Export (API)` | ComfyUI → `Workflow → Export (API)` |
| `workflow_api.json not found` | never exported | same as above |
| Render takes ~80 min, produces **two** files | both `SaveVideo` nodes active | `--show` should list one; mute #4823 in ComfyUI and re-export |
| `Load LoRA` node red-bordered | `lora_name` does not match ComfyUI's list | on Windows it must be `ltxv\ltx2\...` with backslashes |
| Any loader node red-bordered | model file missing or renamed | compare the dropdown against README's model table |
| `VALIDATION FAILED — audio is silent` | render produced no audio | play the file in `renders\`; if silent, re-render |
| `VALIDATION FAILED — duration 5.02s` | frame count is 121, not 241 | check `--show`; fix `PrimitiveInt` #4979 |
| Text misspelled or doubled in the video | `--in-model-text` was used | drop the flag; text is composited by default |
| Voiceover reads extra words | prompt edited so the spoken line is unquoted or not last | keep the quoted line at the very end |
| Out of memory / crawling | wrong launch script, or another GPU app open | use `run_nvidia_gpu.bat`; close games/browsers with WebGL |
| Video will not seek in a player | server not handling ranges | make sure you are on `serve.py`, not a plain file server |
| Tunnel URL "cannot be resolved" | DNS has not propagated yet | wait ~30 s and retry |
| Tunnel URL stopped working | `cloudflared` window was closed | restart it; the hostname will be different |
| Display keeps showing yesterday's ad | caching between here and the screen | verify `curl -I .../latest.mp4` returns `Cache-Control: no-store` |
| Markdown files look like `â€"` in the terminal | PowerShell 5.1 reads UTF-8 as ANSI | `Get-Content file -Encoding UTF8`, or open in an editor |
| `git commit -m "…"` mangles the message | PowerShell native-argument quoting | `git commit -F messagefile.txt` |

### When a render fails validation

Nothing is published, and the file is kept so you can look at it. In order:

1. Read which check failed — it names the problem.
2. Play the file it names. Is the video fine and only the audio broken, or both?
3. `.\py comfy_client.py --show` — are frames/resolution what you expect?
4. Check the ComfyUI console window for errors during the run.
5. Re-run. Seeds are deterministic per campaign, so add `--seed 12345` to force
   a different result.

---

## Where things live

| Path | Contents |
|---|---|
| `video_gen\renders\` | raw text-free renders + campaign `.json` sidecars |
| `video_gen\out\` | finished ads, `latest.mp4`, `manifest.json` — this is what is served |
| `video_gen\items.json` | department library; edit to add sections |
| `video_gen\workflow_api.json` | the workflow the pipeline submits |
| `video_gen\workflows/store_ad.json` | the workflow you open in ComfyUI |
| `ComfyUI_windows_portable\ComfyUI\output\` | ComfyUI's own output, before we copy it |
| `ComfyUI_windows_portable\ComfyUI\models\` | checkpoints, LoRAs, text encoders |

`renders\` and `out\` are gitignored — they are generated, sometimes large.

---

## Do not

- **Do not tunnel port 8188.** It has no authentication.
- **Do not edit `workflows/upstream_template.json`.** It is the pristine template.
- **Do not "normalise" the LoRA backslashes.** They are correct for Windows.
- **Do not run both sampler branches** unless you deliberately want to compare —
  it costs 70 extra minutes per ad.
- **Do not trust in-model text.** It has produced `20% OF TOYS!` and a doubled
  `20%` on real runs.
- **Do not use a full render to test an overlay change.** `--preview` is one
  second and `--skip-render` is two.

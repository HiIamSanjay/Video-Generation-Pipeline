"""Phase 2 -- render an ad for one offer.

    .\\py generate_ad.py --item vegetables --percent 20
    .\\py generate_ad.py --item milk --percent 30 --label item --dry-run

Builds the prompt from scene.py, patches workflow_api.json, submits it to
ComfyUI, and copies the finished render into renders/ with a metadata sidecar
that Phase 3 uses to burn in the text.

By default the render contains NO on-screen text -- text is composited later,
because the model cannot spell reliably. --in-model-text asks the model to draw
it instead, for comparison.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDERS = HERE / "renders"

# ComfyUI's embedded distribution pins sys.path via python313._pth and does not
# include the script's own directory, so sibling imports need help.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import comfy_client as cc  # noqa: E402
import scene as sc  # noqa: E402

STYLE = (
    "Style: modern retail commercial, premium quality, colourful, family-friendly, "
    "cinematic lighting, smooth camera movement, vibrant colours, highly detailed, "
    "16:9 landscape."
)

# Composited-text mode. The centre is deliberately kept clear so the overlay has
# somewhere to sit, and text is forbidden in as many words as the encoder will hear.
NO_TEXT_CLAUSE = (
    "The centre of the frame is clean and uncluttered, leaving open space. "
    # Positive phrasing does more work here than the negative prompt: naming
    # bare shelf edges and plain packaging suppresses the invented gibberish
    # labels better than forbidding "signage" alone, and throwing the back of
    # the store out of focus hides the worst offenders.
    "Shelf edges are bare and completely blank, product packaging is plain and "
    "unbranded, and the back of the store is softly out of focus with a shallow "
    "depth of field. There is no text anywhere in the frame: no letters, no "
    "words, no numbers, no signage, no posters, no banners, no price tags, "
    "no labels, no writing of any kind."
)

NEG_NO_TEXT = (
    "text, letters, words, numbers, typography, signage, posters, banners, "
    "price tags, captions, subtitles, watermark, logos, writing, "
    "blurry visuals, dark lighting, low resolution, distorted products, "
    "excessive motion blur, flickering, empty shelves, "
    "robotic voice, silent, gibberish speech, extra spoken words, "
    "announcer reading the stage directions, mumbling"
)

NEG_IN_MODEL_TEXT = (
    "duplicated text, repeated text, second copy of the offer, "
    "three or more lines of text, extra text, text outside the graphic, "
    "repeated numbers, incorrect spelling, misspelled words, missing letters, "
    "gibberish text, unreadable text, crowded text, cluttered layout, "
    "distracting backgrounds, blurry visuals, dark lighting, low resolution, "
    "distorted products, excessive motion blur, flickering, robotic voice, silent"
)


def build_prompts(camp: dict, in_model_text: bool) -> tuple[str, str]:
    """(positive, negative) for this campaign."""
    opening = (
        f"A vibrant, high-energy 10-second retail advertisement filmed inside "
        f"{camp['scene']}. The camera glides smoothly forward through the aisle. "
        f"Bright cheerful lighting and a lively shopping atmosphere."
    )
    # The spoken line MUST be quoted and MUST come last in the whole prompt.
    # Unquoted, the model cannot tell where the script ends and reads the stage
    # direction aloud too -- "Accompanied by..." came out spoken as "acampass".
    audio = (
        f"Audio: cheerful, fast-paced background music throughout, with an "
        f"energetic, upbeat human male announcer reading out loud: "
        f'"{camp["vo_line"]}"'
    )

    if in_model_text:
        graphic = (
            "A single bold animated promotional graphic is centered in the "
            "foreground. It contains exactly two lines of text and nothing else.\n"
            f"Line 1 reads: {camp['line1']}\n"
            f"Line 2 reads: {camp['line2']}\n"
            "Line 1 is giant, bright red with a thick golden outline. Line 2 sits "
            "directly below it, smaller, clean white with a red outline. Each line "
            "appears exactly once, spelled exactly as written. No other text "
            "anywhere in the frame."
        )
        negative = NEG_IN_MODEL_TEXT
        # Suppress this campaign's own number appearing twice.
        negative = f"multiple {camp['percent']}s, " + negative
    else:
        graphic = NO_TEXT_CLAUSE
        negative = NEG_NO_TEXT

    # Audio last, so the quoted script is the final text the encoder sees.
    positive = "\n\n".join([opening, graphic, STYLE, audio])
    return positive, negative


def campaign_seed(item: str, percent: int) -> int:
    """Stable per campaign, so a re-run reproduces the same video."""
    h = hashlib.sha256(f"{sc.normalize(item)}|{percent}".encode()).digest()
    return int.from_bytes(h[:4], "big") % (2**31)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--item", required=True)
    ap.add_argument("--percent", type=int, required=True)
    ap.add_argument("--label", choices=("department", "item"), default="department")
    ap.add_argument("--in-model-text", action="store_true",
                    help="let the model draw the text instead of compositing it")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--seed", type=int, help="default: derived from item+percent")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--frames", type=int, help="must satisfy (frames-1) %% 8 == 0")
    ap.add_argument("--smoke", action="store_true",
                    help="320x192 / 25 frames, to test plumbing cheaply")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt, do not render")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--server", default=cc.DEFAULT_SERVER)
    ap.add_argument("--timeout", type=float, default=3600.0)
    args = ap.parse_args()

    camp = sc.build(args.item, args.percent, label=args.label, allow_llm=not args.no_llm)
    positive, negative = build_prompts(camp, args.in_model_text)
    seed = args.seed if args.seed is not None else campaign_seed(args.item, args.percent)

    if args.dry_run:
        print(f"item       {args.item}  ->  {camp['key']} (via {camp['source']})")
        print(f"on-screen  {camp['line1']} / {camp['line2']}")
        print(f"mode       {'in-model text' if args.in_model_text else 'text composited in Phase 3'}")
        print(f"seed       {seed}")
        print(f"\n----- POSITIVE -----\n{positive}")
        print(f"\n----- NEGATIVE -----\n{negative}")
        return 0

    wf = copy.deepcopy(cc.load_workflow())
    cc.patch(wf, cc.NODE["positive"], text=positive)
    cc.patch(wf, cc.NODE["negative"], text=negative)
    cc.patch(wf, cc.NODE["seed"], noise_seed=seed)
    prefix = f"ad_{camp['key']}_{args.percent}"
    cc.patch(wf, cc.NODE["save"], filename_prefix=prefix)

    if args.smoke:
        cc.patch(wf, cc.NODE["latent"], width=320, height=192)
        cc.patch(wf, cc.NODE["frames"], value=25)
    else:
        if args.width or args.height:
            cur = wf[cc.NODE["latent"]]["inputs"]
            w, h = args.width or cur["width"], args.height or cur["height"]
            if w % 32 or h % 32:
                raise SystemExit(f"{w}x{h}: both dimensions must be divisible by 32")
            cc.patch(wf, cc.NODE["latent"], width=w, height=h)
        if args.frames:
            if (args.frames - 1) % 8:
                raise SystemExit(f"{args.frames} frames: needs (frames-1) % 8 == 0")
            cc.patch(wf, cc.NODE["frames"], value=args.frames)

    print(f"{args.item} @ {args.percent}%  ->  {camp['key']}  ({camp['source']})")
    print(f"  {cc.describe(wf)}")
    print(f"  on-screen: {camp['line1']} / {camp['line2']}"
          + ("  [long]" if camp["line2_long"] else ""))

    client = cc.ComfyClient(args.server)
    prompt_id = client.submit(wf)
    print(f"  prompt_id {prompt_id}")
    entry = client.wait(prompt_id, timeout=args.timeout)

    files = [f for f in client.outputs(entry) if f["filename"].lower().endswith(".mp4")]
    if not files:
        print("render completed but produced no mp4", file=sys.stderr)
        return 1

    RENDERS.mkdir(exist_ok=True)
    stamp = dt.date.today().isoformat()
    base = f"{camp['key']}_{args.percent}_{stamp}"
    dest = client.download(files[0], RENDERS)
    final = RENDERS / f"{base}.mp4"
    if dest != final:
        shutil.move(str(dest), final)

    meta = {
        **camp,
        "item_input": args.item,
        "seed": seed,
        "in_model_text": args.in_model_text,
        "prompt_id": prompt_id,
        "rendered": stamp,
        "render": final.name,
        "positive": positive,
        "negative": negative,
    }
    (RENDERS / f"{base}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"  -> {final}")
    print(f"  -> {final.with_suffix('.json').name}  (campaign metadata for Phase 3)")
    if args.json:
        print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except cc.ComfyError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)

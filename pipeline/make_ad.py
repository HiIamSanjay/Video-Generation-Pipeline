"""Phase 4 -- one command from offer to finished ad.

    .\\py make_ad.py --item vegetables --percent 20
    .\\py make_ad.py --item milk --percent 30 --label item
    .\\py make_ad.py --item toys --percent 40 --skip-render   # re-composite only

resolve -> render -> composite -> validate -> out/

Validation is the point of this stage: an unattended daily job must refuse to
publish a broken ad rather than quietly serve one. Checks duration, resolution,
and that the audio track exists and is not silent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

paths.bootstrap()

import comfy_client as cc  # noqa: E402
import generate_ad as ga  # noqa: E402
import overlay as ov  # noqa: E402
import scene as sc  # noqa: E402

OUT = paths.OUT
RENDERS = paths.RENDERS
MANIFEST = paths.MANIFEST

# A silent track measures around -91 dB; real audio sits well above -40.
SILENCE_DB = -50.0
DURATION_TOLERANCE = 0.30


def audio_stats(ffmpeg: str, path: Path) -> dict:
    """mean/max volume in dB via volumedetect, which logs at info level."""
    res = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-v", "info", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    log = res.stderr
    out = {}
    for field in ("mean_volume", "max_volume"):
        if m := re.search(rf"{field}:\s*(-?\d+(?:\.\d+)?) dB", log):
            out[field] = float(m.group(1))
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate(ffmpeg: str, path: Path, expect_duration: float | None) -> tuple[bool, list[str]]:
    problems = []
    info = ov.probe(ffmpeg, path)

    if expect_duration and abs(info["duration"] - expect_duration) > DURATION_TOLERANCE:
        problems.append(
            f"duration {info['duration']:.2f}s, expected ~{expect_duration:.2f}s"
        )
    if info["w"] % 2 or info["h"] % 2:
        problems.append(f"odd dimensions {info['w']}x{info['h']} — many players reject these")

    a = audio_stats(ffmpeg, path)
    if not a:
        problems.append("no audio track")
    elif a.get("mean_volume", -99) < SILENCE_DB:
        problems.append(f"audio is silent (mean {a['mean_volume']} dB)")

    print(f"  checks: {info['w']}x{info['h']}  {info['duration']:.2f}s  "
          f"{info['frames']} frames  audio mean {a.get('mean_volume', 'n/a')} dB")
    return (not problems), problems


def newest_render(key: str, percent: int) -> Path | None:
    hits = sorted(RENDERS.glob(f"{key}_{percent}_*.mp4"))
    hits = [h for h in hits if "_v1-" not in h.name]
    return hits[-1] if hits else None


def update_manifest(entry: dict) -> None:
    data = {"ads": []}
    if MANIFEST.exists():
        try:
            data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass                      # corrupt manifest should not lose today's ad
    data.setdefault("ads", [])
    data["ads"] = [a for a in data["ads"] if a.get("file") != entry["file"]]
    data["ads"].append(entry)
    data["ads"] = data["ads"][-50:]
    data["current"] = entry
    # Atomic, so a polling client never reads half-written JSON.
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, MANIFEST)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--item", required=True)
    ap.add_argument("--percent", type=int, required=True)
    ap.add_argument("--label", choices=("department", "item"), default="department")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--skip-render", action="store_true",
                    help="reuse the newest render for this campaign")
    ap.add_argument("--in-model-text", action="store_true",
                    help="model draws the text; skips compositing")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--font", default="ariblk.ttf")
    ap.add_argument("--pop-at", type=float, default=0.35)
    ap.add_argument("--scrim", type=int, default=105)
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--server", default=cc.DEFAULT_SERVER)
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ffmpeg = ov.find_ffmpeg()
    camp = sc.build(args.item, args.percent, label=args.label, allow_llm=not args.no_llm)

    if args.dry_run:
        positive, negative = ga.build_prompts(camp, args.in_model_text)
        print(f"{args.item} @ {args.percent}%  ->  {camp['key']} ({camp['source']})")
        print(f"on-screen  {camp['line1']} / {camp['line2']}")
        print(f"voiceover  {camp['vo_line']}")
        print(f"seed       {args.seed if args.seed is not None else ga.campaign_seed(args.item, args.percent)}")
        print(f"\n----- POSITIVE -----\n{positive}\n\n----- NEGATIVE -----\n{negative}")
        return 0

    # --- render -------------------------------------------------------------
    if args.skip_render:
        render = newest_render(camp["key"], args.percent)
        if not render:
            print(f"no existing render for {camp['key']}_{args.percent}_*", file=sys.stderr)
            return 1
        print(f"{args.item} @ {args.percent}%  ->  {camp['key']}  (reusing {render.name})")
        # Fall back to the derived seed, or the manifest records null.
        seed = args.seed if args.seed is not None else ga.campaign_seed(args.item, args.percent)
        meta = {**camp, "item_input": args.item, "seed": seed,
                "rendered": dt.date.today().isoformat()}
    else:
        result = ga.render_campaign(
            args.item, args.percent, label=args.label,
            in_model_text=args.in_model_text, allow_llm=not args.no_llm,
            seed=args.seed, smoke=args.smoke, server=args.server, timeout=args.timeout,
        )
        render, meta = result["render_path"], result

    # --- composite ----------------------------------------------------------
    OUT.mkdir(exist_ok=True)
    stamp = dt.date.today().isoformat()
    final = OUT / f"{camp['key']}_{args.percent}_{stamp}.mp4"

    if args.in_model_text:
        shutil.copy2(render, final)
        print(f"  in-model text: copied render without compositing")
    else:
        ov.composite(
            render, camp["line1"], camp["line2"], out=final, font=args.font,
            size1=196, size2=76, pop_at=args.pop_at,
            scrim_strength=args.scrim, crf=args.crf, quiet=True,
        )
        print(f"  composited {camp['line1']} / {camp['line2']}")

    # --- validate -----------------------------------------------------------
    expect = None if args.smoke else 241 / 24
    ok, problems = validate(ffmpeg, final, expect)
    if not ok:
        print("\nVALIDATION FAILED — not publishing:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(f"  kept for inspection: {final}", file=sys.stderr)
        return 1

    # --- publish ------------------------------------------------------------
    # Copy to a temp name then os.replace, which is atomic on the same volume.
    # Writing latest.mp4 in place would let a client mid-download receive a mix
    # of the old and new file.
    latest = OUT / "latest.mp4"
    tmp = OUT / "latest.mp4.tmp"
    shutil.copy2(final, tmp)
    os.replace(tmp, latest)
    info = ov.probe(ffmpeg, final)
    update_manifest({
        "item": args.item,
        "department": camp["key"],
        "percent": args.percent,
        "line1": camp["line1"],
        "line2": camp["line2"],
        "vo_line": camp["vo_line"],
        "file": final.name,
        "url": f"/ads/{final.name}",
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration": round(info["duration"], 3),
        "width": info["w"],
        "height": info["h"],
        "bytes": final.stat().st_size,
        "sha256": sha256(final),
        "seed": meta.get("seed"),
        "in_model_text": args.in_model_text,
    })

    print(f"\n  {final}")
    print(f"  {latest.name}  (stable pointer for the display)")
    print(f"  {MANIFEST.name} updated")
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

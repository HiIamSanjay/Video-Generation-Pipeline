"""Phase 3 -- composite animated text onto a text-free render.

    .\\py overlay.py --meta renders\\vegetables_20_2026-08-03.json
    .\\py overlay.py --meta ... --preview 2.0        # single PNG, no encode

Text is drawn with Pillow and piped to ffmpeg as raw RGBA, so it is
pixel-exact: real fonts, exact colours, no spelling risk, and rock steady
across every frame (model-generated text morphs frame to frame).

Animation: SALE punches in with an overshoot, the offer line slides up under
it, then both hold -- no fade-out, so the clip loops cleanly on a display.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

OUT = paths.OUT
PREVIEWS = paths.PREVIEWS
FONTS = Path("C:/Windows/Fonts")

# Brand palette.
RED = (232, 25, 44, 255)
GOLD = (255, 199, 44, 255)
WHITE = (255, 255, 255, 255)
SHADOW = (0, 0, 0, 140)


def find_ffmpeg() -> str:
    """winget installs outside the PATH of already-open shells."""
    import shutil

    for name in ("ffmpeg",):
        if p := shutil.which(name):
            return p
    root = Path.home() / "AppData/Local/Microsoft/WinGet"
    hits = sorted(root.glob("**/ffmpeg.exe"))
    if hits:
        return str(hits[0])
    raise SystemExit("ffmpeg not found. winget install Gyan.FFmpeg")


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (FONTS / name, Path(name)):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    raise SystemExit(f"font not found: {name}")


def make_sprite(
    text: str,
    font_name: str,
    size: int,
    fill: tuple,
    stroke: tuple,
    stroke_w: int,
    max_width: int,
) -> Image.Image:
    """Render one line, shrinking to fit, cropped tight. Returns RGBA."""
    while size > 12:
        font = load_font(font_name, size)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
        if box[2] - box[0] <= max_width:
            break
        size = int(size * 0.94)

    pad = stroke_w * 3 + 24
    w = (box[2] - box[0]) + pad * 2
    h = (box[3] - box[1]) + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ox, oy = pad - box[0], pad - box[1]
    # Offset drop shadow first, then the stroked glyphs over it.
    d.text((ox + 6, oy + 7), text, font=font, fill=SHADOW,
           stroke_width=stroke_w, stroke_fill=SHADOW)
    d.text((ox, oy), text, font=font, fill=fill,
           stroke_width=stroke_w, stroke_fill=stroke)
    # Crop away the transparent padding, or its height inflates the line spacing.
    return img.crop(img.getbbox())


def scrim(w: int, h: int, cy: int, band: int, strength: int) -> Image.Image:
    """Soft dark band behind the text so it reads over busy produce."""
    ys = np.arange(h, dtype=np.float32)
    falloff = np.clip(1.0 - np.abs(ys - cy) / max(band, 1), 0.0, 1.0) ** 1.6
    alpha = (falloff * strength).astype(np.uint8)
    layer = np.zeros((h, w, 4), dtype=np.uint8)
    layer[..., 3] = alpha[:, None]
    return Image.fromarray(layer, "RGBA")


def ease_out_back(t: float, overshoot: float = 1.9) -> float:
    """0..1 with a slight overshoot past 1 -- the 'punch in' feel."""
    t -= 1.0
    return 1.0 + t * t * ((overshoot + 1.0) * t + overshoot)


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def compose_frame(
    t: float, canvas_size: tuple[int, int], sprites: dict, timing: dict, bg: Image.Image
) -> Image.Image:
    W, H = canvas_size
    frame = bg.copy()

    s1, s2 = sprites["line1"], sprites["line2"]
    y1, y2 = timing["y1"], timing["y2"]

    # --- line 1: scale punch + fade ---
    t1 = (t - timing["t1"]) / timing["dur1"]
    if t1 > 0:
        if t1 < 1:
            scale, alpha = ease_out_back(min(t1, 1.0)) * 0.999, ease_out_cubic(min(t1 * 1.6, 1.0))
        else:
            # Gentle idle pulse so it never looks like a frozen still.
            scale = 1.0 + 0.012 * np.sin((t - timing["t1"] - timing["dur1"]) * 2.6)
            alpha = 1.0
        paste_scaled(frame, s1, W // 2, y1, max(scale, 0.05), alpha)

    # --- line 2: slide up + fade ---
    t2 = (t - timing["t2"]) / timing["dur2"]
    if t2 > 0:
        p = ease_out_cubic(min(t2, 1.0))
        paste_scaled(frame, s2, W // 2, int(y2 + (1 - p) * 46), 1.0, p)

    return frame


def paste_scaled(dst: Image.Image, sprite: Image.Image, cx: int, cy: int,
                 scale: float, alpha: float) -> None:
    if alpha <= 0.003:
        return
    w = max(1, int(sprite.width * scale))
    h = max(1, int(sprite.height * scale))
    s = sprite if (w, h) == sprite.size else sprite.resize((w, h), Image.LANCZOS)
    if alpha < 0.997:
        a = s.getchannel("A").point(lambda v: int(v * alpha))
        s = s.copy()
        s.putalpha(a)
    dst.alpha_composite(s, (cx - w // 2, cy - h // 2))


def probe(ffmpeg: str, video: Path) -> dict:
    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe"))
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,nb_frames", "-show_entries",
         "format=duration", "-of", "json", str(video)],
        capture_output=True, text=True, check=True,
    ).stdout
    d = json.loads(out)
    st = d["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    duration = float(d["format"]["duration"])
    frames = int(st.get("nb_frames") or round(duration * fps))
    return {"w": int(st["width"]), "h": int(st["height"]), "fps": fps,
            "frames": frames, "duration": duration}


def composite(
    video: Path,
    line1: str,
    line2: str,
    out: Path | None = None,
    font: str = "ariblk.ttf",
    size1: int = 196,
    size2: int = 76,
    pop_at: float = 0.35,
    scrim_strength: int = 105,
    crf: int = 18,
    quiet: bool = False,
) -> Path:
    """Burn the two lines onto `video`. Returns the written path."""
    ffmpeg = find_ffmpeg()
    info = probe(ffmpeg, video)
    W, H = info["w"], info["h"]

    sprites = {
        "line1": make_sprite(line1, font, size1, RED, GOLD, 11, int(W * 0.72)),
        "line2": make_sprite(line2, font, size2, WHITE, RED, 6, int(W * 0.80)),
    }
    y1 = int(H * 0.38)
    gap = int(H * 0.035)
    y2 = y1 + sprites["line1"].height // 2 + gap + sprites["line2"].height // 2
    timing = {"t1": pop_at, "dur1": 0.42, "t2": pop_at + 0.30, "dur2": 0.40,
              "y1": y1, "y2": y2}
    bg = (scrim(W, H, (y1 + y2) // 2, int(H * 0.34), scrim_strength)
          if scrim_strength > 0 else Image.new("RGBA", (W, H), (0, 0, 0, 0)))

    OUT.mkdir(exist_ok=True)
    dest = out or OUT / video.name
    cmd = [
        ffmpeg, "-y", "-v", "error", *(() if quiet else ("-stats",)),
        "-i", str(video),
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
        "-r", f"{info['fps']:.6f}", "-i", "-",
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
        "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart",
        "-frames:v", str(info["frames"]), str(dest),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(info["frames"]):
            proc.stdin.write(
                compose_frame(i / info["fps"], (W, H), sprites, timing, bg).tobytes()
            )
    except BrokenPipeError:
        pass
    finally:
        if proc.stdin:
            proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg failed while compositing")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meta", type=Path, help="campaign JSON from generate_ad.py")
    ap.add_argument("--video", type=Path, help="override the video path")
    ap.add_argument("--line1")
    ap.add_argument("--line2")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--font", default="ariblk.ttf", help="file in C:/Windows/Fonts or a path")
    ap.add_argument("--size1", type=int, default=196, help="SALE size before fit-shrink")
    ap.add_argument("--size2", type=int, default=76)
    ap.add_argument("--pop-at", type=float, default=0.35, help="seconds before SALE lands")
    ap.add_argument("--scrim", type=int, default=105, help="0-255 backing darkness, 0 = off")
    ap.add_argument("--preview", type=float, help="write a single PNG at this timestamp")
    ap.add_argument("--crf", type=int, default=18)
    args = ap.parse_args()

    line1, line2, video = args.line1, args.line2, args.video
    if args.meta:
        meta = json.loads(args.meta.read_text(encoding="utf-8"))
        line1 = line1 or meta["line1"]
        line2 = line2 or meta["line2"]
        video = video or args.meta.with_suffix(".mp4")
    if not (video and line1 and line2):
        raise SystemExit("need --meta, or --video with --line1 and --line2")
    if not video.exists():
        raise SystemExit(f"video not found: {video}")

    ffmpeg = find_ffmpeg()
    info = probe(ffmpeg, video)
    W, H = info["w"], info["h"]

    sprites = {
        "line1": make_sprite(line1, args.font, args.size1, RED, GOLD, 11, int(W * 0.72)),
        "line2": make_sprite(line2, args.font, args.size2, WHITE, RED, 6, int(W * 0.80)),
    }
    y1 = int(H * 0.38)
    gap = int(H * 0.035)
    y2 = y1 + sprites["line1"].height // 2 + gap + sprites["line2"].height // 2
    timing = {"t1": args.pop_at, "dur1": 0.42,
              "t2": args.pop_at + 0.30, "dur2": 0.40, "y1": y1, "y2": y2}

    bg = (scrim(W, H, (y1 + y2) // 2, int(H * 0.34), args.scrim)
          if args.scrim > 0 else Image.new("RGBA", (W, H), (0, 0, 0, 0)))

    print(f"{video.name}  {W}x{H}  {info['fps']:.3f} fps  {info['frames']} frames")
    print(f"  line1 {line1!r}  sprite {sprites['line1'].size}")
    print(f"  line2 {line2!r}  sprite {sprites['line2'].size}")

    if args.preview is not None:
        frame = compose_frame(args.preview, (W, H), sprites, timing, bg)
        # Flatten over the real video frame so contrast is judged truthfully.
        PREVIEWS.mkdir(parents=True, exist_ok=True)
        still = PREVIEWS / "_bg.png"
        subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", str(args.preview),
                        "-i", str(video), "-frames:v", "1", str(still)], check=True)
        base = Image.open(still).convert("RGBA")
        base.alpha_composite(frame)
        dest = args.out or (PREVIEWS / f"preview_{args.preview:g}s.png")
        base.convert("RGB").save(dest)
        still.unlink(missing_ok=True)
        print(f"  -> {dest}")
        return 0

    dest = composite(
        video, line1, line2, out=args.out, font=args.font, size1=args.size1,
        size2=args.size2, pop_at=args.pop_at, scrim_strength=args.scrim, crf=args.crf,
    )
    print(f"  -> {dest}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)

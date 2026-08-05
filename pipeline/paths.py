"""Every path in the project, resolved from this file's location.

Import this rather than computing paths from __file__ in each module -- when the
layout changes, only this file should need editing.

    video_gen/
      pipeline/     this code
      config/       items.json, workflow_api.json
      workflows/    ComfyUI graphs (UI format, opened in the editor)
      docs/         HOWTORUN, TEACHING, PLAN
      archive/      superseded, kept for history
      renders/      raw text-free renders          (generated)
      out/          finished ads, served to display (generated)
      previews/     single-frame overlay previews   (generated)
"""

from __future__ import annotations

import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
ROOT = PIPELINE.parent

CONFIG = ROOT / "config"
WORKFLOWS = ROOT / "workflows"
DOCS = ROOT / "docs"
ARCHIVE = ROOT / "archive"

RENDERS = ROOT / "renders"
OUT = ROOT / "out"
PREVIEWS = ROOT / "previews"

ITEMS = CONFIG / "items.json"
WORKFLOW_API = CONFIG / "workflow_api.json"
STORE_AD_WORKFLOW = WORKFLOWS / "store_ad.json"

MANIFEST = OUT / "manifest.json"
LATEST = OUT / "latest.mp4"


def bootstrap() -> None:
    """Put pipeline/ on sys.path so sibling modules import when run as scripts.

    ComfyUI's embedded distribution pins sys.path via python313._pth and does
    not add the script's own directory, so this is required, not belt-and-braces.
    """
    if str(PIPELINE) not in sys.path:
        sys.path.insert(0, str(PIPELINE))


def ensure_dirs() -> None:
    for d in (RENDERS, OUT, PREVIEWS):
        d.mkdir(parents=True, exist_ok=True)

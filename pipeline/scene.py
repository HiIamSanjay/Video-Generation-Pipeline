"""Resolve a store item into the pieces an ad needs.

Given ("vegetables", 20) produce:
  scene     -- visual description for the video prompt
  line1/2   -- on-screen text, composited later by ffmpeg
  vo_line   -- what the voiceover says

Resolution order: exact key, alias, token overlap, fuzzy match, Claude API
(if ANTHROPIC_API_KEY is set), then the 'general' fallback. Nothing hard-fails.

    python_embeded\\python.exe scene.py --item vegetables --percent 20
    python_embeded\\python.exe scene.py --selftest
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

ITEMS_PATH = paths.ITEMS
FALLBACK_KEY = "general"

# Long on-screen lines shrink to fit and get hard to read on a display.
LINE2_SOFT_LIMIT = 24

ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_to_words(n: int) -> str:
    """0-100 -> spoken words. The voiceover needs 'twenty five', not '25'."""
    if not 0 <= n <= 100:
        raise ValueError(f"percent out of range: {n}")
    if n == 100:
        return "one hundred"
    if n < 20:
        return ONES[n]
    tens, ones = divmod(n, 10)
    return TENS[tens] + (f" {ONES[ones]}" if ones else "")


def normalize(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)      # drop punctuation, keep words
    s = re.sub(r"\b\d+\s*(kg|g|ml|l|pcs?|pack|packet|litre|liter)\b", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


@dataclass
class Scene:
    key: str
    display: str
    vo_noun: str
    scene: str
    source: str          # curated | alias | fuzzy | llm | fallback
    matched_on: str = ""


class Library:
    def __init__(self, path: Path = ITEMS_PATH):
        self.path = path
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.entries = {k: v for k, v in raw.items() if not k.startswith("_")}
        self.meta = {k: v for k, v in raw.items() if k.startswith("_")}
        if FALLBACK_KEY not in self.entries:
            raise SystemExit(f"{path.name} must contain a '{FALLBACK_KEY}' entry")

        self.alias_map: dict[str, str] = {}
        for key, e in self.entries.items():
            self.alias_map[normalize(key.replace("_", " "))] = key
            self.alias_map[normalize(e["display"])] = key
            for a in e.get("aliases", []):
                self.alias_map[normalize(a)] = key

    # ---------- resolution ----------

    def resolve(self, item: str, allow_llm: bool = True) -> Scene:
        q = normalize(item)
        if not q:
            return self._make(FALLBACK_KEY, "fallback", "empty item")

        if q in self.entries:
            return self._make(q, "curated", q)
        if q in self.alias_map:
            return self._make(self.alias_map[q], "alias", q)

        # Token overlap catches "fresh vegetables 1kg" or "amul butter".
        tokens = set(q.split())
        best, best_score = None, 0
        for alias, key in self.alias_map.items():
            atoks = set(alias.split())
            if not atoks:
                continue
            overlap = len(tokens & atoks) / len(atoks)
            if overlap > best_score:
                best, best_score = (alias, key), overlap
        if best and best_score >= 1.0:
            return self._make(best[1], "alias", best[0])

        # Typos: "vegitables", "stationary".
        close = difflib.get_close_matches(q, list(self.alias_map), n=1, cutoff=0.82)
        if close:
            return self._make(self.alias_map[close[0]], "fuzzy", close[0])

        if allow_llm and os.environ.get("ANTHROPIC_API_KEY"):
            entry = self._ask_llm(item)
            if entry:
                return entry

        return self._make(FALLBACK_KEY, "fallback", q)

    def _make(self, key: str, source: str, matched_on: str) -> Scene:
        e = self.entries[key]
        return Scene(
            key=key,
            display=e["display"],
            vo_noun=e["vo_noun"],
            scene=e["scene"],
            source=source,
            matched_on=matched_on,
        )

    # ---------- llm expansion ----------

    def _ask_llm(self, item: str) -> Scene | None:
        """Write a scene for an unknown item, then cache it back to items.json.

        Deliberately constrained: the model supplies the scene sentence and the
        two word forms only. Prompt assembly, the no-text rule and the negative
        prompt stay with us -- an unconstrained model writes 'a neon sign flashes
        20% OFF', which is the exact defect the overlay exists to remove.
        """
        system = (
            "You describe retail store sections for a video generation model. "
            "Reply with JSON only: "
            '{"key":"snake_case","display":"UPPERCASE","vo_noun":"spoken plural form",'
            '"scene":"one visual sentence"}. '
            "The scene is one sentence, under 45 words, describing the aisle or "
            "section of a general store where this item is sold: shelving, the "
            "goods, and the lighting. "
            "Critical: never mention text, signage, labels, price tags, posters, "
            "banners or writing of any kind. The frame contains no text. "
            "vo_noun completes the sentence 'Get twenty percent off on ___' and "
            "must read naturally (use 'dairy products', not 'dairy')."
        )
        payload = {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "system": system,
            "messages": [{"role": "user", "content": f"Item on sale: {item}"}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                body = json.loads(r.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in body.get("content", []))
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            data = json.loads(text)
            for f in ("key", "display", "vo_noun", "scene"):
                if not isinstance(data.get(f), str) or not data[f].strip():
                    raise ValueError(f"missing field {f}")
        except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as e:
            print(f"note: LLM expansion failed ({e}); using fallback", file=sys.stderr)
            return None

        key = normalize(data["key"]).replace(" ", "_") or normalize(item).replace(" ", "_")
        entry = {
            "display": data["display"].upper()[:28],
            "vo_noun": data["vo_noun"],
            "aliases": sorted({normalize(item), normalize(data["display"])} - {""}),
            "scene": data["scene"],
            "source": "llm",
        }
        self.entries[key] = entry
        self._save()
        for a in entry["aliases"]:
            self.alias_map[a] = key
        return self._make(key, "llm", normalize(item))

    def _save(self) -> None:
        out = {**self.meta, **self.entries}
        self.path.write_text(
            json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


# ---------- assembly ----------


def build(
    item: str,
    percent: int,
    lib: Library | None = None,
    allow_llm: bool = True,
    label: str = "department",
) -> dict:
    """label='department' advertises the section ("OFF ON DAIRY");
    label='item' advertises what the store actually discounted ("OFF ON MILK").
    The scene comes from the department either way."""
    if not 1 <= percent <= 99:
        raise SystemExit(f"percent must be 1-99, got {percent}")
    lib = lib or Library()
    s = lib.resolve(item, allow_llm=allow_llm)

    display, vo_noun = s.display, s.vo_noun
    if label == "item":
        cleaned = normalize(item)
        if cleaned:                      # empty input keeps the department label
            display, vo_noun = cleaned.upper(), cleaned

    line2 = f"{percent}% OFF ON {display}"
    return {
        **asdict(s),
        "label_mode": label,
        "display": display,
        "vo_noun": vo_noun,
        "percent": percent,
        "line1": "SALE",
        "line2": line2,
        "line2_len": len(line2),
        "line2_long": len(line2) > LINE2_SOFT_LIMIT,
        "vo_line": (
            f"The big sale is live right now! "
            f"Get {number_to_words(percent)} percent off on {vo_noun}!"
        ),
    }


SELFTEST = [
    ("vegetables", 20), ("vegetable", 10), ("Fresh Vegetables 1kg", 15),
    ("vegitables", 20), ("stationary", 25), ("milk", 30), ("Amul Butter 500g", 10),
    ("dal", 5), ("ice cream", 40), ("chappals", 50), ("mobile accessories", 15),
    ("dog food", 20), ("storewide", 10), ("quantum flux capacitor", 20), ("", 20),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--item")
    ap.add_argument("--percent", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-llm", action="store_true", help="skip Claude API expansion")
    ap.add_argument(
        "--label",
        choices=("department", "item"),
        default="department",
        help="advertise the department (OFF ON DAIRY) or the item (OFF ON MILK)",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    lib = Library()

    if args.selftest:
        key = os.environ.get("ANTHROPIC_API_KEY")
        print(f"library: {len(lib.entries)} departments")
        print(f"LLM expansion: {'enabled' if key else 'disabled (no ANTHROPIC_API_KEY)'}\n")
        print(f"{'input':24} {'key':13} {'src':9} {'department label':24} item label")
        print("-" * 100)
        for item, pct in SELFTEST:
            d = build(item, pct, lib, allow_llm=not args.no_llm, label="department")
            i = build(item, pct, lib, allow_llm=not args.no_llm, label="item")
            shown = repr(item) if item == "" else item
            flag = " <-long" if d["line2_long"] or i["line2_long"] else ""
            print(f"{shown:24} {d['key']:13} {d['source']:9} {d['line2']:24} {i['line2']}{flag}")
        print("\nvoiceover samples")
        for item, pct in [("vegetables", 20), ("milk", 5), ("dal", 35), ("storewide", 50)]:
            print(f"  {build(item, pct, lib, allow_llm=False)['vo_line']}")
        return 0

    if not args.item:
        ap.print_help()
        return 0

    r = build(args.item, args.percent, lib, allow_llm=not args.no_llm, label=args.label)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print(f"item      {args.item}")
        print(f"matched   {r['key']}  (via {r['source']}: {r['matched_on']!r})")
        print(f"on-screen {r['line1']} / {r['line2']}"
              + ("   [long, will shrink to fit]" if r["line2_long"] else ""))
        print(f"voiceover {r['vo_line']}")
        print(f"scene     {r['scene']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

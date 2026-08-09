#!/usr/bin/env python3
"""Crypto Lore Series — Genesis Series 2026: deterministic production pipeline.

All card content comes from set_manifest.json. This file owns geometry, layout,
serial numbering, foil masks and PDFs — and never invents card data. A missing
manifest field is a hard failure, not a default.

Print geometry is computed once from inches x DPI as integers; no float
accumulation. Any texture jitter uses a per-card seeded RNG so re-runs are
byte-reproducible.

    python generate.py validate
    python generate.py compose --card N [--serial S | --all-serials] [--face front|back|both]
    python generate.py prep    --in PATH [--strategy fit-trim|cover]
    python generate.py proof   --in PATH
    python generate.py foil    --in PATH
    python generate.py sheet
    python generate.py all
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "set_manifest.json"
FONT_DIR = ROOT / "fonts"
OUT_PRINT = ROOT / "output" / "print"
OUT_PROOF = ROOT / "output" / "proofs"
OUT_FOIL = ROOT / "output" / "foil"
OUT_PDF = ROOT / "output" / "pdf"

# ---------------------------------------------------------------------------
# Print geometry — exact integers, derived once from inches x DPI.
# Changing anything here requires updating PRINT_SPEC.md.
# ---------------------------------------------------------------------------
DPI = 600
TRIM_W_IN, TRIM_H_IN = 2.5, 3.5
BLEED_IN = 0.125
SAFE_IN = 0.125

TRIM_W = int(round(TRIM_W_IN * DPI))          # 1500
TRIM_H = int(round(TRIM_H_IN * DPI))          # 2100
BLEED = int(round(BLEED_IN * DPI))            # 75
SAFE = int(round(SAFE_IN * DPI))              # 75
FULL_W = TRIM_W + 2 * BLEED                   # 1650
FULL_H = TRIM_H + 2 * BLEED                   # 2250

TRIM_BOX = (BLEED, BLEED, BLEED + TRIM_W, BLEED + TRIM_H)
SAFE_BOX = (BLEED + SAFE, BLEED + SAFE, BLEED + TRIM_W - SAFE, BLEED + TRIM_H - SAFE)

BORDER_BAND = 96       # opal foil edge; covers the 75px bleed with margin
INNER_RULE = 14        # gold rule inboard of the border band

RARITIES = ("mythic", "epic", "rare", "uncommon", "common")
CATEGORIES = ("archetype", "force", "relic", "realm", "token")
ABILITY_KINDS = ("active", "passive", "ultimate")
STATUSES = (
    "named", "lore_complete", "art_prompted", "art_incoming",
    "art_approved", "composed", "proofed", "print_ready",
)
# Cards at or beyond this status must carry the full content payload.
CONTENT_FROM = STATUSES.index("lore_complete")

TIER_TABLE = {"mythic": 5, "epic": 10, "rare": 20, "uncommon": 30, "common": 35}

# Tier palettes: (foil, foil_dark, accent, holo_strength)
TIER_STYLE = {
    "mythic":   {"foil": (232, 190, 104), "dark": (94, 66, 22), "accent": (255, 226, 158), "holo": 1.00, "serial": (240, 200, 112)},
    "epic":     {"foil": (222, 178, 96),  "dark": (84, 58, 20),  "accent": (246, 214, 146), "holo": 0.72, "serial": (232, 196, 120)},
    "rare":     {"foil": (208, 166, 88),  "dark": (74, 52, 18),  "accent": (238, 204, 136), "holo": 0.42, "serial": (214, 182, 116)},
    "uncommon": {"foil": (186, 190, 198), "dark": (62, 66, 72),  "accent": (226, 230, 238), "holo": 0.30, "serial": (198, 202, 210)},
    "common":   {"foil": (154, 132, 96),  "dark": (54, 46, 32),  "accent": (198, 178, 140), "holo": 0.22, "serial": (176, 160, 128)},
}

INK = (232, 224, 208)
INK_DIM = (176, 166, 146)
PANEL = (14, 12, 11)


class PipelineError(RuntimeError):
    """Raised instead of silently substituting a default."""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise PipelineError(f"missing manifest: {MANIFEST_PATH}")
    with MANIFEST_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def card_by_num(manifest: dict, num: int) -> dict:
    for card in manifest["cards"]:
        if card["num"] == num:
            return card
    raise PipelineError(f"card {num} is not in the manifest")


def require(card: dict, field: str):
    """Fabrication firewall: fail loudly rather than invent a value."""
    if field not in card or card[field] in (None, "", [], {}):
        raise PipelineError(
            f"card {card.get('num', '?')} ({card.get('name', '?')}) is missing "
            f"required field '{field}' — add it to set_manifest.json first"
        )
    return card[field]


def slug(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in name]
    out = "".join(keep)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def cmd_validate(_args) -> int:
    manifest = load_manifest()
    cards = manifest.get("cards", [])
    errors: list[str] = []
    warnings: list[str] = []

    if len(cards) != 100:
        errors.append(f"expected 100 cards, found {len(cards)}")

    nums = [c.get("num") for c in cards]
    if sorted(nums) != list(range(1, 101)):
        missing = sorted(set(range(1, 101)) - set(nums))
        dupes = sorted({n for n in nums if nums.count(n) > 1})
        if missing:
            errors.append(f"missing card numbers: {missing}")
        if dupes:
            errors.append(f"duplicate card numbers: {dupes}")

    names = [c.get("name", "") for c in cards]
    dupe_names = sorted({n for n in names if names.count(n) > 1})
    if dupe_names:
        errors.append(f"duplicate card names: {dupe_names}")

    counts = {r: 0 for r in RARITIES}
    for card in cards:
        num = card.get("num", "?")
        rarity = card.get("rarity")
        if rarity not in RARITIES:
            errors.append(f"card {num}: bad rarity {rarity!r}")
        else:
            counts[rarity] += 1

        if card.get("category") not in CATEGORIES:
            errors.append(f"card {num}: bad category {card.get('category')!r}")

        status = card.get("status")
        if status not in STATUSES:
            errors.append(f"card {num}: bad status {status!r}")
            continue

        if STATUSES.index(status) < CONTENT_FROM:
            continue

        for field in ("subtitle", "type", "alignment", "stats", "abilities",
                      "weakness", "lore", "traits", "flavor", "art_front",
                      "art_back_template"):
            if field not in card or card[field] in (None, "", [], {}):
                errors.append(f"card {num}: status '{status}' requires field '{field}'")

        stats = card.get("stats") or []
        if len(stats) != 5:
            errors.append(f"card {num}: expected 5 stats, found {len(stats)}")
        hundreds = 0
        for stat in stats:
            value = stat.get("value")
            if not isinstance(value, int) or not 0 <= value <= 100:
                errors.append(f"card {num}: stat {stat.get('label')!r} must be an int 0-100")
            elif value == 100:
                hundreds += 1
        if hundreds > 1:
            errors.append(f"card {num}: {hundreds} stats at 100, at most one allowed")

        abilities = card.get("abilities") or []
        kinds = [a.get("kind") for a in abilities]
        if sorted(kinds) != sorted(ABILITY_KINDS):
            errors.append(f"card {num}: abilities must be exactly one active, one passive, one ultimate (found {kinds})")
        for ability in abilities:
            if len((ability.get("name") or "").split()) > 3:
                warnings.append(f"card {num}: ability name over 3 words: {ability.get('name')!r}")
            if len((ability.get("text") or "").split()) > 20:
                warnings.append(f"card {num}: ability text over 20 words: {ability.get('name')!r}")

        traits = card.get("traits") or []
        if len(traits) != 4:
            errors.append(f"card {num}: expected exactly 4 traits, found {len(traits)}")
        for trait in traits:
            if len((trait.get("label") or "").split()) > 2:
                warnings.append(f"card {num}: trait label over 2 words: {trait.get('label')!r}")

        lore_sentences = [s for s in (card.get("lore") or "").replace("!", ".").replace("?", ".").split(".") if s.strip()]
        if not 2 <= len(lore_sentences) <= 4:
            warnings.append(f"card {num}: lore should be 2-4 sentences, found {len(lore_sentences)}")

        if len((card.get("flavor") or "").split()) > 10:
            warnings.append(f"card {num}: flavor over 10 words")

        if STATUSES.index(status) >= STATUSES.index("art_approved"):
            art = ROOT / card["art_front"]
            if not art.exists():
                errors.append(f"card {num}: status '{status}' but art file is missing: {card['art_front']}")

    for rarity, expected in TIER_TABLE.items():
        if counts[rarity] != expected:
            errors.append(f"rarity '{rarity}': expected {expected} cards, found {counts[rarity]}")

    for line in warnings:
        print(f"WARN  {line}")
    for line in errors:
        print(f"ERROR {line}")

    if errors:
        print(f"\nvalidate: FAILED — {len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"\nvalidate: OK — 100 cards, tier counts match, {len(warnings)} warning(s)")
    return 0


# ---------------------------------------------------------------------------
# Imaging helpers (Pillow imported lazily so `validate` runs without it)
# ---------------------------------------------------------------------------

def _imaging():
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        import numpy as np
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise PipelineError(
            f"{exc.name} is required for image commands (pip install -r requirements.txt)"
        ) from exc
    return Image, ImageDraw, ImageFilter, ImageFont, np


_FONT_FILES = {
    "display": "CinzelDecorative-Bold.ttf",
    "display_light": "CinzelDecorative-Regular.ttf",
    "caps": "Cinzel[wght].ttf",
    "body": "EBGaramond[wght].ttf",
    "italic": "EBGaramond-Italic[wght].ttf",
}
_font_cache: dict = {}


def font(role: str, size: int, weight: int | None = None):
    _, _, _, ImageFont, _ = _imaging()
    key = (role, size, weight)
    if key in _font_cache:
        return _font_cache[key]
    path = FONT_DIR / _FONT_FILES[role]
    if not path.exists():
        raise PipelineError(f"missing font: {path}")
    face = ImageFont.truetype(str(path), size)
    if weight is not None:
        try:
            face.set_variation_by_axes([weight])
        except Exception:
            pass
    _font_cache[key] = face
    return face


def text_size(draw, text: str, face) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=face)
    return box[2] - box[0], box[3] - box[1]


def fit_font(draw, text: str, role: str, max_width: int, start: int, minimum: int, weight=None):
    size = start
    while size > minimum:
        face = font(role, size, weight)
        if text_size(draw, text, face)[0] <= max_width:
            return face
        size -= 2
    return font(role, minimum, weight)


def wrap(draw, text: str, face, max_width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if text_size(draw, trial, face)[0] <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_tracked(draw, xy, text: str, face, fill, tracking: int = 0, anchor_center_x: int | None = None):
    """Letter-spaced text; Pillow has no tracking of its own."""
    if tracking <= 0 and anchor_center_x is None:
        draw.text(xy, text, font=face, fill=fill)
        return
    widths = [draw.textlength(ch, font=face) for ch in text]
    total = sum(widths) + tracking * max(len(text) - 1, 0)
    x = (anchor_center_x - total / 2) if anchor_center_x is not None else xy[0]
    y = xy[1]
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=face, fill=fill)
        x += w + tracking
    return total



ART_FOCUS_DEFAULT = 0.24


def place_art(Image, art_path, box, focus: float = ART_FOCUS_DEFAULT):
    """Cover-fit finished art into a card region, trimming the painted canvas edge.

    `focus` is where the kept slice sits in the discarded overflow: 0 keeps the
    top of the painting, 1 the bottom. Cards whose subject sits low can override
    it with `art_focus` in the manifest rather than by editing this file.
    """
    if not art_path.exists():
        raise PipelineError(f"missing art file: {art_path}")
    art = Image.open(art_path).convert("RGB")
    inset_x, inset_y = int(art.width * 0.025), int(art.height * 0.025)
    art = art.crop((inset_x, inset_y, art.width - inset_x, art.height - inset_y))
    bw, bh = box[2] - box[0], box[3] - box[1]
    scale = max(bw / art.width, bh / art.height)
    art = art.resize((max(int(math.ceil(art.width * scale)), bw),
                      max(int(math.ceil(art.height * scale)), bh)), Image.LANCZOS)
    left = (art.width - bw) // 2
    top = max(0, min(int((art.height - bh) * float(focus)), art.height - bh))
    return art.crop((left, top, left + bw, top + bh))


def save_print(img, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", dpi=(DPI, DPI))
    return path


# ---------------------------------------------------------------------------
# Card furniture
#
# Design target is the approved Genesis look: full-bleed art with a prismatic
# opal foil edge, a beveled gold title, a colour-coded stat rail, medallion
# abilities and a badge system. Every element is drawn, so it repeats across
# 100 cards without drift.
# ---------------------------------------------------------------------------

GOLD_HI = (255, 236, 178)
GOLD = (226, 178, 78)
GOLD_DEEP = (146, 100, 28)
GOLD_SHADOW = (44, 28, 6)
PLATE_BG = (10, 9, 12)
PLATE_BG2 = (20, 17, 22)

# Stat rail chip colours, applied by row so a card always reads the same way.
CHIP_COLORS = [
    ((16, 52, 40), (86, 226, 170)),
    ((54, 42, 12), (240, 202, 110)),
    ((38, 24, 66), (178, 150, 255)),
    ((62, 16, 26), (255, 128, 140)),
    ((14, 40, 68), (128, 196, 255)),
]

ABILITY_PILL = {
    "active": ((36, 78, 60), (120, 240, 190)),
    "passive": ((58, 46, 14), (246, 212, 128)),
    "ultimate": ((60, 22, 30), (255, 150, 160)),
}


def _cell_noise(Image, np, rng, w: int, h: int, cell: int):
    """Smooth random field whose features are roughly `cell` pixels across.

    Built by upsampling a small random grid, so the grain size is set in print
    pixels rather than by a spatial frequency that changes with canvas size.
    """
    lw, lh = max(w // cell, 2), max(h // cell, 2)
    grid = (rng.random((lh, lw)) * 255.0).astype("uint8")
    up = Image.fromarray(grid, "L").resize((w, h), Image.BICUBIC)
    return np.asarray(up).astype(np.float32) / 255.0


def _hsv_to_rgb(np, hue, sat, val):
    i = np.floor(hue * 6.0)
    f = hue * 6.0 - i
    p = val * (1.0 - sat)
    q = val * (1.0 - sat * f)
    t = val * (1.0 - sat * (1.0 - f))
    i = (i % 6).astype(np.int32)
    sel = [i == 0, i == 1, i == 2, i == 3, i == 4, i == 5]
    rr = np.select(sel, [val, q, p, p, t, val])
    gg = np.select(sel, [t, val, val, q, p, p])
    bb = np.select(sel, [p, p, t, val, val, q])
    return np.stack([rr, gg, bb], axis=-1) * 255.0


def opal_band(Image, np, seed: int, strength: float):
    """Crushed-opal holographic laminate for the card edge.

    The reference cards use a bright, fine-grained prismatic glitter: mostly
    pale, high-value flecks with the full hue wheel scattered through them at a
    grain of roughly ten print pixels. Anything built from plain sinusoids comes
    out as a regular candy stripe, so every field here is upsampled noise.
    """
    h, w = FULL_H, FULL_W
    rng = np.random.default_rng(seed)

    coarse = _cell_noise(Image, np, rng, w, h, 130)    # broad opal clouds
    mid = _cell_noise(Image, np, rng, w, h, 42)        # facet structure
    fine = _cell_noise(Image, np, rng, w, h, 13)       # crystal grain

    # A diagonal sweep whose phase is dragged around by the coarse field: it
    # gives the laminate its veining without ever repeating as a candy stripe.
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    diag = xx / (w - 1) + yy / (h - 1)
    sweep = 0.5 + 0.5 * np.sin(diag * 26.0 + coarse * 13.0)

    hue = (coarse * 1.7 + sweep * 0.55 + fine * 0.30) % 1.0

    # Saturation stays low. Opal laminate is pale — mostly white with colour
    # blooming through it. Pushing saturation up is what turned earlier passes
    # into neon static.
    sat = (0.10 + 0.30 * strength) * (0.22 + 0.78 * mid)

    # High value floor keeps the band bright; the sweep supplies the contrast.
    val = np.clip(0.60 + 0.30 * mid + 0.16 * fine + 0.20 * sweep, 0.0, 1.0)

    out = _hsv_to_rgb(np, hue.astype(np.float32), sat.astype(np.float32), val.astype(np.float32))
    out = out * 0.84 + 255.0 * 0.16            # laminate whites out under light

    speck = rng.random((h, w)).astype(np.float32)
    out = np.where((speck > 0.990)[..., None], 255.0, out)          # highlights
    out = np.where((speck < 0.006)[..., None], out * 0.52, out)     # dark grit
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")


def vgradient(Image, size, top, mid, bottom):
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        if t < 0.5:
            k = t / 0.5
            c = tuple(int(top[i] + (mid[i] - top[i]) * k) for i in range(3))
        else:
            k = (t - 0.5) / 0.5
            c = tuple(int(mid[i] + (bottom[i] - mid[i]) * k) for i in range(3))
        px[0, y] = c
    return img.resize((w, h))


def gold_text(canvas, Image, ImageDraw, text: str, face, cx: int, y: int,
              stroke: int = 5, shadow: int = 5):
    """Beveled, gradient-filled gold type — the set's title treatment."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    box = draw.textbbox((0, 0), text, font=face)
    tw, th = box[2] - box[0], box[3] - box[1]
    x = int(cx - tw / 2) - box[0]
    top = y - box[1]

    draw.text((x + shadow, top + shadow), text, font=face, fill=(0, 0, 0, 190),
              stroke_width=stroke, stroke_fill=(0, 0, 0, 190))
    draw.text((x, top), text, font=face, fill=GOLD_SHADOW,
              stroke_width=stroke, stroke_fill=GOLD_SHADOW)

    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).text((x, top), text, font=face, fill=255)
    grad = vgradient(Image, canvas.size, GOLD_HI, GOLD, GOLD_DEEP)
    band = vgradient(Image, (canvas.size[0], max(th, 2)), GOLD_HI, GOLD, GOLD_DEEP)
    grad.paste(band, (0, y))
    canvas.paste(grad, (0, 0), mask)

    hi = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(hi).text((x, top - 3), text, font=face, fill=90)
    ImageDraw.Draw(hi).text((x, top), text, font=face, fill=0)
    canvas.paste(Image.new("RGB", canvas.size, GOLD_HI), (0, 0), hi)
    return tw


def gold_text_left(canvas, Image, ImageDraw, text: str, face, x: int, y: int,
                   stroke: int = 3, shadow: int = 3):
    """Left-aligned gold type; `gold_text` only centres."""
    d = ImageDraw.Draw(canvas, "RGBA")
    box = d.textbbox((0, 0), text, font=face)
    return gold_text(canvas, Image, ImageDraw, text, face,
                     x + (box[2] - box[0]) // 2, y, stroke=stroke, shadow=shadow)


def pale_text(draw, text: str, face, x: int, y: int, fill=(246, 242, 234), anchor=None):
    """Near-white display numerals with a dark rim, as on the reference chips."""
    draw.text((x, y), text, font=face, fill=(0, 0, 0, 200), anchor=anchor,
              stroke_width=4, stroke_fill=(0, 0, 0, 200))
    draw.text((x, y), text, font=face, fill=fill, anchor=anchor)


def fit_block(draw, text, role, width, height, sizes, weight=None):
    """Largest size from `sizes` whose wrapped block fits `height`."""
    face = font(role, sizes[-1], weight)
    lines = wrap(draw, text, face, width)
    for size in sizes:
        f = font(role, size, weight)
        ls = wrap(draw, text, f, width)
        if len(ls) * (size + 6) <= height:
            return f, ls
        face, lines = f, ls
    return face, lines


def gold_rule(draw, x0, y, x1, width=4):
    draw.line((x0, y, x1, y), fill=GOLD_DEEP, width=width + 2)
    draw.line((x0, y - 1, x1, y - 1), fill=GOLD, width=max(width - 2, 1))


def plate(canvas, Image, ImageDraw, box, radius=16, fill=None, rule=True, alpha=232):
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    body = (fill or PLATE_BG) + (alpha,)
    d.rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius=radius, fill=body)
    canvas.alpha_composite(layer, (x0, y0))
    if rule:
        d2 = ImageDraw.Draw(canvas, "RGBA")
        d2.rounded_rectangle(box, radius=radius, outline=GOLD_DEEP + (255,), width=3)
        d2.rounded_rectangle((x0 + 3, y0 + 3, x1 - 3, y1 - 3), radius=max(radius - 3, 2),
                             outline=GOLD + (90,), width=1)


def icon_glyph(draw, cx, cy, r, kind: str, color):
    """Small drawn pictographs — the display faces carry no symbol glyphs."""
    if kind == "diamond":
        draw.polygon([(cx, cy - r), (cx + r * 0.8, cy), (cx, cy + r), (cx - r * 0.8, cy)], fill=color)
    elif kind == "shield":
        draw.polygon([(cx - r * 0.8, cy - r * 0.8), (cx + r * 0.8, cy - r * 0.8),
                      (cx + r * 0.8, cy * 1.0 + r * 0.1), (cx, cy + r), (cx - r * 0.8, cy + r * 0.1)],
                     fill=color)
    elif kind == "chevron":
        for k in (0, 1):
            off = k * r * 0.7
            draw.polygon([(cx, cy - r + off), (cx + r * 0.75, cy + off),
                          (cx, cy - r * 0.35 + off), (cx - r * 0.75, cy + off)], fill=color)
    elif kind == "chevron_right":                     # the trait list's »
        t = max(int(r * 0.42), 3)
        for k in (0, 1):
            x = cx - r * 0.9 + k * r * 0.85
            draw.line([(x, cy - r * 0.8), (x + r * 0.7, cy), (x, cy + r * 0.8)],
                      fill=color, width=t, joint="curve")
    elif kind == "heart":
        rr = r * 0.55
        draw.ellipse((cx - rr * 1.5, cy - rr, cx + rr * 0.1, cy + rr * 0.4), fill=color)
        draw.ellipse((cx - rr * 0.1, cy - rr, cx + rr * 1.5, cy + rr * 0.4), fill=color)
        draw.polygon([(cx - r * 0.86, cy), (cx + r * 0.86, cy), (cx, cy + r)], fill=color)
    elif kind == "hex":
        pts = [(cx + math.cos(math.pi / 6 + i * math.pi / 3) * r,
                cy + math.sin(math.pi / 6 + i * math.pi / 3) * r) for i in range(6)]
        draw.polygon(pts, fill=color)
    elif kind == "ring":
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=max(int(r * 0.32), 3))
    else:                                             # star
        pts = []
        for i in range(16):
            ang = -math.pi / 2 + i * math.pi / 8
            rad = r if i % 2 == 0 else r * 0.44
            pts.append((cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
        draw.polygon(pts, fill=color)


STAT_ICONS = ("diamond", "shield", "chevron", "heart", "hex")
ABILITY_ICONS = {"active": "diamond", "passive": "shield", "ultimate": "star"}


def roundel(canvas, Image, ImageDraw, cx, cy, r, letter, face):
    d = ImageDraw.Draw(canvas, "RGBA")
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(8, 7, 9, 255), outline=GOLD_DEEP, width=6)
    d.ellipse((cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12), outline=GOLD, width=4)
    gold_text(canvas, Image, ImageDraw, letter, face, cx, cy - int(r * 0.52), stroke=3, shadow=3)


def rarity_badge(canvas, Image, ImageDraw, box, rarity, style):
    x0, y0, x1, y1 = box
    cx = (x0 + x1) // 2
    plate(canvas, Image, ImageDraw, box, radius=(y1 - y0) // 2, fill=(26, 18, 44), alpha=245)
    d = ImageDraw.Draw(canvas, "RGBA")
    d.text((cx, y0 + 14), "RARITY", font=font("caps", 22, 600), fill=(198, 186, 220), anchor="ma")
    gold_text(canvas, Image, ImageDraw, rarity.upper(), font("display", 38), cx, y0 + 44,
              stroke=3, shadow=3)
    icon_glyph(ImageDraw.Draw(canvas, "RGBA"), cx, y1 - 26, 15, "diamond", style["accent"])


def stat_chip(canvas, Image, ImageDraw, box, label, value, idx):
    """Icon + wrapped label above a large pale numeral, as on the reference rail."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    bg, accent = CHIP_COLORS[idx % len(CHIP_COLORS)]

    # Opaque gradient body pasted through a rounded mask. Earlier versions drew
    # a translucent sheen over the fill, but ImageDraw writes the source alpha
    # into the layer instead of accumulating it, which punched the chip full of
    # holes and let the art show through the stat labels.
    grad = Image.new("RGB", (w, h), bg)
    gd = ImageDraw.Draw(grad)
    for i in range(h):
        k = 0.20 * (1.0 - i / max(h - 1, 1)) ** 2
        gd.line((0, i, w, i), fill=tuple(int(c + (255 - c) * k) for c in bg))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=16, fill=255)
    canvas.paste(grad, (x0, y0), mask)

    d2 = ImageDraw.Draw(canvas, "RGBA")
    d2.rounded_rectangle(box, radius=16, outline=GOLD_DEEP + (255,), width=4)
    d2.rounded_rectangle((x0 + 4, y0 + 4, x1 - 4, y1 - 4), radius=13,
                         outline=GOLD + (110,), width=2)

    icon_glyph(d2, x0 + 46, y0 + 46, 24, STAT_ICONS[idx % len(STAT_ICONS)], accent)

    lab_w = w - 96
    lface = font("caps", 26, 650)
    lines = wrap(d2, label.upper(), lface, lab_w)
    if len(lines) > 2:                       # very long labels drop a size first
        lface = font("caps", 22, 650)
        lines = wrap(d2, label.upper(), lface, lab_w)[:2]
    ly = y0 + 26 if len(lines) > 1 else y0 + 34
    for line in lines:
        d2.text((x0 + 82, ly), line, font=lface, fill=(236, 232, 242))
        ly += lface.size + 4

    pale_text(d2, f"{value}", font("caps", 58, 700), x0 + 34, y1 - 76)


def cmd_compose(args) -> int:
    manifest = load_manifest()
    card = card_by_num(manifest, args.card)
    serials = range(1, 101) if args.all_serials else [args.serial or 1]
    faces = ("front", "back") if args.face == "both" else (args.face,)
    base = f"{card['num']:03d}_{slug(card['name'])}"
    for serial in serials:
        for face in faces:
            img = compose_front(card, serial) if face == "front" else compose_back(card, serial)
            out = OUT_PRINT / f"{base}_{face}_{serial:03d}.png"
            save_print(img, out)
            print(f"composed {out.relative_to(ROOT)}  ({img.width}x{img.height} @ {DPI}dpi)")
    return 0


def card_base(Image, ImageDraw, np, card, seed):
    """Opal foil edge with a black field inside it. Shared by both faces.

    Art and furniture are placed by the faces; `inner_frame` closes the gold
    rule afterwards so nothing paints over it.
    """
    style = TIER_STYLE[card["rarity"]]
    canvas = opal_band(Image, np, seed, style["holo"]).convert("RGBA")
    d = ImageDraw.Draw(canvas, "RGBA")
    d.rectangle((BORDER_BAND, BORDER_BAND, FULL_W - BORDER_BAND - 1, FULL_H - BORDER_BAND - 1),
                fill=(7, 6, 9, 255))
    return canvas, style


def art_box(card, bottom: int):
    return (BORDER_BAND, BORDER_BAND, FULL_W - BORDER_BAND, bottom)


def paste_art(canvas, Image, card, bottom: int):
    box = art_box(card, bottom)
    art = place_art(Image, ROOT / require(card, "art_front"), box,
                    float(card.get("art_focus", ART_FOCUS_DEFAULT)))
    canvas.paste(art, (box[0], box[1]))


def paste_back_plate(canvas, Image, card):
    """Full-bleed ornate back plate. The back never repeats the card art.

    Scaled to the field rather than cover-cropped: the plate is a designed
    frame, and cropping it eats the corner medallions and the outer rule.
    """
    path = ROOT / require(card, "art_back_template")
    if not path.exists():
        raise PipelineError(f"missing back template: {path}")
    img = Image.open(path).convert("RGB")
    ix, iy = int(img.width * 0.02), int(img.height * 0.02)     # painted canvas edge
    img = img.crop((ix, iy, img.width - ix, img.height - iy))
    box = (BORDER_BAND, BORDER_BAND, FULL_W - BORDER_BAND, FULL_H - BORDER_BAND)
    canvas.paste(img.resize((box[2] - box[0], box[3] - box[1]), Image.LANCZOS), (box[0], box[1]))


def sheen(canvas, Image, np, strength: float = 1.0):
    """Diagonal gloss sweep so the finished card reads as laminated foil stock."""
    w, h = canvas.size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = xx / (w - 1) * 0.72 + (1.0 - yy / (h - 1)) * 0.28
    band = (np.exp(-((t - 0.28) ** 2) / 0.017)
            + np.exp(-((t - 0.63) ** 2) / 0.006) * 0.60)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 255, 247, 228
    rgba[..., 3] = np.clip(band * 52.0 * strength, 0, 60).astype(np.uint8)
    canvas.alpha_composite(Image.fromarray(rgba, "RGBA"))


def text_glow(canvas, Image, ImageDraw, ImageFilter, text, face, cx, y,
              color=(255, 206, 122), radius: int = 22, alpha: int = 165):
    """Warm bloom behind display type — the reference titles sit in their own light."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    box = d.textbbox((0, 0), text, font=face)
    d.text((int(cx - (box[2] - box[0]) / 2) - box[0], y - box[1]), text,
           font=face, fill=color + (alpha,))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius)))


def inner_frame(canvas, ImageDraw):
    d = ImageDraw.Draw(canvas, "RGBA")
    d.rectangle((BORDER_BAND - 6, BORDER_BAND - 6, FULL_W - BORDER_BAND + 5, FULL_H - BORDER_BAND + 5),
                outline=GOLD_DEEP, width=6)
    d.rectangle((BORDER_BAND, BORDER_BAND, FULL_W - BORDER_BAND - 1, FULL_H - BORDER_BAND - 1),
                outline=GOLD, width=3)


def scrim(canvas, Image, ImageDraw, box, top_alpha=0, bottom_alpha=238):
    x0, y0, x1, y1 = box
    h = y1 - y0
    layer = Image.new("RGBA", (x1 - x0, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(h):
        t = i / max(h - 1, 1)
        a = int(top_alpha + (bottom_alpha - top_alpha) * (t ** 1.4))
        d.line((0, i, x1 - x0, i), fill=(6, 5, 8, a))
    canvas.alpha_composite(layer, (x0, y0))


def compose_front(card: dict, serial: int):
    Image, ImageDraw, ImageFilter, ImageFont, np = _imaging()
    canvas, style = card_base(Image, ImageDraw, np, card, card["num"])
    manifest = load_manifest()
    cx = FULL_W // 2
    L, R = BORDER_BAND + 26, FULL_W - BORDER_BAND - 26

    # Art fills the card from the foil edge down to the panel field.
    PANEL_TOP = 1268
    paste_art(canvas, Image, card, PANEL_TOP)
    scrim(canvas, Image, ImageDraw, (BORDER_BAND, PANEL_TOP - 210, FULL_W - BORDER_BAND, PANEL_TOP), 0, 250)
    scrim(canvas, Image, ImageDraw, (BORDER_BAND, BORDER_BAND, FULL_W - BORDER_BAND, BORDER_BAND + 440), 238, 0)
    d = ImageDraw.Draw(canvas, "RGBA")
    gold_rule(d, BORDER_BAND, PANEL_TOP, FULL_W - BORDER_BAND, 5)

    # --- badges (placed first; the title is fitted around them) -------------
    monogram = require(card, "name").replace("The ", "")[:1].upper()
    roundel(canvas, Image, ImageDraw, BORDER_BAND + 92, BORDER_BAND + 94, 80,
            monogram, font("display", 80))

    ed_box = (R - 330, BORDER_BAND + 18, R, BORDER_BAND + 132)
    plate(canvas, Image, ImageDraw, ed_box, radius=12, fill=PLATE_BG2)
    d = ImageDraw.Draw(canvas, "RGBA")
    d.text(((ed_box[0] + ed_box[2]) // 2, ed_box[1] + 16), "GENESIS EDITION",
           font=font("caps", 24, 600), fill=(214, 198, 166), anchor="ma")
    pale_text(d, f"{serial:03d}/100", font("caps", 48, 700),
              (ed_box[0] + ed_box[2]) // 2, ed_box[1] + 50, anchor="ma")

    rarity_badge(canvas, Image, ImageDraw, (R - 300, BORDER_BAND + 152, R, BORDER_BAND + 292),
                 card["rarity"], style)

    # --- series line + title ------------------------------------------------
    # The title owns the band between the roundel and the edition plate, which
    # is off-centre — the same asymmetry the reference cards have.
    t_left = BORDER_BAND + 92 + 80 + 22
    t_right = ed_box[0] - 18
    t_cx = (t_left + t_right) // 2
    t_width = t_right - t_left

    d = ImageDraw.Draw(canvas, "RGBA")
    series_face = font("caps", 30, 600)
    sw = draw_tracked(d, (0, -999), manifest["set"]["title"].upper(), series_face,
                      (0, 0, 0, 0), tracking=8, anchor_center_x=t_cx)
    plate(canvas, Image, ImageDraw,
          (int(t_cx - sw / 2) - 34, BORDER_BAND + 16, int(t_cx + sw / 2) + 34, BORDER_BAND + 70),
          radius=10, fill=PLATE_BG2, alpha=210)
    d = ImageDraw.Draw(canvas, "RGBA")
    draw_tracked(d, (0, BORDER_BAND + 26), manifest["set"]["title"].upper(), series_face,
                 (232, 214, 178), tracking=8, anchor_center_x=t_cx)

    title = require(card, "name").upper()
    tface = fit_font(d, title, "display", t_width, 148, 54)
    tbox = d.textbbox((0, 0), title, font=tface)
    title_y = BORDER_BAND + 84
    text_glow(canvas, Image, ImageDraw, ImageFilter, title, tface, t_cx, title_y, radius=26)
    gold_text(canvas, Image, ImageDraw, title, tface, t_cx, title_y)

    sub = require(card, "subtitle").upper()
    sub_y = title_y + (tbox[3] - tbox[1]) + 30
    d = ImageDraw.Draw(canvas, "RGBA")
    sface = fit_font(d, sub, "caps", t_width - 120, 36, 17, weight=600)
    subw = draw_tracked(d, (0, sub_y), sub, sface, (236, 220, 184), tracking=7, anchor_center_x=t_cx)
    rule_y = sub_y + sface.size + 14
    gold_rule(d, int(t_cx - subw / 2) - 40, rule_y, int(t_cx - subw / 2) - 8, 3)
    gold_rule(d, int(t_cx + subw / 2) + 8, rule_y, int(t_cx + subw / 2) + 40, 3)

    # --- stat rail ----------------------------------------------------------
    stats = require(card, "stats")
    chip_w, chip_h, gap = 330, 146, 14
    sy = BORDER_BAND + 340
    for i, stat in enumerate(stats):
        top = sy + i * (chip_h + gap)
        stat_chip(canvas, Image, ImageDraw, (L, top, L + chip_w, top + chip_h),
                  stat["label"], stat["value"], i)

    # --- type line ----------------------------------------------------------
    d = ImageDraw.Draw(canvas, "RGBA")
    tl = f"TYPE: {require(card,'type').upper()}    |    ALIGNMENT: {require(card,'alignment').upper()}    |    RARITY: {card['rarity'].upper()}"
    tface2 = fit_font(d, tl, "caps", R - L - 20, 30, 17, weight=600)
    draw_tracked(d, (0, PANEL_TOP + 28), tl, tface2, (236, 224, 196), tracking=2, anchor_center_x=cx)
    gold_rule(d, L, PANEL_TOP + 80, R, 3)

    # --- bottom stack is measured upward from the footer --------------------
    ft_bot = FULL_H - BORDER_BAND - 22
    ft_top = ft_bot - 100
    fl_bot = ft_top - 18
    fl_top = fl_bot - 212
    wk_bot = fl_top - 18
    wk_top = wk_bot - 88

    # --- abilities: medallion left, copy right, three columns ---------------
    ab_top = PANEL_TOP + 106
    ab_bot = wk_top - 20
    plate(canvas, Image, ImageDraw, (L, ab_top, R, ab_bot), radius=14, fill=PLATE_BG2, alpha=246)
    d = ImageDraw.Draw(canvas, "RGBA")
    lab = font("caps", 24, 700)
    lw = d.textlength("ABILITIES", font=lab) + 40
    d.rectangle((cx - lw / 2 - 22, ab_top - 13, cx + lw / 2 + 22, ab_top + 13), fill=(20, 17, 22, 255))
    draw_tracked(d, (0, ab_top - 12), "ABILITIES", lab, GOLD_HI, tracking=6, anchor_center_x=cx)

    abilities = sorted(require(card, "abilities"), key=lambda a: ABILITY_KINDS.index(a["kind"]))
    colw = (R - L) // 3
    med_r = 46
    for i, ab in enumerate(abilities):
        x0 = L + i * colw
        if i:
            d.line((x0, ab_top + 26, x0, ab_bot - 26), fill=GOLD_DEEP + (150,), width=2)

        mcx, mcy = x0 + 30 + med_r, ab_top + 44 + med_r
        d.ellipse((mcx - med_r, mcy - med_r, mcx + med_r, mcy + med_r),
                  fill=(12, 10, 14, 255), outline=GOLD_DEEP, width=5)
        d.ellipse((mcx - med_r + 10, mcy - med_r + 10, mcx + med_r - 10, mcy + med_r - 10),
                  outline=GOLD + (150,), width=2)
        icon_glyph(d, mcx, mcy, 26, ABILITY_ICONS[ab["kind"]], GOLD_HI)

        tx = mcx + med_r + 22
        tw = x0 + colw - 24 - tx
        nface = fit_font(d, ab["name"].upper(), "caps", tw, 30, 17, weight=700)
        nlines = wrap(d, ab["name"].upper(), nface, tw)[:2]
        ny = ab_top + 40
        for line in nlines:
            d.text((tx, ny), line, font=nface, fill=(244, 240, 232))
            ny += nface.size + 4

        pbg, pfg = ABILITY_PILL[ab["kind"]]
        ptxt = ab["kind"].upper()
        pf = font("caps", 19, 700)
        pw = d.textlength(ptxt, font=pf) + 26
        py = ny + 6
        d.rounded_rectangle((tx, py, tx + pw, py + 34), radius=8,
                            fill=pbg + (255,), outline=pfg + (170,), width=2)
        d.text((tx + pw / 2, py + 5), ptxt, font=pf, fill=pfg, anchor="ma")

        body_top = py + 50
        bface, blines = fit_block(d, ab["text"], "body", tw, ab_bot - 24 - body_top,
                                  (30, 28, 26, 24, 22), 450)
        ty = body_top
        for line in blines:
            d.text((tx, ty), line, font=bface, fill=(224, 216, 202))
            ty += bface.size + 6

    # --- weakness -----------------------------------------------------------
    plate(canvas, Image, ImageDraw, (L, wk_top, R, wk_bot), radius=12, fill=PLATE_BG2, alpha=246)
    d = ImageDraw.Draw(canvas, "RGBA")
    draw_tracked(d, (L + 26, wk_top + 30), "WEAKNESS", font("caps", 24, 700), GOLD_HI, tracking=5)
    d.line((L + 214, wk_top + 22, L + 214, wk_bot - 22), fill=GOLD_DEEP + (200,), width=2)
    wface = fit_font(d, card["weakness"], "body", R - L - 270, 28, 18, weight=450)
    d.text((L + 244, wk_top + 28), require(card, "weakness"), font=wface, fill=(226, 218, 204))

    # --- flavor + market lore ----------------------------------------------
    split = L + int((R - L) * 0.38)
    plate(canvas, Image, ImageDraw, (L, fl_top, split - 10, fl_bot), radius=12, fill=PLATE_BG2, alpha=246)
    plate(canvas, Image, ImageDraw, (split + 10, fl_top, R, fl_bot), radius=12, fill=PLATE_BG2, alpha=246)
    d = ImageDraw.Draw(canvas, "RGBA")
    draw_tracked(d, (L + 24, fl_top + 20), "FLAVOR TEXT", font("caps", 22, 700), GOLD_HI, tracking=4)
    fface, flines = fit_block(d, f"“{require(card,'flavor')}”", "italic",
                              split - L - 50, fl_bot - fl_top - 76, (30, 28, 26, 24, 22), 500)
    fy = fl_top + 58
    for line in flines:
        d.text((L + 24, fy), line, font=fface, fill=(238, 228, 208))
        fy += fface.size + 8

    draw_tracked(d, (split + 34, fl_top + 20), "MARKET LORE", font("caps", 22, 700), GOLD_HI, tracking=4)
    lface, llines = fit_block(d, require(card, "lore"), "body",
                              R - split - 60, fl_bot - fl_top - 74, (26, 24, 22, 20, 18), 450)
    ly = fl_top + 56
    for line in llines:
        d.text((split + 34, ly), line, font=lface, fill=(224, 216, 202))
        ly += lface.size + 5

    # --- footer -------------------------------------------------------------
    plate(canvas, Image, ImageDraw, (L, ft_top, R, ft_bot), radius=12, fill=PLATE_BG, alpha=250)
    d = ImageDraw.Draw(canvas, "RGBA")
    draw_tracked(d, (L + 26, ft_top + 16), "SUPPLY", font("caps", 20, 600), (188, 176, 156), tracking=4)
    sup = card.get("supply") or manifest["set"]["supply_default"]
    d.text((L + 26, ft_top + 48), sup.upper(), font=font("caps", 26, 700), fill=GOLD_HI)
    roundel(canvas, Image, ImageDraw, cx, (ft_top + ft_bot) // 2, 44,
            require(card, "name").replace("The ", "")[:1].upper(), font("display", 42))
    d = ImageDraw.Draw(canvas, "RGBA")
    d.text((R - 26, ft_top + 14), manifest["set"]["series"].upper(),
           font=font("caps", 24, 600), fill=(212, 198, 170), anchor="ra")
    pale_text(d, f"CARD #{card['num']:03d}", font("caps", 40, 700), R - 26, ft_top + 48, anchor="ra")

    sheen(canvas, Image, np, 0.85)
    inner_frame(canvas, ImageDraw)
    return canvas.convert("RGB")


# Where the furniture sits on the back, as fractions of the field inside the
# foil edge. These are tuned to the shipped back templates: the title lands on
# the engraved nameplate, the lore on the gold shield, the traits on the open
# starfield below it. Regenerating a template means re-checking these.
BACK_BANDS = {
    "title": (0.150, 0.262),
    "lore": (0.310, 0.598),
    "traits": (0.616, 0.855),
    "flavor": 0.882,
    "serial": 0.907,
    "footer": 0.958,
}


def _band(name):
    """Band fractions → absolute y, or a single y for the scalar anchors."""
    inner_top, inner_h = BORDER_BAND, FULL_H - 2 * BORDER_BAND
    v = BACK_BANDS[name]
    if isinstance(v, tuple):
        return inner_top + int(inner_h * v[0]), inner_top + int(inner_h * v[1])
    return inner_top + int(inner_h * v)


def _trait_rows(d, card, size: int, L: int, R: int):
    head = font("caps", min(size - 2, 28), 700)
    body = font("body", size, 450)
    traits = require(card, "traits")
    label_w = int(max(d.textlength(t["label"].upper(), font=head) for t in traits))
    col_chev = L + 82 + label_w + 30
    col_text = col_chev + 58
    rows = [(t["label"].upper(), wrap(d, t["text"], body, R - col_text - 40)) for t in traits]
    line_h = size + 6
    row_h = size + 28
    total = sum(row_h + max(len(r[1]) - 1, 0) * line_h for r in rows)
    return {"head": head, "body": body, "rows": rows, "col_chev": col_chev,
            "col_text": col_text, "line_h": line_h, "row_h": row_h, "total": total}


def compose_back(card: dict, serial: int):
    """Ornate template plate + engraved type. The back never repeats the art."""
    Image, ImageDraw, ImageFilter, ImageFont, np = _imaging()
    canvas, style = card_base(Image, ImageDraw, np, card, card["num"] + 1000)
    manifest = load_manifest()
    cx = FULL_W // 2
    L, R = BORDER_BAND + 26, FULL_W - BORDER_BAND - 26
    inner = (BORDER_BAND, BORDER_BAND, FULL_W - BORDER_BAND, FULL_H - BORDER_BAND)

    paste_back_plate(canvas, Image, card)
    # The plates come back lit like jewellery. A flat veil pulls them down to
    # the reference's antique register and gives the type something to sit on.
    canvas.alpha_composite(
        Image.new("RGBA", (inner[2] - inner[0], inner[3] - inner[1]), (5, 4, 8, 96)),
        (inner[0], inner[1]))
    d = ImageDraw.Draw(canvas, "RGBA")

    # --- header --------------------------------------------------------------
    series_face = font("caps", 28, 600)
    sw = draw_tracked(d, (0, -999), manifest["set"]["title"].upper(), series_face,
                      (0, 0, 0, 0), tracking=8, anchor_center_x=cx)
    plate(canvas, Image, ImageDraw,
          (int(cx - sw / 2) - 34, BORDER_BAND + 20, int(cx + sw / 2) + 34, BORDER_BAND + 74),
          radius=10, fill=PLATE_BG2, alpha=200)
    d = ImageDraw.Draw(canvas, "RGBA")
    draw_tracked(d, (0, BORDER_BAND + 30), manifest["set"]["title"].upper(), series_face,
                 (234, 216, 178), tracking=8, anchor_center_x=cx)

    t_top, t_bot = _band("title")
    title = require(card, "name").upper()
    tface = fit_font(d, title, "display", R - L - 300, 118, 46)
    tbox = d.textbbox((0, 0), title, font=tface)
    th = tbox[3] - tbox[1]
    sub = require(card, "subtitle").upper()
    sface = fit_font(d, sub, "caps", R - L - 340, 34, 18, weight=600)
    block_h = th + 26 + sface.size
    title_y = t_top + max((t_bot - t_top - block_h) // 2, 0)

    text_glow(canvas, Image, ImageDraw, ImageFilter, title, tface, cx, title_y, radius=26)
    gold_text(canvas, Image, ImageDraw, title, tface, cx, title_y)
    d = ImageDraw.Draw(canvas, "RGBA")
    sub_y = title_y + th + 26
    subw = draw_tracked(d, (0, sub_y), sub, sface, (240, 226, 192), tracking=7, anchor_center_x=cx)
    rule_y = sub_y + sface.size + 12
    gold_rule(d, int(cx - subw / 2) - 46, rule_y, int(cx - subw / 2) - 12, 3)
    gold_rule(d, int(cx + subw / 2) + 12, rule_y, int(cx + subw / 2) + 46, 3)

    # --- lore on parchment, sized to its band -------------------------------
    l_top, l_bot = _band("lore")
    lface, llines = fit_block(d, require(card, "lore"), "body", R - L - 96,
                              l_bot - l_top - 72, (44, 42, 40, 38, 36, 34, 32, 30, 28), 450)
    step = lface.size + 14
    # Sized to the copy with a generous margin, then centred in the band: a
    # parchment stretched to the full band reads as an empty box on short lore.
    lore_h = min(len(llines) * step + 112, l_bot - l_top)
    ly0 = l_top + (l_bot - l_top - lore_h) // 2
    parch = Image.new("RGBA", (R - L, lore_h), (0, 0, 0, 0))
    ImageDraw.Draw(parch).rounded_rectangle((0, 0, R - L - 1, lore_h - 1), radius=12,
                                            fill=(218, 206, 180, 249))
    canvas.alpha_composite(parch, (L, ly0))
    d = ImageDraw.Draw(canvas, "RGBA")
    d.rounded_rectangle((L, ly0, R, ly0 + lore_h), radius=12, outline=GOLD_DEEP, width=5)
    d.rounded_rectangle((L + 11, ly0 + 11, R - 11, ly0 + lore_h - 11), radius=7,
                        outline=(152, 132, 94, 160), width=2)
    ly = ly0 + (lore_h - len(llines) * step) // 2 + 4
    for line in llines:
        d.text((cx, ly), line, font=lface, fill=(30, 25, 20), anchor="ma")
        ly += step

    # --- traits --------------------------------------------------------------
    tr_top, tr_bot = _band("traits")
    for size in (36, 34, 32, 30, 28, 26, 24, 22):
        tr = _trait_rows(d, card, size, L, R)
        if tr["total"] <= tr_bot - tr_top - 78:
            break
    plate(canvas, Image, ImageDraw, (L, tr_top, R, tr_bot), radius=12, fill=PLATE_BG2, alpha=214)
    d = ImageDraw.Draw(canvas, "RGBA")
    head = tr["head"]
    lw = d.textlength("TRAITS", font=head) + 36
    d.rectangle((cx - lw / 2 - 22, tr_top - 14, cx + lw / 2 + 22, tr_top + 14), fill=(20, 17, 22, 255))
    draw_tracked(d, (0, tr_top - 13), "TRAITS", head, GOLD_HI, tracking=6, anchor_center_x=cx)

    ty = tr_top + (tr_bot - tr_top - tr["total"] + 40) // 2
    for i, (label, lines2) in enumerate(tr["rows"]):
        icon_glyph(d, L + 44, ty + 16, 17, STAT_ICONS[i % len(STAT_ICONS)],
                   CHIP_COLORS[i % len(CHIP_COLORS)][1])
        d.text((L + 82, ty), label, font=head, fill=GOLD_HI)
        icon_glyph(d, tr["col_chev"] + 18, ty + 18, 15, "chevron_right", GOLD)
        for j, line in enumerate(lines2):
            d.text((tr["col_text"], ty + j * tr["line_h"]), line,
                   font=tr["body"], fill=(228, 220, 206))
        ty += tr["row_h"] + max(len(lines2) - 1, 0) * tr["line_h"]

    # --- flavor + serial + footer -------------------------------------------
    # The template's bottom scrollwork is busy; the closing lines get their own
    # dark field so the serial stays legible.
    foot_top = BORDER_BAND + int((FULL_H - 2 * BORDER_BAND) * 0.865)
    scrim(canvas, Image, ImageDraw, (BORDER_BAND, foot_top - 46, FULL_W - BORDER_BAND, foot_top), 0, 214)
    d = ImageDraw.Draw(canvas, "RGBA")
    d.rectangle((BORDER_BAND, foot_top, FULL_W - BORDER_BAND, FULL_H - BORDER_BAND), fill=(6, 5, 9, 214))

    flavor = f"\u201c{require(card, 'flavor')}\u201d"
    fface = fit_font(d, flavor, "italic", R - L - 80, 44, 24, weight=500)
    d.text((cx, _band("flavor")), flavor, font=fface, fill=(244, 230, 202), anchor="ma")

    serial_face = font("caps", 56, 700)
    serial_y = _band("serial")
    text_glow(canvas, Image, ImageDraw, ImageFilter, f"#{card['num']:03d} / 100",
              serial_face, cx, serial_y, radius=18, alpha=120)
    d = ImageDraw.Draw(canvas, "RGBA")
    pale_text(d, f"#{card['num']:03d} / 100", serial_face, cx, serial_y, anchor="ma")
    foot = f"{manifest['set']['series'].upper()}  \u2022  {serial:03d}/100"
    draw_tracked(d, (0, _band("footer")), foot, font("caps", 26, 600),
                 (214, 200, 172), tracking=5, anchor_center_x=cx)

    sheen(canvas, Image, np, 1.0)
    inner_frame(canvas, ImageDraw)
    return canvas.convert("RGB")

# ---------------------------------------------------------------------------

def cmd_prep(args) -> int:
    Image, _, _, _, np = _imaging()
    src = Path(args.inp)
    if not src.exists():
        raise PipelineError(f"missing input: {src}")
    art = Image.open(src).convert("RGB")

    if args.strategy == "cover":
        scale = max(FULL_W / art.width, FULL_H / art.height)
        art = art.resize((int(math.ceil(art.width * scale)), int(math.ceil(art.height * scale))), Image.LANCZOS)
        left, top = (art.width - FULL_W) // 2, (art.height - FULL_H) // 2
        out = art.crop((left, top, left + FULL_W, top + FULL_H))
    else:
        # fit-trim: nothing is ever cut; bleed is synthesised from edge strips only.
        scale = min(TRIM_W / art.width, TRIM_H / art.height)
        fitted = art.resize((int(round(art.width * scale)), int(round(art.height * scale))), Image.LANCZOS)
        arr = np.asarray(fitted)
        pad_x = (FULL_W - fitted.width) // 2
        pad_y = (FULL_H - fitted.height) // 2
        pad = ((pad_y, FULL_H - fitted.height - pad_y), (pad_x, FULL_W - fitted.width - pad_x), (0, 0))
        out = Image.fromarray(np.pad(arr, pad, mode="symmetric"))

    dst = OUT_PRINT / f"{src.stem}_fullbleed.png"
    save_print(out, dst)
    print(f"prep ({args.strategy}) {dst.relative_to(ROOT)}  ({out.width}x{out.height})")
    return 0


def cmd_proof(args) -> int:
    Image, ImageDraw, _, _, _ = _imaging()
    src = Path(args.inp)
    if not src.exists():
        raise PipelineError(f"missing input: {src}")
    img = Image.open(src).convert("RGB")
    if (img.width, img.height) != (FULL_W, FULL_H):
        raise PipelineError(f"{src.name} is {img.width}x{img.height}, expected {FULL_W}x{FULL_H}")
    draw = ImageDraw.Draw(img)
    draw.rectangle((TRIM_BOX[0], TRIM_BOX[1], TRIM_BOX[2] - 1, TRIM_BOX[3] - 1), outline=(255, 0, 0), width=4)
    x0, y0, x1, y1 = SAFE_BOX
    dash = 26
    for x in range(x0, x1, dash * 2):
        draw.line((x, y0, min(x + dash, x1), y0), fill=(0, 132, 255), width=4)
        draw.line((x, y1, min(x + dash, x1), y1), fill=(0, 132, 255), width=4)
    for y in range(y0, y1, dash * 2):
        draw.line((x0, y, x0, min(y + dash, y1)), fill=(0, 132, 255), width=4)
        draw.line((x1, y, x1, min(y + dash, y1)), fill=(0, 132, 255), width=4)
    dst = OUT_PROOF / f"{src.stem}_proof.png"
    save_print(img, dst)
    print(f"proof {dst.relative_to(ROOT)}")
    return 0


def cmd_foil(args) -> int:
    Image, _, _, _, np = _imaging()
    src = Path(args.inp)
    if not src.exists():
        raise PipelineError(f"missing input: {src}")
    img = Image.open(src).convert("RGB")
    hsv = np.asarray(img.convert("HSV")).astype(np.int16)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gold = ((h >= 18) & (h <= 46) & (s >= 70) & (v >= 110))
    silver = (s <= 42) & (v >= 168)
    mask = np.where(gold | silver, 255, 0).astype("uint8")
    dst = OUT_FOIL / f"{src.stem}_foil.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask, "L").convert("1").save(dst, "PNG", dpi=(DPI, DPI))
    coverage = float(mask.mean()) / 255.0
    print(f"foil {dst.relative_to(ROOT)}  coverage={coverage:.1%}")
    return 0


# ---------------------------------------------------------------------------
# sheet
# ---------------------------------------------------------------------------

def cmd_sheet(_args) -> int:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas as pdfcanvas
    except ModuleNotFoundError as exc:
        raise PipelineError("reportlab is required for `sheet` (pip install -r requirements.txt)") from exc

    files = sorted(OUT_PRINT.glob("*_front_*.png")) + sorted(OUT_PRINT.glob("*_back_*.png"))
    if not files:
        raise PipelineError("no composed cards in output/print — run `compose` first")

    OUT_PDF.mkdir(parents=True, exist_ok=True)
    singles = OUT_PDF / "GenesisSeries_PRINT_singles.pdf"
    page_w, page_h = (TRIM_W_IN + 2 * BLEED_IN) * inch, (TRIM_H_IN + 2 * BLEED_IN) * inch
    pdf = pdfcanvas.Canvas(str(singles), pagesize=(page_w, page_h))
    for path in files:
        pdf.drawImage(str(path), 0, 0, width=page_w, height=page_h)
        pdf.showPage()
    pdf.save()
    print(f"sheet {singles.relative_to(ROOT)}  ({len(files)} pages @ {page_w/inch:.3f}x{page_h/inch:.3f}in)")

    proof_sheet = OUT_PDF / "GenesisSeries_PROOF_letter.pdf"
    pdf = pdfcanvas.Canvas(str(proof_sheet), pagesize=letter)
    cols, rows = 3, 3
    cw, ch = TRIM_W_IN * inch, TRIM_H_IN * inch
    mx = (letter[0] - cols * cw) / (cols + 1)
    my = (letter[1] - rows * ch) / (rows + 1)
    for i, path in enumerate(files):
        slot = i % (cols * rows)
        if slot == 0 and i:
            pdf.showPage()
        col, row = slot % cols, slot // cols
        x = mx + col * (cw + mx)
        y = letter[1] - my - ch - row * (ch + my)
        pdf.drawImage(str(path), x, y, width=cw, height=ch)
    pdf.showPage()
    pdf.save()
    print(f"sheet {proof_sheet.relative_to(ROOT)}")
    return 0


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------

def cmd_all(args) -> int:
    manifest = load_manifest()
    ready = [c for c in manifest["cards"]
             if STATUSES.index(c["status"]) >= STATUSES.index("art_approved")]
    if not ready:
        raise PipelineError("no cards at status 'art_approved' or beyond")
    serial = args.serial or 1
    for card in ready:
        base = f"{card['num']:03d}_{slug(card['name'])}"
        front = save_print(compose_front(card, serial), OUT_PRINT / f"{base}_front_{serial:03d}.png")
        back = save_print(compose_back(card, serial), OUT_PRINT / f"{base}_back_{serial:03d}.png")
        for path in (front, back):
            cmd_proof(argparse.Namespace(inp=str(path)))
            cmd_foil(argparse.Namespace(inp=str(path)))
        print(f"card {card['num']:03d} {card['name']}: composed + proofed + foil")
    cmd_sheet(args)
    return 0


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="generate.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate"); p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("compose")
    p.add_argument("--card", type=int, required=True)
    p.add_argument("--serial", type=int)
    p.add_argument("--all-serials", action="store_true")
    p.add_argument("--face", choices=("front", "back", "both"), default="both")
    p.set_defaults(fn=cmd_compose)

    p = sub.add_parser("prep")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--strategy", choices=("fit-trim", "cover"), default="fit-trim")
    p.set_defaults(fn=cmd_prep)

    p = sub.add_parser("proof")
    p.add_argument("--in", dest="inp", required=True)
    p.set_defaults(fn=cmd_proof)

    p = sub.add_parser("foil")
    p.add_argument("--in", dest="inp", required=True)
    p.set_defaults(fn=cmd_foil)

    p = sub.add_parser("sheet"); p.set_defaults(fn=cmd_sheet)

    p = sub.add_parser("all")
    p.add_argument("--serial", type=int)
    p.set_defaults(fn=cmd_all)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except PipelineError as exc:
        print(f"FATAL {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

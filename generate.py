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

BORDER_BAND = 118      # holo/foil border thickness from the full-bleed edge
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


# ---------------------------------------------------------------------------
# Frame construction
# ---------------------------------------------------------------------------

def holo_tint(np, w: int, h: int, seed: int, strength: float):
    """Opalescent sheen as a narrow-gamut per-channel gain around 1.0.

    Deliberately NOT a full-spectrum rainbow: a collector foil shifts through
    warm gold / green-gold / rose within a tight band. Amplitude is capped so
    the engraved metal stays dominant and the card never reads as candy stripe.
    """
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / max(w - 1, 1), yy / max(h - 1, 1)
    rng = np.random.default_rng(seed)
    phase = rng.uniform(0.0, 2.0 * math.pi)
    band = u * 2.6 + v * 3.4
    swirl = np.sin((u - 0.5) * (v - 0.5) * 9.0)
    wave = np.sin(band * 2.0 * math.pi + phase + swirl * 1.1)
    amp = 0.16 * float(strength)          # <= 16% channel deviation
    gain = np.empty((h, w, 3), dtype=np.float32)
    gain[..., 0] = 1.0 + amp * wave                     # red leads
    gain[..., 1] = 1.0 + amp * np.sin(band * 2.0 * math.pi + phase + 2.2 + swirl * 1.1) * 0.75
    gain[..., 2] = 1.0 + amp * np.sin(band * 2.0 * math.pi + phase + 4.3 + swirl * 1.1) * 0.55
    return gain


def build_border(Image, ImageDraw, ImageFilter, np, style: dict, seed: int, mythic: bool):
    """Ornate engraved border occupying the outer band (bleed is border only)."""
    foil = np.array(style["foil"], dtype=np.float32)
    dark = np.array(style["dark"], dtype=np.float32)

    yy, xx = np.mgrid[0:FULL_H, 0:FULL_W].astype(np.float32)
    edge = np.minimum(np.minimum(xx, FULL_W - 1 - xx), np.minimum(yy, FULL_H - 1 - yy))
    t = np.clip(edge / float(BORDER_BAND), 0.0, 1.0)

    # Engraved ribbing across the band plus a brushed-metal grain.
    rib = 0.5 + 0.5 * np.sin(t * math.pi * 6.0)
    rng = np.random.default_rng(seed)
    grain = rng.normal(0.0, 1.0, size=(FULL_H, FULL_W)).astype(np.float32) * 0.05

    shade = (0.34 + 0.62 * rib + grain)[..., None]
    band = dark + (foil - dark) * np.clip(shade, 0.0, 1.35)

    # Darken toward the inner edge so the frame reads as a raised bevel.
    bevel = (0.72 + 0.28 * (1.0 - t))[..., None]
    band = band * bevel

    band = band * holo_tint(np, FULL_W, FULL_H, seed, style["holo"])

    img = Image.fromarray(np.clip(band, 0, 255).astype("uint8"), "RGB")
    img = img.filter(ImageFilter.GaussianBlur(0.6))

    # Ornament: corner rosettes and a repeating engraved motif along the band.
    draw = ImageDraw.Draw(img, "RGBA")
    accent = style["accent"]
    rnd = random.Random(seed)
    for cx, cy in ((BORDER_BAND, BORDER_BAND), (FULL_W - BORDER_BAND, BORDER_BAND),
                   (BORDER_BAND, FULL_H - BORDER_BAND), (FULL_W - BORDER_BAND, FULL_H - BORDER_BAND)):
        for r in (86, 64, 42, 22):
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=accent + (70,), width=3)
        for k in range(12):
            ang = k * math.pi / 6.0 + rnd.random() * 0.05
            draw.line((cx, cy, cx + math.cos(ang) * 78, cy + math.sin(ang) * 78),
                      fill=accent + (46,), width=2)

    step = 96
    for x in range(BORDER_BAND + step, FULL_W - BORDER_BAND, step):
        for y in (BORDER_BAND // 2, FULL_H - BORDER_BAND // 2):
            draw.ellipse((x - 11, y - 11, x + 11, y + 11), outline=accent + (58,), width=2)
    for y in range(BORDER_BAND + step, FULL_H - BORDER_BAND, step):
        for x in (BORDER_BAND // 2, FULL_W - BORDER_BAND // 2):
            draw.ellipse((x - 11, y - 11, x + 11, y + 11), outline=accent + (58,), width=2)
    return img


def place_art(Image, art_path: Path, box: tuple[int, int, int, int]):
    """Cover-fit finished art into the card's art window."""
    if not art_path.exists():
        raise PipelineError(f"missing art file: {art_path}")
    art = Image.open(art_path).convert("RGB")
    bw, bh = box[2] - box[0], box[3] - box[1]
    scale = max(bw / art.width, bh / art.height)
    new = (max(int(math.ceil(art.width * scale)), bw), max(int(math.ceil(art.height * scale)), bh))
    art = art.resize(new, Image.LANCZOS)
    left = (art.width - bw) // 2
    top = int((art.height - bh) * 0.34)   # bias upward: subjects sit high in frame
    top = max(0, min(top, art.height - bh))
    return art.crop((left, top, left + bw, top + bh))


def panel(Image, ImageDraw, size, radius=18, alpha=214):
    plate = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius,
                                            fill=PANEL + (alpha,))
    return plate


def hairline(draw, box, color, width=3, radius=14):
    draw.rounded_rectangle(box, radius=radius, outline=color, width=width)


def kind_mark(draw, cx, cy, kind: str, style):
    """Ability marker drawn as geometry — the display faces have no such glyphs."""
    r = 13
    if kind == "active":                       # filled diamond
        draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=style["accent"])
    elif kind == "passive":                    # open ring
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=style["accent"], width=4)
    else:                                      # ultimate: eight-point star
        pts = []
        for i in range(16):
            ang = -math.pi / 2 + i * math.pi / 8
            rad = r if i % 2 == 0 else r * 0.42
            pts.append((cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
        draw.polygon(pts, fill=style["accent"])


def draw_sigil(draw, cx, cy, r, style):
    """Engraved set sigil — fills the lower back panel instead of dead space."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=style["dark"], width=5)
    draw.ellipse((cx - r * 0.82, cy - r * 0.82, cx + r * 0.82, cy + r * 0.82),
                 outline=style["dark"], width=2)
    for i in range(24):
        ang = i * math.pi / 12
        x0, y0 = cx + math.cos(ang) * r * 0.82, cy + math.sin(ang) * r * 0.82
        x1, y1 = cx + math.cos(ang) * r * 0.62, cy + math.sin(ang) * r * 0.62
        draw.line((x0, y0, x1, y1), fill=style["dark"], width=3)
    for i in range(6):
        ang = -math.pi / 2 + i * math.pi / 3
        x0, y0 = cx + math.cos(ang) * r * 0.52, cy + math.sin(ang) * r * 0.52
        draw.line((cx, cy, x0, y0), fill=style["dark"], width=3)
        draw.ellipse((x0 - 9, y0 - 9, x0 + 9, y0 + 9), outline=style["dark"], width=3)
    draw.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), fill=style["dark"])


def rarity_crest(draw, cx, cy, r, style, rarity):
    pips = {"mythic": 5, "epic": 4, "rare": 3, "uncommon": 2, "common": 1}[rarity]
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=style["foil"], width=4)
    draw.ellipse((cx - r + 9, cy - r + 9, cx + r - 9, cy + r - 9), outline=style["dark"], width=2)
    for i in range(pips):
        ang = -math.pi / 2 + i * (2 * math.pi / max(pips, 1))
        px, py = cx + math.cos(ang) * (r * 0.52), cy + math.sin(ang) * (r * 0.52)
        draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=style["accent"])


# ---------------------------------------------------------------------------
# compose
# ---------------------------------------------------------------------------

def compose_front(card: dict, serial: int) -> "object":
    Image, ImageDraw, ImageFilter, ImageFont, np = _imaging()
    rarity = card["rarity"]
    style = TIER_STYLE[rarity]
    seed = card["num"]

    canvas = build_border(Image, ImageDraw, ImageFilter, np, style, seed, rarity == "mythic")

    inner = (BORDER_BAND, BORDER_BAND, FULL_W - BORDER_BAND, FULL_H - BORDER_BAND)
    canvas.paste(Image.new("RGB", (inner[2] - inner[0], inner[3] - inner[1]), (9, 8, 8)), inner[:2])

    art_box = (BORDER_BAND + 34, BORDER_BAND + 214, FULL_W - BORDER_BAND - 34, BORDER_BAND + 1214)
    canvas.paste(place_art(Image, ROOT / require(card, "art_front"), art_box), art_box[:2])

    canvas = canvas.convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")

    # Vignette under the lower text stack so type always reads.
    fade = Image.new("RGBA", (art_box[2] - art_box[0], 260), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    for i in range(260):
        fd.line((0, i, fade.width, i), fill=(6, 5, 5, int(226 * (i / 259) ** 1.5)))
    canvas.alpha_composite(fade, (art_box[0], art_box[3] - 260))

    draw.rectangle(inner, outline=style["foil"], width=INNER_RULE // 2)

    cx = FULL_W // 2
    safe_l, safe_r = SAFE_BOX[0], SAFE_BOX[2]
    inner_w = safe_r - safe_l

    # --- title plate -------------------------------------------------------
    name = require(card, "name").upper()
    title_face = fit_font(draw, name, "display", inner_w - 60, 92, 46)
    draw_tracked(draw, (0, BORDER_BAND + 40), name, title_face, style["accent"], tracking=4, anchor_center_x=cx)

    sub_face = fit_font(draw, require(card, "subtitle"), "italic", inner_w - 120, 44, 26, weight=500)
    sw = draw.textlength(card["subtitle"], font=sub_face)
    draw.text((cx - sw / 2, BORDER_BAND + 150), card["subtitle"], font=sub_face, fill=INK_DIM)

    rule_y = BORDER_BAND + 202
    draw.line((safe_l + 40, rule_y, safe_r - 40, rule_y), fill=style["dark"], width=3)

    # --- type line ---------------------------------------------------------
    type_line = f"{require(card, 'type')}  •  {require(card, 'alignment')}"
    tl_face = font("caps", 40, 600)
    ty = art_box[3] + 26
    draw_tracked(draw, (0, ty), type_line.upper(), tl_face, style["accent"], tracking=6, anchor_center_x=cx)
    rarity_crest(draw, safe_r - 44, ty + 22, 40, style, rarity)

    # --- stats -------------------------------------------------------------
    stats = require(card, "stats")
    sy = ty + 96
    stat_h = 52
    plate_h = stat_h * len(stats) + 30
    canvas.alpha_composite(panel(Image, ImageDraw, (inner_w, plate_h)), (safe_l, sy))
    hairline(draw, (safe_l, sy, safe_l + inner_w, sy + plate_h), style["dark"], 3)

    label_face = font("caps", 32, 600)
    value_face = font("caps", 34, 700)
    bar_l = safe_l + 430
    bar_r = safe_l + inner_w - 130
    for i, stat in enumerate(stats):
        row_y = sy + 21 + i * stat_h
        draw.text((safe_l + 26, row_y), stat["label"].upper(), font=label_face, fill=INK_DIM)
        value = stat["value"]
        draw.line((bar_l, row_y + 20, bar_r, row_y + 20), fill=(46, 40, 34), width=12)
        filled = bar_l + int(round((bar_r - bar_l) * value / 100.0))
        if filled > bar_l:
            draw.line((bar_l, row_y + 20, filled, row_y + 20), fill=style["foil"], width=12)
        vt = f"{value:3d}"
        draw.text((safe_l + inner_w - 112, row_y - 2), vt, font=value_face, fill=style["accent"])

    # --- abilities ---------------------------------------------------------
    abilities = sorted(require(card, "abilities"), key=lambda a: ABILITY_KINDS.index(a["kind"]))
    ay = sy + plate_h + 24
    ab_bottom = SAFE_BOX[3] - 88
    avail = ab_bottom - ay

    # Fit before drawing: shrink type until the block provably clears the footer.
    text_l = safe_l + 66
    text_w = inner_w - 92
    for name_pt, body_pt in ((34, 32), (32, 30), (30, 28), (28, 26), (26, 24)):
        name_face = font("caps", name_pt, 700)
        body_face = font("body", body_pt, 450)
        head_h, line_h, gap = name_pt + 12, body_pt + 5, 14
        block = []
        total = 24
        for ability in abilities:
            lines = wrap(draw, ability["text"], body_face, text_w)
            block.append((ability, lines))
            total += head_h + len(lines) * line_h + gap
        if total <= avail:
            break

    canvas.alpha_composite(panel(Image, ImageDraw, (inner_w, avail)), (safe_l, ay))
    hairline(draw, (safe_l, ay, safe_l + inner_w, ab_bottom), style["dark"], 3)

    y = ay + 16
    for ability, lines in block:
        kind_mark(draw, safe_l + 36, y + name_pt // 2 + 2, ability["kind"], style)
        draw.text((text_l, y), ability["name"].upper(), font=name_face, fill=style["accent"])
        y += head_h
        for line in lines:
            draw.text((text_l, y), line, font=body_face, fill=INK)
            y += line_h
        y += gap

    # --- footer ------------------------------------------------------------
    foot_y = SAFE_BOX[3] - 62
    foot_face = font("caps", 34, 600)
    draw.text((safe_l + 4, foot_y), f"{card['num']:03d}/100", font=foot_face, fill=INK_DIM)
    code = load_manifest()["set"]["code"]
    draw_tracked(draw, (0, foot_y), code, font("caps", 30, 500), INK_DIM, tracking=5, anchor_center_x=cx)
    serial_text = f"{serial:03d}/100"
    sw = draw.textlength(serial_text, font=foot_face)
    draw.text((safe_r - sw - 4, foot_y), serial_text, font=foot_face, fill=style["serial"])

    return canvas.convert("RGB")


def compose_back(card: dict, serial: int) -> "object":
    Image, ImageDraw, ImageFilter, ImageFont, np = _imaging()
    rarity = card["rarity"]
    style = TIER_STYLE[rarity]
    seed = card["num"] + 1000

    canvas = build_border(Image, ImageDraw, ImageFilter, np, style, seed, rarity == "mythic")
    inner = (BORDER_BAND, BORDER_BAND, FULL_W - BORDER_BAND, FULL_H - BORDER_BAND)
    canvas.paste(Image.new("RGB", (inner[2] - inner[0], inner[3] - inner[1]), (11, 9, 9)), inner[:2])
    canvas = canvas.convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rectangle(inner, outline=style["foil"], width=INNER_RULE // 2)

    cx = FULL_W // 2
    safe_l, safe_r = SAFE_BOX[0], SAFE_BOX[2]
    inner_w = safe_r - safe_l

    name = require(card, "name").upper()
    title_face = fit_font(draw, name, "display", inner_w - 80, 70, 38)
    draw_tracked(draw, (0, BORDER_BAND + 40), name, title_face, style["accent"], tracking=3, anchor_center_x=cx)
    rule_y = BORDER_BAND + 132
    draw.line((safe_l + 60, rule_y, safe_r - 60, rule_y), fill=style["dark"], width=3)

    y = rule_y + 34

    # --- lore --------------------------------------------------------------
    lore_face = font("body", 36, 450)
    lore_lines = wrap(draw, require(card, "lore"), lore_face, inner_w - 68)
    lore_h = len(lore_lines) * 44 + 40
    canvas.alpha_composite(panel(Image, ImageDraw, (inner_w, lore_h), alpha=196), (safe_l, y))
    hairline(draw, (safe_l, y, safe_l + inner_w, y + lore_h), style["dark"], 3)
    ly = y + 22
    for line in lore_lines:
        draw.text((safe_l + 32, ly), line, font=lore_face, fill=INK)
        ly += 44
    y += lore_h + 28

    # --- traits ------------------------------------------------------------
    traits = require(card, "traits")
    head_face = font("caps", 30, 700)
    trait_face = font("body", 31, 450)
    # Column width is measured from the widest label, so labels can never
    # collide with their text however they are worded.
    label_w = max(draw.textlength(t["label"].upper(), font=head_face) for t in traits)
    col = int(label_w) + 44
    trait_rows = [(t["label"].upper(), wrap(draw, t["text"], trait_face, inner_w - col - 56))
                  for t in traits]
    row_h = 42
    traits_h = sum(row_h + max(len(r[1]) - 1, 0) * 36 for r in trait_rows) + 82

    canvas.alpha_composite(panel(Image, ImageDraw, (inner_w, traits_h), alpha=196), (safe_l, y))
    hairline(draw, (safe_l, y, safe_l + inner_w, y + traits_h), style["dark"], 3)
    draw_tracked(draw, (safe_l + 30, y + 18), "TRAITS", font("caps", 30, 700), style["accent"], tracking=7)
    ty = y + 66
    for label, lines in trait_rows:
        draw.text((safe_l + 30, ty), label, font=head_face, fill=style["serial"])
        for j, line in enumerate(lines):
            draw.text((safe_l + col, ty + j * 36), line, font=trait_face, fill=INK)
        ty += row_h + max(len(lines) - 1, 0) * 36
    y += traits_h + 26

    # --- weakness ----------------------------------------------------------
    weak_face = font("body", 32, 500)
    weak_lines = wrap(draw, require(card, "weakness"), weak_face, inner_w - 300)
    weak_h = 30 + len(weak_lines) * 38 + 26
    canvas.alpha_composite(panel(Image, ImageDraw, (inner_w, weak_h), alpha=196), (safe_l, y))
    hairline(draw, (safe_l, y, safe_l + inner_w, y + weak_h), style["dark"], 3)
    draw_tracked(draw, (safe_l + 30, y + 20), "WEAKNESS", font("caps", 30, 700), style["accent"], tracking=7)
    for j, line in enumerate(weak_lines):
        draw.text((safe_l + 300, y + 18 + j * 38), line, font=weak_face, fill=INK)
    y += weak_h + 30

    # --- flavor ------------------------------------------------------------
    flavor = f"“{require(card, 'flavor')}”"
    fl_face = fit_font(draw, flavor, "italic", inner_w - 120, 40, 26, weight=500)
    fw = draw.textlength(flavor, font=fl_face)
    draw.text((cx - fw / 2, y + 10), flavor, font=fl_face, fill=style["serial"])
    y += 74

    # --- set sigil fills the remaining panel rather than leaving it dead ----
    foot_top = SAFE_BOX[3] - 96
    room = foot_top - y
    if room > 180:
        radius = min(int(room * 0.42), 260)
        draw_sigil(draw, cx, y + room // 2, radius, style)

    # --- footer ------------------------------------------------------------
    foot_y = SAFE_BOX[3] - 62
    foot_face = font("caps", 34, 600)
    manifest = load_manifest()
    draw.text((safe_l + 4, foot_y), f"{card['num']:03d}/100", font=foot_face, fill=INK_DIM)
    draw_tracked(draw, (0, foot_y), manifest["set"]["series"].upper(), font("caps", 28, 500),
                 INK_DIM, tracking=4, anchor_center_x=cx)
    serial_text = f"{serial:03d}/100"
    sw = draw.textlength(serial_text, font=foot_face)
    draw.text((safe_r - sw - 4, foot_y), serial_text, font=foot_face, fill=style["serial"])

    return canvas.convert("RGB")


def save_print(img, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", dpi=(DPI, DPI))
    return path


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


# ---------------------------------------------------------------------------
# prep / proof / foil
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

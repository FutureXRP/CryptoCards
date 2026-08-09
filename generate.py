#!/usr/bin/env python3
"""
Crypto Lore Series - Genesis Series 2026
=======================================
Deterministic production pipeline: manifest -> print-ready card files.

Hard rules this file implements (see CLAUDE.md):

* ALL geometry, scaling, bleed math, serial numbering, layout coordinates,
  foil masks and PDFs are computed here, never by a language model.
* Print geometry is integer arithmetic derived once from inches x DPI.
  Floats are rounded exactly once, never accumulated.
* Any randomness (texture jitter, engraving noise) uses a seeded RNG keyed by
  the card number / rarity, so re-runs are byte-reproducible.
* Fabrication firewall: nothing is invented at render time. Every string on a
  card comes from set_manifest.json or from the SET-level constants below.
  A missing manifest field is a loud failure, never a silent default.

CLI contract
------------
    python generate.py compose  --card N [--serial S | --all-serials]
    python generate.py prep     --in PATH [--strategy fit-trim|cover]
    python generate.py proof    --in PATH
    python generate.py foil     --in PATH
    python generate.py sheet
    python generate.py all
    python generate.py validate

Supporting commands (asset synthesis / bookkeeping):

    python generate.py templates            # build art/templates/* frames
    python generate.py artgen  --card N      # deterministic placeholder art
    python generate.py prompts [--card N]    # manifest -> prompts/cards/*.md
    python generate.py status                # rewrite STATUS.md from manifest
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------
# 0. Paths
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "set_manifest.json"
FONT_DIR = ROOT / "fonts"

ART_APPROVED = ROOT / "art" / "approved"
ART_INCOMING = ROOT / "art" / "incoming"
ART_TEMPLATES = ROOT / "art" / "templates"
ART_PLACEHOLDER = ROOT / "art" / "placeholder"

PROMPT_DIR = ROOT / "prompts"
PROMPT_CARD_DIR = PROMPT_DIR / "cards"

OUT_PRINT = ROOT / "output" / "print"
OUT_PROOF = ROOT / "output" / "proofs"
OUT_FOIL = ROOT / "output" / "foil"
OUT_PDF = ROOT / "output" / "pdf"

SET_NAME = "GENESIS SERIES 2026"
SET_MARK = "CRYPTO LORE SERIES"
EDITION_TOTAL = 100


class PipelineError(RuntimeError):
    """Any condition that must stop the pipeline loudly."""


# --------------------------------------------------------------------------
# 1. Print geometry - integer pixels, computed once
# --------------------------------------------------------------------------

DPI = 600
TRIM_W_IN, TRIM_H_IN = 2.5, 3.5
BLEED_IN = 0.125
SAFE_IN = 0.125


def _px(inches: float) -> int:
    """inches -> device pixels, rounded exactly once."""
    return int(round(inches * DPI))


TRIM_W, TRIM_H = _px(TRIM_W_IN), _px(TRIM_H_IN)          # 1500 x 2100
BLEED = _px(BLEED_IN)                                    # 75
SAFE = _px(SAFE_IN)                                      # 75
FULL_W, FULL_H = TRIM_W + 2 * BLEED, TRIM_H + 2 * BLEED  # 1650 x 2250

TRIM_BOX = (BLEED, BLEED, BLEED + TRIM_W, BLEED + TRIM_H)
SAFE_BOX = (BLEED + SAFE, BLEED + SAFE, FULL_W - BLEED - SAFE, FULL_H - BLEED - SAFE)

EDGE_STRIP = 10          # px of art edge that may be mirrored into the bleed
BAND_INSET = 28          # frame band reaches this far past the trim line
BAND_RADIUS = 34

PT_PER_IN = 72.0
PROOF_SHEET_DPI = 200   # the letter proof sheet is for reading, not for printing

# --------------------------------------------------------------------------
# 2. Rarity tiers - the tier table from CLAUDE.md, encoded once
# --------------------------------------------------------------------------

MYTHIC_NUMBERS = (1, 2, 3, 4, 100)

RARITIES = ("mythic", "epic", "rare", "uncommon", "common")

TIER_COUNTS = {"mythic": 5, "epic": 10, "rare": 20, "uncommon": 30, "common": 35}

TIER_FINISH = {
    "mythic": "Rainbow holo border, textured gold foil, embossed sigil, gold serial",
    "epic": "Full holo overlay + gold foil title/icons",
    "rare": "Gold foil title + foil rarity crest",
    "uncommon": "Silver foil title + standard holo border",
    "common": "Standard print + holo border",
}


def rarity_for_number(num: int) -> str:
    if num in MYTHIC_NUMBERS:
        return "mythic"
    if 5 <= num <= 14:
        return "epic"
    if 15 <= num <= 34:
        return "rare"
    if 35 <= num <= 64:
        return "uncommon"
    if 65 <= num <= 99:
        return "common"
    raise PipelineError(f"card number out of range: {num}")


# Palette ------------------------------------------------------------------

GOLD = (198, 158, 74)
GOLD_HI = (245, 222, 160)
GOLD_LO = (120, 88, 40)
SILVER = (186, 192, 200)
SILVER_HI = (232, 236, 242)
PARCHMENT = (223, 209, 181)
INK = (14, 12, 10)
BONE = (208, 198, 178)

TIER_STYLE = {
    #                foil colour    holo ring   full sheen  spokes
    "mythic":   {"foil": "gold",   "ring": 1.00, "sheen": 0.16, "spokes": 12, "rainbow": True},
    "epic":     {"foil": "gold",   "ring": 0.72, "sheen": 0.11, "spokes": 10, "rainbow": False},
    "rare":     {"foil": "gold",   "ring": 0.46, "sheen": 0.00, "spokes": 8, "rainbow": False},
    "uncommon": {"foil": "silver", "ring": 0.34, "sheen": 0.00, "spokes": 6, "rainbow": False},
    "common":   {"foil": None,     "ring": 0.24, "sheen": 0.00, "spokes": 4, "rainbow": False},
}


def foil_ink(rarity: str) -> tuple[int, int, int]:
    return GOLD if TIER_STYLE[rarity]["foil"] != "silver" else SILVER


def foil_ink_hi(rarity: str) -> tuple[int, int, int]:
    return GOLD_HI if TIER_STYLE[rarity]["foil"] != "silver" else SILVER_HI


# --------------------------------------------------------------------------
# 3. Layout boxes - shared by template synthesis and typesetting
# --------------------------------------------------------------------------

FRONT_BOXES = {
    "title": (150, 150, 1500, 400),
    "ribbon": (330, 412, 1320, 486),
    "art_window": (150, 500, 1500, 1170),   # left clear, art shows through
    "stats": (150, 1186, 1500, 1606),
    "abilities": (150, 1622, 1500, 2004),
    "weakness": (150, 2010, 1500, 2052),
    "footer": (150, 2058, 1500, 2100),
}

BACK_BOXES = {
    "title": (150, 150, 1500, 330),
    "lore": (150, 346, 1500, 900),
    "traits": (150, 916, 1500, 1640),
    "flavor": (150, 1656, 1500, 1800),
    "sigil": (150, 1816, 1500, 2040),
    "footer": (150, 2050, 1500, 2100),
}

PAD = 26  # inner padding used inside every plate


def _assert_layout_inside_safe() -> None:
    x0, y0, x1, y1 = SAFE_BOX
    for name, boxes in (("front", FRONT_BOXES), ("back", BACK_BOXES)):
        for key, (a, b, c, d) in boxes.items():
            if a < x0 or b < y0 or c > x1 or d > y1:
                raise PipelineError(
                    f"layout box {name}.{key}={(a, b, c, d)} escapes safe zone {SAFE_BOX}"
                )


_assert_layout_inside_safe()


# --------------------------------------------------------------------------
# 4. Fonts
# --------------------------------------------------------------------------

FONT_FILES = {
    "display": FONT_DIR / "Cinzel[wght].ttf",
    "deco": FONT_DIR / "CinzelDecorative-Regular.ttf",
    "deco_bold": FONT_DIR / "CinzelDecorative-Bold.ttf",
    "deco_black": FONT_DIR / "CinzelDecorative-Black.ttf",
    "body": FONT_DIR / "EBGaramond[wght].ttf",
    "body_italic": FONT_DIR / "EBGaramond-Italic[wght].ttf",
}

_FONT_CACHE: dict[tuple, ImageFont.FreeTypeFont] = {}


def get_font(key: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    """Load a project font. Missing font files are a loud failure."""
    ck = (key, size, weight)
    if ck in _FONT_CACHE:
        return _FONT_CACHE[ck]
    path = FONT_FILES.get(key)
    if path is None:
        raise PipelineError(f"unknown font key {key!r}")
    if not path.exists():
        raise PipelineError(
            f"missing font {path}. The set must render in its own typefaces; "
            "see fonts/README.md for the exact files required."
        )
    font = ImageFont.truetype(str(path), size)
    if weight is not None:
        try:
            font.set_variation_by_axes([float(weight)])
        except OSError:
            pass  # static face, weight already baked in
    _FONT_CACHE[ck] = font
    return font


def text_width(font: ImageFont.FreeTypeFont, text: str, tracking: float = 0.0) -> float:
    if not text:
        return 0.0
    return sum(font.getlength(c) for c in text) + tracking * (len(text) - 1)


def fit_font(key: str, text: str, max_w: int, size_max: int, size_min: int,
             weight: int | None = None, track: float = 0.0) -> ImageFont.FreeTypeFont:
    """Largest size in [size_min, size_max] whose single line fits max_w."""
    for size in range(size_max, size_min - 1, -1):
        font = get_font(key, size, weight)
        if text_width(font, text, track * size) <= max_w:
            return font
    raise PipelineError(
        f"text does not fit at minimum size {size_min}px in {max_w}px: {text!r}"
    )


def wrap_text(font: ImageFont.FreeTypeFont, text: str, max_w: int,
              tracking: float = 0.0) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or text_width(font, trial, tracking) <= max_w:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_wrapped(key: str, text: str, max_w: int, max_h: int, size_max: int,
                size_min: int, leading: float = 1.32,
                weight: int | None = None) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Largest size whose wrapped block fits (max_w, max_h). Loud on failure."""
    for size in range(size_max, size_min - 1, -1):
        font = get_font(key, size, weight)
        lines = wrap_text(font, text, max_w)
        line_h = int(round(size * leading))
        if line_h * len(lines) <= max_h:
            return font, lines, line_h
    raise PipelineError(
        f"paragraph does not fit {max_w}x{max_h}px at minimum size {size_min}px: {text[:60]!r}..."
    )


# --------------------------------------------------------------------------
# 5. Painter - draws to the card and to the spot-foil masks at the same time
# --------------------------------------------------------------------------

class Painter:
    """RGBA card surface plus one 8-bit separation per foil colour."""

    def __init__(self, image: Image.Image):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        self.img = image
        self.draw = ImageDraw.Draw(self.img, "RGBA")
        self.foil = {c: Image.new("L", image.size, 0) for c in ("gold", "silver")}
        self.foil_draw = {c: ImageDraw.Draw(v) for c, v in self.foil.items()}

    # -- text ------------------------------------------------------------
    def text(self, xy, text, font, fill, anchor="la", tracking: float = 0.0,
             foil: str | None = None) -> None:
        """Draw text; optionally stamp the same glyphs into a foil separation."""
        if not text:
            return
        x, y = xy
        if tracking:
            width = text_width(font, text, tracking)
            if anchor[0] == "m":
                x -= width / 2.0
            elif anchor[0] == "r":
                x -= width
            cursor = x
            for ch in text:
                self.draw.text((cursor, y), ch, font=font, fill=fill, anchor="l" + anchor[1])
                if foil:
                    self.foil_draw[foil].text(
                        (cursor, y), ch, font=font, fill=255, anchor="l" + anchor[1]
                    )
                cursor += font.getlength(ch) + tracking
        else:
            self.draw.text((x, y), text, font=font, fill=fill, anchor=anchor)
            if foil:
                self.foil_draw[foil].text((x, y), text, font=font, fill=255, anchor=anchor)

    # -- shapes ----------------------------------------------------------
    def line(self, xy, fill, width=1, foil: str | None = None) -> None:
        self.draw.line(xy, fill=fill, width=width)
        if foil:
            self.foil_draw[foil].line(xy, fill=255, width=width)

    def rect(self, box, fill=None, outline=None, width=1, radius=0,
             foil: str | None = None) -> None:
        if radius:
            self.draw.rounded_rectangle(box, radius=radius, fill=fill,
                                        outline=outline, width=width)
        else:
            self.draw.rectangle(box, fill=fill, outline=outline, width=width)
        if foil:
            d = self.foil_draw[foil]
            f = 255 if fill else None
            o = 255 if outline else None
            if radius:
                d.rounded_rectangle(box, radius=radius, fill=f, outline=o, width=width)
            else:
                d.rectangle(box, fill=f, outline=o, width=width)


# --------------------------------------------------------------------------
# 6. Procedural texture - all seeded, all reproducible
# --------------------------------------------------------------------------

def rng_for(*parts) -> np.random.Generator:
    """Seeded generator: same inputs -> same pixels, every run, every machine."""
    seed = 0
    for part in parts:
        for ch in str(part):
            seed = (seed * 131 + ord(ch)) & 0xFFFFFFFF
    return np.random.default_rng(seed)


def _noise(w: int, h: int, rng: np.random.Generator, octave: int = 8,
           blur: float = 2.0) -> np.ndarray:
    """Smooth [0,1] noise field at (h, w)."""
    small = rng.random((max(2, h // octave), max(2, w // octave))).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(img, dtype=np.float32) / 255.0


def _spectral(w: int, h: int, scale: float = 52.0, phase: float = 0.0) -> np.ndarray:
    """Iridescent rainbow field in [0,1]^3."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (xx * 0.9 + yy * 0.45) / scale + phase
    fine = np.sin((xx - yy) / 11.0) * 0.35
    t = t + fine
    r = 0.5 + 0.5 * np.sin(t)
    g = 0.5 + 0.5 * np.sin(t + 2.0944)
    b = 0.5 + 0.5 * np.sin(t + 4.1888)
    return np.stack([r, g, b], axis=-1).astype(np.float32)


def _edge_falloff(w: int, h: int, depth: int) -> np.ndarray:
    """1.0 at the canvas edge, 0.0 `depth` px inward."""
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.minimum(np.minimum(xx, w - 1 - xx), np.minimum(yy, h - 1 - yy)).astype(np.float32)
    return np.clip(1.0 - d / float(depth), 0.0, 1.0) ** 1.4


def parchment_field(w: int, h: int, seed_key: str) -> np.ndarray:
    """Aged-paper RGB float field in [0,1]."""
    rng = rng_for("parchment", seed_key)
    grain = _noise(w, h, rng, octave=5, blur=1.2)
    blotch = _noise(w, h, rng, octave=26, blur=8.0)
    base = np.array(PARCHMENT, dtype=np.float32) / 255.0
    field = base[None, None, :] * (0.80 + 0.18 * blotch[..., None] + 0.10 * grain[..., None])
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    vig = 1.0 - 0.30 * (((xx / w - 0.5) ** 2 + (yy / h - 0.5) ** 2) * 2.4)
    return np.clip(field * vig[..., None], 0.0, 1.0)


# --------------------------------------------------------------------------
# 7. Frame template synthesis (text-free blanks, one front + back per tier)
# --------------------------------------------------------------------------

def _engraved_band(rarity: str, side: str) -> Image.Image:
    """Opaque ornamental border band, transparent interior. RGBA, full bleed."""
    style = TIER_STYLE[rarity]
    rng = rng_for("band", rarity, side)

    grain = _noise(FULL_W, FULL_H, rng, octave=3, blur=0.9)
    weave = _noise(FULL_W, FULL_H, rng, octave=34, blur=10.0)
    base = np.array((26, 20, 15), dtype=np.float32) / 255.0
    bronze = np.array(GOLD_LO, dtype=np.float32) / 255.0
    field = base[None, None, :] * (0.85 + 0.28 * grain[..., None])
    field = field + bronze[None, None, :] * (0.12 + 0.34 * weave[..., None])

    depth = BLEED + BAND_INSET + 46
    ring = _edge_falloff(FULL_W, FULL_H, depth)
    holo = _spectral(FULL_W, FULL_H, scale=64.0 if style["rainbow"] else 132.0)
    if not style["rainbow"]:
        # Sub-mythic tiers get bronze-dominant iridescence, not a full rainbow.
        luma = holo.mean(axis=-1, keepdims=True)
        holo = luma * (np.array(GOLD, dtype=np.float32) / 255.0) * 1.45 + (holo - luma) * 0.30
    field = field + holo * (ring[..., None] * style["ring"] * 0.55)

    if style["sheen"]:
        field = field + _spectral(FULL_W, FULL_H, scale=210.0, phase=1.1) * style["sheen"]

    rgb = np.clip(field, 0.0, 1.0)
    band = Image.fromarray((rgb * 255).astype(np.uint8), "RGB").convert("RGBA")

    # alpha: opaque band, transparent window
    alpha = Image.new("L", (FULL_W, FULL_H), 255)
    ImageDraw.Draw(alpha).rounded_rectangle(
        (BLEED + BAND_INSET, BLEED + BAND_INSET,
         FULL_W - BLEED - BAND_INSET - 1, FULL_H - BLEED - BAND_INSET - 1),
        radius=BAND_RADIUS, fill=0,
    )
    alpha = alpha.filter(ImageFilter.GaussianBlur(0.6))
    band.putalpha(alpha)
    return band


def _band_lines(painter: Painter, rarity: str) -> None:
    """Gold hairlines and corner filigree on the border band."""
    foil = TIER_STYLE[rarity]["foil"]
    ink = foil_ink(rarity)
    hi = foil_ink_hi(rarity)
    inner = (BLEED + BAND_INSET, BLEED + BAND_INSET,
             FULL_W - BLEED - BAND_INSET - 1, FULL_H - BLEED - BAND_INSET - 1)
    outer = (BLEED - 12, BLEED - 12, FULL_W - BLEED + 11, FULL_H - BLEED + 11)
    # engraved guilloche: concentric hairlines stepping out toward the trim edge
    for step, alpha in ((14, 70), (26, 46), (38, 30), (52, 22)):
        painter.draw.rounded_rectangle(
            (inner[0] - step, inner[1] - step, inner[2] + step, inner[3] + step),
            radius=BAND_RADIUS + step, outline=ink + (alpha,), width=2,
        )
    painter.rect(inner, outline=ink + (255,), width=4, radius=BAND_RADIUS, foil=foil)
    painter.rect((inner[0] + 9, inner[1] + 9, inner[2] - 9, inner[3] - 9),
                 outline=hi + (150,), width=2, radius=BAND_RADIUS - 6, foil=foil)
    painter.rect(outer, outline=ink + (120,), width=3, radius=BAND_RADIUS + 12, foil=foil)

    # corner filigree: nested arcs + a lozenge, mirrored on all four corners
    span = 132
    for cx, cy, sx, sy in (
        (inner[0], inner[1], 1, 1),
        (inner[2], inner[1], -1, 1),
        (inner[0], inner[3], 1, -1),
        (inner[2], inner[3], -1, -1),
    ):
        for k, alpha in ((0, 255), (16, 170), (32, 110)):
            x1, y1 = cx + sx * (span - k), cy + sy * (span - k)
            painter.line([(cx + sx * 6, y1), (x1, cy + sy * 6)], fill=ink + (alpha,),
                         width=3, foil=foil)
        d = 22
        painter.draw.polygon(
            [(cx + sx * 46, cy + sy * 46 - d), (cx + sx * 46 + d, cy + sy * 46),
             (cx + sx * 46, cy + sy * 46 + d), (cx + sx * 46 - d, cy + sy * 46)],
            fill=ink + (235,), outline=hi + (255,),
        )
        if foil:
            painter.foil_draw[foil].polygon(
                [(cx + sx * 46, cy + sy * 46 - d), (cx + sx * 46 + d, cy + sy * 46),
                 (cx + sx * 46, cy + sy * 46 + d), (cx + sx * 46 - d, cy + sy * 46)],
                fill=255,
            )


def draw_emblem(painter: Painter, cx: int, cy: int, r: int, rarity: str,
                filled: bool = True) -> None:
    """The rarity crest / set sigil: spoked rosette with a central lozenge."""
    foil = TIER_STYLE[rarity]["foil"]
    ink = foil_ink(rarity)
    hi = foil_ink_hi(rarity)
    spokes = TIER_STYLE[rarity]["spokes"]
    if filled:
        painter.draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(10, 8, 6, 210))
    painter.draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ink + (255,), width=4)
    painter.draw.ellipse((cx - r + 12, cy - r + 12, cx + r - 12, cy + r - 12),
                         outline=hi + (150,), width=2)
    if foil:
        painter.foil_draw[foil].ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=4)
    for i in range(spokes):
        a = 2 * math.pi * i / spokes
        x0, y0 = cx + math.cos(a) * (r - 16), cy + math.sin(a) * (r - 16)
        x1, y1 = cx + math.cos(a) * (r - 40), cy + math.sin(a) * (r - 40)
        painter.line([(x0, y0), (x1, y1)], fill=ink + (255,), width=3, foil=foil)
    d = max(10, r // 3)
    pts = [(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)]
    painter.draw.polygon(pts, fill=ink + (255,), outline=hi + (255,))
    if foil:
        painter.foil_draw[foil].polygon(pts, fill=255)


def _plate(painter: Painter, box, radius: int = 18, strength: int = 168) -> None:
    """Translucent dark plate: art texture still reads through, type stays legible."""
    painter.rect(box, fill=(15, 12, 9, strength), outline=(150, 116, 52, 200),
                 width=3, radius=radius)
    painter.rect((box[0] + 7, box[1] + 7, box[2] - 7, box[3] - 7),
                 outline=(198, 158, 74, 95), width=1, radius=max(2, radius - 5))
    painter.draw.line([(box[0] + radius, box[1] + 4), (box[2] - radius, box[1] + 4)],
                      fill=(232, 206, 150, 60), width=2)


def build_front_template(rarity: str) -> tuple[Image.Image, dict[str, Image.Image]]:
    band = _engraved_band(rarity, "front")
    painter = Painter(band)
    _band_lines(painter, rarity)
    for key in ("title", "ribbon", "stats", "abilities"):
        _plate(painter, FRONT_BOXES[key])
    _plate(painter, FRONT_BOXES["weakness"], radius=10, strength=150)
    _plate(painter, FRONT_BOXES["footer"], radius=10, strength=170)
    # rarity crest sits in the open art window, clear of every text plate
    ax0, _, ax1, ay1 = FRONT_BOXES["art_window"]
    draw_emblem(painter, (ax0 + ax1) // 2, ay1 - 60, 44, rarity)
    return painter.img, painter.foil


def build_back_template(rarity: str) -> tuple[Image.Image, dict[str, Image.Image]]:
    # opaque dark ground for the whole card
    rng = rng_for("backfield", rarity)
    grain = _noise(FULL_W, FULL_H, rng, octave=6, blur=1.4)
    yy, xx = np.mgrid[0:FULL_H, 0:FULL_W].astype(np.float32)
    glow = np.exp(-(((xx - FULL_W / 2) / (FULL_W * 0.62)) ** 2
                    + ((yy - FULL_H * 0.42) / (FULL_H * 0.55)) ** 2))
    base = np.array((17, 14, 12), dtype=np.float32) / 255.0
    warm = np.array((92, 66, 32), dtype=np.float32) / 255.0
    field = base[None, None, :] * (0.7 + 0.6 * grain[..., None]) + warm[None, None, :] * (
        0.20 * glow[..., None]
    )
    ground = Image.fromarray((np.clip(field, 0, 1) * 255).astype(np.uint8), "RGB").convert("RGBA")

    band = _engraved_band(rarity, "back")
    ground.alpha_composite(band)
    painter = Painter(ground)
    _band_lines(painter, rarity)

    # parchment inset for the lore panel
    lx0, ly0, lx1, ly1 = BACK_BOXES["lore"]
    pw, ph = lx1 - lx0, ly1 - ly0
    paper = Image.fromarray(
        (parchment_field(pw, ph, rarity) * 255).astype(np.uint8), "RGB"
    ).convert("RGBA")
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=18, fill=245)
    painter.img.paste(paper, (lx0, ly0), mask)
    painter.rect((lx0, ly0, lx1, ly1), outline=foil_ink(rarity) + (230,), width=4, radius=18,
                 foil=TIER_STYLE[rarity]["foil"])

    for key in ("title", "traits", "flavor"):
        _plate(painter, BACK_BOXES[key])
    _plate(painter, BACK_BOXES["footer"], radius=10, strength=170)

    sx0, sy0, sx1, sy1 = BACK_BOXES["sigil"]
    draw_emblem(painter, (sx0 + sx1) // 2, sy0 + 64, 64, rarity)
    return painter.img, painter.foil


def cmd_templates(_args) -> None:
    ART_TEMPLATES.mkdir(parents=True, exist_ok=True)
    for rarity in RARITIES:
        for side, builder in (("front", build_front_template), ("back", build_back_template)):
            img, foils = builder(rarity)
            path = ART_TEMPLATES / f"{side}_{rarity}.png"
            img.save(path, dpi=(DPI, DPI))
            for colour, mask in foils.items():
                if mask.getbbox() is None:
                    continue
                mask.convert("1").save(
                    ART_TEMPLATES / f"{side}_{rarity}.foil-{colour}.png", dpi=(DPI, DPI)
                )
            print(f"template  {path.relative_to(ROOT)}")


def load_template(side: str, rarity: str) -> Image.Image:
    path = ART_TEMPLATES / f"{side}_{rarity}.png"
    if not path.exists():
        raise PipelineError(f"missing frame template {path} - run: python generate.py templates")
    img = Image.open(path).convert("RGBA")
    if img.size != (FULL_W, FULL_H):
        raise PipelineError(f"template {path} is {img.size}, expected {(FULL_W, FULL_H)}")
    return img


def load_template_foil(side: str, rarity: str) -> dict[str, Image.Image]:
    out: dict[str, Image.Image] = {}
    for colour in ("gold", "silver"):
        path = ART_TEMPLATES / f"{side}_{rarity}.foil-{colour}.png"
        if path.exists():
            out[colour] = Image.open(path).convert("L")
    return out


# --------------------------------------------------------------------------
# 8. prep - finished art -> full-bleed print canvas
# --------------------------------------------------------------------------

def _mirror_extend(strip: np.ndarray, n: int, axis: int, before: bool) -> np.ndarray:
    """Repeat-mirror `strip` outward for n px along `axis`."""
    if n <= 0:
        return strip[:0] if axis == 0 else strip[:, :0]
    tiles, total = [], 0
    current = np.flip(strip, axis=axis)
    while total < n:
        tiles.append(current)
        total += current.shape[axis]
        current = np.flip(current, axis=axis)
    if before:
        band = np.concatenate(list(reversed(tiles)), axis=axis)
        return band[-n:] if axis == 0 else band[:, -n:]
    band = np.concatenate(tiles, axis=axis)
    return band[:n] if axis == 0 else band[:, :n]


def prep_art(src: Image.Image, strategy: str = "fit-trim") -> Image.Image:
    """Scale finished art onto the 1650x2250 full-bleed canvas."""
    src = src.convert("RGB")
    w, h = src.size
    if strategy == "cover":
        scale = max(FULL_W / w, FULL_H / h)
        nw, nh = int(math.ceil(w * scale)), int(math.ceil(h * scale))
        big = src.resize((nw, nh), Image.LANCZOS)
        left, top = (nw - FULL_W) // 2, (nh - FULL_H) // 2
        return big.crop((left, top, left + FULL_W, top + FULL_H))

    if strategy != "fit-trim":
        raise PipelineError(f"unknown strategy {strategy!r}")

    scale = min(TRIM_W / w, TRIM_H / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    art = np.asarray(src.resize((nw, nh), Image.LANCZOS), dtype=np.uint8)

    x0 = BLEED + (TRIM_W - nw) // 2
    y0 = BLEED + (TRIM_H - nh) // 2
    canvas = np.zeros((FULL_H, FULL_W, 3), dtype=np.uint8)
    canvas[y0:y0 + nh, x0:x0 + nw] = art

    s = min(EDGE_STRIP, nw, nh)
    if x0 > 0:
        canvas[y0:y0 + nh, :x0] = _mirror_extend(art[:, :s], x0, axis=1, before=True)
    right = FULL_W - (x0 + nw)
    if right > 0:
        canvas[y0:y0 + nh, x0 + nw:] = _mirror_extend(art[:, -s:], right, axis=1, before=False)
    if y0 > 0:
        canvas[:y0, :] = _mirror_extend(canvas[y0:y0 + s, :], y0, axis=0, before=True)
    bottom = FULL_H - (y0 + nh)
    if bottom > 0:
        canvas[y0 + nh:, :] = _mirror_extend(
            canvas[y0 + nh - s:y0 + nh, :], bottom, axis=0, before=False
        )
    return Image.fromarray(canvas, "RGB")


def cmd_prep(args) -> None:
    src_path = Path(args.inp)
    if not src_path.exists():
        raise PipelineError(f"no such art file: {src_path}")
    OUT_PRINT.mkdir(parents=True, exist_ok=True)
    out = OUT_PRINT / f"{src_path.stem}_bleed_{args.strategy}.png"
    prep_art(Image.open(src_path), args.strategy).save(out, dpi=(DPI, DPI))
    print(f"prep      {out.relative_to(ROOT)}  {FULL_W}x{FULL_H} @ {DPI}dpi ({args.strategy})")


# --------------------------------------------------------------------------
# 9. Manifest
# --------------------------------------------------------------------------

STATUS_ORDER = [
    "named", "lore_complete", "art_prompted", "art_incoming",
    "art_approved", "composed", "proofed", "print_ready",
]

ALIGNMENTS = {"Order", "Chaos", "Neutral", "Greed", "Fear", "Faith"}
CATEGORIES = {"archetype", "force", "relic", "realm", "token"}

BANNED_PHRASES = [
    "delve", "unleash your", "in the world of", "game-changer",
    "game changer", "revolutionary",
]
MOON_EXEMPT = "The Moon Boy"

CONTENT_FIELDS = [
    "subtitle", "type", "alignment", "stats", "abilities",
    "weakness", "lore", "traits", "flavor", "art_back_template",
]

REQUIRED_BY_STATUS = {
    "named": ["num", "name", "rarity", "category", "status"],
    "lore_complete": CONTENT_FIELDS,
    "art_prompted": CONTENT_FIELDS + ["prompt_file"],
    "art_incoming": CONTENT_FIELDS + ["prompt_file", "art_front"],
    "art_approved": CONTENT_FIELDS + ["prompt_file", "art_front"],
    "composed": CONTENT_FIELDS + ["prompt_file", "art_front"],
    "proofed": CONTENT_FIELDS + ["prompt_file", "art_front"],
    "print_ready": CONTENT_FIELDS + ["prompt_file", "art_front"],
}


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        raise PipelineError(f"missing {MANIFEST_PATH}")
    data = json.loads(MANIFEST_PATH.read_text())
    cards = data["cards"] if isinstance(data, dict) else data
    if not isinstance(cards, list):
        raise PipelineError("set_manifest.json must hold a list of cards under 'cards'")
    return cards


def get_card(num: int) -> dict:
    for card in load_manifest():
        if card.get("num") == num:
            return card
    raise PipelineError(f"card {num} is not in set_manifest.json")


def require(card: dict, field: str):
    """Fabrication firewall: read a field or fail loudly. Never defaults."""
    if field not in card or card[field] in (None, "", [], {}):
        raise PipelineError(
            f"card {card.get('num', '??')} ({card.get('name', 'unnamed')}) "
            f"is missing manifest field {field!r}. Add it to set_manifest.json - "
            "the pipeline will not invent it."
        )
    return card[field]


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return re.sub(r"_+", "_", s)


def card_stem(card: dict) -> str:
    return f"{card['num']:03d}_{slug(card['name'])}"


def status_at_least(card: dict, status: str) -> bool:
    try:
        return STATUS_ORDER.index(card.get("status", "named")) >= STATUS_ORDER.index(status)
    except ValueError as exc:
        raise PipelineError(f"card {card.get('num')} has unknown status {card.get('status')!r}") from exc


# --------------------------------------------------------------------------
# 10. validate
# --------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def validate_manifest() -> list[str]:
    cards = load_manifest()
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    # -- set-level -------------------------------------------------------
    if len(cards) != 100:
        err(f"set has {len(cards)} cards, expected exactly 100")

    numbers = [c.get("num") for c in cards]
    if sorted(n for n in numbers if isinstance(n, int)) != list(range(1, 101)):
        err("card numbers must be the contiguous unique range 1..100")

    names = [c.get("name") for c in cards]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        err(f"duplicate card names: {dupes}")

    counts = {r: 0 for r in RARITIES}
    for card in cards:
        if card.get("rarity") in counts:
            counts[card["rarity"]] += 1
    for rarity, expected in TIER_COUNTS.items():
        if counts[rarity] != expected:
            err(f"rarity {rarity}: {counts[rarity]} cards, tier table says {expected}")

    # -- per card --------------------------------------------------------
    for card in cards:
        num = card.get("num")
        tag = f"card {num:03d}" if isinstance(num, int) else f"card {num!r}"
        name = card.get("name", "?")

        if not isinstance(num, int) or not 1 <= num <= 100:
            err(f"{tag}: 'num' must be an int in 1..100")
            continue
        if not card.get("name"):
            err(f"{tag}: missing name")

        expected_rarity = rarity_for_number(num)
        if card.get("rarity") != expected_rarity:
            err(f"{tag} {name}: rarity {card.get('rarity')!r} but number sits in "
                f"the {expected_rarity} range")

        if card.get("category") not in CATEGORIES:
            err(f"{tag} {name}: category {card.get('category')!r} not in {sorted(CATEGORIES)}")

        status = card.get("status")
        if status not in STATUS_ORDER:
            err(f"{tag} {name}: unknown status {status!r}")
            continue

        missing = [f for f in REQUIRED_BY_STATUS["named"] if not card.get(f)]
        if status != "named":
            missing += [f for f in REQUIRED_BY_STATUS[status] if not card.get(f)]
        if missing:
            err(f"{tag} {name}: status {status!r} requires missing fields {sorted(set(missing))}")
            continue
        if status == "named":
            continue

        if card.get("alignment") not in ALIGNMENTS:
            err(f"{tag} {name}: alignment {card.get('alignment')!r} not in {sorted(ALIGNMENTS)}")

        # stats
        stats = card["stats"]
        if len(stats) != 5:
            err(f"{tag} {name}: {len(stats)} stats, expected exactly 5")
        maxed = 0
        for stat in stats:
            value, label = stat.get("value"), stat.get("label", "")
            if not isinstance(value, int) or not 0 <= value <= 100:
                err(f"{tag} {name}: stat {label!r} value must be an int 0..100, got {value!r}")
            elif value == 100:
                maxed += 1
            if len(_words(label)) > 2:
                err(f"{tag} {name}: stat label {label!r} exceeds 2 words")
        if maxed > 1:
            err(f"{tag} {name}: {maxed} stats at 100, at most one allowed")

        # abilities
        abilities = card["abilities"]
        kinds = [a.get("kind") for a in abilities]
        if sorted(kinds) != ["active", "passive", "ultimate"]:
            err(f"{tag} {name}: abilities must be exactly one active, one passive, "
                f"one ultimate; got {kinds}")
        for ability in abilities:
            if len(_words(ability.get("name", ""))) > 3:
                err(f"{tag} {name}: ability name {ability.get('name')!r} exceeds 3 words")
            if len(_words(ability.get("text", ""))) > 20:
                err(f"{tag} {name}: ability {ability.get('name')!r} text exceeds 20 words")

        # traits
        traits = card["traits"]
        if len(traits) != 4:
            err(f"{tag} {name}: {len(traits)} traits, expected exactly 4")
        for trait in traits:
            if len(_words(trait.get("label", ""))) > 2:
                err(f"{tag} {name}: trait label {trait.get('label')!r} exceeds 2 words")
            if not trait.get("text"):
                err(f"{tag} {name}: trait {trait.get('label')!r} has no text")

        # lore / flavor
        n_sent = len(_sentences(card["lore"]))
        if not 2 <= n_sent <= 4:
            err(f"{tag} {name}: lore has {n_sent} sentences, expected 2-4")
        if len(_words(card["flavor"])) > 10:
            err(f"{tag} {name}: flavor exceeds 10 words")

        # banned copy
        blob = " ".join([
            card["lore"], card["flavor"], card["subtitle"], card["weakness"],
            " ".join(a.get("text", "") for a in abilities),
            " ".join(t.get("text", "") for t in traits),
        ]).lower()
        for phrase in BANNED_PHRASES:
            if phrase in blob:
                err(f"{tag} {name}: banned phrase {phrase!r} in copy")
        if "to the moon" in blob and name != MOON_EXEMPT:
            err(f"{tag} {name}: 'to the moon' is only allowed on {MOON_EXEMPT}")
        second = re.search(r"\b(you|your|yours|yourself)\b", blob)
        if second:
            err(f"{tag} {name}: second person ({second.group(0)!r}) is never used in copy")

        # assets
        back = ART_TEMPLATES / Path(card["art_back_template"]).name
        if not (ROOT / card["art_back_template"]).exists() and not back.exists():
            err(f"{tag} {name}: back template {card['art_back_template']} does not exist "
                "(run: python generate.py templates)")
        if card.get("prompt_file") and not (ROOT / card["prompt_file"]).exists():
            err(f"{tag} {name}: prompt file {card['prompt_file']} does not exist")
        if status_at_least(card, "art_approved"):
            if not (ROOT / card["art_front"]).exists():
                err(f"{tag} {name}: status {status!r} but front art {card['art_front']} "
                    "does not exist")

    return errors


def cmd_validate(_args) -> None:
    errors = validate_manifest()
    cards = load_manifest()
    if errors:
        print(f"validate  FAIL  ({len(errors)} problem(s))")
        for e in errors:
            print(f"  - {e}")
        raise SystemExit(1)
    by_status: dict[str, int] = {}
    for card in cards:
        by_status[card["status"]] = by_status.get(card["status"], 0) + 1
    order = ", ".join(f"{s}={by_status[s]}" for s in STATUS_ORDER if s in by_status)
    print(f"validate  OK  100 cards, tier counts match, no duplicates")
    print(f"          status: {order}")


# --------------------------------------------------------------------------
# 11. Placeholder art (never printable, explicitly stamped)
# --------------------------------------------------------------------------

ART_W, ART_H = 1024, 1536


def placeholder_art(card: dict) -> Image.Image:
    """Deterministic stand-in so layout can be proofed before real art lands."""
    num, rarity = card["num"], card["rarity"]
    rng = rng_for("placeholder", num, rarity)
    grain = _noise(ART_W, ART_H, rng, octave=6, blur=2.0)
    cloud = _noise(ART_W, ART_H, rng, octave=40, blur=14.0)
    yy, xx = np.mgrid[0:ART_H, 0:ART_W].astype(np.float32)
    glow = np.exp(-(((xx - ART_W / 2) / (ART_W * 0.45)) ** 2
                    + ((yy - ART_H * 0.40) / (ART_H * 0.38)) ** 2))
    base = np.array((18, 15, 13), dtype=np.float32) / 255.0
    warm = np.array(GOLD_LO, dtype=np.float32) / 255.0
    field = (base[None, None, :] * (0.6 + 0.8 * grain[..., None])
             + warm[None, None, :] * (0.55 * glow[..., None] + 0.22 * cloud[..., None]))
    img = Image.fromarray((np.clip(field, 0, 1) * 255).astype(np.uint8), "RGB").convert("RGBA")

    draw = ImageDraw.Draw(img, "RGBA")
    cx, cy = ART_W // 2, int(ART_H * 0.30)
    for i, r in enumerate(range(150, 430, 46)):
        draw.ellipse((cx - r, cy - r, cx + r, cy + r),
                     outline=GOLD + (110 - i * 12,), width=3)
    for i in range(TIER_STYLE[rarity]["spokes"]):
        a = 2 * math.pi * i / TIER_STYLE[rarity]["spokes"]
        draw.line([(cx + math.cos(a) * 150, cy + math.sin(a) * 150),
                   (cx + math.cos(a) * 420, cy + math.sin(a) * 420)],
                  fill=GOLD + (90,), width=3)
    # Stamped inside the zone the frame leaves open, so no proof can be mistaken
    # for finished art.
    stamp = get_font("display", 54, 700)
    draw.text((cx, int(ART_H * 0.42)), "PLACEHOLDER", font=stamp, fill=(228, 78, 62, 235),
              anchor="mm")
    draw.text((cx, int(ART_H * 0.47)), f"CARD {num:03d} - NOT FOR PRINT",
              font=get_font("display", 30, 500), fill=(228, 120, 100, 220), anchor="mm")
    return img.convert("RGB")


def placeholder_path(card: dict) -> Path:
    return ART_PLACEHOLDER / f"{card_stem(card)}_front.png"


def cmd_artgen(args) -> None:
    ART_PLACEHOLDER.mkdir(parents=True, exist_ok=True)
    cards = [get_card(args.card)] if args.card else load_manifest()
    for card in cards:
        path = placeholder_path(card)
        placeholder_art(card).save(path, dpi=(DPI, DPI))
        print(f"artgen    {path.relative_to(ROOT)}  {ART_W}x{ART_H}")


# --------------------------------------------------------------------------
# 12. compose
# --------------------------------------------------------------------------

def resolve_front_art(card: dict, allow_placeholder: bool) -> tuple[Path, bool]:
    approved = ROOT / card["art_front"] if card.get("art_front") else None
    if approved and approved.exists():
        return approved, False
    if not allow_placeholder:
        raise PipelineError(
            f"card {card['num']:03d} {card['name']}: front art "
            f"{card.get('art_front') or '(unset)'} not found. Approve real art, or pass "
            "--allow-placeholder to compose a clearly-marked proof."
        )
    path = placeholder_path(card)
    if not path.exists():
        ART_PLACEHOLDER.mkdir(parents=True, exist_ok=True)
        placeholder_art(card).save(path, dpi=(DPI, DPI))
    return path, True


def _serial_text(serial: int | None) -> str:
    if serial is None:
        return "EDITION PROOF"
    return f"EDITION {serial:03d} / {EDITION_TOTAL}"


def compose_front(card: dict, serial: int | None, allow_placeholder: bool) -> Painter:
    rarity = card["rarity"]
    style = TIER_STYLE[rarity]
    foil = style["foil"]
    ink, hi = foil_ink(rarity), foil_ink_hi(rarity)

    art_path, is_placeholder = resolve_front_art(card, allow_placeholder)
    base = prep_art(Image.open(art_path), "fit-trim").convert("RGBA")
    base.alpha_composite(load_template("front", rarity))
    painter = Painter(base)
    for colour, mask in load_template_foil("front", rarity).items():
        painter.foil[colour] = Image.fromarray(
            np.maximum(np.asarray(painter.foil[colour]), np.asarray(mask))
        )
        painter.foil_draw[colour] = ImageDraw.Draw(painter.foil[colour])

    # -- title plate -----------------------------------------------------
    x0, y0, x1, y1 = FRONT_BOXES["title"]
    cx = (x0 + x1) // 2
    name = require(card, "name").upper()
    name_font = fit_font("display", name, x1 - x0 - 2 * PAD, 78, 40, weight=700, track=0.055)
    painter.text((cx, y0 + 34), name, name_font, ink + (255,), anchor="ma",
                 tracking=name_font.size * 0.055, foil=foil)
    sub = require(card, "subtitle")
    sub_font = fit_font("body_italic", sub, x1 - x0 - 2 * PAD, 40, 26, weight=500)
    painter.text((cx, y0 + 34 + name_font.size + 42), sub, sub_font, BONE + (240,), anchor="ma")

    # -- ribbon ----------------------------------------------------------
    rx0, ry0, rx1, ry1 = FRONT_BOXES["ribbon"]
    ribbon = f"{require(card, 'type')}  ·  {require(card, 'alignment')}  ·  {rarity.upper()}"
    rib_font = fit_font("display", ribbon.upper(), rx1 - rx0 - 2 * PAD - 90, 30, 20,
                        weight=600, track=0.10)
    painter.text(((rx0 + rx1) // 2, (ry0 + ry1) // 2), ribbon.upper(), rib_font,
                 PARCHMENT + (245,), anchor="mm", tracking=rib_font.size * 0.10)

    # -- stats -----------------------------------------------------------
    sx0, sy0, sx1, sy1 = FRONT_BOXES["stats"]
    head = get_font("display", 26, 600)
    painter.text(((sx0 + sx1) // 2, sy0 + 18), "ATTRIBUTES", head, ink + (250,),
                 anchor="ma", tracking=26 * 0.24)
    painter.line([(sx0 + PAD, sy0 + 62), (sx1 - PAD, sy0 + 62)], fill=ink + (120,), width=2)

    stats = require(card, "stats")
    rows_top = sy0 + 78
    row_h = (sy1 - PAD - rows_top) // max(1, len(stats))
    label_font = get_font("display", 27, 600)
    value_font = get_font("display", 32, 700)
    bar_x0, bar_x1 = sx0 + 470, sx1 - 150
    for i, stat in enumerate(stats):
        cy_row = rows_top + i * row_h + row_h // 2
        painter.text((sx0 + PAD + 8, cy_row), str(stat["label"]).upper(), label_font,
                     PARCHMENT + (245,), anchor="lm", tracking=27 * 0.09)
        track_box = (bar_x0, cy_row - 11, bar_x1, cy_row + 11)
        painter.rect(track_box, fill=(0, 0, 0, 150), outline=(150, 116, 52, 170),
                     width=2, radius=11)
        value = int(stat["value"])
        fill_w = int(round((bar_x1 - bar_x0 - 8) * value / 100.0))
        if fill_w > 4:
            painter.rect((bar_x0 + 4, cy_row - 7, bar_x0 + 4 + fill_w, cy_row + 7),
                         fill=ink + (255,), radius=7)
            painter.rect((bar_x0 + 4, cy_row - 7, bar_x0 + 4 + fill_w, cy_row - 2),
                         fill=hi + (170,), radius=3)
        painter.text((sx1 - PAD - 8, cy_row), f"{value:d}", value_font, hi + (255,), anchor="rm")

    # -- abilities -------------------------------------------------------
    ax0, ay0, ax1, ay1 = FRONT_BOXES["abilities"]
    painter.text(((ax0 + ax1) // 2, ay0 + 16), "ABILITIES", head, ink + (250,),
                 anchor="ma", tracking=26 * 0.24)
    painter.line([(ax0 + PAD, ay0 + 60), (ax1 - PAD, ay0 + 60)], fill=ink + (120,), width=2)

    abilities = require(card, "abilities")
    order = {"active": 0, "passive": 1, "ultimate": 2}
    abilities = sorted(abilities, key=lambda a: order.get(a.get("kind"), 9))
    block_top = ay0 + 74
    block_h = (ay1 - PAD - block_top) // max(1, len(abilities))
    kind_font = get_font("display", 19, 600)
    for i, ability in enumerate(abilities):
        top = block_top + i * block_h
        aname = str(ability["name"]).upper()
        an_font = fit_font("display", aname, ax1 - ax0 - 2 * PAD - 260, 30, 22,
                           weight=700, track=0.08)
        painter.text((ax0 + PAD + 8, top), aname, an_font, ink + (255,), anchor="la",
                     tracking=an_font.size * 0.08, foil=foil if rarity in ("mythic", "epic") else None)
        kind = str(ability["kind"]).upper()
        kw = text_width(kind_font, kind, 19 * 0.14)
        pill = (ax1 - PAD - 16 - kw - 26, top - 3, ax1 - PAD - 8, top + 29)
        painter.rect(pill, fill=(0, 0, 0, 140), outline=ink + (170,), width=2, radius=15)
        painter.text(((pill[0] + pill[2]) / 2, (pill[1] + pill[3]) / 2), kind, kind_font,
                     PARCHMENT + (240,), anchor="mm", tracking=19 * 0.14)
        body_font, lines, line_h = fit_wrapped(
            "body", str(ability["text"]), ax1 - ax0 - 2 * PAD - 16, block_h - 46, 29, 22,
            leading=1.24, weight=430,
        )
        for j, line in enumerate(lines):
            painter.text((ax0 + PAD + 8, top + 40 + j * line_h), line, body_font,
                         BONE + (245,), anchor="la")

    # -- weakness --------------------------------------------------------
    wx0, wy0, wx1, wy1 = FRONT_BOXES["weakness"]
    wl_font = get_font("display", 21, 600)
    painter.text((wx0 + PAD, (wy0 + wy1) // 2), "WEAKNESS", wl_font, ink + (250,),
                 anchor="lm", tracking=21 * 0.16)
    lead = text_width(wl_font, "WEAKNESS", 21 * 0.16) + 26
    wk_font = fit_font("body_italic", require(card, "weakness"),
                       (wx1 - PAD) - (wx0 + PAD + lead), 28, 20, weight=460)
    painter.text((wx0 + PAD + lead, (wy0 + wy1) // 2), card["weakness"], wk_font,
                 BONE + (245,), anchor="lm")

    # -- footer ----------------------------------------------------------
    _footer(painter, card, serial, FRONT_BOXES["footer"], rarity)

    if is_placeholder:
        _placeholder_stamp(painter)
    return painter


def compose_back(card: dict, serial: int | None) -> Painter:
    rarity = card["rarity"]
    foil = TIER_STYLE[rarity]["foil"]
    ink, hi = foil_ink(rarity), foil_ink_hi(rarity)

    painter = Painter(load_template("back", rarity).copy())
    for colour, mask in load_template_foil("back", rarity).items():
        painter.foil[colour] = Image.fromarray(
            np.maximum(np.asarray(painter.foil[colour]), np.asarray(mask))
        )
        painter.foil_draw[colour] = ImageDraw.Draw(painter.foil[colour])

    # -- header ----------------------------------------------------------
    x0, y0, x1, y1 = BACK_BOXES["title"]
    cx = (x0 + x1) // 2
    name = require(card, "name").upper()
    name_font = fit_font("display", name, x1 - x0 - 2 * PAD, 58, 34, weight=700, track=0.06)
    painter.text((cx, y0 + 30), name, name_font, ink + (255,), anchor="ma",
                 tracking=name_font.size * 0.06, foil=foil)
    tagline = f"{require(card, 'category').upper()}  ·  {rarity.upper()}"
    painter.text((cx, y0 + 30 + name_font.size + 30), tagline, get_font("display", 22, 600),
                 PARCHMENT + (230,), anchor="ma", tracking=22 * 0.18)

    # -- lore on parchment ----------------------------------------------
    lx0, ly0, lx1, ly1 = BACK_BOXES["lore"]
    lore_font, lines, line_h = fit_wrapped(
        "body", require(card, "lore"), lx1 - lx0 - 2 * (PAD + 12), ly1 - ly0 - 2 * (PAD + 8),
        40, 26, leading=1.40, weight=440,
    )
    block_h = line_h * len(lines)
    top = ly0 + ((ly1 - ly0) - block_h) // 2
    for j, line in enumerate(lines):
        painter.text((cx, top + j * line_h), line, lore_font, (44, 34, 22, 255), anchor="ma")

    # -- traits ----------------------------------------------------------
    tx0, ty0, tx1, ty1 = BACK_BOXES["traits"]
    head = get_font("display", 26, 600)
    painter.text(((tx0 + tx1) // 2, ty0 + 18), "TRAITS", head, ink + (250,),
                 anchor="ma", tracking=26 * 0.24)
    painter.line([(tx0 + PAD, ty0 + 62), (tx1 - PAD, ty0 + 62)], fill=ink + (120,), width=2)
    traits = require(card, "traits")
    rows_top = ty0 + 80
    row_h = (ty1 - PAD - rows_top) // max(1, len(traits))
    for i, trait in enumerate(traits):
        top = rows_top + i * row_h
        label = str(trait["label"]).upper()
        lf = fit_font("display", label, tx1 - tx0 - 2 * PAD, 29, 22, weight=700, track=0.09)
        painter.text((tx0 + PAD + 8, top), label, lf, ink + (255,), anchor="la",
                     tracking=lf.size * 0.09)
        bf, lines, lh = fit_wrapped("body", str(trait["text"]), tx1 - tx0 - 2 * PAD - 16,
                                    row_h - 44, 33, 21, leading=1.26, weight=430)
        for j, line in enumerate(lines):
            painter.text((tx0 + PAD + 8, top + 38 + j * lh), line, bf, BONE + (245,), anchor="la")

    # -- flavor ----------------------------------------------------------
    fx0, fy0, fx1, fy1 = BACK_BOXES["flavor"]
    flavor = f"“{require(card, 'flavor')}”"
    ff, flines, flh = fit_wrapped("body_italic", flavor, fx1 - fx0 - 2 * PAD - 40,
                                  fy1 - fy0 - 2 * PAD, 42, 26, leading=1.30, weight=500)
    ftop = fy0 + ((fy1 - fy0) - flh * len(flines)) // 2
    for j, line in enumerate(flines):
        painter.text(((fx0 + fx1) // 2, ftop + j * flh), line, ff, PARCHMENT + (250,), anchor="ma")

    # -- set mark under the sigil ---------------------------------------
    sx0, sy0, sx1, sy1 = BACK_BOXES["sigil"]
    painter.text(((sx0 + sx1) // 2, sy1 - 82), SET_MARK, get_font("display", 24, 600),
                 ink + (240,), anchor="ma", tracking=24 * 0.22, foil=foil)
    painter.text(((sx0 + sx1) // 2, sy1 - 48), SET_NAME, get_font("display", 20, 500),
                 PARCHMENT + (215,), anchor="ma", tracking=20 * 0.22)

    _footer(painter, card, serial, BACK_BOXES["footer"], rarity)
    return painter


def _footer(painter: Painter, card: dict, serial: int | None, box, rarity: str) -> None:
    x0, y0, x1, y1 = box
    cy = (y0 + y1) // 2
    ink = foil_ink(rarity)
    num_font = get_font("display", 24, 700)
    mid_font = get_font("display", 20, 500)
    painter.text((x0 + PAD, cy), f"No. {card['num']:03d} / 100", num_font,
                 foil_ink_hi(rarity) + (250,), anchor="lm", tracking=24 * 0.10)
    painter.text(((x0 + x1) // 2, cy), SET_NAME, mid_font, PARCHMENT + (210,),
                 anchor="mm", tracking=20 * 0.20)
    # Mythic serials print in gold foil; every other tier prints flat.
    serial_foil = TIER_STYLE[rarity]["foil"] if rarity == "mythic" else None
    painter.text((x1 - PAD, cy), _serial_text(serial), num_font, ink + (250,),
                 anchor="rm", tracking=24 * 0.10, foil=serial_foil)


def _placeholder_stamp(painter: Painter) -> None:
    label = "PLACEHOLDER ART - NOT FOR PRINT"
    font = get_font("display", 26, 700)
    width = int(math.ceil(text_width(font, label, 26 * 0.02)))
    painter.rect((BLEED + 10, BLEED + 10, BLEED + 34 + width, BLEED + 62),
                 fill=(150, 30, 24, 215), outline=(255, 220, 210, 230), width=2, radius=8)
    painter.text((BLEED + 22, BLEED + 22), label, font, (255, 236, 232, 255),
                 anchor="la", tracking=26 * 0.02)


def _write_card_outputs(painter: Painter, stem: str, serial: int | None) -> Path:
    OUT_PRINT.mkdir(parents=True, exist_ok=True)
    OUT_FOIL.mkdir(parents=True, exist_ok=True)
    suffix = f"_s{serial:03d}" if serial is not None else ""
    out = OUT_PRINT / f"{stem}{suffix}.png"
    painter.img.convert("RGB").save(out, dpi=(DPI, DPI))
    for colour, mask in painter.foil.items():
        if mask.getbbox() is None:
            continue
        mask.convert("1").save(OUT_FOIL / f"{stem}{suffix}.foil-{colour}.png", dpi=(DPI, DPI))
    return out


def compose_card(card: dict, serial: int | None, allow_placeholder: bool) -> list[Path]:
    stem = card_stem(card)
    written = []
    front = compose_front(card, serial, allow_placeholder)
    written.append(_write_card_outputs(front, f"{stem}_front", serial))
    back = compose_back(card, serial)
    written.append(_write_card_outputs(back, f"{stem}_back", serial))
    return written


def cmd_compose(args) -> None:
    card = get_card(args.card)
    if card.get("status") == "named":
        raise PipelineError(
            f"card {card['num']:03d} {card['name']} is only 'named' - write its content "
            "into set_manifest.json before composing."
        )
    serials: list[int | None]
    if args.all_serials:
        serials = list(range(1, EDITION_TOTAL + 1))
    elif args.serial is not None:
        if not 1 <= args.serial <= EDITION_TOTAL:
            raise PipelineError(f"serial must be 1..{EDITION_TOTAL}")
        serials = [args.serial]
    else:
        serials = [None]
    for serial in serials:
        for path in compose_card(card, serial, args.allow_placeholder):
            print(f"compose   {path.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# 13. proof + foil
# --------------------------------------------------------------------------

def proof_overlay(img: Image.Image) -> Image.Image:
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out, "RGBA")
    draw.rectangle((TRIM_BOX[0], TRIM_BOX[1], TRIM_BOX[2] - 1, TRIM_BOX[3] - 1),
                   outline=(226, 32, 32, 255), width=3)
    x0, y0, x1, y1 = SAFE_BOX
    dash, gap = 26, 18
    for x in range(x0, x1, dash + gap):
        xe = min(x + dash, x1)
        draw.line([(x, y0), (xe, y0)], fill=(40, 140, 255, 255), width=3)
        draw.line([(x, y1 - 1), (xe, y1 - 1)], fill=(40, 140, 255, 255), width=3)
    for y in range(y0, y1, dash + gap):
        ye = min(y + dash, y1)
        draw.line([(x0, y), (x0, ye)], fill=(40, 140, 255, 255), width=3)
        draw.line([(x1 - 1, y), (x1 - 1, ye)], fill=(40, 140, 255, 255), width=3)
    tiny = get_font("display", 18, 600)
    draw.text((6, 6), "TRIM", font=tiny, fill=(226, 32, 32, 255))
    draw.text((6, 30), "SAFE", font=tiny, fill=(40, 140, 255, 255))
    draw.text((FULL_W - 6, 6), f"{FULL_W}x{FULL_H}px @ {DPI}dpi", font=tiny,
              fill=(255, 255, 255, 210), anchor="ra")
    return out


def cmd_proof(args) -> None:
    src = Path(args.inp)
    if not src.exists():
        raise PipelineError(f"no such image: {src}")
    OUT_PROOF.mkdir(parents=True, exist_ok=True)
    out = OUT_PROOF / f"{src.stem}_proof.png"
    proof_overlay(Image.open(src)).save(out, dpi=(DPI, DPI))
    print(f"proof     {out.relative_to(ROOT)}")


def foil_mask_from_image(img: Image.Image) -> Image.Image:
    """HSV threshold pass: pull gold-ink pixels out of a finished card image."""
    hsv = np.asarray(img.convert("RGB").convert("HSV"), dtype=np.int16)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gold = (h >= 15) & (h <= 42) & (s >= 70) & (v >= 105)
    mask = Image.fromarray((gold * 255).astype(np.uint8), "L")
    return mask.filter(ImageFilter.MedianFilter(3)).convert("1")


def cmd_foil(args) -> None:
    src = Path(args.inp)
    if not src.exists():
        raise PipelineError(f"no such image: {src}")
    OUT_FOIL.mkdir(parents=True, exist_ok=True)
    out = OUT_FOIL / f"{src.stem}.foil-threshold.png"
    foil_mask_from_image(Image.open(src)).save(out, dpi=(DPI, DPI))
    print(f"foil      {out.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# 14. sheet - singles PDF + letter proof sheet
# --------------------------------------------------------------------------

def _print_pngs() -> list[Path]:
    if not OUT_PRINT.exists():
        return []
    return sorted(p for p in OUT_PRINT.glob("*.png") if "_bleed" not in p.stem)


def cmd_sheet(_args) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdfcanvas

    pages = _print_pngs()
    if not pages:
        raise PipelineError("nothing in output/print - run compose first")
    OUT_PDF.mkdir(parents=True, exist_ok=True)

    # 1. singles: one full-bleed card per page, page == 2.75in x 3.75in
    singles = OUT_PDF / "GenesisSeries_PRINT_singles.pdf"
    pw, ph = (TRIM_W_IN + 2 * BLEED_IN) * PT_PER_IN, (TRIM_H_IN + 2 * BLEED_IN) * PT_PER_IN
    pdf = pdfcanvas.Canvas(str(singles), pagesize=(pw, ph), pageCompression=1)
    pdf.setTitle("Crypto Lore Series - Genesis Series 2026 - print singles")
    for path in pages:
        pdf.drawImage(ImageReader(str(path)), 0, 0, width=pw, height=ph)
        pdf.showPage()
    pdf.save()
    print(f"sheet     {singles.relative_to(ROOT)}  ({len(pages)} pages, "
          f"{TRIM_W_IN + 2 * BLEED_IN}x{TRIM_H_IN + 2 * BLEED_IN} in)")

    # 2. letter proof sheet: 3x3 trim-size cards with crop marks
    proof = OUT_PDF / "GenesisSeries_PROOF_sheet.pdf"
    pdf = pdfcanvas.Canvas(str(proof), pagesize=letter, pageCompression=1)
    pdf.setTitle("Crypto Lore Series - Genesis Series 2026 - proof sheet")
    cw, ch = TRIM_W_IN * PT_PER_IN, TRIM_H_IN * PT_PER_IN
    cols, rows = 3, 3
    mx = (letter[0] - cols * cw) / 2.0
    my = (letter[1] - rows * ch) / 2.0
    for index, path in enumerate(pages):
        slot = index % (cols * rows)
        if slot == 0 and index:
            pdf.showPage()
        col, row = slot % cols, slot // cols
        x = mx + col * cw
        y = letter[1] - my - (row + 1) * ch
        # Proof sheet is for reading layout, not for printing: downsample to
        # PROOF_SHEET_DPI so the file stays openable on a normal machine.
        trimmed = Image.open(path).crop(TRIM_BOX).resize(
            (int(round(TRIM_W_IN * PROOF_SHEET_DPI)), int(round(TRIM_H_IN * PROOF_SHEET_DPI))),
            Image.LANCZOS,
        )
        pdf.drawImage(ImageReader(trimmed), x, y, width=cw, height=ch)
        pdf.setLineWidth(0.4)
        pdf.setStrokeColorRGB(0.6, 0.6, 0.6)
        for cx, cy in ((x, y), (x + cw, y), (x, y + ch), (x + cw, y + ch)):
            pdf.line(cx - 9, cy, cx - 3, cy)
            pdf.line(cx + 3, cy, cx + 9, cy)
            pdf.line(cx, cy - 9, cx, cy - 3)
            pdf.line(cx, cy + 3, cx, cy + 9)
    pdf.save()
    print(f"sheet     {proof.relative_to(ROOT)}  (letter, {cols}x{rows} per page)")


# --------------------------------------------------------------------------
# 15. prompts + STATUS.md
# --------------------------------------------------------------------------

def _read_template(name: str) -> str:
    path = PROMPT_DIR / name
    if not path.exists():
        raise PipelineError(f"missing prompt template {path}")
    return path.read_text()


def build_prompt(card: dict) -> str:
    tier = _read_template(f"tier_{card['rarity']}.md")
    category = _read_template(f"category_{card['category']}.md")
    beats = "\n".join(f"- {t['label']}: {t['text']}" for t in require(card, "traits")[:3])
    body = [
        f"# {card['num']:03d} - {card['name']}",
        f"*{require(card, 'subtitle')}*",
        "",
        f"- **Rarity:** {card['rarity']}  |  **Category:** {card['category']}  "
        f"|  **Type:** {require(card, 'type')}  |  **Alignment:** {require(card, 'alignment')}",
        "- **Output:** 1024 x 1536 (2:3), no text of any kind, no logos, no real people.",
        "",
        "## Subject",
        "",
        category.strip(),
        "",
        "## Scene beats",
        "",
        beats,
        "",
        "## Frame and finish",
        "",
        tier.strip(),
        "",
        "## Lore reference (for mood only - do not render as text)",
        "",
        f"> {require(card, 'lore')}",
        "",
        "## Negative prompt",
        "",
        "text, letters, words, numerals, watermark, signature, logo, brand marks, ticker "
        "symbols, real people, celebrity likeness, existing IP characters, photorealistic "
        "faces, low contrast, flat lighting, cartoon, meme style, extra fingers.",
        "",
    ]
    return "\n".join(body)


def cmd_prompts(args) -> None:
    PROMPT_CARD_DIR.mkdir(parents=True, exist_ok=True)
    cards = [get_card(args.card)] if args.card else load_manifest()
    written = 0
    for card in cards:
        if card.get("status") == "named":
            continue
        path = PROMPT_CARD_DIR / f"{card_stem(card)}.md"
        path.write_text(build_prompt(card))
        written += 1
    print(f"prompts   wrote {written} card prompt(s) to {PROMPT_CARD_DIR.relative_to(ROOT)}")


def write_manifest(cards: list[dict]) -> None:
    """Rewrite the manifest, preserving the document wrapper and formatting."""
    doc = json.loads(MANIFEST_PATH.read_text())
    if isinstance(doc, dict):
        doc["cards"] = cards
    else:
        doc = cards
    MANIFEST_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def download_art(url: str, dest: Path) -> Path:
    """Fetch generated art straight into art/incoming/. No manual handoff."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "crypto-lore-pipeline"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            if getattr(response, "status", 200) != 200:
                raise PipelineError(f"{url} returned HTTP {response.status}")
            with open(dest, "wb") as handle:
                shutil.copyfileobj(response, handle)
    except PipelineError:
        raise
    except Exception as exc:  # network, TLS, proxy policy
        dest.unlink(missing_ok=True)
        raise PipelineError(
            f"could not fetch {url}: {exc}\n"
            "If the session's network policy blocks the host, set the cloud "
            "environment's Network access to Custom and add the host to Allowed "
            "domains (keeping the default package-manager list), then start a new "
            "session - a running session keeps the policy it started with."
        ) from exc
    return dest


def cmd_ingest(args) -> None:
    """QC a finished art file and approve it into art/approved/ for one card.

    Checks the things a human eye cannot check quickly (aspect, resolution,
    colour mode) and refuses on failure. It cannot check for garbled lettering
    or composition — that is QC gate 2 and stays a human job.
    """
    card = get_card(args.card)
    if args.url:
        src = download_art(args.url, ART_INCOMING / f"{card_stem(card)}_front.png")
        print(f"fetch     {args.url} -> {src.relative_to(ROOT)}")
    else:
        src = Path(args.inp)
    if not src.exists():
        raise PipelineError(f"no such art file: {src}")

    try:
        img = Image.open(src)
        img.load()
    except Exception as exc:
        raise PipelineError(f"{src} is not a readable image: {exc}") from exc
    w, h = img.size
    ratio = w / h
    target = ART_W / ART_H
    if abs(ratio - target) > 0.01:
        raise PipelineError(
            f"{src} is {w}x{h} (ratio {ratio:.3f}); card art must be 2:3 "
            f"(ratio {target:.3f}). Reframe it before approving."
        )
    if w < ART_W or h < ART_H:
        raise PipelineError(
            f"{src} is {w}x{h}, below the {ART_W}x{ART_H} minimum. Upscale it "
            "before approving — the pipeline will not invent detail."
        )

    ART_APPROVED.mkdir(parents=True, exist_ok=True)
    dest = ART_APPROVED / f"{card_stem(card)}_front.png"
    img.convert("RGB").save(dest, dpi=(DPI, DPI))
    print(f"ingest    {src} -> {dest.relative_to(ROOT)}  ({w}x{h})")

    if card.get("art_front") != str(dest.relative_to(ROOT)):
        raise PipelineError(
            f"card {card['num']:03d} declares art_front={card.get('art_front')!r} "
            f"but ingest wrote {dest.relative_to(ROOT)}. Fix the manifest."
        )

    cards = load_manifest()
    for entry in cards:
        if entry["num"] == card["num"]:
            previous = entry["status"]
            entry["status"] = args.status
    write_manifest(cards)
    print(f"ingest    card {card['num']:03d} {card['name']}: "
          f"status {previous} -> {args.status}")
    cmd_status(args)


def cmd_status(_args) -> None:
    cards = sorted(load_manifest(), key=lambda c: c["num"])
    counts: dict[str, int] = {s: 0 for s in STATUS_ORDER}
    for card in cards:
        counts[card["status"]] = counts.get(card["status"], 0) + 1
    lines = [
        "# STATUS - Crypto Lore Series: Genesis Series 2026",
        "",
        "Generated by `python generate.py status`. Do not hand-edit; edit "
        "`set_manifest.json` and regenerate.",
        "",
        "## State machine",
        "",
        "`named -> lore_complete -> art_prompted -> art_incoming -> art_approved -> "
        "composed -> proofed -> print_ready`",
        "",
        "## Roll-up",
        "",
        "| Status | Cards |",
        "|---|---|",
    ]
    for status in STATUS_ORDER:
        lines.append(f"| {status} | {counts.get(status, 0)} |")
    lines += [
        f"| **total** | **{len(cards)}** |",
        "",
        "## Per card",
        "",
        "| # | Name | Rarity | Category | Status |",
        "|---|---|---|---|---|",
    ]
    for card in cards:
        lines.append(
            f"| {card['num']:03d} | {card['name']} | {card['rarity']} | "
            f"{card['category']} | {card['status']} |"
        )
    (ROOT / "STATUS.md").write_text("\n".join(lines) + "\n")
    print(f"status    STATUS.md rewritten from manifest ({len(cards)} cards)")


# --------------------------------------------------------------------------
# 16. all
# --------------------------------------------------------------------------

def cmd_all(args) -> None:
    errors = validate_manifest()
    if errors:
        print(f"validate  FAIL  ({len(errors)} problem(s)) - fix these before batching:")
        for e in errors:
            print(f"  - {e}")
        raise SystemExit(1)

    if not (ART_TEMPLATES / "front_mythic.png").exists():
        cmd_templates(args)

    composed = 0
    skipped: list[str] = []
    for card in sorted(load_manifest(), key=lambda c: c["num"]):
        if card.get("status") == "named":
            skipped.append(f"{card['num']:03d} named")
            continue
        art = ROOT / card["art_front"] if card.get("art_front") else None
        has_art = bool(art and art.exists())
        if not has_art and not args.allow_placeholder:
            skipped.append(f"{card['num']:03d} no approved art")
            continue
        for path in compose_card(card, args.serial, args.allow_placeholder):
            proof_path = OUT_PROOF / f"{path.stem}_proof.png"
            OUT_PROOF.mkdir(parents=True, exist_ok=True)
            proof_overlay(Image.open(path)).save(proof_path, dpi=(DPI, DPI))
        composed += 1
    print(f"all       composed {composed} card(s); {len(skipped)} skipped")
    for note in skipped[:10]:
        print(f"          skip {note}")
    if len(skipped) > 10:
        print(f"          ... and {len(skipped) - 10} more")
    if composed:
        cmd_sheet(args)


# --------------------------------------------------------------------------
# 17. CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generate.py",
        description="Crypto Lore Series - Genesis Series 2026 production pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("compose", help="manifest -> finished card images")
    p.add_argument("--card", type=int, required=True)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--serial", type=int, default=None)
    g.add_argument("--all-serials", action="store_true")
    p.add_argument("--allow-placeholder", action="store_true",
                   help="compose against stamped placeholder art when none is approved")
    p.set_defaults(func=cmd_compose)

    p = sub.add_parser("prep", help="finished art -> full-bleed print file")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--strategy", choices=["fit-trim", "cover"], default="fit-trim")
    p.set_defaults(func=cmd_prep)

    p = sub.add_parser("proof", help="add trim/safe-zone overlay")
    p.add_argument("--in", dest="inp", required=True)
    p.set_defaults(func=cmd_proof)

    p = sub.add_parser("foil", help="1-bit gold spot mask (HSV threshold)")
    p.add_argument("--in", dest="inp", required=True)
    p.set_defaults(func=cmd_foil)

    p = sub.add_parser("sheet", help="singles PDF + letter proof sheet")
    p.set_defaults(func=cmd_sheet)

    p = sub.add_parser("all", help="batch everything that has art")
    p.add_argument("--serial", type=int, default=None)
    p.add_argument("--allow-placeholder", action="store_true")
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("validate", help="manifest lint")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("templates", help="synthesise art/templates frames")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("artgen", help="deterministic placeholder art (never printable)")
    p.add_argument("--card", type=int, default=None)
    p.set_defaults(func=cmd_artgen)

    p = sub.add_parser("prompts", help="manifest -> prompts/cards/*.md")
    p.add_argument("--card", type=int, default=None)
    p.set_defaults(func=cmd_prompts)

    p = sub.add_parser("ingest", help="QC and approve finished art for one card")
    p.add_argument("--card", type=int, required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--in", dest="inp", help="path to a finished art file")
    src.add_argument("--url", help="download the art directly from a generator URL")
    p.add_argument("--status", default="art_approved", choices=STATUS_ORDER)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("status", help="rewrite STATUS.md from the manifest")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

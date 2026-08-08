# PRINT_SPEC — Crypto Lore Series: Genesis Series 2026

Printer handoff specification. Everything here is produced deterministically by
`generate.py`; if a number in this document disagrees with the code, the code is
wrong and must be corrected — not the artwork.

- **Set:** Crypto Lore Series — Genesis Series 2026
- **Run:** 100 cards × 100 serialized editions (10,000 cards, 200 unique faces)
- **Every card is double-sided:** unique FRONT, tier BACK

---

## 1. Geometry

| Property | Value |
|---|---|
| Trim size | 2.5 in × 3.5 in (63.5 × 88.9 mm) |
| Bleed | 0.125 in per side |
| Full-bleed size | 2.75 in × 3.75 in (69.85 × 95.25 mm) |
| Safe zone | 0.125 in inside trim on all four sides |
| Resolution | 600 DPI |
| Full-bleed pixels | 1650 × 2250 |
| Trim pixels | 1500 × 2100 (offset 75, 75) |
| Safe-area pixels | 1350 × 1950 (offset 150, 150) |
| Corner radius | Printer standard TCG corner (2.5 mm), cut after trim |

All pixel values are derived once as `round(inches × 600)`; nothing accumulates
floating-point error. Files carry 600 DPI metadata in the PNG header.

**Bleed content rule.** Source art is 1024 × 1536 (2:3). It is scaled to fit
*inside* the trim box (`fit-trim`) so no artwork is ever cut, and the bleed is
synthesized by mirror-tiling only the outer ~10 px edge strip. Inner content is
never mirrored outward. On composed cards the ornamental border band covers the
bleed entirely, so all four bleed edges are border texture by construction.

---

## 2. Files delivered per card

| File | Contents |
|---|---|
| `output/print/NNN_slug_front[_sSSS].png` | Full-bleed 1650×2250 front, RGB, 600 DPI |
| `output/print/NNN_slug_back[_sSSS].png` | Full-bleed 1650×2250 back, RGB, 600 DPI |
| `output/foil/NNN_slug_front[_sSSS].foil-gold.png` | 1-bit gold spot-foil separation |
| `output/foil/NNN_slug_front[_sSSS].foil-silver.png` | 1-bit silver spot-foil separation |
| `output/proofs/…_proof.png` | Same image + trim/safe overlay — **proofing only, never print** |
| `output/pdf/GenesisSeries_PRINT_singles.pdf` | One card per page, page size exactly 2.75 × 3.75 in |
| `output/pdf/GenesisSeries_PROOF_sheet.pdf` | US Letter, 3×3 trim-size cards with crop marks — proofing only |

`_sSSS` is the edition serial (`_s007` = edition 007/100). A file with no
serial suffix is a proof render and prints `EDITION PROOF` in the footer rather
than a fabricated number.

**Foil separations are authoritative.** They are rendered from the same draw
calls as the card face, not traced from the image, so registration is exact to
the pixel. White (1) = apply foil, black (0) = no foil. The `foil --in` command
performs an independent HSV-threshold pass on a finished image; use it for
verification only, never as the plate.

---

## 3. Stock and finish

- **Stock:** 350 gsm black-core casino board (blue-core acceptable substitute),
  linen or superior smooth finish, opacity ≥ 99 %.
- **Lamination:** matte soft-touch on the face; gloss UV is out of spec — the
  holo border supplies the shine.
- **Cut:** trim then round-corner die, 2.5 mm radius.
- **Registration tolerance:** ±0.5 mm. All type sits ≥ 0.125 in inside trim, so
  a worst-case shift never clips copy.

### Per-tier finish

| Tier | Cards | Count | Finish |
|---|---|---|---|
| Mythic | 001–004, 100 | 5 | Rainbow holographic border, textured gold foil (title, crest, corner lozenges, band hairlines, ability names, serial), embossed sigil on the back rosette (0.3 mm emboss), gold foil serial |
| Epic | 005–014 | 10 | Full-card holographic overlay, gold foil title, crest, band hairlines and ability names |
| Rare | 015–034 | 20 | Gold foil title and rarity crest; band hairlines foiled; no full-card holo |
| Uncommon | 035–064 | 30 | **Silver** foil title and crest; standard holographic border only |
| Common | 065–099 | 35 | Standard print, holographic border only, no spot foil (no foil separation is emitted for this tier) |

Gold foil reference: warm antique gold, ~PMS 8384 C metallic.
Silver foil reference: cool bright silver, ~PMS 877 C metallic.

### Serialization

Each of the 100 editions is numbered `001/100`–`100/100`. The edition serial
prints in the footer right of both faces. On Mythic cards the serial is part of
the gold foil separation; on all other tiers it prints flat. Card number
(`No. NNN / 100`) prints flat on every tier.

---

## 4. QC gates before a card is marked `print_ready`

1. **Proof overlay** — zero text or critical art outside the dashed blue safe
   zone. The pipeline asserts every layout box against the safe rectangle at
   import time and refuses to render if one escapes.
2. **100 % zoom pass** — no garbled or AI-mangled lettering anywhere in the art.
   All type on the card is set by the pipeline from the manifest; any lettering
   in the art itself must be proofed letter-by-letter or removed.
3. **Bleed zone** — border texture only, all four sides, no subject matter.
4. **Serial + card number** render exactly as the manifest states.
5. `python generate.py validate` passes clean.
6. Rarity finish notes for that tier recorded in this document (section 3).

Placeholder art can never pass gate 2: it is stamped PLACEHOLDER ART — NOT FOR
PRINT across the face and `CARD NNN - NOT FOR PRINT` inside the art window.

---

## 5. Regenerating everything

```bash
pip install -r requirements.txt
python generate.py validate      # must pass clean first
python generate.py templates     # blank frames, one front + back per tier
python generate.py all           # compose + proof + PDFs for everything with approved art
```

Add `--allow-placeholder` to `all` to render the complete set against stand-in
art for layout review. Output is byte-reproducible: all texture randomness is
seeded by card number and rarity, and no wall-clock or system entropy is used.

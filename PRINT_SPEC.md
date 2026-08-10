# PRINT_SPEC.md — Crypto Lore Series, Genesis Series 2026

Printer handoff specification. Everything here is produced deterministically by
`generate.py` from `set_manifest.json`; nothing is hand-placed.

## 1. Product

| | |
|---|---|
| Set | Crypto Lore Series — Genesis Series 2026 (`CLS-GEN`) |
| Cards | 100 unique, each printed in 100 serialised editions |
| Serial | `XXX/100`, printed on both faces, bottom right |
| Card number | `NNN/100`, bottom left |
| Faces | Every card has a distinct FRONT and BACK |

## 2. Geometry

| | Inches | Pixels @ 600 DPI |
|---|---|---|
| Trim | 2.500 × 3.500 | 1500 × 2100 |
| Bleed (per side) | 0.125 | 75 |
| **Full bleed (supplied artwork)** | **2.750 × 3.750** | **1650 × 2250** |
| Safe zone inset from trim | 0.125 | 75 |
| Safe box (in full-bleed coords) | — | (150, 150) → (1500, 2100) |

- Corner radius: **3 mm**, cut by the finisher. No corner marks are printed.
- All pixel values are integers derived once as `inches × DPI` and rounded a
  single time. No accumulated float geometry.
- Supplied PNGs embed 600 DPI in `pHYs`. PNG stores pixels-per-metre as an
  integer, so readers report **599.9988 DPI** — this is the exact encoding of
  600 DPI and is not a discrepancy.
- Singles PDF pages are exactly 2.750 × 3.750 in, one card face per page.

## 3. Colour and ink

- Supplied as RGB PNG. Convert to the press profile at prep; the palette is
  deliberately narrow (black, bronze, oxidised gold, ember orange, bone white)
  and holds up through a standard CMYK conversion.
- Maximum total ink coverage is not exceeded — the darkest field is a rich
  black built from the art, not a 400% flood.
- The card body is intentionally near-black. Please do **not** apply automatic
  levels, contrast or "auto-enhance" to any supplied file.

## 4. Finishes by rarity tier

Rarity is a **finish tier, not a pull rate** — every complete set contains all
100 cards. Spot-foil masks are supplied as 1-bit PNGs at 600 DPI, registered
to the same 1650 × 2250 canvas (`output/foil/`).

| Tier | Count | Finish |
|---|---|---|
| Mythic | 5 | Rainbow holographic border, textured gold foil, embossed sigil, gold serial |
| Legendary | 10 | Near-Mythic holographic border + textured gold foil title and crest |
| Epic | 20 | Full holographic overlay + gold foil title and icons |
| Rare | 30 | Gold foil title + foil rarity crest |
| Common | 35 | Standard print + holographic border |

Tier assignment is per card in `set_manifest.json`, not by number range; the
manifest is the authoritative tier list for the finisher.

Notes for the finisher:

- **Embossing (Mythic only):** the back sigil, centred on the lower panel.
  Registration to the printed sigil outline, light depth — this is a texture
  cue, not a relief.
- **Foil masks** cover the engraved border, the title, the rarity crest and the
  serial. Typical coverage is 15–25% of the card face; `generate.py foil`
  reports exact coverage per file.
- The holographic border must stay **within the gold family**. The artwork is
  built so the foil reads as metal; a high-refraction rainbow film would fight
  it. Sample before running the edition.

## 5. Stock and construction

- 350 gsm black-core card stock, matte varnish on the art field, gloss only
  where foiled.
- Target caliper 0.32 mm ± 0.02 mm.
- Black core is required: these cards are near-black to the edge and any light
  core will show as a bright line at the trim.

## 6. QC gates (all must pass before a card is `print_ready`)

1. Proof overlay shows zero text or critical art outside the dashed safe zone.
2. 100% zoom pass: no garbled or AI-mangled lettering anywhere on the card.
3. Bleed zone is border texture only, on all four sides.
4. Serial and card number render exactly as the manifest states.
5. `python generate.py validate` passes clean.
6. The finish notes above are confirmed for that card's tier.

## 7. Deliverables

```
output/print/   1650×2250 PNG, 600 DPI, one per face per serial
output/proofs/  same + red trim rule and dashed blue safe zone (proofing only,
                never send these to plate)
output/foil/    1-bit spot-foil masks, same canvas
output/pdf/     GenesisSeries_PRINT_singles.pdf  (2.75×3.75in pages)
                GenesisSeries_PROOF_letter.pdf   (3×3 letter proof sheet)
```

Regenerate everything from a clean checkout with:

```
pip install -r requirements.txt
python generate.py all
```

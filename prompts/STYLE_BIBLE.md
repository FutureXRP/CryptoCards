# Style bible — Crypto Lore Series: Genesis Series 2026

Every prompt in this directory inherits these rules. Cards #001–004 are the
reference look; anything that does not sit beside them on a table is wrong.

## The look

Dark epic fantasy meets financial mythology. Oil-painted, museum-plate
rendering — painterly brushwork, visible impasto in the highlights, never
photoreal and never 3D-render clean.

- **Palette:** black, bronze, oxidised gold, ember orange, bone white. Cool
  accents only as contrast (verdigris, cold blue rim light) and never more
  than a tenth of the frame.
- **Light:** single dramatic key with hard rim lighting, deep falloff into
  black. Volumetric haze in the mid-ground. The subject emerges from darkness.
- **Composition:** centred, iconic, frontal or three-quarter. Portrait 2:3.
  The subject occupies the upper two thirds; the lower third stays quiet and
  low-contrast — the pipeline lays stat and ability plates over it.
- **Ornament:** engraved gold filigree, ledger geometry, chain and seal
  motifs, parchment, wax, cracked lacquer, opalescent iridescence at the
  extreme edges.
- **Texture:** aged varnish, fine canvas grain, gilt flaking at the edges.

## Hard rules

1. **No text.** No words, letters, numerals, glyph-like lettering, runes that
   resemble script, watermarks, or signatures. AI lettering killed the first
   Whale front. All type is set by `generate.py` from `set_manifest.json`.
   Incidental in-scene signage is the only exception and must be proofed
   letter-by-letter before approval.
2. **No real people**, no celebrity likeness, no recognisable public figures.
3. **No real logos, brand marks, ticker symbols, or token logos.** Coins in
   frame are blank, sigil-stamped, or archaic.
4. **No existing IP characters** or costumes traceable to one.
5. **Archetypes and symbols only.** These are myths about behaviour, not
   depictions of companies.
6. **Output 1024 × 1536 (2:3), no border, no frame drawn in the art** — the
   frame is a pipeline template. Art must read edge-to-edge; the outermost
   ~10 px is what gets mirrored into the bleed, so keep it dark and quiet.

## Composition zones (why the lower third stays calm)

At 600 DPI the pipeline scales the art to the trim box and lays plates over
it. Anything vital below roughly 70 % of the image height will be covered.

| Zone | Image height | Treatment |
|---|---|---|
| Crown | 0–18 % | Covered by the title plate — atmosphere only |
| Subject | 18–52 % | The face, the icon, the moment. Highest contrast here |
| Mid | 52–70 % | Supporting scene, falling off into shadow |
| Base | 70–100 % | Quiet, dark, low detail — plates sit here |

## Prompt anatomy

`generate.py prompts` fills these templates from the manifest:

    prompts/tier_<rarity>.md      frame, finish and grandeur for that tier
    prompts/category_<category>.md  what the subject fundamentally *is*
    prompts/cards/NNN_slug.md     generated per card — do not hand-edit

Regenerate after any manifest change:

    python generate.py prompts

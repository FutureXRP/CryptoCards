# Prompt system

A finished art prompt is assembled from three parts, in this order:

1. `_base.md`          — the style bible. Identical on every card in the set.
2. `tier_<rarity>.md`  — how much ornament and light the tier carries.
3. `category_<cat>.md` — what kind of subject this is and how it is staged.

The card-specific scene beats (2–3 of them) are derived from that card's
`lore` in `set_manifest.json` and dropped into the `{SCENE}` slot.

Rendered prompts are checked in under `cards/NNN_slug.md` so that any art in
`art/approved/` can be traced back to the exact text that produced it.

## Hard rules

- **No text in the art.** All type is set by `generate.py` from the manifest.
  AI lettering is banned — garbled words killed the first Whale front.
- No real people, no real logos or brand marks, no existing IP characters,
  no real token logos. Archetypes and symbols only.
- The outer ~10 px of the image must stay dark and undisturbed: `prep`
  synthesises bleed by symmetric-tiling that strip, and `compose` crops the
  art into the frame's window.
- Source art is generated 2:3 so it drops into the art window without
  distortion.

## Model settings that produced the approved set

`nano_banana_2`, `aspect_ratio: 2:3`, `resolution: 2k` (1696×2528), 2 credits
per image. Generate 2 variants and keep the one with the cleanest edges.

# CLAUDE.md — Crypto Lore Series: Genesis Series 2026

## Mission

Design and produce a 100-card premium physical trading card set ("Crypto Lore Series — Genesis Series 2026"). Only 100 serialized sets will ever be printed. Every card must be unique in content but perfectly consistent in pattern, and every output file must be print-ready at collector grade: 2.5"×3.5" trim, full bleed, 600 DPI. The quality bar is the premium end of the TCG market (Magic collector boosters, PSA-slab product). Ship-first-then-iterate, but never ship a card that fails the QC gates below.

## Product definition

- Fixed 100-card set × 100 serialized editions. Every card carries `XXX/100`.
- Rarity = physical finish tier, not pull odds (every set contains all 100 cards):

| Tier | Count | Finish |
|---|---|---|
| Mythic | 5 | Rainbow holo border, textured gold foil, embossed sigil, gold serial |
| Legendary | 10 | Near-Mythic holo + textured gold foil title and crest |
| Epic | 20 | Full holo overlay + gold foil title/icons |
| Rare | 30 | Gold foil title + foil rarity crest |
| Common | 35 | Standard print + holo border |

- Tier assignment is **per card in the manifest**, not by number range — the set's icons are scattered through the checklist. `validate` enforces the counts.
- Cards #001–004 (The HODLR, The SEC Punisher, The Whale, The Rug Puller) have finished approved art in `art/approved/`. Match their look exactly.
- Card #100 "The Crypto King — Survivor of Every Cycle" is the set-closing Mythic chase card.
- Every card has a FRONT (stats/abilities frame) and a BACK (lore/traits frame).

## Repo structure

```
/CLAUDE.md                  ← this file (persistent project context)
/set_manifest.json          ← single source of truth for all 100 cards
/generate.py                ← deterministic production pipeline (Python + Pillow + numpy + reportlab)
/prompts/                   ← AI art prompt templates, one per rarity tier + per category
/art/approved/              ← finished, human-approved 1024×1536 card art
/art/incoming/              ← new AI art awaiting QC
/art/templates/             ← text-free blank frame templates, one front + one back per tier
/fonts/                     ← Cinzel, Cinzel Decorative, EB Garamond (+ Italic)
/output/print/              ← full-bleed 600 DPI print PNGs (gitignored)
/output/proofs/             ← trim/safe-zone overlay proofs (gitignored)
/output/foil/               ← 1-bit spot-foil masks (gitignored)
/output/pdf/                ← printer handoff PDFs (gitignored)
/PRINT_SPEC.md              ← printer handoff spec (geometry, stock, finishes, QC)
/STATUS.md                  ← live progress tracker: per-card state machine
```

## The single source of truth: set_manifest.json

All card content lives in `set_manifest.json`. Never hardcode card text in Python. Schema per card:

```json
{
  "num": 16,
  "name": "The Degen",
  "subtitle": "Fortune Favors the Reckless",
  "type": "Gambler",
  "alignment": "Chaos",
  "rarity": "rare",
  "category": "archetype",
  "status": "lore_complete",
  "stats": [
    {"label": "Risk Appetite", "value": 100},
    {"label": "Speed", "value": 97},
    {"label": "Conviction", "value": 88},
    {"label": "Timing", "value": 41},
    {"label": "Survival", "value": 63}
  ],
  "abilities": [
    {"name": "Full Send", "kind": "active", "text": "Enters any position at maximum size, ignoring all risk signals."},
    {"name": "Shrug It Off", "kind": "passive", "text": "Losses do not reduce conviction. Ever."},
    {"name": "Generational Trade", "kind": "ultimate", "text": "Once per cycle, turns pocket change into legend."}
  ],
  "weakness": "Risk management. All of it.",
  "lore": "2–4 sentences, back-of-card register.",
  "traits": [{"label": "Risk Appetite", "text": "No position too large, no odds too long."}],
  "flavor": "Scared money makes none.",
  "art_front": "art/approved/016_the_degen_front.png",
  "art_back_template": "art/templates/back_rare.png"
}
```

`status` state machine (mirror it in STATUS.md): `named → lore_complete → art_prompted → art_incoming → art_approved → composed → proofed → print_ready`.

## Division of labor (hard rule)

- **Claude (language):** card names, lore, traits, abilities, flavor text, art prompts, docs. Nothing else.
- **Deterministic code (generate.py):** ALL geometry, scaling, bleed math, serial numbering, layout coordinates, foil masks, PDFs. Integer/exact arithmetic for all print geometry (pixels computed from inches × DPI, rounded once, never accumulated floats). Any randomness (e.g., texture jitter) uses a seeded RNG with the card number as seed, so re-runs are byte-reproducible.
- **Fabrication firewall:** never invent stats, serials, or card numbers at render time — everything renders from the manifest. If a manifest field is missing, the pipeline must FAIL LOUDLY, not fill in a default.

## Print geometry (do not change without updating PRINT_SPEC.md)

- Trim: 2.5" × 3.5" (63.5 × 88.9 mm). Bleed: 0.125" per side → full-bleed 2.75" × 3.75".
- Safe zone: 0.125" inside trim. ALL text and critical art stays inside it.
- Master canvas: 600 DPI → 1650 × 2250 px full bleed, 1500 × 2100 trim, 75 px bleed, 75 px safe inset.
- Source AI art is 1024×1536 (2:3). Strategy `fit-trim`: scale art to fit fully inside the trim box (nothing is ever cut), synthesize bleed by symmetric-tiling ONLY the outer ~10 px holo-border strip (numpy `pad(mode="symmetric")` on edge strips; never mirror inner content into bleed).
- Output PNGs embed DPI metadata; singles PDF pages are exactly 2.75" × 3.75".

## generate.py CLI contract

```
python generate.py compose --card N [--serial S | --all-serials]   # manifest → finished card image
python generate.py prep   --in PATH [--strategy fit-trim|cover]    # finished art → full-bleed print file
python generate.py proof  --in PATH                                 # + red trim / dashed blue safe overlay
python generate.py foil   --in PATH                                 # 1-bit gold spot mask (HSV threshold)
python generate.py sheet                                            # singles PDF + letter proof sheet
python generate.py all                                              # batch everything in art/approved/
python generate.py validate                                         # manifest lint: schema, counts, duplicates
```

`validate` must check: exactly 100 cards, numbers 1–100 unique and contiguous, rarity counts match the tier table, no duplicate names, every non-`named` card has all fields required by its status, all referenced art files exist.

## Art direction (for prompt authoring)

- Style bible: dark epic fantasy meets financial mythology. Ornate engraved gold frames, black/bronze palette, opalescent holographic border, parchment inset panels, dramatic rim lighting, painterly not photoreal. Match cards #001–004.
- Prompt templates live in `/prompts/` — one per tier (border/frame treatment differs) and per category (archetype/force/relic/realm/token). When generating a new card's prompt, fill the template with the card's name, subtitle, and 2–3 scene beats derived from its lore.
- **Generate art WITHOUT text.** All type is set by the pipeline from the manifest. AI-rendered lettering is banned in new art (garbled words killed the first Whale front). The only text allowed in AI art is incidental in-scene signage, and it must be proofed letter-by-letter.
- Owner's call (2026-08-10): the checklist deliberately includes real assets and historic events (The Bitcoin, The XRP, The Satoshi, Mt. Gox, FTX, Silk Road…). Card NAMES may reference them; card ART stays archetypal — no trademarked logo lockups, no real people's likenesses, no exchange branding. Symbols (₿-style coin marks) are allowed as the owner's reference cards use them.

## Writing style (lore, traits, abilities, flavor)

- Voice: mythic, wry, economical. The set is dark satire of crypto culture that still flatters the collector. Second person never; present tense preferred.
- Lore: 2–4 sentences. Traits: exactly 4, label ≤ 2 words + one clause. Flavor: one line, quotable, ≤ 10 words. Abilities: name ≤ 3 words; text ≤ 20 words; exactly one active, one passive, one ultimate.
- Banned in copy: "delve", "unleash your", "in the world of", "game-changer", "revolutionary", "to the moon" (except as deliberate irony on The Moonboy), any price predictions or investment claims. Real project/event names appear only where the card itself is named for them. This is lore, not financial advice — keep it archetypal.
- Stats are 0–100 integers chosen for character truth, not balance math; exactly 5 per card; at most one stat of 100.

## Claude Code session protocol

- Batch work: design cards in blocks of 10 (one category/tier block per session where possible).
- A "design session" = for each card: write lore/traits/abilities/flavor/stats into the manifest, write its art prompt file, advance `status`, update STATUS.md — then run `python generate.py validate` and fix anything it flags.
- A "production session" = QC incoming art (move approved files to `art/approved/` with `NNN_slug_front.png` naming), run `compose`/`prep`/`proof` for the block, visually check every proof (text inside safe zone, no garbled letters, bleed is pure border texture), advance statuses.
- Complete file replacements over surgical patches when editing the manifest or generate.py.
- ONE batch commit per session. Message format: `cards 015-024: lore + prompts complete` or `pipeline: <change>`. Never commit `/output/`.
- Never mark a card `print_ready` without a generated proof having been visually inspected in the session.
- If a request would require inventing data not in the manifest, stop and add it to the manifest first.

## QC gates before print_ready

1. Proof overlay: zero text/critical art outside the dashed safe zone.
2. 100% zoom pass: no garbled or AI-mangled lettering anywhere.
3. Bleed zone: border texture only, all four sides.
4. Serial + card number render exactly as manifest states.
5. `validate` passes clean.
6. Rarity finish notes for the printer recorded in PRINT_SPEC.md for that tier.

## Definition of done

100/100 cards `print_ready`; `GenesisSeries_PRINT_singles.pdf` regenerates deterministically from the repo in one command (`python generate.py all`); PRINT_SPEC.md current; a stranger with this repo and no context could reprint the entire set identically.

---

## Operating notes (added in production)

### Environment

This repo is developed from Claude Code on the web. That container has **no
PyPI and no apt access**, so Pillow / numpy / reportlab cannot be installed
locally. Consequences:

- `python generate.py validate` runs on the standard library alone and works
  everywhere. Run it after every manifest edit.
- Every **image/PDF** command needs the dependencies in `requirements.txt`.
  They run on any normal machine (`pip install -r requirements.txt`), and in
  the Higgsfield sandbox, which has Pillow + numpy + ImageMagick preinstalled.
- Sandbox render loop: push the branch, then in the sandbox
  `git clone --depth 1 -b <branch> https://github.com/FutureXRP/CryptoCards.git`
  and run `generate.py` there. The repo is public, so no credentials are
  involved. To look at a result, request a `media_upload` URL, `PUT` the PNG
  from the sandbox, and fetch it from the returned CDN URL.

### Fonts

`fonts/` holds Cinzel, Cinzel Decorative and EB Garamond (variable TTFs) with
their OFL licences. Google Fonts is unreachable from the container; they were
pulled from the `google/fonts` GitHub mirror. Do not delete them — `compose`
fails loudly without them.

### Art generation

Higgsfield, `nano_banana_2` (the batch endpoint may serve `nano_banana_flash`),
`2:3`, `2k` → 1696×2528, 2 credits per image. The prompt is assembled per
`prompts/README.md`. Generate 2 variants, keep the one with the calmest edges.

The paintings come back with a painted canvas edge; `place_art` trims 2.5% off
each side before fitting so it never reads as a second frame.

### Frame design decisions

- The holo tint is a **narrow-gamut warm gain**, not a spectrum. All three
  channels share one phase so the hue can only travel gold ↔ bronze. An early
  full-spectrum version printed as plastic candy stripe.
- The abilities block **measures itself and shrinks** before drawing, so text
  can never cross into the footer regardless of how long the copy is.
- Ability markers and the back sigil are **drawn geometry**, not glyphs —
  Cinzel has no ◆ ○ ✦ and rendered them as tofu.

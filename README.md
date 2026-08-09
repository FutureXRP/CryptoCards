# Crypto Lore Series — Genesis Series 2026

A 100-card premium physical trading card set. 100 serialized editions, no
reprints. Every card is double-sided, full-bleed, 600 DPI, print-ready.

This repository is the whole factory: the card content, the art prompts, the
typefaces, and a deterministic pipeline that turns them into files a printer can
run. Nothing about a card is decided at render time — the manifest decides.

```
set_manifest.json  →  generate.py  →  output/print, output/foil, output/pdf
```

## Quick start

```bash
pip install -r requirements.txt

python generate.py validate                       # lint the 100-card manifest
python generate.py templates                      # build the blank tier frames
python generate.py compose --card 16 --serial 7   # one finished card, both faces
python generate.py all --allow-placeholder        # the whole set + proofs + PDFs
```

`--allow-placeholder` renders against stamped stand-in art so layout can be
reviewed before any art exists. Those renders are marked
`PLACEHOLDER ART — NOT FOR PRINT` and can never pass QC.

## What is where

| Path | What it is |
|---|---|
| `CLAUDE.md` | Persistent project context: rules, division of labor, session protocol |
| `build.md` | The original brief, kept verbatim |
| `set_manifest.json` | **Single source of truth.** All 100 cards: stats, abilities, lore, traits, flavor |
| `generate.py` | The entire pipeline — geometry, typesetting, foil masks, PDFs |
| `PRINT_SPEC.md` | Printer handoff: geometry, stock, per-tier finishes, QC gates |
| `STATUS.md` | Generated per-card progress table |
| `prompts/` | Style bible, one prompt template per tier and per category |
| `prompts/cards/` | Generated per-card art prompts (regenerate, don't hand-edit) |
| `art/templates/` | Text-free blank frames, one front + back per rarity tier |
| `art/approved/` | Human-approved 1024×1536 card art (the remaining work) |
| `fonts/` | Cinzel, Cinzel Decorative, EB Garamond — vendored, OFL |

## The two rules that shape everything

**Division of labor.** Language (names, lore, traits, abilities, flavor,
prompts) is written by hand into the manifest. Everything measurable — bleed
math, layout coordinates, serial numbering, foil separations, PDF page sizes —
is computed by `generate.py` in integer arithmetic, with all texture randomness
seeded by card number so re-runs are byte-identical.

**Fabrication firewall.** The renderer never invents a value. A missing
manifest field raises, with the card number and the field name, instead of
falling back to a default. If art is missing, `compose` refuses unless you
explicitly ask for a stamped placeholder.

## Pipeline commands

| Command | Does |
|---|---|
| `validate` | Manifest lint: counts, tier ranges, duplicates, per-status required fields, writing-style limits, banned copy, asset existence |
| `templates` | Synthesizes the 10 blank frames (front + back × 5 tiers) and their foil separations |
| `prompts` | Manifest + templates → `prompts/cards/*.md` |
| `artgen` | Deterministic placeholder art, stamped NOT FOR PRINT |
| `ingest --card N` | QC finished art (`--in PATH` or `--url URL`) and approve it into `art/approved/` |
| `compose --card N` | Manifest → finished front and back, plus exact foil separations |
| `prep --in PATH` | Finished art → full-bleed canvas (`fit-trim` or `cover`) |
| `proof --in PATH` | Adds red trim / dashed blue safe-zone overlay |
| `foil --in PATH` | Independent HSV-threshold foil mask, for verification |
| `sheet` | Singles PDF (2.75 × 3.75 in pages) + US Letter 3×3 proof sheet |
| `all` | validate → templates → compose + proof everything → PDFs |
| `status` | Rewrites `STATUS.md` from the manifest |

## The art loop

Per card, once its image has been generated from `prompts/cards/NNN_slug.md`:

```bash
python generate.py ingest  --card 1 --url https://…/generated.png   # or --in PATH
python generate.py compose --card 1 --serial 1
python generate.py proof   --in output/print/001_the_hodlr_front_s001.png
```

`ingest` refuses art that is not 2:3 or is smaller than 1024×1536, writes it
under the canonical name, and advances the card's status. Nothing is renamed,
moved, or resized by hand.

### Network access for `--url`

`--url` needs the image host reachable from the session. Cloud sessions default
to **Trusted** network access, which allows package registries and GitHub and
nothing else — an image CDN is blocked, and the session will say so rather than
half-finish. To fix it, edit the cloud environment at
[claude.ai/code](https://claude.ai/code): set **Network access** to **Custom**,
add the CDN host to **Allowed domains** (a leading `*.` matches subdomains),
leave *Also include default list of common package managers* checked, and start
a new session — a running session keeps the policy it started with.

## State of the set

All 100 cards are written, validated, and prompted — `status: art_prompted`.
The pipeline runs end to end and produces both PDFs. What remains is art:
generate each card's image from its prompt, then run the loop above.

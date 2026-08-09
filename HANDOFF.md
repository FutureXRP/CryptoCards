# HANDOFF — Crypto Lore Series, Genesis Series 2026

Written 2026-08-09. Read this first, then `CLAUDE.md`.
Branch: `claude/handoff-build-review-h1zi8n` (public repo, all work pushed).

---

## 0. Where the design landed (owner-approved direction)

The design target is the owner's reference cards. As of this session:

- **Front:** full-bleed art, prismatic opal foil edge, huge beveled gold title
  with a warm bloom, five colour-coded stat chips, three medallion ability
  columns (medallion left, copy right), badge system, weakness bar, flavor +
  market lore, footer. Owner reviewed and approved the look.
- **Back:** an **ornate template plate — no character art**. The owner
  explicitly does not want the figure repeated on the back. The plate is a
  text-free AI painting in `art/templates/back_<tier>.png`; all type is set by
  the pipeline over it.
- Both faces carry a diagonal gloss `sheen()` so the card reads as laminated
  stock ("make it shine a bit more").

### The back layout is band-based

`BACK_BANDS` in `generate.py` places the title on the plate's engraved
nameplate, the lore parchment on the gold shield and the traits on the open
starfield below it, as fractions of the field inside the foil edge. **These
fractions are tuned to the shipped template — regenerating a plate means
re-checking them.** Copy length is absorbed by auto-shrinking type, so the
bands hold for any card.

### Two rendering traps, both hit and fixed

1. `ImageDraw` on an RGBA layer **writes the source alpha** rather than
   accumulating it. Drawing a translucent sheen over an opaque fill punched the
   stat chips full of holes and the art showed through the labels. Chips are now
   an opaque gradient pasted through a rounded mask. Do not reintroduce
   translucent overdraw on a panel that must stay opaque.
2. The opal foil went through neon-static and candy-stripe failures. It is now
   upsampled noise (not sinusoids) at a **low saturation** with a high value
   floor. Saturation is the dial that ruins it — leave it low.

---

## 1. Where the work actually stands


### Done and working

| Thing | State |
|---|---|
| `set_manifest.json` | All 100 cards exist, numbered 1–100, tier counts exact, no duplicate names. Cards 1–14 and 100 have full content (lore, 5 stats, 3 abilities, 4 traits, weakness, flavor). Cards 15–99 are `named` only. |
| `generate.py validate` | Passes clean, 0 warnings. Runs on the stdlib alone — no Pillow needed. |
| `generate.py compose` | Renders front + back at 1650×2250 @ 600 DPI. Works. |
| `prep` / `proof` / `foil` / `sheet` / `all` | Implemented. `proof` and `foil` verified on real output; `sheet` (PDF) is **implemented but never executed** — see §4. |
| Fonts | Cinzel, Cinzel Decorative, EB Garamond + Italic in `fonts/`, with OFL licences. Committed. |
| Art | Cards 001–004 have approved 1696×2528 paintings in `art/approved/`, all text-free, all visually QC'd at full size. `art/templates/back_mythic.png` is the approved back plate; the other four tiers are not generated yet. |
| Prompts | `prompts/` has base + 5 tier + 5 category templates, plus per-card records for 001–004. |
| Docs | `CLAUDE.md`, `PRINT_SPEC.md`, `STATUS.md`. |
| Permissions | `.claude/settings.json` allows all Higgsfield MCP tools. Committed, so a **fresh session will not prompt**. |

### The renderer is ~85% there, not finished

The current layout matches the reference structurally. Confirmed fixed:
title clears the badges, no text overflows any panel, abilities auto-shrink,
flavor/lore auto-shrink, bottom stack is measured upward from the footer so it
can never collide.

**Still not right — this is the top of the next session's list:**

1. **The opal foil edge.** It has gone through three passes (garish rainbow →
   muted gold, wrong → pastel marble → fine grain). The last change tightened
   the grain a lot but **the result was never visually reviewed** — the render
   completed and uploaded, but the session ended before anyone looked at it.
   Look at it first. Reference is a tight, glittery, high-frequency prismatic
   sparkle with white highlights, not a soft swirl.
2. **Card art is cropped tight.** `place_art` uses a 0.20 vertical bias; the
   HODLR's lower body is cut by the panel edge. Consider a taller art window or
   a per-card crop bias field in the manifest.
3. **Back face never reviewed** in its current form. It renders without error;
   nobody has looked at the latest version.
4. **Content mismatch with the references.** The owner's cards use different
   stat labels and subtitles (e.g. `DIAMOND HANDS ETERNAL`, `LEGENDARY HUMAN`,
   `CHAOS RESISTANT`, stats PATIENCE / RESILIENCE / LONG-TERM POWER / BELIEF /
   EMOTIONAL CONTROL). The manifest currently holds different, independently
   written copy. **Ask the owner which wording wins** before mass-producing.

---

## 2. The environment (this is the part that wastes time — read it)

The web container is heavily restricted:

- **No PyPI, no apt, no Google Fonts, no raw.githubusercontent.** All 403.
  You cannot `pip install pillow` here. Do not try.
- **GitHub is reachable.** `github.com`, `codeload`, and the Higgsfield CDN work.
- Therefore `validate` runs locally, and **every image command must run in the
  Higgsfield sandbox**.

### The render loop that works

```
1. commit + push the branch
2. mcp__Higgsfield__sandbox_exec:
     git clone -q --depth 1 -b <branch> https://github.com/FutureXRP/CryptoCards.git /home/user/cc
     cd /home/user/cc && python3 generate.py compose --card N --serial 1 --face both
     convert output/print/<file>.png -resize 760x f.png
3. mcp__Higgsfield__media_upload  -> get presigned upload_url + a CDN url
4. in the SAME sandbox command: curl -f -X PUT --upload-file f.png "<upload_url>"
5. locally: curl the CDN url, then Read the PNG to actually look at it
```

The repo is **public**, so the sandbox clones with no credentials. The sandbox
is discarded ~10s after each call — chain everything into one command.

Get the upload URLs *before* the sandbox call, since you must PUT from inside it.

---

## 3. Higgsfield art generation

- Model `nano_banana_2`, `aspect_ratio: 2:3`, `resolution: 2k` → 1696×2528.
- **2 credits per image.** Balance was 1028 at the end of this session
  (~500 images of headroom). Cards 002–004 cost 6 credits total.
- `generate_image_batch` may silently serve `nano_banana_flash`. Output quality
  was still good, but note it.
- The full working prompt formula is in `prompts/_base.md` and the four
  per-card files in `prompts/cards/`. It reliably produces text-free painterly
  art with dark, calm edges. **Reuse it — do not reinvent it.**
- Paintings arrive with a painted canvas edge; `place_art` trims 2.5% per side.

---

## 4. Known gaps / things I did not verify

Being explicit so nothing is taken on trust:

- **`generate.py sheet` has never been run.** reportlab is not installable
  locally and the PDF step was never executed in the sandbox. It is written but
  unproven. Run it before believing the "one command regenerates everything"
  claim in the definition of done.
- **`generate.py all` has never been run end-to-end.**
- **No card is `print_ready`.** Cards 001–004 are `art_approved`. Nothing has
  passed the full QC gate, because the design was still moving.
- The `prep` command's `fit-trim` path is implemented per the original spec but
  is now largely vestigial — `compose` does its own full-bleed placement.
- PNG DPI reads back as 599.9988; that is PNG's integer pixels-per-metre
  encoding of exactly 600 DPI, not a bug. Noted in `PRINT_SPEC.md`.

---

## 5. Suggested next session

1. Render card 001 front + back, **look at both**, and finish the opal edge.
2. Show the owner one finished card against their reference and get a yes.
3. Resolve the copy question in §1.4.
4. Then, and only then, scale: write content for cards 015–024, generate art in
   batches, and run `all` + `sheet` for the first time.

Do not generate 100 cards' worth of art until one card is signed off. Art is
cheap (2 credits) but re-doing 100 of them is not.

---

## 6. Commit log this session

```
pipeline: manifest, generate.py, fonts, card 001 art
pipeline: subtler foil, overflow-safe abilities, back layout fixes
pipeline: constrain foil hue to the gold family        <- later reverted in spirit
cards 001-004: art approved + prompt system, docs, print spec
render: rebuild card layout to match approved Genesis design
render: pearlescent foil, badge-safe title, overflow-proof bottom stack
config: allow all Higgsfield MCP tools without prompting
render: fine-grain prismatic foil edge                 <- NOT visually reviewed
```

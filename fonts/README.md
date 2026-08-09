# Fonts

Three families, all SIL Open Font License 1.1, vendored so the set renders
identically on any machine. Licence texts are next to the font files.

| File | Used for |
|---|---|
| `Cinzel[wght].ttf` | Card names, stat labels, ability names, headers, footers (variable weight, 400–900) |
| `CinzelDecorative-Regular/Bold/Black.ttf` | Reserved for ornamental display use |
| `EBGaramond[wght].ttf` | Lore, ability text, trait text |
| `EBGaramond-Italic[wght].ttf` | Subtitles, flavor text, weakness line |

Sources (upstream, unmodified):

- Cinzel — https://github.com/google/fonts/tree/main/ofl/cinzel
- Cinzel Decorative — https://github.com/google/fonts/tree/main/ofl/cinzeldecorative
- EB Garamond — https://github.com/google/fonts/tree/main/ofl/ebgaramond

`generate.py` loads these by path and **fails loudly** if one is missing — it
will not silently substitute a system font, because a substituted face changes
every line break and therefore every layout.

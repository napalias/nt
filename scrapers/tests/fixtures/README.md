# Spider test fixtures

Saved pages the spider tests parse instead of the live site. They carry no contact data.

| File | Source | Fetched |
|------|--------|---------|
| `domoplius_index_kretinga_p1.html` | `https://domoplius.lt/skelbimai/namai-kotedzai-sodai/kretinga` (page 1, 20 listings) | 2026-11-15 |
| `domoplius_detail_house.html` | `https://domoplius.lt/skelbimai/parduodamas-gyvenamasis-namas-kretingos-r-sav-kretingoje-8816944.html` | 2026-11-15 |
| `alio_index_kretinga_p1.html` | `https://www.alio.lt/paieska/kretingos-r-sav/?category_id=1433` (page 1, 24 cards) | 2026-11-16 |
| `alio_detail_house.html` | a garden house in Daubėnai, `.../skelbimai/sodo-namas-kretingos-r.-sav.-daubenai/ID69851715.html` (no map pin) | 2026-11-16 |
| `alio_detail_house_part_pin.html` | a part of a house in Žutautai (`ID69532877`; has a map pin the page calls `city` precision, no description) | 2026-11-16 |
| `skelbiu_index_kretinga_p1.html` | `https://www.skelbiu.lt/paieska/namai-kretingoje/` (page 1, 24 cards, mixed ad types) | 2026-11-16 |
| `skelbiu_detail_house.html` | a garden house in Kumponai, `.../skelbimai/parduodamas-sodo-namas-su-ukiniu-pastatu-kumponuose-elniu-81518753.html` | 2026-11-16 |
| `skelbiu_detail_house_outside_area.html` | a house in Bruzdeilynas, Klaipėda district (`87188154`): in the search results, outside the scan area | 2026-11-16 |
| `skelbiu_detail_flat.html` | a flat in Kretinga (`86968032`): in the search results, wrong category | 2026-11-16 |
| `skelbiu_detail_timber.html` | a timber ad (`22612105`): in the search results, not property at all | 2026-11-16 |

How they differ from the live pages:

- Trimmed to a minimal HTML shell around `div#app[data-page]`, the only thing the spider reads.
  Inside the JSON, props the spider does not use (navigation, SEO blocks, chat, footer links)
  are dropped. The listing data (`properties`, `pagination`, `property`) is unchanged.
- Redacted: every seller name in the index (`seller`) is `REDACTED`; the listing page's
  `contact` block (name, phone, email, logo, website, profile) is replaced with `REDACTED`,
  `+370 600 00000` and `redacted@example.invalid`. Tests assert none of those values reach an item.
- The listing description is the advertiser's own text and carries no phone number or name.

- alio and skelbiu fixtures are trimmed the same way, to the sections the spider reads (title,
  price, fact rows, description, location, JSON-LD with at most 5 photo URLs; for index pages
  the result cards and pager). The seller blocks (`.av2-contact`, `.user-card`, contact
  buttons) are not kept. Phone numbers in descriptions are `+370 600 00000`, emails
  `agent@example.invalid`, a broker's name and social link are placeholders, a broker's licence
  number and web address are zeros and `example.invalid`, and the timber seller's map point is
  a generic Vilnius coordinate. Tests assert the placeholders are removed by `ScrubPipeline`.

To add a fixture: fetch one page politely (one request, the project user agent), keep
`div#app[data-page]`, replace every name, phone, email and logo URL, and grep the file for the
original values before committing.

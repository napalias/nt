# Spider test fixtures

Saved pages the spider tests parse instead of the live site. They carry no contact data.

| File | Source | Fetched |
|------|--------|---------|
| `domoplius_index_kretinga_p1.html` | `https://domoplius.lt/skelbimai/namai-kotedzai-sodai/kretinga` (page 1, 20 listings) | 2026-11-15 |
| `domoplius_detail_house.html` | `https://domoplius.lt/skelbimai/parduodamas-gyvenamasis-namas-kretingos-r-sav-kretingoje-8816944.html` | 2026-11-15 |
| `realu_search_page.html` | `https://www.realu.lt/nekilnojamasis-turtas/pirkti/namas/kretingos-r-sav` (the search form only) | 2026-11-16 |
| `realu_estates_p1.json` | POST `https://www.realu.lt/index.php?route=product/category/getEstates`, page 1 (first 10 of 31 cards, photo carousels dropped) | 2026-11-16 |
| `realu_detail_house.html` | `https://www.realu.lt/parduodamas-namas-jokubavo-k-kretingos-r-sav-66-m2-ploto-2-aukstai-realu113233` | 2026-11-16 |
| `realu_detail_plot.html` | `https://www.realu.lt/parduodamas-namas-mokyklos-g-rudaiciu-k-kretingos-r-sav-277-m2-ploto-2-aukstai-realu160471` | 2026-11-16 |
| `capital_index_kretinga.html` | `https://www.capital.lt/lt/nekilnojamas-turtas/pirkti/kretingos-r-sav/namai` (12 cards) | 2026-11-16 |
| `capital_detail_house.html` | `https://www.capital.lt/lt/parduodamas-namas-teresieciu-g-21-kretingoje-12722-kvm-ploto-2-aukstai-p356897` | 2026-11-16 |
| `capital_detail_darbenai.html` | `https://www.capital.lt/lt/parduodamas-namas-5-kambariu12156-kvadr-sodyba-su-42-aru-zeme-darbenuose-p284390` | 2026-11-16 |

How they differ from the live pages:

- Trimmed to a minimal HTML shell around `div#app[data-page]`, the only thing the spider reads.
  Inside the JSON, props the spider does not use (navigation, SEO blocks, chat, footer links)
  are dropped. The listing data (`properties`, `pagination`, `property`) is unchanged.
- Redacted: every seller name in the index (`seller`) is `REDACTED`; the listing page's
  `contact` block (name, phone, email, logo, website, profile) is replaced with `REDACTED`,
  `+370 600 00000` and `redacted@example.invalid`. Tests assert none of those values reach an item.
- The listing description is the advertiser's own text and carries no phone number or name.

To add a fixture: fetch one page politely (one request, the project user agent), keep
`div#app[data-page]`, replace every name, phone, email and logo URL, and grep the file for the
original values before committing.

Realu and Capital fixtures (2026-11-16):

- Trimmed to the parts the spiders read (search form, card list, facts, description, photos);
  scripts, banners, navigation and similar-ad strips are dropped.
- The broker block of each detail page was replaced, not redacted in place: `Agentas Pavardenis`,
  `+370 600 00000`, `agent@example.invalid`. The card `tel:` links in the Capital index are
  `tel:+37060000000`. The Capital description's closing "contact <name> <phone>" line carries the
  same placeholders. Tests assert none of these reach an item.
- `realu_detail_house.html` adds one synthetic "similar ads" anchor (`estate_999999`) to test that
  photos from other listings are not picked up.
- `realu_estates_p1.json` keeps the real `total`/`last_page` (110 houses, 4 pages) with 10 cards.

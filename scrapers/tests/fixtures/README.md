# Spider test fixtures

Saved pages the spider tests parse instead of the live site. They carry no contact data.

| File | Source | Fetched |
|------|--------|---------|
| `domoplius_index_kretinga_p1.html` | `https://domoplius.lt/skelbimai/namai-kotedzai-sodai/kretinga` (page 1, 20 listings) | 2026-11-15 |
| `domoplius_detail_house.html` | `https://domoplius.lt/skelbimai/parduodamas-gyvenamasis-namas-kretingos-r-sav-kretingoje-8816944.html` | 2026-11-15 |
| `meguvosbustas_index_p1.html` | `https://meguvosbustas.lt/paieska` (page 1, 9 cards: 3 houses, flats, plots) | 2026-11-16 |
| `meguvosbustas_detail_house.html` | `.../parduodami-namai/Kretingos+r./5-kambariu-/649/` (village house) | 2026-11-16 |
| `meguvosbustas_detail_house_town.html` | `.../parduodami-namai/Kretinga/2-kambariu-/624/` (half a house in town, price 0) | 2026-11-16 |
| `pamario_nt_detail_house.html` | `https://www.pamario-nt.lt/lt/nta/37229-namas-kretinga` (what `/lt/kretinga/namai---` redirects to) | 2026-11-16 |
| `pamario_nt_index_klaipeda_houses.html` | `https://www.pamario-nt.lt/lt/klaipeda/klpnamai` (5 cards; the only multi-object list shape seen, Kretinga has one house) | 2026-11-16 |
| `alginosnt_index_kretingos_r.html` | `https://alginosnt.lt/search?itemType=namas&region=25` (3 houses) | 2026-11-16 |
| `alginosnt_detail_house.html` | `https://alginosnt.lt/nekilnojamas-turtas/skelbimas/12083` | 2026-11-16 |
| `kretingosskelbimai_index_namai.html` | `https://www.kretingosskelbimai.lt/skelbimai/nekilnojamas-turtas/namai` (12 ads, 8 are houses for sale) | 2026-11-16 |
| `kretingosskelbimai_index_category_p1.html` | `.../skelbimai/nekilnojamas-turtas/1.html` (first 4 of 25 ads: building materials, and the pager) | 2026-11-16 |
| `kretingosskelbimai_detail_house.html` | `.../skelbimas/259896-parduodamas-jaukiai-skoningai-irengtas-namas-guobu-aklg.-kretingoje.html` | 2026-11-16 |
| `realu_search_page.html` | `https://www.realu.lt/nekilnojamasis-turtas/pirkti/namas/kretingos-r-sav` (the search form only) | 2026-11-16 |
| `realu_estates_p1.json` | POST `https://www.realu.lt/index.php?route=product/category/getEstates`, page 1 (first 10 of 31 cards, photo carousels dropped) | 2026-11-16 |
| `realu_detail_house.html` | `https://www.realu.lt/parduodamas-namas-jokubavo-k-kretingos-r-sav-66-m2-ploto-2-aukstai-realu113233` | 2026-11-16 |
| `realu_detail_plot.html` | `https://www.realu.lt/parduodamas-namas-mokyklos-g-rudaiciu-k-kretingos-r-sav-277-m2-ploto-2-aukstai-realu160471` | 2026-11-16 |
| `capital_index_kretinga.html` | `https://www.capital.lt/lt/nekilnojamas-turtas/pirkti/kretingos-r-sav/namai` (12 cards) | 2026-11-16 |
| `capital_detail_house.html` | `https://www.capital.lt/lt/parduodamas-namas-teresieciu-g-21-kretingoje-12722-kvm-ploto-2-aukstai-p356897` | 2026-11-16 |
| `capital_detail_darbenai.html` | `https://www.capital.lt/lt/parduodamas-namas-5-kambariu12156-kvadr-sodyba-su-42-aru-zeme-darbenuose-p284390` | 2026-11-16 |
| `alio_index_kretinga_p1.html` | `https://www.alio.lt/paieska/kretingos-r-sav/?category_id=1433` (page 1, 24 cards) | 2026-11-16 |
| `alio_detail_house.html` | a garden house in Daubėnai, `.../skelbimai/sodo-namas-kretingos-r.-sav.-daubenai/ID69851715.html` (no map pin) | 2026-11-16 |
| `alio_detail_house_part_pin.html` | a part of a house in Žutautai (`ID69532877`; has a map pin the page calls `city` precision, no description) | 2026-11-16 |
| `skelbiu_index_kretinga_p1.html` | `https://www.skelbiu.lt/paieska/namai-kretingoje/` (page 1, 24 cards, mixed ad types) | 2026-11-16 |
| `skelbiu_detail_house.html` | a garden house in Kumponai, `.../skelbimai/parduodamas-sodo-namas-su-ukiniu-pastatu-kumponuose-elniu-81518753.html` | 2026-11-16 |
| `skelbiu_detail_house_outside_area.html` | a house in Bruzdeilynas, Klaipėda district (`87188154`): in the search results, outside the scan area | 2026-11-16 |
| `skelbiu_detail_flat.html` | a flat in Kretinga (`86968032`): in the search results, wrong category | 2026-11-16 |
| `skelbiu_detail_timber.html` | a timber ad (`22612105`): in the search results, not property at all | 2026-11-16 |

The agency fixtures (all but domoplius) are trimmed to the part of the page the spider reads
(cards, the specs table, the description, the contact box that must NOT be read) and redacted
by replacing, with the same text everywhere: agent names with `Agentas Pavardenis`, phones
with `+370 600 00000`, emails with `agent@example.invalid`, broker photo URLs with
`.../redacted.jpg`, and a Google Maps API key with `REDACTED`. Raw downloads were kept outside
the repo and deleted. The agency name and the advertiser's own listing text are unchanged.

How the domoplius fixtures differ from the live pages:

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

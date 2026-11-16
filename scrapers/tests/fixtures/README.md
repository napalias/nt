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

To add a fixture: fetch one page politely (one request, the project user agent), keep
`div#app[data-page]`, replace every name, phone, email and logo URL, and grep the file for the
original values before committing.

# House Search Criteria & Website Evaluation Rules

Personal house-hunting criteria for Kretinga area. This is a living document — criteria in the "Learned Preferences" section grow as listings are liked/disliked.

---

## Budget

| Parameter | Value |
|-----------|-------|
| Target price | ~200,000 EUR |
| Max price to review | 250,000 EUR |
| Skip above | 250,000 EUR |

## House Requirements

| Parameter | Preferred | Acceptable range |
|-----------|-----------|-----------------|
| Type | **New build only** | Naujos statybos |
| Living area | 100 kv.m | 95–120 kv.m |
| Plot size | 10 arų | 8–12 arų |

## Location

### Primary area
- **Kretinga** (city)

### Accepted villages and surroundings
- Dupulčiai
- Klonaičiai (Klonaliai)
- Jakubavai (Jokubavai)
- Raguviškiai
- Kartena
- Other locations **between these villages and Kretinga**

### Location rule
Any listing outside this area — skip. Don't waste time evaluating properties in Palanga, Gargždai, or other towns unless explicitly added here later.

---

## Website Evaluation Rules

When reviewing a listing on Aruodas, Domoplius, Skelbiu, or any other portal, check the following **in order**. Stop as soon as a listing fails a hard filter.

### Step 1 — Hard filters (instant skip)

1. **Price** > 250,000 EUR — skip
2. **Not new build** (renovation, old construction, unfinished shell without completion date) — skip
3. **Living area** < 95 kv.m or > 120 kv.m — skip
4. **Plot size** < 8 arų or > 12 arų — skip
5. **Location** outside the defined area — skip

### Step 2 — Government / planning red flags

These require checking government sources (TPDRIS, Infostatyba, GeoPortal). A listing may look perfect but have critical planning issues.

| Red flag | What to check | Source |
|----------|---------------|--------|
| **Techninis koridorius** | Utility corridor / technical easement crossing or adjacent to the plot | TPDRIS territorial plan, cadastral map |
| **Planuojamas aplinkelis** | Planned bypass road near the property — noise, land acquisition risk | TPDRIS, municipality general plan |
| **Planuojama infrastruktūra** | Any planned major infrastructure (roads, substations, water treatment) near the plot | TPDRIS, municipality special plans |
| **Geležinkelis** | Railroad proximity — noise, vibration, restrictions | GeoPortal map layers, check distance |
| **Apsaugos zonos** | Protection zones — water, cultural heritage, environmental — that restrict what you can do on the plot | GeoPortal WFS layers (SŽNS) |
| **Potvynių zona** | Flood risk zone | GeoPortal environmental layers |

**Rule**: If any of the above affects the plot directly (easement crosses it, bypass planned through it), mark as **deal-breaker**. If it's nearby but doesn't directly affect the plot, note it and evaluate case by case.

### Step 3 — Listing quality check

| Check | What to look for |
|-------|-----------------|
| Photos | Are there real photos of the actual house? Stock renders only = early construction, verify completion date |
| Cadastral number | Is it listed? If missing, harder to cross-reference with gov data |
| Developer / seller | Company or private person? If company — check their other projects, completion track record |
| Description completeness | Does it mention heating type, energy class, materials, warranty? Vague descriptions = caution |
| Price per kv.m | Calculate and compare against area average. Significantly below average = investigate why |
| Listed duration | How long has it been listed? Very long = potential issues (overpriced, legal, construction) |

### Step 4 — Deeper evaluation (for listings that pass Steps 1–3)

- Cross-check plot on cadastral map — confirm boundaries, purpose (gyvenamoji paskirtis)
- Check active building permits on Infostatyba for the address
- Look at the territorial plan covering the area — what else is allowed nearby
- Check if the developer has permits for the surrounding plots (is this part of a larger development?)
- Verify road access — is the access road public or through someone else's plot?

---

## Learned Preferences

This section is populated over time as listings are reviewed and marked as liked/disliked. Each entry records the reason so the system can learn patterns.

### Likes (patterns to prioritize)
_No entries yet — will be added as listings are reviewed._

### Dislikes (patterns to deprioritize)
_No entries yet — will be added as listings are reviewed._

### Example format for future entries
```
- [LIKE] 2024-XX-XX: Listing #1234 — liked the open-plan layout, large windows facing south
- [DISLIKE] 2024-XX-XX: Listing #5678 — too close to main road, noise concern
- [DISLIKE] 2024-XX-XX: Listing #9012 — plot shape too narrow (< 20m width)
```

---

## How to Use This Document

1. **Scrapers** reference the hard filters (Step 1) to auto-skip listings that don't match
2. **Backend** uses government checks (Step 2) to flag risks on matching listings
3. **Frontend** shows risk badges and preference match scores
4. **User** reviews listings that pass auto-filters, marks like/dislike with reasons
5. **System** learns from likes/dislikes to refine future recommendations

As the Learned Preferences section grows, the app should:
- Auto-exclude listings matching 3+ dislike patterns
- Boost listings matching like patterns
- Surface "you might like this because..." reasoning

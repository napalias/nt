# GOV_DATA_SOURCES.md

Lithuanian government and registry data sources relevant to real estate research. This is a reference document — Claude Code should read it when working on Phase 7+.

## What we want to surface for a given location

For any point on the map (or address), the app should show within 5 km:

1. **Active listings** (Phase 1–4 — done)
2. **Real estate developers** active in the area — their other projects, age, size, history
3. **Building permits** — what's being built, by whom, when issued
4. **Territorial planning documents** — master plan, detailed plans, zoning, height limits, allowed uses
5. **Cadastral data** — plot boundaries, ownership type (private/state/municipal), area, purpose
6. **Cultural heritage / environmental restrictions** — protected zones, listed buildings, water protection zones
7. **Transaction history** (if accessible) — recent sale prices in the area

## Data sources by category

### 1. Companies / developers (free-ish)

#### Registrų centras — Juridinių asmenų registras (JAR / JADIS)
- **What**: All Lithuanian legal entities, names, codes, addresses, status, NACE codes
- **URL**: https://www.registrucentras.lt/jar/
- **API**: https://www.registrucentras.lt/atviri-duomenys/ — open data downloads (CSV/JSON), refreshed periodically
- **Filter by NACE**:
  - `41.10` — Development of building projects (this is exactly "real estate developer")
  - `41.20` — Construction of residential and non-residential buildings
  - `68.10` — Buying and selling of own real estate
  - `68.20` — Renting and operating of own or leased real estate
  - `68.31` — Real estate agencies
- **Strategy**: Download the JAR open data dump, filter to these NACE codes, ingest as `Developer` records. Refresh monthly.

#### Sodra — employer registry
- **URL**: https://atvira.sodra.lt/
- **What**: Number of employees per company, monthly snapshots
- **Use**: gauge developer size and activity (growing vs. shrinking workforce signals momentum)
- **Format**: CSV downloads, free

#### data.gov.lt — Lithuanian open data portal
- **URL**: https://data.gov.lt/
- **What**: Catalog with hundreds of datasets, including company financials summaries, real estate transaction stats by municipality, building permits aggregates
- **Format**: Mostly JSON / CSV via CKAN-style API
- **Use**: secondary enrichment

---

### 2. Building permits (free, public)

#### Infostatyba (IS „Infostatyba")
- **What**: ALL building permits, declarations, project notices in Lithuania since ~2008
- **URL**: https://www.planuojustatyti.lt/ (public search) / https://infostatyba.aplinka.lt/ (system)
- **API**: No official public API. Has a public search UI; data is queryable but requires scraping.
- **Per record**: applicant (often the developer), address, plot cadastral number, project type, status (issued/refused/in progress), key dates, sometimes architect/contractor
- **Strategy**:
  - Scrape the public search by date ranges (the URL/POST params accept date filters)
  - Match each permit to a cadastral plot via the plot number → join to GeoPortal cadastral layer for geometry
  - Heavy initial backfill (~years of permits), then incremental daily
- **Volume**: ~50–100k records nationally per year

#### Aplinkos apsaugos agentūra (AAA) — environmental impact assessments
- **URL**: https://aaa.lrv.lt/
- **What**: Environmental impact screening / full assessment decisions for larger projects
- **Use**: catches big developments that need EIA before building permit
- **Format**: HTML pages, PDF documents — scrape

---

### 3. Territorial planning documents (free, public)

#### TPDRIS — Teritorijų planavimo dokumentų rengimo ir teritorijų planavimo proceso valstybinės priežiūros informacinė sistema
- **URL**: https://www.tpdris.lt/
- **What**: ALL territorial planning documents — master plans (bendrieji planai), detailed plans (detalieji planai), special plans, currently in preparation or already approved
- **API**: No formal public API. Search UI returns documents and PDFs.
- **Per document**: title, type, status, municipality, geographic scope (often as a PDF map, sometimes shapefile), responsible organization, key dates, downloadable PDF documents
- **Strategy**:
  - Scrape document metadata page by page
  - Download PDFs to object storage
  - Extract geographic boundaries (when shapefiles are attached) — these become polygons in our DB
  - LLM-extract key facts from PDFs: allowed uses, height limits, density, parking requirements
- **The big payoff**: showing "your search area is covered by Detailed Plan #X (approved 2019), which allows residential up to 3 stories with density of 0.4" is a unique value prop no listings site provides

#### GeoPortal.lt — central spatial data infrastructure
- **URL**: https://www.geoportal.lt/
- **What**: WMS/WFS endpoints for cadastral data, planning data, restrictions, addresses, orthophotos, terrain
- **API**: Standard OGC WMS/WFS — well documented
- **Useful layers**:
  - `Adresų registras` — all Lithuanian addresses (use as geocoder fallback / address autocomplete)
  - `Nekilnojamojo turto kadastras (NTKR)` — cadastral plots (geometry + plot purpose category)
  - `Teritorijų planavimo dokumentų erdvinių duomenų rinkinys (TPDR)` — planning document polygons
  - `Saugomos teritorijos` — protected areas
  - `Kultūros vertybės` — cultural heritage objects (listed buildings, archaeological sites)
  - `Specialiosios žemės naudojimo sąlygos (SŽNS)` — special land use conditions (gas pipelines, power lines, water protection)
- **Strategy**:
  - For static layers (cadastre, restrictions): WFS GetFeature with bounding box → store geometries in our PostGIS
  - For visual overlays (orthophoto): WMS as map tile layer in MapLibre

---

### 4. Cadastral / property data (free + paid)

#### Registrų centras — Nekilnojamojo turto registras (NTR)
- **What**: Real estate registry — ownership, encumbrances, mortgages, transactions, property characteristics
- **URL**: https://www.registrucentras.lt/p/
- **API**: PAID. https://www.registrucentras.lt/p/ has VEPSIS web services
- **Free public**: Limited "REGIA" map (regia.lt) — can see cadastral plot info free for visualisation, but no bulk export
- **Strategy**:
  - For MVP: skip paid integration, use only free GeoPortal cadastre + Infostatyba
  - For premium tier: paid NTR integration unlocks ownership info, transaction prices, encumbrances

#### REGIA (free public-facing version of cadastral data)
- **URL**: https://www.regia.lt/
- **What**: Free public map showing cadastral plots, addresses, infrastructure
- **Use**: human verification / debugging — not for bulk scraping (ToS restricts)

---

### 5. Cultural heritage

#### Kultūros paveldo departamentas — Kultūros vertybių registras (KVR)
- **URL**: https://kvr.kpd.lt/
- **What**: All registered cultural heritage objects (listed buildings, archaeological sites, monuments, urban heritage zones)
- **Format**: Web search + downloadable XML/CSV exports for some categories
- **Also available as WFS layer via GeoPortal**
- **Use**: flag listings inside heritage zones (significant restrictions on renovation)

---

### 6. Environmental restrictions

#### Aplinkos apsaugos agentūra
- Water protection zones, flood zones, Natura 2000 areas
- All available as WFS layers via GeoPortal under different namespaces
- Same approach as cadastre: WFS GetFeature with bbox → PostGIS

---

## Implementation strategy

### Priority order

1. **GeoPortal WFS layers** — easiest win, real OGC services, just code
   - Cadastre (plot geometries)
   - Special land use conditions
   - Cultural heritage
   - Protected areas
2. **JAR open data dump** — developers list, monthly refresh
3. **Infostatyba scraper** — building permits, ongoing
4. **TPDRIS scraper + PDF extraction** — planning documents (highest value, hardest)
5. **Sodra enrichment** — developer size signal
6. **Optional later**: paid NTR integration for transactions/ownership

### New Django apps

```
apps/
├── developers/        # Company records, NACE filter, projects link
├── permits/           # Infostatyba records
├── planning/          # TPDRIS documents, polygons, extracted facts
├── cadastre/          # NTKR plots, SŽNS, heritage, environmental
└── documents/         # PDF blob storage, indexing, full-text search
```

### Schema additions (sketch)

```python
class Developer(models.Model):
    """A company in the real estate / construction sector (filtered by NACE)."""
    company_code = models.CharField(max_length=16, unique=True)  # JAR identifier
    name = models.CharField(max_length=300)
    nace_codes = models.JSONField(default=list)  # ['41.10', '68.10']
    registered_address = models.CharField(max_length=500)
    registered_address_point = gis_models.PointField(geography=True, null=True)
    founded = models.DateField(null=True)
    status = models.CharField(max_length=32)  # active / liquidated / etc.
    employee_count_history = models.JSONField(default=list)  # from Sodra
    last_synced_at = models.DateTimeField()


class BuildingPermit(models.Model):
    """A record from Infostatyba."""
    permit_number = models.CharField(max_length=64, unique=True)
    permit_type = models.CharField(max_length=64)  # statybos leidimas / declaration / etc.
    status = models.CharField(max_length=32)
    issued_at = models.DateField(null=True)

    applicant_name = models.CharField(max_length=500)
    applicant = models.ForeignKey(Developer, null=True, on_delete=models.SET_NULL)
    contractor = models.ForeignKey(Developer, null=True, on_delete=models.SET_NULL,
                                   related_name='contracted_permits')

    cadastral_number = models.CharField(max_length=64, db_index=True)
    address_raw = models.CharField(max_length=500)
    location = gis_models.PointField(geography=True, srid=4326, null=True)
    plot = models.ForeignKey('cadastre.CadastralPlot', null=True, on_delete=models.SET_NULL)

    project_description = models.TextField(blank=True)
    project_type = models.CharField(max_length=64)  # new / reconstruction / repair
    building_purpose = models.CharField(max_length=64)  # residential / commercial / mixed
    raw_data = models.JSONField(default=dict)  # everything else from the source


class PlanningDocument(models.Model):
    """A territorial planning document from TPDRIS."""
    DOC_TYPES = [
        ('master', 'Master plan (bendrasis planas)'),
        ('detailed', 'Detailed plan (detalusis planas)'),
        ('special', 'Special plan'),
    ]
    STATUSES = [
        ('preparation', 'In preparation'),
        ('public_review', 'Public review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ]

    tpdris_id = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=500)
    doc_type = models.CharField(max_length=16, choices=DOC_TYPES)
    status = models.CharField(max_length=16, choices=STATUSES)
    municipality = models.CharField(max_length=100)
    organizer = models.CharField(max_length=300)  # who is preparing it
    approved_at = models.DateField(null=True)
    expires_at = models.DateField(null=True)

    boundary = gis_models.MultiPolygonField(geography=True, srid=4326, null=True)

    # Extracted via LLM from PDFs:
    allowed_uses = models.JSONField(default=list)         # ['residential', 'commercial']
    max_height_m = models.FloatField(null=True)
    max_floors = models.IntegerField(null=True)
    max_density = models.FloatField(null=True)            # building density coefficient
    parking_requirements = models.TextField(blank=True)
    extraction_confidence = models.FloatField(null=True)  # how sure was the LLM

    documents = models.ManyToManyField('documents.Document')
    source_url = models.URLField()


class CadastralPlot(models.Model):
    """A plot from GeoPortal NTKR layer."""
    cadastral_number = models.CharField(max_length=64, unique=True)
    geometry = gis_models.MultiPolygonField(geography=True, srid=4326)
    area_sqm = models.FloatField()
    purpose = models.CharField(max_length=200)  # 'Gyvenamoji teritorija', etc.
    purpose_category = models.CharField(max_length=64)  # normalized: residential/commercial/agricultural/forest/...
    municipality = models.CharField(max_length=100)


class HeritageObject(models.Model):
    """Cultural heritage from KVR / GeoPortal."""
    kvr_code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=500)
    category = models.CharField(max_length=100)
    protection_level = models.CharField(max_length=64)
    geometry = gis_models.GeometryField(geography=True, srid=4326)


class SpecialLandUseCondition(models.Model):
    """SŽNS: special land use restrictions like water protection zones, gas pipeline buffers."""
    sns_code = models.CharField(max_length=64)
    category = models.CharField(max_length=200)
    geometry = gis_models.MultiPolygonField(geography=True, srid=4326)
    description = models.TextField(blank=True)


class Document(models.Model):
    """Generic PDF / file storage. Used by planning, permits, etc."""
    url = models.URLField()
    storage_path = models.CharField(max_length=500)  # local path or object storage key
    title = models.CharField(max_length=500)
    extracted_text = models.TextField(blank=True)
    file_size = models.IntegerField(null=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    extracted_at = models.DateTimeField(null=True)
```

### Search response — multi-layer

The `/api/search` endpoint should grow to return layers, not just listings:

```json
{
  "center": { "lat": 54.6872, "lng": 25.2797 },
  "radius_m": 5000,
  "listings": [...],
  "developers": [
    {
      "id": 1,
      "name": "UAB Statybos meistras",
      "company_code": "300123456",
      "active_permits_count": 3,
      "completed_permits_count": 12,
      "registered_point": [54.69, 25.28]
    }
  ],
  "permits": [
    {
      "id": 1,
      "permit_number": "LSNS-01-23-1234",
      "applicant": "UAB Statybos meistras",
      "status": "issued",
      "issued_at": "2024-03-15",
      "building_purpose": "residential",
      "location": [54.685, 25.283]
    }
  ],
  "planning_documents": [
    {
      "id": 1,
      "title": "Vilniaus m. centrinės dalies detalusis planas",
      "doc_type": "detailed",
      "status": "approved",
      "max_floors": 3,
      "allowed_uses": ["residential", "commercial"],
      "source_url": "https://www.tpdris.lt/..."
    }
  ],
  "restrictions": [
    {
      "type": "heritage_zone",
      "name": "Vilniaus senamiestis",
      "protection_level": "UNESCO"
    },
    {
      "type": "special_land_use",
      "category": "water protection zone (50m)"
    }
  ]
}
```

### Map UI — toggleable layers

The frontend map gets a layer panel:

- ☑ Listings (markers, default on)
- ☐ Building permits (different colored markers)
- ☐ Cadastral plots (polygon overlay)
- ☐ Planning documents (polygon overlay, color by document type)
- ☐ Heritage zones (polygon overlay, red)
- ☐ Special land use restrictions (polygon overlay, hatched)
- ☐ Orthophoto (WMS tile layer from GeoPortal)

Each layer shows a count in the legend, click to toggle.

---

## Legal / ethical

Government data on real estate planning is **public information**. There's no copyright/ToS bar to using it, and arguably a public interest in making it more accessible.

That said:
- Be polite when scraping gov sites — same throttling as commercial sites
- Cite sources clearly in the UI ("source: TPDRIS, last updated DD.MM.YYYY")
- Don't republish full PDFs without checking license — link out to source
- For developer profiles, you're combining public data, but **don't editorialize negatively** — stick to facts (permit counts, dates, sizes), avoid claims about quality/reputation unless sourced
- GDPR: company data is mostly fine; if individual people appear (e.g., self-employed builders in JAR), treat carefully

---

## What this unlocks (product-wise)

A buyer searching "Vilnius Žvėrynas" gets:
- 47 active listings
- 5 active building permits within 5 km (1 next door — useful to know)
- Detailed plan from 2017 saying max 3 floors, 0.4 density
- Heritage zone overlay (this neighborhood is partially protected)
- "UAB X" is the developer of 2 nearby buildings (you can click to their profile and see all their projects)
- Water protection zone overlay (river is 200m away — affects basement)

That's the kind of integrated view nobody offers today in Lithuania. Aruodas et al. show only listings. The gov sites are siloed and have terrible UX. This is the differentiator.

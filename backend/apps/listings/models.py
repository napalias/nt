from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.listings.blocks import LABELS as BLOCK_KIND_LABELS
from apps.listings.blocks import BlockKind


class Listing(models.Model):
    class PropertyType(models.TextChoices):
        HOUSE = "house", "Namas"
        FLAT = "flat", "Butas"
        PLOT = "plot", "Sklypas"
        COMMERCIAL = "commercial", "Komercinis"
        COTTAGE = "cottage", "Sodyba"
        PART_OF_HOUSE = "part_of_house", "Namo dalis"

    class ListingType(models.TextChoices):
        SALE = "sale", "Pardavimas"
        RENT = "rent", "Nuoma"

    class LocationPrecision(models.TextChoices):
        EXACT = "exact", "Tikslus"
        STREET = "street", "Gatvės lygio"
        CITY = "city", "Miesto / gyvenvietės centras"

    class BuildingType(models.TextChoices):
        BRICK = "brick", "Mūrinis"
        BLOCK = "block", "Blokinis"
        WOODEN = "wooden", "Medinis"
        MONOLITHIC = "monolithic", "Monolitinis"
        LOG = "log", "Rąstinis"
        FRAME = "frame", "Karkasinis"
        OTHER = "other", "Kita"

    class CheckResult(models.TextChoices):
        OK = "ok", "Dar galioja"
        GONE = "gone", "Pašalintas (404/410)"
        STALE = "stale", "Nebegalioja"
        BLOCKED = "blocked", "Užblokuota"
        ERROR = "error", "Klaida"

    # --- Source tracking ---
    source = models.CharField(max_length=32, db_index=True, verbose_name="Šaltinis")
    source_url = models.URLField(max_length=500, verbose_name="Šaltinio nuoroda")
    source_id = models.CharField(max_length=64, verbose_name="Šaltinio ID")
    content_hash = models.CharField(max_length=64, db_index=True, verbose_name="Turinio hash")

    # --- Core listing fields ---
    title = models.CharField(max_length=500, verbose_name="Pavadinimas")
    # `description` is the text readers use (frontend, classifier, admin): the cleaned text
    # when one exists, else the scrubbed raw text. description_raw is what the portal said
    # (phones/emails removed); description_clean is the AI-cleaned version. A re-scrape
    # keeps the clean text while the raw text is unchanged.
    description = models.TextField(blank=True, verbose_name="Aprašymas")
    description_raw = models.TextField(blank=True, verbose_name="Aprašymas (originalas)")
    description_clean = models.TextField(blank=True, verbose_name="Aprašymas (išvalytas)")
    property_type = models.CharField(
        max_length=16, choices=PropertyType.choices, db_index=True, verbose_name="Turto tipas"
    )
    listing_type = models.CharField(
        max_length=8, choices=ListingType.choices, db_index=True, verbose_name="Skelbimo tipas"
    )

    # --- Price ---
    price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Kaina"
    )
    price_per_sqm = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Kaina už kv.m"
    )
    currency = models.CharField(max_length=3, default="EUR", verbose_name="Valiuta")

    # --- Size ---
    area_sqm = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Plotas kv.m"
    )
    plot_area_ares = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Sklypo plotas arais"
    )
    rooms = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Kambariai")

    # --- Building details ---
    floor = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="Aukštas")
    total_floors = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Aukštų skaičius"
    )
    year_built = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Statybos metai"
    )
    building_type = models.CharField(
        max_length=16,
        choices=BuildingType.choices,
        blank=True,
        verbose_name="Pastato tipas",
    )
    heating_type = models.CharField(max_length=64, blank=True, verbose_name="Šildymas")
    energy_class = models.CharField(max_length=8, blank=True, verbose_name="Energijos klasė")
    is_new_construction = models.BooleanField(
        default=False, db_index=True, verbose_name="Naujos statybos"
    )

    # --- Location ---
    address_raw = models.CharField(max_length=500, verbose_name="Adresas (originalus)")
    city = models.CharField(max_length=100, blank=True, db_index=True, verbose_name="Miestas")
    municipality = models.CharField(
        max_length=100, blank=True, db_index=True, verbose_name="Savivaldybė"
    )
    district = models.CharField(max_length=100, blank=True, verbose_name="Rajonas / mikrorajonas")
    location = gis_models.PointField(geography=True, srid=4326, verbose_name="Koordinatės")
    # How good `location` is. Blank = unknown (rows scraped before this field existed).
    location_precision = models.CharField(
        max_length=8,
        choices=LocationPrecision.choices,
        blank=True,
        default="",
        verbose_name="Koordinačių tikslumas",
    )
    cadastral_number = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name="Kadastro numeris"
    )

    # --- Media ---
    photo_urls = models.JSONField(default=list, verbose_name="Nuotraukų nuorodos")

    # --- Timestamps ---
    first_seen_at = models.DateTimeField(auto_now_add=True, verbose_name="Pirmas pastebėjimas")
    last_seen_at = models.DateTimeField(auto_now=True, verbose_name="Paskutinis pastebėjimas")
    scraped_at = models.DateTimeField(verbose_name="Nuskaityta")
    is_active = models.BooleanField(default=True, db_index=True, verbose_name="Aktyvus")
    # The validity checker (tasks.check_listing_validity) stamps both on every attempt, so
    # it rotates through all active listings: never-checked first, then the longest ago.
    # The result says what the attempt found; "blocked" and "error" are not a verification.
    last_checked_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name="Paskutinį kartą tikrinta"
    )
    last_check_result = models.CharField(
        max_length=8, choices=CheckResult.choices, blank=True, verbose_name="Patikros rezultatas"
    )

    # --- Raw data ---
    raw_data = models.JSONField(default=dict, blank=True, verbose_name="Neapdoroti duomenys")

    class Meta:
        verbose_name = "Skelbimas"
        verbose_name_plural = "Skelbimai"
        ordering = ["-scraped_at"]
        constraints = [
            models.UniqueConstraint(fields=["source", "source_id"], name="unique_source_listing"),
        ]
        indexes = [
            models.Index(fields=["property_type", "listing_type", "is_active"]),
            models.Index(fields=["price"]),
            models.Index(fields=["area_sqm"]),
            models.Index(fields=["rooms"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.source})"

    def save(self, *args, **kwargs) -> None:
        if self.area_sqm and self.price and self.area_sqm > 0:
            self.price_per_sqm = self.price / self.area_sqm
        else:
            self.price_per_sqm = None
        super().save(*args, **kwargs)


class ExcludedListing(models.Model):
    listing = models.OneToOneField(Listing, on_delete=models.CASCADE, related_name="exclusion")
    reason = models.TextField(blank=True, verbose_name="Priežastis")
    excluded_at = models.DateTimeField(auto_now_add=True, verbose_name="Pašalinta")

    class Meta:
        verbose_name = "Pašalintas skelbimas"
        verbose_name_plural = "Pašalinti skelbimai"

    def __str__(self) -> str:
        return f"Excluded: {self.listing}"


class ScrapeRun(models.Model):
    """One spider run: what it crawled, found, saved and dropped, and how it ended.

    Scheduled runs: `run_spider` creates the row, hands its id to the crawl, and stores the
    log tail when the crawl exits. Inside the crawl the scrapers' ScrapeRunRecorder fills
    in the counts and status. Hand-started `scrapy crawl` runs get a row from the recorder
    alone. Shown on the /scans page; open problems feed /api/scrapes/attention and the
    notification e-mail (apps/listings/notify.py)."""

    class Status(models.TextChoices):
        RUNNING = "running", "Vykdoma"
        OK = "ok", "Sėkminga"
        # Saved some, but with errors, 401/403/429 answers, a block page or >30% dropped.
        PARTIAL = "partial", "Iš dalies"
        EMPTY = "empty", "Nieko nerasta"
        BLOCKED = "blocked", "Užblokuota"
        FAILED = "failed", "Nepavyko"

    class Trigger(models.TextChoices):
        BEAT = "beat", "Pagal grafiką"
        MANUAL = "manual", "Rankinis"

    BLOCK_KIND_CHOICES = [(kind.value, BLOCK_KIND_LABELS[kind]) for kind in BlockKind]
    # Longer start URLs are cut to this on write (apps/listings/scrape_runs.clip_start_url).
    START_URL_MAX_LENGTH = 500

    spider = models.CharField(max_length=32, db_index=True, verbose_name="Šaltinis")
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RUNNING, verbose_name="Būsena"
    )
    trigger = models.CharField(
        max_length=8, choices=Trigger.choices, default=Trigger.MANUAL, verbose_name="Paleista"
    )
    start_url = models.URLField(
        max_length=START_URL_MAX_LENGTH, blank=True, verbose_name="Pradinis URL"
    )
    started_at = models.DateTimeField(db_index=True, verbose_name="Pradėta")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Baigta")
    finish_reason = models.CharField(max_length=64, blank=True, verbose_name="Pabaigos priežastis")
    pages_crawled = models.PositiveIntegerField(default=0, verbose_name="Puslapiai")
    # Listing links the spider saw on index pages (stat nt/links_seen), else every request
    # the crawl queued (scheduler/enqueued).
    links_seen = models.PositiveIntegerField(default=0, verbose_name="Matytos nuorodos")
    items_found = models.PositiveIntegerField(default=0, verbose_name="Rasta")
    items_saved = models.PositiveIntegerField(default=0, verbose_name="Išsaugota")
    items_new = models.PositiveIntegerField(default=0, verbose_name="Nauji")
    items_updated = models.PositiveIntegerField(default=0, verbose_name="Atnaujinti")
    items_unchanged = models.PositiveIntegerField(default=0, verbose_name="Nepakitę")
    items_dropped = models.PositiveIntegerField(default=0, verbose_name="Atmesti")
    errors = models.PositiveIntegerField(default=0, verbose_name="Klaidos")
    drop_reasons = models.JSONField(default=dict, blank=True, verbose_name="Atmetimo priežastys")
    # Every spider argument (-a) the crawl got.
    args = models.JSONField(default=dict, blank=True, verbose_name="Argumentai")
    block_kind = models.CharField(
        max_length=16, choices=BLOCK_KIND_CHOICES, blank=True, verbose_name="Blokavimo tipas"
    )
    needs_human = models.BooleanField(default=False, verbose_name="Reikia žmogaus")
    # Last lines of the crawl's log, contact data scrubbed. Scheduled runs only.
    log_tail = models.TextField(blank=True, verbose_name="Žurnalo pabaiga")
    # When the principal was e-mailed about this run (at most once per source per 24 h).
    notified_at = models.DateTimeField(null=True, blank=True, verbose_name="Pranešta")

    class Meta:
        verbose_name = "Nuskaitymas"
        verbose_name_plural = "Nuskaitymai"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.spider} {self.started_at:%Y-%m-%d %H:%M} ({self.status})"

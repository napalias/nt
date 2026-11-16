import scrapy


class ListingItem(scrapy.Item):
    """A single real estate listing scraped from any source."""

    # Identity
    url = scrapy.Field()
    source = scrapy.Field()
    source_id = scrapy.Field()

    # Core
    title = scrapy.Field()
    description = scrapy.Field()
    price = scrapy.Field()
    currency = scrapy.Field()
    area_sqm = scrapy.Field()
    plot_area_ares = scrapy.Field()
    rooms = scrapy.Field()

    # Location
    address = scrapy.Field()
    city = scrapy.Field()
    municipality = scrapy.Field()
    district = scrapy.Field()
    latitude = scrapy.Field()
    longitude = scrapy.Field()
    # exact / street / city, set by GeocodePipeline (city = a settlement or city-centre fallback)
    location_precision = scrapy.Field()
    cadastral_number = scrapy.Field()

    # Classification
    property_type = scrapy.Field()
    listing_type = scrapy.Field()
    building_type = scrapy.Field()
    is_new_construction = scrapy.Field()

    # Details
    floor = scrapy.Field()
    total_floors = scrapy.Field()
    year_built = scrapy.Field()
    heating_type = scrapy.Field()
    energy_class = scrapy.Field()

    # Media
    photo_urls = scrapy.Field()

    # Raw
    raw_data = scrapy.Field()
    scraped_at = scrapy.Field()

    # Pipeline-internal (set by NormalizePipeline)
    _content_hash = scrapy.Field()
    # Pipeline-internal (set by mixed-feed spiders, read and removed by JevScreenPipeline): the
    # spider's own code filter's verdict on "is this a house for sale?". False items reach the
    # pipeline only while JEV_MODE is shadow or on, so Jev's answer on them is logged too.
    _house_ad_heuristic = scrapy.Field()


class PermitItem(scrapy.Item):
    """A building permit scraped from Infostatyba / planuojustatyti.lt."""

    # Identity
    permit_number = scrapy.Field()
    permit_type = scrapy.Field()
    status = scrapy.Field()
    issued_at = scrapy.Field()

    # Participants
    applicant_name = scrapy.Field()

    # Location
    cadastral_number = scrapy.Field()
    address_raw = scrapy.Field()

    # Project
    project_description = scrapy.Field()
    project_type = scrapy.Field()
    building_purpose = scrapy.Field()

    # Source
    source_url = scrapy.Field()
    raw_data = scrapy.Field()
    scraped_at = scrapy.Field()

    # Pipeline-internal (set by PermitMatchPlotPipeline / PermitMatchDeveloperPipeline)
    _plot_id = scrapy.Field()
    _plot_centroid = scrapy.Field()
    _applicant_id = scrapy.Field()


class PlanningItem(scrapy.Item):
    """A territorial planning document scraped from TPDRIS."""

    # Identity
    tpdris_id = scrapy.Field()
    source_url = scrapy.Field()

    # Core metadata
    title = scrapy.Field()
    doc_type = scrapy.Field()  # master / detailed / special
    status = scrapy.Field()  # preparation / public_review / approved / rejected / expired
    municipality = scrapy.Field()
    organizer = scrapy.Field()

    # Dates
    approved_at = scrapy.Field()
    expires_at = scrapy.Field()

    # Documents (list of dicts: [{url, title}])
    pdf_links = scrapy.Field()

    # Raw
    raw_data = scrapy.Field()
    scraped_at = scrapy.Field()

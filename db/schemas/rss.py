"""RSS Feed schemas for scraping configuration."""

from datetime import datetime

from pydantic import BaseModel, Field


class ParsingPattern(BaseModel):
    """Individual parsing pattern."""

    field: str
    path: str
    regex: str | None = None
    regex_group: int | None = 0


class RSSFeedParsingPatterns(BaseModel):
    """RSS feed parsing patterns configuration."""

    title: str | None = "title"
    description: str | None = "description"
    pubDate: str | None = "pubDate"
    poster: str | None = None
    background: str | None = None
    logo: str | None = None
    category: str | None = "category"

    # Advanced patterns with regex support
    magnet: str | None = None
    magnet_regex: str | None = None
    torrent: str | None = None
    torrent_regex: str | None = None
    info_hash: str | None = None  # Direct info hash extraction (e.g., torznab:attr[@name="infohash"]@value)
    info_hash_regex: str | None = None
    size: str | None = None
    size_regex: str | None = None
    seeders: str | None = None
    seeders_regex: str | None = None
    category_regex: str | None = None
    episode_name_parser: str | None = None

    # Regex group numbers
    magnet_regex_group: int = 1
    torrent_regex_group: int = 1
    info_hash_regex_group: int = 1
    size_regex_group: int = 1
    seeders_regex_group: int = 1
    category_regex_group: int = 1


class CatalogPattern(BaseModel):
    """Catalog pattern for auto-detection."""

    name: str
    regex: str
    case_sensitive: bool = False
    enabled: bool = True
    target_catalogs: list[str]


class RSSFeedFilters(BaseModel):
    """RSS feed filtering configuration."""

    title_filter: str | None = None
    title_exclude_filter: str | None = None
    min_size_mb: int | None = None
    max_size_mb: int | None = None
    min_seeders: int | None = None
    category_filter: list[str] | None = None


class RSSFeedMetrics(BaseModel):
    """RSS Feed scraping metrics."""

    total_items_found: int = 0
    total_items_processed: int = 0
    total_items_skipped: int = 0
    total_errors: int = 0
    last_scrape_duration: float | None = None
    items_processed_last_run: int = 0
    items_skipped_last_run: int = 0
    errors_last_run: int = 0
    skip_reasons: dict[str, int] = Field(default_factory=dict)


class RSSFeed(BaseModel):
    """RSS feed configuration (Pydantic schema)."""

    id: str | None = Field(alias="_id")
    name: str
    url: str
    parsing_patterns: RSSFeedParsingPatterns = Field(default_factory=RSSFeedParsingPatterns)
    filters: RSSFeedFilters = Field(default_factory=RSSFeedFilters)
    active: bool = True
    last_scraped: datetime | None = None
    source: str | None = None
    torrent_type: str | None = "public"
    auto_detect_catalog: bool | None = False
    catalog_patterns: list[CatalogPattern] | None = Field(default_factory=list)
    metrics: RSSFeedMetrics | None = Field(default_factory=RSSFeedMetrics)


class RSSFeedCreate(BaseModel):
    """Create a new RSS feed."""

    name: str
    url: str
    auto_detect_catalog: bool | None = False
    catalog_patterns: list[CatalogPattern] | None = Field(default_factory=list)
    parsing_patterns: RSSFeedParsingPatterns = RSSFeedParsingPatterns()
    filters: RSSFeedFilters = RSSFeedFilters()
    active: bool = True
    api_password: str | None = None
    source: str | None = None
    torrent_type: str | None = "public"


class RSSFeedUpdate(BaseModel):
    """Update an existing RSS feed."""

    name: str | None = None
    url: str | None = None
    parsing_patterns: RSSFeedParsingPatterns | None = None
    filters: RSSFeedFilters | None = None
    active: bool | None = None
    auto_detect_catalog: bool | None = None
    catalog_patterns: list[CatalogPattern] | None = None
    source: str | None = None
    torrent_type: str | None = None


class RSSFeedBulkImport(BaseModel):
    """Bulk import RSS feeds."""

    feeds: list[RSSFeedCreate]
    api_password: str


class RSSFeedExamine(BaseModel):
    """Examine an RSS feed URL."""

    url: str
    api_password: str


class RSSFeedCatalogPatternSchema(BaseModel):
    """Schema for RSS feed catalog pattern from SQL model."""

    id: int
    name: str | None = None
    regex: str
    enabled: bool = True
    case_sensitive: bool = False
    target_catalogs: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class RSSFeedSchema(BaseModel):
    """Schema for RSS feed response from SQL model."""

    id: int
    name: str
    url: str
    parsing_patterns: RSSFeedParsingPatterns | None = None
    filters: RSSFeedFilters | None = None
    active: bool = True
    last_scraped: datetime | None = None
    source: str | None = None
    torrent_type: str = "public"
    auto_detect_catalog: bool = False
    catalog_patterns: list[RSSFeedCatalogPatternSchema] = Field(default_factory=list)
    metrics: RSSFeedMetrics | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_sql_model(cls, feed) -> "RSSFeedSchema":
        """Create from SQL model RSSFeed."""
        return cls(
            id=feed.id,
            name=feed.name,
            url=feed.url,
            parsing_patterns=RSSFeedParsingPatterns(**feed.parsing_patterns) if feed.parsing_patterns else None,
            filters=RSSFeedFilters(**feed.filters) if feed.filters else None,
            active=feed.active,
            last_scraped=feed.last_scraped,
            source=feed.source,
            torrent_type=feed.torrent_type,
            auto_detect_catalog=feed.auto_detect_catalog,
            catalog_patterns=[
                RSSFeedCatalogPatternSchema(
                    id=p.id,
                    name=p.name,
                    regex=p.regex,
                    enabled=p.enabled,
                    case_sensitive=p.case_sensitive,
                    target_catalogs=p.target_catalogs,
                )
                for p in (feed.catalog_patterns or [])
            ],
            metrics=RSSFeedMetrics(**feed.metrics) if feed.metrics else None,
            created_at=feed.created_at,
            updated_at=feed.updated_at,
        )


# User RSS Feed Schemas


class UserRSSFeedOwner(BaseModel):
    """Owner information for admin view."""

    id: str
    email: str
    username: str | None = None


class UserRSSFeedCatalogPatternSchema(BaseModel):
    """Schema for user RSS feed catalog pattern."""

    id: str
    name: str | None = None
    regex: str
    enabled: bool = True
    case_sensitive: bool = False
    target_catalogs: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class UserRSSFeedCreate(BaseModel):
    """Create a new user RSS feed."""

    name: str
    url: str
    is_active: bool = True
    source: str | None = None
    torrent_type: str = "public"
    auto_detect_catalog: bool = False
    parsing_patterns: RSSFeedParsingPatterns | None = None
    filters: RSSFeedFilters | None = None
    catalog_patterns: list[CatalogPattern] | None = Field(default_factory=list)


class UserRSSFeedUpdate(BaseModel):
    """Update an existing user RSS feed."""

    name: str | None = None
    url: str | None = None
    is_active: bool | None = None
    source: str | None = None
    torrent_type: str | None = None
    auto_detect_catalog: bool | None = None
    parsing_patterns: RSSFeedParsingPatterns | None = None
    filters: RSSFeedFilters | None = None
    catalog_patterns: list[CatalogPattern] | None = None


class UserRSSFeedResponse(BaseModel):
    """Response schema for user RSS feed."""

    id: str
    user_id: str
    name: str
    url: str
    is_active: bool = True
    source: str | None = None
    torrent_type: str = "public"
    auto_detect_catalog: bool = False
    parsing_patterns: RSSFeedParsingPatterns | None = None
    filters: RSSFeedFilters | None = None
    metrics: RSSFeedMetrics | None = None
    catalog_patterns: list[UserRSSFeedCatalogPatternSchema] = Field(default_factory=list)
    last_scraped_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    user: UserRSSFeedOwner | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_sql_model(cls, feed, include_user: bool = False) -> "UserRSSFeedResponse":
        """Create from SQL model UserRSSFeed."""
        response = cls(
            id=feed.id,
            user_id=feed.user_id,
            name=feed.name,
            url=feed.url,
            is_active=feed.is_active,
            source=feed.source,
            torrent_type=feed.torrent_type,
            auto_detect_catalog=feed.auto_detect_catalog,
            parsing_patterns=RSSFeedParsingPatterns(**feed.parsing_patterns) if feed.parsing_patterns else None,
            filters=RSSFeedFilters(**feed.filters) if feed.filters else None,
            metrics=RSSFeedMetrics(**feed.metrics) if feed.metrics else None,
            catalog_patterns=[
                UserRSSFeedCatalogPatternSchema(
                    id=p.id,
                    name=p.name,
                    regex=p.regex,
                    enabled=p.enabled,
                    case_sensitive=p.case_sensitive,
                    target_catalogs=p.target_catalogs,
                )
                for p in (feed.catalog_patterns or [])
            ],
            last_scraped_at=feed.last_scraped_at,
            created_at=feed.created_at,
            updated_at=feed.updated_at,
        )
        if include_user and hasattr(feed, "user") and feed.user:
            response.user = UserRSSFeedOwner(
                id=feed.user.id,
                email=feed.user.email,
                username=feed.user.username,
            )
        return response


class UserRSSFeedTestRequest(BaseModel):
    """Request to test an RSS feed URL."""

    url: str
    patterns: dict | None = None


class UserRSSFeedTestResponse(BaseModel):
    """Response from testing an RSS feed."""

    status: str
    message: str
    sample_item: dict | None = None
    detected_patterns: dict | None = None
    items_count: int | None = None
    regex_results: dict | None = None


class RSSSchedulerStatus(BaseModel):
    """RSS scheduler status information."""

    crontab: str
    next_run: datetime | None = None
    enabled: bool
    last_global_run: datetime | None = None

"""Reference/lookup tables for media data."""

from sqlalchemy import Column, Computed, Index
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import Field, SQLModel, UniqueConstraint

# ============================================
# Catalog Display Name Mappings
# ============================================

CATALOG_DISPLAY_NAMES = {
    # Scraper-based catalogs
    "prowlarr_movies": "Prowlarr Movies",
    "prowlarr_series": "Prowlarr Series",
    "prowlarr_streams": "Prowlarr",
    "zilean_dmm_movies": "Zilean Movies",
    "zilean_dmm_series": "Zilean Series",
    "zilean_dmm_streams": "Zilean",
    "torrentio_streams": "Torrentio",
    "jackett_movies": "Jackett Movies",
    "jackett_series": "Jackett Series",
    "jackett_streams": "Jackett",
    "yts_movies": "YTS Movies",
    "yts_streams": "YTS",
    "bt4g_streams": "BT4G",
    "rss_feed_movies": "RSS Feeds",
    # Contribution catalogs
    "contribution_movies": "Community Movies",
    "contribution_series": "Community Series",
    # Language-specific movies
    "english_hdrip": "English HD",
    "english_tcrip": "English TC",
    "english_series": "English Series",
    "hindi_hdrip": "Hindi HD",
    "hindi_tcrip": "Hindi TC",
    "hindi_dubbed": "Hindi Dubbed",
    "hindi_series": "Hindi Series",
    "hindi_old": "Hindi Classic",
    "tamil_hdrip": "Tamil HD",
    "tamil_tcrip": "Tamil TC",
    "tamil_dubbed": "Tamil Dubbed",
    "tamil_series": "Tamil Series",
    "tamil_old": "Tamil Classic",
    "telugu_hdrip": "Telugu HD",
    "telugu_tcrip": "Telugu TC",
    "telugu_dubbed": "Telugu Dubbed",
    "telugu_series": "Telugu Series",
    "telugu_old": "Telugu Classic",
    "malayalam_hdrip": "Malayalam HD",
    "malayalam_tcrip": "Malayalam TC",
    "malayalam_dubbed": "Malayalam Dubbed",
    "malayalam_series": "Malayalam Series",
    "malayalam_old": "Malayalam Classic",
    "kannada_hdrip": "Kannada HD",
    "kannada_tcrip": "Kannada TC",
    "kannada_dubbed": "Kannada Dubbed",
    "kannada_series": "Kannada Series",
    "kannada_old": "Kannada Classic",
    "punjabi_movies": "Punjabi Movies",
    "punjabi_series": "Punjabi Series",
    "bangla_movies": "Bangla Movies",
    "bangla_series": "Bangla Series",
    "bangala_movies": "Bangla Movies",  # Typo variant
    "arabic_movies": "Arabic Movies",
    "arabic_series": "Arabic Series",
    # Anime catalogs
    "anime_movies": "Anime Movies",
    "anime_series": "Anime Series",
    # Sports catalogs
    "football": "Football/Soccer",
    "american_football": "American Football",
    "basketball": "Basketball",
    "baseball": "Baseball",
    "hockey": "Hockey",
    "rugby": "Rugby",
    "fighting": "Combat Sports",
    "formula_racing": "Formula Racing",
    "motogp_racing": "MotoGP",
    "other_sports": "Other Sports",
}


def format_catalog_name(name: str) -> str:
    """
    Format catalog name for display.
    Returns mapped display name or auto-formatted version.
    """
    if name in CATALOG_DISPLAY_NAMES:
        return CATALOG_DISPLAY_NAMES[name]

    # Auto-format: replace underscores with spaces and title case
    # e.g., "prowlarr_movies" -> "Prowlarr Movies"
    formatted = name.replace("_", " ").title()

    # Handle common abbreviations
    abbreviations = {
        "Hdrip": "HD",
        "Tcrip": "TC",
        "Dmm": "DMM",
        "Yts": "YTS",
        "Bt4g": "BT4G",
        "Rss": "RSS",
        "Motogp": "MotoGP",
    }
    for old, new in abbreviations.items():
        formatted = formatted.replace(old, new)

    return formatted


class Genre(SQLModel, table=True):
    """Genre lookup table."""

    __tablename__ = "genre"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class Catalog(SQLModel, table=True):
    """Catalog lookup table."""

    __tablename__ = "catalog"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    display_name: str | None = None  # Human-readable name (auto-generated if not set)
    description: str | None = None
    is_system: bool = Field(default=False)  # system vs discoverable
    display_order: int = Field(default=100)

    @property
    def display(self) -> str:
        """Get display name, falling back to formatted name."""
        if self.display_name:
            return self.display_name
        return format_catalog_name(self.name)


class AkaTitle(SQLModel, table=True):
    """Alternative titles for media (AKA - Also Known As)."""

    __tablename__ = "aka_title"
    __table_args__ = (
        UniqueConstraint("media_id", "title"),
        Column(
            "title_tsv",
            TSVECTOR,
            Computed("to_tsvector('simple'::regconfig, title)"),
            nullable=False,
        ),
        Index("idx_aka_title_fts", "title_tsv", postgresql_using="gin"),
        Index(
            "idx_aka_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: int = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    media_id: int = Field(foreign_key="media.id", index=True, ondelete="CASCADE")
    language_code: str | None = None


class ParentalCertificate(SQLModel, table=True):
    """Parental certificate/rating lookup table."""

    __tablename__ = "parental_certificate"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class Language(SQLModel, table=True):
    """Language lookup table.

    Stores language names as parsed from torrent titles (e.g., "Hindi", "Japanese", "English").
    """

    __tablename__ = "language"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # Language name


class AudioFormat(SQLModel, table=True):
    """Audio format/codec lookup table.

    Stores normalized audio formats like: AAC, AC3, DTS, DTS-HD,
    Atmos, TrueHD, EAC3, FLAC, Opus, MP3, etc.
    """

    __tablename__ = "audio_format"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # AAC, DTS, Atmos, TrueHD


class AudioChannel(SQLModel, table=True):
    """Audio channel configuration lookup table.

    Stores normalized channel configurations like: 2.0 (stereo), 5.1 (surround),
    7.1, Atmos (object-based), etc.
    """

    __tablename__ = "audio_channel"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # 2.0, 5.1, 7.1, Atmos


class HDRFormat(SQLModel, table=True):
    """HDR format lookup table.

    Stores normalized HDR formats like: HDR10, HDR10+, Dolby Vision, HLG, etc.
    A stream can have multiple HDR formats (e.g., HDR10 + Dolby Vision combo).
    """

    __tablename__ = "hdr_format"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)  # HDR10, HDR10+, Dolby Vision, HLG


class Keyword(SQLModel, table=True):
    """Keyword/tag lookup table."""

    __tablename__ = "keyword"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)


class ProductionCompany(SQLModel, table=True):
    """Production company lookup table."""

    __tablename__ = "production_company"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    logo_url: str | None = None
    origin_country: str | None = None
    tmdb_id: int | None = Field(default=None, unique=True)


# Simple Pydantic schemas for returning just names
class GenreName(SQLModel):
    """Schema for returning just genre name."""

    name: str


class CatalogName(SQLModel):
    """Schema for returning just catalog name."""

    name: str


class ParentalCertificateName(SQLModel):
    """Schema for returning just certificate name."""

    name: str


class AkaTitleName(SQLModel):
    """Schema for returning just AKA title."""

    title: str

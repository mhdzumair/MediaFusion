"""
Unified sports title parsing utilities.

This module consolidates all sports-related title parsing logic from multiple
scattered locations into a single reusable module.

Exports:
    - SPORTS_CATEGORIES: Category key to display name mapping
    - SPORTS_CATEGORY_KEYWORDS: Category key to detection keywords mapping
    - detect_sports_category(): Auto-detect sports category from title
    - clean_sports_event_title(): Remove quality/codec markers from title
    - parse_sports_title(): Full parsing with structured output
    - SportsParsedTitle: Dataclass with parsed title components
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# =============================================================================
# Constants
# =============================================================================

# Sports categories matching the catalog names in the database
SPORTS_CATEGORIES: dict[str, str] = {
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

# Keywords to auto-detect sports category from title
# Merged from telegram_bot.py and rss_scraper.py
# NOTE: Order matters! League identifiers should come FIRST in each category
# to ensure they're matched before potentially conflicting team names.
# Categories are ordered from most specific to most generic.
SPORTS_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    # Hockey FIRST - "nhl" and "stanley cup" are unique identifiers
    # Must come before american_football since teams like Panthers, Oilers overlap
    "hockey": [
        # League identifiers - MUST BE FIRST
        "nhl",
        "stanley cup",
        "khl",
        "iihf",
        # Teams (only check after league is confirmed by title structure)
        "bruins",
        "maple leafs",
        "canadiens",
        "blackhawks",
        "penguins",
        "red wings",
        "flyers",
        "flames",
        "canucks",
        "avalanche",
        "lightning",
        "golden knights",
        "kraken",
        "senators",
        "sabres",
        "devils",
        "islanders",
        "capitals",
        "blue jackets",
        "coyotes",
    ],
    # Rugby - "nrl", "super rugby", "six nations" are unique
    # Must come before american_football since "chiefs", "panthers" overlap
    "rugby": [
        # League identifiers - MUST BE FIRST
        "rugby",
        "six nations",
        "rugby world cup",
        "super rugby",
        "premiership rugby",
        "top 14",
        "pro14",
        "nrl",
        "australian football",
        # Teams
        "all blacks",
        "springboks",
        "wallabies",
        "england rugby",
        "ireland rugby",
        "wales rugby",
        "scotland rugby",
        "france rugby",
        "crusaders",
    ],
    # Baseball - "mlb", "npb", "kbo" are unique
    # Must come before american_football since "giants", "cardinals", "rangers" overlap
    "baseball": [
        # League identifiers - MUST BE FIRST
        "mlb",
        "npb",
        "kbo",
        "japan series",
        # Teams (mostly unique to baseball)
        # NOTE: "athletics" removed - too ambiguous with track & field
        # Oakland A's should be detected via "mlb" or "oakland athletics"
        "yankees",
        "dodgers",
        "red sox",
        "cubs",
        "astros",
        "braves",
        "mets",
        "phillies",
        "padres",
        "mariners",
        "blue jays",
        "twins",
        "white sox",
        "guardians",
        "orioles",
        "tigers",
        "royals",
        "angels",
        "rockies",
        "marlins",
        "brewers",
        "pirates",
        "diamondbacks",
        "nationals",
        "oakland athletics",  # Full team name to avoid ambiguity
    ],
    # MotoGP before formula_racing (both have "gp" keywords)
    "motogp_racing": [
        # Series - most specific keywords first
        "motogp",
        "moto gp",
        "moto2",
        "moto3",
        "superbike",
        "wsbk",
        "bsb",
        "worldsbk",
        "isle of man",
        # Riders
        "marquez",
        "bagnaia",
        "quartararo",
        # Manufacturers (unique to MotoGP context)
        "ducati",
        "ktm",
        "aprilia",
    ],
    # Fighting - UFC, WWE, Boxing, etc.
    # Removed generic "heavyweight" etc. that could match other sports
    "fighting": [
        # Organizations - MUST BE FIRST
        "ufc",
        "mma",
        "boxing",
        "wwe",
        "aew",
        "bellator",
        "one championship",
        "pfl",
        "cage warriors",
        "glory",
        "pride fc",
        "rizin",
        "pwg",
        "pro wrestling",
        # Sports
        "kickboxing",
        "muay thai",
        "k-1",
        # WWE shows (unique identifiers)
        "monday night raw",
        "smackdown",
        "wrestlemania",
        "summerslam",
        "royal rumble",
        "nxt",
        # Events (unique to combat sports)
        "title fight",
        "championship fight",
        # Fighters (well-known names)
        "canelo",
        "fury",
        "joshua",
        "wilder",
        "mcgregor",
        "khabib",
        "adesanya",
        "ngannou",
    ],
    # American Football - NFL
    "american_football": [
        # League identifiers - MUST BE FIRST
        "nfl",
        "super bowl",
        "nfc championship",
        "afc championship",
        "college football",
        "ncaa football",
        # Teams (checked only after league context)
        "patriots",
        "cowboys",
        "packers",
        "49ers",
        "seahawks",
        "ravens",
        "bills",
        "eagles",
        "broncos",
        "steelers",
        "raiders",
        "chargers",
        "dolphins",
        "jets",
        "bears",
        "lions",
        "vikings",
        "saints",
        "falcons",
        "buccaneers",
        "rams",
        "titans",
        "colts",
        "texans",
        "jaguars",
        "bengals",
        "browns",
        "commanders",
    ],
    # Basketball - NBA, WNBA
    "basketball": [
        # League identifiers - MUST BE FIRST
        "nba",
        "wnba",
        "ncaa basketball",
        "march madness",
        "euroleague",
        "fiba",
        # Teams (mostly unique to basketball)
        "lakers",
        "celtics",
        "warriors",
        "nets",
        "knicks",
        "bulls",
        "heat",
        "bucks",
        "suns",
        "clippers",
        "mavericks",
        "76ers",
        "nuggets",
        "grizzlies",
        "cavaliers",
        "raptors",
        "spurs",
        "jazz",
        "pelicans",
        "blazers",
        "timberwolves",
        "thunder",
        "pistons",
        "hornets",
        "pacers",
        "magic",
        "wizards",
    ],
    # Formula Racing - F1, F2, F3, IndyCar, NASCAR
    "formula_racing": [
        # Series identifiers - MUST BE FIRST
        "formula 1",
        "formula1",
        "f1 ",
        " f1",
        "formula 2",
        "formula 3",
        "f2 ",
        "f3 ",
        "indycar",
        "indy 500",
        "nascar",
        "wec",
        "le mans",
        # Events
        "grand prix",
        "monaco",
        "silverstone",
        "spa",
        "monza",
        "suzuka",
        "interlagos",
        "daytona",
        # Teams
        "ferrari",
        "mercedes",
        "red bull racing",
        "mclaren",
        "alpine",
        "aston martin",
        "williams",
        "haas",
        "alfa romeo",
        "alphatauri",
        # Drivers
        "verstappen",
        "hamilton",
        "leclerc",
        "sainz",
        "norris",
        "perez",
    ],
    # Football/soccer last since it has generic keywords like "fc"
    "football": [
        # League identifiers - MUST BE FIRST
        "fifa",
        "uefa",
        "premier league",
        "la liga",
        "bundesliga",
        "serie a",
        "ligue 1",
        "champions league",
        "europa league",
        "world cup",
        "euro 20",
        "epl",
        "efl",
        "copa america",
        "copa libertadores",
        "copa sudamericana",
        "concacaf",
        "mls",
        "eredivisie",
        "scottish premier",
        "primeira liga",
        # Generic
        "soccer",
        "fc ",
        " fc",
        # Top clubs
        "manchester",
        "liverpool",
        "chelsea",
        "arsenal",
        "tottenham",
        "barcelona",
        "real madrid",
        "juventus",
        "psg",
        "bayern",
        "inter milan",
        "ac milan",
        "dortmund",
        "ajax",
    ],
}

# General sports keywords for fallback detection
# NOTE: Avoid overly generic words like "game" that cause false positives
GENERAL_SPORTS_KEYWORDS: list[str] = [
    "sport",
    "sports",
    "match",
    "vs",
    "versus",
    "highlights",
    "replay",
    "tournament",
    "championship",
    "espn",
    "sky sports",
    "bt sport",
    "bein",
    "fox sports",
    "nbc sports",
    "match of the day",
    "motd",
    # Individual sports (not categorized separately)
    "golf",
    "tennis",
    "cricket",
    "cycling",
    "swimming",
    "athletics",
    "olympics",
    "marathon",
    "triathlon",
    "snooker",
    "darts",
    "bowling",
]

# Resolution mapping from various formats to standardized values
RESOLUTION_MAP: dict[str, str] = {
    "3840x2160": "4k",
    "2560x1440": "1440p",
    "1920x1080": "1080p",
    "1280x720": "720p",
    "854x480": "480p",
    "640x360": "360p",
    "426x240": "240p",
    "4K": "4k",
    "UHD": "4k",
    "2160p": "4k",
    "SD": "576p",
}

# Quality indicators to strip from titles
QUALITY_INDICATORS: list[str] = [
    "2160p",
    "1080p",
    "720p",
    "480p",
    "360p",
    "240p",
    "4K",
    "UHD",
    "HDTV",
    "WEB-DL",
    "WEBDL",
    "WEBRip",
    "BluRay",
    "BDRip",
    "HDRip",
    "DVDRip",
    "PDTV",
    "SDTV",
    "WEB",
    "Sportnet360",
]

# Codec indicators to strip from titles
CODEC_INDICATORS: list[str] = [
    "H.264",
    "H264",
    "H.265",
    "H265",
    "HEVC",
    "x264",
    "x265",
    "AVC",
    "XviD",
    "DivX",
    "VP9",
    "AV1",
]

# Audio indicators to strip from titles
AUDIO_INDICATORS: list[str] = [
    "AAC",
    "AAC2.0",
    "AC3",
    "DTS",
    "DD5.1",
    "DD2.0",
    "FLAC",
    "MP3",
    "EAC3",
    "TrueHD",
    "Atmos",
    "Multi",
]

# Release flags to strip from titles
RELEASE_FLAGS: list[str] = [
    "PROPER",
    "REPACK",
    "INTERNAL",
    "LIMITED",
    "UNRATED",
    "EXTENDED",
    "RERIP",
    "REAL",
    "READNFO",
    "DIRFIX",
    "NFOFIX",
]

# Common sports release groups
SPORTS_RELEASE_GROUPS: list[str] = [
    "DARKSPORT",
    "SPORT720",
    "SPORT480",
    "VERUM",
    "HDCTV",
    "SKYSPORT",
    "F1CARRERAS",
    "EGORTECH",
    "SMCGILL1969",
]

# Date format patterns
DATE_PATTERNS: list[tuple[str, str]] = [
    (r"\d{4}\.\d{2}\.\d{2}", "%Y.%m.%d"),  # 2026.02.08
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),  # 2026-02-08
    (r"\d{2}\.\d{2}\.\d{4}", "%d.%m.%Y"),  # 08.02.2026
    (r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),  # 08-02-2026
    (r"\d{4}_\d{2}_\d{2}", "%Y_%m_%d"),  # 2026_02_08
]


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SportsParsedTitle:
    """Parsed sports title with extracted components."""

    # Core title info
    title: str  # Clean display title
    raw_title: str  # Original input
    category: str | None = None  # Sports category key

    # Event details
    event: str | None = None  # Event name (e.g., "Super Bowl LX")
    league: str | None = None  # League/organization (e.g., "NFL")
    teams: list[str] = field(default_factory=list)  # Teams involved

    # Date/time info
    event_date: date | None = None
    year: int | None = None
    round_number: int | None = None  # For racing series

    # Technical specs
    resolution: str | None = None
    quality: str | None = None
    codec: str | None = None
    audio: str | None = None
    languages: list[str] = field(default_factory=list)

    # Release info
    release_group: str | None = None
    broadcaster: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "raw_title": self.raw_title,
            "category": self.category,
            "event": self.event,
            "league": self.league,
            "teams": self.teams,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "year": self.year,
            "round_number": self.round_number,
            "resolution": self.resolution,
            "quality": self.quality,
            "codec": self.codec,
            "audio": self.audio,
            "languages": self.languages,
            "release_group": self.release_group,
            "broadcaster": self.broadcaster,
        }


# =============================================================================
# Core Functions
# =============================================================================


def detect_sports_category(title: str) -> str | None:
    """Detect sports category from title using keyword matching.

    Normalizes the title by replacing dots, underscores, and dashes
    with spaces before matching to handle filenames like
    "NFL.2026.Super.Bowl" or "Formula_1_Monaco_GP".

    Uses a two-pass approach:
    1. First pass: Look for league/organization identifiers (first few keywords
       in each category) which are highly reliable
    2. Second pass: If no league found, check team names and other keywords

    Uses word-boundary matching for keywords that could be substrings
    of other words (e.g., "fc " shouldn't match inside "ufc").

    Args:
        title: The title/filename to analyze

    Returns:
        Sports category key if detected, None otherwise
    """
    if not title:
        return None

    # Normalize: replace dots, underscores, dashes with spaces for better matching
    title_normalized = re.sub(r"[._-]+", " ", title.lower())
    # Add padding for word boundary matching
    title_padded = f" {title_normalized} "

    # Keywords that need word-boundary matching (could be substrings)
    # These are checked with word boundaries to avoid false positives
    # Format: keyword -> we check " keyword " in padded title
    boundary_keywords = {
        "fc",  # "fc" shouldn't match "ufc"
        "spa",  # F1 circuit, shouldn't match "spain"
        "raw",  # WWE show, shouldn't match other words
        "kbo",  # Korean Baseball - shouldn't match "kickboxing"
    }

    # League/organization identifiers that are highly reliable
    # These should be checked with priority
    # ORDER MATTERS: More specific identifiers first, avoid substring issues
    # (e.g., "nfl" before "mma" since "commanders" contains "mma")
    league_identifiers: dict[str, list[str]] = {
        "american_football": ["nfl", "super bowl", "nfc championship", "afc championship"],
        "hockey": ["nhl", "stanley cup", "khl", "iihf"],
        "rugby": ["rugby", "six nations", "super rugby", "nrl", "afl"],
        "baseball": ["mlb", "npb", "kbo", "japan series", "world series"],
        "motogp_racing": ["motogp", "moto gp", "moto2", "moto3", "wsbk", "worldsbk", "superbike"],
        "fighting": ["ufc", "wwe", "aew", "bellator", "boxing", "wrestlemania"],
        "basketball": ["nba", "wnba", "march madness", "euroleague", "fiba"],
        "formula_racing": ["formula 1", "formula1", "f1 ", " f1", "indycar", "nascar"],
        "football": ["fifa", "uefa", "premier league", "champions league", "la liga", "bundesliga"],
    }

    def matches_keyword(keyword: str, normalized: str, padded: str) -> bool:
        """Check if keyword matches, using word boundaries for ambiguous keywords."""
        # Strip any existing padding from keyword for comparison
        kw_clean = keyword.strip()
        if kw_clean in boundary_keywords:
            # Require word boundaries
            return f" {kw_clean} " in padded
        else:
            return keyword in normalized

    # First pass: Check league identifiers (high confidence)
    for category, identifiers in league_identifiers.items():
        for identifier in identifiers:
            if matches_keyword(identifier, title_normalized, title_padded):
                return category

    # Second pass: Check all keywords including team names
    for category, keywords in SPORTS_CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if matches_keyword(keyword, title_normalized, title_padded):
                return category

    # Check for general sports keywords -> other_sports
    for keyword in GENERAL_SPORTS_KEYWORDS:
        if matches_keyword(keyword, title_normalized, title_padded):
            return "other_sports"

    return None


def clean_sports_event_title(raw_name: str) -> str:
    """Clean up a sports event filename into a readable title.

    PTT (parse-torrent-title) doesn't handle sports event names well.
    This function cleans up the raw torrent/file name for sports content.

    Example:
        Input: "NFL.2026.02.08.Super.Bowl.LX.Seattle.Seahawks.Vs.New.England.Patriots.1080p.HDTV.H264-DARKSPORT"
        Output: "NFL 2026 02 08 Super Bowl LX Seattle Seahawks Vs New England Patriots"

    Args:
        raw_name: Raw torrent name or filename

    Returns:
        Cleaned event title
    """
    if not raw_name:
        return "Sports Event"

    cleaned = raw_name

    # Remove common release group suffixes (e.g., "-DARKSPORT", "-SPORT720")
    cleaned = re.sub(r"-[A-Za-z0-9]+$", "", cleaned)

    # Remove file extension if present
    cleaned = re.sub(
        r"\.(mkv|mp4|avi|m4v|ts|webm|mov|wmv|flv)$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Build pattern for quality/codec/audio/release indicators
    all_indicators = QUALITY_INDICATORS + CODEC_INDICATORS + AUDIO_INDICATORS + RELEASE_FLAGS

    # Remove quality/codec indicators
    for indicator in all_indicators:
        # Match with optional leading dot/space
        pattern = rf"[.\s]?{re.escape(indicator)}(?=[.\s]|$)"
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Replace dots, underscores, dashes with spaces
    cleaned = re.sub(r"[._-]+", " ", cleaned)

    # Remove multiple spaces and trim
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned if cleaned else "Sports Event"


def normalize_resolution(resolution: str | None) -> str | None:
    """Normalize resolution to standard format.

    Args:
        resolution: Raw resolution string (e.g., "4K", "1080P", "1920x1080")

    Returns:
        Normalized resolution (e.g., "4k", "1080p") or None
    """
    if not resolution:
        return None

    resolution_clean = resolution.strip()

    # Check direct mapping
    if resolution_clean in RESOLUTION_MAP:
        return RESOLUTION_MAP[resolution_clean]

    # Normalize case
    resolution_lower = resolution_clean.lower()

    # Handle "P" suffix
    if resolution_lower.endswith("p"):
        return resolution_lower

    # Handle "K" suffix
    if resolution_lower.endswith("k"):
        return resolution_lower

    # Try to extract height from dimensions (e.g., "1920x1080")
    normalized = resolution_clean.replace("×", "x").replace("х", "x")
    if "x" in normalized:
        height = normalized.split("x")[-1]
        for res_key, res_val in RESOLUTION_MAP.items():
            if res_key.endswith(height):
                return res_val
        return f"{height}p" if height.isdigit() else None

    return resolution_lower if resolution_lower else None


def extract_date_from_title(title: str) -> tuple[date | None, str | None]:
    """Extract date from title string.

    Args:
        title: Title string potentially containing a date

    Returns:
        Tuple of (extracted date, matched date string) or (None, None)
    """
    if not title:
        return None, None

    for pattern, date_format in DATE_PATTERNS:
        match = re.search(pattern, title)
        if match:
            date_str = match.group()
            try:
                parsed_date = datetime.strptime(date_str, date_format).date()
                return parsed_date, date_str
            except ValueError:
                continue

    return None, None


def extract_release_group(title: str) -> str | None:
    """Extract release group from title.

    Args:
        title: Title string (may end with "-GROUP")

    Returns:
        Release group name or None
    """
    if not title:
        return None

    # Match release group at end of title (after last hyphen)
    match = re.search(r"-([A-Za-z0-9]+)$", title)
    if match:
        group = match.group(1).upper()
        # Verify it looks like a release group (not a codec or quality)
        all_tech = QUALITY_INDICATORS + CODEC_INDICATORS + AUDIO_INDICATORS
        if group not in [t.upper() for t in all_tech]:
            return group

    return None


def extract_teams_from_title(title: str) -> list[str]:
    """Extract team names from "Team1 vs Team2" pattern.

    Args:
        title: Title string

    Returns:
        List of team names (usually 2) or empty list
    """
    if not title:
        return []

    # Normalize separators
    normalized = re.sub(r"[._-]+", " ", title)

    # Look for "vs" or "versus" pattern
    patterns = [
        r"(.+?)\s+(?:vs\.?|versus|v\.?)\s+(.+)",
        r"(.+?)\s+@\s+(.+)",  # "Team1 @ Team2" format
    ]

    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            team1 = match.group(1).strip()
            team2 = match.group(2).strip()

            # Clean up team names (remove quality indicators from team2)
            for indicator in QUALITY_INDICATORS + CODEC_INDICATORS:
                team2 = re.sub(
                    rf"\s*{re.escape(indicator)}.*$",
                    "",
                    team2,
                    flags=re.IGNORECASE,
                )

            return [team1.strip(), team2.strip()] if team1 and team2 else []

    return []


def extract_round_number(title: str) -> int | None:
    """Extract round number from title (for racing series).

    Args:
        title: Title string

    Returns:
        Round number or None
    """
    if not title:
        return None

    # Patterns for round number
    patterns = [
        r"[Rr](?:ound)?[.\s]*(\d{1,2})",  # R01, Round 5, R.01
        r"x(\d{2})",  # 2024x03 format (season x round)
    ]

    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue

    return None


def parse_sports_title(
    title: str,
    category: str | None = None,
    fallback_date: date | None = None,
) -> SportsParsedTitle:
    """Parse a sports title into structured components.

    This is the main entry point for sports title parsing. It combines
    category detection, title cleaning, and component extraction.

    Args:
        title: Raw title/filename to parse
        category: Optional category override (skip auto-detection)
        fallback_date: Date to use if none found in title

    Returns:
        SportsParsedTitle with extracted components
    """
    if not title:
        return SportsParsedTitle(
            title="Sports Event",
            raw_title=title or "",
            category=category,
        )

    # Detect category if not provided
    detected_category = category or detect_sports_category(title)

    # Extract release group before cleaning
    release_group = extract_release_group(title)

    # Extract date
    event_date, date_str = extract_date_from_title(title)
    if not event_date and fallback_date:
        event_date = fallback_date

    # Extract round number (for racing)
    round_number = None
    if detected_category in ("formula_racing", "motogp_racing"):
        round_number = extract_round_number(title)

    # Extract teams
    teams = extract_teams_from_title(title)

    # Extract technical specs before cleaning
    resolution = _extract_tech_spec(title, QUALITY_INDICATORS[:8])  # Resolution only
    quality = _extract_tech_spec(title, QUALITY_INDICATORS[8:])  # Quality type
    codec = _extract_tech_spec(title, CODEC_INDICATORS)
    audio = _extract_tech_spec(title, AUDIO_INDICATORS)

    # Clean the title
    clean_title = clean_sports_event_title(title)

    # Extract league/organization
    league = _extract_league(title, detected_category)

    # Build event name
    event = _build_event_name(clean_title, league, teams, date_str)

    # Normalize resolution
    resolution = normalize_resolution(resolution)

    return SportsParsedTitle(
        title=clean_title,
        raw_title=title,
        category=detected_category,
        event=event,
        league=league,
        teams=teams,
        event_date=event_date,
        year=event_date.year if event_date else None,
        round_number=round_number,
        resolution=resolution,
        quality=quality,
        codec=codec,
        audio=audio,
        release_group=release_group,
    )


# =============================================================================
# Helper Functions
# =============================================================================


def _extract_tech_spec(title: str, indicators: list[str]) -> str | None:
    """Extract first matching technical spec from title."""
    title_upper = title.upper()
    for indicator in indicators:
        if indicator.upper() in title_upper:
            return indicator
    return None


def _extract_league(title: str, category: str | None) -> str | None:
    """Extract league/organization from title based on category."""
    if not title:
        return None

    title_upper = title.upper()
    normalized = re.sub(r"[._-]+", " ", title_upper)

    # Category-specific league detection
    league_patterns: dict[str | None, list[str]] = {
        "american_football": ["NFL", "NCAA", "XFL", "USFL", "CFL"],
        "basketball": ["NBA", "WNBA", "NCAA", "EUROLEAGUE", "FIBA"],
        "baseball": ["MLB", "NPB", "KBO"],
        "hockey": ["NHL", "KHL", "AHL"],
        "fighting": ["UFC", "WWE", "AEW", "BELLATOR", "PFL", "ONE"],
        "formula_racing": ["F1", "F2", "F3", "FORMULA 1", "FORMULA 2", "FORMULA 3", "INDYCAR", "NASCAR"],
        "motogp_racing": ["MOTOGP", "MOTO2", "MOTO3", "WSBK"],
        "football": ["UEFA", "FIFA", "EPL", "LA LIGA", "BUNDESLIGA", "SERIE A", "LIGUE 1", "MLS"],
        "rugby": ["NRL", "AFL", "SUPER RUGBY"],
    }

    # Check category-specific patterns first
    if category in league_patterns:
        for league in league_patterns[category]:
            if league in normalized or league.replace(" ", "") in title_upper:
                return league

    # Generic check across all leagues
    for leagues in league_patterns.values():
        for league in leagues:
            if league in normalized or league.replace(" ", "") in title_upper:
                return league

    return None


def _build_event_name(
    clean_title: str,
    league: str | None,
    teams: list[str],
    date_str: str | None,
) -> str | None:
    """Build a concise event name from components."""
    if not clean_title:
        return None

    # If we have teams, the event is the matchup
    if len(teams) == 2:
        return f"{teams[0]} vs {teams[1]}"

    # Try to extract event name by removing league and date from clean title
    event = clean_title

    if league:
        event = re.sub(rf"^{re.escape(league)}\s*", "", event, flags=re.IGNORECASE)

    if date_str:
        # Remove date patterns
        for pattern, _ in DATE_PATTERNS:
            event = re.sub(pattern, "", event)

    # Clean up
    event = re.sub(r"\s+", " ", event).strip()

    return event if event else None


# =============================================================================
# Sport-Specific Parsers
# =============================================================================


def parse_f1_title(title: str) -> SportsParsedTitle:
    """Parse Formula 1/2/3 title with racing-specific extraction.

    Handles formats from uploaders: egortech, F1Carreras, smcgill1969

    Args:
        title: F1 torrent title

    Returns:
        SportsParsedTitle with F1-specific fields populated
    """
    result = parse_sports_title(title, category="formula_racing")

    # Extract series number (F1, F2, F3)
    series_match = re.search(r"Formula[.\s]?([123])", title, re.IGNORECASE)
    if series_match:
        series_num = series_match.group(1)
        result.league = f"Formula {series_num}"

    # Extract broadcaster
    broadcaster_patterns = [
        r"(SkyF1(?:HD|UHD)?)",
        r"(Sky Sports (?:F1|Main Event)(?: UHD)?)",
        r"(Sky Sports Arena)",
        r"(V Sport Ultra HD)",
        r"(F1TV)",
        r"(SkySports)",
        r"(BTSportHD|TNTSportsHD)",
    ]
    for pattern in broadcaster_patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            result.broadcaster = match.group(1)
            break

    return result


def parse_ufc_title(title: str) -> SportsParsedTitle:
    """Parse UFC event title.

    Args:
        title: UFC torrent title

    Returns:
        SportsParsedTitle with UFC-specific fields populated
    """
    result = parse_sports_title(title, category="fighting")
    result.league = "UFC"

    # Extract UFC event number if present
    event_match = re.search(r"UFC[.\s]*(\d+)", title, re.IGNORECASE)
    if event_match:
        result.event = f"UFC {event_match.group(1)}"

    return result


def parse_wwe_title(title: str) -> SportsParsedTitle:
    """Parse WWE event title.

    Args:
        title: WWE torrent title

    Returns:
        SportsParsedTitle with WWE-specific fields populated
    """
    result = parse_sports_title(title, category="fighting")
    result.league = "WWE"

    # Known WWE shows with IMDb IDs
    wwe_shows = {
        "raw": "WWE Raw",
        "monday night raw": "WWE Monday Night Raw",
        "smackdown": "WWE SmackDown",
        "friday night smackdown": "WWE Friday Night SmackDown",
        "nxt": "WWE NXT",
        "main event": "WWE Main Event",
    }

    title_lower = title.lower()
    for show_key, show_name in wwe_shows.items():
        if show_key in title_lower:
            result.event = show_name
            break

    return result


def parse_nfl_title(title: str) -> SportsParsedTitle:
    """Parse NFL game title.

    Args:
        title: NFL torrent title

    Returns:
        SportsParsedTitle with NFL-specific fields populated
    """
    result = parse_sports_title(title, category="american_football")
    result.league = "NFL"

    # Check for special events
    if "super bowl" in title.lower():
        # Extract Super Bowl number
        sb_match = re.search(
            r"Super[.\s]*Bowl[.\s]*([IVXLCDM]+|\d+)",
            title,
            re.IGNORECASE,
        )
        if sb_match:
            result.event = f"Super Bowl {sb_match.group(1)}"

    return result


def parse_nba_title(title: str) -> SportsParsedTitle:
    """Parse NBA game title.

    Args:
        title: NBA torrent title

    Returns:
        SportsParsedTitle with NBA-specific fields populated
    """
    result = parse_sports_title(title, category="basketball")
    result.league = "NBA"

    # Check for special events
    special_events = ["finals", "playoffs", "all-star", "all star"]
    title_lower = title.lower()
    for event in special_events:
        if event in title_lower:
            result.event = f"NBA {event.replace('-', ' ').title()}"
            break

    return result


def parse_motogp_title(title: str) -> SportsParsedTitle:
    """Parse MotoGP title with racing-specific extraction.

    Args:
        title: MotoGP torrent title

    Returns:
        SportsParsedTitle with MotoGP-specific fields populated
    """
    result = parse_sports_title(title, category="motogp_racing")

    # Detect series (MotoGP, Moto2, Moto3)
    if "moto2" in title.lower():
        result.league = "Moto2"
    elif "moto3" in title.lower():
        result.league = "Moto3"
    else:
        result.league = "MotoGP"

    # Extract broadcaster
    broadcaster_match = re.search(
        r"(BTSportHD|TNTSportsHD)",
        title,
        re.IGNORECASE,
    )
    if broadcaster_match:
        result.broadcaster = broadcaster_match.group(1)

    return result

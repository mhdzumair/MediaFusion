/// Sports-specific torrent title parser.
///
/// PTT is optimised for movie/TV torrents and truncates titles at the first
/// year or technical marker.  For sports the *full* event name is essential:
/// "Formula 1 2026 R04 Miami Grand Prix" is far more useful than "Formula 1".
///
/// Public API:
///   - `detect_sports_category(title)` → category key or None
///   - `is_sports_title(title)` → bool
///   - `clean_sports_title(raw)` → human-readable event name
///   - `parse_sports_title(raw)` → ParsedTitle with correct title/year
use std::sync::OnceLock;

use regex::Regex;

// ─── Category detection tables (ported from Python sports_parser.py) ──────────

/// High-confidence league/org identifiers checked in pass 1.
/// Order matters: more specific identifiers first to prevent substring
/// collisions (e.g. "nfl" before "mma" since "commanders" contains "man").
static LEAGUE_IDENTIFIERS: &[(&str, &[&str])] = &[
    (
        "american_football",
        &["nfl", "super bowl", "nfc championship", "afc championship"],
    ),
    ("hockey", &["nhl", "stanley cup", "khl", "iihf"]),
    (
        "rugby",
        &["rugby", "six nations", "super rugby", "nrl", "afl"],
    ),
    (
        "baseball",
        &["mlb", "npb", "kbo", "japan series", "world series"],
    ),
    (
        "motogp_racing",
        &[
            "motogp",
            "moto gp",
            "moto2",
            "moto3",
            "wsbk",
            "worldsbk",
            "superbike",
        ],
    ),
    (
        "fighting",
        &[
            "ufc",
            "wwe",
            "aew",
            "bellator",
            "boxing",
            "wrestlemania",
            "smackdown",
            "monday night raw",
        ],
    ),
    (
        "basketball",
        &["nba", "wnba", "march madness", "euroleague", "fiba"],
    ),
    (
        "formula_racing",
        &[
            "formula 1",
            "formula1",
            "formula 2",
            "formula 3",
            "indycar",
            "nascar",
        ],
    ),
    (
        "football",
        &[
            "fifa",
            "uefa",
            "premier league",
            "champions league",
            "la liga",
            "bundesliga",
        ],
    ),
];

/// Full keyword table including team names — used in pass 2 when no league
/// identifier matched.  Same ordering as the Python source.
static SPORTS_CATEGORY_KEYWORDS: &[(&str, &[&str])] = &[
    (
        "hockey",
        &[
            "nhl",
            "stanley cup",
            "khl",
            "iihf",
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
        ],
    ),
    (
        "rugby",
        &[
            "rugby",
            "six nations",
            "rugby world cup",
            "super rugby",
            "premiership rugby",
            "top 14",
            "pro14",
            "nrl",
            "australian football",
            "all blacks",
            "springboks",
            "wallabies",
            "crusaders",
        ],
    ),
    (
        "baseball",
        &[
            "mlb",
            "npb",
            "kbo",
            "japan series",
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
            "oakland athletics",
        ],
    ),
    (
        "motogp_racing",
        &[
            "motogp",
            "moto gp",
            "moto2",
            "moto3",
            "superbike",
            "wsbk",
            "bsb",
            "worldsbk",
            "isle of man",
            "marquez",
            "bagnaia",
            "quartararo",
            "ducati",
            "ktm",
            "aprilia",
        ],
    ),
    (
        "fighting",
        &[
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
            "pro wrestling",
            "kickboxing",
            "muay thai",
            "monday night raw",
            "smackdown",
            "wrestlemania",
            "summerslam",
            "royal rumble",
            "nxt",
            "title fight",
            "championship fight",
            "canelo",
            "fury",
            "joshua",
            "wilder",
            "mcgregor",
            "khabib",
            "adesanya",
            "ngannou",
        ],
    ),
    (
        "american_football",
        &[
            "nfl",
            "super bowl",
            "nfc championship",
            "afc championship",
            "college football",
            "ncaa football",
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
    ),
    (
        "basketball",
        &[
            "nba",
            "wnba",
            "ncaa basketball",
            "march madness",
            "euroleague",
            "fiba",
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
    ),
    (
        "formula_racing",
        &[
            "formula 1",
            "formula1",
            "formula 2",
            "formula 3",
            "indycar",
            "indy 500",
            "nascar",
            "wec",
            "le mans",
            "grand prix",
            "monaco",
            "silverstone",
            "monza",
            "suzuka",
            "interlagos",
            "daytona",
            "ferrari",
            "mercedes",
            "red bull racing",
            "mclaren",
            "alpine",
            "aston martin",
            "williams",
            "haas",
            "verstappen",
            "hamilton",
            "leclerc",
            "sainz",
            "norris",
            "perez",
        ],
    ),
    (
        "football",
        &[
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
            "epl",
            "efl",
            "copa america",
            "copa libertadores",
            "mls",
            "eredivisie",
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
    ),
];

/// Short keywords that require word-boundary matching to prevent false
/// positives (e.g. "fc" inside "ufc", "spa" inside "spain", "raw" anywhere).
static BOUNDARY_KEYWORDS: &[&str] = &["fc", "spa", "raw", "kbo", "f1", "f2", "f3"];

// ─── Static regexes ───────────────────────────────────────────────────────────

fn sep_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"[._\-]+").expect("sep_re"))
}

fn release_group_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"-[A-Za-z0-9]+$").expect("release_group_re"))
}

/// Strip bracket/paren group tags: "[TJET]", "[BluRay]", "(PROPER)", etc.
fn bracket_tag_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\s*[\[\(][A-Za-z0-9._-]+[\]\)]").expect("bracket_tag_re"))
}

fn file_ext_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\.(mkv|mp4|avi|m4v|ts|webm|mov|wmv|flv)$").expect("file_ext_re")
    })
}

/// Single combined regex for all quality/codec/audio/release flags.
/// Longer/more-specific patterns come first to avoid partial matches.
fn tech_indicator_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        let alts = [
            // Resolution / quality (longer patterns first)
            "2160[pP]",
            "1080[pP]",
            "720[pP]",
            "480[pP]",
            "360[pP]",
            "240[pP]",
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
            "SD",
            // Codec (longer first)
            r"H\.265",
            "H265",
            r"H\.264",
            "H264",
            "HEVC",
            "x265",
            "x264",
            "AVC",
            "XviD",
            "DivX",
            "VP9",
            "AV1",
            // Audio (longer first)
            r"AAC2\.0",
            "AAC",
            "AC3",
            r"DD5\.1",
            r"DD2\.0",
            "DTS",
            "FLAC",
            "MP3",
            "EAC3",
            "TrueHD",
            "Atmos",
            "Multi",
            // Release flags
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
        ];
        let alternation = alts.join("|");
        Regex::new(&format!(r"(?i)[.\s]?(?:{})\b", alternation)).expect("tech_indicator_re")
    })
}

/// Broadcaster / provider labels to strip from cleaned titles.
fn broadcaster_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)\b(?:SkyF1(?:HD|UHD)?|Sky\s*Sports(?:\s*[A-Za-z0-9]+){0,2}|SkySports(?:\s*[A-Za-z0-9]+){0,2}|Sky(?:HD|UHD)?|F1TV(?:\s*Pro)?|BTSportHD|TNTSportsHD|V\s*Sport(?:\s*Ultra\s*HD)?)\b",
        )
        .expect("broadcaster_re")
    })
}

fn multi_space_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\s{2,}").expect("multi_space_re"))
}

fn year_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b((?:19|20)\d{2})\b").expect("year_re"))
}

// ─── Category detection ────────────────────────────────────────────────────────

/// Check whether `keyword` appears in the (already normalised) title.
///
/// Keywords in `BOUNDARY_KEYWORDS` require surrounding spaces to avoid matching
/// as substrings (e.g. "f1" inside "wf1" or "fc" inside "ufc").
/// All other keywords are matched as plain substrings of the normalised title.
fn keyword_matches(keyword: &str, normalized: &str, padded: &str) -> bool {
    if BOUNDARY_KEYWORDS.contains(&keyword) {
        // Require a space on both sides (padded already has leading/trailing space)
        padded.contains(&format!(" {keyword} "))
    } else {
        normalized.contains(keyword)
    }
}

/// Detect the sports category of a torrent title.
///
/// Returns a category key such as `"formula_racing"`, `"motogp_racing"`,
/// `"fighting"`, `"football"`, etc., or `None` if no sports content is detected.
///
/// Uses a two-pass approach ported from the Python `sports_parser.py`:
///   1. League/organisation identifiers (high confidence, fast)
///   2. Full keyword list including team names
pub fn detect_sports_category(title: &str) -> Option<&'static str> {
    if title.is_empty() {
        return None;
    }

    // Normalise: replace separators with spaces, lowercase.
    let normalized = sep_re()
        .replace_all(&title.to_lowercase(), " ")
        .into_owned();
    // Pad for clean word-boundary checks (avoids prefix/suffix edge cases).
    let padded = format!(" {normalized} ");

    // Pass 1 — league/org identifiers (high confidence).
    for &(category, identifiers) in LEAGUE_IDENTIFIERS {
        for &id in identifiers {
            if keyword_matches(id, &normalized, &padded) {
                return Some(category);
            }
        }
    }

    // Pass 2 — full keyword list (team names, venues, drivers, …).
    for &(category, keywords) in SPORTS_CATEGORY_KEYWORDS {
        for &kw in keywords {
            if keyword_matches(kw, &normalized, &padded) {
                return Some(category);
            }
        }
    }

    None
}

/// Returns `true` if the title looks like a sports torrent.
///
/// Use this to decide whether to call `parse_sports_title` instead of the
/// standard `parse_title` in generic import flows.
pub fn is_sports_title(title: &str) -> bool {
    detect_sports_category(title).is_some()
}

// ─── Title cleaning ───────────────────────────────────────────────────────────

/// Clean a sports torrent title by stripping technical markers and broadcaster
/// labels, leaving only the human-readable event name.
///
/// ```text
/// "Formula 1 2026. R04. Miami Grand Prix. Sky Sports F1 UHD"
///   → "Formula 1 2026 R04 Miami Grand Prix"
///
/// "NFL.2026.02.08.Super.Bowl.LX.Seahawks.Vs.Patriots.1080p.HDTV.H264-DARKSPORT"
///   → "NFL 2026 02 08 Super Bowl LX Seahawks Vs Patriots"
/// ```
pub fn clean_sports_title(raw: &str) -> String {
    let mut s = raw.to_string();

    // Strip bracket/paren group tags first: "[TJET]", "[eztv]", "(PROPER)", …
    s = bracket_tag_re().replace_all(&s, "").into_owned();

    // Strip trailing release-group suffix (e.g. "-DARKSPORT", "-NWCHD")
    s = release_group_re().replace(&s, "").into_owned();

    // Strip file extension
    s = file_ext_re().replace(&s, "").into_owned();

    // Strip quality / codec / audio / release-flag tokens
    s = tech_indicator_re().replace_all(&s, "").into_owned();

    // Strip broadcaster labels
    s = broadcaster_re().replace_all(&s, "").into_owned();

    // Replace separator characters with spaces
    s = sep_re().replace_all(&s, " ").into_owned();

    // Collapse whitespace and trim
    s = multi_space_re().replace_all(s.trim(), " ").into_owned();

    if s.is_empty() {
        "Sports Event".to_string()
    } else {
        s
    }
}

// ─── WWE episode classification ──────────────────────────────────────────────

/// PPV / premium live event keywords — these map to standalone movie imports.
/// More specific phrases are listed first to prevent substring shadowing.
static WWE_PPV_IDENTIFIERS: &[&str] = &[
    "wrestlemania",
    "nxt takeover",
    "nxt stand & deliver",
    "nxt stand and deliver",
    "clash at the castle",
    "clash of champions",
    "saturday night main event",
    "tables ladders chairs",
    "survivor series",
    "royal rumble",
    "summerslam",
    "money in the bank",
    "elimination chamber",
    "hell in a cell",
    "night of champions",
    "extreme rules",
    "battleground",
    "fastlane",
    "stomping grounds",
    "bad blood",
    "no mercy",
    "vengeance",
    "unforgiven",
    "armageddon",
    "judgment day",
    "new year revolution",
    "one night stand",
    "cyber sunday",
    "bragging rights",
    "over the limit",
    "capitol punishment",
    "wargames",
    "war games",
    "backlash",
    "payback",
    "evolution",
    "in your house",
    "king of the ring",
    "crown jewel",
    "greatest royal rumble",
    "super showdown",
    "super show-down",
];

/// Weekly show identifiers mapped to their canonical series title.
/// Order matters: more specific patterns before their substrings
/// (e.g. "monday night raw" before "raw").
/// The third field indicates whether a word-boundary check is required.
static WWE_WEEKLY_SHOWS: &[(&str, &str, bool)] = &[
    ("monday night raw", "WWE Monday Night Raw", false),
    ("friday night smackdown", "WWE SmackDown", false),
    ("smackdown live", "WWE SmackDown", false),
    ("smackdown", "WWE SmackDown", false),
    ("205 live", "WWE 205 Live", false),
    ("main event", "WWE Main Event", false),
    ("nxt level up", "WWE NXT Level Up", false),
    ("nxt", "WWE NXT", true),
    ("raw", "WWE Monday Night Raw", true),
];

fn wwe_date_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        // Matches YYYY MM DD after separator normalisation (e.g. "2018 05 07")
        Regex::new(r"\b((?:19|20)\d{2})\s+(\d{1,2})\s+(\d{1,2})\b").expect("wwe_date_re")
    })
}

/// Information extracted from a WWE weekly show title.
pub struct WweEpisodeInfo {
    /// Canonical series title (e.g. "WWE Monday Night Raw").
    pub series_title: &'static str,
    /// Calendar year — used as Stremio season number.
    pub season_number: i32,
    /// MMDD-encoded episode number (e.g. 507 for May 7th).
    pub episode_number: i32,
}

/// Classify a WWE torrent title as a weekly series episode or a PPV/standalone movie.
///
/// Returns `Some(WweEpisodeInfo)` when the title is a recognised weekly show AND a
/// `YYYY MM DD` date can be extracted.  Returns `None` for PPV events or titles
/// without a parseable date — those should be imported as movies.
pub fn classify_wwe_title(title: &str) -> Option<WweEpisodeInfo> {
    let normalized = sep_re()
        .replace_all(&title.to_lowercase(), " ")
        .into_owned();
    let padded = format!(" {normalized} ");

    // PPV / premium live events → movie
    for ppv in WWE_PPV_IDENTIFIERS {
        if normalized.contains(ppv) {
            return None;
        }
    }

    // Match a weekly show identifier
    let mut series_title: Option<&'static str> = None;
    for &(identifier, series, boundary) in WWE_WEEKLY_SHOWS {
        let matched = if boundary {
            padded.contains(&format!(" {identifier} "))
        } else {
            normalized.contains(identifier)
        };
        if matched {
            series_title = Some(series);
            break;
        }
    }
    let series_title = series_title?;

    // Extract YYYY MM DD from the normalised title
    let caps = wwe_date_re().captures(&normalized)?;
    let year: i32 = caps.get(1)?.as_str().parse().ok()?;
    let month: i32 = caps.get(2)?.as_str().parse().ok()?;
    let day: i32 = caps.get(3)?.as_str().parse().ok()?;

    if !(1990..=2030).contains(&year) || !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }

    Some(WweEpisodeInfo {
        series_title,
        season_number: year,
        episode_number: month * 100 + day,
    })
}

// ─── Title parsing ────────────────────────────────────────────────────────────

/// Parse a sports torrent title, returning a `ParsedTitle` with a corrected
/// `title` (full event name) and `year`.
///
/// PTT is used for technical metadata (resolution, codec, quality, …); only
/// `title` and `year` are overridden with sports-aware values.
pub fn parse_sports_title(raw: &str) -> super::ParsedTitle {
    let mut parsed = super::parse_title(raw);

    let clean = clean_sports_title(raw);
    parsed.title = Some(clean);

    // Extract year directly — PTT sometimes misses it inside multi-part titles.
    if let Some(y) = year_re()
        .captures(raw)
        .and_then(|c| c.get(1))
        .and_then(|m| m.as_str().parse::<i32>().ok())
    {
        parsed.year = Some(y);
    }

    parsed
}

// ─── Racing (Formula 1/2/3, MotoGP) parsing ─────────────────────────────────────

/// Result of parsing a racing torrent name.
///
/// Racing content (F1/F2/F3, MotoGP) is stored in the DB as a *series* whose title
/// is `"{league} {event} {year}"`; the session (Qualifying / Race / Sprint / …) is
/// the individual episode.
#[derive(Debug, Clone)]
pub struct RacingParsed {
    /// Series title, e.g. "Formula 1 Canadian Grand Prix 2026".
    pub series_title: String,
    pub year: Option<i32>,
    /// Session / episode label, e.g. "Qualifying". `None` when not present.
    pub session: Option<String>,
}

/// Canonical league prefix at the start of a racing title (Formula 1/2/3, MotoGP, …).
fn racing_league(s: &str) -> Option<String> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| {
        Regex::new(r"(?i)\b(formula\s*[123]|f[123]|moto\s*gp|moto2|moto3|indycar|nascar)\b")
            .expect("racing_league_re")
    });
    let m = re.find(s)?;
    let raw = m.as_str().to_lowercase();
    let canonical = match raw.replace(' ', "").as_str() {
        "formula1" | "f1" => "Formula 1",
        "formula2" | "f2" => "Formula 2",
        "formula3" | "f3" => "Formula 3",
        "motogp" => "MotoGP",
        "moto2" => "Moto2",
        "moto3" => "Moto3",
        "indycar" => "IndyCar",
        "nascar" => "NASCAR",
        _ => return None,
    };
    Some(canonical.to_string())
}

/// Strip a DD MM YYYY / YYYY MM DD date from `s` (space-separated form) and return
/// `(year, remainder)`. Falls back to a standalone 4-digit year token.
fn racing_year_and_strip(s: &str) -> (Option<i32>, String) {
    static DMY_RE: OnceLock<Regex> = OnceLock::new();
    static YMD_RE: OnceLock<Regex> = OnceLock::new();
    let dmy = DMY_RE.get_or_init(|| {
        Regex::new(r"\b\d{1,2}\s+\d{1,2}\s+((?:19|20)\d{2})\b").expect("dmy_re")
    });
    let ymd = YMD_RE.get_or_init(|| {
        Regex::new(r"\b((?:19|20)\d{2})\s+\d{1,2}\s+\d{1,2}\b").expect("ymd_re")
    });

    if let Some(c) = dmy.captures(s) {
        let year = c.get(1).and_then(|m| m.as_str().parse().ok());
        let stripped = dmy.replace(s, "").to_string();
        return (year, stripped);
    }
    if let Some(c) = ymd.captures(s) {
        let year = c.get(1).and_then(|m| m.as_str().parse().ok());
        let stripped = ymd.replace(s, "").to_string();
        return (year, stripped);
    }
    // Standalone year token — strip it so it isn't duplicated when re-appended.
    if let Some(c) = year_re().captures(s) {
        let year = c.get(1).and_then(|m| m.as_str().parse().ok());
        let stripped = year_re().replace(s, "").to_string();
        return (year, stripped);
    }
    (None, s.to_string())
}

/// Parse a Formula/MotoGP racing torrent name into a series title, year, and session.
///
/// ```text
/// "Formula 1 Canadian Grand Prix Qualifying 23.05.2026"
///   → series_title="Formula 1 Canadian Grand Prix 2026",
///     year=Some(2026), session=Some("Qualifying")
/// ```
///
/// Returns `None` when the title is not recognisably a Formula/MotoGP racing release.
pub fn parse_racing_title(raw: &str) -> Option<RacingParsed> {
    // Strip broadcaster / quality / codec tokens and normalise separators to spaces.
    let cleaned = clean_sports_title(raw);

    // Must be a racing release with a known league (checked on the normalised form,
    // since the raw name may use dot/underscore separators, e.g. "Formula.1").
    let league = racing_league(&cleaned)?;

    // Pull out the year and remove the date so it doesn't pollute the event name.
    let (year, no_date) = racing_year_and_strip(&cleaned);
    let no_date = multi_space_re().replace_all(no_date.trim(), " ").into_owned();

    // Split around "Grand Prix": the event is everything up to & including it,
    // and the remaining trailing text is the session (episode).
    let lower = no_date.to_lowercase();
    let (event, session) = if let Some(idx) = lower.find("grand prix") {
        let end = idx + "grand prix".len();
        let event = no_date[..end].trim().to_string();
        let session = no_date[end..].trim().to_string();
        (event, session)
    } else {
        // No "Grand Prix" marker — strip a trailing session keyword if present.
        let (event, session) = split_trailing_session(&no_date);
        (event, session)
    };

    // Ensure the event begins with the canonical league name.
    let event = if event.to_lowercase().starts_with(&league.to_lowercase()) {
        event
    } else {
        format!("{league} {event}").trim().to_string()
    };

    let series_title = match year {
        Some(y) => format!("{event} {y}"),
        None => event,
    };

    let session = if session.is_empty() {
        None
    } else {
        Some(session)
    };

    Some(RacingParsed {
        series_title,
        year,
        session,
    })
}

/// Known racing session keywords (longest/most-specific first).
static RACING_SESSIONS: &[&str] = &[
    "sprint qualifying",
    "sprint shootout",
    "sprint race",
    "free practice 1",
    "free practice 2",
    "free practice 3",
    "practice 1",
    "practice 2",
    "practice 3",
    "qualifying",
    "sprint",
    "fp1",
    "fp2",
    "fp3",
    "practice",
    "warm up",
    "warmup",
    "race",
];

/// Map a racing session name (or a filename/title containing one) to its canonical
/// episode slot and a normalised display title.
///
/// A Grand Prix weekend is modelled as a 5-episode "season", with sprint weekends
/// reusing the same slots:
///
/// | slot | normal weekend       | sprint weekend        |
/// |------|----------------------|-----------------------|
/// | 1    | Free Practice 1      | Free Practice 1       |
/// | 2    | Free Practice 2      | Sprint Qualifying     |
/// | 3    | Free Practice 3      | Sprint                |
/// | 4    | Qualifying           | Qualifying            |
/// | 5    | Race                 | Race                  |
///
/// Using fixed slots keeps episode numbers stable across separate imports of the
/// same Grand Prix (e.g. importing Qualifying first, then the Race later).
/// Returns `None` when no known session is recognised.
pub fn racing_session_episode(session_or_name: &str) -> Option<(i32, String)> {
    let s = session_or_name.to_lowercase();
    // Most-specific patterns first to avoid "qualifying"/"race" shadowing.
    let table: &[(&[&str], i32, &str)] = &[
        (&["sprint qualifying", "sprint shootout"], 2, "Sprint Qualifying"),
        (&["sprint race", "sprint"], 3, "Sprint"),
        (&["free practice 1", "practice 1", "fp1"], 1, "Free Practice 1"),
        (&["free practice 2", "practice 2", "fp2"], 2, "Free Practice 2"),
        (&["free practice 3", "practice 3", "fp3"], 3, "Free Practice 3"),
        (&["qualifying", "quali"], 4, "Qualifying"),
        (&["grand prix", "race", " gp "], 5, "Race"),
    ];
    let padded = format!(" {s} ");
    for (keywords, episode, title) in table {
        for kw in *keywords {
            let hit = if kw.starts_with(' ') {
                padded.contains(kw)
            } else {
                s.contains(kw)
            };
            if hit {
                return Some((*episode, (*title).to_string()));
            }
        }
    }
    None
}

/// Split a date-less title into `(event, session)` by detecting a trailing session keyword.
fn split_trailing_session(s: &str) -> (String, String) {
    let lower = s.to_lowercase();
    for kw in RACING_SESSIONS {
        // Match the keyword as a trailing token.
        if let Some(idx) = lower.rfind(kw) {
            let after = lower[idx + kw.len()..].trim();
            // Only treat as session if it's at/near the end of the string.
            if after.is_empty() {
                let event = s[..idx].trim().to_string();
                let session = s[idx..].trim().to_string();
                if !event.is_empty() {
                    return (event, session);
                }
            }
        }
    }
    (s.to_string(), String::new())
}

#[cfg(test)]
mod racing_tests {
    use super::*;

    #[test]
    fn f1_grand_prix_with_date() {
        let r = parse_racing_title("Formula 1 Canadian Grand Prix Qualifying 23 05 2026").unwrap();
        assert_eq!(r.series_title, "Formula 1 Canadian Grand Prix 2026");
        assert_eq!(r.year, Some(2026));
        assert_eq!(r.session.as_deref(), Some("Qualifying"));
    }

    #[test]
    fn f1_dotted_with_race_session() {
        let r = parse_racing_title("Formula.1.Canadian.Grand.Prix.Race.23.05.2026.1080p").unwrap();
        assert_eq!(r.series_title, "Formula 1 Canadian Grand Prix 2026");
        assert_eq!(r.year, Some(2026));
        assert_eq!(r.session.as_deref(), Some("Race"));
    }

    #[test]
    fn f1_no_session() {
        let r = parse_racing_title("Formula 1 Miami Grand Prix 2026").unwrap();
        assert_eq!(r.series_title, "Formula 1 Miami Grand Prix 2026");
        assert_eq!(r.session, None);
    }

    #[test]
    fn motogp_grand_prix() {
        let r = parse_racing_title("MotoGP Spanish Grand Prix Sprint 04 05 2026").unwrap();
        assert_eq!(r.series_title, "MotoGP Spanish Grand Prix 2026");
        assert_eq!(r.session.as_deref(), Some("Sprint"));
    }

    #[test]
    fn f1_filename_with_dots_and_extension() {
        let r = parse_racing_title("Formula 1 Canadian Grand Prix Qualifying 23.05.2026.mkv").unwrap();
        assert_eq!(r.series_title, "Formula 1 Canadian Grand Prix 2026");
        assert_eq!(r.year, Some(2026));
        assert_eq!(r.session.as_deref(), Some("Qualifying"));
    }

    #[test]
    fn non_racing_returns_none() {
        assert!(parse_racing_title("WWE Raw 23 05 2026").is_none());
    }

    #[test]
    fn session_episode_slots() {
        let ep = |s: &str| racing_session_episode(s).unwrap();
        // Normal weekend ordering.
        assert_eq!(ep("Free Practice 1"), (1, "Free Practice 1".to_string()));
        assert_eq!(ep("FP2"), (2, "Free Practice 2".to_string()));
        assert_eq!(ep("FP3"), (3, "Free Practice 3".to_string()));
        assert_eq!(ep("Qualifying"), (4, "Qualifying".to_string()));
        assert_eq!(ep("Race"), (5, "Race".to_string()));
        // Sprint weekend reuses slots 2 and 3.
        assert_eq!(ep("Sprint Qualifying"), (2, "Sprint Qualifying".to_string()));
        assert_eq!(ep("Sprint Shootout"), (2, "Sprint Qualifying".to_string()));
        assert_eq!(ep("Sprint Race"), (3, "Sprint".to_string()));
        assert_eq!(ep("Sprint"), (3, "Sprint".to_string()));
        // "Grand Prix" without a session word is the race.
        assert_eq!(ep("Canadian Grand Prix"), (5, "Race".to_string()));
        // Unknown.
        assert!(racing_session_episode("Pit Lane Channel").is_none());
    }
}

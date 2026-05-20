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
            "motogp", "moto gp", "moto2", "moto3", "wsbk", "worldsbk",
            "superbike",
        ],
    ),
    (
        "fighting",
        &[
            "ufc", "wwe", "aew", "bellator", "boxing", "wrestlemania",
            "smackdown", "monday night raw",
        ],
    ),
    (
        "basketball",
        &["nba", "wnba", "march madness", "euroleague", "fiba"],
    ),
    (
        "formula_racing",
        &[
            "formula 1", "formula1", "formula 2", "formula 3",
            "indycar", "nascar",
        ],
    ),
    (
        "football",
        &[
            "fifa", "uefa", "premier league", "champions league",
            "la liga", "bundesliga",
        ],
    ),
];

/// Full keyword table including team names — used in pass 2 when no league
/// identifier matched.  Same ordering as the Python source.
static SPORTS_CATEGORY_KEYWORDS: &[(&str, &[&str])] = &[
    (
        "hockey",
        &[
            "nhl", "stanley cup", "khl", "iihf",
            "bruins", "maple leafs", "canadiens", "blackhawks", "penguins",
            "red wings", "flyers", "flames", "canucks", "avalanche",
            "lightning", "golden knights", "kraken", "senators", "sabres",
            "devils", "islanders", "capitals", "blue jackets",
        ],
    ),
    (
        "rugby",
        &[
            "rugby", "six nations", "rugby world cup", "super rugby",
            "premiership rugby", "top 14", "pro14", "nrl", "australian football",
            "all blacks", "springboks", "wallabies", "crusaders",
        ],
    ),
    (
        "baseball",
        &[
            "mlb", "npb", "kbo", "japan series",
            "yankees", "dodgers", "red sox", "cubs", "astros", "braves",
            "mets", "phillies", "padres", "mariners", "blue jays", "twins",
            "white sox", "guardians", "orioles", "tigers", "royals", "angels",
            "rockies", "marlins", "brewers", "pirates", "diamondbacks",
            "nationals", "oakland athletics",
        ],
    ),
    (
        "motogp_racing",
        &[
            "motogp", "moto gp", "moto2", "moto3", "superbike",
            "wsbk", "bsb", "worldsbk", "isle of man",
            "marquez", "bagnaia", "quartararo",
            "ducati", "ktm", "aprilia",
        ],
    ),
    (
        "fighting",
        &[
            "ufc", "mma", "boxing", "wwe", "aew", "bellator",
            "one championship", "pfl", "cage warriors", "glory", "pride fc",
            "rizin", "pro wrestling", "kickboxing", "muay thai",
            "monday night raw", "smackdown", "wrestlemania", "summerslam",
            "royal rumble", "nxt", "title fight", "championship fight",
            "canelo", "fury", "joshua", "wilder", "mcgregor",
            "khabib", "adesanya", "ngannou",
        ],
    ),
    (
        "american_football",
        &[
            "nfl", "super bowl", "nfc championship", "afc championship",
            "college football", "ncaa football",
            "patriots", "cowboys", "packers", "49ers", "seahawks",
            "ravens", "bills", "eagles", "broncos", "steelers",
            "raiders", "chargers", "dolphins", "jets", "bears",
            "lions", "vikings", "saints", "falcons", "buccaneers",
            "rams", "titans", "colts", "texans", "jaguars",
            "bengals", "browns", "commanders",
        ],
    ),
    (
        "basketball",
        &[
            "nba", "wnba", "ncaa basketball", "march madness",
            "euroleague", "fiba",
            "lakers", "celtics", "warriors", "nets", "knicks",
            "bulls", "heat", "bucks", "suns", "clippers",
            "mavericks", "76ers", "nuggets", "grizzlies", "cavaliers",
            "raptors", "spurs", "jazz", "pelicans", "blazers",
            "timberwolves", "thunder", "pistons", "hornets", "pacers",
            "magic", "wizards",
        ],
    ),
    (
        "formula_racing",
        &[
            "formula 1", "formula1", "formula 2", "formula 3",
            "indycar", "indy 500", "nascar", "wec", "le mans",
            "grand prix", "monaco", "silverstone", "monza", "suzuka",
            "interlagos", "daytona",
            "ferrari", "mercedes", "red bull racing", "mclaren",
            "alpine", "aston martin", "williams", "haas",
            "verstappen", "hamilton", "leclerc", "sainz", "norris", "perez",
        ],
    ),
    (
        "football",
        &[
            "fifa", "uefa", "premier league", "la liga",
            "bundesliga", "serie a", "ligue 1", "champions league",
            "europa league", "world cup", "epl", "efl",
            "copa america", "copa libertadores", "mls", "eredivisie",
            "manchester", "liverpool", "chelsea", "arsenal", "tottenham",
            "barcelona", "real madrid", "juventus", "psg", "bayern",
            "inter milan", "ac milan", "dortmund", "ajax",
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
    RE.get_or_init(|| {
        Regex::new(r"\s*[\[\(][A-Za-z0-9._-]+[\]\)]").expect("bracket_tag_re")
    })
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
            "2160[pP]", "1080[pP]", "720[pP]", "480[pP]", "360[pP]", "240[pP]",
            "4K", "UHD", "HDTV", "WEB-DL", "WEBDL", "WEBRip", "BluRay", "BDRip",
            "HDRip", "DVDRip", "PDTV", "SDTV", "WEB", "Sportnet360", "SD",
            // Codec (longer first)
            r"H\.265", "H265", r"H\.264", "H264", "HEVC", "x265", "x264", "AVC",
            "XviD", "DivX", "VP9", "AV1",
            // Audio (longer first)
            r"AAC2\.0", "AAC", "AC3", r"DD5\.1", r"DD2\.0", "DTS", "FLAC", "MP3",
            "EAC3", "TrueHD", "Atmos", "Multi",
            // Release flags
            "PROPER", "REPACK", "INTERNAL", "LIMITED", "UNRATED", "EXTENDED",
            "RERIP", "REAL", "READNFO", "DIRFIX", "NFOFIX",
        ];
        let alternation = alts.join("|");
        Regex::new(&format!(r"(?i)[.\s]?(?:{})\b", alternation))
            .expect("tech_indicator_re")
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

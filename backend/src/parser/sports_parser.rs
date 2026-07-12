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

use chrono::Datelike;
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
/// positives (e.g. "fc" inside "ufc", "spa" inside "spain", "raw" anywhere,
/// "mma" inside "Emmanuelle").
static BOUNDARY_KEYWORDS: &[&str] = &["fc", "spa", "raw", "kbo", "f1", "f2", "f3", "mma"];

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

fn indexer_source_prefix_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)^(?:www\.)?[a-z0-9.-]+\.(?:org|net|com|pl)\s*-+\s*")
            .expect("indexer_source_prefix_re")
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
            // Release-group / uploader tags common on Spanish F1 uploads
            "EveHQ",
            "Lat",
            "Spa",
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
            r"(?i)\b(?:SkyF1(?:HD|UHD)?|Sky\s*Sports(?:\s*[A-Za-z0-9]+){0,2}|SkySports(?:\s*[A-Za-z0-9]+){0,2}|Sky(?:HD|UHD)?|F1TV(?:\s*Pro)?|BTSportHD|TNTSportsHD|V\s*Sport(?:\s*Ultra\s*HD)?|ESPNF1(?:\s*Lat)?|DAZNF1|DAZN\s*F1)\b",
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

    // Strip indexer/source prefixes: "www.UIndex.org - boxing …"
    s = indexer_source_prefix_re()
        .replace(&s, "")
        .trim()
        .to_string();

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

// ─── Team matchup canonicalization ─────────────────────────────────────────────

/// Sports catalog keys where events are identified by two teams plus a date.
/// Fuzzy pg_trgm matching is unsafe here: reversed team order and nearby dates
/// routinely clear a 70% threshold and merge unrelated games.
pub const TEAM_MATCHUP_CATEGORIES: &[&str] = &[
    "baseball",
    "basketball",
    "hockey",
    "american_football",
    "football",
    "rugby",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TeamMatchup {
    pub team_a: String,
    pub team_b: String,
    /// Calendar date extracted from the title, when present.
    pub event_date: Option<chrono::NaiveDate>,
}

fn team_matchup_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)\b([A-Za-z][A-Za-z0-9&'.-]*(?:\s+[A-Za-z][A-Za-z0-9&'.-]*){0,5})\s+(?:vs\.?|versus|v\.?|@|\bat\b)\s+([A-Za-z][A-Za-z0-9&'.-]*(?:\s+[A-Za-z][A-Za-z0-9&'.-]*){0,5})\b",
        )
        .expect("team_matchup_re")
    })
}

fn strip_trailing_date_from_team(name: &str) -> String {
    static TRAILING_DATE: OnceLock<Regex> = OnceLock::new();
    let re = TRAILING_DATE.get_or_init(|| {
        Regex::new(
            r"(?i)\s+(?:\d{1,2}[.\-/]\d{1,2}[.\-/](?:19|20)\d{2}|\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}|\d{4}\s+\d{1,2}\s+\d{1,2}|\d{1,2}\s+\d{1,2}\s+\d{4})$",
        )
        .expect("trailing_date_re")
    });
    multi_space_re()
        .replace_all(re.replace(name, "").trim(), " ")
        .into_owned()
}

fn trim_team_name(name: &str) -> String {
    let mut s = strip_trailing_date_from_team(name)
        .trim()
        .trim_end_matches('.')
        .to_string();
    for token in ["1080p", "720p", "480p", "2160p", "4K", "UHD", "HDTV", "WEB"] {
        if s.to_ascii_lowercase()
            .ends_with(&token.to_ascii_lowercase())
        {
            s = s[..s.len().saturating_sub(token.len())].trim().to_string();
        }
    }
    multi_space_re().replace_all(s.trim(), " ").into_owned()
}

/// Extract the trailing team-vs-team segment from a sports title.
pub fn extract_team_matchup(title: &str) -> Option<TeamMatchup> {
    if title.is_empty() {
        return None;
    }

    let normalized = sep_re().replace_all(title, " ").into_owned();
    let caps = team_matchup_re().captures(&normalized)?;
    let team1 = trim_team_name(caps.get(1)?.as_str());
    let team2 = trim_team_name(caps.get(2)?.as_str());
    if team1.is_empty() || team2.is_empty() || team1.eq_ignore_ascii_case(&team2) {
        return None;
    }

    Some(TeamMatchup {
        team_a: team1,
        team_b: team2,
        event_date: extract_event_date_from_title(title),
    })
}

/// Parse common sports event date formats embedded in torrent/page titles.
pub fn extract_event_date_from_title(title: &str) -> Option<chrono::NaiveDate> {
    static PATTERNS: &[(&str, &str)] = &[
        (r"\b((?:19|20)\d{2})\.(\d{1,2})\.(\d{1,2})\b", "ymd_dot"),
        (r"\b((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})\b", "ymd_dash"),
        (r"\b((?:19|20)\d{2})\s+(\d{1,2})\s+(\d{1,2})\b", "ymd_space"),
        (r"\b(\d{1,2})\.(\d{1,2})\.((?:19|20)\d{2})\b", "dmy_dot"),
        (r"\b(\d{1,2})-(\d{1,2})-((?:19|20)\d{2})\b", "dmy_dash"),
        (r"\b(\d{1,2})\s+(\d{1,2})\s+((?:19|20)\d{2})\b", "dmy_space"),
        (
            r"\b((?:19|20)\d{2})_(\d{1,2})_(\d{1,2})\b",
            "ymd_underscore",
        ),
    ];

    for (pattern, kind) in PATTERNS {
        let Ok(re) = Regex::new(pattern) else {
            continue;
        };
        let Some(caps) = re.captures(title) else {
            continue;
        };
        let (year, month, day): (i32, i32, i32) = match *kind {
            "ymd_dot" | "ymd_dash" | "ymd_space" | "ymd_underscore" => (
                caps.get(1)?.as_str().parse().ok()?,
                caps.get(2)?.as_str().parse().ok()?,
                caps.get(3)?.as_str().parse().ok()?,
            ),
            "dmy_dot" | "dmy_dash" | "dmy_space" => (
                caps.get(3)?.as_str().parse().ok()?,
                caps.get(2)?.as_str().parse().ok()?,
                caps.get(1)?.as_str().parse().ok()?,
            ),
            _ => return None,
        };
        if (1..=12).contains(&month) && (1..=31).contains(&day) {
            return chrono::NaiveDate::from_ymd_opt(year, month as u32, day as u32);
        }
    }
    None
}

fn format_event_date(date: chrono::NaiveDate) -> String {
    format!("{:02}.{:02}.{}", date.day(), date.month(), date.year())
}

/// Build a deterministic media title for a team matchup: teams sorted
/// alphabetically (case-insensitive), joined with `" at "`, optional
/// `DD.MM.YYYY` suffix when a date is known.
pub fn canonical_matchup_title(title: &str) -> Option<String> {
    let matchup = extract_team_matchup(title)?;
    let mut teams = [matchup.team_a, matchup.team_b];
    teams.sort_by_key(|a| a.to_ascii_lowercase());
    let mut out = format!("{} at {}", teams[0], teams[1]);
    if let Some(date) = matchup.event_date {
        out.push(' ');
        out.push_str(&format_event_date(date));
    }
    Some(out)
}

/// Resolve the media title/year used when creating or looking up sports stubs
/// for team-based sports (MLB, NBA, etc.).
pub fn resolve_team_matchup_media_title(
    title: &str,
    category: &str,
) -> Option<(String, Option<i32>)> {
    if !TEAM_MATCHUP_CATEGORIES.contains(&category) {
        return None;
    }
    let canonical = canonical_matchup_title(title)?;
    let year = extract_event_date_from_title(title)
        .map(|d| d.year())
        .or_else(|| {
            year_re()
                .captures(title)
                .and_then(|c| c.get(1))
                .and_then(|m| m.as_str().parse().ok())
        });
    Some((canonical, year))
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

/// Weekly fighting series episode (WWE, AEW, …).
pub struct FightingSeriesEpisode {
    pub series_title: String,
    pub season_number: i32,
    pub episode_number: i32,
    /// Brand key for poster selection (`WWE`, `AEW`, `UFC`, …).
    pub brand: &'static str,
}

static AEW_PPV_IDENTIFIERS: &[&str] = &[
    "forbidden door",
    "all out",
    "double or nothing",
    "full gear",
    "revolution",
    "worlds end",
    "wrestledream",
    "all in",
    "grand slam",
];

static AEW_WEEKLY_SHOWS: &[(&str, &str)] = &[
    ("aew dynamite", "AEW Dynamite"),
    ("aew collision", "AEW Collision"),
    ("aew rampage", "AEW Rampage"),
];

fn season_episode_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\bS(\d{1,2})E(\d{1,2})\b").expect("season_episode_re"))
}

fn parse_season_episode_from_title(title: &str) -> Option<(i32, i32)> {
    let parsed = super::parse_title(title);
    if let (Some(&season), Some(&episode)) = (parsed.seasons.first(), parsed.episodes.first()) {
        return Some((season, episode));
    }
    let caps = season_episode_re().captures(title)?;
    let season: i32 = caps.get(1)?.as_str().parse().ok()?;
    let episode: i32 = caps.get(2)?.as_str().parse().ok()?;
    Some((season, episode))
}

fn is_aew_ppv(normalized: &str) -> bool {
    AEW_PPV_IDENTIFIERS
        .iter()
        .any(|ppv| normalized.contains(ppv))
}

/// Classify an AEW weekly show as a series episode when SxxExx or a date is present.
pub fn classify_aew_title(title: &str) -> Option<FightingSeriesEpisode> {
    let normalized = sep_re()
        .replace_all(&title.to_lowercase(), " ")
        .into_owned();

    if !normalized.contains("aew") {
        return None;
    }

    if is_aew_ppv(&normalized) {
        return None;
    }

    for &(identifier, series) in AEW_WEEKLY_SHOWS {
        if !normalized.contains(identifier) {
            continue;
        }

        if let Some((season, episode)) = parse_season_episode_from_title(title) {
            return Some(FightingSeriesEpisode {
                series_title: series.to_string(),
                season_number: season,
                episode_number: episode,
                brand: "AEW",
            });
        }

        if let Some(caps) = wwe_date_re().captures(&normalized) {
            let year: i32 = caps.get(1)?.as_str().parse().ok()?;
            let month: i32 = caps.get(2)?.as_str().parse().ok()?;
            let day: i32 = caps.get(3)?.as_str().parse().ok()?;
            if (1990..=2030).contains(&year) && (1..=12).contains(&month) && (1..=31).contains(&day)
            {
                return Some(FightingSeriesEpisode {
                    series_title: series.to_string(),
                    season_number: year,
                    episode_number: month * 100 + day,
                    brand: "AEW",
                });
            }
        }
    }

    None
}

/// Classify weekly fighting series episodes (WWE, AEW, …).
pub fn classify_fighting_series_title(title: &str) -> Option<FightingSeriesEpisode> {
    if let Some(wwe) = classify_wwe_title(title) {
        return Some(FightingSeriesEpisode {
            series_title: wwe.series_title.to_string(),
            season_number: wwe.season_number,
            episode_number: wwe.episode_number,
            brand: "WWE",
        });
    }
    classify_aew_title(title)
}

fn ufc_card_suffix_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\s+(?:Main\s+Card|Early\s+Prelims?|Prelims?|PPV)\s*$")
            .expect("ufc_card_suffix_re")
    })
}

fn ufc_ppv_mid_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)\bPPV\b\s*").expect("ufc_ppv_mid_re"))
}

/// Strip UFC card-type suffixes and inline PPV tags for TMDB/event lookup.
pub fn clean_ufc_event_title(title: &str) -> String {
    let mut s = clean_sports_title(title);
    s = ufc_card_suffix_re().replace(&s, "").into_owned();
    s = ufc_ppv_mid_re().replace(&s, "").into_owned();
    multi_space_re().replace_all(s.trim(), " ").into_owned()
}

/// Detect the fighting brand for poster selection.
pub fn detect_fighting_brand(title: &str) -> &'static str {
    let normalized = sep_re()
        .replace_all(&title.to_lowercase(), " ")
        .into_owned();
    if normalized.contains("aew") {
        "AEW"
    } else if normalized.contains("ufc") {
        "UFC"
    } else if normalized.contains("bellator") {
        "Bellator"
    } else if normalized.contains("boxing") || normalized.contains("zuffa boxing") {
        "Boxing"
    } else if normalized.contains("wwe") {
        "WWE"
    } else {
        "Fighting"
    }
}

/// Build a display title for standalone fighting events (PPV, UFC cards, …).
pub fn clean_fighting_event_title(raw: &str) -> String {
    let normalized = sep_re().replace_all(&raw.to_lowercase(), " ").into_owned();
    if normalized.contains("ufc") {
        return clean_ufc_event_title(raw);
    }
    clean_sports_title(raw)
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

    // PTT does not map bare "UHD" (e.g. "Sky Sports F1 UHD") to a resolution.
    if parsed.resolution.is_none() && uhd_resolution_re().is_match(raw) {
        parsed.resolution = Some("2160p".into());
    }

    parsed
}

fn uhd_resolution_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"(?i)\b(?:UHD|4K|2160p)\b").expect("uhd_resolution_re"))
}

/// Map a sports category key to its genre display name (mirrors scraper `category_to_genre`).
pub fn sports_category_to_genre(category: &str) -> &'static str {
    match category {
        "football" => "Football",
        "basketball" => "Basketball",
        "hockey" => "Hockey",
        "american_football" => "American Football",
        "baseball" => "Baseball",
        "rugby" => "Rugby/AFL",
        "formula_racing" => "Formula Racing",
        "motogp_racing" => "MotoGP Racing",
        "fighting" => "Fighting/Wrestling",
        "tennis" => "Tennis",
        "golf" => "Golf",
        "cycling" => "Cycling",
        "athletics" => "Athletics",
        _ => "Other Sports",
    }
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

/// Strip round-number noise tokens emitted by some release groups, e.g.
/// "2026x09" (year x round) or "R09" (round marker). The year in "2026x09" is
/// kept (as a standalone "2026" token) since `racing_year_and_strip` picks it
/// up afterwards; the round number itself carries no reusable information for
/// grouping, so it's dropped entirely.
fn strip_round_tokens(s: &str) -> String {
    static YEAR_X_ROUND_RE: OnceLock<Regex> = OnceLock::new();
    static ROUND_RE: OnceLock<Regex> = OnceLock::new();
    let year_x_round = YEAR_X_ROUND_RE
        .get_or_init(|| Regex::new(r"\b((?:19|20)\d{2})x\d{1,3}\b").expect("year_x_round_re"));
    let round = ROUND_RE.get_or_init(|| Regex::new(r"(?i)\bR\d{1,3}\b").expect("round_re"));

    let s = year_x_round.replace_all(s, "$1");
    round.replace_all(&s, "").into_owned()
}

/// Expand a concatenated "<Country>GP" token (e.g. "BritishGP") into
/// "<Country> Grand Prix", without touching league names that legitimately
/// end in "GP" (e.g. "MotoGP").
fn expand_gp_suffix(s: &str) -> String {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"\b([A-Za-z]+)GP\b").expect("gp_suffix_re"));
    re.replace_all(s, |caps: &regex::Captures| {
        let prefix = &caps[1];
        if prefix.eq_ignore_ascii_case("moto") {
            caps[0].to_string()
        } else {
            format!("{prefix} Grand Prix")
        }
    })
    .into_owned()
}

/// Known alternate circuit/event names that should collapse to one canonical
/// form so the same Grand Prix weekend groups into a single series regardless
/// of which wording a given release used.
static CIRCUIT_ALIASES: &[(&str, &str)] = &[
    ("great britain", "british"),
    ("reino unido", "british"),
    ("emilia-romagna", "emilia romagna"),
    ("mexico city", "mexico"),
    ("united states", "usa"),
    ("estados unidos", "usa"),
];

/// Spanish / alternate "GP &lt;country&gt;" tokens → English Grand Prix host name.
static GP_COUNTRY_ALIASES: &[(&str, &str)] = &[
    ("reino unido", "British"),
    ("estados unidos", "United States"),
    ("emilia romagna", "Emilia Romagna"),
    ("san marino", "Emilia Romagna"),
    ("arabia saudita", "Saudi Arabian"),
    ("arabia saudí", "Saudi Arabian"),
    ("saudi arabia", "Saudi Arabian"),
    ("paises bajos", "Dutch"),
    ("países bajos", "Dutch"),
    ("netherlands", "Dutch"),
    ("holanda", "Dutch"),
    ("españa", "Spanish"),
    ("espana", "Spanish"),
    ("spain", "Spanish"),
    ("italia", "Italian"),
    ("italy", "Italian"),
    ("monaco", "Monaco"),
    ("mónaco", "Monaco"),
    ("australia", "Australian"),
    ("japon", "Japanese"),
    ("japón", "Japanese"),
    ("japan", "Japanese"),
    ("china", "Chinese"),
    ("singapur", "Singapore"),
    ("singapore", "Singapore"),
    ("bahrain", "Bahrain"),
    ("baréin", "Bahrain"),
    ("barein", "Bahrain"),
    ("canada", "Canadian"),
    ("canadá", "Canadian"),
    ("miami", "Miami"),
    ("las vegas", "Las Vegas"),
    ("austria", "Austrian"),
    ("hungria", "Hungarian"),
    ("hungría", "Hungarian"),
    ("hungary", "Hungarian"),
    ("belgica", "Belgian"),
    ("bélgica", "Belgian"),
    ("belgium", "Belgian"),
    ("azerbaijan", "Azerbaijan"),
    ("azerbaiyán", "Azerbaijan"),
    ("qatar", "Qatar"),
    ("catar", "Qatar"),
    ("brazil", "Brazilian"),
    ("brasil", "Brazilian"),
    ("mexico", "Mexican"),
    ("méxico", "Mexican"),
    ("abu dhabi", "Abu Dhabi"),
    ("abudhabi", "Abu Dhabi"),
];

fn gp_country_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\bGP\s+([A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+)?)\b").expect("gp_country_re")
    })
}

/// Expand "GP Reino Unido" / "GP Austria" style tokens to "British Grand Prix", etc.
fn expand_gp_country_names(s: &str) -> String {
    let mut out = s.to_string();
    let mut aliases: Vec<&&str> = GP_COUNTRY_ALIASES.iter().map(|(alias, _)| alias).collect();
    aliases.sort_by_key(|alias| std::cmp::Reverse(alias.len()));

    for alias in aliases {
        let host = GP_COUNTRY_ALIASES
            .iter()
            .find(|(a, _)| *a == *alias)
            .map(|(_, host)| *host)
            .unwrap_or(alias);
        let pattern = format!(r"(?i)\bGP\s+{}\b", regex::escape(alias));
        if let Ok(re) = Regex::new(&pattern) {
            out = re
                .replace_all(&out, format!("{host} Grand Prix"))
                .into_owned();
        }
    }

    // Residual single-token "GP Monza" style — title-case the token.
    out = gp_country_re()
        .replace_all(&out, |caps: &regex::Captures| {
            let token = caps[1]
                .split_whitespace()
                .map(title_case_word)
                .collect::<Vec<_>>()
                .join(" ");
            format!("{token} Grand Prix")
        })
        .into_owned();

    out
}

fn normalize_circuit_aliases(s: &str) -> String {
    let mut out = s.to_string();
    for (alias, canonical) in CIRCUIT_ALIASES {
        if let Some(idx) = out.to_lowercase().find(alias) {
            out.replace_range(idx..idx + alias.len(), canonical);
        }
    }
    out
}

/// Strip a DD MM YYYY / YYYY MM DD date from `s` (space-separated form) and return
/// `(year, remainder)`. Falls back to a standalone 4-digit year token.
fn racing_year_and_strip(s: &str) -> (Option<i32>, String) {
    static DMY_RE: OnceLock<Regex> = OnceLock::new();
    static YMD_RE: OnceLock<Regex> = OnceLock::new();
    let dmy = DMY_RE
        .get_or_init(|| Regex::new(r"\b\d{1,2}\s+\d{1,2}\s+((?:19|20)\d{2})\b").expect("dmy_re"));
    let ymd = YMD_RE
        .get_or_init(|| Regex::new(r"\b((?:19|20)\d{2})\s+\d{1,2}\s+\d{1,2}\b").expect("ymd_re"));

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
    // Drop release-group round markers ("2026x09", "R09") and expand
    // concatenated "<Country>GP" tokens before league/date detection so they
    // don't pollute the event name or block "Grand Prix" detection.
    let cleaned = strip_round_tokens(&cleaned);
    let cleaned = expand_gp_suffix(&cleaned);
    let cleaned = expand_gp_country_names(&cleaned);
    let cleaned = multi_space_re()
        .replace_all(cleaned.trim(), " ")
        .into_owned();

    // Must be a racing release with a known league (checked on the normalised form,
    // since the raw name may use dot/underscore separators, e.g. "Formula.1").
    let league = racing_league(&cleaned)?;

    // Pull out the year and remove the date so it doesn't pollute the event name.
    let (year, no_date) = racing_year_and_strip(&cleaned);
    let no_date = multi_space_re()
        .replace_all(no_date.trim(), " ")
        .into_owned();

    // Split around "Grand Prix": the event is everything up to & including it,
    // and the remaining trailing text is the session (episode).
    let lower = no_date.to_lowercase();
    let (event, session, had_grand_prix) = if let Some(idx) = lower.find("grand prix") {
        let end = idx + "grand prix".len();
        let event = no_date[..end].trim().to_string();
        let session = no_date[end..].trim().to_string();
        (event, session, true)
    } else {
        // No "Grand Prix" marker — strip a trailing session keyword if present.
        let (event, session) = split_trailing_session(&no_date);
        (event, session, false)
    };

    // A recognised session with no explicit "Grand Prix" marker still implies
    // a race weekend (e.g. "Formula 1 Great Britain Sprint Qualifying") — add
    // the suffix so it groups with releases that spell it out.
    let event = if !had_grand_prix && !session.is_empty() {
        format!("{event} Grand Prix")
    } else {
        event
    };

    // Collapse known alternate circuit names ("Great Britain" vs "British", …)
    // so differently-worded releases for the same event group together.
    let event = normalize_circuit_aliases(&event);

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
    "pole position",
    "qualifying",
    "sprint",
    "carrera",
    "fp1",
    "fp2",
    "fp3",
    "practice",
    "warm up",
    "warmup",
    "pole",
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
///
/// Prefer [`racing_file_episode`] when mapping torrent *filenames* to episodes —
/// it honours leading index prefixes (`01.`, `02.`, …) before falling back here.
pub fn racing_session_episode(session_or_name: &str) -> Option<(i32, String)> {
    // Release filenames use dots as word separators (e.g. "Sprint.Qualifying",
    // "Free.Practice") — normalise so keyword matching works the same as on
    // human-readable titles with spaces.
    let s = session_or_name.to_lowercase().replace('.', " ");

    // Non-session "extras" some broadcasts release alongside the sessions
    // themselves (press conferences, interviews, highlights reels, …). These
    // titles/filenames often still mention the event name or bare "Grand
    // Prix" in passing, which would otherwise fall through to the generic
    // "Race" wildcard below and get misclassified as the actual race (e.g. a
    // "British Grand Prix ... Press Conference" release is not the race).
    // Reject them explicitly before the session table gets a chance to match.
    const NON_SESSION_EXTRAS: &[&str] = &[
        "press conference",
        "interview",
        "highlights",
        "preview",
        "review",
        "paddock",
        "grid walk",
        "documentary",
        "post race",
        "post-race",
        "pre race",
        "pre-race",
        "pitlane",
        "pit lane",
        "notebook",
        "f1 show",
        " ted ",
    ];
    if NON_SESSION_EXTRAS.iter().any(|kw| s.contains(kw)) {
        return None;
    }

    // F2/F3 uploads often use bare "Practice" without a "Free" prefix.
    if s.contains("practice")
        && !s.contains("free practice")
        && !s.contains("fp1")
        && !s.contains("fp2")
        && !s.contains("fp3")
        && !s.contains("practice 1")
        && !s.contains("practice 2")
        && !s.contains("practice 3")
    {
        return Some((1, "Practice".to_string()));
    }

    // Most-specific patterns first to avoid "qualifying"/"race" shadowing.
    let table: &[(&[&str], i32, &str)] = &[
        (
            &["sprint qualifying", "sprint shootout"],
            2,
            "Sprint Qualifying",
        ),
        (&["sprint race", "sprint"], 3, "Sprint"),
        (
            &["free practice 1", "practice 1", "fp1"],
            1,
            "Free Practice 1",
        ),
        (
            &["free practice 2", "practice 2", "fp2"],
            2,
            "Free Practice 2",
        ),
        (
            &["free practice 3", "practice 3", "fp3"],
            3,
            "Free Practice 3",
        ),
        (
            &["qualifying", "quali", "pole position", " pole "],
            4,
            "Qualifying",
        ),
        // Bare "free practice" without a session number (common in F1 release
        // names like "Free.Practice.SkyF1HD") — only when FP2/FP3 didn't match.
        (&["free practice"], 1, "Free Practice 1"),
        (&["race two", "race 2"], 5, "Race Two"),
        (&["race one", "race 1"], 4, "Race One"),
        (&["carrera"], 5, "Race"),
        // "weekend" covers full-weekend bundle releases (no specific session
        // named) — grouped under the same slot as an unspecified "Grand Prix"
        // upload, since neither names a single session.
        (&["grand prix", "race", " gp ", "weekend"], 5, "Race"),
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

/// Leading `NN.` index on bundled racing filenames (e.g. `01.Practice…`, `04.Race.Two…`).
pub fn numbered_prefix_episode(filename: &str) -> Option<i32> {
    let base = std::path::Path::new(filename)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(filename);
    numbered_prefix_re()
        .captures(base)
        .and_then(|c| c.get(1))
        .and_then(|m| m.as_str().parse::<i32>().ok())
        .filter(|&n| n > 0)
}

fn numbered_prefix_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^(\d{1,3})\.").expect("numbered_prefix_re"))
}

/// Netflix documentary series bundled under Formula keyword searches.
///
/// Titles like `Formula 1 Drive to Survive S07E10 …` must not go through the
/// race-weekend parser (which would treat them as a standalone movie).
pub fn classify_drive_to_survive(raw: &str) -> Option<(String, i32, i32)> {
    let normalized = raw.to_lowercase().replace('.', " ");
    if !normalized.contains("drive to survive") {
        return None;
    }
    let parsed = crate::parser::parse_title(raw);
    let season = *parsed.seasons.first()?;
    let episode = *parsed.episodes.first()?;
    Some(("Formula 1: Drive to Survive".to_string(), season, episode))
}

/// Map a racing torrent *filename* to `(episode, display title)`.
///
/// Bundled F1/F2/F3 releases often prefix every file with a running index
/// (`01.` … `10.`). When present, that index is the episode number and the
/// fixed session-slot table (FP1=1, Qualifying=4, Race=5) is not used.
pub fn racing_file_episode(filename: &str) -> Option<(i32, String)> {
    if let Some(ep) = numbered_prefix_episode(filename) {
        let title = racing_file_display_title(filename)
            .or_else(|| racing_session_episode(filename).map(|(_, t)| t))
            .unwrap_or_else(|| numbered_file_fallback_title(filename));
        return Some((ep, title));
    }
    racing_session_episode(filename)
}

/// Human-readable episode label from a numbered racing filename (e.g.
/// `01.F1…Drivers.Press.Conference…` → `"Drivers Press Conference"`).
///
/// Uses the trailing session/extra segment that [`parse_racing_title`] already
/// extracts after the Grand Prix marker — the same approach as the Python
/// `derive_sports_episode_title()` helper.
pub fn racing_file_display_title(filename: &str) -> Option<String> {
    let base = std::path::Path::new(filename)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(filename);
    parse_racing_title(base)
        .and_then(|r| r.session)
        .map(|s| multi_space_re().replace_all(s.trim(), " ").into_owned())
        .filter(|s| !s.is_empty())
}

/// Best-effort label when we have a numeric prefix but no session keyword.
fn numbered_file_fallback_title(filename: &str) -> String {
    if let Some(title) = racing_file_display_title(filename) {
        return title;
    }

    let base = std::path::Path::new(filename)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(filename);
    let stripped = numbered_prefix_re().replace(base, "").replace('.', " ");
    // Drop technical tokens and racing metadata (year, round, league prefix).
    let words: Vec<&str> = stripped
        .split_whitespace()
        .filter(|w| !is_racing_filename_noise(w))
        .collect();
    // Use the last few meaningful tokens (usually the session / extra name).
    let tail: Vec<&str> = words.iter().copied().rev().take(5).collect();
    if tail.is_empty() {
        return format!("Episode {}", numbered_prefix_episode(filename).unwrap_or(0));
    }
    tail.into_iter()
        .rev()
        .map(title_case_word)
        .collect::<Vec<_>>()
        .join(" ")
}

fn is_racing_filename_noise(word: &str) -> bool {
    let lower = word.to_lowercase();
    if lower.eq_ignore_ascii_case("mkv") || lower.eq_ignore_ascii_case("mp4") {
        return true;
    }
    if lower.ends_with('p')
        && lower
            .trim_end_matches(|c: char| c.is_ascii_alphabetic())
            .chars()
            .all(|c| c.is_ascii_digit())
    {
        return true;
    }
    if lower.len() == 4 && lower.chars().all(|c| c.is_ascii_digit()) {
        return true;
    }
    if lower.len() <= 4 && lower.starts_with('r') && lower[1..].chars().all(|c| c.is_ascii_digit())
    {
        return true;
    }
    matches!(
        lower.as_str(),
        "f1" | "f2" | "f3" | "formula" | "motogp" | "moto2" | "moto3"
    ) || matches!(
        lower.as_str(),
        "grand" | "prix" | "british" | "skyf1hd" | "f1tv"
    )
}

fn title_case_word(word: &str) -> String {
    let mut c = word.chars();
    c.next()
        .map(|f| f.to_uppercase().collect::<String>() + c.as_str())
        .unwrap_or_default()
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
        let r =
            parse_racing_title("Formula 1 Canadian Grand Prix Qualifying 23.05.2026.mkv").unwrap();
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
        assert_eq!(
            ep("Sprint Qualifying"),
            (2, "Sprint Qualifying".to_string())
        );
        assert_eq!(ep("Sprint Shootout"), (2, "Sprint Qualifying".to_string()));
        assert_eq!(ep("Sprint Race"), (3, "Sprint".to_string()));
        assert_eq!(ep("Sprint"), (3, "Sprint".to_string()));
        // "Grand Prix" without a session word is the race.
        assert_eq!(ep("Canadian Grand Prix"), (5, "Race".to_string()));
        // Full-weekend bundle release with no named session.
        assert_eq!(ep("Formula 2 British Weekend"), (5, "Race".to_string()));
        // Unknown.
        assert!(racing_session_episode("Pit Lane Channel").is_none());
        // Press conferences mention "Grand Prix" but are not the race session.
        assert!(
            racing_session_episode(
                "01.F1.2026.R09.British.Grand.Prix.Drivers.Press.Conference.SkyF1HD.1080P.mkv"
            )
            .is_none()
        );
        assert_eq!(
            racing_session_episode("09.F1.2026.R09.British.Grand.Prix.Race.SkyF1HD.1080P.mkv"),
            Some((5, "Race".to_string()))
        );
        assert_eq!(
            racing_session_episode(
                "04.F1.2026.R09.British.Grand.Prix.Sprint.Qualifying.SkyF1HD.1080P.mkv"
            ),
            Some((2, "Sprint Qualifying".to_string()))
        );
        assert_eq!(
            racing_session_episode(
                "03.F1.2026.R09.British.Grand.Prix.Free.Practice.SkyF1HD.1080P.mkv"
            ),
            Some((1, "Free Practice 1".to_string()))
        );
        assert_eq!(
            racing_session_episode(
                "07.F1.2026.R09.British.Grand.Prix.Qualifying.SkyF1HD.1080P.mkv"
            ),
            Some((4, "Qualifying".to_string()))
        );
        assert!(
            racing_session_episode(
                "08.F1.2026.R09.British.Grand.Prix.Teds.Qualifying.Notebook.SkyF1HD.1080P.mkv"
            )
            .is_none()
        );
    }

    #[test]
    fn numbered_prefix_episode_mapping() {
        assert_eq!(
            numbered_prefix_episode("01.Formula.3.Practice.mkv"),
            Some(1)
        );
        assert_eq!(
            numbered_prefix_episode("04.Formula.3.Race.Two.mkv"),
            Some(4)
        );
        assert_eq!(numbered_prefix_episode("Race.mkv"), None);

        assert_eq!(
            racing_file_episode("02.Formula.3.2026.R05.British.Qualifying.SkyF1HD.1080P.mkv"),
            Some((2, "Qualifying".to_string()))
        );
        assert_eq!(
            racing_file_episode("01.Formula.2.2026.R07.British.Practice.SkyF1HD.1080P.mkv"),
            Some((1, "Practice".to_string()))
        );
        assert_eq!(
            racing_file_episode("Formula.2.2026.R07.British.Practice.SkyF1HD.1080P.mkv"),
            Some((1, "Practice".to_string()))
        );
        assert_eq!(
            racing_file_episode("03.Formula.3.2026.R05.British.Race.One.SkyF1HD.1080P.mkv"),
            Some((3, "Race One".to_string()))
        );
        assert_eq!(
            racing_file_episode("04.Formula.3.2026.R05.British.Race.Two.SkyF1HD.1080P.mkv"),
            Some((4, "Race Two".to_string()))
        );
        // Numbered prefix wins over session-slot table (Qualifying would be slot 4).
        assert_eq!(
            racing_file_episode("02.F1.Qualifying.mkv").map(|(e, _)| e),
            Some(2)
        );
    }

    #[test]
    fn numbered_prefix_f1_extra_titles() {
        let cases = [
            (
                "01.F1.2026.R09.British.Grand.Prix.Drivers.Press.Conference.SkyF1HD.1080P.mkv",
                1,
                "Drivers Press Conference",
            ),
            (
                "05.F1.2026.R09.British.Grand.Prix.Team.Principals.Press.Conference.SkyF1HD.1080P.mkv",
                5,
                "Team Principals Press Conference",
            ),
            (
                "02.F1.2026.R09.British.Grand.Prix.F1.Show.SkyF1HD.1080P.mkv",
                2,
                "F1 Show",
            ),
            (
                "08.F1.2026.R09.British.Grand.Prix.Teds.Qualifying.Notebook.SkyF1HD.1080P.mkv",
                8,
                "Teds Qualifying Notebook",
            ),
            (
                "10.F1.2026.R09.British.Grand.Prix.Teds.Notebook.SkyF1HD.1080P.mkv",
                10,
                "Teds Notebook",
            ),
        ];
        for (filename, ep, title) in cases {
            let parsed = racing_file_episode(filename)
                .unwrap_or_else(|| panic!("expected parse for {filename}"));
            assert_eq!(parsed.0, ep, "episode for {filename}");
            assert_eq!(parsed.1, title, "title for {filename}");
        }
    }
    #[test]
    fn drive_to_survive_series_episode() {
        let (title, season, episode) = classify_drive_to_survive(
            "Formula 1 Drive to Survive S07E10 1080p WEB H264-SuccessfulCrab EZTV",
        )
        .unwrap();
        assert_eq!(title, "Formula 1: Drive to Survive");
        assert_eq!(season, 7);
        assert_eq!(episode, 10);
        assert!(classify_drive_to_survive("Formula 1 British Grand Prix 2026").is_none());
    }

    #[test]
    fn spanish_gp_reino_unido_carrera() {
        let r =
            parse_racing_title("Formula 1 GP Reino Unido Carrera ESPNF1 Lat EveHQ 2026").unwrap();
        assert_eq!(r.series_title, "Formula 1 British Grand Prix 2026");
        assert_eq!(r.year, Some(2026));
        assert_eq!(r.session.as_deref(), Some("Carrera"));
        assert_eq!(
            racing_session_episode("Carrera"),
            Some((5, "Race".to_string()))
        );
    }

    #[test]
    fn spanish_gp_austria_pole() {
        let r = parse_racing_title("Formula 1 GP Austria Pole DAZNF1 Spa EveHQ 2026").unwrap();
        assert_eq!(r.series_title, "Formula 1 Austrian Grand Prix 2026");
        assert_eq!(r.year, Some(2026));
        assert_eq!(r.session.as_deref(), Some("Pole"));
        assert_eq!(
            racing_session_episode("Pole"),
            Some((4, "Qualifying".to_string()))
        );
    }
}

#[cfg(test)]
mod matchup_tests {
    use super::*;

    #[test]
    fn canonical_title_sorts_reversed_teams_with_same_date() {
        let a = canonical_matchup_title("Los Angeles Angels at Boston Red Sox 13.04.2024").unwrap();
        let b = canonical_matchup_title("Boston Red Sox at Los Angeles Angels 13.04.2024").unwrap();
        assert_eq!(a, "Boston Red Sox at Los Angeles Angels 13.04.2024");
        assert_eq!(a, b);
    }

    #[test]
    fn canonical_title_distinguishes_different_dates() {
        let a = canonical_matchup_title("New York Mets at Atlanta Braves 06.07.2026").unwrap();
        let b = canonical_matchup_title("New York Mets at Atlanta Braves 04.07.2026").unwrap();
        assert_ne!(a, b);
        assert_eq!(a, "Atlanta Braves at New York Mets 06.07.2026");
        assert_eq!(b, "Atlanta Braves at New York Mets 04.07.2026");
    }

    #[test]
    fn canonical_title_distinguishes_different_matchups() {
        let a = canonical_matchup_title("Los Angeles Angels at Boston Red Sox 13.04.2024").unwrap();
        let b = canonical_matchup_title("Boston Red Sox at Los Angeles Angels 04.07.2026").unwrap();
        assert_ne!(a, b);
    }
}

#[cfg(test)]
mod category_detection_tests {
    use super::*;

    #[test]
    fn emmanuelle_is_not_mma() {
        // "Emmanuelle" contains the substring "mma" ("E-mma-nuelle"); this must
        // not be misdetected as the "fighting" (MMA) category.
        assert_eq!(
            detect_sports_category("Emmanuelle 2024 REMUX HD MA 51"),
            None
        );
        assert!(!is_sports_title("Emmanuelle 2024 REMUX HD MA 51"));
    }

    #[test]
    fn standalone_mma_is_fighting() {
        assert_eq!(
            detect_sports_category("MMA Fight Night 300 2026"),
            Some("fighting")
        );
    }

    #[test]
    fn aew_dynamite_with_season_episode_is_series() {
        let info =
            classify_aew_title("AEW Dynamite S07E41 Beach Break MyAEW 2026-07-08 1080p").unwrap();
        assert_eq!(info.series_title, "AEW Dynamite");
        assert_eq!(info.season_number, 7);
        assert_eq!(info.episode_number, 41);
        assert_eq!(info.brand, "AEW");
    }

    #[test]
    fn aew_forbidden_door_is_not_weekly_series() {
        assert!(classify_aew_title("aew forbidden door 2026 ppv amzn 1080p").is_none());
    }

    #[test]
    fn fighting_brand_detection() {
        assert_eq!(detect_fighting_brand("AEW Collision S04E03"), "AEW");
        assert_eq!(detect_fighting_brand("UFC Fight Night 240"), "UFC");
        assert_eq!(detect_fighting_brand("WWE SmackDown 2026"), "WWE");
    }

    #[test]
    fn classify_fighting_series_covers_aew() {
        let info =
            classify_fighting_series_title("AEW Collision S04E03 MyAEW 2026-07-11 1080p").unwrap();
        assert_eq!(info.series_title, "AEW Collision");
        assert_eq!(info.season_number, 4);
        assert_eq!(info.episode_number, 3);
    }
}

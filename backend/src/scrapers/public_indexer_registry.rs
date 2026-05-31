/// Static registry of public torrent indexer definitions.
///
/// Mirrors Python's `public_indexer_registry.py` INDEXER_OVERRIDES + generic selectors.
/// Each entry contains CSS selectors (parsel-style `::text` / `::attr(x)` suffixes),
/// URL templates, and capability flags. The HTML scraper strips the pseudo-elements
/// at runtime before passing to the `scraper` crate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HandlerType {
    Html,
    Rss,
    SubsPleaseJson,
}

/// Configuration for browsing/crawling recent items from an indexer.
#[derive(Debug, Clone)]
pub struct CrawlConfig {
    /// URL for browsing recent items. Use `{page}` placeholder for pagination.
    pub browse_url: &'static str,
    /// Maximum pages to fetch per crawl run.
    pub max_pages: u32,
}

pub struct IndexerDef {
    pub key: &'static str,
    pub source_name: &'static str,
    /// URL templates with `{query}` and optional `{page}` placeholders.
    pub query_url_templates: &'static [&'static str],
    pub row_selectors: &'static [&'static str],
    pub title_selectors: &'static [&'static str],
    pub detail_selectors: &'static [&'static str],
    pub magnet_selectors: &'static [&'static str],
    pub size_selectors: &'static [&'static str],
    pub seeder_selectors: &'static [&'static str],
    pub supports_movie: bool,
    pub supports_series: bool,
    pub supports_anime: bool,
    /// When true this indexer is behind Cloudflare and needs Byparr to work reliably.
    pub solve_cloudflare: bool,
    /// When true a plain HTTP fetch is attempted even if CF bypass is unavailable.
    pub http_fallback: bool,
    pub pages_per_query: u32,
    pub handler: HandlerType,
    /// Detail-page hrefs longer than this are considered ad/redirect links and skipped.
    pub max_detail_url_length: usize,
    /// Browse/crawl configuration for feed-mode crawling recent items.
    pub crawl: Option<CrawlConfig>,
}

// ─── Generic selector sets (mirrors Python GENERIC_* constants) ───────────────

const GENERIC_ROW_SELECTORS: &[&str] = &[
    "table.table-list tbody tr",
    "table.torrent-list tbody tr",
    "table tbody tr",
    "table tr",
    "tr",
    "div.torrent",
    "div.search-result",
    "li",
];
const GENERIC_TITLE_SELECTORS: &[&str] = &[
    "td.name a:last-child::text",
    "td.name a:nth-child(2)::text",
    "td:nth-child(2) a:last-child::text",
    "td:nth-child(2) a::text",
    "a.detLink::text",
    "a[href*='/torrent/']::text",
    "a[href*='details']::text",
    "a[title]::attr(title)",
];
const GENERIC_DETAIL_SELECTORS: &[&str] = &[
    "td.name a:last-child::attr(href)",
    "td.name a:nth-child(2)::attr(href)",
    "td:nth-child(2) a:last-child::attr(href)",
    "td:nth-child(2) a::attr(href)",
    "a.detLink::attr(href)",
    "a[href*='/torrent/']::attr(href)",
    "a[href*='details']::attr(href)",
];
const GENERIC_MAGNET_SELECTORS: &[&str] = &[
    "a[href^='magnet:?']::attr(href)",
    "a[title*='Magnet']::attr(href)",
];
const GENERIC_SIZE_SELECTORS: &[&str] = &[
    "td.size *::text",
    "td.size::text",
    "td:nth-child(4)::text",
    "td:nth-child(5)::text",
    "span.size::text",
];
const GENERIC_SEEDER_SELECTORS: &[&str] = &[
    "td.seeds::text",
    "td.seeders::text",
    "td:nth-child(6)::text",
    "td:nth-child(7)::text",
    "span.seeds::text",
];

// ─── Registry ─────────────────────────────────────────────────────────────────

pub static ALL_INDEXERS: &[IndexerDef] = &[
    // ── 1337x (CF, stealthy) ─────────────────────────────────────────────────
    IndexerDef {
        key: "x1337",
        source_name: "1337x",
        query_url_templates: &["https://1337x.to/search/{query}/{page}/"],
        row_selectors: &["table.table-list tbody tr", "table.table-list tr"],
        title_selectors: &[
            "td.name a:last-child::text",
            "td.name a:nth-child(2)::text",
            "a[href*='/torrent/']::text",
        ],
        detail_selectors: &[
            "td.name a:last-child::attr(href)",
            "td.name a:nth-child(2)::attr(href)",
            "a[href*='/torrent/']::attr(href)",
        ],
        magnet_selectors: &["a[href^='magnet:?']::attr(href)"],
        size_selectors: &["td.size *::text", "td.size::text"],
        seeder_selectors: &["td.seeds::text", "td.seeders::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: true,
        http_fallback: false,
        pages_per_query: 2,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://1337x.to/trending/{page}/",
            max_pages: 3,
        }),
    },
    // ── BT4G (RSS, no CF) ────────────────────────────────────────────────────
    IndexerDef {
        key: "bt4g",
        source_name: "BT4G",
        query_url_templates: &["https://bt4gprx.com/search?q={query}&page=rss"],
        row_selectors: &[],
        title_selectors: &[],
        detail_selectors: &[],
        magnet_selectors: &[],
        size_selectors: &[],
        seeder_selectors: &[],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: false,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::Rss,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://bt4gprx.com/search?q=&orderby=date&p={page}",
            max_pages: 3,
        }),
    },
    // ── Nyaa (no CF, anime) ──────────────────────────────────────────────────
    IndexerDef {
        key: "nyaa",
        source_name: "Nyaa",
        query_url_templates: &["https://nyaa.si/?f=0&c=1_0&q={query}&p={page}"],
        row_selectors: &["table.torrent-list tbody tr"],
        title_selectors: &[
            "td:nth-child(2) a:last-child::text",
            "td:nth-child(2) a::text",
        ],
        detail_selectors: &[
            "td:nth-child(2) a:last-child::attr(href)",
            "td:nth-child(2) a::attr(href)",
        ],
        magnet_selectors: &[
            "td:nth-child(3) a[href^='magnet:?']::attr(href)",
            "a[href^='magnet:?']::attr(href)",
        ],
        size_selectors: &["td:nth-child(4)::text"],
        seeder_selectors: &["td:nth-child(6)::text"],
        supports_movie: false,
        supports_series: false,
        supports_anime: true,
        solve_cloudflare: false,
        http_fallback: false,
        pages_per_query: 2,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://nyaa.si/?p={page}",
            max_pages: 3,
        }),
    },
    // ── SubsPlease (JSON API, anime) ─────────────────────────────────────────
    IndexerDef {
        key: "subsplease",
        source_name: "SubsPlease",
        query_url_templates: &["https://subsplease.org/api/?f=search&tz=UTC&s={query}"],
        row_selectors: &[],
        title_selectors: &[],
        detail_selectors: &[],
        magnet_selectors: &[],
        size_selectors: &[],
        seeder_selectors: &[],
        supports_movie: false,
        supports_series: false,
        supports_anime: true,
        solve_cloudflare: false,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::SubsPleaseJson,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://subsplease.org/api/?f=latest&tz=UTC",
            max_pages: 1,
        }),
    },
    // ── AnimeTosho (no CF, anime) ────────────────────────────────────────────
    IndexerDef {
        key: "animetosho",
        source_name: "AnimeTosho",
        query_url_templates: &[
            "https://animetosho.org/search?q={query}",
            "https://animetosho.org/search?q={query}&page={page}",
        ],
        row_selectors: &["div.home_list_entry", "article", "li", "tr"],
        title_selectors: &["a[href*='/view/']::text", "a::text"],
        detail_selectors: &["a[href*='/view/']::attr(href)", "a::attr(href)"],
        magnet_selectors: &["a[href^='magnet:?']::attr(href)"],
        size_selectors: &["span.size::text"],
        seeder_selectors: &["span.seeders::text"],
        supports_movie: false,
        supports_series: false,
        supports_anime: true,
        solve_cloudflare: false,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://animetosho.org/feed/atom?offset={page}",
            max_pages: 3,
        }),
    },
    // ── EZTV (no CF, series + anime) ─────────────────────────────────────────
    IndexerDef {
        key: "eztv",
        source_name: "EZTV",
        query_url_templates: &[
            "https://eztvx.to/search/{query}",
            "https://eztv.wf/search/{query}",
        ],
        row_selectors: &["tr.forum_header_border"],
        title_selectors: &["a.epinfo::text"],
        detail_selectors: &["a.epinfo::attr(href)"],
        magnet_selectors: &[
            "a[href^='magnet:?']::attr(href)",
            "a[title*='Magnet']::attr(href)",
        ],
        size_selectors: &["td.forum_thread_post::text"],
        seeder_selectors: &[
            "td.forum_thread_post_end font::text",
            "td.forum_thread_post_end::text",
        ],
        supports_movie: false,
        supports_series: true,
        supports_anime: true,
        solve_cloudflare: false,
        http_fallback: false,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: None,
    },
    // ── YTS (CF-protected, movies only) ──────────────────────────────────────
    IndexerDef {
        key: "yts",
        source_name: "YTS",
        query_url_templates: &["https://yts.bz/browse-movies/{query}/all/all/0/latest/0/all"],
        row_selectors: &["div.browse-movie-wrap"],
        title_selectors: &["a.browse-movie-title::text"],
        detail_selectors: &["a.browse-movie-link::attr(href)"],
        magnet_selectors: &["a[href^='magnet:?']::attr(href)"],
        size_selectors: &[],
        seeder_selectors: &[],
        supports_movie: true,
        supports_series: false,
        supports_anime: false,
        solve_cloudflare: true,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://yts.mx/browse-movies/0/all/all/0/latest/0/all?page={page}",
            max_pages: 3,
        }),
    },
    // ── The Pirate Bay (CF) ──────────────────────────────────────────────────
    IndexerDef {
        key: "thepiratebay",
        source_name: "The Pirate Bay",
        query_url_templates: &["https://thepiratebay.org/search.php?q={query}"],
        row_selectors: &["li.list-entry"],
        title_selectors: &["span.item-name a::text"],
        detail_selectors: &["span.item-name a::attr(href)"],
        magnet_selectors: &["a[href^='magnet:?']::attr(href)"],
        size_selectors: &["span.item-size::text"],
        seeder_selectors: &["span.item-seed::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: true,
        http_fallback: false,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://thepiratebay.org/browse/100/{page}/7",
            max_pages: 3,
        }),
    },
    // ── LimeTorrents (no CF, http_fallback) ──────────────────────────────────
    IndexerDef {
        key: "limetorrents",
        source_name: "LimeTorrents",
        query_url_templates: &[
            "https://www.limetorrents.fun/search/all/{query}/seeds/1/",
            "https://www.limetorrents.lol/search/all/{query}/seeds/1/",
        ],
        row_selectors: &["table.table2 tr", "table tr"],
        title_selectors: &[
            "td.tdleft div.tt-name a:last-child::text",
            "td.tdleft div.tt-name a:nth-child(2)::text",
        ],
        detail_selectors: &[
            "td.tdleft div.tt-name a:last-child::attr(href)",
            "td.tdleft div.tt-name a:nth-child(2)::attr(href)",
        ],
        magnet_selectors: &[
            "a[href^='magnet:?']::attr(href)",
            "a[title*='Magnet']::attr(href)",
        ],
        size_selectors: &["td.tdnormal::text"],
        seeder_selectors: &["td.tdseed::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: false,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://www.limetorrents.lol/browse-torrents/all/{page}/",
            max_pages: 3,
        }),
    },
    // ── UIndex (no CF, all types) ────────────────────────────────────────────
    IndexerDef {
        key: "uindex",
        source_name: "UIndex",
        query_url_templates: &["https://uindex.org/search.php?search={query}&c=0"],
        row_selectors: &["table tr"],
        title_selectors: &["a[href*='/details.php?id=']::text"],
        detail_selectors: &["a[href*='/details.php?id=']::attr(href)"],
        magnet_selectors: &["a[href^='magnet:?']::attr(href)"],
        size_selectors: &["td:nth-child(3)::text"],
        seeder_selectors: &["td:nth-child(4) span.g::text", "td:nth-child(4)::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: true,
        solve_cloudflare: false,
        http_fallback: false,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "https://uindex.org/browse/{page}",
            max_pages: 3,
        }),
    },
    // ── Rutor (no CF) ────────────────────────────────────────────────────────
    IndexerDef {
        key: "rutor",
        source_name: "Rutor",
        query_url_templates: &["https://rutor.info/search/{query}"],
        row_selectors: &["table tr"],
        title_selectors: &[
            "td:nth-child(2) a[href^='/torrent/']::text",
            "td:nth-child(2) a[href*='/torrent/']::text",
        ],
        detail_selectors: &[
            "td:nth-child(2) a[href^='/torrent/']::attr(href)",
            "td:nth-child(2) a[href*='/torrent/']::attr(href)",
        ],
        magnet_selectors: &[
            "td:nth-child(2) a[href^='magnet:?']::attr(href)",
            "a[href^='magnet:?']::attr(href)",
        ],
        size_selectors: &["td:nth-child(4)::text"],
        seeder_selectors: &["td:nth-child(5) span.green::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: false,
        http_fallback: false,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: Some(CrawlConfig {
            browse_url: "http://rutor.info/browse/{page}",
            max_pages: 3,
        }),
    },
    // ── OxTorrent (no CF, http_fallback) ─────────────────────────────────────
    IndexerDef {
        key: "oxtorrent",
        source_name: "OxTorrent",
        query_url_templates: &[
            "https://www.oxtorrent.co/recherche/{query}",
            "https://www.oxtorrent.co/search_torrent?torrentSearch={query}",
        ],
        row_selectors: &["table tbody tr", "table tr"],
        title_selectors: &["td:nth-child(1) a[href^='/torrent/']::text"],
        detail_selectors: &["td:nth-child(1) a[href^='/torrent/']::attr(href)"],
        magnet_selectors: &["a[href^='magnet:?']::attr(href)"],
        size_selectors: &["td:nth-child(2)::text"],
        seeder_selectors: &["td:nth-child(3)::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: false,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: None,
    },
    // ── Torlock (no CF, http_fallback, short URL limit) ───────────────────────
    IndexerDef {
        key: "torlock",
        source_name: "Torlock",
        query_url_templates: &["https://www.torlock.com/all/torrents/{query}.html"],
        row_selectors: &["tr"],
        title_selectors: &[
            "a[href*='/torrent/']::text",
            "a[href*='.t0r.space/torrent/']::text",
        ],
        detail_selectors: &[
            "a[href*='/torrent/']::attr(href)",
            "a[href*='.t0r.space/torrent/']::attr(href)",
        ],
        magnet_selectors: &["a[href^='magnet:?']::attr(href)"],
        size_selectors: &["td.ts::text"],
        seeder_selectors: &["td.tul::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: false,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 180,
        crawl: None,
    },
    // ── TheRARBG (no CF, http_fallback) ──────────────────────────────────────
    IndexerDef {
        key: "therarbg",
        source_name: "TheRARBG",
        query_url_templates: &["https://therarbg.to/get-posts/keywords:{query}/"],
        row_selectors: &["div.wrapper"],
        title_selectors: &["a[href^='/post-detail/']::text"],
        detail_selectors: &["a[href^='/post-detail/']::attr(href)"],
        magnet_selectors: GENERIC_MAGNET_SELECTORS,
        size_selectors: GENERIC_SIZE_SELECTORS,
        seeder_selectors: GENERIC_SEEDER_SELECTORS,
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: false,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: None,
    },
    // ── TorrentDownloads (CF) ─────────────────────────────────────────────────
    IndexerDef {
        key: "torrentdownloads",
        source_name: "TorrentDownloads",
        query_url_templates: &["https://www.torrentdownloads.pro/search/?search={query}"],
        row_selectors: &["div.grey_bar3"],
        title_selectors: &["p a[href^='/torrent/']::text"],
        detail_selectors: &["p a[href^='/torrent/']::attr(href)"],
        magnet_selectors: GENERIC_MAGNET_SELECTORS,
        size_selectors: &["span::text"],
        seeder_selectors: &["span:nth-of-type(2)::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: true,
        http_fallback: false,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: None,
    },
    // ── YourBittorrent (CF, generic selectors) ────────────────────────────────
    IndexerDef {
        key: "yourbittorrent",
        source_name: "YourBittorrent",
        query_url_templates: &[
            "https://yourbittorrent.com/?q={query}",
            "https://yourbittorrent2.com/?q={query}",
        ],
        row_selectors: GENERIC_ROW_SELECTORS,
        title_selectors: GENERIC_TITLE_SELECTORS,
        detail_selectors: GENERIC_DETAIL_SELECTORS,
        magnet_selectors: GENERIC_MAGNET_SELECTORS,
        size_selectors: GENERIC_SIZE_SELECTORS,
        seeder_selectors: GENERIC_SEEDER_SELECTORS,
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: true,
        http_fallback: false,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: None,
    },
    // ── TorrentDownload (no CF, http_fallback) ────────────────────────────────
    IndexerDef {
        key: "torrentdownload",
        source_name: "TorrentDownload",
        query_url_templates: &[
            "https://www.torrentdownload.info/search?q={query}",
            "https://www.torrentdownload.info/searchr?q={query}",
        ],
        row_selectors: &["tr"],
        title_selectors: &["td.tdleft .tt-name a[href*='-']::text"],
        detail_selectors: &["td.tdleft .tt-name a[href*='-']::attr(href)"],
        magnet_selectors: GENERIC_MAGNET_SELECTORS,
        size_selectors: &["td.tdnormal::text"],
        seeder_selectors: &["td.tdseed::text"],
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: false,
        http_fallback: true,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: None,
    },
    // ── BitSearch (CF, generic selectors) ────────────────────────────────────
    IndexerDef {
        key: "bitsearch",
        source_name: "BitSearch",
        query_url_templates: &[
            "https://bitsearch.to/search?q={query}",
            "https://bitsearch.mrunblock.bond/search?q={query}",
        ],
        row_selectors: GENERIC_ROW_SELECTORS,
        title_selectors: GENERIC_TITLE_SELECTORS,
        detail_selectors: GENERIC_DETAIL_SELECTORS,
        magnet_selectors: GENERIC_MAGNET_SELECTORS,
        size_selectors: GENERIC_SIZE_SELECTORS,
        seeder_selectors: GENERIC_SEEDER_SELECTORS,
        supports_movie: true,
        supports_series: true,
        supports_anime: false,
        solve_cloudflare: true,
        http_fallback: false,
        pages_per_query: 1,
        handler: HandlerType::Html,
        max_detail_url_length: 260,
        crawl: None,
    },
];

/// Return the subset of indexers relevant for a given media type, filtered by
/// enabled-sites list and Byparr availability.
pub fn get_indexers_for_media(
    media_type: &str,
    enabled_sites: Option<&str>,
    byparr_available: bool,
) -> Vec<&'static IndexerDef> {
    let enabled_set: Option<std::collections::HashSet<&str>> = enabled_sites.map(|s| {
        s.split(',')
            .map(str::trim)
            .filter(|k| !k.is_empty())
            .collect()
    });

    // Non-CF indexers first so cheap scrapes consume the budget before any
    // Chromium/byparr session is launched. CF indexers run only if budget remains.
    let mut indexers: Vec<&'static IndexerDef> = ALL_INDEXERS
        .iter()
        .filter(|def| {
            let type_ok = match media_type {
                "movie" => def.supports_movie,
                "series" => def.supports_series,
                "anime" => def.supports_anime,
                _ => def.supports_movie || def.supports_series,
            };
            if !type_ok {
                return false;
            }
            if let Some(ref enabled) = enabled_set {
                if !enabled.contains(def.key) {
                    return false;
                }
            }
            // Skip CF indexers when Byparr is unavailable (worker-only)
            if def.solve_cloudflare && !byparr_available {
                return false;
            }
            true
        })
        .collect();
    // Stable sort: non-CF first, CF (byparr) last.
    indexers.sort_by_key(|def| def.solve_cloudflare);
    indexers
}

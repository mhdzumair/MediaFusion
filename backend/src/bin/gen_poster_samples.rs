/// Standalone binary to generate placeholder poster samples for visual review.
/// Run with: cargo run --bin gen_poster_samples
/// Output: /tmp/poster_samples/
fn main() {
    // (title, media_type, year)
    let samples: &[(&str, &str, Option<i32>)] = &[
        // ── Latin / English ──────────────────────────────────────────────────
        ("Inception", "movie", Some(2010)),
        ("The Princess Bride", "movie", Some(1987)),
        ("Breaking Bad S01E02", "series", Some(2008)),
        ("The Mandalorian S03E01", "series", Some(2023)),
        ("BBC News", "tv", None),
        // Long title — wrapping stress test
        (
            "Endurance: Shackleton's Incredible Voyage to the South Pole",
            "movie",
            Some(2022),
        ),
        // Very short
        ("Dune", "movie", Some(2021)),
        // ── Cyrillic ─────────────────────────────────────────────────────────
        (
            "Ульяна Лобаева - Приют неприкаянных душ",
            "movie",
            Some(2026),
        ),
        ("Игра престолов", "series", Some(2011)),
        ("Война и мир", "movie", Some(1966)),
        // ── Arabic / Persian ─────────────────────────────────────────────────
        ("مسلسل عربي", "series", Some(2023)),
        ("لعبة العروش", "series", Some(2011)),
        // Persian (Farsi)
        ("سریال فارسی", "series", Some(2022)),
        // ── Hebrew ───────────────────────────────────────────────────────────
        ("משחקי הכס", "series", Some(2011)),
        ("הבית הגדול", "movie", Some(2020)),
        // ── Greek ────────────────────────────────────────────────────────────
        ("Το Παιχνίδι των Θρόνων", "series", Some(2011)),
        // ── Devanagari (Hindi) ───────────────────────────────────────────────
        ("बाहुबली: द बिगनिंग", "movie", Some(2015)),
        ("क्राइम पेट्रोल", "series", Some(2010)),
        // ── Thai ─────────────────────────────────────────────────────────────
        ("เกมส์ออฟโธรนส์", "series", Some(2011)),
        // ── Korean ───────────────────────────────────────────────────────────
        ("오징어 게임", "series", Some(2021)),
        ("기생충", "movie", Some(2019)),
        // ── Japanese ─────────────────────────────────────────────────────────
        ("進撃の巨人", "series", Some(2013)),
        ("千と千尋の神隠し", "movie", Some(2001)),
        // ── Chinese ──────────────────────────────────────────────────────────
        ("权力的游戏", "series", Some(2011)),
        ("卧虎藏龙", "movie", Some(2000)),
        // ── Mixed / edge cases ───────────────────────────────────────────────
        // Mixed script in one title
        ("Attack on Titan 進撃の巨人", "series", Some(2013)),
        // Emoji (should fallback gracefully)
        ("Movie Night 🎬", "movie", Some(2024)),
        // Turkish (Latin with diacritics)
        ("Diriliş: Ertuğrul", "series", Some(2014)),
    ];

    let out = std::path::Path::new("/tmp/poster_samples");
    std::fs::create_dir_all(out).unwrap();

    let total = samples.len();
    let mut ok = 0usize;
    let mut failed = 0usize;

    for (title, media_type, year) in samples {
        let poster_result = mediafusion_api::poster::generate_placeholder(title, media_type, *year);
        match poster_result {
            Ok(bytes) => {
                let safe: String = title
                    .chars()
                    .map(|c| {
                        if c.is_alphanumeric() || c == '-' {
                            c
                        } else {
                            '_'
                        }
                    })
                    .collect();
                let truncated: String = safe.chars().take(35).collect();
                let path = out.join(format!("{}_{}.jpg", media_type, truncated));
                std::fs::write(&path, &bytes).unwrap();
                println!("✓  {}", path.display());
                ok += 1;
            }
            Err(e) => {
                eprintln!("✗  [{media_type}] {title:?}: {e}");
                failed += 1;
            }
        }
    }

    println!("\n{ok}/{total} generated ({failed} failed) → /tmp/poster_samples/");
}

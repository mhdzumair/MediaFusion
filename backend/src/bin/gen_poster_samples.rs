/// Standalone binary to generate placeholder poster samples for visual review.
/// Run with: cargo run --bin gen_poster_samples
/// Output: /tmp/poster_samples/
fn main() {
    // (title, media_type, year)
    let samples: &[(&str, &str, Option<i32>)] = &[
        (
            "Endurance Shackleton's Incredible Voyage",
            "movie",
            Some(2022),
        ),
        ("Good Omens A Full Cast Production", "series", Some(2019)),
        ("The Princess Bride", "movie", Some(1987)),
        ("Leviathan Wakes", "series", Some(2015)),
        ("Frank Herbert", "movie", None),
        ("How to Build a Car", "movie", None),
        ("The Intelligent Investor", "movie", None),
        ("Breaking Bad S01E02", "series", Some(2008)),
        ("Inception", "movie", Some(2010)),
        ("Rai 1", "tv", None),
        ("BBC News", "tv", None),
        ("The Mandalorian S03E01", "series", Some(2023)),
    ];

    let out = std::path::Path::new("/tmp/poster_samples");
    std::fs::create_dir_all(out).unwrap();

    for (title, media_type, year) in samples {
        let bytes =
            mediafusion_api::poster::generate_placeholder(title, media_type, *year).unwrap();
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
        let path = out.join(format!(
            "{}_{}.jpg",
            media_type,
            &safe[..safe.len().min(35)]
        ));
        std::fs::write(&path, &bytes).unwrap();
        println!("wrote {}", path.display());
    }
    println!("done — open /tmp/poster_samples/ to review");
}

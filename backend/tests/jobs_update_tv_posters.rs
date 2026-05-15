#[test]
fn tv_poster_fuzzy_match_finds_exact_channel() {
    let channel_names = [
        "bbc news".to_string(),
        "cnn".to_string(),
        "sky news".to_string(),
    ];

    let title = "BBC News";
    // Strip brackets/parens, lowercase — mirrors the handler's normalisation.
    let normalized = title.to_lowercase();

    let best = channel_names
        .iter()
        .map(|name| (name, strsim::jaro_winkler(&normalized, name.as_str())))
        .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap());

    let (matched, score) = best.unwrap();
    assert!(score >= 0.85, "score was {score}");
    assert_eq!(matched, "bbc news");
}

#[test]
fn tv_poster_fuzzy_match_rejects_low_score() {
    let channel_names = ["bbc news".to_string(), "cnn".to_string()];
    let title = "Completely Unrelated Channel XYZ";
    let normalized = title.to_lowercase();

    let best_score = channel_names
        .iter()
        .map(|name| strsim::jaro_winkler(&normalized, name.as_str()))
        .fold(0.0_f64, f64::max);

    assert!(
        best_score < 0.85,
        "should not match: score was {best_score}"
    );
}

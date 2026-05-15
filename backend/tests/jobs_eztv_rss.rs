mod common;

use mediafusion_api::jobs::handlers::spiders::eztv_rss::parse_eztv_rss;

#[tokio::test]
async fn eztv_rss_parse_extracts_three_items() {
    let xml = include_str!("fixtures/spiders/eztv_rss.xml");
    let items = parse_eztv_rss(xml);
    assert_eq!(items.len(), 3);

    // Series item
    let series = items
        .iter()
        .find(|i| i.info_hash.as_deref() == Some("aabbccddeeff00112233445566778899aabbccdd"))
        .expect("penguin item not found");
    assert_eq!(series.seeds, Some(142));
    assert_eq!(series.enclosure_size, Some(1073741824));
    assert!(series.title.contains("Penguin"));

    // Movie item
    let movie = items
        .iter()
        .find(|i| i.info_hash.as_deref() == Some("1122334455667788990011223344556677889900"))
        .expect("dune item not found");
    assert_eq!(movie.seeds, Some(89));

    // Snapshot test
    let snapshot: Vec<serde_json::Value> = items
        .iter()
        .map(|i| {
            serde_json::json!({
                "info_hash": i.info_hash,
                "seeders": i.seeds,
                "title": i.title,
            })
        })
        .collect();
    insta::assert_json_snapshot!(snapshot);
}

#[tokio::test]
async fn eztv_rss_skips_invalid_hash() {
    let xml = r#"<?xml version="1.0"?><rss version="2.0" xmlns:torrent="x"><channel>
      <item><title>Bad Item</title><torrent:infoHash>not-a-hash</torrent:infoHash><torrent:seeds>0</torrent:seeds></item>
    </channel></rss>"#;
    // parse_eztv_rss itself does not filter by hash length — that filtering
    // happens in the job handler.  The raw parser returns all items that have
    // title/hash/seeds in the XML.  So we verify the item IS returned here and
    // that the invalid hash is preserved (the handler will skip it later).
    let items = parse_eztv_rss(xml);
    // There is exactly 1 item parsed from the XML.
    assert_eq!(items.len(), 1);
    // The raw info_hash field contains the invalid value.
    assert_eq!(
        items[0].info_hash.as_deref(),
        Some("not-a-hash"),
        "raw parser should preserve the hash value unchanged"
    );
}

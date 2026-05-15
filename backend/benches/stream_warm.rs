use criterion::{criterion_group, criterion_main, Criterion};

fn bench_decode_blob(c: &mut Criterion) {
    use mediafusion_api::cache::codec::{decode_blob, encode_blob};
    use serde_json::json;

    let data = json!({
        "torrents": (0..50).map(|i| json!({
            "name": format!("Movie.2024.1080p.BluRay-GRP [{}]", i),
            "info_hash": format!("{:040x}", i),
            "quality": "BluRay",
            "resolution": "1080p",
            "is_public": true
        })).collect::<Vec<_>>()
    });
    let blob = encode_blob(&data).unwrap();

    c.bench_function("decode_blob_50_torrents", |b| b.iter(|| decode_blob(&blob)));
}

criterion_group!(benches, bench_decode_blob);
criterion_main!(benches);

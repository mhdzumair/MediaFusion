/// One-time setup binary: download the quantized NSFW ONNX model from HuggingFace.
///
/// The ONNX file is fully cross-platform — the same file runs on Apple Silicon,
/// Linux x86_64, Linux arm64, etc.  `ort` handles the platform-specific runtime.
///
/// Usage:
///   cargo run --bin mediafusion-nsfw-setup
///   cargo run --bin mediafusion-nsfw-setup -- --out /custom/path/nsfw_model.onnx
///   cargo run --bin mediafusion-nsfw-setup -- --url https://example.com/model.onnx
use std::path::{Path, PathBuf};

use mediafusion_api::nsfw::{MODEL_HF_URL, NsfwClassifier, NsfwScores};

struct Args {
    out: PathBuf,
    url: String,
    verify: bool,
}

fn parse_args() -> Args {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let mut out = default_model_path();
    let mut url = MODEL_HF_URL.to_string();
    let mut verify = true;
    let mut i = 0;

    while i < raw.len() {
        match raw[i].as_str() {
            "--out" => {
                i += 1;
                if let Some(v) = raw.get(i) {
                    out = PathBuf::from(v);
                }
            }
            "--url" => {
                i += 1;
                if let Some(v) = raw.get(i) {
                    url = v.clone();
                }
            }
            "--no-verify" => {
                verify = false;
            }
            "--help" | "-h" => {
                eprintln!(
                    "usage: mediafusion-nsfw-setup [--out PATH] [--url URL] [--no-verify]\n\n\
                     Downloads the NSFW ONNX model and saves it to PATH.\n\
                     Default path: {}\n\
                     Default URL:  {}",
                    default_model_path().display(),
                    MODEL_HF_URL
                );
                std::process::exit(0);
            }
            other => {
                eprintln!("unknown argument '{other}'; try --help");
                std::process::exit(1);
            }
        }
        i += 1;
    }

    Args { out, url, verify }
}

fn default_model_path() -> PathBuf {
    // Resolve resources/ relative to the Cargo manifest or the working directory.
    let from_manifest =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../resources/nsfw_model.onnx");
    if let Some(parent) = from_manifest.parent() {
        if parent.exists() {
            return from_manifest;
        }
    }
    PathBuf::from("resources/nsfw_model.onnx")
}

#[tokio::main]
async fn main() {
    let args = parse_args();

    println!("━━ mediafusion-nsfw-setup");
    println!("   model  → {}", args.out.display());
    println!("   source → {}", args.url);

    // Ensure parent directory exists.
    if let Some(parent) = args.out.parent() {
        std::fs::create_dir_all(parent).expect("could not create output directory");
    }

    // Download with progress.
    download(&args.url, &args.out).await;

    if args.verify {
        verify(&args.out);
    }

    println!("\n✓  Done. Set POSTER_NSFW_ENABLED=true (default) and restart the server.");
    println!("   To run the backfill scan:");
    println!("   cargo run --bin mediafusion-worker -- --run-job poster_nsfw_scan");
}

// ─── Download ────────────────────────────────────────────────────────────────

async fn download(url: &str, dest: &PathBuf) {
    use std::io::Write;

    println!("\n━━ Downloading …");

    let client = reqwest::Client::builder()
        .user_agent("mediafusion-nsfw-setup/1.0")
        .redirect(reqwest::redirect::Policy::limited(5))
        .build()
        .expect("reqwest client");

    let resp = client.get(url).send().await.unwrap_or_else(|e| {
        eprintln!("request failed: {e}");
        std::process::exit(1);
    });

    if !resp.status().is_success() {
        eprintln!("HTTP {}: {url}", resp.status());
        std::process::exit(1);
    }

    let total_bytes = resp.content_length();

    let mut file = std::fs::File::create(dest).unwrap_or_else(|e| {
        eprintln!("create {}: {e}", dest.display());
        std::process::exit(1);
    });

    let mut downloaded: u64 = 0;
    let mut stream = resp.bytes_stream();

    use futures::StreamExt;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.unwrap_or_else(|e| {
            eprintln!("stream error: {e}");
            std::process::exit(1);
        });
        file.write_all(&chunk).unwrap_or_else(|e| {
            eprintln!("write error: {e}");
            std::process::exit(1);
        });
        downloaded += chunk.len() as u64;

        if let Some(total) = total_bytes {
            let pct = downloaded * 100 / total;
            let bar = "#".repeat((pct / 4) as usize);
            print!(
                "\r  [{bar:<25}] {pct:3}%  {:.1} MB / {:.1} MB",
                downloaded as f64 / 1e6,
                total as f64 / 1e6
            );
            std::io::stdout().flush().ok();
        } else {
            print!("\r  {:.1} MB downloaded", downloaded as f64 / 1e6);
            std::io::stdout().flush().ok();
        }
    }
    println!();

    let size_mb = std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0) as f64 / 1e6;
    println!("  saved {:.1} MB → {}", size_mb, dest.display());
}

// ─── Verify ──────────────────────────────────────────────────────────────────

fn verify(path: &Path) {
    println!("\n━━ Verifying ONNX model …");

    let path_str = path.to_string_lossy();
    let clf = NsfwClassifier::load(&path_str).unwrap_or_else(|| {
        eprintln!("  failed to load model");
        std::process::exit(1);
    });

    // Create a 384×384 white JPEG in memory and classify it.
    let white_img = image::DynamicImage::new_rgb8(384, 384);
    let mut buf = Vec::new();
    white_img
        .write_to(
            &mut std::io::Cursor::new(&mut buf),
            image::ImageFormat::Jpeg,
        )
        .expect("encode test image");

    let scores: NsfwScores = clf.classify(&buf).unwrap_or_else(|e| {
        eprintln!("  inference failed: {e}");
        std::process::exit(1);
    });

    println!("  test image (white 384×384):");
    println!("    sfw  score: {:.4}", scores.sfw);
    println!("    nsfw score: {:.4}", scores.nsfw);
    println!(
        "    combined:   {:.4}  (threshold default=0.7)",
        scores.combined()
    );

    let total = scores.sfw + scores.nsfw;
    if (total - 1.0).abs() > 0.05 {
        eprintln!("  WARNING: probabilities sum to {total:.4}, expected ≈1.0");
    } else {
        println!("  ✓ probabilities sum to {total:.4}");
    }

    // White image should not be flagged as NSFW.
    if scores.combined() < 0.5 {
        println!(
            "  ✓ white image correctly scores as sfw ({:.4})",
            scores.combined()
        );
    } else {
        println!(
            "  ⚠ white image scores nsfw={:.4} — model may need review",
            scores.combined()
        );
    }
}

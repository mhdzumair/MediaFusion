/// NSFW classifier benchmark: load the ONNX model and classify a directory of
/// images, reporting wall time, CPU time (via `std::thread` clock), and
/// resident-set size before/after for each image.
///
/// Usage:
///   cargo run --bin nsfw-bench -- /path/to/images [--model /path/to/nsfw_model.onnx]
///
/// Example:
///   cargo run --release --bin nsfw-bench -- ../test_images
use std::path::PathBuf;
use std::time::Instant;

use mediafusion_api::nsfw::{read_rss_kb, NsfwClassifier};

struct Args {
    images_dir: PathBuf,
    model_path: PathBuf,
}

fn parse_args() -> Args {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let mut images_dir: Option<PathBuf> = None;
    let mut model_path = PathBuf::from(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../resources/nsfw_model.onnx"
    ));
    let mut i = 0;

    while i < raw.len() {
        match raw[i].as_str() {
            "--model" => {
                i += 1;
                if let Some(v) = raw.get(i) {
                    model_path = PathBuf::from(v);
                }
            }
            "--help" | "-h" => {
                eprintln!(
                    "usage: nsfw-bench <images-dir> [--model PATH]\n\n\
                     Benchmarks NSFW classification on all JPEG/PNG images in <images-dir>."
                );
                std::process::exit(0);
            }
            other => {
                if images_dir.is_none() {
                    images_dir = Some(PathBuf::from(other));
                } else {
                    eprintln!("unexpected argument '{other}'; try --help");
                    std::process::exit(1);
                }
            }
        }
        i += 1;
    }

    let images_dir = images_dir.unwrap_or_else(|| {
        eprintln!("error: <images-dir> argument is required");
        std::process::exit(1);
    });

    Args {
        images_dir,
        model_path,
    }
}

fn main() {
    let args = parse_args();

    // ── Load model ────────────────────────────────────────────────────────
    let model_str = args.model_path.to_string_lossy();
    println!("━━ nsfw-bench");
    println!("   model      → {model_str}");
    println!("   images dir → {}", args.images_dir.display());

    let rss_before_load = read_rss_kb();
    let load_start = Instant::now();
    let clf = NsfwClassifier::load(&model_str).unwrap_or_else(|| {
        eprintln!("  model not found or failed to load — aborting");
        std::process::exit(1);
    });
    let load_elapsed = load_start.elapsed();
    let rss_after_load = read_rss_kb();

    println!(
        "   model load: {:.1} ms  |  RSS delta: +{} kB",
        load_elapsed.as_secs_f64() * 1000.0,
        rss_after_load.saturating_sub(rss_before_load),
    );
    println!();

    // ── Enumerate images ──────────────────────────────────────────────────
    let mut entries: Vec<PathBuf> = std::fs::read_dir(&args.images_dir)
        .unwrap_or_else(|e| {
            eprintln!("cannot read directory {}: {e}", args.images_dir.display());
            std::process::exit(1);
        })
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            matches!(
                p.extension()
                    .and_then(|s| s.to_str())
                    .unwrap_or("")
                    .to_lowercase()
                    .as_str(),
                "jpg" | "jpeg" | "png" | "webp"
            )
        })
        .collect();

    entries.sort();

    if entries.is_empty() {
        eprintln!(
            "no JPEG/PNG/WebP images found in {}",
            args.images_dir.display()
        );
        std::process::exit(1);
    }

    println!(
        "{:<30}  {:>8}  {:>8}  {:>8}  {:>12}  {}",
        "file", "sfw", "nsfw", "wall_ms", "rss_delta_kB", "flag"
    );
    println!("{}", "─".repeat(90));

    let mut total_wall_ms = 0.0f64;
    let mut total_rss_delta: i64 = 0;
    let mut n_flagged = 0usize;
    let threshold = 0.7_f32;

    for path in &entries {
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("  skipping {}: {e}", path.display());
                continue;
            }
        };

        let rss_pre = read_rss_kb();
        let t0 = Instant::now();
        let cpu_t0 = cpu_time_ms();

        let result = clf.classify(&bytes);

        let wall_ms = t0.elapsed().as_secs_f64() * 1000.0;
        let _cpu_ms = cpu_time_ms() - cpu_t0;
        let rss_post = read_rss_kb();
        let rss_delta = rss_post as i64 - rss_pre as i64;

        let name = path.file_name().unwrap_or_default().to_string_lossy();

        match result {
            Ok(scores) => {
                let flag = if scores.combined() >= threshold {
                    "⚠ NSFW"
                } else {
                    "  safe"
                };
                if scores.combined() >= threshold {
                    n_flagged += 1;
                }
                println!(
                    "{:<30}  {:>8.4}  {:>8.4}  {:>8.1}  {:>12}  {}",
                    name, scores.sfw, scores.nsfw, wall_ms, rss_delta, flag
                );
            }
            Err(e) => {
                println!("{:<30}  ERROR: {e}", name);
            }
        }

        total_wall_ms += wall_ms;
        total_rss_delta += rss_delta;
    }

    let n = entries.len();
    println!("{}", "─".repeat(90));
    println!(
        "  {} images  |  total wall: {:.1} ms  |  avg per image: {:.1} ms  |  flagged: {}/{}",
        n,
        total_wall_ms,
        total_wall_ms / n as f64,
        n_flagged,
        n,
    );
    println!(
        "  RSS delta across all inferences: {:+} kB  (macOS: always 0 — use Instruments)",
        total_rss_delta,
    );
    println!(
        "  RSS note: /proc/self/status is Linux-only; on macOS use 'time' or Instruments for memory."
    );
}

/// Returns process CPU time in milliseconds on Linux via `clock_gettime(CLOCK_PROCESS_CPUTIME_ID)`.
/// Returns 0 on other platforms.
fn cpu_time_ms() -> f64 {
    #[cfg(target_os = "linux")]
    {
        use std::mem::MaybeUninit;
        extern "C" {
            fn clock_gettime(clk_id: libc_clockid_t, tp: *mut libc_timespec) -> i32;
        }
        type libc_clockid_t = i32;
        #[repr(C)]
        struct libc_timespec {
            tv_sec: i64,
            tv_nsec: i64,
        }
        const CLOCK_PROCESS_CPUTIME_ID: libc_clockid_t = 2;
        let mut ts = MaybeUninit::<libc_timespec>::zeroed();
        unsafe {
            clock_gettime(CLOCK_PROCESS_CPUTIME_ID, ts.as_mut_ptr());
            let ts = ts.assume_init();
            ts.tv_sec as f64 * 1000.0 + ts.tv_nsec as f64 / 1e6
        }
    }
    #[cfg(not(target_os = "linux"))]
    {
        0.0
    }
}

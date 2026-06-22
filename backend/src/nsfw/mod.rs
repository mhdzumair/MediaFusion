/// In-process NSFW poster classifier using ONNX Runtime via the `ort` crate.
///
/// # Model
/// Uses `AdamCodd/vit-base-nsfw-detector` (ViT-base fine-tuned for NSFW detection).
/// Download the pre-converted quantized ONNX with the `mediafusion-nsfw-setup` binary:
///
///   cargo run --bin mediafusion-nsfw-setup
///
/// The ONNX file is fully **cross-platform** — the same file runs on Apple Silicon,
/// Linux x86_64, Linux arm64, etc.  `ort` downloads the correct native ONNX Runtime
/// binary for the current platform at build time (via the `download-binaries` feature).
///
/// # Contract
///   Input  `pixel_values`:  [1, 3, 384, 384]  float32  NCHW
///                           values normalised as (pixel/255 − 0.5) / 0.5  → [−1, 1]
///   Output `logits`:        [1, 2]             float32  raw logits (softmax applied in Rust)
///                           index 0 = sfw logit → probability after softmax
///                           index 1 = nsfw logit → probability after softmax
use std::path::Path;
use std::sync::Mutex;

use image::DynamicImage;
use ndarray::Array4;
use ort::{inputs, session::Session, value::TensorRef};
use tracing::{info, warn};

use crate::jobs::error::JobError;

/// Download URL for the quantized ONNX model.
pub const MODEL_HF_URL: &str =
    "https://huggingface.co/AdamCodd/vit-base-nsfw-detector/resolve/main/onnx/model_quantized.onnx";

const INPUT_H: u32 = 384;
const INPUT_W: u32 = 384;

// ViT preprocessing: pixel/255, then normalise with mean=0.5 std=0.5
// → equivalent to pixel/127.5 − 1.0
const MEAN: f32 = 0.5;
const STD: f32 = 0.5;

/// Per-image classification result.
#[derive(Debug, Clone, Copy)]
pub struct NsfwScores {
    /// Probability the image is safe-for-work.
    pub sfw: f32,
    /// Probability the image is not-safe-for-work.
    pub nsfw: f32,
}

impl NsfwScores {
    /// The primary signal used for thresholding: `nsfw` probability.
    pub fn combined(self) -> f32 {
        self.nsfw
    }
}

/// Thread-safe NSFW image classifier wrapping an ONNX Runtime session.
///
/// `Session::run` requires `&mut self`, so the session is held behind a `Mutex`.
/// CPU inference on a 384×384 image takes ~20–80 ms; call via `spawn_blocking`.
pub struct NsfwClassifier {
    session: Mutex<Session>,
}

// SAFETY: Session holds a raw pointer to OrtSession (C++).
// The Mutex ensures single-threaded access at a time.
unsafe impl Send for NsfwClassifier {}
unsafe impl Sync for NsfwClassifier {}

impl NsfwClassifier {
    /// Load the classifier from an ONNX file.
    /// Returns `None` (not an error) when the file does not exist — the binary
    /// boots normally and the scan job skips with an info log.
    pub fn load(path: &str) -> Option<Self> {
        if !Path::new(path).exists() {
            info!(
                path,
                "NSFW model not found — classifier disabled. \
                 Run `cargo run --bin mediafusion-nsfw-setup` to download it."
            );
            return None;
        }
        match Session::builder().and_then(|mut b| b.commit_from_file(path)) {
            Ok(session) => {
                info!(path, "NSFW classifier loaded");
                Some(Self {
                    session: Mutex::new(session),
                })
            }
            Err(e) => {
                warn!(path, "failed to load NSFW model: {e}");
                None
            }
        }
    }

    /// Classify raw image bytes (JPEG / PNG / …).
    ///
    /// Returns `NsfwScores` with `.combined()` = nsfw probability ∈ [0, 1].
    /// **CPU-bound** — call from `tokio::task::spawn_blocking`.
    pub fn classify(&self, image_bytes: &[u8]) -> Result<NsfwScores, JobError> {
        let img = image::load_from_memory(image_bytes)
            .map_err(|e| JobError::Other(format!("image decode: {e}")))?;

        let arr = preprocess(&img);

        let tensor = TensorRef::from_array_view(arr.view())
            .map_err(|e| JobError::Other(format!("ort tensor: {e}")))?;

        let mut session = self
            .session
            .lock()
            .map_err(|_| JobError::Other("session mutex poisoned".into()))?;

        let outputs = session
            .run(inputs![tensor])
            .map_err(|e| JobError::Other(format!("ort run: {e}")))?;

        let view = outputs[0]
            .try_extract_array::<f32>()
            .map_err(|e| JobError::Other(format!("ort extract: {e}")))?;

        let logits: Vec<f32> = view.iter().cloned().collect();

        if logits.len() < 2 {
            return Err(JobError::Other(format!(
                "unexpected output length {} — expected ≥2 (sfw, nsfw)",
                logits.len()
            )));
        }

        // AdamCodd ViT outputs raw logits; apply softmax to get probabilities.
        let max = logits[0].max(logits[1]);
        let e0 = (logits[0] - max).exp();
        let e1 = (logits[1] - max).exp();
        let sum = e0 + e1;

        Ok(NsfwScores {
            sfw: e0 / sum,
            nsfw: e1 / sum,
        })
    }
}

/// Resize to 384×384 and apply ViT normalisation → NCHW float32 tensor.
fn preprocess(img: &DynamicImage) -> Array4<f32> {
    use image::GenericImageView;

    let resized = img.resize_exact(INPUT_W, INPUT_H, image::imageops::FilterType::Triangle);

    let mut arr = Array4::<f32>::zeros([1, 3, INPUT_H as usize, INPUT_W as usize]);

    for (x, y, px) in resized.pixels() {
        arr[[0, 0, y as usize, x as usize]] = (px[0] as f32 / 255.0 - MEAN) / STD;
        arr[[0, 1, y as usize, x as usize]] = (px[1] as f32 / 255.0 - MEAN) / STD;
        arr[[0, 2, y as usize, x as usize]] = (px[2] as f32 / 255.0 - MEAN) / STD;
    }

    arr
}

/// Read process RSS from `/proc/self/status` (Linux only). Returns 0 elsewhere.
pub fn read_rss_kb() -> u64 {
    #[cfg(target_os = "linux")]
    {
        if let Ok(s) = std::fs::read_to_string("/proc/self/status") {
            for line in s.lines() {
                if let Some(rest) = line.strip_prefix("VmRSS:") {
                    if let Ok(kb) = rest.trim().trim_end_matches(" kB").trim().parse::<u64>() {
                        return kb;
                    }
                }
            }
        }
    }
    0
}

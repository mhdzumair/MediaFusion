/// In-process NSFW poster classifier using ONNX Runtime via the `ort` crate.
///
/// Gated behind the `nsfw-classifier` Cargo feature (enabled by default).
/// On targets where ort does not provide prebuilt binaries (Windows GNU,
/// x86_64-apple-darwin, musl) the feature is disabled and `NsfwClassifier::load`
/// always returns `None` — the rest of the codebase is unchanged.
///
/// Supported targets with prebuilt ort binaries:
///   aarch64-apple-darwin, x86_64-unknown-linux-gnu, aarch64-unknown-linux-gnu
pub const MODEL_HF_URL: &str =
    "https://huggingface.co/AdamCodd/vit-base-nsfw-detector/resolve/main/onnx/model_quantized.onnx";

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

// ─── Per-image result (always compiled) ──────────────────────────────────────

/// Per-image classification result.
#[derive(Debug, Clone, Copy)]
pub struct NsfwScores {
    pub sfw: f32,
    pub nsfw: f32,
}

impl NsfwScores {
    pub fn combined(self) -> f32 {
        self.nsfw
    }
}

// ─── Classifier implementation (nsfw-classifier feature) ─────────────────────

#[cfg(feature = "nsfw-classifier")]
mod inner {
    use std::path::Path;
    use std::sync::Mutex;

    use image::DynamicImage;
    use ndarray::Array4;
    use ort::{inputs, session::Session, value::TensorRef};
    use tracing::{info, warn};

    use crate::jobs::error::JobError;

    const INPUT_H: u32 = 384;
    const INPUT_W: u32 = 384;
    const MEAN: f32 = 0.5;
    const STD: f32 = 0.5;

    /// Thread-safe NSFW image classifier wrapping an ONNX Runtime session.
    pub struct NsfwClassifier {
        session: Mutex<Session>,
    }

    // SAFETY: Session holds a raw pointer to OrtSession (C++).
    // The Mutex ensures single-threaded access at a time.
    unsafe impl Send for NsfwClassifier {}
    unsafe impl Sync for NsfwClassifier {}

    impl NsfwClassifier {
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

        pub fn classify(&self, image_bytes: &[u8]) -> Result<super::NsfwScores, JobError> {
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

            let max = logits[0].max(logits[1]);
            let e0 = (logits[0] - max).exp();
            let e1 = (logits[1] - max).exp();
            let sum = e0 + e1;

            Ok(super::NsfwScores {
                sfw: e0 / sum,
                nsfw: e1 / sum,
            })
        }
    }

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
}

#[cfg(feature = "nsfw-classifier")]
pub use inner::NsfwClassifier;

// ─── Stub (nsfw-classifier feature disabled) ──────────────────────────────────

#[cfg(not(feature = "nsfw-classifier"))]
pub struct NsfwClassifier;

#[cfg(not(feature = "nsfw-classifier"))]
impl NsfwClassifier {
    pub fn load(_path: &str) -> Option<Self> {
        tracing::info!("NSFW classifier disabled at compile time (nsfw-classifier feature off)");
        None
    }

    pub fn classify(
        &self,
        _image_bytes: &[u8],
    ) -> Result<NsfwScores, crate::jobs::error::JobError> {
        unreachable!("classify called on stub NsfwClassifier — load() always returns None")
    }
}

use flate2::{read::ZlibDecoder, write::ZlibEncoder, Compression};
use serde_json::Value;
use std::io::{Read, Write};
use tracing::warn;

/// Python-compatible blob magic prefix.
/// Python writes: magic + zlib(json). Rust must read/write the same format.
const MAGIC: &[u8] = b"\x01MFsc1";

pub fn decode_blob(blob: &[u8]) -> Option<Value> {
    let data: Vec<u8> = if blob.starts_with(MAGIC) {
        let compressed = &blob[MAGIC.len()..];
        let mut dec = ZlibDecoder::new(compressed);
        let mut raw = Vec::new();
        dec.read_to_end(&mut raw).ok()?;
        raw
    } else if !blob.is_empty() && (blob[0] == 0x78 || blob[0] == 0x9c || blob[0] == 0xda) {
        // Legacy plain zlib blobs (no magic prefix)
        let mut dec = ZlibDecoder::new(blob);
        let mut raw = Vec::new();
        dec.read_to_end(&mut raw).ok()?;
        raw
    } else {
        blob.to_vec()
    };

    match serde_json::from_slice::<Value>(&data) {
        Ok(v) => Some(v),
        Err(e) => {
            warn!("blob JSON parse: {e}");
            None
        }
    }
}

pub fn encode_blob(data: &Value) -> Option<Vec<u8>> {
    let json_bytes = serde_json::to_vec(data).ok()?;
    let mut enc = ZlibEncoder::new(Vec::new(), Compression::fast());
    enc.write_all(&json_bytes).ok()?;
    let compressed = enc.finish().ok()?;
    let mut blob = Vec::with_capacity(MAGIC.len() + compressed.len());
    blob.extend_from_slice(MAGIC);
    blob.extend_from_slice(&compressed);
    Some(blob)
}

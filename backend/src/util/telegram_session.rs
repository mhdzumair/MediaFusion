//! Grammers session loading and Telethon StringSession conversion.

use std::net::{Ipv4Addr, SocketAddrV4};

use base64::{engine::general_purpose::STANDARD as BASE64, Engine};
use grammers_session::{storages::MemorySession, SessionData};

/// Parse session blob into SessionData (Telethon StringSession format).
pub fn parse_session_data(session_b64: &str) -> Result<SessionData, String> {
    let trimmed = session_b64.trim();
    if trimmed.is_empty() {
        return Err("empty session".into());
    }

    if trimmed.starts_with('1') {
        return extract_data_from_telethon(trimmed);
    }

    if let Ok(bytes) = BASE64.decode(trimmed) {
        if let Ok(text) = std::str::from_utf8(&bytes) {
            if text.starts_with('1') {
                return extract_data_from_telethon(text);
            }
        }
    }

    Err(
        "Could not parse TELEGRAM_GRAMMERS_SESSION. Provide a Telethon StringSession \
         (starts with '1') or run: cargo run --bin telegram_session -- --convert-telethon \"...\""
            .into(),
    )
}

/// Load a grammers `MemorySession` from session env value.
pub fn load_memory_session(session_b64: &str) -> Result<MemorySession, String> {
    Ok(MemorySession::from(parse_session_data(session_b64)?))
}

/// Returns true when the session has at least one datacenter auth key.
pub fn session_is_authenticated(data: &SessionData) -> bool {
    data.dc_options.values().any(|dc| dc.auth_key.is_some())
}

fn extract_data_from_telethon(session_string: &str) -> Result<SessionData, String> {
    use base64::engine::general_purpose::URL_SAFE as B64_URL;

    if !session_string.starts_with('1') {
        return Err("not a Telethon StringSession".into());
    }
    let encoded = &session_string[1..];
    let bytes = B64_URL
        .decode(encoded)
        .or_else(|_| BASE64.decode(encoded))
        .map_err(|e| format!("Telethon session base64 decode failed: {e}"))?;

    if bytes.len() < 263 {
        return Err(format!(
            "Telethon session payload too short ({} bytes)",
            bytes.len()
        ));
    }

    let dc_id = bytes[0] as i32;
    let ip = Ipv4Addr::new(bytes[1], bytes[2], bytes[3], bytes[4]);
    let port = u16::from_be_bytes([bytes[5], bytes[6]]);
    let mut auth_key = [0u8; 256];
    auth_key.copy_from_slice(&bytes[7..263]);

    let mut data = SessionData::default();
    data.home_dc = dc_id;
    if let Some(opt) = data.dc_options.get_mut(&dc_id) {
        opt.ipv4 = SocketAddrV4::new(ip, port);
        opt.auth_key = Some(auth_key);
    }
    Ok(data)
}

/// Convert an existing Telethon StringSession to value for `TELEGRAM_GRAMMERS_SESSION`.
/// Accepts either raw StringSession or base64-wrapped copy.
pub fn convert_telethon_string(telethon_session: &str) -> Result<String, String> {
    let trimmed = telethon_session.trim();
    if trimmed.starts_with('1') {
        return Ok(trimmed.to_string());
    }
    if let Ok(bytes) = BASE64.decode(trimmed) {
        if let Ok(text) = String::from_utf8(bytes) {
            if text.starts_with('1') {
                return Ok(text);
            }
        }
    }
    Err("input is not a Telethon StringSession (must start with '1')".into())
}

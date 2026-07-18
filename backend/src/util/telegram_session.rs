//! Grammers session loading and Telethon / Pyrogram StringSession conversion.

use std::net::{Ipv4Addr, SocketAddrV4};

use base64::{Engine, engine::general_purpose::STANDARD as BASE64};
use grammers_session::{SessionData, storages::MemorySession};

const PYROGRAM_PACK_LEN_V1: usize = 1 + 1 + 256 + 8 + 1; // dc, test_mode, auth_key, user_id, is_bot
const PYROGRAM_PACK_LEN_V2: usize = 1 + 4 + 1 + 256 + 8 + 1; // + api_id

fn decode_base64_payload(input: &str) -> Result<Vec<u8>, String> {
    use base64::engine::general_purpose::URL_SAFE as B64_URL;

    let mut padded = input.trim().to_string();
    let rem = padded.len() % 4;
    if rem != 0 {
        padded.extend(std::iter::repeat_n('=', 4 - rem));
    }

    B64_URL
        .decode(&padded)
        .or_else(|_| BASE64.decode(&padded))
        .map_err(|e| format!("session base64 decode failed: {e}"))
}

/// Reject bot-account sessions before attempting MTProto connection.
pub fn validate_user_session(session: &str) -> Result<(), String> {
    let trimmed = session.trim();
    if trimmed.starts_with('1') {
        return Ok(());
    }
    if let Ok((_, meta)) = extract_data_from_pyrogram(trimmed) {
        if meta.is_bot {
            return Err(
                "session is a Pyrogram bot session; channel scraping requires a user account \
                 session created with your phone number"
                    .into(),
            );
        }
    }
    Ok(())
}

/// Parse session blob into SessionData (Telethon or Pyrogram StringSession format).
pub fn parse_session_data(session_b64: &str) -> Result<SessionData, String> {
    let trimmed = session_b64.trim();
    if trimmed.is_empty() {
        return Err("empty session".into());
    }

    if trimmed.starts_with('1') {
        return extract_data_from_telethon(trimmed);
    }

    if let Ok(bytes) = BASE64.decode(trimmed)
        && let Ok(text) = std::str::from_utf8(&bytes)
        && text.starts_with('1')
    {
        return extract_data_from_telethon(text);
    }

    if let Ok((data, _)) = extract_data_from_pyrogram(trimmed) {
        return Ok(data);
    }

    Err(
        "Could not parse Telegram session string. Provide a Telethon StringSession \
         (starts with '1') or a Pyrogram StringSession (base64)."
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
    if !session_string.starts_with('1') {
        return Err("not a Telethon StringSession".into());
    }
    let encoded = &session_string[1..];
    let bytes = decode_base64_payload(encoded)?;

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

    let mut data = SessionData {
        home_dc: dc_id,
        ..Default::default()
    };
    if let Some(opt) = data.dc_options.get_mut(&dc_id) {
        opt.ipv4 = SocketAddrV4::new(ip, port);
        opt.auth_key = Some(auth_key);
    }
    Ok(data)
}

/// Parse a Pyrogram StringSession (URL-safe or standard base64, 267 or 271 bytes).
fn extract_data_from_pyrogram(session_string: &str) -> Result<(SessionData, PyrogramMeta), String> {
    let bytes = decode_base64_payload(session_string)?;

    let (dc_id, auth_key, meta) = match bytes.len() {
        PYROGRAM_PACK_LEN_V1 => parse_pyrogram_v1(&bytes)?,
        PYROGRAM_PACK_LEN_V2 => parse_pyrogram_v2(&bytes)?,
        len => {
            return Err(format!(
                "Pyrogram session payload has unexpected length ({len} bytes; expected \
                 {PYROGRAM_PACK_LEN_V1} or {PYROGRAM_PACK_LEN_V2})"
            ));
        }
    };

    let mut data = SessionData {
        home_dc: dc_id,
        ..Default::default()
    };
    if let Some(opt) = data.dc_options.get_mut(&dc_id) {
        opt.auth_key = Some(auth_key);
    } else {
        return Err(format!("Pyrogram session references unknown DC {dc_id}"));
    }

    Ok((data, meta))
}

struct PyrogramMeta {
    is_bot: bool,
}

fn parse_pyrogram_v1(bytes: &[u8]) -> Result<(i32, [u8; 256], PyrogramMeta), String> {
    let dc_id = bytes[0] as i32;
    let mut auth_key = [0u8; 256];
    auth_key.copy_from_slice(&bytes[2..258]);
    let is_bot = bytes[266] != 0;
    Ok((dc_id, auth_key, PyrogramMeta { is_bot }))
}

fn parse_pyrogram_v2(bytes: &[u8]) -> Result<(i32, [u8; 256], PyrogramMeta), String> {
    let dc_id = bytes[0] as i32;
    let mut auth_key = [0u8; 256];
    auth_key.copy_from_slice(&bytes[6..262]);
    let is_bot = bytes[270] != 0;
    Ok((dc_id, auth_key, PyrogramMeta { is_bot }))
}

/// Export authenticated session data as a Telethon StringSession value.
pub fn export_telethon_string(data: &SessionData) -> Result<String, String> {
    use base64::engine::general_purpose::URL_SAFE as B64_URL;

    let dc_id = data.home_dc;
    let dc = data
        .dc_options
        .get(&dc_id)
        .ok_or_else(|| format!("session missing DC {dc_id} options"))?;
    let auth_key = dc
        .auth_key
        .ok_or_else(|| "session is not authenticated".to_string())?;

    let ip = dc.ipv4.ip().octets();
    let port = dc.ipv4.port();
    let mut payload = Vec::with_capacity(263);
    payload.push(u8::try_from(dc_id).map_err(|_| format!("invalid DC id {dc_id}"))?);
    payload.extend_from_slice(&ip);
    payload.extend_from_slice(&port.to_be_bytes());
    payload.extend_from_slice(&auth_key);

    Ok(format!("1{}", B64_URL.encode(payload)))
}

/// Convert Telethon or Pyrogram StringSession to a Telethon export string.
pub fn convert_session_string(session: &str) -> Result<String, String> {
    let trimmed = session.trim();
    if trimmed.starts_with('1') {
        // Validate by parsing, then return as-is.
        extract_data_from_telethon(trimmed)?;
        return Ok(trimmed.to_string());
    }

    if let Ok(bytes) = BASE64.decode(trimmed)
        && let Ok(text) = String::from_utf8(bytes)
        && text.starts_with('1')
    {
        extract_data_from_telethon(&text)?;
        return Ok(text);
    }

    let (data, meta) = extract_data_from_pyrogram(trimmed)?;
    if meta.is_bot {
        return Err(
            "Pyrogram session is a bot session; channel scraping requires a user account \
             session created with your phone number (not a bot token)"
                .into(),
        );
    }
    export_telethon_string(&data)
}

/// Convert an existing Telethon StringSession export string.
/// Accepts either raw StringSession or base64-wrapped copy.
pub fn convert_telethon_string(telethon_session: &str) -> Result<String, String> {
    convert_session_string(telethon_session)
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::engine::general_purpose::URL_SAFE as B64_URL;

    fn sample_auth_key() -> [u8; 256] {
        let mut key = [0u8; 256];
        for (i, b) in key.iter_mut().enumerate() {
            *b = i as u8;
        }
        key
    }

    #[test]
    fn telethon_roundtrip() {
        let auth_key = sample_auth_key();
        let ip = Ipv4Addr::new(91, 108, 56, 104);
        let port = 443u16;
        let mut payload = Vec::with_capacity(263);
        payload.push(5);
        payload.extend_from_slice(&ip.octets());
        payload.extend_from_slice(&port.to_be_bytes());
        payload.extend_from_slice(&auth_key);
        let session = format!("1{}", B64_URL.encode(payload));

        let data = extract_data_from_telethon(&session).expect("telethon parse");
        assert_eq!(data.home_dc, 5);
        assert!(session_is_authenticated(&data));

        let exported = export_telethon_string(&data).expect("export");
        assert_eq!(exported, session);
    }

    #[test]
    fn pyrogram_v2_parses_and_exports() {
        let auth_key = sample_auth_key();
        let mut bytes = vec![0u8; PYROGRAM_PACK_LEN_V2];
        bytes[0] = 5;
        bytes[1..5].copy_from_slice(&39549894i32.to_be_bytes());
        bytes[5] = 0;
        bytes[6..262].copy_from_slice(&auth_key);
        bytes[262..270].copy_from_slice(&1234567890u64.to_be_bytes());
        bytes[270] = 0;
        let session = B64_URL.encode(&bytes);

        let (data, meta) = extract_data_from_pyrogram(&session).expect("pyrogram parse");
        assert!(!meta.is_bot);
        assert_eq!(data.home_dc, 5);
        assert!(session_is_authenticated(&data));

        let exported = convert_session_string(&session).expect("convert");
        assert!(exported.starts_with('1'));
        assert!(parse_session_data(&session).is_ok());
        assert!(parse_session_data(&exported).is_ok());
    }

    #[test]
    fn pyrogram_bot_session_is_rejected_for_conversion() {
        let auth_key = sample_auth_key();
        let mut bytes = vec![0u8; PYROGRAM_PACK_LEN_V2];
        bytes[0] = 5;
        bytes[6..262].copy_from_slice(&auth_key);
        bytes[270] = 1;
        let session = B64_URL.encode(&bytes);

        let err = convert_session_string(&session).expect_err("bot session");
        assert!(err.contains("bot session"));
    }
}

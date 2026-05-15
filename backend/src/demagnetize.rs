//! Magnet-to-torrent metadata resolver.
//!
//! Discovers peers via BitTorrent Mainline DHT then fetches the info
//! dictionary from the first responding peer using BEP-9 (ut_metadata
//! extension, BEP-10 Extension Protocol).
//!
//! No file download is performed — only the metadata (name + file list).

use std::collections::HashMap;
use std::net::SocketAddr;
use std::time::Duration;

use futures::stream;
use rand::RngExt;
use sha1::{Digest, Sha1};
use thiserror::Error;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
};

// ─── Public types ─────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum Error {
    #[error("invalid info_hash '{0}' (expected 40 lowercase hex chars)")]
    BadInfoHash(String),
    #[error("timed out — no peers responded within the deadline")]
    Timeout,
    #[error("no peers found or none responded with valid metadata")]
    NoPeers,
    #[error("info-hash SHA-1 mismatch after metadata assembly")]
    HashMismatch,
    #[error("protocol error: {0}")]
    Protocol(String),
}

pub struct TorrentMeta {
    pub name: String,
    pub files: Vec<FileInfo>,
    pub total_size: i64,
}

pub struct FileInfo {
    pub path: String,
    pub size: i64,
}

// ─── Public entry point ───────────────────────────────────────────────────────

/// Resolve a magnet `info_hash_hex` to torrent metadata (name + file list).
///
/// Peers are discovered via BitTorrent Mainline DHT.  The first peer that
/// delivers a valid info dictionary wins.
pub async fn resolve(info_hash_hex: &str, overall_timeout: Duration) -> Result<TorrentMeta, Error> {
    let hash = parse_hex20(info_hash_hex)?;

    tokio::time::timeout(overall_timeout, resolve_inner(hash, overall_timeout))
        .await
        .map_err(|_| Error::Timeout)?
}

async fn resolve_inner(hash: [u8; 20], budget: Duration) -> Result<TorrentMeta, Error> {
    // First half of budget: DHT peer discovery.
    let peers = find_peers(hash, budget / 2).await;
    if peers.is_empty() {
        return Err(Error::NoPeers);
    }
    tracing::debug!("demagnetize: {} peers for {}", peers.len(), fmt_hex(&hash));

    // Second half: race peers to fetch the info dict via BEP-9.
    let per_peer = Duration::from_secs(12);
    tokio::time::timeout(budget / 2, fetch_metadata_from_peers(peers, hash, per_peer))
        .await
        .map_err(|_| Error::Timeout)?
        .ok_or(Error::NoPeers)
        .and_then(|raw| {
            // Verify SHA-1 of assembled info bytes matches the info_hash.
            let actual: [u8; 20] = Sha1::digest(&raw).into();
            if actual != hash {
                return Err(Error::HashMismatch);
            }
            parse_info_dict(&raw).map_err(Error::Protocol)
        })
}

// ─── DHT peer discovery ───────────────────────────────────────────────────────

async fn find_peers(hash: [u8; 20], timeout: Duration) -> Vec<SocketAddr> {
    match tokio::time::timeout(
        timeout,
        tokio::task::spawn_blocking(move || dht_get_peers(hash)),
    )
    .await
    {
        Ok(Ok(peers)) => peers,
        Ok(Err(e)) => {
            tracing::warn!("demagnetize: spawn_blocking error: {e}");
            vec![]
        }
        Err(_) => {
            tracing::debug!("demagnetize: DHT discovery timed out");
            vec![]
        }
    }
}

/// Blocking DHT lookup.  Runs on the blocking thread pool so it does not
/// stall the async executor.
fn dht_get_peers(hash: [u8; 20]) -> Vec<SocketAddr> {
    let dht = match mainline::Dht::client() {
        Ok(d) => d,
        Err(e) => {
            tracing::warn!("demagnetize: DHT init failed: {e}");
            return vec![];
        }
    };
    let id = mainline::Id::from(hash);
    let mut peers: Vec<SocketAddr> = Vec::new();

    for batch in dht.get_peers(id) {
        for addr in batch {
            peers.push(SocketAddr::V4(addr));
        }
        if peers.len() >= 40 {
            break;
        }
    }
    peers
}

// ─── Concurrent BEP-9 fetch (first-wins race) ────────────────────────────────

async fn fetch_metadata_from_peers(
    peers: Vec<SocketAddr>,
    hash: [u8; 20],
    per_peer: Duration,
) -> Option<Vec<u8>> {
    let peer_id = random_peer_id();

    let mut tasks = stream::iter(peers)
        .map(|addr| {
            let pid = peer_id;
            async move {
                tokio::time::timeout(per_peer, fetch_from_peer(addr, hash, pid))
                    .await
                    .ok() // None on per-peer timeout
                    .and_then(|r| r.ok()) // None on protocol error
            }
        })
        .buffer_unordered(8); // try up to 8 peers concurrently

    // Drive the stream manually so we can return the first success.
    use futures::StreamExt as _;
    while let Some(opt) = tasks.next().await {
        if let Some(raw) = opt {
            return Some(raw);
        }
    }
    None
}

// ─── BEP-9 / BEP-10 per-peer metadata fetch ──────────────────────────────────

const PIECE_SIZE: usize = 16 * 1024; // 16 KiB per BEP-9 spec

async fn fetch_from_peer(
    addr: SocketAddr,
    hash: [u8; 20],
    peer_id: [u8; 20],
) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    let mut stream = TcpStream::connect(addr).await?;
    stream.set_nodelay(true)?;

    // ── Step 1: BitTorrent handshake ─────────────────────────────────────────
    stream.write_all(&build_handshake(&hash, &peer_id)).await?;

    let mut phs = [0u8; 68];
    stream.read_exact(&mut phs).await?;

    if phs[28..48] != hash[..] {
        return Err("info_hash mismatch in peer handshake".into());
    }
    // Byte 25 = reserved[5]; bit 4 = BEP-10 extension protocol
    if phs[25] & 0x10 == 0 {
        return Err("peer does not support BEP-10 extension protocol".into());
    }

    // ── Step 2: Send our BEP-10 extension handshake ──────────────────────────
    // Advertise ut_metadata with local extension ID = 1
    // Bencode: {"m": {"ut_metadata": 1}}  →  d1:md11:ut_metadatai1eee
    send_ext_msg(&mut stream, 0, b"d1:md11:ut_metadatai1eee").await?;

    // ── Step 3: Read messages until we see the peer's extension handshake ────
    let (peer_ut_id, metadata_size) = {
        let mut found = None;
        for _ in 0..25 {
            let (msg_id, payload) = read_msg(&mut stream).await?;
            // Extension handshake: msg_id=20, ext_id=0
            if msg_id == 20 && payload.first() == Some(&0) {
                if let Some(info) = parse_ext_handshake(&payload[1..]) {
                    found = Some(info);
                    break;
                }
            }
        }
        found.ok_or("no extension handshake received")?
    };

    if metadata_size == 0 || metadata_size > 16 * 1024 * 1024 {
        return Err(format!("implausible metadata_size={metadata_size}").into());
    }

    // ── Step 4: Request every metadata piece ─────────────────────────────────
    let num_pieces = metadata_size.div_ceil(PIECE_SIZE);
    for i in 0..num_pieces {
        let req = format!("d8:msg_typei0e5:piecei{i}ee");
        send_ext_msg(&mut stream, peer_ut_id, req.as_bytes()).await?;
    }

    // ── Step 5: Collect piece responses ──────────────────────────────────────
    let mut pieces: HashMap<usize, Vec<u8>> = HashMap::new();

    // Allow up to (num_pieces × 3 + 30) messages to account for intervening
    // keep-alives and non-extension messages.
    for _ in 0..(num_pieces * 3 + 30) {
        let (msg_id, payload) = read_msg(&mut stream).await?;
        if msg_id != 20 || payload.is_empty() {
            continue;
        }
        // payload[0] = ext_id that the peer used when sending back to us
        let data = &payload[1..];

        match parse_ut_metadata_response(data) {
            Some(UtResponse::Data { piece, data_offset }) => {
                if piece < num_pieces {
                    pieces
                        .entry(piece)
                        .or_insert_with(|| data[data_offset..].to_vec());
                }
                if pieces.len() == num_pieces {
                    break;
                }
            }
            Some(UtResponse::Reject) => {
                return Err("peer rejected ut_metadata request".into());
            }
            None => {}
        }
    }

    if pieces.len() != num_pieces {
        return Err(format!("incomplete metadata: {}/{num_pieces} pieces", pieces.len()).into());
    }

    // ── Step 6: Assemble and return ───────────────────────────────────────────
    let mut raw = Vec::with_capacity(metadata_size);
    for i in 0..num_pieces {
        raw.extend_from_slice(pieces.get(&i).expect("already checked all pieces present"));
    }
    raw.truncate(metadata_size);
    Ok(raw)
}

// ─── Wire-format helpers ──────────────────────────────────────────────────────

/// 68-byte BitTorrent handshake with BEP-10 extension bit set in reserved[5].
fn build_handshake(hash: &[u8; 20], peer_id: &[u8; 20]) -> [u8; 68] {
    let mut buf = [0u8; 68];
    buf[0] = 19; // pstrlen
    buf[1..20].copy_from_slice(b"BitTorrent protocol");
    // reserved bytes: indices 20–27.  Byte 25 (= reserved[5]) bit 4 = BEP-10.
    buf[25] = 0x10;
    buf[28..48].copy_from_slice(hash);
    buf[48..68].copy_from_slice(peer_id);
    buf
}

fn random_peer_id() -> [u8; 20] {
    let mut id = [0u8; 20];
    id[..8].copy_from_slice(b"-MF0001-"); // MediaFusion client prefix
    rand::rng().fill(&mut id[8..]);
    id
}

/// Send a BEP-10 extended message: `[len:4][0x14][ext_id][payload]`.
async fn send_ext_msg(
    stream: &mut TcpStream,
    ext_id: u8,
    payload: &[u8],
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let len = (2 + payload.len()) as u32;
    let mut buf = Vec::with_capacity(6 + payload.len());
    buf.extend_from_slice(&len.to_be_bytes());
    buf.push(20u8); // msg_id: extended
    buf.push(ext_id);
    buf.extend_from_slice(payload);
    stream.write_all(&buf).await?;
    Ok(())
}

/// Read one BitTorrent message: `[len:4][msg_id:1][payload...]`.
/// Keep-alives (len=0) are silently skipped.
async fn read_msg(
    stream: &mut TcpStream,
) -> Result<(u8, Vec<u8>), Box<dyn std::error::Error + Send + Sync>> {
    loop {
        let mut lbuf = [0u8; 4];
        stream.read_exact(&mut lbuf).await?;
        let len = u32::from_be_bytes(lbuf) as usize;
        if len == 0 {
            continue; // keep-alive
        }
        if len > 8 * 1024 * 1024 {
            return Err(format!("message too large: {len} bytes").into());
        }
        let mut payload = vec![0u8; len];
        stream.read_exact(&mut payload).await?;
        let msg_id = payload[0];
        return Ok((msg_id, payload[1..].to_vec()));
    }
}

// ─── BEP-10 extension handshake parser ───────────────────────────────────────

/// Extract `(peer_ut_metadata_ext_id, metadata_size)` from the BEP-10
/// extension handshake payload (byte after the `0x00` ext-id byte).
fn parse_ext_handshake(data: &[u8]) -> Option<(u8, usize)> {
    let (dict, _) = decode_bencode_dict(data)?;
    let m = match dict.get("m")? {
        BVal::Dict(d) => d,
        _ => return None,
    };
    let ut_id = match m.get("ut_metadata")? {
        BVal::Int(n) => *n as u8,
        _ => return None,
    };
    let size = match dict.get("metadata_size")? {
        BVal::Int(n) => *n as usize,
        _ => return None,
    };
    Some((ut_id, size))
}

// ─── BEP-9 ut_metadata response parser ───────────────────────────────────────

enum UtResponse {
    /// `msg_type=1`: data follows the bencode dict at `data_offset` bytes in.
    Data { piece: usize, data_offset: usize },
    /// `msg_type=2`: peer rejected the request.
    Reject,
}

/// Parse a `ut_metadata` payload.  The format is:
/// `d8:msg_typei{T}e5:piecei{N}e...e<raw_piece_bytes>`
/// Returns where the raw bytes start (`data_offset`) so the caller can split.
fn parse_ut_metadata_response(data: &[u8]) -> Option<UtResponse> {
    let (dict, dict_end) = decode_bencode_dict(data)?;
    let msg_type = match dict.get("msg_type")? {
        BVal::Int(n) => *n,
        _ => return None,
    };
    match msg_type {
        1 => {
            let piece = match dict.get("piece")? {
                BVal::Int(n) => *n as usize,
                _ => return None,
            };
            Some(UtResponse::Data {
                piece,
                data_offset: dict_end,
            })
        }
        2 => Some(UtResponse::Reject),
        _ => None,
    }
}

// ─── Minimal bencode parser ───────────────────────────────────────────────────

/// Internal bencode value type — only what BEP-9/10 messages require.
enum BVal {
    Int(i64),
    Bytes(Vec<u8>),
    Dict(HashMap<String, BVal>),
    List(Vec<BVal>),
}

/// Parse a bencode dict starting at `data[0]` and return `(dict, bytes_consumed)`.
fn decode_bencode_dict(data: &[u8]) -> Option<(HashMap<String, BVal>, usize)> {
    match decode_bval(data, 0)? {
        (BVal::Dict(d), end) => Some((d, end)),
        _ => None,
    }
}

fn decode_bval(data: &[u8], pos: usize) -> Option<(BVal, usize)> {
    if pos >= data.len() {
        return None;
    }
    match data[pos] {
        b'i' => {
            let rel = data[pos + 1..].iter().position(|&b| b == b'e')?;
            let end = pos + 1 + rel;
            let n: i64 = std::str::from_utf8(&data[pos + 1..end])
                .ok()?
                .parse()
                .ok()?;
            Some((BVal::Int(n), end + 1))
        }
        b'l' => {
            let mut cur = pos + 1;
            let mut list = Vec::new();
            while cur < data.len() && data[cur] != b'e' {
                let (v, next) = decode_bval(data, cur)?;
                list.push(v);
                cur = next;
            }
            Some((BVal::List(list), cur + 1))
        }
        b'd' => {
            let mut cur = pos + 1;
            let mut dict = HashMap::new();
            while cur < data.len() && data[cur] != b'e' {
                let (kv, nk) = decode_bval(data, cur)?;
                let key = match kv {
                    BVal::Bytes(b) => String::from_utf8(b).ok()?,
                    _ => return None,
                };
                let (val, nv) = decode_bval(data, nk)?;
                dict.insert(key, val);
                cur = nv;
            }
            Some((BVal::Dict(dict), cur + 1))
        }
        b'0'..=b'9' => {
            let rel = data[pos..].iter().position(|&b| b == b':')?;
            let colon = pos + rel;
            let len: usize = std::str::from_utf8(&data[pos..colon]).ok()?.parse().ok()?;
            let start = colon + 1;
            let end = start + len;
            (end <= data.len()).then(|| (BVal::Bytes(data[start..end].to_vec()), end))
        }
        _ => None,
    }
}

// ─── Info-dict → TorrentMeta ──────────────────────────────────────────────────

/// Parse a raw bencoded info dictionary into a `TorrentMeta`.
fn parse_info_dict(raw: &[u8]) -> Result<TorrentMeta, String> {
    let (dict, _) =
        decode_bencode_dict(raw).ok_or_else(|| "could not parse bencode info dict".to_string())?;

    let name = get_str(&dict, "name").unwrap_or_else(|| "Unknown".to_string());

    // Multi-file torrent: has a "files" list.
    if let Some(BVal::List(file_list)) = dict.get("files") {
        let mut files = Vec::with_capacity(file_list.len());
        let mut total = 0i64;
        for entry in file_list {
            if let BVal::Dict(fd) = entry {
                let size = match fd.get("length") {
                    Some(BVal::Int(n)) => *n,
                    _ => 0,
                };
                let path = extract_file_path(fd);
                total += size;
                files.push(FileInfo { path, size });
            }
        }
        return Ok(TorrentMeta {
            name,
            files,
            total_size: total,
        });
    }

    // Single-file torrent: length is at the top level.
    let size = match dict.get("length") {
        Some(BVal::Int(n)) => *n,
        _ => 0,
    };
    Ok(TorrentMeta {
        files: vec![FileInfo {
            path: name.clone(),
            size,
        }],
        total_size: size,
        name,
    })
}

/// Extract a UTF-8 string from a bencode dict value (tries Bytes → String).
fn get_str(dict: &HashMap<String, BVal>, key: &str) -> Option<String> {
    match dict.get(key)? {
        BVal::Bytes(b) => Some(String::from_utf8_lossy(b).into_owned()),
        _ => None,
    }
}

/// Build a POSIX-style relative path from the "path" list in a file entry.
fn extract_file_path(fd: &HashMap<String, BVal>) -> String {
    match fd.get("path") {
        Some(BVal::List(parts)) => parts
            .iter()
            .filter_map(|p| {
                if let BVal::Bytes(b) = p {
                    String::from_utf8(b.clone()).ok()
                } else {
                    None
                }
            })
            .collect::<Vec<_>>()
            .join("/"),
        Some(BVal::Bytes(b)) => String::from_utf8_lossy(b).into_owned(),
        _ => String::new(),
    }
}

// ─── Misc helpers ─────────────────────────────────────────────────────────────

fn parse_hex20(hex_str: &str) -> Result<[u8; 20], Error> {
    if hex_str.len() != 40 {
        return Err(Error::BadInfoHash(hex_str.to_owned()));
    }
    let mut out = [0u8; 20];
    for (i, chunk) in hex_str.as_bytes().chunks(2).enumerate() {
        let s = std::str::from_utf8(chunk).map_err(|_| Error::BadInfoHash(hex_str.to_owned()))?;
        out[i] = u8::from_str_radix(s, 16).map_err(|_| Error::BadInfoHash(hex_str.to_owned()))?;
    }
    Ok(out)
}

fn fmt_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── parse_hex20 ────────────────────────────────────────────────────────────

    #[test]
    fn parse_hex20_valid() {
        let h = parse_hex20("da39a3ee5e6b4b0d3255bfef95601890afd80709").unwrap();
        assert_eq!(h[0], 0xda);
        assert_eq!(h[1], 0x39);
        assert_eq!(h[19], 0x09);
    }

    #[test]
    fn parse_hex20_rejects_short() {
        assert!(parse_hex20("da39").is_err());
    }

    #[test]
    fn parse_hex20_rejects_non_hex() {
        let long = "zz39a3ee5e6b4b0d3255bfef95601890afd80709";
        assert!(parse_hex20(long).is_err());
    }

    // ── bencode parser ─────────────────────────────────────────────────────────

    #[test]
    fn decode_integer() {
        let (val, end) = decode_bval(b"i42e", 0).unwrap();
        assert!(matches!(val, BVal::Int(42)));
        assert_eq!(end, 4);
    }

    #[test]
    fn decode_negative_integer() {
        let (val, _) = decode_bval(b"i-7e", 0).unwrap();
        assert!(matches!(val, BVal::Int(-7)));
    }

    #[test]
    fn decode_bytestring() {
        let (val, end) = decode_bval(b"4:spam", 0).unwrap();
        match val {
            BVal::Bytes(b) => assert_eq!(b, b"spam"),
            _ => panic!("expected Bytes"),
        }
        assert_eq!(end, 6);
    }

    #[test]
    fn decode_empty_string() {
        let (val, end) = decode_bval(b"0:", 0).unwrap();
        assert!(matches!(val, BVal::Bytes(ref b) if b.is_empty()));
        assert_eq!(end, 2);
    }

    #[test]
    fn decode_list() {
        // l4:spami42ee
        let data = b"l4:spami42ee";
        let (val, end) = decode_bval(data, 0).unwrap();
        match val {
            BVal::List(items) => {
                assert_eq!(items.len(), 2);
                assert!(matches!(&items[0], BVal::Bytes(b) if b == b"spam"));
                assert!(matches!(&items[1], BVal::Int(42)));
            }
            _ => panic!("expected List"),
        }
        assert_eq!(end, data.len());
    }

    #[test]
    fn decode_dict() {
        // d3:bari1e3:fooi2ee  (keys must be sorted in bencode)
        let data = b"d3:bari1e3:fooi2ee";
        let (dict, end) = decode_bencode_dict(data).unwrap();
        assert_eq!(dict.len(), 2);
        assert!(matches!(dict.get("bar"), Some(BVal::Int(1))));
        assert!(matches!(dict.get("foo"), Some(BVal::Int(2))));
        assert_eq!(end, data.len());
    }

    #[test]
    fn decode_nested_dict() {
        // d1:md11:ut_metadatai3eee  — the BEP-10 extension handshake "m" dict
        let data = b"d1:md11:ut_metadatai3eee";
        let (outer, _) = decode_bencode_dict(data).unwrap();
        let inner = match outer.get("m") {
            Some(BVal::Dict(d)) => d,
            _ => panic!("expected inner dict"),
        };
        assert!(matches!(inner.get("ut_metadata"), Some(BVal::Int(3))));
    }

    // ── parse_ext_handshake ────────────────────────────────────────────────────

    #[test]
    fn ext_handshake_full() {
        // {"m": {"ut_metadata": 2}, "metadata_size": 32768, "v": "..."}
        // d13:metadata_sizei32768e1:md11:ut_metadatai2ee1:v5:dummye
        let data = b"d13:metadata_sizei32768e1:md11:ut_metadatai2ee1:v5:dummye";
        let (ut_id, size) = parse_ext_handshake(data).unwrap();
        assert_eq!(ut_id, 2);
        assert_eq!(size, 32768);
    }

    #[test]
    fn ext_handshake_missing_metadata_size_returns_none() {
        // {"m": {"ut_metadata": 1}}  — no metadata_size
        let data = b"d1:md11:ut_metadatai1eee";
        assert!(parse_ext_handshake(data).is_none());
    }

    // ── parse_ut_metadata_response ─────────────────────────────────────────────

    #[test]
    fn ut_metadata_data_response() {
        // d8:msg_typei1e5:piecei0e10:total_sizei512ee<8 raw bytes>
        let mut payload = b"d8:msg_typei1e5:piecei0e10:total_sizei512ee".to_vec();
        let raw: &[u8] = b"RAWBYTES";
        payload.extend_from_slice(raw);

        match parse_ut_metadata_response(&payload) {
            Some(UtResponse::Data { piece, data_offset }) => {
                assert_eq!(piece, 0);
                assert_eq!(&payload[data_offset..], raw);
            }
            _ => panic!("expected Data response"),
        }
    }

    #[test]
    fn ut_metadata_reject_response() {
        let data = b"d8:msg_typei2e5:piecei0ee";
        assert!(matches!(
            parse_ut_metadata_response(data),
            Some(UtResponse::Reject)
        ));
    }

    // ── parse_info_dict ────────────────────────────────────────────────────────

    #[test]
    fn single_file_torrent() {
        // d6:lengthi1048576e4:name8:test.mkve
        let data = b"d6:lengthi1048576e4:name8:test.mkve";
        let meta = parse_info_dict(data).unwrap();
        assert_eq!(meta.name, "test.mkv");
        assert_eq!(meta.total_size, 1_048_576);
        assert_eq!(meta.files.len(), 1);
        assert_eq!(meta.files[0].path, "test.mkv");
        assert_eq!(meta.files[0].size, 1_048_576);
    }

    #[test]
    fn multi_file_torrent() {
        // d { "files": [ d{"length":100,"path":["video","foo.mkv"]}e  d{"length":50,"path":["subs","foo.srt"]}e ]  "name":"foo" }
        // Key:  eed = end-path-list, end-file1-dict, start-file2-dict
        let data = b"d5:filesld6:lengthi100e4:pathl5:video7:foo.mkveed6:lengthi50e4:pathl4:subs7:foo.srteee4:name3:fooe";
        let meta = parse_info_dict(data).unwrap();
        assert_eq!(meta.name, "foo");
        assert_eq!(meta.total_size, 150);
        assert_eq!(meta.files.len(), 2);
        assert_eq!(meta.files[0].path, "video/foo.mkv");
        assert_eq!(meta.files[0].size, 100);
        assert_eq!(meta.files[1].path, "subs/foo.srt");
        assert_eq!(meta.files[1].size, 50);
    }

    // ── build_handshake ────────────────────────────────────────────────────────

    #[test]
    fn handshake_length_and_magic() {
        let hash = [0xabu8; 20];
        let pid = [0x01u8; 20];
        let hs = build_handshake(&hash, &pid);

        assert_eq!(hs.len(), 68);
        assert_eq!(hs[0], 19); // pstrlen
        assert_eq!(&hs[1..20], b"BitTorrent protocol"); // pstr
        assert_eq!(hs[25], 0x10); // BEP-10 extension bit
        assert_eq!(&hs[28..48], &hash[..]); // info_hash
        assert_eq!(&hs[48..68], &pid[..]); // peer_id
    }

    #[test]
    fn handshake_other_reserved_bytes_are_zero() {
        let hs = build_handshake(&[0u8; 20], &[0u8; 20]);
        // reserved = bytes 20..28; only byte 25 should be non-zero
        for (i, &b) in hs[20..28].iter().enumerate() {
            if i == 5 {
                assert_eq!(b, 0x10, "reserved[5] must be 0x10");
            } else {
                assert_eq!(b, 0, "reserved[{i}] must be 0");
            }
        }
    }
}

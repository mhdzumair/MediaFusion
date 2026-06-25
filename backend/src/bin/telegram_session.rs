//! Interactive grammers session generator / Telethon converter.
//!
//! Usage:
//!   cargo run --bin telegram_session
//!   cargo run --bin telegram_session -- --convert-telethon "1ABC..."

use std::env;
use std::io::{self, Write};

use mediafusion_api::config::AppConfig;
use mediafusion_api::util::telegram_session;

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() >= 3 && args[1] == "--convert-telethon" {
        match telegram_session::convert_telethon_string(&args[2]) {
            Ok(blob) => {
                println!("TELEGRAM_GRAMMERS_SESSION={blob}");
            }
            Err(e) => {
                eprintln!("Conversion failed: {e}");
                std::process::exit(1);
            }
        }
        return;
    }

    let config = AppConfig::from_env();
    let api_id = config.telegram_api_id.unwrap_or_else(|| {
        eprint!("API ID: ");
        io::stdout().flush().ok();
        let mut line = String::new();
        io::stdin().read_line(&mut line).ok();
        line.trim().parse().expect("invalid API ID")
    });
    let api_hash = config
        .telegram_api_hash
        .unwrap_or_else(|| prompt("API Hash: "));

    if let Some(existing) = &config.telegram_grammers_session
        && let Ok(data) = telegram_session::parse_session_data(existing)
        && telegram_session::session_is_authenticated(&data)
    {
        println!("Existing TELEGRAM_GRAMMERS_SESSION is already authenticated.");
        return;
    }

    // Try converting legacy Telethon session from env TELEGRAM_SESSION_STRING if set
    if let Ok(telethon) = env::var("TELEGRAM_SESSION_STRING")
        && !telethon.is_empty()
    {
        match telegram_session::convert_telethon_string(&telethon) {
            Ok(blob) => {
                println!("Converted Telethon session:");
                println!("TELEGRAM_GRAMMERS_SESSION={blob}");
                return;
            }
            Err(e) => eprintln!("Telethon conversion failed: {e}"),
        }
    }

    println!("Interactive grammers login is not yet wired in this binary.");
    println!("Convert an existing Telethon StringSession instead:");
    println!("  cargo run --bin telegram_session -- --convert-telethon \"YOUR_STRING_SESSION\"");
    println!();
    println!(
        "Required env: TELEGRAM_API_ID={}, TELEGRAM_API_HASH=<set>",
        api_id
    );
    let _ = api_hash;
}

fn prompt(label: &str) -> String {
    eprint!("{label}");
    io::stdout().flush().ok();
    let mut line = String::new();
    io::stdin().read_line(&mut line).ok();
    line.trim().to_string()
}

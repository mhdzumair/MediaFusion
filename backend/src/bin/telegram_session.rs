//! Local Telegram session generator (admin/dev utility).
//!
//! Users should connect their account via the web UI:
//!   Configure → Telegram → Connect scraping session
//!
//! This binary remains useful for testing Telethon/Pyrogram string conversion:
//!   cargo run --bin telegram_session -- --convert "YOUR_STRING_SESSION"

use mediafusion_api::util::telegram_session;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() >= 3 && (args[1] == "--convert-telethon" || args[1] == "--convert") {
        match telegram_session::convert_session_string(&args[2]) {
            Ok(blob) => {
                println!("Converted Telethon session string:\n{blob}");
            }
            Err(e) => {
                eprintln!("Conversion failed: {e}");
                std::process::exit(1);
            }
        }
        return;
    }

    eprintln!("Per-user Telegram sessions are configured in the web UI.");
    eprintln!("Use --convert to validate/convert a Telethon or Pyrogram string session.");
}

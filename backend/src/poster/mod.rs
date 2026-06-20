pub mod sports;

/// Poster annotation pipeline.
///
/// Mirrors Python `utils/poster.py`:
///   1. Resize to 300×450
///   2. IMDb badge (semi-transparent rect + logo + rating text) at bottom-left
///   3. MediaFusion watermark (logo_text.png) at top-right
///   4. Optional centered title text with outline
use std::io::Cursor;

use ab_glyph::{Font, FontArc, PxScale, ScaleFont};
use image::{
    imageops::{self, FilterType},
    DynamicImage, Rgba, RgbaImage,
};
use imageproc::drawing::{draw_filled_rect_mut, draw_text_mut};
use imageproc::rect::Rect;

// ─── Embedded assets ──────────────────────────────────────────────────────────

static IMDB_LOGO: &[u8] = include_bytes!("../../../resources/images/imdb_logo.png");
static WATERMARK: &[u8] = include_bytes!("../../../resources/images/logo_text.png");
static FONT_MEDIUM: &[u8] = include_bytes!("../../../resources/fonts/IBMPlexSans-Medium.ttf");
static FONT_BOLD: &[u8] = include_bytes!("../../../resources/fonts/IBMPlexSans-Bold.ttf");
// Archivo Black (900) for ghost monogram; ExtraBold (800) for titles — matches design spec
static FONT_ARCHIVO_BLACK: &[u8] = include_bytes!("../../../resources/fonts/Archivo-Black.ttf");
static FONT_ARCHIVO_EXTRABOLD: &[u8] =
    include_bytes!("../../../resources/fonts/Archivo-ExtraBold.ttf");

// Lazy-loaded fonts (compiled into the binary at startup)
use std::sync::OnceLock;
static MEDIUM_FONT: OnceLock<FontArc> = OnceLock::new();
static BOLD_FONT: OnceLock<FontArc> = OnceLock::new();
static ARCHIVO_BLACK_FONT: OnceLock<FontArc> = OnceLock::new();
static ARCHIVO_EXTRABOLD_FONT: OnceLock<FontArc> = OnceLock::new();

fn medium_font() -> &'static FontArc {
    MEDIUM_FONT.get_or_init(|| FontArc::try_from_slice(FONT_MEDIUM).expect("IBM Plex Sans Medium"))
}

fn bold_font() -> &'static FontArc {
    BOLD_FONT.get_or_init(|| FontArc::try_from_slice(FONT_BOLD).expect("IBM Plex Sans Bold"))
}

fn archivo_black() -> &'static FontArc {
    ARCHIVO_BLACK_FONT
        .get_or_init(|| FontArc::try_from_slice(FONT_ARCHIVO_BLACK).expect("Archivo Black"))
}

fn archivo_extrabold() -> &'static FontArc {
    ARCHIVO_EXTRABOLD_FONT
        .get_or_init(|| FontArc::try_from_slice(FONT_ARCHIVO_EXTRABOLD).expect("Archivo ExtraBold"))
}

// ─── Public API ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct AnnotateParams {
    pub imdb_rating: Option<f32>,
    pub title: Option<String>,
    pub is_add_title: bool,
}

/// Annotate a JPEG poster image.
///
/// This is CPU-bound; wrap in `tokio::task::spawn_blocking` at the call site.
pub fn annotate(
    jpeg_bytes: &[u8],
    params: &AnnotateParams,
) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    // Load and resize to canonical 300×450 poster size
    let img = image::load_from_memory(jpeg_bytes)?.resize_exact(300, 450, FilterType::Lanczos3);
    let mut canvas = img.to_rgba8();

    // 1. IMDb badge
    if let Some(rating) = params.imdb_rating {
        add_imdb_badge(&mut canvas, rating);
    }

    // 2. Watermark
    add_watermark(&mut canvas);

    // 3. Title text
    if params.is_add_title {
        if let Some(ref title) = params.title {
            if !title.is_empty() {
                add_title(&mut canvas, title);
            }
        }
    }

    // Encode as JPEG
    let rgb = DynamicImage::ImageRgba8(canvas).to_rgb8();
    let mut buf = Cursor::new(Vec::new());
    DynamicImage::ImageRgb8(rgb).write_to(&mut buf, image::ImageFormat::Jpeg)?;
    Ok(buf.into_inner())
}

/// Generate a placeholder poster (300×450 JPEG) for media without artwork.
///
/// Layout (from the design spec):
///  - 145° diagonal gradient `hsl(h,34%,13%) → hsl(h+22°,40%,24%)`
///  - Bottom vignette for legibility
///  - Top-left radial sheen (10% white)
///  - Oversized ghost monogram bleeding off the bottom-right corner (7% white)
///  - Type kicker (accent bar + media-type label) at top-left
///  - MediaFusion watermark at top-right
///  - Title anchored to bottom-left; size based on character count
///  - Year and S·E chip metadata row below the title
///
/// Deterministic: same title+type always produces the same poster.
/// CPU-bound; wrap in `tokio::task::spawn_blocking` at the call site.
pub fn generate_placeholder(
    title: &str,
    media_type: &str,
    year: Option<i32>,
) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    const W: u32 = 300;
    const H: u32 = 450;

    // ── Colour palette ──────────────────────────────────────────────────────
    let mut hash: u32 = 2_166_136_261;
    for byte in title.bytes() {
        hash = (hash ^ byte as u32).wrapping_mul(16_777_619);
    }
    let hue_deg = (hash & 0xFFFF) as f32 / 65536.0 * 360.0;
    let bg_top = hsl_to_rgb(hue_deg / 360.0, 0.34, 0.13);
    let bg_bot = hsl_to_rgb(((hue_deg + 22.0) % 360.0) / 360.0, 0.40, 0.24);
    let accent = hsl_to_rgb(hue_deg / 360.0, 0.72, 0.62);

    // ── 1. Diagonal gradient background (≈145°) ─────────────────────────────
    let mut canvas: RgbaImage = RgbaImage::new(W, H);
    for y in 0..H {
        for x in 0..W {
            let t = ((x as f32 / W as f32) * 0.45 + (y as f32 / H as f32) * 0.55).clamp(0.0, 1.0);
            canvas.put_pixel(
                x,
                y,
                Rgba([
                    lerp(bg_top.0, bg_bot.0, t),
                    lerp(bg_top.1, bg_bot.1, t),
                    lerp(bg_top.2, bg_bot.2, t),
                    255,
                ]),
            );
        }
    }

    // ── 2. Bottom vignette ───────────────────────────────────────────────────
    for y in 0..H {
        let frac = y as f32 / H as f32;
        let alpha: f32 = if frac < 0.38 {
            0.0
        } else if frac < 0.72 {
            (frac - 0.38) / (0.72 - 0.38) * 0.30
        } else {
            0.30 + (frac - 0.72) / (1.0 - 0.72) * 0.32
        };
        if alpha > 0.0 {
            for x in 0..W {
                let p = canvas.get_pixel_mut(x, y);
                p[0] = (p[0] as f32 * (1.0 - alpha)) as u8;
                p[1] = (p[1] as f32 * (1.0 - alpha)) as u8;
                p[2] = (p[2] as f32 * (1.0 - alpha)) as u8;
            }
        }
    }

    // ── 3. Top-left radial sheen ─────────────────────────────────────────────
    let sheen_cx = W as f32 * 0.25;
    let sheen_cy = H as f32 * 0.08;
    let rx = W as f32 * 0.60;
    let ry = H as f32 * 0.30;
    for y in 0..((H as f32 * 0.45) as u32) {
        for x in 0..W {
            let d = ((x as f32 - sheen_cx) / rx).hypot((y as f32 - sheen_cy) / ry);
            let alpha = (1.0 - d).max(0.0) * 0.10;
            if alpha > 0.0 {
                let p = canvas.get_pixel_mut(x, y);
                p[0] = (p[0] as f32 * (1.0 - alpha) + 255.0 * alpha) as u8;
                p[1] = (p[1] as f32 * (1.0 - alpha) + 255.0 * alpha) as u8;
                p[2] = (p[2] as f32 * (1.0 - alpha) + 255.0 * alpha) as u8;
            }
        }
    }

    // ── Derived display values ───────────────────────────────────────────────
    let display = {
        let c = clean_placeholder_title(title);
        if c.trim().is_empty() {
            "Media".to_string()
        } else {
            c
        }
    };
    let initials = placeholder_initials(&display);
    let (season, episode) = parse_placeholder_se(&display);
    // Display fonts: Archivo Black for monogram (weight 900), ExtraBold for title (weight 800)
    // UI fonts: IBM Plex Sans Bold/Medium for kicker, year, chip
    let font_monogram = archivo_black();
    let font_title = archivo_extrabold();
    let font_ui = bold_font();

    // ── 4. Oversized ghost monogram (bottom-right bleed, 7% white) ───────────
    // CSS: right:-14px; bottom:-46px — bleeds off the corner.
    // In image coords: x = W - glyph_width + 14, y = H - glyph_height + 46
    let ghost_scale = PxScale::from(230.0);
    let sf_ghost = font_monogram.as_scaled(ghost_scale);
    let ghost_w = text_width(font_monogram, ghost_scale, &initials) as i32;
    let ghost_h = (sf_ghost.ascent() - sf_ghost.descent()) as i32;
    let ghost_x = W as i32 - ghost_w + 14;
    let ghost_y = H as i32 - ghost_h + 46;
    draw_text_mut(
        &mut canvas,
        Rgba([255u8, 255, 255, 18]), // 18/255 ≈ 7%
        ghost_x,
        ghost_y,
        ghost_scale,
        font_monogram,
        &initials,
    );

    // ── 5. Top bar: accent bar + kicker (left) | "MediaFusion" text (right) ───
    let kicker = media_type_kicker(media_type);
    let accent_rgba = Rgba([accent.0, accent.1, accent.2, 255]);
    let label_scale = PxScale::from(14.0);
    let top_y = 16i32;

    // Accent bar: 18×3 px, vertically centred on text baseline
    for px in 22u32..40 {
        for py in 24u32..27 {
            if px < W && py < H {
                canvas.put_pixel(px, py, accent_rgba);
            }
        }
    }
    // Kicker label — IBM Plex Sans Bold, white at 0.82 alpha, tracking .24em
    draw_text_mut(
        &mut canvas,
        Rgba([255u8, 255, 255, 209]),
        44,
        top_y,
        label_scale,
        font_ui,
        kicker,
    );

    // "MediaFusion" — IBM Plex Sans Bold, "Media" at 0.42 alpha, "Fusion" in accent
    let mf_scale = PxScale::from(14.0);
    let media_w = text_width(font_ui, mf_scale, "Media") as i32;
    let fusion_w = text_width(font_ui, mf_scale, "Fusion") as i32;
    let mf_x = W as i32 - 22 - media_w - fusion_w;
    draw_text_mut(
        &mut canvas,
        Rgba([255u8, 255, 255, 107]),
        mf_x,
        top_y,
        mf_scale,
        font_ui,
        "Media",
    );
    draw_text_mut(
        &mut canvas,
        accent_rgba,
        mf_x + media_w,
        top_y,
        mf_scale,
        font_ui,
        "Fusion",
    );

    // ── 7. Title + metadata anchored to bottom-left ──────────────────────────
    let white = Rgba([255u8, 255, 255, 255]);

    // Archivo ExtraBold (800) for title; size by character count per design spec
    let title_px: f32 = match display.len() {
        0..=9 => 42.0,
        10..=16 => 34.0,
        17..=26 => 27.0,
        _ => 22.0,
    };
    let title_scale = PxScale::from(title_px);
    let sf_t = font_title.as_scaled(title_scale);
    let title_line_h = (sf_t.ascent() - sf_t.descent()) as i32;

    // Word-wrap title — right margin 22px on each side
    let title_lines = word_wrap(&display, font_title, title_scale, W as i32 - 44);

    // Metadata row height (only rendered when data exists)
    let has_year = year.is_some();
    let has_chip = season.is_some() && episode.is_some();
    let meta_row_h = if has_year || has_chip { 26i32 } else { 0 };

    let block_h = title_line_h * title_lines.len() as i32 + meta_row_h;
    let mut ty = H as i32 - 22 - block_h;

    for line in &title_lines {
        // No text shadow needed — the bottom vignette provides contrast
        draw_text_mut(&mut canvas, white, 22, ty, title_scale, font_title, line);
        ty += title_line_h;
    }

    // ── 8. Metadata row: year + S·E chip ─────────────────────────────────────
    if has_year || has_chip {
        ty += 13; // 13px margin-top per design spec
        let mut mx = 22i32;

        if let Some(y) = year {
            let ys = y.to_string();
            let yscale = PxScale::from(11.0);
            draw_text_mut(
                &mut canvas,
                Rgba([255u8, 255, 255, 168]), // 0.66 alpha
                mx,
                ty + 2,
                yscale,
                font_ui,
                &ys,
            );
            mx += text_width(font_ui, yscale, &ys) as i32 + 9;
        }

        if has_chip {
            let chip_str = format!("S{:02} \u{00B7} E{:02}", season.unwrap(), episode.unwrap());
            let cscale = PxScale::from(9.5);
            let chip_tw = text_width(font_ui, cscale, &chip_str) as i32;
            let pad = 8i32;
            let chip_w = chip_tw + pad * 2;
            let chip_h = 16i32;

            // Chip background (accent at 12%)
            blend_rect(
                &mut canvas,
                mx,
                ty,
                chip_w,
                chip_h,
                accent.0,
                accent.1,
                accent.2,
                31,
            );
            // Chip border (accent at 55%)
            outline_rect(
                &mut canvas,
                mx,
                ty,
                chip_w,
                chip_h,
                accent.0,
                accent.1,
                accent.2,
                140,
            );
            // Chip text (accent colour)
            draw_text_mut(
                &mut canvas,
                accent_rgba,
                mx + pad,
                ty + (chip_h - 10) / 2,
                cscale,
                font_ui,
                &chip_str,
            );
        }
    }

    let rgb = DynamicImage::ImageRgba8(canvas).to_rgb8();
    let mut buf = Cursor::new(Vec::new());
    DynamicImage::ImageRgb8(rgb).write_to(&mut buf, image::ImageFormat::Jpeg)?;
    Ok(buf.into_inner())
}

// ─── Placeholder helpers ──────────────────────────────────────────────────────

#[inline]
fn lerp(a: u8, b: u8, t: f32) -> u8 {
    (a as f32 * (1.0 - t) + b as f32 * t) as u8
}

fn hsl_to_rgb(h: f32, s: f32, l: f32) -> (u8, u8, u8) {
    let c = (1.0 - (2.0 * l - 1.0).abs()) * s;
    let x = c * (1.0 - ((h * 6.0) % 2.0 - 1.0).abs());
    let m = l - c / 2.0;
    let (r1, g1, b1) = if h < 1.0 / 6.0 {
        (c, x, 0.0)
    } else if h < 2.0 / 6.0 {
        (x, c, 0.0)
    } else if h < 3.0 / 6.0 {
        (0.0, c, x)
    } else if h < 4.0 / 6.0 {
        (0.0, x, c)
    } else if h < 5.0 / 6.0 {
        (x, 0.0, c)
    } else {
        (c, 0.0, x)
    };
    (
        ((r1 + m) * 255.0).clamp(0.0, 255.0) as u8,
        ((g1 + m) * 255.0).clamp(0.0, 255.0) as u8,
        ((b1 + m) * 255.0).clamp(0.0, 255.0) as u8,
    )
}

fn media_type_kicker(media_type: &str) -> &'static str {
    match media_type {
        "movie" => "MOVIE",
        "series" => "SERIES",
        "tv" => "LIVE TV",
        _ => "MEDIA",
    }
}

fn clean_placeholder_title(title: &str) -> String {
    const EXTS: &[&str] = &[
        ".m4b", ".m4a", ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mp3", ".aac",
        ".flac", ".wav", ".ogg", ".opus", ".m3u8", ".ts", ".mpd",
    ];
    let lower = title.to_lowercase();
    let stripped = EXTS
        .iter()
        .find(|&&e| lower.ends_with(e))
        .map(|e| &title[..title.len() - e.len()])
        .unwrap_or(title);
    stripped
        .trim_matches(|c: char| c == '-' || c == '_' || c.is_whitespace())
        .to_string()
}

fn placeholder_initials(title: &str) -> String {
    const STOP: &[&str] = &[
        "a", "an", "the", "of", "by", "to", "and", "in", "at", "on", "for", "with", "from",
    ];
    let initials: String = title
        .split_whitespace()
        .filter(|w| {
            let lo = w.to_lowercase();
            !STOP.contains(&lo.as_str()) && w.chars().any(|c| c.is_alphabetic())
        })
        .take(2)
        .filter_map(|w| w.chars().find(|c| c.is_alphabetic()))
        .flat_map(|c| c.to_uppercase())
        .collect();
    if initials.is_empty() {
        title
            .chars()
            .find(|c| c.is_alphabetic())
            .map(|c| c.to_uppercase().to_string())
            .unwrap_or_else(|| "?".to_string())
    } else {
        initials
    }
}

/// Parse year (1900–2099) and SxxExx season/episode from a title string.
/// Parse SxxExx season/episode numbers from a title string (e.g. IPTV channel names).
fn parse_placeholder_se(title: &str) -> (Option<i32>, Option<i32>) {
    let upper = title.to_uppercase();
    let bytes = upper.as_bytes();
    for i in 0..bytes.len().saturating_sub(3) {
        if bytes[i] == b'S' {
            let s_start = i + 1;
            let s_end = s_start
                + bytes[s_start..]
                    .iter()
                    .take(3)
                    .take_while(|b| b.is_ascii_digit())
                    .count();
            if s_end > s_start && s_end < bytes.len() && bytes[s_end] == b'E' {
                let e_start = s_end + 1;
                let e_end = e_start
                    + bytes[e_start..]
                        .iter()
                        .take(3)
                        .take_while(|b| b.is_ascii_digit())
                        .count();
                if e_end > e_start {
                    if let (Ok(s), Ok(e)) = (
                        upper[s_start..s_end].parse::<i32>(),
                        upper[e_start..e_end].parse::<i32>(),
                    ) {
                        return (Some(s), Some(e));
                    }
                }
            }
        }
    }
    (None, None)
}

fn blend_rect(canvas: &mut RgbaImage, x: i32, y: i32, w: i32, h: i32, r: u8, g: u8, b: u8, a: u8) {
    let alpha = a as f32 / 255.0;
    for py in y..y + h {
        for px in x..x + w {
            if px >= 0 && py >= 0 && px < canvas.width() as i32 && py < canvas.height() as i32 {
                let p = canvas.get_pixel_mut(px as u32, py as u32);
                p[0] = (p[0] as f32 * (1.0 - alpha) + r as f32 * alpha) as u8;
                p[1] = (p[1] as f32 * (1.0 - alpha) + g as f32 * alpha) as u8;
                p[2] = (p[2] as f32 * (1.0 - alpha) + b as f32 * alpha) as u8;
            }
        }
    }
}

fn outline_rect(
    canvas: &mut RgbaImage,
    x: i32,
    y: i32,
    w: i32,
    h: i32,
    r: u8,
    g: u8,
    b: u8,
    a: u8,
) {
    let alpha = a as f32 / 255.0;
    let mut blend = |px: i32, py: i32| {
        if px >= 0 && py >= 0 && px < canvas.width() as i32 && py < canvas.height() as i32 {
            let p = canvas.get_pixel_mut(px as u32, py as u32);
            p[0] = (p[0] as f32 * (1.0 - alpha) + r as f32 * alpha) as u8;
            p[1] = (p[1] as f32 * (1.0 - alpha) + g as f32 * alpha) as u8;
            p[2] = (p[2] as f32 * (1.0 - alpha) + b as f32 * alpha) as u8;
        }
    };
    for px in x..x + w {
        blend(px, y);
        blend(px, y + h - 1);
    }
    for py in y..y + h {
        blend(x, py);
        blend(x + w - 1, py);
    }
}

// ─── IMDb badge ───────────────────────────────────────────────────────────────

fn add_imdb_badge(canvas: &mut RgbaImage, rating: f32) {
    let font = medium_font();
    let scale = PxScale::from(20.0);
    let margin = 10i32;
    let pad = 5i32;

    let rating_text = format!(" {:.1}/10", rating);

    // Measure text height from font metrics
    let text_h = {
        let sf = font.as_scaled(scale);
        sf.ascent() - sf.descent()
    } as i32;

    let text_w = text_width(font, scale, &rating_text) as i32;

    // Load and resize IMDb logo to match text height
    let imdb_logo = image::load_from_memory(IMDB_LOGO)
        .map(|img| {
            let aspect = img.width() as f32 / img.height() as f32;
            let logo_w = (text_h as f32 * aspect) as u32;
            img.resize_exact(logo_w, text_h as u32, FilterType::Lanczos3)
                .to_rgba8()
        })
        .ok();

    let logo_w = imdb_logo.as_ref().map(|l| l.width() as i32).unwrap_or(0);
    let total_w = logo_w + text_w + 2 * pad;
    let total_h = text_h + 2 * pad;

    // Semi-transparent dark background rect
    let rect_x = margin;
    let rect_y = canvas.height() as i32 - margin - total_h;
    let bg_color = Rgba([0u8, 0, 0, 176]);
    if rect_y >= 0 {
        draw_filled_rect_mut(
            canvas,
            Rect::at(rect_x, rect_y).of_size(total_w as u32, total_h as u32),
            bg_color,
        );
    }

    // Composite IMDb logo
    if let Some(logo) = imdb_logo {
        let logo_x = (rect_x + pad) as i64;
        let logo_y = (rect_y + pad) as i64;
        if logo_x >= 0 && logo_y >= 0 {
            imageops::overlay(canvas, &logo, logo_x, logo_y);
        }
    }

    // Draw rating text in IMDb gold
    let text_x = rect_x + pad + logo_w;
    let text_y = rect_y;
    let gold = Rgba([0xF5u8, 0xC5, 0x18, 0xFF]);
    if text_x >= 0 && text_y >= 0 {
        draw_text_mut(canvas, gold, text_x, text_y, scale, font, &rating_text);
    }
}

// ─── Watermark ────────────────────────────────────────────────────────────────

fn add_watermark(canvas: &mut RgbaImage) {
    let margin = 10i64;
    let Ok(wm_img) = image::load_from_memory(WATERMARK) else {
        return;
    };
    let new_w = canvas.width() / 2;
    let aspect = wm_img.width() as f32 / wm_img.height() as f32;
    let new_h = (new_w as f32 / aspect) as u32;
    let wm = wm_img
        .resize_exact(new_w, new_h, FilterType::Lanczos3)
        .to_rgba8();
    let x = canvas.width() as i64 - wm.width() as i64 - margin;
    let y = margin;
    imageops::overlay(canvas, &wm, x, y);
}

// ─── Title text ───────────────────────────────────────────────────────────────

fn add_title(canvas: &mut RgbaImage, title: &str) {
    let font = bold_font();
    let max_w = canvas.width() as i32 - 20;
    let max_lines = 3usize;

    // Find a font size that fits within max_lines
    let (lines, scale) = fit_title(title, font, max_w, max_lines, 50, 20);
    if lines.is_empty() {
        return;
    }

    let line_spacing = 10i32;
    let sf = font.as_scaled(scale);
    let line_h = (sf.ascent() - sf.descent()) as i32;
    let block_h = line_h * lines.len() as i32 + line_spacing * (lines.len() as i32 - 1);

    let y_start = ((canvas.height() as i32 - block_h) / 2).max(0);

    // Sample center strip to pick text color
    let sample_top = (y_start - block_h / 2).clamp(0, canvas.height() as i32 - 1) as u32;
    let sample_bot = (y_start + block_h + block_h / 2).clamp(0, canvas.height() as i32 - 1) as u32;
    let (text_color, outline_color) =
        text_color_for_region(canvas, 0, sample_top, canvas.width(), sample_bot);

    let mut y = y_start;
    for line in &lines {
        let w = text_width(font, scale, line) as i32;
        let x = ((canvas.width() as i32 - w) / 2).max(0);
        draw_outlined_text(canvas, x, y, scale, font, line, text_color, outline_color);
        y += line_h + line_spacing;
    }
}

// ─── Text helpers ─────────────────────────────────────────────────────────────

fn text_width(font: &FontArc, scale: PxScale, text: &str) -> f32 {
    let sf = font.as_scaled(scale);
    text.chars().map(|c| sf.h_advance(font.glyph_id(c))).sum()
}

fn word_wrap(text: &str, font: &FontArc, scale: PxScale, max_w: i32) -> Vec<String> {
    let mut lines: Vec<String> = Vec::new();
    let mut current = String::new();

    for word in text.split_whitespace() {
        let candidate = if current.is_empty() {
            word.to_string()
        } else {
            format!("{current} {word}")
        };
        if text_width(font, scale, &candidate) as i32 <= max_w {
            current = candidate;
        } else {
            if !current.is_empty() {
                lines.push(current);
            }
            current = word.to_string();
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

fn fit_title(
    title: &str,
    font: &FontArc,
    max_w: i32,
    max_lines: usize,
    initial_size: u32,
    min_size: u32,
) -> (Vec<String>, PxScale) {
    for size in (min_size..=initial_size).rev() {
        let scale = PxScale::from(size as f32);
        let lines = word_wrap(title, font, scale, max_w);
        if lines.len() <= max_lines {
            return (lines, scale);
        }
    }
    let scale = PxScale::from(min_size as f32);
    let mut lines = word_wrap(title, font, scale, max_w);
    lines.truncate(max_lines);
    (lines, scale)
}

fn text_color_for_region(
    img: &RgbaImage,
    x: u32,
    y_top: u32,
    w: u32,
    y_bot: u32,
) -> (Rgba<u8>, Rgba<u8>) {
    let mut r_sum = 0u64;
    let mut g_sum = 0u64;
    let mut b_sum = 0u64;
    let mut count = 0u64;

    let x_end = (x + w).min(img.width());
    let y_end = y_bot.min(img.height());
    for py in y_top..y_end {
        for px in x..x_end {
            let p = img.get_pixel(px, py);
            r_sum += p[0] as u64;
            g_sum += p[1] as u64;
            b_sum += p[2] as u64;
            count += 1;
        }
    }

    if count == 0 {
        return (Rgba([255, 255, 255, 255]), Rgba([0, 0, 0, 255]));
    }

    let brightness =
        (0.299 * r_sum as f64 + 0.587 * g_sum as f64 + 0.114 * b_sum as f64) / count as f64;
    if brightness > 128.0 {
        (Rgba([0, 0, 0, 255]), Rgba([255, 255, 255, 255]))
    } else {
        (Rgba([255, 255, 255, 255]), Rgba([0, 0, 0, 255]))
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_outlined_text(
    canvas: &mut RgbaImage,
    x: i32,
    y: i32,
    scale: PxScale,
    font: &FontArc,
    text: &str,
    fill: Rgba<u8>,
    outline: Rgba<u8>,
) {
    const W: i32 = 2;
    for dx in -W..=W {
        for dy in -W..=W {
            if dx == 0 && dy == 0 {
                continue;
            }
            draw_text_mut(canvas, outline, x + dx, y + dy, scale, font, text);
        }
    }
    draw_text_mut(canvas, fill, x, y, scale, font, text);
}

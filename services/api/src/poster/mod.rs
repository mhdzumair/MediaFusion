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

static IMDB_LOGO:   &[u8] = include_bytes!("../../../../resources/images/imdb_logo.png");
static WATERMARK:   &[u8] = include_bytes!("../../../../resources/images/logo_text.png");
static FONT_MEDIUM: &[u8] = include_bytes!("../../../../resources/fonts/IBMPlexSans-Medium.ttf");
static FONT_BOLD:   &[u8] = include_bytes!("../../../../resources/fonts/IBMPlexSans-Bold.ttf");

// Lazy-loaded fonts (compiled into the binary at startup)
use std::sync::OnceLock;
static MEDIUM_FONT: OnceLock<FontArc> = OnceLock::new();
static BOLD_FONT:   OnceLock<FontArc> = OnceLock::new();

fn medium_font() -> &'static FontArc {
    MEDIUM_FONT.get_or_init(|| FontArc::try_from_slice(FONT_MEDIUM).expect("IBM Plex Sans Medium"))
}

fn bold_font() -> &'static FontArc {
    BOLD_FONT.get_or_init(|| FontArc::try_from_slice(FONT_BOLD).expect("IBM Plex Sans Bold"))
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
pub fn annotate(jpeg_bytes: &[u8], params: &AnnotateParams) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    // Load and resize to canonical 300×450 poster size
    let img = image::load_from_memory(jpeg_bytes)?
        .resize_exact(300, 450, FilterType::Lanczos3);
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

// ─── IMDb badge ───────────────────────────────────────────────────────────────

fn add_imdb_badge(canvas: &mut RgbaImage, rating: f32) {
    let font   = medium_font();
    let scale  = PxScale::from(20.0);
    let margin = 10i32;
    let pad    = 5i32;

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
            img.resize_exact(logo_w, text_h as u32, FilterType::Lanczos3).to_rgba8()
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
    let Ok(wm_img) = image::load_from_memory(WATERMARK) else { return };
    let new_w = canvas.width() / 2;
    let aspect = wm_img.width() as f32 / wm_img.height() as f32;
    let new_h = (new_w as f32 / aspect) as u32;
    let wm = wm_img.resize_exact(new_w, new_h, FilterType::Lanczos3).to_rgba8();
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
    if lines.is_empty() { return; }

    let line_spacing = 10i32;
    let sf = font.as_scaled(scale);
    let line_h = (sf.ascent() - sf.descent()) as i32;
    let block_h = line_h * lines.len() as i32 + line_spacing * (lines.len() as i32 - 1);

    let y_start = ((canvas.height() as i32 - block_h) / 2).max(0);

    // Sample center strip to pick text color
    let sample_top = (y_start - block_h / 2).clamp(0, canvas.height() as i32 - 1) as u32;
    let sample_bot = (y_start + block_h + block_h / 2).clamp(0, canvas.height() as i32 - 1) as u32;
    let (text_color, outline_color) = text_color_for_region(canvas, 0, sample_top, canvas.width(), sample_bot);

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
    x: u32, y_top: u32, w: u32, y_bot: u32,
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

    let brightness = (0.299 * r_sum as f64 + 0.587 * g_sum as f64 + 0.114 * b_sum as f64)
        / count as f64;
    if brightness > 128.0 {
        (Rgba([0, 0, 0, 255]), Rgba([255, 255, 255, 255]))
    } else {
        (Rgba([255, 255, 255, 255]), Rgba([0, 0, 0, 255]))
    }
}

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
            if dx == 0 && dy == 0 { continue; }
            draw_text_mut(canvas, outline, x + dx, y + dy, scale, font, text);
        }
    }
    draw_text_mut(canvas, fill, x, y, scale, font, text);
}

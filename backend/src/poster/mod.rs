pub mod sports;

/// Poster annotation and placeholder generation.
///
/// Text rendering uses `cosmic-text` (rustybuzz shaping + BiDi) for full
/// Unicode support: Cyrillic, Arabic, Hebrew, Greek, Devanagari, CJK (via
/// system fonts), etc.  Bundled Noto Sans fonts cover Cyrillic/Greek/Latin
/// and Arabic out of the box; install `fonts-noto-cjk` in the container for
/// CJK coverage.
use std::cell::RefCell;
use std::io::Cursor;

use cosmic_text::{
    Align, Attrs, Buffer, Color as CosColor, Family, FontSystem, Metrics, Shaping, SwashCache,
    Weight,
};
use image::{
    DynamicImage, Rgba, RgbaImage,
    imageops::{self, FilterType},
};
use imageproc::drawing::draw_filled_rect_mut;
use imageproc::rect::Rect;

// ─── Embedded assets ──────────────────────────────────────────────────────────

static IMDB_LOGO: &[u8] = include_bytes!("../../../resources/images/imdb_logo.png");
static WATERMARK: &[u8] = include_bytes!("../../../resources/images/logo_text.png");

// Branded design fonts
static FONT_IBM_PLEX_MEDIUM: &[u8] =
    include_bytes!("../../../resources/fonts/IBMPlexSans-Medium.ttf");
static FONT_IBM_PLEX_BOLD: &[u8] = include_bytes!("../../../resources/fonts/IBMPlexSans-Bold.ttf");
static FONT_ARCHIVO_BLACK: &[u8] = include_bytes!("../../../resources/fonts/Archivo-Black.ttf");
static FONT_ARCHIVO_EXTRABOLD: &[u8] =
    include_bytes!("../../../resources/fonts/Archivo-ExtraBold.ttf");

// Bundled Unicode fallback fonts (Cyrillic/Greek/Latin + Arabic/Persian/Urdu)
static FONT_NOTO_SANS_BOLD: &[u8] = include_bytes!("../../../resources/fonts/NotoSans-Bold.ttf");
static FONT_NOTO_SANS_ARABIC_BOLD: &[u8] =
    include_bytes!("../../../resources/fonts/NotoSansArabic-Bold.ttf");

// ─── Thread-local text system ─────────────────────────────────────────────────
//
// One FontSystem + SwashCache per spawn_blocking thread; initialised on first
// use.  FontSystem::new() also loads system fonts, giving automatic CJK
// fallback when e.g. fonts-noto-cjk is installed in the container.

thread_local! {
    static TL_TEXT: RefCell<(FontSystem, SwashCache)> = RefCell::new({
        let mut fs = FontSystem::new();
        // Branded fonts first — fontdb picks the first matching family/weight.
        fs.db_mut().load_font_data(FONT_IBM_PLEX_MEDIUM.to_vec());
        fs.db_mut().load_font_data(FONT_IBM_PLEX_BOLD.to_vec());
        fs.db_mut().load_font_data(FONT_ARCHIVO_BLACK.to_vec());
        fs.db_mut().load_font_data(FONT_ARCHIVO_EXTRABOLD.to_vec());
        // Bundled Unicode fallbacks — cover Cyrillic, Greek, Arabic, etc.
        fs.db_mut().load_font_data(FONT_NOTO_SANS_BOLD.to_vec());
        fs.db_mut().load_font_data(FONT_NOTO_SANS_ARABIC_BOLD.to_vec());
        (fs, SwashCache::new())
    });
}

// ─── Font spec ────────────────────────────────────────────────────────────────

#[derive(Clone, Copy)]
enum Font {
    IbmPlexMedium,
    IbmPlexBold,
    ArchivoBlack,
    ArchivoExtraBold,
}

impl Font {
    fn attrs(self) -> Attrs<'static> {
        match self {
            Self::IbmPlexMedium => Attrs::new()
                .family(Family::Name("IBM Plex Sans"))
                .weight(Weight::MEDIUM),
            Self::IbmPlexBold => Attrs::new()
                .family(Family::Name("IBM Plex Sans"))
                .weight(Weight::BOLD),
            Self::ArchivoBlack => Attrs::new()
                .family(Family::Name("Archivo Black"))
                .weight(Weight::BLACK),
            Self::ArchivoExtraBold => Attrs::new()
                .family(Family::Name("Archivo"))
                .weight(Weight::EXTRA_BOLD),
        }
    }
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
/// CPU-bound; call from `tokio::task::spawn_blocking`.
pub fn annotate(
    jpeg_bytes: &[u8],
    params: &AnnotateParams,
) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    let img = image::load_from_memory(jpeg_bytes)?.resize_exact(300, 450, FilterType::Lanczos3);
    let mut canvas = img.to_rgba8();

    if let Some(rating) = params.imdb_rating {
        add_imdb_badge(&mut canvas, rating);
    }
    add_watermark(&mut canvas);
    if params.is_add_title
        && let Some(ref t) = params.title
            && !t.is_empty() {
                add_title(&mut canvas, t);
            }

    let rgb = DynamicImage::ImageRgba8(canvas).to_rgb8();
    let mut buf = Cursor::new(Vec::new());
    DynamicImage::ImageRgb8(rgb).write_to(&mut buf, image::ImageFormat::Jpeg)?;
    Ok(buf.into_inner())
}

/// Generate a placeholder poster (300×450 JPEG) for media without artwork.
///
/// Deterministic: same title+type always produces the same poster.
/// CPU-bound; call from `tokio::task::spawn_blocking`.
pub fn generate_placeholder(
    title: &str,
    media_type: &str,
    year: Option<i32>,
) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    const W: u32 = 300;
    const H: u32 = 450;

    // ── Colour palette (FNV hash of title → hue) ────────────────────────────
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
    let accent_rgba = Rgba([accent.0, accent.1, accent.2, 255]);

    // ── 4. Oversized ghost monogram (bottom-right bleed, 7% white) ───────────
    let ghost_size = 230.0f32;
    let (ghost_w, ghost_h) = measure_text(&initials, Font::ArchivoBlack, ghost_size, None);
    let ghost_x = W as i32 - ghost_w as i32 + 14;
    let ghost_y = H as i32 - ghost_h as i32 + 46;
    draw_text_plain(
        &mut canvas,
        ghost_x,
        ghost_y,
        &initials,
        Font::ArchivoBlack,
        ghost_size,
        None,
        None,
        Rgba([255, 255, 255, 18]),
    );

    // ── 5. Top bar: accent bar + kicker (left) | "MediaFusion" (right) ───────
    let kicker = media_type_kicker(media_type);
    let label_size = 14.0f32;
    let top_y = 16i32;

    // Accent bar: 18×3 px
    for px in 22u32..40 {
        for py in 24u32..27 {
            if px < W && py < H {
                canvas.put_pixel(px, py, accent_rgba);
            }
        }
    }

    draw_text_plain(
        &mut canvas,
        44,
        top_y,
        kicker,
        Font::IbmPlexBold,
        label_size,
        None,
        None,
        Rgba([255, 255, 255, 209]),
    );

    // "Media" (dimmed) + "Fusion" (accent colour)
    let media_w = measure_text("Media", Font::IbmPlexBold, label_size, None).0;
    let fusion_w = measure_text("Fusion", Font::IbmPlexBold, label_size, None).0;
    let mf_x = W as i32 - 22 - media_w as i32 - fusion_w as i32;
    draw_text_plain(
        &mut canvas,
        mf_x,
        top_y,
        "Media",
        Font::IbmPlexBold,
        label_size,
        None,
        None,
        Rgba([255, 255, 255, 107]),
    );
    draw_text_plain(
        &mut canvas,
        mf_x + media_w as i32,
        top_y,
        "Fusion",
        Font::IbmPlexBold,
        label_size,
        None,
        None,
        accent_rgba,
    );

    // ── 7. Title anchored to bottom-left ─────────────────────────────────────
    let white = Rgba([255u8, 255, 255, 255]);
    // Font size by character count (Unicode-aware)
    let title_size: f32 = match display.chars().count() {
        0..=9 => 42.0,
        10..=16 => 34.0,
        17..=26 => 27.0,
        _ => 22.0,
    };
    let title_max_w = (W - 44) as f32;
    let (_, title_h) = measure_text(
        &display,
        Font::ArchivoExtraBold,
        title_size,
        Some(title_max_w),
    );

    let has_year = year.is_some();
    let has_chip = season.is_some() && episode.is_some();
    let meta_row_h = if has_year || has_chip { 26i32 } else { 0 };
    let block_h = title_h as i32 + meta_row_h;
    let ty = H as i32 - 22 - block_h;

    draw_text_plain(
        &mut canvas,
        22,
        ty,
        &display,
        Font::ArchivoExtraBold,
        title_size,
        Some(title_max_w),
        None,
        white,
    );

    // ── 8. Metadata row: year + S·E chip ─────────────────────────────────────
    if has_year || has_chip {
        let meta_y = ty + title_h as i32 + 13;
        let mut mx = 22i32;

        if let Some(y) = year {
            let ys = y.to_string();
            draw_text_plain(
                &mut canvas,
                mx,
                meta_y + 2,
                &ys,
                Font::IbmPlexBold,
                11.0,
                None,
                None,
                Rgba([255, 255, 255, 168]),
            );
            mx += measure_text(&ys, Font::IbmPlexBold, 11.0, None).0 as i32 + 9;
        }

        if has_chip {
            let chip_str = format!("S{:02} \u{00B7} E{:02}", season.unwrap(), episode.unwrap());
            let chip_tw = measure_text(&chip_str, Font::IbmPlexBold, 9.5, None).0 as i32;
            let pad = 8i32;
            let chip_w = chip_tw + pad * 2;
            let chip_h = 16i32;

            blend_rect(
                &mut canvas,
                mx,
                meta_y,
                chip_w,
                chip_h,
                accent.0,
                accent.1,
                accent.2,
                31,
            );
            outline_rect(
                &mut canvas,
                mx,
                meta_y,
                chip_w,
                chip_h,
                accent.0,
                accent.1,
                accent.2,
                140,
            );
            draw_text_plain(
                &mut canvas,
                mx + pad,
                meta_y + (chip_h - 10) / 2,
                &chip_str,
                Font::IbmPlexBold,
                9.5,
                None,
                None,
                accent_rgba,
            );
        }
    }

    let rgb = DynamicImage::ImageRgba8(canvas).to_rgb8();
    let mut out = Cursor::new(Vec::new());
    DynamicImage::ImageRgb8(rgb).write_to(&mut out, image::ImageFormat::Jpeg)?;
    Ok(out.into_inner())
}

// ─── Core text primitives ─────────────────────────────────────────────────────

/// Alpha-composite a single pixel from a cosmic-text draw callback onto the canvas.
#[inline]
fn composite(canvas: &mut RgbaImage, x: i32, y: i32, src: CosColor) {
    if x < 0 || y < 0 {
        return;
    }
    let (cw, ch) = canvas.dimensions();
    if x as u32 >= cw || y as u32 >= ch {
        return;
    }
    let a = src.a() as f32 / 255.0;
    if a <= 0.0 {
        return;
    }
    let p = canvas.get_pixel_mut(x as u32, y as u32);
    p[0] = (src.r() as f32 * a + p[0] as f32 * (1.0 - a)) as u8;
    p[1] = (src.g() as f32 * a + p[1] as f32 * (1.0 - a)) as u8;
    p[2] = (src.b() as f32 * a + p[2] as f32 * (1.0 - a)) as u8;
    p[3] = ((a + p[3] as f32 / 255.0 * (1.0 - a)) * 255.0) as u8;
}

/// Draw shaped text at `(ox, oy)` with an optional 2-pixel outline for contrast.
///
/// When `max_width` is `Some(w)`, cosmic-text wraps at that width.
/// `align` controls horizontal alignment within that width.
fn draw_text_plain(
    canvas: &mut RgbaImage,
    ox: i32,
    oy: i32,
    text: &str,
    font: Font,
    size: f32,
    max_width: Option<f32>,
    align: Option<Align>,
    color: Rgba<u8>,
) {
    TL_TEXT.with_borrow_mut(|(fs, cache)| {
        let attrs = font.attrs();
        let mut buf = Buffer::new(fs, Metrics::new(size, size * 1.2));
        buf.set_size(max_width, None);
        buf.set_text(text, &attrs, Shaping::Advanced, align);
        buf.shape_until_scroll(fs, false);

        // cosmic-text 0.19 ignores the input Color's alpha in its draw callback —
        // coverage-based alpha only. Apply desired opacity by scaling c.a() ourselves.
        let opacity = color[3] as f32 / 255.0;
        let cf = CosColor::rgba(color[0], color[1], color[2], 255);
        buf.draw(fs, cache, cf, |px, py, _w, _h, c| {
            let a = (c.a() as f32 * opacity) as u8;
            composite(
                canvas,
                ox + px,
                oy + py,
                CosColor::rgba(c.r(), c.g(), c.b(), a),
            );
        });
    });
}

/// Draw shaped text with a 2-pixel pixel-outline for contrast (used on title overlays).
fn draw_text_outlined(
    canvas: &mut RgbaImage,
    ox: i32,
    oy: i32,
    text: &str,
    font: Font,
    size: f32,
    max_width: Option<f32>,
    align: Option<Align>,
    fill: Rgba<u8>,
    outline: Rgba<u8>,
) {
    TL_TEXT.with_borrow_mut(|(fs, cache)| {
        let attrs = font.attrs();
        let mut buf = Buffer::new(fs, Metrics::new(size, size * 1.2));
        buf.set_size(max_width, None);
        buf.set_text(text, &attrs, Shaping::Advanced, align);
        buf.shape_until_scroll(fs, false);

        // Outline pass: draw at each ±2px offset
        let outline_opacity = outline[3] as f32 / 255.0;
        let co = CosColor::rgba(outline[0], outline[1], outline[2], 255);
        const OW: i32 = 2;
        for dx in -OW..=OW {
            for dy in -OW..=OW {
                if dx == 0 && dy == 0 {
                    continue;
                }
                buf.draw(fs, cache, co, |px, py, _w, _h, c| {
                    let a = (c.a() as f32 * outline_opacity) as u8;
                    composite(
                        canvas,
                        ox + px + dx,
                        oy + py + dy,
                        CosColor::rgba(c.r(), c.g(), c.b(), a),
                    );
                });
            }
        }

        // Fill pass
        let fill_opacity = fill[3] as f32 / 255.0;
        let cf = CosColor::rgba(fill[0], fill[1], fill[2], 255);
        buf.draw(fs, cache, cf, |px, py, _w, _h, c| {
            let a = (c.a() as f32 * fill_opacity) as u8;
            composite(
                canvas,
                ox + px,
                oy + py,
                CosColor::rgba(c.r(), c.g(), c.b(), a),
            );
        });
    });
}

/// Measure the pixel bounding box of shaped, word-wrapped text.
/// Returns `(width, height)`.
fn measure_text(text: &str, font: Font, size: f32, max_width: Option<f32>) -> (f32, f32) {
    TL_TEXT.with_borrow_mut(|(fs, _)| {
        let attrs = font.attrs();
        let mut buf = Buffer::new(fs, Metrics::new(size, size * 1.2));
        buf.set_size(max_width, None);
        buf.set_text(text, &attrs, Shaping::Advanced, None);
        buf.shape_until_scroll(fs, false);

        let mut w = 0.0f32;
        let mut h = 0.0f32;
        for run in buf.layout_runs() {
            w = w.max(run.line_w);
            h += run.line_height;
        }
        (w, h)
    })
}

/// Count the number of visual lines after wrapping at `max_width`.
fn line_count(text: &str, font: Font, size: f32, max_width: f32) -> usize {
    TL_TEXT.with_borrow_mut(|(fs, _)| {
        let attrs = font.attrs();
        let mut buf = Buffer::new(fs, Metrics::new(size, size * 1.2));
        buf.set_size(Some(max_width), None);
        buf.set_text(text, &attrs, Shaping::Advanced, None);
        buf.shape_until_scroll(fs, false);
        buf.layout_runs().count()
    })
}

/// Binary search for the largest font size ≥ `min` at which `text` fits in
/// `max_lines` lines when wrapped at `max_width`.
fn fit_font_size(
    text: &str,
    font: Font,
    max_width: f32,
    max_lines: usize,
    initial: f32,
    min: f32,
) -> f32 {
    let mut size = initial;
    while size > min {
        if line_count(text, font, size, max_width) <= max_lines {
            return size;
        }
        size -= 1.0;
    }
    min
}

// ─── IMDb badge ───────────────────────────────────────────────────────────────

fn add_imdb_badge(canvas: &mut RgbaImage, rating: f32) {
    let size = 20.0f32;
    let margin = 10i32;
    let pad = 5i32;
    let rating_text = format!(" {:.1}/10", rating);

    let (text_w, text_h) = measure_text(&rating_text, Font::IbmPlexMedium, size, None);
    let (tw, th) = (text_w as i32, text_h as i32);

    let imdb_logo = image::load_from_memory(IMDB_LOGO).ok().map(|img| {
        let aspect = img.width() as f32 / img.height() as f32;
        let lw = (th as f32 * aspect).max(1.0) as u32;
        img.resize_exact(lw, th.max(1) as u32, FilterType::Lanczos3)
            .to_rgba8()
    });

    let logo_w = imdb_logo.as_ref().map(|l| l.width() as i32).unwrap_or(0);
    let total_w = logo_w + tw + 2 * pad;
    let total_h = th + 2 * pad;

    let rect_x = margin;
    let rect_y = canvas.height() as i32 - margin - total_h;
    if rect_y < 0 {
        return;
    }

    draw_filled_rect_mut(
        canvas,
        Rect::at(rect_x, rect_y).of_size(total_w as u32, total_h as u32),
        Rgba([0u8, 0, 0, 176]),
    );

    if let Some(logo) = imdb_logo {
        imageops::overlay(canvas, &logo, (rect_x + pad) as i64, (rect_y + pad) as i64);
    }

    draw_text_plain(
        canvas,
        rect_x + pad + logo_w,
        rect_y,
        &rating_text,
        Font::IbmPlexMedium,
        size,
        None,
        None,
        Rgba([0xF5u8, 0xC5, 0x18, 0xFF]),
    );
}

// ─── Watermark ────────────────────────────────────────────────────────────────

fn add_watermark(canvas: &mut RgbaImage) {
    let Ok(wm) = image::load_from_memory(WATERMARK) else {
        return;
    };
    let new_w = canvas.width() / 2;
    let aspect = wm.width() as f32 / wm.height() as f32;
    let new_h = ((new_w as f32) / aspect).max(1.0) as u32;
    let wm = wm
        .resize_exact(new_w, new_h, FilterType::Lanczos3)
        .to_rgba8();
    let x = canvas.width() as i64 - wm.width() as i64 - 10;
    imageops::overlay(canvas, &wm, x, 10);
}

// ─── Title overlay ────────────────────────────────────────────────────────────

fn add_title(canvas: &mut RgbaImage, title: &str) {
    let max_w = canvas.width() as f32 - 20.0;
    let size = fit_font_size(title, Font::IbmPlexBold, max_w, 3, 50.0, 20.0);
    let (_, block_h) = measure_text(title, Font::IbmPlexBold, size, Some(max_w));

    let y_start = ((canvas.height() as f32 - block_h) / 2.0).max(0.0) as i32;

    let sy = (y_start as f32 - block_h / 2.0).clamp(0.0, canvas.height() as f32 - 1.0) as u32;
    let ey = (y_start as f32 + block_h * 1.5).clamp(0.0, canvas.height() as f32 - 1.0) as u32;
    let (text_color, outline_color) = text_color_for_region(canvas, 0, sy, canvas.width(), ey);

    draw_text_outlined(
        canvas,
        10,
        y_start,
        title,
        Font::IbmPlexBold,
        size,
        Some(max_w),
        Some(Align::Center),
        text_color,
        outline_color,
    );
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
                if e_end > e_start
                    && let (Ok(s), Ok(e)) = (
                        upper[s_start..s_end].parse::<i32>(),
                        upper[e_start..e_end].parse::<i32>(),
                    ) {
                        return (Some(s), Some(e));
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

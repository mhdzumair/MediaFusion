use fancy_regex::{Regex, RegexBuilder};
/// Mirrors `clean_title()` from PTT/parse.py exactly.
use once_cell::sync::Lazy;

// Non-English character ranges (same as NON_ENGLISH_CHARS in parse.py).
// Regular Rust string — \u{XXXX} are real Unicode chars embedded in the char class.
const NON_EN: &str = "\u{3040}-\u{30ff}\u{3400}-\u{4dbf}\u{4e00}-\u{9fff}\u{f900}-\u{faff}\
     \u{ff66}-\u{ff9f}\u{0400}-\u{04ff}\u{0600}-\u{06ff}\u{0750}-\u{077f}\
     \u{0c80}-\u{0cff}\u{0d00}-\u{0d7f}\u{0e00}-\u{0e7f}";

fn c(p: &str) -> Regex {
    Regex::new(p).unwrap_or_else(|e| panic!("bad clean regex `{p}`: {e}"))
}

fn ci(p: &str) -> Regex {
    RegexBuilder::new(p)
        .case_insensitive(true)
        .build()
        .unwrap_or_else(|e| panic!("bad clean regex (i) `{p}`: {e}"))
}

static MOVIE_RE: Lazy<Regex> = Lazy::new(|| ci(r"[\[(]movie[)\u{005D}]"));

static RUSSIAN_CAST_RE: Lazy<Regex> = Lazy::new(|| {
    // second branch: bounded lookbehind (max 200 chars) — PCRE2 10.40+
    c(r"\([^)]*[\u{0400}-\u{04ff}][^)]*\)$|(?<=\/[^(]{0,200})\(.*\)$")
});

static ALT_TITLES_RE: Lazy<Regex> = Lazy::new(|| {
    let p = format!(
        "[^/|(]*[{NON_EN}][^/|]*[/|]|[/|][^/|(]*[{NON_EN}][^/|]*",
        NON_EN = NON_EN
    );
    c(&p)
});

static NOT_ONLY_NON_EN_RE: Lazy<Regex> = Lazy::new(|| {
    // bounded variable-length lookbehind (max 101 chars < PCRE2's 255 char limit)
    let p = format!(
        "(?<=[a-zA-Z][^{ne}]{{1,100}})[{ne}].*[{ne}]|[{ne}].*[{ne}](?=[^{ne}]{{1,100}}[a-zA-Z])",
        ne = NON_EN
    );
    c(&p)
});

static NOT_ALLOWED_START_END: Lazy<Regex> = Lazy::new(|| {
    // \x{{300b}} in format! → \x{300b} in the pattern (PCRE2 Unicode hex escape)
    let p = format!(
        r"^[^\w{ne}#\[\u{{300b}}\u{{2605}}]+|[ \-:/\\\[|\{{(#$&^]+$",
        ne = NON_EN
    );
    c(&p)
});

static REMAINING_NOT_ALLOWED: Lazy<Regex> = Lazy::new(|| {
    let p = format!(r"^[^\w{ne}#]+|]$", ne = NON_EN);
    c(&p)
});

static REDUNDANT_END: Lazy<Regex> = Lazy::new(|| c(r"[ \-:./\\]+$"));

static PARENS_NO_CONTENT: Lazy<Regex> = Lazy::new(|| c(r"\(\W*\)|\[\W*\]|\{\W*\}"));

// \x{300a} = 《  \x{2605} = ★  \x{300b} = 》
static STAR1_RE: Lazy<Regex> =
    Lazy::new(|| c(r"^[\[\u{300a}\u{2605}].*[\u{005D}\u{300b}\u{2605}][ .]?(.+)"));

static STAR2_RE: Lazy<Regex> =
    Lazy::new(|| c(r"(.+)[ .]?[\[\u{300a}\u{2605}].*[\u{005D}\u{300b}\u{2605}]$"));

static MP3_RE: Lazy<Regex> = Lazy::new(|| ci(r"\bmp3$"));

static SPACING_RE: Lazy<Regex> = Lazy::new(|| c(r"\s+"));

static SPECIAL_CHAR_SPACING: Lazy<Regex> = Lazy::new(|| c(r"[-+_\{\}\[\u{005D}]\W{2,}"));

static DOT_RE: Lazy<Regex> = Lazy::new(|| c(r"\."));

static EMPTY_BRACKETS_RE: Lazy<Regex> = Lazy::new(|| c(r"\(\s*\)|\[\s*\]|\{\s*\}"));

// ── Helpers ───────────────────────────────────────────────────────────────────

fn apply(re: &Regex, src: &str, replacement: &str) -> String {
    let mut out = String::with_capacity(src.len());
    let mut last_end = 0;
    for m in re.find_iter(src).filter_map(|r| r.ok()) {
        out.push_str(&src[last_end..m.start()]);
        out.push_str(replacement);
        last_end = m.end();
    }
    out.push_str(&src[last_end..]);
    out
}

fn apply_cap1(re: &Regex, src: &str) -> String {
    re.captures(src)
        .ok()
        .flatten()
        .and_then(|caps| caps.get(1))
        .map(|m| m.as_str().to_string())
        .unwrap_or_else(|| src.to_string())
}

// ── Public ────────────────────────────────────────────────────────────────────

pub fn clean_title(raw: &str) -> String {
    let mut s = raw.replace('_', " ");

    s = apply(&MOVIE_RE, &s, "");
    s = apply(&NOT_ALLOWED_START_END, &s, "");
    s = apply(&RUSSIAN_CAST_RE, &s, "");

    if STAR1_RE.is_match(&s).unwrap_or(false) {
        s = apply_cap1(&STAR1_RE, &s);
    }
    if STAR2_RE.is_match(&s).unwrap_or(false) {
        s = apply_cap1(&STAR2_RE, &s);
    }

    s = apply(&ALT_TITLES_RE, &s, "");
    s = apply(&NOT_ONLY_NON_EN_RE, &s, "");
    s = apply(&REMAINING_NOT_ALLOWED, &s, "");
    s = apply(&EMPTY_BRACKETS_RE, &s, "");
    s = apply(&MP3_RE, &s, "");
    s = apply(&PARENS_NO_CONTENT, &s, "");
    s = apply(&SPECIAL_CHAR_SPACING, &s, "");

    // Remove brackets if unbalanced
    for (open, close) in [('(', ')'), ('[', ']'), ('{', '}')] {
        let opens = s.chars().filter(|&c| c == open).count();
        let closes = s.chars().filter(|&c| c == close).count();
        if opens != closes {
            s = s.replace([open, close], "");
        }
    }

    // If no spaces but has dots, replace dots with spaces
    if !s.contains(' ') && s.contains('.') {
        s = apply(&DOT_RE, &s, " ");
    }

    s = apply(&REDUNDANT_END, &s, "");
    s = apply(&SPACING_RE, &s, " ");
    s.trim().to_string()
}

/// Mirrors `clean_title()` from PTT/parse.py exactly.
use once_cell::sync::Lazy;
use pcre2::bytes::{Regex, RegexBuilder};

// Non-English character ranges (same as NON_ENGLISH_CHARS in parse.py).
// Regular Rust string — \u{XXXX} are real Unicode chars embedded in the char class.
const NON_EN: &str = "\u{3040}-\u{30ff}\u{3400}-\u{4dbf}\u{4e00}-\u{9fff}\u{f900}-\u{faff}\
     \u{ff66}-\u{ff9f}\u{0400}-\u{04ff}\u{0600}-\u{06ff}\u{0750}-\u{077f}\
     \u{0c80}-\u{0cff}\u{0d00}-\u{0d7f}\u{0e00}-\u{0e7f}";

fn c(p: &str) -> Regex {
    RegexBuilder::new()
        .ucp(true)
        .utf(true)
        .build(p)
        .unwrap_or_else(|e| panic!("bad clean regex `{p}`: {e}"))
}

fn ci(p: &str) -> Regex {
    RegexBuilder::new()
        .caseless(true)
        .ucp(true)
        .utf(true)
        .build(p)
        .unwrap_or_else(|e| panic!("bad clean regex (i) `{p}`: {e}"))
}

static MOVIE_RE: Lazy<Regex> = Lazy::new(|| ci(r"[[(]movie[)\]]"));

static RUSSIAN_CAST_RE: Lazy<Regex> = Lazy::new(|| {
    // second branch: bounded lookbehind (max 200 chars) — PCRE2 10.40+
    c(r"\([^)]*[\x{0400}-\x{04ff}][^)]*\)$|(?<=\/[^(]{0,200})\(.*\)$")
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
        r"^[^\w{ne}#\[\x{{300b}}\x{{2605}}]+|[ \-:/\\[|\{{(#$&^]+$",
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
    Lazy::new(|| c(r"^[[\x{300a}\x{2605}].*[\]\x{300b}\x{2605}][ .]?(.+)"));

static STAR2_RE: Lazy<Regex> =
    Lazy::new(|| c(r"(.+)[ .]?[[\x{300a}\x{2605}].*[\]\x{300b}\x{2605}]$"));

static MP3_RE: Lazy<Regex> = Lazy::new(|| ci(r"\bmp3$"));

static SPACING_RE: Lazy<Regex> = Lazy::new(|| c(r"\s+"));

static SPECIAL_CHAR_SPACING: Lazy<Regex> = Lazy::new(|| c(r"[-+_\{\}\[\]]\W{2,}"));

static DOT_RE: Lazy<Regex> = Lazy::new(|| c(r"\."));

static EMPTY_BRACKETS_RE: Lazy<Regex> = Lazy::new(|| c(r"\(\s*\)|\[\s*\]|\{\s*\}"));

// ── Helpers ───────────────────────────────────────────────────────────────────

fn apply(re: &Regex, src: &str, replacement: &[u8]) -> String {
    let haystack = src.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(haystack.len());
    let mut last_end = 0;
    for m in re.find_iter(haystack).filter_map(|r| r.ok()) {
        out.extend_from_slice(&haystack[last_end..m.start()]);
        out.extend_from_slice(replacement);
        last_end = m.end();
    }
    out.extend_from_slice(&haystack[last_end..]);
    String::from_utf8_lossy(&out).into_owned()
}

fn apply_cap1(re: &Regex, src: &str) -> String {
    re.captures(src.as_bytes())
        .ok()
        .flatten()
        .and_then(|caps| caps.get(1))
        .map(|m| String::from_utf8_lossy(m.as_bytes()).into_owned())
        .unwrap_or_else(|| src.to_string())
}

// ── Public ────────────────────────────────────────────────────────────────────

pub fn clean_title(raw: &str) -> String {
    let mut s = raw.replace('_', " ");

    s = apply(&MOVIE_RE, &s, b"");
    s = apply(&NOT_ALLOWED_START_END, &s, b"");
    s = apply(&RUSSIAN_CAST_RE, &s, b"");

    if STAR1_RE.is_match(s.as_bytes()).unwrap_or(false) {
        s = apply_cap1(&STAR1_RE, &s);
    }
    if STAR2_RE.is_match(s.as_bytes()).unwrap_or(false) {
        s = apply_cap1(&STAR2_RE, &s);
    }

    s = apply(&ALT_TITLES_RE, &s, b"");
    s = apply(&NOT_ONLY_NON_EN_RE, &s, b"");
    s = apply(&REMAINING_NOT_ALLOWED, &s, b"");
    s = apply(&EMPTY_BRACKETS_RE, &s, b"");
    s = apply(&MP3_RE, &s, b"");
    s = apply(&PARENS_NO_CONTENT, &s, b"");
    s = apply(&SPECIAL_CHAR_SPACING, &s, b"");

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
        s = apply(&DOT_RE, &s, b" ");
    }

    s = apply(&REDUNDANT_END, &s, b"");
    s = apply(&SPACING_RE, &s, b" ");
    s.trim().to_string()
}

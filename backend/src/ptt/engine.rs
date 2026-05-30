use std::collections::HashMap;
use std::sync::Arc;

use fancy_regex::{Captures, Regex, RegexBuilder};
use once_cell::sync::OnceCell;

// ── Value types stored in the result map ─────────────────────────────────────

#[derive(Clone, Debug, PartialEq)]
pub enum FieldValue {
    Bool(bool),
    Str(String),
    Int(i32),
    Ints(Vec<i32>),
    Strs(Vec<String>),
}

// ── Handler options ───────────────────────────────────────────────────────────

#[derive(Clone, Debug, Default)]
pub struct Opts {
    pub skip_if_already_found: bool,
    pub skip_from_title: bool,
    pub skip_if_first: bool,
    pub remove: bool,
}

impl Opts {
    pub fn defaults() -> Self {
        Self {
            skip_if_already_found: true,
            ..Default::default()
        }
    }
    pub fn with_skip(mut self, v: bool) -> Self {
        self.skip_if_already_found = v;
        self
    }
    pub fn with_remove(mut self, v: bool) -> Self {
        self.remove = v;
        self
    }
    pub fn with_skip_from_title(mut self, v: bool) -> Self {
        self.skip_from_title = v;
        self
    }
    pub fn with_skip_if_first(mut self, v: bool) -> Self {
        self.skip_if_first = v;
        self
    }
}

// ── Per-match bookkeeping ─────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct MatchInfo {
    pub raw_match: String,
    pub match_index: usize,
}

// ── What a handler returns ────────────────────────────────────────────────────

pub struct HandlerReturn {
    pub raw_match: String,
    pub match_index: usize,
    pub remove: bool,
    pub skip_from_title: bool,
}

// ── Parse context ─────────────────────────────────────────────────────────────

pub struct Ctx {
    pub title: String,
    pub result: HashMap<String, FieldValue>,
    pub matched: HashMap<String, MatchInfo>,
}

// ── Transformer type ─────────────────────────────────────────────────────────

pub type Transformer = Arc<dyn Fn(&str, Option<&FieldValue>) -> Option<FieldValue> + Send + Sync>;

// ── Handler type ──────────────────────────────────────────────────────────────

pub type Handler = Box<dyn Fn(&mut Ctx) -> Option<HandlerReturn> + Send + Sync>;

// ── PCRE2 regex builder helpers ───────────────────────────────────────────────

/// Compile a regex (panics at startup if pattern is invalid).
pub fn compile(pattern: &str) -> Regex {
    Regex::new(pattern).unwrap_or_else(|e| panic!("bad regex `{pattern}`: {e}"))
}

/// Compile a case-insensitive regex.
pub fn compile_i(pattern: &str) -> Regex {
    RegexBuilder::new(pattern)
        .case_insensitive(true)
        .build()
        .unwrap_or_else(|e| panic!("bad regex (i) `{pattern}`: {e}"))
}

// ── Helper: extract text from a PCRE2 match ──────────────────────────────────

fn caps_to_str(caps: &Captures, index: usize) -> Option<String> {
    caps.get(index).map(|m| m.as_str().to_string())
}

// ── Regex used to detect "before-title" bracket content ──────────────────────

fn before_title_re() -> &'static Regex {
    static RE: OnceCell<Regex> = OnceCell::new();
    RE.get_or_init(|| compile(r"^\[([^\[\]]+)\]"))
}

// ── Factory: create a regex-based handler ─────────────────────────────────────

pub fn regex_handler(
    name: &'static str,
    re: Regex,
    transformer: Transformer,
    opts: Opts,
) -> Handler {
    Box::new(move |ctx: &mut Ctx| {
        if opts.skip_if_already_found && ctx.result.contains_key(name) {
            return None;
        }

        let caps = re.captures(&ctx.title).ok()??;
        let full = caps_to_str(&caps, 0)?;
        let raw_match = full.clone();
        let match_start = caps.get(0).map(|m| m.start()).unwrap_or(0);

        // Group 1 if present, else full match
        let clean = caps_to_str(&caps, 1).unwrap_or_else(|| raw_match.clone());

        let existing = ctx.result.get(name);
        let transformed = transformer(clean.trim(), existing)?;

        // skipIfFirst: skip when this match precedes every other known match
        if opts.skip_if_first {
            let other: Vec<_> = ctx
                .matched
                .iter()
                .filter(|(k, _)| k.as_str() != name)
                .collect();
            if !other.is_empty() && other.iter().all(|(_, v)| match_start < v.match_index) {
                return None;
            }
        }

        ctx.matched.entry(name.to_string()).or_insert(MatchInfo {
            raw_match: raw_match.clone(),
            match_index: match_start,
        });
        ctx.result.insert(name.to_string(), transformed);

        // is_before_title: match sits inside the leading `[...]`
        let is_before_title = before_title_re()
            .captures(&ctx.title)
            .ok()
            .flatten()
            .and_then(|caps| caps_to_str(&caps, 1))
            .is_some_and(|bracket_content| bracket_content.contains(&raw_match));

        Some(HandlerReturn {
            raw_match,
            match_index: match_start,
            remove: opts.remove,
            skip_from_title: is_before_title || opts.skip_from_title,
        })
    })
}

// ── The parser ────────────────────────────────────────────────────────────────

pub struct Parser {
    handlers: Vec<Handler>,
}

impl Default for Parser {
    fn default() -> Self {
        Self::new()
    }
}

impl Parser {
    pub fn new() -> Self {
        Self { handlers: vec![] }
    }

    pub fn add(&mut self, name: &'static str, re: Regex, tr: Transformer, opts: Opts) {
        self.handlers.push(regex_handler(name, re, tr, opts));
    }

    pub fn add_fn(&mut self, h: Handler) {
        self.handlers.push(h);
    }

    pub fn parse_raw(&self, title: &str) -> HashMap<String, FieldValue> {
        let initial = title.replace('_', " ");
        let mut ctx = Ctx {
            title: initial,
            result: HashMap::new(),
            matched: HashMap::new(),
        };
        let mut end_of_title = ctx.title.len();

        for handler in &self.handlers {
            let Some(ret) = handler(&mut ctx) else {
                continue;
            };
            let idx = ret.match_index;
            let raw_len = ret.raw_match.len();

            if ret.remove {
                let start = ctx.title.floor_char_boundary(idx.min(ctx.title.len()));
                let end = ctx.title.floor_char_boundary((idx + raw_len).min(ctx.title.len()));
                ctx.title = format!("{}{}", &ctx.title[..start], &ctx.title[end..]);
            }

            if !ret.skip_from_title && idx > 1 && idx < end_of_title {
                end_of_title = idx;
            }

            if ret.remove && ret.skip_from_title && idx < end_of_title {
                end_of_title = end_of_title.saturating_sub(raw_len);
            }
        }

        ctx.result
            .entry("episodes".into())
            .or_insert(FieldValue::Ints(vec![]));
        ctx.result
            .entry("seasons".into())
            .or_insert(FieldValue::Ints(vec![]));
        ctx.result
            .entry("languages".into())
            .or_insert(FieldValue::Strs(vec![]));

        let title_str = &ctx.title[..ctx.title.floor_char_boundary(end_of_title.min(ctx.title.len()))];
        ctx.result.insert(
            "title".into(),
            FieldValue::Str(super::clean::clean_title(title_str)),
        );

        ctx.result
    }
}

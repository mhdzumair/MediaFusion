/// Mirrors PTT/transformers.py
use once_cell::sync::OnceCell;
use pcre2::bytes::Regex;

use super::engine::{FieldValue, Transformer};

// ── Simple transformers ───────────────────────────────────────────────────────

pub fn tr_none(input: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    Some(FieldValue::Str(input.to_string()))
}

pub fn tr_boolean(_: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    Some(FieldValue::Bool(true))
}

pub fn tr_lowercase(input: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    Some(FieldValue::Str(input.to_lowercase()))
}

pub fn tr_uppercase(input: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    Some(FieldValue::Str(input.to_uppercase()))
}

pub fn tr_integer(input: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    let digits: String = input.chars().filter(|c| c.is_ascii_digit()).collect();
    digits.parse::<i32>().ok().map(FieldValue::Int)
}

pub fn tr_first_integer(input: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    static RE: OnceCell<Regex> = OnceCell::new();
    let re = RE.get_or_init(|| Regex::new(r"\d+").unwrap());
    re.find(input.as_bytes())
        .ok()
        .flatten()
        .and_then(|m| std::str::from_utf8(m.as_bytes()).ok())
        .and_then(|s| s.parse::<i32>().ok())
        .map(FieldValue::Int)
}

/// Parse a range string → Vec<i32>.
///  "1-3"     → [1,2,3]
///  "s1,s2"   → [1,2]  (via digit extraction)
///  "5"       → [5]
pub fn tr_range_func(input: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    static RE: OnceCell<Regex> = OnceCell::new();
    let re = RE.get_or_init(|| Regex::new(r"\d+").unwrap());
    let nums: Vec<i32> = re
        .find_iter(input.as_bytes())
        .filter_map(|r| r.ok())
        .filter_map(|m| std::str::from_utf8(m.as_bytes()).ok().and_then(|s| s.parse().ok()))
        .collect();

    match nums.len() {
        0 => None,
        1 => Some(FieldValue::Ints(nums)),
        2 if nums[0] < nums[1] => {
            Some(FieldValue::Ints((nums[0]..=nums[1]).collect()))
        }
        n if n > 2 && nums.windows(2).all(|w| w[0] + 1 == w[1]) => {
            Some(FieldValue::Ints(nums))
        }
        _ => Some(FieldValue::Ints(nums)),
    }
}

/// "16 of 26" → [1..=16]  (lower-bound becomes the range end)
pub fn tr_range_x_of_y(input: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    static RE: OnceCell<Regex> = OnceCell::new();
    let re = RE.get_or_init(|| Regex::new(r"\d+").unwrap());
    let nums: Vec<i32> = re
        .find_iter(input.as_bytes())
        .filter_map(|r| r.ok())
        .filter_map(|m| std::str::from_utf8(m.as_bytes()).ok().and_then(|s| s.parse().ok()))
        .collect();
    if nums.len() != 1 {
        return None;
    }
    Some(FieldValue::Ints((1..=nums[0]).collect()))
}

/// Normalise resolution strings to canonical form.
pub fn tr_transform_resolution(input: &str, _: Option<&FieldValue>) -> Option<FieldValue> {
    let lower = input.to_lowercase();
    let out = if lower.contains("2160") || lower.contains("4k") {
        "2160p"
    } else if lower.contains("1440") || lower.contains("2k") {
        "1440p"
    } else if lower.contains("1080") {
        "1080p"
    } else if lower.contains("720") {
        "720p"
    } else if lower.contains("480") {
        "480p"
    } else if lower.contains("360") {
        "360p"
    } else if lower.contains("240") {
        "240p"
    } else {
        return Some(FieldValue::Str(lower));
    };
    Some(FieldValue::Str(out.to_string()))
}

// ── Higher-order transformer factories ───────────────────────────────────────

/// Return a fixed string value, substituting `$1` with the matched text.
pub fn value(val: &'static str) -> Transformer {
    std::sync::Arc::new(move |input: &str, _: Option<&FieldValue>| {
        Some(FieldValue::Str(val.replace("$1", input)))
    })
}

/// Wrap the result of `chain` in a single-element Vec.
pub fn array(chain: Transformer) -> Transformer {
    std::sync::Arc::new(move |input: &str, _: Option<&FieldValue>| {
        match chain(input, None)? {
            FieldValue::Int(n) => Some(FieldValue::Ints(vec![n])),
            FieldValue::Str(s) => Some(FieldValue::Strs(vec![s])),
            other => Some(other),
        }
    })
}

/// Append unique string values returned by `chain` to an existing Strs list.
pub fn uniq_concat(chain: Transformer) -> Transformer {
    std::sync::Arc::new(move |input: &str, existing: Option<&FieldValue>| {
        let new_val = chain(input, None)?;
        let new_str = match &new_val {
            FieldValue::Str(s) => s.clone(),
            _ => return None,
        };
        let mut list: Vec<String> = match existing {
            Some(FieldValue::Strs(v)) => v.clone(),
            _ => vec![],
        };
        if !list.contains(&new_str) {
            list.push(new_str);
        }
        Some(FieldValue::Strs(list))
    })
}

// ── Convenience wrappers to use simple fns as Transformer Arcs ───────────────

pub fn arc(f: fn(&str, Option<&FieldValue>) -> Option<FieldValue>) -> Transformer {
    std::sync::Arc::new(f)
}

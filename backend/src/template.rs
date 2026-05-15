/// Stream template engine — compatible with the Python `utils/template_engine.py` syntax.
///
/// Syntax:
///   {var.path}                    — variable lookup
///   {var.path|modifier}           — with modifier
///   {var.path|mod(arg)}           — modifier with argument
///   {if condition}...{/if}        — conditional
///   {if cond}...{elif cond}...{else}...{/if}
///
/// Modifiers: bytes, time, upper, lower, title, first, last, join(sep), truncate(n), replace(a,b), escape
/// Operators in conditions: =, !=, >, <, >=, <=, ~, $, ^  plus `and`, `or`, `not`
use serde_json::Value;

const MAX_TEMPLATE_LEN: usize = 10_000;

// ─── Public API ───────────────────────────────────────────────────────────────

pub fn render(template: &str, context: &Value) -> String {
    if template.len() > MAX_TEMPLATE_LEN {
        return String::new();
    }
    let nodes = parse(template);
    let raw = render_nodes(&nodes, context);
    // Remove blank lines (mirrors Python's cleaned_lines logic)
    raw.lines()
        .filter(|l| !l.trim().is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

// ─── AST ──────────────────────────────────────────────────────────────────────

#[derive(Debug)]
enum Node {
    Text(String),
    Var {
        path: String,
        modifiers: Vec<(String, Option<String>)>,
    },
    If {
        condition: String,
        true_branch: Vec<Node>,
        elif_branches: Vec<(String, Vec<Node>)>,
        false_branch: Vec<Node>,
    },
}

// ─── Parser ───────────────────────────────────────────────────────────────────

fn parse(template: &str) -> Vec<Node> {
    let mut pos = 0;
    let bytes = template.as_bytes();
    let mut nodes = Vec::new();
    parse_block(bytes, &mut pos, &mut nodes, false);
    nodes
}

fn parse_block(bytes: &[u8], pos: &mut usize, out: &mut Vec<Node>, inside_if: bool) {
    let n = bytes.len();
    loop {
        if *pos >= n {
            break;
        }
        if bytes[*pos] != b'{' {
            let start = *pos;
            while *pos < n && bytes[*pos] != b'{' {
                *pos += 1;
            }
            if *pos > start {
                out.push(Node::Text(
                    String::from_utf8_lossy(&bytes[start..*pos]).into_owned(),
                ));
            }
            continue;
        }
        let Some(close) = find_close(bytes, *pos) else {
            out.push(Node::Text("{".to_string()));
            *pos += 1;
            continue;
        };
        let inner = std::str::from_utf8(&bytes[*pos + 1..close])
            .unwrap_or("")
            .trim();

        // Stopping tokens: when inside_if=true, break WITHOUT consuming them.
        // The {if}-parsing code is responsible for consuming these markers.
        // When at top-level (inside_if=false), consume and skip stray tokens.
        if inner.eq_ignore_ascii_case("/if")
            || inner.eq_ignore_ascii_case("else")
            || (inner.len() > 5 && inner[..5].eq_ignore_ascii_case("elif "))
        {
            if inside_if {
                break; // leave token in place for caller to consume
            }
            // stray token at top level — consume and skip
            *pos = close + 1;
            continue;
        }

        // {if ...}
        if inner.len() > 3 && inner[..3].eq_ignore_ascii_case("if ") {
            let condition = inner[3..].trim().to_string();
            *pos = close + 1; // consume {if ...}
            let mut true_branch = Vec::new();
            parse_block(bytes, pos, &mut true_branch, true);
            // true_branch stopped at {elif}, {else}, or {/if} — we must consume the stopping token

            let mut elif_branches: Vec<(String, Vec<Node>)> = Vec::new();
            loop {
                if *pos >= n || bytes[*pos] != b'{' {
                    break;
                }
                let Some(c2) = find_close(bytes, *pos) else {
                    break;
                };
                let tag = std::str::from_utf8(&bytes[*pos + 1..c2])
                    .unwrap_or("")
                    .trim();
                if tag.len() > 5 && tag[..5].eq_ignore_ascii_case("elif ") {
                    let elif_cond = tag[5..].trim().to_string();
                    *pos = c2 + 1; // consume {elif ...}
                    let mut elif_body = Vec::new();
                    parse_block(bytes, pos, &mut elif_body, true);
                    elif_branches.push((elif_cond, elif_body));
                } else {
                    break;
                }
            }

            let mut false_branch = Vec::new();
            if *pos < n && bytes[*pos] == b'{' {
                if let Some(c2) = find_close(bytes, *pos) {
                    let tag = std::str::from_utf8(&bytes[*pos + 1..c2])
                        .unwrap_or("")
                        .trim();
                    if tag.eq_ignore_ascii_case("else") {
                        *pos = c2 + 1; // consume {else}
                        parse_block(bytes, pos, &mut false_branch, true);
                    }
                }
            }
            // consume {/if}
            if *pos < n && bytes[*pos] == b'{' {
                if let Some(c2) = find_close(bytes, *pos) {
                    let tag = std::str::from_utf8(&bytes[*pos + 1..c2])
                        .unwrap_or("")
                        .trim();
                    if tag.eq_ignore_ascii_case("/if") {
                        *pos = c2 + 1;
                    }
                }
            }
            out.push(Node::If {
                condition,
                true_branch,
                elif_branches,
                false_branch,
            });
            continue;
        }

        // variable
        if !inner.is_empty()
            && inner
                .chars()
                .next()
                .map(|c| c.is_alphanumeric() || c == '_')
                .unwrap_or(false)
        {
            let (path, modifiers) = parse_var(inner);
            *pos = close + 1;
            out.push(Node::Var { path, modifiers });
            continue;
        }
        // unrecognised — emit as literal text
        out.push(Node::Text(
            String::from_utf8_lossy(&bytes[*pos..=close]).into_owned(),
        ));
        *pos = close + 1;
    }
}

/// Find the closing '}' for an opening '{' at `start`, respecting nesting.
fn find_close(bytes: &[u8], start: usize) -> Option<usize> {
    let mut depth = 0usize;
    let mut i = start;
    while i < bytes.len() {
        match bytes[i] {
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Some(i);
                }
            }
            _ => {}
        }
        i += 1;
    }
    None
}

/// Parse `path|mod1|mod2(arg)` into `(path, modifiers)`.
fn parse_var(inner: &str) -> (String, Vec<(String, Option<String>)>) {
    let parts = smart_split(inner, '|');
    let path = parts[0].trim().to_string();
    let mut modifiers = Vec::new();
    for part in &parts[1..] {
        let part = part.trim();
        if let Some(paren) = part.find('(') {
            if part.ends_with(')') {
                let mod_name = part[..paren].trim().to_lowercase();
                let arg = part[paren + 1..part.len() - 1].trim().to_string();
                modifiers.push((mod_name, Some(arg)));
                continue;
            }
        }
        modifiers.push((part.to_lowercase(), None));
    }
    (path, modifiers)
}

/// Split `s` by `delim`, skipping delimiters inside parentheses or quotes.
fn smart_split(s: &str, delim: char) -> Vec<String> {
    let mut parts = Vec::new();
    let mut cur = String::new();
    let mut depth = 0usize;
    let mut in_sq = false;
    let mut in_dq = false;
    for ch in s.chars() {
        if ch == '\'' && !in_dq {
            in_sq = !in_sq;
            cur.push(ch);
        } else if ch == '"' && !in_sq {
            in_dq = !in_dq;
            cur.push(ch);
        } else if ch == '(' && !in_sq && !in_dq {
            depth += 1;
            cur.push(ch);
        } else if ch == ')' && !in_sq && !in_dq {
            depth = depth.saturating_sub(1);
            cur.push(ch);
        } else if ch == delim && depth == 0 && !in_sq && !in_dq {
            parts.push(cur.clone());
            cur.clear();
        } else {
            cur.push(ch);
        }
    }
    parts.push(cur);
    parts
}

// ─── Renderer ─────────────────────────────────────────────────────────────────

fn render_nodes(nodes: &[Node], ctx: &Value) -> String {
    nodes.iter().map(|n| render_node(n, ctx)).collect()
}

fn render_node(node: &Node, ctx: &Value) -> String {
    match node {
        Node::Text(t) => t.clone(),
        Node::Var { path, modifiers } => {
            let val = get_value(ctx, path);
            apply_modifiers(val.as_ref(), modifiers)
        }
        Node::If {
            condition,
            true_branch,
            elif_branches,
            false_branch,
        } => {
            if eval_condition(condition, ctx) {
                render_nodes(true_branch, ctx)
            } else {
                for (cond, body) in elif_branches {
                    if eval_condition(cond, ctx) {
                        return render_nodes(body, ctx);
                    }
                }
                render_nodes(false_branch, ctx)
            }
        }
    }
}

// ─── Value Lookup ─────────────────────────────────────────────────────────────

fn get_value(ctx: &Value, path: &str) -> Option<Value> {
    let mut cur = ctx;
    for part in path.split('.') {
        if part.starts_with('_') {
            return None;
        }
        match cur {
            Value::Object(map) => {
                cur = map.get(part)?;
            }
            _ => return None,
        }
    }
    Some(cur.clone())
}

fn is_truthy(v: Option<&Value>) -> bool {
    match v {
        None => false,
        Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        Some(Value::String(s)) => !s.trim().is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

// ─── Condition Evaluator ──────────────────────────────────────────────────────

fn eval_condition(cond: &str, ctx: &Value) -> bool {
    let cond = cond.trim();

    // `and` — split first (lower precedence than `or`)
    // Actually Python splits `and` first (short-circuits), then `or`
    // We match Python: `and` has higher precedence than `or`
    // split on ' or ' first → each part must have all ' and ' satisfied
    let or_parts: Vec<&str> = split_logical(cond, " or ");
    if or_parts.len() > 1 {
        return or_parts.iter().any(|p| eval_condition(p, ctx));
    }
    let and_parts: Vec<&str> = split_logical(cond, " and ");
    if and_parts.len() > 1 {
        return and_parts.iter().all(|p| eval_condition(p, ctx));
    }

    // `not`
    if cond.to_lowercase().starts_with("not ") {
        return !eval_condition(&cond[4..], ctx);
    }

    // Comparison operators (longest first)
    for op in &[">=", "<=", "!=", "=", ">", "<", "~", "$", "^"] {
        if let Some(idx) = cond.find(op) {
            // Make sure this isn't inside an operator that was already matched
            let left = cond[..idx].trim();
            let right = cond[idx + op.len()..].trim();
            if !left.is_empty() {
                let lv = resolve_expr(left, ctx);
                let rv = resolve_expr(right, ctx);
                return compare_values(lv.as_ref(), op, rv.as_ref());
            }
        }
    }

    // Truthy check
    is_truthy(get_value(ctx, cond).as_ref())
}

/// Split on a literal `sep` (case-insensitive) at word boundaries only.
fn split_logical<'a>(s: &'a str, sep: &str) -> Vec<&'a str> {
    let mut parts = Vec::new();
    let mut start = 0;
    let lower = s.to_lowercase();
    let sep_lower = sep.to_lowercase();
    let mut i = 0;
    while i + sep.len() <= s.len() {
        if lower[i..].starts_with(&sep_lower) {
            parts.push(s[start..i].trim());
            i += sep.len();
            start = i;
        } else {
            i += 1;
        }
    }
    parts.push(s[start..].trim());
    if parts.len() == 1 && parts[0] == s.trim() {
        return vec![s];
    }
    parts
}

fn resolve_expr(expr: &str, ctx: &Value) -> Option<Value> {
    let expr = expr.trim();
    // quoted string literal
    if (expr.starts_with('"') && expr.ends_with('"'))
        || (expr.starts_with('\'') && expr.ends_with('\''))
    {
        return Some(Value::String(expr[1..expr.len() - 1].to_string()));
    }
    // boolean literals
    if expr.eq_ignore_ascii_case("true") {
        return Some(Value::Bool(true));
    }
    if expr.eq_ignore_ascii_case("false") {
        return Some(Value::Bool(false));
    }
    // number
    if let Ok(n) = expr.parse::<i64>() {
        return Some(Value::Number(n.into()));
    }
    if let Ok(f) = expr.parse::<f64>() {
        return serde_json::Number::from_f64(f).map(Value::Number);
    }
    // variable path (contains '.')
    if expr.contains('.') {
        return get_value(ctx, expr);
    }
    // bare word — try as context key, else treat as string literal
    if let Some(v) = ctx.get(expr) {
        return Some(v.clone());
    }
    Some(Value::String(expr.to_string()))
}

fn compare_values(left: Option<&Value>, op: &str, right: Option<&Value>) -> bool {
    let ls = value_to_str(left);
    let rs = value_to_str(right);
    let ll = ls.to_lowercase();
    let rl = rs.to_lowercase();

    match op {
        "=" => ll == rl,
        "!=" => ll != rl,
        "~" => ll.contains(&rl),
        "$" => ll.starts_with(&rl),
        "^" => ll.ends_with(&rl),
        ">" | "<" | ">=" | "<=" => {
            let lf = ls.parse::<f64>().unwrap_or(0.0);
            let rf = rs.parse::<f64>().unwrap_or(0.0);
            match op {
                ">" => lf > rf,
                "<" => lf < rf,
                ">=" => lf >= rf,
                "<=" => lf <= rf,
                _ => false,
            }
        }
        _ => false,
    }
}

fn value_to_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::Bool(b)) => b.to_string(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(a)) => a
            .iter()
            .map(|x| value_to_str(Some(x)))
            .collect::<Vec<_>>()
            .join(", "),
        Some(Value::Object(_)) => String::new(),
    }
}

// ─── Modifiers ────────────────────────────────────────────────────────────────

fn apply_modifiers(val: Option<&Value>, modifiers: &[(String, Option<String>)]) -> String {
    if val.is_none() || matches!(val, Some(Value::Null)) {
        return String::new();
    }
    let mut current: Value = val.unwrap().clone();
    for (name, arg) in modifiers {
        current = apply_modifier(current, name, arg.as_deref());
    }
    // Final conversion: don't render booleans as true/false
    match &current {
        Value::Bool(_) => String::new(),
        Value::Null => String::new(),
        Value::Array(a) => a
            .iter()
            .map(|x| value_to_str(Some(x)))
            .collect::<Vec<_>>()
            .join(", "),
        other => value_to_str(Some(other)),
    }
}

fn apply_modifier(val: Value, name: &str, arg: Option<&str>) -> Value {
    match name {
        "bytes" => {
            let n = match &val {
                Value::Number(n) => n.as_i64().unwrap_or(0),
                Value::String(s) => s.parse().unwrap_or(0),
                _ => 0,
            };
            Value::String(format_bytes(n))
        }
        "time" => {
            let n = match &val {
                Value::Number(n) => n.as_i64().unwrap_or(0),
                Value::String(s) => s.parse().unwrap_or(0),
                _ => 0,
            };
            Value::String(format_time(n))
        }
        "upper" => Value::String(value_to_str(Some(&val)).to_uppercase()),
        "lower" => Value::String(value_to_str(Some(&val)).to_lowercase()),
        "title" => Value::String(to_title_case(&value_to_str(Some(&val)))),
        "first" => match val {
            Value::Array(a) => a.into_iter().next().unwrap_or(Value::Null),
            _ => val,
        },
        "last" => match val {
            Value::Array(a) => a.into_iter().last().unwrap_or(Value::Null),
            _ => val,
        },
        "join" => {
            let sep = arg
                .map(|a| a.trim_matches(|c| c == '\'' || c == '"'))
                .unwrap_or(", ");
            let s = match &val {
                Value::Array(a) => a
                    .iter()
                    .map(|x| value_to_str(Some(x)))
                    .collect::<Vec<_>>()
                    .join(sep),
                _ => value_to_str(Some(&val)),
            };
            Value::String(s)
        }
        "truncate" => {
            let n: usize = arg.and_then(|a| a.parse().ok()).unwrap_or(50);
            let s = value_to_str(Some(&val));
            Value::String(if s.len() > n {
                format!("{}...", &s[..n])
            } else {
                s
            })
        }
        "replace" => {
            let s = value_to_str(Some(&val));
            if let Some(a) = arg {
                let parts: Vec<&str> = a.splitn(2, ',').collect();
                if parts.len() == 2 {
                    let old = parts[0].trim().trim_matches(|c| c == '\'' || c == '"');
                    let new = parts[1].trim().trim_matches(|c| c == '\'' || c == '"');
                    return Value::String(s.replace(old, new));
                }
            }
            Value::String(s)
        }
        "escape" | "e" => {
            let s = value_to_str(Some(&val));
            Value::String(html_escape(&s))
        }
        _ => val,
    }
}

fn format_bytes(n: i64) -> String {
    if n <= 0 {
        return "0 B".to_string();
    }
    let units = ["B", "KB", "MB", "GB", "TB", "PB"];
    let mut v = n as f64;
    let mut i = 0usize;
    while v >= 1000.0 && i < units.len() - 1 {
        v /= 1000.0;
        i += 1;
    }
    format!("{v:.1} {}", units[i])
}

fn format_time(secs: i64) -> String {
    if secs <= 0 {
        return String::new();
    }
    let h = secs / 3600;
    let m = (secs % 3600) / 60;
    let s = secs % 60;
    if h > 0 {
        format!("{h:02}:{m:02}:{s:02}")
    } else {
        format!("{m:02}:{s:02}")
    }
}

fn to_title_case(s: &str) -> String {
    s.split_whitespace()
        .map(|w| {
            let mut c = w.chars();
            match c.next() {
                None => String::new(),
                Some(f) => f.to_uppercase().to_string() + c.as_str(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#x27;")
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_simple_var() {
        let ctx = json!({"addon": {"name": "MediaFusion"}, "stream": {"resolution": "1080p"}});
        assert_eq!(render("{addon.name}", &ctx), "MediaFusion");
        assert_eq!(render("{stream.resolution}", &ctx), "1080p");
    }

    #[test]
    fn test_if_elif_else() {
        let ctx = json!({"stream": {"type": "torrent"}});
        let t = "{if stream.type = torrent}🧲{elif stream.type = usenet}📰{else}🔗{/if}";
        assert_eq!(render(t, &ctx), "🧲");
        let ctx2 = json!({"stream": {"type": "usenet"}});
        assert_eq!(render(t, &ctx2), "📰");
        let ctx3 = json!({"stream": {"type": "http"}});
        assert_eq!(render(t, &ctx3), "🔗");
    }

    #[test]
    fn test_modifier_bytes() {
        let ctx = json!({"stream": {"size": 4_500_000_000i64}});
        let out = render("{stream.size|bytes}", &ctx);
        assert_eq!(out, "4.5 GB");
    }

    #[test]
    fn test_modifier_join() {
        let ctx = json!({"stream": {"audio_formats": ["AAC", "DTS"]}});
        let out = render("{stream.audio_formats|join('|')}", &ctx);
        assert_eq!(out, "AAC|DTS");
    }

    #[test]
    fn test_nested_if() {
        let ctx =
            json!({"service": {"cached": true, "shortName": "RD"}, "stream": {"type": "torrent"}});
        let t = "{if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{/if}";
        assert_eq!(render(t, &ctx), "🧲 RD ⚡️");
    }
}

"""
Simplified Template Engine

A more readable template syntax that maintains high performance.

Syntax:
    {var.path}                           - Simple variable
    {var.path|modifier}                  - With modifier
    {var.path|mod1|mod2}                 - Chained modifiers
    {if condition}...{else}...{/if}      - Conditional
    {if var.path = value}...{/if}        - Equality check
    {if var.path > 0}...{/if}            - Comparison
    {if var.path}...{/if}                - Exists/truthy check

Modifiers:
    |bytes         - Format as human-readable bytes
    |time          - Format seconds as HH:MM:SS
    |upper         - Uppercase
    |lower         - Lowercase
    |title         - Title case
    |join(sep)     - Join array with separator
    |first         - First element of array
    |last          - Last element of array
    |truncate(n)   - Truncate to n characters
    |replace(a,b)  - Replace a with b

Comparisons in conditions:
    =   - Equals
    !=  - Not equals
    >   - Greater than
    <   - Less than
    >=  - Greater than or equal
    <=  - Less than or equal
    ~   - Contains (string)
    $   - Starts with
    ^   - Ends with

Logical operators:
    {if cond1 and cond2}...{/if}
    {if cond1 or cond2}...{/if}
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# =============================================================================
# SECURITY LIMITS
# =============================================================================

MAX_TEMPLATE_LENGTH = 10_000
MAX_RECURSION_DEPTH = 10
MAX_MODIFIERS_CHAIN = 10

# Forbidden path segments - block access to Python internals
FORBIDDEN_PATHS = frozenset(
    {
        "__class__",
        "__bases__",
        "__mro__",
        "__subclasses__",
        "__init__",
        "__new__",
        "__del__",
        "__dict__",
        "__slots__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
        "__getattribute__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__import__",
        "__call__",
        "__reduce__",
        "__reduce_ex__",
        "__module__",
        "__weakref__",
        "__doc__",
        "__annotations__",
        "__wrapped__",
        "__self__",
        "__func__",
        "_sa_instance_state",  # SQLAlchemy internal
    }
)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def format_bytes(size: int, base: int = 1000) -> str:
    """Format bytes to human readable string."""
    if not size or size == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    value = float(size)
    while value >= base and i < len(units) - 1:
        value /= base
        i += 1
    return f"{value:.1f} {units[i]}"


def format_time(seconds: int) -> str:
    """Format seconds to HH:MM:SS or MM:SS."""
    if not seconds or seconds <= 0:
        return ""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# =============================================================================
# MODIFIERS
# =============================================================================


def escape_html(text: str) -> str:
    """Escape HTML special characters to prevent XSS."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


MODIFIERS: dict[str, Callable] = {
    "bytes": lambda v, _: format_bytes(int(v)) if v else "",
    "time": lambda v, _: format_time(int(v)) if v else "",
    "upper": lambda v, _: str(v).upper() if v else "",
    "lower": lambda v, _: str(v).lower() if v else "",
    "title": lambda v, _: str(v).title() if v else "",
    "first": lambda v, _: v[0] if isinstance(v, list) and v else "",
    "last": lambda v, _: v[-1] if isinstance(v, list) and v else "",
    "reverse": lambda v, _: str(v)[::-1] if isinstance(v, str) else (list(reversed(v)) if isinstance(v, list) else v),
    "length": lambda v, _: len(v) if v else 0,
    "exists": lambda v, _: v is not None and (bool(v) if not isinstance(v, (int, float)) else True),
    "escape": lambda v, _: escape_html(str(v)) if v else "",  # HTML escape
    "e": lambda v, _: escape_html(str(v)) if v else "",  # Short alias for escape
}


# Modifiers with arguments
def join_modifier(v: Any, arg: str) -> str:
    if not v:
        return ""
    # Handle argument - strip outer quotes
    if arg:
        # Remove surrounding quotes (single or double)
        arg = arg.strip()
        if (arg.startswith("'") and arg.endswith("'")) or (arg.startswith('"') and arg.endswith('"')):
            sep = arg[1:-1]
        else:
            sep = arg
    else:
        sep = ", "
    if isinstance(v, list):
        return sep.join(str(x) for x in v)
    return str(v)


def truncate_modifier(v: Any, arg: str) -> str:
    if not v:
        return ""
    try:
        length = int(arg)
        s = str(v)
        return s[:length] + "..." if len(s) > length else s
    except (ValueError, TypeError):
        return str(v)


def replace_modifier(v: Any, arg: str) -> str:
    if not v:
        return ""
    # Parse replace(old, new)
    parts = arg.split(",", 1)
    if len(parts) == 2:
        old = parts[0].strip().strip("'\"")
        new = parts[1].strip().strip("'\"")
        return str(v).replace(old, new)
    return str(v)


MODIFIERS_WITH_ARGS = {
    "join": join_modifier,
    "truncate": truncate_modifier,
    "replace": replace_modifier,
}


# =============================================================================
# TOKEN TYPES
# =============================================================================


@dataclass
class Token:
    """Base token class."""

    pass


@dataclass
class TextToken(Token):
    """Literal text."""

    text: str


@dataclass
class VariableToken(Token):
    """Variable with optional modifiers: {var.path|mod1|mod2}"""

    path: str
    modifiers: List[Tuple[str, Optional[str]]] = field(default_factory=list)


@dataclass
class IfToken(Token):
    """If block start: {if condition}"""

    condition: str


@dataclass
class ElifToken(Token):
    """Elif block: {elif condition}"""

    condition: str


@dataclass
class ElseToken(Token):
    """Else marker: {else}"""

    pass


@dataclass
class EndIfToken(Token):
    """End if block: {/if}"""

    pass


# =============================================================================
# LEXER
# =============================================================================


class Lexer:
    """Tokenize the template string."""

    # Regex patterns
    VARIABLE_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_.]*(?:\|[^}]+)?)\}")
    IF_PATTERN = re.compile(r"\{if\s+(.+?)\}", re.IGNORECASE)
    ELIF_PATTERN = re.compile(r"\{elif\s+(.+?)\}", re.IGNORECASE)
    ELSE_PATTERN = re.compile(r"\{else\}", re.IGNORECASE)
    ENDIF_PATTERN = re.compile(r"\{/if\}", re.IGNORECASE)

    def tokenize(self, template: str) -> List[Token]:
        """Convert template string to list of tokens."""
        tokens: List[Token] = []
        pos = 0

        while pos < len(template):
            # Try to match control structures first (order matters - elif before else)
            if_match = self.IF_PATTERN.match(template, pos)
            elif_match = self.ELIF_PATTERN.match(template, pos)
            else_match = self.ELSE_PATTERN.match(template, pos)
            endif_match = self.ENDIF_PATTERN.match(template, pos)

            if if_match:
                tokens.append(IfToken(condition=if_match.group(1).strip()))
                pos = if_match.end()
            elif elif_match:
                tokens.append(ElifToken(condition=elif_match.group(1).strip()))
                pos = elif_match.end()
            elif else_match:
                tokens.append(ElseToken())
                pos = else_match.end()
            elif endif_match:
                tokens.append(EndIfToken())
                pos = endif_match.end()
            elif template[pos] == "{":
                # Try variable pattern
                var_match = self.VARIABLE_PATTERN.match(template, pos)
                if var_match:
                    content = var_match.group(1)
                    path, modifiers = self._parse_variable(content)
                    tokens.append(VariableToken(path=path, modifiers=modifiers))
                    pos = var_match.end()
                else:
                    # Literal brace
                    tokens.append(TextToken(text="{"))
                    pos += 1
            else:
                # Collect text until next potential token
                end = pos
                while end < len(template) and template[end] != "{":
                    end += 1
                tokens.append(TextToken(text=template[pos:end]))
                pos = end

        return tokens

    def _parse_variable(self, content: str) -> Tuple[str, List[Tuple[str, Optional[str]]]]:
        """Parse variable path and modifiers from {var.path|mod1|mod2(arg)}."""
        # Split on | but not inside quotes or parentheses
        parts = self._smart_split(content, "|")
        path = parts[0].strip()
        modifiers = []

        for mod_str in parts[1:]:
            mod_str = mod_str.strip()
            # Check for modifier with argument: mod(arg)
            paren_match = re.match(r"(\w+)\((.+)\)$", mod_str)
            if paren_match:
                modifiers.append((paren_match.group(1), paren_match.group(2)))
            else:
                modifiers.append((mod_str, None))

        return path, modifiers

    def _smart_split(self, s: str, delimiter: str) -> List[str]:
        """Split string by delimiter, but respect quotes and parentheses."""
        parts = []
        current = []
        depth = 0
        in_single_quote = False
        in_double_quote = False

        for char in s:
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                current.append(char)
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                current.append(char)
            elif char == "(" and not in_single_quote and not in_double_quote:
                depth += 1
                current.append(char)
            elif char == ")" and not in_single_quote and not in_double_quote:
                depth -= 1
                current.append(char)
            elif char == delimiter and depth == 0 and not in_single_quote and not in_double_quote:
                parts.append("".join(current))
                current = []
            else:
                current.append(char)

        if current:
            parts.append("".join(current))

        return parts


# =============================================================================
# PARSER - Build AST
# =============================================================================


@dataclass
class ASTNode:
    """Base AST node."""

    pass


@dataclass
class TextNode(ASTNode):
    """Literal text node."""

    text: str


@dataclass
class VariableNode(ASTNode):
    """Variable node with modifiers."""

    path: str
    modifiers: List[Tuple[str, Optional[str]]]


@dataclass
class ElifBranch:
    """Elif branch with condition and body."""

    condition: str
    body: List[ASTNode]


@dataclass
class IfNode(ASTNode):
    """Conditional node with optional elif branches."""

    condition: str
    true_branch: List[ASTNode]
    elif_branches: List[ElifBranch]
    false_branch: List[ASTNode]


class Parser:
    """Parse tokens into AST."""

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def parse(self) -> List[ASTNode]:
        """Parse all tokens into AST nodes."""
        return self._parse_block()

    def _parse_block(
        self, stop_at_elif: bool = False, stop_at_else: bool = False, stop_at_endif: bool = False
    ) -> List[ASTNode]:
        """Parse a block of tokens until end or control token."""
        nodes: List[ASTNode] = []

        while self.pos < len(self.tokens):
            token = self.tokens[self.pos]

            if isinstance(token, TextToken):
                nodes.append(TextNode(text=token.text))
                self.pos += 1
            elif isinstance(token, VariableToken):
                nodes.append(VariableNode(path=token.path, modifiers=token.modifiers))
                self.pos += 1
            elif isinstance(token, IfToken):
                self.pos += 1  # consume {if}
                true_branch = self._parse_block(stop_at_elif=True, stop_at_else=True, stop_at_endif=True)
                elif_branches: List[ElifBranch] = []
                false_branch: List[ASTNode] = []

                # Process elif branches
                while self.pos < len(self.tokens) and isinstance(self.tokens[self.pos], ElifToken):
                    elif_token = self.tokens[self.pos]
                    self.pos += 1  # consume {elif}
                    elif_body = self._parse_block(stop_at_elif=True, stop_at_else=True, stop_at_endif=True)
                    elif_branches.append(ElifBranch(condition=elif_token.condition, body=elif_body))

                # Check if we stopped at {else}
                if self.pos < len(self.tokens) and isinstance(self.tokens[self.pos], ElseToken):
                    self.pos += 1  # consume {else}
                    false_branch = self._parse_block(stop_at_endif=True)

                # Consume {/if}
                if self.pos < len(self.tokens) and isinstance(self.tokens[self.pos], EndIfToken):
                    self.pos += 1

                nodes.append(
                    IfNode(
                        condition=token.condition,
                        true_branch=true_branch,
                        elif_branches=elif_branches,
                        false_branch=false_branch,
                    )
                )
            elif isinstance(token, ElifToken):
                if stop_at_elif:
                    break
                self.pos += 1
            elif isinstance(token, ElseToken):
                if stop_at_else:
                    break
                self.pos += 1
            elif isinstance(token, EndIfToken):
                if stop_at_endif:
                    break
                self.pos += 1
            else:
                self.pos += 1

        return nodes


# =============================================================================
# CONDITION EVALUATOR
# =============================================================================


class ConditionEvaluator:
    """Evaluate condition strings."""

    # Comparison operators (order matters - longer first)
    OPERATORS = [">=", "<=", "!=", "=", ">", "<", "~", "$", "^"]

    def evaluate(self, condition: str, context: dict) -> bool:
        """Evaluate a condition string against context."""
        condition = condition.strip()

        # Handle logical operators (and, or)
        # Split on ' and ' or ' or ' while preserving the operator
        and_parts = re.split(r"\s+and\s+", condition, flags=re.IGNORECASE)
        if len(and_parts) > 1:
            return all(self.evaluate(part, context) for part in and_parts)

        or_parts = re.split(r"\s+or\s+", condition, flags=re.IGNORECASE)
        if len(or_parts) > 1:
            return any(self.evaluate(part, context) for part in or_parts)

        # Handle negation
        if condition.startswith("not "):
            return not self.evaluate(condition[4:], context)

        # Find comparison operator
        for op in self.OPERATORS:
            if op in condition:
                parts = condition.split(op, 1)
                if len(parts) == 2:
                    left = parts[0].strip()
                    right = parts[1].strip()
                    return self._compare(left, op, right, context)

        # No operator - truthy check
        return self._is_truthy(self._resolve_value(condition, context))

    def _compare(self, left: str, op: str, right: str, context: dict) -> bool:
        """Compare two values with an operator."""
        left_val = self._resolve_value(left, context)
        right_val = self._resolve_value(right, context)

        # String comparison - case insensitive
        left_str = str(left_val).lower() if left_val is not None else ""
        right_str = str(right_val).lower() if right_val is not None else ""

        if op == "=":
            return left_str == right_str
        elif op == "!=":
            return left_str != right_str
        elif op == "~":  # contains
            return right_str in left_str
        elif op == "$":  # starts with
            return left_str.startswith(right_str)
        elif op == "^":  # ends with
            return left_str.endswith(right_str)

        # Numeric comparison
        try:
            left_num = float(left_val) if left_val is not None else 0
            right_num = float(right_val) if right_val is not None else 0

            if op == ">":
                return left_num > right_num
            elif op == "<":
                return left_num < right_num
            elif op == ">=":
                return left_num >= right_num
            elif op == "<=":
                return left_num <= right_num
        except (ValueError, TypeError):
            return False

        return False

    def _resolve_value(self, expr: str, context: dict) -> Any:
        """Resolve a value expression - either a literal or variable path."""
        expr = expr.strip()

        # Check if it's a quoted string literal
        if (expr.startswith('"') and expr.endswith('"')) or (expr.startswith("'") and expr.endswith("'")):
            return expr[1:-1]

        # Check if it's a number
        try:
            if "." in expr and not any(c.isalpha() for c in expr):
                return float(expr)
            elif expr.isdigit() or (expr.startswith("-") and expr[1:].isdigit()):
                return int(expr)
        except ValueError:
            pass

        # Check for boolean literals
        if expr.lower() == "true":
            return True
        if expr.lower() == "false":
            return False

        # Try as variable path (looks like var.path)
        if "." in expr:
            value, found = self._get_value_with_exists(expr, context)
            if found:
                return value  # Return even if None - it exists but is null
            # Variable path not found - return None (not the literal string)
            return None

        # Simple variable name without dots - check if it exists in context
        if expr in context:
            return context[expr]

        # Not a variable path, treat as literal string (for unquoted values like "torrent")
        return expr

    def _get_value_with_exists(self, path: str, context: dict) -> tuple[Any, bool]:
        """Get nested value from context, returning (value, exists)."""
        parts = path.split(".")
        value = context
        for part in parts:
            # Security: Block access to Python internals
            if part in FORBIDDEN_PATHS or part.startswith("_"):
                logger.warning(f"Blocked access to forbidden path: {part}")
                return None, False
            if isinstance(value, dict):
                if part not in value:
                    return None, False
                value = value.get(part)
            else:
                return None, False
        return value, True

    def _get_value(self, path: str, context: dict) -> Any:
        """Get nested value from context using dot notation."""
        parts = path.split(".")
        value = context
        for part in parts:
            # Security: Block access to Python internals
            if part in FORBIDDEN_PATHS or part.startswith("_"):
                logger.warning(f"Blocked access to forbidden path: {part}")
                return None
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
            if value is None:
                return None
        return value

    def _is_truthy(self, value: Any) -> bool:
        """Check if a value is truthy."""
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list):
            return len(value) > 0
        if isinstance(value, (int, float)):
            return value != 0
        return bool(value)


# =============================================================================
# COMPILED TEMPLATE
# =============================================================================


class CompiledTemplate:
    """Pre-compiled template for fast rendering."""

    def __init__(self, ast: List[ASTNode]):
        self.ast = ast
        self.evaluator = ConditionEvaluator()

    def render(self, context: dict) -> str:
        """Render the template with given context."""
        return self._render_nodes(self.ast, context)

    def _render_nodes(self, nodes: List[ASTNode], context: dict) -> str:
        """Render a list of AST nodes."""
        result = []
        for node in nodes:
            result.append(self._render_node(node, context))

        # Join and clean up empty lines
        output = "".join(result)
        lines = output.split("\n")
        cleaned_lines = [line for line in lines if line.strip()]
        return "\n".join(cleaned_lines)

    def _render_node(self, node: ASTNode, context: dict) -> str:
        """Render a single AST node."""
        if isinstance(node, TextNode):
            return node.text

        elif isinstance(node, VariableNode):
            value = self._get_value(node.path, context)
            return self._apply_modifiers(value, node.modifiers)

        elif isinstance(node, IfNode):
            # Check main if condition
            if self.evaluator.evaluate(node.condition, context):
                return self._render_nodes_raw(node.true_branch, context)

            # Check elif branches
            for elif_branch in node.elif_branches:
                if self.evaluator.evaluate(elif_branch.condition, context):
                    return self._render_nodes_raw(elif_branch.body, context)

            # Fall through to else
            return self._render_nodes_raw(node.false_branch, context)

        return ""

    def _render_nodes_raw(self, nodes: List[ASTNode], context: dict) -> str:
        """Render nodes without line cleanup (for conditional branches)."""
        result = []
        for node in nodes:
            result.append(self._render_node(node, context))
        return "".join(result)

    def _get_value(self, path: str, context: dict) -> Any:
        """Get nested value from context."""
        parts = path.split(".")
        value = context
        for part in parts:
            # Security: Block access to Python internals
            if part in FORBIDDEN_PATHS or part.startswith("_"):
                return None
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
            if value is None:
                return None
        return value

    def _apply_modifiers(self, value: Any, modifiers: List[Tuple[str, Optional[str]]]) -> str:
        """Apply modifiers to a value."""
        if value is None:
            return ""

        for mod_name, mod_arg in modifiers:
            mod_name_lower = mod_name.lower()

            if mod_name_lower in MODIFIERS_WITH_ARGS:
                value = MODIFIERS_WITH_ARGS[mod_name_lower](value, mod_arg or "")
            elif mod_name_lower in MODIFIERS:
                value = MODIFIERS[mod_name_lower](value, mod_arg)
            # Unknown modifier - skip

        # Convert final value to string
        if isinstance(value, list):
            return ", ".join(str(x) for x in value)
        if isinstance(value, bool):
            return ""  # Don't render booleans as "True"/"False"
        return str(value) if value is not None else ""


# =============================================================================
# PUBLIC API
# =============================================================================

# Template cache
_template_cache: dict[str, CompiledTemplate] = {}


def compile_template(template: str) -> CompiledTemplate:
    """Compile a template string into a CompiledTemplate."""
    if len(template) > MAX_TEMPLATE_LENGTH:
        raise ValueError(f"Template too long: {len(template)} > {MAX_TEMPLATE_LENGTH}")

    lexer = Lexer()
    tokens = lexer.tokenize(template)

    parser = Parser(tokens)
    ast = parser.parse()

    return CompiledTemplate(ast)


def get_compiled_template(template: str) -> CompiledTemplate:
    """Get or create a compiled template (cached)."""
    if template not in _template_cache:
        _template_cache[template] = compile_template(template)
    return _template_cache[template]


def render_template(template: str, context: dict) -> str:
    """Render a template with the given context."""
    compiled = get_compiled_template(template)
    return compiled.render(context)


def clear_cache():
    """Clear the template cache."""
    _template_cache.clear()


# =============================================================================
# SYNTAX CONVERTER (AIOStreams -> MediaFusion Simplified)
# =============================================================================


def convert_aiostreams_to_mediafusion(aio_template: str) -> str:
    """
    Convert AIOStreams syntax to MediaFusion Simplified syntax.

    Conversions:
        {var::modifier}                    -> {var|modifier}
        {var::mod1::mod2}                  -> {var|mod1|mod2}
        {var::istrue["yes"||"no"]}         -> {if var}yes{else}no{/if}
        {var::isfalse["yes"||"no"]}        -> {if not var}yes{else}no{/if}
        {var::exists["yes"||""]}           -> {if var}yes{/if}
        {var::=value["yes"||"no"]}         -> {if var = value}yes{else}no{/if}
        {var::>0["yes"||"no"]}             -> {if var > 0}yes{else}no{/if}
        {var::join(' ')}                   -> {var|join(' ')}
        Nested conditionals                -> Nested {if}...{/if}

    Args:
        aio_template: Template in AIOStreams syntax

    Returns:
        Template converted to MediaFusion simplified syntax
    """
    if not aio_template:
        return aio_template

    result = aio_template

    # Step 1: Convert conditionals with true/false branches
    # Pattern: {var::check["true_content"||"false_content"]}
    # Handles nested quotes by matching the outermost brackets

    def convert_conditional(match_str: str) -> str:
        """Convert a single conditional expression."""
        # Extract: var_path::check[true||false]
        # Find the variable and check part
        bracket_start = match_str.find("[")
        if bracket_start == -1:
            return match_str

        var_check = match_str[1:bracket_start]  # Remove leading {
        bracket_content = match_str[bracket_start + 1 : -2]  # Remove [ and ]}

        # Split var::check
        parts = var_check.split("::", 1)
        if len(parts) != 2:
            return match_str

        var_path = parts[0]
        check = parts[1]

        # Find the || separator (not inside nested brackets/quotes)
        separator_idx = find_separator(bracket_content)
        if separator_idx == -1:
            return match_str

        true_val = bracket_content[:separator_idx].strip()
        false_val = bracket_content[separator_idx + 2 :].strip()

        # Remove surrounding quotes from true/false values
        true_val = strip_quotes(true_val)
        false_val = strip_quotes(false_val)

        # Convert check to condition
        if check == "istrue" or check == "exists":
            condition = var_path
        elif check == "isfalse":
            condition = f"not {var_path}"
        elif check.startswith("="):
            value = check[1:]
            condition = f"{var_path} = {value}"
        elif check.startswith(">="):
            value = check[2:]
            condition = f"{var_path} >= {value}"
        elif check.startswith("<="):
            value = check[2:]
            condition = f"{var_path} <= {value}"
        elif check.startswith(">"):
            value = check[1:]
            condition = f"{var_path} > {value}"
        elif check.startswith("<"):
            value = check[1:]
            condition = f"{var_path} < {value}"
        elif check.startswith("!="):
            value = check[2:]
            condition = f"{var_path} != {value}"
        elif check.startswith("~"):
            value = check[1:]
            condition = f"{var_path} ~ {value}"
        elif check.startswith("$"):
            value = check[1:]
            condition = f"{var_path} $ {value}"
        elif check.startswith("^"):
            value = check[1:]
            condition = f"{var_path} ^ {value}"
        else:
            condition = var_path

        # Recursively convert any nested AIOStreams syntax in true/false values
        true_val = convert_aiostreams_to_mediafusion(true_val)
        false_val = convert_aiostreams_to_mediafusion(false_val)

        # Build if/else/endif
        if not false_val or false_val in ("", "''", '""'):
            return f"{{if {condition}}}{true_val}{{/if}}"
        else:
            return f"{{if {condition}}}{true_val}{{else}}{false_val}{{/if}}"

    def find_separator(s: str) -> int:
        """Find || separator not inside quotes or brackets."""
        depth = 0
        in_single = False
        in_double = False
        i = 0
        while i < len(s) - 1:
            c = s[i]
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            elif c == "[" and not in_single and not in_double:
                depth += 1
            elif c == "]" and not in_single and not in_double:
                depth -= 1
            elif c == "|" and s[i + 1] == "|" and depth == 0 and not in_single and not in_double:
                return i
            i += 1
        return -1

    def strip_quotes(s: str) -> str:
        """Remove surrounding quotes from a string."""
        s = s.strip()
        if len(s) >= 2:
            if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
                return s[1:-1]
        return s

    def find_conditional_end(template: str, start: int) -> int:
        """Find the end of a conditional expression starting at start."""
        depth = 0
        in_single = False
        in_double = False
        i = start
        while i < len(template):
            c = template[i]
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            elif c == "[" and not in_single and not in_double:
                depth += 1
            elif c == "]" and not in_single and not in_double:
                depth -= 1
                if depth == 0 and i + 1 < len(template) and template[i + 1] == "}":
                    return i + 2
            i += 1
        return -1

    # Process conditionals (patterns with [...||...])
    i = 0
    while i < len(result):
        if result[i] == "{":
            # Check if this is a conditional (contains :: and [)
            end_brace = result.find("}", i)
            if end_brace != -1:
                segment = result[i : end_brace + 1]
                if "::" in segment and "[" in segment:
                    # Find the actual end of this conditional
                    cond_end = find_conditional_end(result, i)
                    if cond_end != -1:
                        old_segment = result[i:cond_end]
                        new_segment = convert_conditional(old_segment)
                        result = result[:i] + new_segment + result[cond_end:]
                        i += len(new_segment)
                        continue
        i += 1

    # Step 2: Convert simple modifiers (:: -> |)
    # Pattern: {var::modifier} or {var::mod1::mod2}
    # But NOT inside conditionals (already converted)
    result = re.sub(
        r"\{([a-zA-Z_][a-zA-Z0-9_.]*)((?:::[a-zA-Z_][a-zA-Z0-9_]*(?:\([^)]*\))?)+)\}",
        lambda m: "{" + m.group(1) + m.group(2).replace("::", "|") + "}",
        result,
    )

    return result

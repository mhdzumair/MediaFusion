#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "db/config.py"
DOC_PATH = ROOT_DIR / "docs/env-reference.md"
DOCKER_ENV_SAMPLE_PATH = ROOT_DIR / "deployment/docker-compose/.env-sample"
K8S_LOCAL_DEPLOYMENT_PATH = ROOT_DIR / "deployment/k8s/local-deployment.yaml"

K8S_APP_CONTAINERS = ("mediafusion", "taskiq-worker-default")
TOKEN_WORD_MAP = {
    "api": "API",
    "aka": "AKA",
    "bt4g": "BT4G",
    "cdp": "CDP",
    "db": "DB",
    "dmm": "DMM",
    "dlhd": "DLHD",
    "id": "ID",
    "iptv": "IPTV",
    "m3u8": "M3U8",
    "motogp": "MotoGP",
    "nzb": "NZB",
    "nzbdav": "NzbDAV",
    "oauth": "OAuth",
    "p2p": "P2P",
    "redis": "Redis",
    "rss": "RSS",
    "s3": "S3",
    "simkl": "Simkl",
    "smtp": "SMTP",
    "tmdb": "TMDB",
    "torznab": "Torznab",
    "ttl": "TTL",
    "tv": "TV",
    "tvdb": "TVDB",
    "ufc": "UFC",
    "uri": "URI",
    "url": "URL",
    "wwe": "WWE",
    "yts": "YTS",
}


@dataclass(frozen=True)
class SettingField:
    name: str
    env_name: str
    annotation: str
    required: bool
    default_repr: str
    description: str


def _is_ellipsis(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _is_field_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Field"


def _field_required_from_call(node: ast.Call) -> bool:
    if node.args and _is_ellipsis(node.args[0]):
        return True
    for keyword in node.keywords:
        if keyword.arg == "default" and _is_ellipsis(keyword.value):
            return True
    return False


def _field_default_from_call(node: ast.Call) -> str:
    for keyword in node.keywords:
        if keyword.arg == "default":
            return ast.unparse(keyword.value)
        if keyword.arg == "default_factory":
            return f"default_factory={ast.unparse(keyword.value)}"
    if node.args and not _is_ellipsis(node.args[0]):
        return ast.unparse(node.args[0])
    return ""


def _normalize_default(default_repr: str) -> str:
    normalized = default_repr.strip().replace("\n", " ")
    if len(normalized) > 80:
        return f"{normalized[:77]}..."
    return normalized


def _normalize_description(description: str) -> str:
    normalized = " ".join(description.strip().split())
    if len(normalized) > 140:
        return f"{normalized[:137]}..."
    return normalized


def _markdown_cell(content: str) -> str:
    return content.replace("|", "\\|")


def _extract_comment_text(raw_comment: str) -> str:
    cleaned = raw_comment.lstrip("#").strip()
    return cleaned


def _is_section_heading_comment(comment: str) -> bool:
    stripped = comment.strip()
    if not stripped:
        return False
    if stripped.endswith(":"):
        return True
    if any(char in stripped for char in ".!?"):
        return False

    words = stripped.split()
    if len(words) > 6:
        return False

    connector_words = {"and", "or", "for", "to", "with", "of", "in", "on", "the"}
    for word in words:
        first_char = word[0]
        if word.lower() in connector_words:
            continue
        if first_char.isalpha() and not first_char.isupper():
            return False
    return True


def _extract_section_and_detail_comments(comments: list[str]) -> tuple[str, list[str]]:
    if not comments:
        return "", []

    section_heading = ""
    filtered = comments[:]
    if _is_section_heading_comment(filtered[0]):
        section_heading = filtered[0]
        filtered = filtered[1:]
    if len(filtered) == 1 and _is_section_heading_comment(filtered[0]):
        if not section_heading:
            section_heading = filtered[0]
        return section_heading, []
    return section_heading, filtered


def _collect_comment_tokens(source: str) -> dict[int, str]:
    comments_by_line: dict[int, list[str]] = {}
    token_stream = tokenize.generate_tokens(io.StringIO(source).readline)
    for token in token_stream:
        if token.type != tokenize.COMMENT:
            continue
        line_number = token.start[0]
        cleaned = _extract_comment_text(token.string)
        if not cleaned:
            continue
        comments_by_line.setdefault(line_number, []).append(cleaned)
    return {line_number: " ".join(parts) for line_number, parts in comments_by_line.items()}


def _collect_preceding_comments(source_lines: list[str], line_number: int) -> tuple[str, str]:
    docs: list[str] = []
    cursor = line_number - 1
    while cursor > 0:
        raw_line = source_lines[cursor - 1]
        stripped = raw_line.strip()
        if not stripped:
            break
        if not stripped.startswith("#"):
            break
        comment_text = _extract_comment_text(stripped)
        if comment_text:
            docs.append(comment_text)
        cursor -= 1
    docs.reverse()
    section_heading, filtered_docs = _extract_section_and_detail_comments(docs)
    return " ".join(filtered_docs), section_heading


def _humanize_token(token: str) -> str:
    lower_token = token.lower()
    if lower_token in TOKEN_WORD_MAP:
        return TOKEN_WORD_MAP[lower_token]
    if re.fullmatch(r"\d+", token):
        return token
    return token.capitalize()


def _humanize_identifier(identifier: str) -> str:
    return " ".join(_humanize_token(token) for token in identifier.split("_") if token)


def _infer_description(field_name: str, annotation: str, section_heading: str) -> str:
    lower_name = field_name.lower()

    def subject_without_suffix(suffix: str) -> str:
        return _humanize_identifier(field_name[: -len(suffix)])

    if lower_name.startswith("is_scrap_from_"):
        provider_name = _humanize_identifier(field_name[len("is_scrap_from_") :])
        return f"Enable scraping from {provider_name}."
    if lower_name.startswith("is_"):
        subject = _humanize_identifier(field_name[3:]).lower()
        return f"Whether {subject} is enabled."
    if lower_name.startswith("enable_"):
        subject = _humanize_identifier(field_name[7:]).lower()
        return f"Enable {subject}."
    if lower_name.startswith("disable_"):
        subject = _humanize_identifier(field_name[8:]).lower()
        return f"Disable {subject} when set to true."
    if lower_name.endswith("_url"):
        return f"URL for {subject_without_suffix('_url')}."
    if lower_name.endswith("_uri"):
        return f"Connection URI for {subject_without_suffix('_uri')}."
    if lower_name.endswith("_api_key"):
        return f"API key for {subject_without_suffix('_api_key')}."
    if lower_name.endswith("_client_id"):
        return f"Client ID for {subject_without_suffix('_client_id')}."
    if lower_name.endswith("_client_secret"):
        return f"Client secret for {subject_without_suffix('_client_secret')}."
    if lower_name.endswith("_username"):
        return f"Username for {subject_without_suffix('_username')}."
    if lower_name.endswith("_password"):
        return f"Password for {subject_without_suffix('_password')}."
    if lower_name.endswith("_token"):
        return f"Token for {subject_without_suffix('_token')}."
    if lower_name.endswith("_chat_id"):
        return f"Chat ID for {subject_without_suffix('_chat_id')}."
    if lower_name.endswith("_port"):
        return f"Port used by {subject_without_suffix('_port')}."
    if lower_name.endswith("_timeout"):
        return f"Timeout in seconds for {subject_without_suffix('_timeout').lower()}."
    if lower_name.endswith("_ttl"):
        return f"Time-to-live in seconds for {subject_without_suffix('_ttl').lower()}."
    if lower_name.endswith("_crontab"):
        return f"Cron schedule for {subject_without_suffix('_crontab').lower()}."
    if "_interval_hour" in lower_name:
        subject = _humanize_identifier(field_name.replace("_interval_hour", "").replace("_interval_hours", "")).lower()
        return f"Interval in hours for {subject}."
    if "_interval_day" in lower_name:
        subject = _humanize_identifier(field_name.replace("_interval_day", "").replace("_interval_days", "")).lower()
        return f"Interval in days for {subject}."
    if lower_name.startswith("max_"):
        return f"Maximum value for {_humanize_identifier(field_name[4:]).lower()}."
    if lower_name.startswith("min_"):
        return f"Minimum value for {_humanize_identifier(field_name[4:]).lower()}."
    if lower_name.endswith("_size"):
        return f"Size limit for {subject_without_suffix('_size').lower()}."
    if lower_name.endswith("_limit"):
        return f"Limit for {subject_without_suffix('_limit').lower()}."
    if lower_name.endswith("_threshold"):
        return f"Threshold for {subject_without_suffix('_threshold').lower()}."
    if lower_name.endswith("_count"):
        return f"Count for {subject_without_suffix('_count').lower()}."

    if annotation.strip() == "bool":
        return f"Boolean toggle for {_humanize_identifier(field_name).lower()}."

    humanized_name = _humanize_identifier(field_name)
    if section_heading:
        section = section_heading.strip()
        if section.lower().endswith("settings"):
            section = section[: -len("settings")].strip()
        return f"{humanized_name} setting for {section.lower()}."
    return f"Configuration for {humanized_name.lower()}."


def _build_field_description(
    source_lines: list[str],
    comment_tokens: dict[int, str],
    field_name: str,
    annotation: str,
    line_number: int,
) -> str:
    inline_comment = comment_tokens.get(line_number, "")
    preceding_comment, section_heading = _collect_preceding_comments(source_lines, line_number)
    description_parts = [part for part in (preceding_comment, inline_comment) if part]
    explicit_description = _normalize_description(" ".join(description_parts))
    if explicit_description:
        return explicit_description
    return _normalize_description(_infer_description(field_name, annotation, section_heading))


def extract_settings_fields(config_path: Path) -> list[SettingField]:
    source = config_path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    comment_tokens = _collect_comment_tokens(source)
    module = ast.parse(source)

    settings_class: ast.ClassDef | None = None
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            settings_class = node
            break

    if settings_class is None:
        raise RuntimeError("Settings class not found in db/config.py")

    fields: list[SettingField] = []

    for node in settings_class.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        if node.target.id.startswith("_"):
            continue

        value = node.value
        annotation = ast.unparse(node.annotation)
        required = value is None
        default_repr = ""

        if isinstance(value, ast.Call) and _is_field_call(value):
            required = _field_required_from_call(value)
            default_repr = _field_default_from_call(value)
        elif value is not None:
            default_repr = ast.unparse(value)

        field_name = node.target.id
        description = _build_field_description(source_lines, comment_tokens, field_name, annotation, node.lineno)
        fields.append(
            SettingField(
                name=field_name,
                env_name=field_name.upper(),
                annotation=annotation,
                required=required,
                default_repr=default_repr,
                description=description,
            )
        )

    return fields


def render_env_reference(fields: list[SettingField]) -> str:
    required_fields = [field for field in fields if field.required]
    optional_fields = [field for field in fields if not field.required]

    lines = [
        "# Environment Variable Reference",
        "",
        "This file is auto-generated from `db/config.py` using `scripts/check_env_sync.py`.",
        "Descriptions come from comments in `db/config.py`, with inferred fallback text where comments are missing.",
        "Do not edit this file manually.",
        "",
        f"- Total variables: **{len(fields)}**",
        f"- Required variables: **{len(required_fields)}**",
        f"- Optional variables: **{len(optional_fields)}**",
        "",
        "## Required Variables",
        "",
        "| Env Var | Field | Type | Description |",
        "|---|---|---|---|",
    ]

    for field in required_fields:
        description_value = f"`{_markdown_cell(field.description)}`" if field.description else "—"
        lines.append(
            f"| `{_markdown_cell(field.env_name)}` | `{_markdown_cell(field.name)}` | "
            f"`{_markdown_cell(field.annotation)}` | {description_value} |"
        )

    lines.extend(
        [
            "",
            "## All Variables",
            "",
            "| Env Var | Field | Required | Type | Default | Description |",
            "|---|---|---|---|---|---|",
        ]
    )

    for field in fields:
        required_label = "yes" if field.required else "no"
        default_value = "—" if field.required else f"`{_markdown_cell(_normalize_default(field.default_repr))}`"
        description_value = f"`{_markdown_cell(field.description)}`" if field.description else "—"
        lines.append(
            f"| `{_markdown_cell(field.env_name)}` | `{_markdown_cell(field.name)}` | {required_label} | "
            f"`{_markdown_cell(field.annotation)}` | {default_value} | {description_value} |"
        )

    lines.append("")
    return "\n".join(lines)


def extract_env_names_from_env_sample(file_path: Path) -> set[str]:
    env_names: set[str] = set()
    pattern = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=")
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            env_names.add(match.group(1))
    return env_names


def extract_k8s_container_env_names(file_path: Path, container_name: str) -> set[str]:
    content = file_path.read_text(encoding="utf-8")
    container_marker = f"- name: {container_name}"
    start_index = content.find(container_marker)
    if start_index == -1:
        return set()

    next_doc_index = content.find("\n---", start_index)
    next_container_index = content.find("\n      - name:", start_index + 1)
    block_end_candidates = [index for index in (next_doc_index, next_container_index) if index != -1]
    block_end = min(block_end_candidates) if block_end_candidates else len(content)
    block = content[start_index:block_end]

    return set(re.findall(r"- name:\s*([A-Z][A-Z0-9_]*)", block))


def check_required_env_coverage(fields: list[SettingField]) -> list[str]:
    required_env_names = {field.env_name for field in fields if field.required}
    errors: list[str] = []

    docker_env_names = extract_env_names_from_env_sample(DOCKER_ENV_SAMPLE_PATH)
    missing_in_docker = sorted(required_env_names - docker_env_names)
    if missing_in_docker:
        errors.append(
            "Missing required env vars in deployment/docker-compose/.env-sample: " + ", ".join(missing_in_docker)
        )

    for container_name in K8S_APP_CONTAINERS:
        k8s_env_names = extract_k8s_container_env_names(K8S_LOCAL_DEPLOYMENT_PATH, container_name)
        missing_in_container = sorted(required_env_names - k8s_env_names)
        if missing_in_container:
            errors.append(
                f"Missing required env vars in deployment/k8s/local-deployment.yaml "
                f"container '{container_name}': {', '.join(missing_in_container)}"
            )

    return errors


def check_description_coverage(fields: list[SettingField]) -> list[str]:
    missing_descriptions = sorted(field.env_name for field in fields if not field.description.strip())
    if not missing_descriptions:
        return []

    return [
        "Missing descriptions for env vars: " + ", ".join(missing_descriptions),
        "Add comments in db/config.py or update description inference in scripts/check_env_sync.py.",
    ]


def check_docs_sync(expected_content: str) -> list[str]:
    if not DOC_PATH.exists():
        return [f"Missing generated docs file: {DOC_PATH.relative_to(ROOT_DIR)}"]
    current_content = DOC_PATH.read_text(encoding="utf-8")
    if current_content != expected_content:
        return [
            "Generated docs are out of date: docs/env-reference.md",
            "Run: python scripts/check_env_sync.py --write",
        ]
    return []


def write_docs(expected_content: str) -> None:
    DOC_PATH.write_text(expected_content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate environment-variable synchronization between db/config.py, "
            "generated docs, and deployment templates."
        )
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Regenerate docs/env-reference.md from db/config.py.",
    )
    args = parser.parse_args()

    fields = extract_settings_fields(CONFIG_PATH)
    expected_docs = render_env_reference(fields)

    if args.write:
        write_docs(expected_docs)
        print(f"Updated {DOC_PATH.relative_to(ROOT_DIR)}")
        return 0

    errors = check_docs_sync(expected_docs)
    errors.extend(check_required_env_coverage(fields))
    errors.extend(check_description_coverage(fields))

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("Environment variable docs and deployment templates are in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import argparse
import re
import subprocess
import textwrap
from pathlib import Path
from tempfile import NamedTemporaryFile

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXCEPTIONS_DIR = PROJECT_ROOT / "resources" / "exceptions"
SCAN_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}
SKIP_DIR_NAMES = {
    ".git",
    ".idea",
    ".vscode",
    ".cursor",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}

# Optional overrides for better user-facing wording.
CUSTOM_VIDEO_TEXT: dict[str, str] = {
    "mediaflow_ip_error.mp4": "MediaFlow proxy IP lookup failed.\nPlease check your MediaFlow is working correctly.",
    "stream_not_found.mp4": "Stream not found.\nThis stream may have been removed or expired.\nPlease refresh and try another stream.",
    "watchlist_deleted.mp4": "Watchlist has been cleared.\nPlease refresh to see the latest state.",
}


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,").replace("%", "\\%")


def _build_default_text(video_name: str) -> str:
    stem = Path(video_name).stem.replace("_", " ").strip()
    heading = " ".join(word.capitalize() for word in stem.split()) or "Playback Error"
    wrapped_heading = textwrap.wrap(heading, width=34) or ["Playback Error"]
    lines = wrapped_heading[:2]
    lines.append("Please try again in a moment.")
    return "\n".join(lines)


def _build_video_text(video_name: str) -> str:
    custom_text = CUSTOM_VIDEO_TEXT.get(video_name)
    if custom_text:
        return custom_text
    return _build_default_text(video_name)


def _iter_source_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def _discover_exception_video_names(root: Path) -> list[str]:
    patterns = [
        re.compile(r"/static/exceptions/(?P<name>[a-z0-9_\-]+\.mp4)"),
        re.compile(r"ProviderException\([^)]*['\"](?P<name>[a-z0-9_\-]+\.mp4)['\"]"),
        re.compile(r"video_file_name\s*==\s*['\"](?P<name>[a-z0-9_\-]+\.mp4)['\"]"),
    ]

    discovered_names: set[str] = set(CUSTOM_VIDEO_TEXT.keys())
    for source_file in _iter_source_files(root):
        try:
            content = source_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in patterns:
            for match in pattern.finditer(content):
                discovered_names.add(match.group("name"))

    return sorted(discovered_names)


def create_text_video(
    output_path: Path,
    text: str,
    duration: int = 20,
    resolution: tuple[int, int] = (1280, 720),
    fontsize: int = 45,
    bgcolor: str = "black",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(suffix=".mp4", delete=False) as temp_background:
        background_path = Path(temp_background.name)

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-f",
                "lavfi",
                "-i",
                f"color=c={bgcolor}:s={resolution[0]}x{resolution[1]}",
                "-t",
                str(duration),
                "-y",
                str(background_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        lines = text.split("\n")
        line_height = fontsize + 10
        total_height = line_height * len(lines)
        start_y = f"(h-{total_height})/2"

        drawtext_filters = []
        for index, line in enumerate(lines):
            escaped = _escape_drawtext(line)
            y_expr = f"{start_y}+{index * line_height}"
            drawtext_filters.append(
                f"drawtext=text='{escaped}':fontcolor=white:fontsize={fontsize}:"
                f"x=(w-text_w)/2:y={y_expr}:shadowx=2:shadowy=2:shadowcolor=black@0.5"
            )

        vf = ",".join(drawtext_filters)
        vf += f",fade=t=in:st=0:d=1,fade=t=out:st={max(duration - 1, 1)}:d=1"

        subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(background_path),
                "-vf",
                vf,
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        if background_path.exists():
            background_path.unlink()


def ensure_exception_videos(force: bool = False) -> tuple[list[str], list[str]]:
    discovered_names = _discover_exception_video_names(PROJECT_ROOT)
    created_names: list[str] = []
    existing_names: list[str] = []

    for name in discovered_names:
        output_file = EXCEPTIONS_DIR / name
        if output_file.exists() and not force:
            existing_names.append(name)
            continue
        create_text_video(output_file, _build_video_text(name))
        created_names.append(name)

    return created_names, existing_names


def main():
    parser = argparse.ArgumentParser(description="Generate missing exception videos under resources/exceptions.")
    parser.add_argument("--force", action="store_true", help="Regenerate videos even if they already exist.")
    args = parser.parse_args()

    created_names, existing_names = ensure_exception_videos(force=args.force)
    print(f"Exception videos discovered: {len(created_names) + len(existing_names)}")
    print(f"Created: {len(created_names)}")
    print(f"Already present: {len(existing_names)}")
    if created_names:
        print("Generated files:")
        for name in created_names:
            print(f"  - {name}")


if __name__ == "__main__":
    main()

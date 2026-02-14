import re

resolution_mapper = {
    "4K": (2160, 3840),
    "2160P": (2160, 3840),
    "1440P": (1440, 2560),
    "1080P": (1080, 1920),
    "720P": (720, 1280),
    "576P": (576, 720),
    "480P": (480, 640),
    "360P": (360, 480),
    "240P": (240, 320),
    None: (0, 0),
}


def parse_stream_info(name, description, behaviour_hint):
    """Parse stream name and description into structured information."""
    info = {
        "name": behaviour_hint.get("filename") or name,
        "description": description,
        "resolution": "",
        "width": 0,
        "height": 0,
        "cache_status": ("CACHED" if "⚡️" in name else "NOT CACHED" if "⏳" in name else ""),
        "size": behaviour_hint.get("videoSize"),
        "language": "",
        "source": "",
        "hdr": "",
        "codec": "",
        "audio": "",
        "seeders": "0",
    }

    # Extract quality from name
    resolution_match = re.search(r"(4K|2160p|1440p|1080p|720p|576p|480p|360p|240p)", name, re.IGNORECASE)
    if resolution_match:
        resolution = resolution_match.group(1).upper()
        info["resolution"] = resolution
        info["height"], info["width"] = resolution_mapper.get(resolution) or (0, 0)

    # Parse description for detailed information
    desc_patterns = {
        "filename": r"📂 (.+?)(?:\n|$)",
        "language": r"🌐 (.+?)(?:\n|$)",
        "source": r"🔗 (.+?)(?:\n|$)",
        "seeders": r"👤 (\d+)",
        "hdr": r"🎨 (.+?) 🎞️",
        "codec": r"🎞️ (.+?) 🎵",
        "audio": r"🎵 (.+?)(?:\n|-|$)",
    }

    for key, pattern in desc_patterns.items():
        if match := re.search(pattern, description):
            info[key] = match.group(1).strip()
        elif match := re.search(pattern, behaviour_hint.get("bingeGroup", "")):
            info[key] = match.group(1).strip()

    return info


def format_stream_label(name, description):
    """Format stream information into Kodi-friendly labels."""
    # Main label components
    name = name.replace("⚡️", "CACHED").replace("⏳", "NOT CACHED")
    description = (
        description.replace("📂", "Torrent Name:")
        .replace("┈➤", "| File:")
        .replace("💾", "Size:")
        .replace("👤", "Seeders:")
        .replace("🔗", "Source:")
        .replace("🧑‍💻", "Uploader:")
        .replace("🌐", "Language:")
        .replace("🎨 ", "HDR:")
        .replace("📺 ", "Quality:")
        .replace("🎞️ ", "Codec:")
    )
    return name, description

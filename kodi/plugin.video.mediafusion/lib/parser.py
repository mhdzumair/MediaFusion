import re


def _format_size(size_bytes):
    if not size_bytes:
        return ""
    try:
        value = float(size_bytes)
    except (TypeError, ValueError):
        return str(size_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    return f"{value:.2f} {units[idx]}"


def _parse_audio_channels(channel_value):
    if channel_value is None:
        return 0
    if isinstance(channel_value, int):
        return max(channel_value, 0)
    channel_map = {
        "1.0": 1,
        "2.0": 2,
        "2.1": 3,
        "5.1": 6,
        "7.1": 8,
    }
    normalized = str(channel_value).strip()
    if normalized in channel_map:
        return channel_map[normalized]
    number_match = re.search(r"(\d+)", normalized)
    return int(number_match.group(1)) if number_match else 0


def parse_stream_entry(stream_entry):
    """Parse structured Kodi stream payload entry."""
    stream = stream_entry.get("stream") or {}
    metadata = stream_entry.get("metadata") or {}
    behavior_hints = stream.get("behaviorHints") or {}

    stream_name = metadata.get("name") or behavior_hints.get("filename") or stream.get("name") or "Stream"
    stream_type = (metadata.get("stream_type") or "").upper()
    resolution = (metadata.get("resolution") or "").upper()
    provider = metadata.get("provider_short_name") or metadata.get("provider_name") or ""
    cache_status = "CACHED" if metadata.get("cached") else ""

    label_parts = [part for part in [stream_type, provider, resolution, cache_status] if part]
    detail_label = " | ".join(label_parts)

    info_parts = []
    size_display = metadata.get("size_display") or _format_size(metadata.get("size"))
    seeders = metadata.get("seeders")
    if size_display or seeders:
        line = f"Size: {size_display}" if size_display else ""
        if seeders:
            line = f"{line} | Seeders: {seeders}" if line else f"Seeders: {seeders}"
        info_parts.append(line)
    languages = metadata.get("languages") or []
    if languages:
        info_parts.append(f"Languages: {' + '.join(languages)}")
    source = metadata.get("source")
    uploader = metadata.get("uploader")
    if source or uploader:
        source_line = f"Source: {source}" if source else ""
        if uploader:
            source_line = f"{source_line} | Uploader: {uploader}" if source_line else f"Uploader: {uploader}"
        info_parts.append(source_line)

    plot = "\n".join([line for line in [detail_label] + info_parts if line])
    hdr_formats = metadata.get("hdr_formats") or []
    audio_formats = metadata.get("audio_formats") or []
    channels = metadata.get("channels") or []

    video_info = {
        "name": stream_name,
        "description": plot,
        "resolution": resolution,
        "width": int(metadata.get("video_width") or 0),
        "height": int(metadata.get("video_height") or 0),
        "cache_status": cache_status,
        "size": metadata.get("size") or behavior_hints.get("videoSize"),
        "language": languages[0] if languages else "",
        "source": source or "",
        "hdr": hdr_formats[0] if hdr_formats else "",
        "codec": metadata.get("codec") or "",
        "audio": " | ".join(audio_formats),
        "audio_codec": audio_formats[0] if audio_formats else "",
        "audio_channels": _parse_audio_channels(channels[0]) if channels else 0,
        "seeders": str(seeders or "0"),
    }
    return {
        "stream": stream,
        "main_label": stream_name,
        "detail_label": detail_label,
        "plot": plot,
        "video_info": video_info,
    }

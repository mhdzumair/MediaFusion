import re


def _stream_type_label(stream_type):
    mapping = {
        "TORRENT": "TOR",
        "USENET": "NZB",
        "TELEGRAM": "TGR",
        "HTTP": "WEB",
        "YOUTUBE": "YT",
        "ACESTREAM": "ACE",
    }
    return mapping.get(stream_type, stream_type or "UNK")


def _provider_color(provider):
    colors = {
        "RD": "FF4DB6FF",
        "TRB": "FFB388FF",
        "AD": "FFFFB74D",
        "EN": "FF81C784",
        "OC": "FFFF8A80",
        "PKP": "FF80DEEA",
    }
    key = (provider or "").upper()
    return colors.get(key, "FF9FA8DA")


def _badge(text, color):
    if not text:
        return ""
    return f"[COLOR {color}][B]{text}[/B][/COLOR]"


def _resolution_rank(resolution):
    ranks = {
        "4K": 6,
        "2160P": 6,
        "1440P": 5,
        "1080P": 4,
        "720P": 3,
        "576P": 2,
        "480P": 2,
        "360P": 1,
        "240P": 1,
    }
    return ranks.get((resolution or "").upper(), 0)


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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

    stream_name = behavior_hints.get("filename") or metadata.get("filename") or metadata.get("name") or "Stream"
    stream_type = (metadata.get("stream_type") or "").upper()
    stream_type_label = _stream_type_label(stream_type)
    resolution = (metadata.get("resolution") or "").upper()
    quality = (metadata.get("quality") or "").upper()
    provider = metadata.get("provider_short_name") or metadata.get("provider_name") or ""
    is_cached = bool(metadata.get("cached"))
    cache_status = "CACHED" if is_cached else "UNCACHED"

    size_bytes = _to_int(metadata.get("size") or behavior_hints.get("videoSize"))
    size_display = metadata.get("size_display") or _format_size(size_bytes)
    seeders = _to_int(metadata.get("seeders"))
    info_parts = [f"Title: {stream_name}"] if stream_name else []
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

    hdr_formats = metadata.get("hdr_formats") or []
    audio_formats = metadata.get("audio_formats") or []
    channels = metadata.get("channels") or []
    codec = (metadata.get("codec") or "").upper()
    hdr = " ".join(hdr_formats).upper() if hdr_formats else ""
    first_audio = (audio_formats[0] if audio_formats else "").upper()
    first_channel = channels[0] if channels else ""
    source_short = (source or "-").strip()
    lang_short = " / ".join([(lang or "")[:2].upper() for lang in languages if lang][:3]) or "-"
    if len(languages) > 3:
        lang_short += " +"

    provider_badge = _badge(provider.upper() or "MF", _provider_color(provider))
    type_badge = _badge(stream_type_label, "FF90CAF9")
    cache_badge = _badge("CACHED" if is_cached else "UNCACHED", "FF81C784" if is_cached else "FFFFCC80")

    row_quality_parts = [
        resolution,
        quality,
        codec,
        hdr,
        f"{first_audio} {first_channel}".strip(),
    ]
    main_label = " | ".join([part for part in row_quality_parts if part]) or stream_name
    detail_label = (
        f"{stream_type_label} | {cache_status}" if stream_type in {"TORRENT", "USENET"} else stream_type_label
    )

    list_primary = main_label or stream_name
    list_secondary_parts = [lang_short]
    if size_display:
        list_secondary_parts.append(f"SIZE {size_display}")
    if seeders > 0:
        list_secondary_parts.append(f"S:{seeders}")
    if source_short:
        list_secondary_parts.append(source_short[:26])
    list_secondary = " | ".join([part for part in list_secondary_parts if part])

    info_header = " | ".join(
        [
            part
            for part in [
                f"Provider: {provider}" if provider else "",
                f"Type: {stream_type_label}" if stream_type_label else "",
                f"Cache: {cache_status}" if stream_type in {"TORRENT", "USENET"} else "",
            ]
            if part
        ]
    )
    badges_line = " ".join(
        [
            part
            for part in [
                provider_badge,
                type_badge,
                cache_badge if stream_type in {"TORRENT", "USENET"} else "",
                _badge(resolution, "FFECEFF1") if resolution else "",
                _badge(quality, "FFB0BEC5") if quality else "",
                _badge(codec, "FFB39DDB") if codec else "",
                _badge(hdr, "FFE57373") if hdr else "",
                _badge(f"{first_audio} {first_channel}".strip(), "FF80CBC4") if (first_audio or first_channel) else "",
            ]
            if part
        ]
    )
    video_line = " | ".join(
        [
            part
            for part in [
                f"Resolution: {resolution}" if resolution else "",
                f"Quality: {quality}" if quality else "",
                f"Codec: {codec}" if codec else "",
                f"HDR: {hdr}" if hdr else "",
            ]
            if part
        ]
    )
    audio_line = " | ".join(
        [
            part
            for part in [
                f"Audio: {' | '.join(audio_formats)}" if audio_formats else "",
                f"Channels: {' | '.join(str(ch) for ch in channels)}" if channels else "",
            ]
            if part
        ]
    )
    plot = "\n".join([line for line in [info_header, video_line, audio_line, *info_parts] if line])

    video_info = {
        "name": stream_name,
        "description": plot,
        "resolution": resolution,
        "width": int(metadata.get("video_width") or 0),
        "height": int(metadata.get("video_height") or 0),
        "cache_status": cache_status,
        "size": size_bytes,
        "language": languages[0] if languages else "",
        "source": source or "",
        "provider": provider,
        "stream_type": stream_type_label,
        "hdr": hdr_formats[0] if hdr_formats else "",
        "codec": metadata.get("codec") or "",
        "audio": " | ".join(audio_formats),
        "audio_codec": audio_formats[0] if audio_formats else "",
        "audio_channels": _parse_audio_channels(channels[0]) if channels else 0,
        "seeders": str(seeders),
        "languages": " + ".join(languages),
    }
    return {
        "stream": stream,
        "main_label": main_label or stream_name,
        "detail_label": detail_label,
        "list_primary": list_primary,
        "list_secondary": list_secondary,
        "detail_title": stream_name,
        "detail_badges": badges_line,
        "plot": plot,
        "sort_cached": 0 if is_cached else 1,
        "sort_resolution": _resolution_rank(resolution),
        "sort_seeders": seeders,
        "sort_size": size_bytes,
        "stream_type_raw": stream_type,
        "video_info": video_info,
    }

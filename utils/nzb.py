"""NZB file parsing utilities.

This module provides utilities for parsing NZB (Newzbin) XML files,
extracting metadata, and generating unique identifiers for NZB content.
"""

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import BinaryIO

logger = logging.getLogger(__name__)


@dataclass
class NZBFile:
    """Represents a single file within an NZB."""

    filename: str
    size: int
    groups: list[str] = field(default_factory=list)
    segments_count: int = 0
    poster: str | None = None
    date: datetime | None = None

    @property
    def is_video(self) -> bool:
        """Check if this file is a video file."""
        video_extensions = {
            ".mkv",
            ".mp4",
            ".avi",
            ".mov",
            ".wmv",
            ".flv",
            ".webm",
            ".m4v",
            ".mpg",
            ".mpeg",
            ".ts",
            ".m2ts",
            ".vob",
        }
        lower_name = self.filename.lower()
        return any(lower_name.endswith(ext) for ext in video_extensions)

    @property
    def is_sample(self) -> bool:
        """Check if this file is a sample video."""
        lower_name = self.filename.lower()
        return "sample" in lower_name and self.is_video

    @property
    def is_archive(self) -> bool:
        """Check if this file is an archive."""
        archive_extensions = {".rar", ".zip", ".7z", ".tar", ".gz"}
        lower_name = self.filename.lower()
        return any(lower_name.endswith(ext) for ext in archive_extensions)


@dataclass
class NZBMetadata:
    """Metadata extracted from an NZB file."""

    title: str
    total_size: int
    files: list[NZBFile]
    groups: list[str]
    poster: str | None = None
    date: datetime | None = None
    nzb_hash: str | None = None
    password: str | None = None
    is_passworded: bool = False

    @property
    def files_count(self) -> int:
        """Total number of files in the NZB."""
        return len(self.files)

    @property
    def video_files(self) -> list[NZBFile]:
        """Get only video files, excluding samples."""
        return [f for f in self.files if f.is_video and not f.is_sample]

    @property
    def main_video_file(self) -> NZBFile | None:
        """Get the largest video file (likely the main content)."""
        videos = self.video_files
        if not videos:
            return None
        return max(videos, key=lambda f: f.size)

    @property
    def has_video(self) -> bool:
        """Check if NZB contains any video files."""
        return len(self.video_files) > 0

    @property
    def primary_group(self) -> str | None:
        """Get the primary Usenet group."""
        return self.groups[0] if self.groups else None


def parse_nzb_content(nzb_content: bytes) -> NZBMetadata:
    """Parse NZB XML content and extract metadata.

    Args:
        nzb_content: Raw NZB file content (XML bytes)

    Returns:
        NZBMetadata object with parsed information

    Raises:
        ValueError: If the NZB content is invalid or cannot be parsed
    """
    try:
        # Parse XML
        root = ET.fromstring(nzb_content)

        # Handle namespace
        namespace = ""
        if root.tag.startswith("{"):
            namespace = root.tag.split("}")[0] + "}"

        files: list[NZBFile] = []
        all_groups: set[str] = set()
        total_size = 0
        poster = None
        date = None
        title = None
        password = None

        # Parse head section for metadata
        head = root.find(f"{namespace}head")
        if head is not None:
            for meta in head.findall(f"{namespace}meta"):
                meta_type = meta.get("type", "").lower()
                meta_value = meta.text or ""
                if meta_type == "name" or meta_type == "title":
                    title = meta_value
                elif meta_type == "password":
                    password = meta_value

        # Parse file entries
        for file_elem in root.findall(f"{namespace}file"):
            file_poster = file_elem.get("poster")
            file_date_str = file_elem.get("date")
            file_subject = file_elem.get("subject", "")

            # Extract filename from subject
            # Format: "filename" yEnc (1/10)
            filename = _extract_filename_from_subject(file_subject)

            # Parse date
            file_date = None
            if file_date_str:
                try:
                    file_date = datetime.fromtimestamp(int(file_date_str))
                except (ValueError, OSError):
                    pass

            # Get groups for this file
            file_groups: list[str] = []
            groups_elem = file_elem.find(f"{namespace}groups")
            if groups_elem is not None:
                for group in groups_elem.findall(f"{namespace}group"):
                    if group.text:
                        file_groups.append(group.text)
                        all_groups.add(group.text)

            # Calculate file size from segments
            file_size = 0
            segments_count = 0
            segments_elem = file_elem.find(f"{namespace}segments")
            if segments_elem is not None:
                for segment in segments_elem.findall(f"{namespace}segment"):
                    segments_count += 1
                    bytes_attr = segment.get("bytes")
                    if bytes_attr:
                        try:
                            file_size += int(bytes_attr)
                        except ValueError:
                            pass

            total_size += file_size

            # Use first file's poster and date as default
            if poster is None and file_poster:
                poster = file_poster
            if date is None and file_date:
                date = file_date

            files.append(
                NZBFile(
                    filename=filename,
                    size=file_size,
                    groups=file_groups,
                    segments_count=segments_count,
                    poster=file_poster,
                    date=file_date,
                )
            )

        # Generate title from largest file if not in metadata
        if not title:
            main_file = max(files, key=lambda f: f.size) if files else None
            if main_file:
                title = _clean_filename_for_title(main_file.filename)
            else:
                title = "Unknown"

        # Generate hash for the NZB content
        nzb_hash = generate_nzb_hash(nzb_content)

        return NZBMetadata(
            title=title,
            total_size=total_size,
            files=files,
            groups=sorted(all_groups),
            poster=poster,
            date=date,
            nzb_hash=nzb_hash,
            password=password,
            is_passworded=password is not None and password != "",
        )

    except ET.ParseError as e:
        raise ValueError(f"Invalid NZB XML: {e}") from e
    except Exception as e:
        logger.exception("Error parsing NZB content")
        raise ValueError(f"Failed to parse NZB: {e}") from e


def parse_nzb_file(file: BinaryIO) -> NZBMetadata:
    """Parse an NZB file object.

    Args:
        file: File-like object containing NZB content

    Returns:
        NZBMetadata object with parsed information
    """
    content = file.read()
    return parse_nzb_content(content)


def generate_nzb_hash(nzb_content: bytes) -> str:
    """Generate a unique hash for NZB content.

    Uses SHA-256 to generate a consistent identifier for the NZB.

    Args:
        nzb_content: Raw NZB file content

    Returns:
        40-character hex string (truncated SHA-256)
    """
    return hashlib.sha256(nzb_content).hexdigest()[:40]


def _extract_filename_from_subject(subject: str) -> str:
    """Extract filename from NZB subject line.

    Subject format is typically:
    - "filename.ext" yEnc (1/10)
    - [group] filename.ext yEnc (1/10)
    - filename.ext - [1/10] - yEnc

    Args:
        subject: NZB file subject string

    Returns:
        Extracted filename or the subject if extraction fails
    """
    if not subject:
        return "unknown"

    # Try to extract quoted filename
    if '"' in subject:
        parts = subject.split('"')
        if len(parts) >= 2:
            return parts[1]

    # Try to extract from yEnc format
    if " yEnc " in subject or " yenc " in subject.lower():
        # Remove yEnc suffix and part numbers
        clean = subject.split(" yEnc")[0].split(" yenc")[0]
        # Remove leading brackets/groups
        if "]" in clean:
            clean = clean.split("]")[-1]
        return clean.strip()

    # Try to find filename with extension
    import re

    # Match common video/archive extensions
    match = re.search(r"[\w\-\.\s]+\.(mkv|mp4|avi|rar|zip|nfo|srt|sub)", subject, re.IGNORECASE)
    if match:
        return match.group(0).strip()

    return subject.strip()


def _clean_filename_for_title(filename: str) -> str:
    """Clean a filename to use as a title.

    Removes file extension and common patterns like release group tags.

    Args:
        filename: Filename to clean

    Returns:
        Cleaned title string
    """
    import re

    # Remove file extension
    name = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", filename)

    # Remove common patterns
    # Remove release info in brackets
    name = re.sub(r"\[.*?\]", "", name)

    # Replace dots and underscores with spaces
    name = re.sub(r"[._]", " ", name)

    # Remove extra whitespace
    name = " ".join(name.split())

    return name.strip()


def extract_video_file_info(nzb_metadata: NZBMetadata) -> dict:
    """Extract information about video files from NZB metadata.

    Args:
        nzb_metadata: Parsed NZB metadata

    Returns:
        Dictionary with video file information
    """
    video_files = nzb_metadata.video_files
    main_file = nzb_metadata.main_video_file

    return {
        "video_count": len(video_files),
        "main_file": {
            "filename": main_file.filename,
            "size": main_file.size,
        }
        if main_file
        else None,
        "files": [{"filename": f.filename, "size": f.size, "index": i} for i, f in enumerate(video_files)],
        "total_video_size": sum(f.size for f in video_files),
    }

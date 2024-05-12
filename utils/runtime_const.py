import re

from db.config import settings
from utils import get_json_data

ADULT_CONTENT_KEYWORDS = re.compile(
    settings.adult_content_regex_keywords,
    re.IGNORECASE,
)
PARENT_GUIDE_NUDITY_FILTER_TYPES_REGEX = re.compile(
    settings.parent_guide_nudity_filter_types_regex,
    re.IGNORECASE,
)
PARENT_GUIDE_CERTIFICATES_FILTER_REGEX = re.compile(
    settings.parent_guide_certificates_filter_regex,
    re.IGNORECASE,
)

SPORTS_ARTIFACTS = get_json_data("resources/json/sports_artifacts.json")

PRIVATE_CIDR = re.compile(
    r"^(10\.|127\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)",
)

import re

from db.config import settings


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

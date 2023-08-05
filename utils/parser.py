import re

from db.schemas import Stream


def extract_stream_details(video_qualities: dict) -> list[Stream]:
    stream_list = []
    for quality_string, hash_value in video_qualities.items():
        stream_details = {}

        # Quality
        quality = re.search(
            r"(4K|\d{3,4}p|HQ HDRip|HDRip|HQ PreDVDRip|HQ Real-PreDVDRip|HQ-Real PreDVDRip|HQ-S Print)", quality_string
        )
        if quality:
            quality = quality.group(0)
        else:
            quality = "N/A"

        # Size
        size = re.search(r"(\d+(\.\d+)?(MB|GB))", quality_string)
        if size:
            size = size.group(0)
        else:
            size = "N/A"

        # Languages
        lang_dict = {
            "Tam": "Tamil",
            "Mal": "Malayalam",
            "Tel": "Telugu",
            "Kan": "Kannada",
            "Hin": "Hindi",
            "Eng": "English",
        }

        languages = []
        for short, full in lang_dict.items():
            if re.search(f"({short}|{full})", quality_string, re.IGNORECASE):
                languages.append(full)

        stream_details["name"] = "TamilBlasters"

        # Concatenate the description components
        stream_details["description"] = f"{quality}, {size}, {' + '.join(languages)}"

        # Add the info hash
        stream_details["infoHash"] = hash_value

        stream_list.append(Stream(**stream_details))

    return stream_list

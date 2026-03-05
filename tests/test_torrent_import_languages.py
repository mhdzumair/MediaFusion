from api.routers.content.torrent_import import (
    _normalize_string_list,
    _parse_csv_form_values,
    _resolve_import_languages,
)


def test_parse_csv_form_values_handles_empty_input() -> None:
    assert _parse_csv_form_values(None) == []
    assert _parse_csv_form_values("") == []


def test_parse_csv_form_values_trims_and_filters() -> None:
    assert _parse_csv_form_values("Tamil, English , , Hindi") == ["Tamil", "English", "Hindi"]


def test_normalize_string_list_supports_list_and_string_values() -> None:
    assert _normalize_string_list([" Tamil ", "English", 3, ""]) == ["Tamil", "English"]
    assert _normalize_string_list("Tamil,English") == ["Tamil", "English"]


def test_resolve_import_languages_prefers_form_values() -> None:
    torrent_data = {"languages": ["Tamil", "English", "Hindi"]}
    resolved = _resolve_import_languages("Italian, English", torrent_data)
    assert resolved == ["Italian", "English"]


def test_resolve_import_languages_falls_back_to_parsed_torrent_languages() -> None:
    torrent_data = {"languages": ["Tamil", "English", "Hindi"]}
    resolved = _resolve_import_languages(None, torrent_data)
    assert resolved == ["Tamil", "English", "Hindi"]


def test_resolve_import_languages_handles_string_torrent_languages() -> None:
    torrent_data = {"languages": "Tamil, English"}
    resolved = _resolve_import_languages("", torrent_data)
    assert resolved == ["Tamil", "English"]

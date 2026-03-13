from api.routers.content.metadata import LinkMultipleExternalIdsRequest, _get_canonical_external_id


def test_canonical_external_id_prefers_anime_ids_over_tmdb():
    canonical = _get_canonical_external_id({"tmdb": "500", "mal": "21", "kitsu": "99"})
    assert canonical == "mal:21"


def test_link_multiple_external_ids_request_supports_anilist():
    request = LinkMultipleExternalIdsRequest(anilist_id="12345", media_type="series")
    assert str(request.anilist_id) == "12345"

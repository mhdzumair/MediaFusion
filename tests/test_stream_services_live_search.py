from db.schemas.config import UserData


def test_user_data_parses_anime_live_search_controls():
    user_data = UserData.model_validate(
        {
            "lss": True,
            "ia": False,
            "aso": ["anilist", "kitsu"],
            "also": ["nyaa", "nyaa", "subsplease"],
            "asc": ["public_indexer"],
        }
    )

    assert user_data.live_search_streams is True
    assert user_data.include_anime is False
    assert user_data.anime_source_order == ["anilist", "kitsu"]
    assert user_data.anime_live_source_order == ["nyaa", "subsplease"]
    assert user_data.anime_source_classes == ["public_indexer"]

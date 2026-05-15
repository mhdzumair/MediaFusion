from db.schemas.config import UserData


def test_user_data_parses_anime_metadata_controls():
    user_data = UserData.model_validate(
        {
            "lss": True,
            "ia": False,
        }
    )

    assert user_data.live_search_streams is True
    assert user_data.include_anime is False

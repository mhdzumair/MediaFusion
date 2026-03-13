from api.routers.content.scraping import SCRAPER_CONFIG


def test_public_indexer_scraper_config_exposes_anime_capability():
    assert "public_indexers" in SCRAPER_CONFIG
    description = SCRAPER_CONFIG["public_indexers"]["description"].lower()
    assert "anime" in description

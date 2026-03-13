import scrapy
from scrapy.http import HtmlResponse, Request

from mediafusion_scrapy.spiders.public_indexers import BasePublicIndexerSpider


class _FallbackSpider(BasePublicIndexerSpider):
    name = "test_anime_hoster"
    source = "TestAnimeHoster"
    catalog_source = "anime_series"
    scraped_info_hash_key = "test_anime_hoster_hash"
    detail_fallback_selectors = ("a.fallback::attr(href)",)
    max_detail_hops = 2
    magnet_selectors = ("a[href^='magnet:?']::attr(href)",)


def test_parse_detail_follows_fallback_chain_when_magnet_missing():
    spider = _FallbackSpider()
    request = Request(
        url="https://example.test/detail",
        meta={"item": {"torrent_title": "Example Item", "torrent_name": "Example Item"}},
    )
    response = HtmlResponse(
        url=request.url,
        request=request,
        body="<html><body><a class='fallback' href='/next-detail'>next</a></body></html>",
        encoding="utf-8",
    )

    results = list(spider.parse_detail(response))

    assert len(results) == 1
    assert isinstance(results[0], scrapy.Request)
    assert results[0].meta["detail_depth"] == 1
    assert results[0].url.endswith("/next-detail")


def test_parse_detail_yields_item_when_magnet_present():
    spider = _FallbackSpider()
    item = {
        "torrent_title": "Example Item",
        "torrent_name": "Example Item",
        "source": spider.source,
        "catalog_source": spider.catalog_source,
        "catalog": [spider.catalog_source],
        "scraped_info_hash_key": spider.scraped_info_hash_key,
        "expected_sources": [spider.source, "Contribution Stream"],
    }
    request = Request(url="https://example.test/detail", meta={"item": item})
    response = HtmlResponse(
        url=request.url,
        request=request,
        body=(
            "<html><body>"
            "<a href='magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567'>magnet</a>"
            "</body></html>"
        ),
        encoding="utf-8",
    )

    results = list(spider.parse_detail(response))

    assert len(results) == 1
    assert isinstance(results[0], dict)
    assert results[0]["info_hash"] == "0123456789abcdef0123456789abcdef01234567"

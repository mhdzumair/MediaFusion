import scrapy
from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem
from scrapy.http.request import NO_CALLBACK
from scrapy.utils.defer import maybe_deferred_to_future

from utils import const


class LiveStreamResolverPipeline:
    async def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        stream_url = adapter.get("stream_url")
        stream_headers = adapter.get("stream_headers")
        if not stream_headers:
            referer = adapter.get("referer")
            stream_headers = {"Referer": referer} if referer else {}

        if not stream_url:
            raise DropItem(f"No stream URL found in item: {item}")

        response = await maybe_deferred_to_future(
            spider.crawler.engine.download(
                scrapy.Request(
                    stream_url,
                    callback=NO_CALLBACK,
                    headers=stream_headers,
                    method="HEAD",
                    dont_filter=True,
                )
            )
        )
        content_type = response.headers.get("Content-Type", b"").decode().lower()

        if response.status == 200 and content_type in const.M3U8_VALID_CONTENT_TYPES:
            stream_headers.update(
                {
                    "User-Agent": response.request.headers.get("User-Agent").decode(),
                    "Referer": response.request.headers.get("Referer").decode(),
                }
            )

            item["streams"].append(
                {
                    "name": adapter["stream_name"],
                    "url": adapter["stream_url"],
                    "source": adapter["stream_source"],
                    "behaviorHints": {
                        "notWebReady": True,
                        "proxyHeaders": {
                            "request": stream_headers,
                        },
                    },
                }
            )
            return item
        else:
            raise DropItem(
                f"Invalid M3U8 URL: {stream_url} with Content-Type: {content_type} response: {response.status}"
            )

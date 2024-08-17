from urllib.parse import urljoin, urlparse

import scrapy

from mediafusion_scrapy.spiders.live_tv import LiveTVSpider


class TamilUltraSpider(LiveTVSpider):
    name = "tamilultra"
    start_urls = ["https://tamilultra.tv/"]

    def extract_player_api_base(self, response):
        """Extracts the admin-ajax URL for POST requests."""
        # Directly extract the URL used for admin-ajax POST requests
        admin_ajax_url = response.xpath(
            "//script[contains(text(), 'player_api')]/text()"
        ).re_first(r'"url":"([^"]+)"')
        if admin_ajax_url:
            # Correctly format and return the full URL
            admin_ajax_full_url = urljoin(
                response.url, admin_ajax_url.replace("\\/", "/")
            )
            return admin_ajax_full_url
        else:
            self.logger.error("Admin AJAX URL not found for TamilUltra.")
            return None

    def process_player_option(self, element, channel_data, player_api_post_url):
        """Processes each player option element to send a POST request."""
        stream_title, country_name = self.extract_stream_details(element)
        data_post, data_nume, data_type = (
            element.attrib.get("data-post"),
            element.attrib.get("data-nume"),
            element.attrib.get("data-type"),
        )

        if all([data_post, data_nume, data_type]):
            form_data = {
                "action": "doo_player_ajax",
                "post": data_post,
                "nume": data_nume,
                "type": data_type,
            }
            yield scrapy.FormRequest(
                url=player_api_post_url,
                formdata=form_data,
                callback=self.parse_api_response,
                meta={
                    "channel_data": channel_data,
                    "stream_title": stream_title,
                    "country_name": country_name,
                },
            )

    def extract_m3u8_urls(self, response):
        """Extracts M3U8 URLs using direct and fallback regex patterns."""
        query_string = urlparse(response.url).query
        m3u8_urls = [urljoin(response.url, query_string)]

        user_agent = response.request.headers.get("User-Agent").decode()
        parsed_url = urlparse(response.url)
        referer = f"{parsed_url.scheme}://{parsed_url.netloc}"

        behavior_hints = {
            "notWebReady": True,
            "proxyHeaders": {
                "request": {
                    "User-Agent": user_agent,
                    "Referer": referer,
                }
            },
        }

        return m3u8_urls, behavior_hints

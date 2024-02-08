import argparse
import asyncio
import json
import logging
import re
from urllib.parse import urlparse, urljoin, parse_qs

import aiohttp
import requests
from playwright.async_api import async_playwright

from scrappers.helpers import get_scrapper_config

logging.basicConfig(
    format="%(levelname)s::%(asctime)s - %(message)s", level=logging.INFO
)
BASE_URL = get_scrapper_config("mhdtvplay", "homepage")
MEDIAFUSION_URL = "http://127.0.0.1:8000"


def get_country_name(country_code):
    with open("resources/json/countries.json") as file:
        countries = json.load(file)
    return countries.get(country_code.upper(), "India")


async def extract_player_source_url(iframe_src):
    """
    Asynchronously extracts the player source URL from the HTML response.

    :param iframe_src: The iframe source URL.
    :return: The final player source URL or None if not found.
    """
    headers = {
        "Referer": BASE_URL,
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(iframe_src, headers=headers) as response:
            if response.status == 200:
                html_response = await response.text()
                player_source_regex = re.compile(r'source: [\'"]([^\'"]+)[\'"]')
                match = player_source_regex.search(html_response)

                if match:
                    source_url = match.group(1)
                    # Check if the URL is a DASH stream (MPD)
                    if source_url.endswith(".mpd"):
                        return None
                    # Check if the URL is complete or relative
                    if source_url.startswith(("http:", "https:")):
                        return source_url
                    else:
                        # Construct the full URL from the base URL and the relative path
                        parsed_iframe_src = urlparse(iframe_src)
                        base_url = (
                            f"{parsed_iframe_src.scheme}://{parsed_iframe_src.netloc}"
                        )
                        return urljoin(base_url, source_url)
    return None


async def scrape_tv_channels(page):
    # Scrape channel metadata
    channels_data = []
    channel_elements = await page.query_selector_all("article.item.movies")

    # First, store all channel information in a list
    channel_info_list = []
    for channel_element in channel_elements:
        title_element = await channel_element.query_selector("h3 > a")
        title = await title_element.text_content() if title_element else "No Title"
        poster_element = await channel_element.query_selector(".poster > img")
        poster_url = (
            await poster_element.get_attribute("src")
            if poster_element
            else "No Poster URL"
        )
        stream_page_link_element = await channel_element.query_selector(".poster > a")
        stream_page_url = (
            await stream_page_link_element.get_attribute("href")
            if stream_page_link_element
            else "No Stream Page URL"
        )

        channel_info_list.append((title, poster_url, stream_page_url))

    # Then, navigate to each channel's stream page and capture the M3U8 URLs
    for title, poster_url, stream_page_url in channel_info_list:
        # Navigate to the stream page
        await page.goto(stream_page_url)

        m3u8_url_data = []

        # Scrape genre tags
        genre_elements = await page.query_selector_all(".sgeneros a[rel='tag']")
        genres = [await genre.text_content() for genre in genre_elements]
        genres = [genre.title() for genre in genres]

        # Query for player option elements and click to load M3U8 URL
        player_option_elements = await page.query_selector_all(
            "#playeroptionsul > li.dooplay_player_option"
        )
        country_name = "India"
        for player_option_element in player_option_elements:
            # Click the player option element
            await player_option_element.click()
            stream_title_element = await player_option_element.query_selector(
                "span.title"
            )
            stream_title = (
                await stream_title_element.text_content()
                if stream_title_element
                else "No Stream Title"
            )
            country_flag_element = await player_option_element.query_selector(
                "span.flag > img"
            )
            country_flag_url = (
                await country_flag_element.get_attribute("src")
                if country_flag_element
                else "No Country Flag URL"
            )
            country_name = get_country_name(
                country_flag_url.split("/")[-1].split(".")[0]
            )
            # Wait for the iframe to load and get its 'src' attribute
            try:
                iframe_element = await page.wait_for_selector("iframe.metaframe.rptss")
            except Exception:
                continue
            iframe_src = await iframe_element.get_attribute("src")
            iframe_src = iframe_src.strip()
            # Check if iframe_src is a valid URL
            parsed_src = urlparse(iframe_src)
            behavior_hints = {}
            # if "source" in parsed_src.query:
            #     m3u8_url = parse_qs(parsed_src.query)["source"][0]
            if "youtube.com" in iframe_src:
                m3u8_url = iframe_src
            elif "yuppstream.net.in" in iframe_src:
                m3u8_url = iframe_src
            else:
                try:
                    m3u8_url = await extract_player_source_url(iframe_src)
                except Exception:
                    continue

                if m3u8_url is None:
                    if "https://mhdtvplay.com" in iframe_src:
                        channel_id = parse_qs(parsed_src.query)["watch"][0]
                        m3u8_url = f"https://mhdtvplay.com/crichd/stream.php?id={channel_id}&e=.m3u8"
                    else:
                        continue
                behavior_hints = {
                    "notWebReady": True,
                    "proxyHeaders": {
                        "request": {
                            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
                            "Referer": iframe_src,
                        }
                    },
                }
            m3u8_url_data.append((stream_title, m3u8_url, behavior_hints))

        if not m3u8_url_data:
            continue

        channels_data.append(
            {
                "title": title.replace("Hd", "").strip(),
                "poster": poster_url,
                "genres": genres,
                "country": country_name,  # Set default country
                "tv_language": genres[0],
                "streams": [
                    {
                        "name": f"{title} - {index} | {stream_title}",
                        "url": m3u8_url,
                        "source": "MHDTVWorld",
                        "behaviorHints": behavior_hints,
                    }
                    for index, (stream_title, m3u8_url, behavior_hints) in enumerate(
                        m3u8_url_data, 1
                    )
                ],
            }
        )
        logging.info("Scraped %s", title)

    return channels_data


async def scrape_category(category_url, page):
    # Navigate to the category page
    await page.goto(category_url)

    # Collect all page URLs
    page_urls = [category_url] + [
        await link.get_attribute("href")
        for link in await page.query_selector_all("div.pagination a.inactive")
        if await link.get_attribute("href")
    ]

    logging.info("found %d pages", len(page_urls))

    # Scrape channels from each page
    channels_data = []
    for page_url in page_urls:
        await page.goto(page_url)
        try:
            channels_data.extend(await scrape_tv_channels(page))
        except Exception as e:
            logging.error("Failed to scrape %s. error: %s", page_url, e, exc_info=True)

    return channels_data


async def scrape_all_categories():
    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        page = await browser.new_page()

        # Extract category URLs
        await page.goto(BASE_URL)
        category_elements = await page.query_selector_all("#header a")
        category_urls = [
            urljoin(BASE_URL, await element.get_attribute("href"))
            for element in category_elements
        ]
        # remove duplicates and BASE_URL
        category_urls = list(set(category_urls))
        category_urls.remove(BASE_URL)
        category_urls.remove("https://mhdtv.org/")

        # Scrape channels from each category
        all_channels_data = []
        for category_url in category_urls:
            logging.info("Scraping %s", category_url)
            try:
                all_channels_data.extend(await scrape_category(category_url, page))
            except Exception as e:
                logging.error(
                    "Failed to scrape %s. error: %s", category_url, e, exc_info=True
                )

        await browser.close()

        # remove duplicates
        unique_channels = {channel["title"]: channel for channel in all_channels_data}
        unique_channels_data = list(unique_channels.values())

        logging.info("found %d channels", len(unique_channels_data))

        with open("tamilultra.json", "w") as file:
            json.dump({"channels": unique_channels_data}, file, indent=4)

        logging.info(
            "Done scraping TamilUltra. Manually verify the data & add it via /tv-metadata endpoint"
        )


def main(is_scraping: bool = True):
    if is_scraping:
        asyncio.run(scrape_all_categories())
        return

    with open("scrappers/temp.json") as file:
        channels = json.load(file)["channels"]

    for channel in channels:
        logging.info("Adding %s", channel["title"])
        response = requests.post(f"{MEDIAFUSION_URL}/tv-metadata", json=channel)

        try:
            response.raise_for_status()
        except requests.HTTPError as err:
            logging.info("Response data: %s", response.text)
            continue

        logging.info("Response data: %s", response.json())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape TamilUltra Live TV")
    parser.add_argument(
        "--no-scrape",
        action="store_true",
        help="Don't scrape TamilUltra. Use this option to add the data to MediaFusion",
    )
    args = parser.parse_args()
    main(not args.no_scrape)

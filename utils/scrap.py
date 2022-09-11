#!/usr/bin/env python3

import argparse
import asyncio
import logging
import re

import cloudscraper
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db import database, crud

homepage = "https://tamilblasters.cloud"

tamil_blaster_links = {
    "tamil": {
        "hdrip": f"{homepage}/index.php?/forums/forum/7-tamil-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "tcrip": f"{homepage}/index.php?/forums/forum/8-tamil-new-movies-tcrip-dvdscr-hdcam-predvd",
        "dubbed": f"{homepage}/index.php?/forums/forum/9-tamil-dubbed-movies-bdrips-hdrips-dvdscr-hdcam-in-multi-audios"
    },
    "malayalam": {
        "tcrip": f"{homepage}/index.php?/forums/forum/75-malayalam-new-movies-tcrip-dvdscr-hdcam-predvd",
        "hdrip": f"{homepage}/index.php?/forums/forum/74-malayalam-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "dubbed": f"{homepage}/index.php?/forums/forum/76-malayalam-dubbed-movies-bdrips-hdrips-dvdscr-hdcam"
    },
    "telugu": {
        "tcrip": f"{homepage}/index.php?/forums/forum/79-telugu-new-movies-tcrip-dvdscr-hdcam-predvd",
        "hdrip": f"{homepage}/index.php?/forums/forum/78-telugu-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "dubbed": f"{homepage}/index.php?/forums/forum/80-telugu-dubbed-movies-bdrips-hdrips-dvdscr-hdcam"
    },
    "hindi": {
        "tcrip": f"{homepage}/index.php?/forums/forum/87-hindi-new-movies-tcrip-dvdscr-hdcam-predvd",
        "hdrip": f"{homepage}/index.php?/forums/forum/86-hindi-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "dubbed": f"{homepage}/index.php?/forums/forum/88-hindi-dubbed-movies-bdrips-hdrips-dvdscr-hdcam"
    },
    "kannada": {
        "tcrip": f"{homepage}/index.php?/forums/forum/83-kannada-new-movies-tcrip-dvdscr-hdcam-predvd",
        "hdrip": f"{homepage}/index.php?/forums/forum/82-kannada-new-movies-hdrips-bdrips-dvdrips-hdtv",
        "dubbed": f"{homepage}/index.php?/forums/forum/84-kannada-dubbed-movies-bdrips-hdrips-dvdscr-hdcam"
    },
    "english": {
        "tcrip": f"{homepage}/index.php?/forums/forum/52-english-movies-hdcam-dvdscr-predvd",
        "hdrip": f"{homepage}/index.php?/forums/forum/53-english-movies-hdrips-bdrips-dvdrips"
    }
}


def get_scrapper_session():
    session = requests.session()
    adapter = HTTPAdapter(max_retries=Retry(total=10, read=10, connect=10, backoff_factor=0.3, allowed_methods=False))
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'firefox',
            'platform': 'windows',
            'mobile': False
        },
        delay=10,
        sess=session
    )
    return scraper


async def scrap_page(url, language, video_type):
    scraper = get_scrapper_session()
    response = scraper.get(url)
    response.raise_for_status()

    tamil_blasters = BeautifulSoup(response.content, "html.parser")

    movies = tamil_blasters.find("ol").select("li[data-rowid]")

    for movie in movies:
        movie = movie.find("a")
        data = re.search(r"^(.+\(\d{4}\))(.+)", movie.text.strip())
        try:
            title, video_quality = data[1].strip(), data[2].strip("[] ")
        except TypeError:
            logging.error(f"not able to parse: {movie.text}")
            continue
        logging.info(f"getting movie data for '{title}'")

        page_link = movie.get("href")
        response = scraper.get(page_link)
        movie_page = BeautifulSoup(response.content, "html.parser")
        try:
            magnet_link = movie_page.find("a", class_="magnet-plugin").get("href")
            info_hash = re.search(r"urn:btih:(.{32,40})&", magnet_link)[1]
        except AttributeError:
            logging.warning(f"skipping due to, megnet link not found in {page_link}")
            continue
        except TypeError:
            logging.error(f"not able to parse magnet link in : {page_link}")
            continue

        poster = movie_page.select_one("img[data-src]").get("data-src")
        created_at = movie_page.find("time").get("datetime")

        metadata = {"name": title, "catalog": f"{language}_{video_type}",
                    "video_qualities": {video_quality: info_hash},
                    "poster": poster, "created_at": created_at}
        await crud.save_movie_metadata(metadata)


async def scrap_homepage():
    scraper = get_scrapper_session()
    response = scraper.get(homepage)
    response.raise_for_status()
    tamil_blasters = BeautifulSoup(response.content, "html.parser")
    movie_list_div = tamil_blasters.find("div", class_="ipsWidget_inner ipsPad ipsType_richText")
    movie_list = movie_list_div.find_all("p")[2:-2]

    for movie in movie_list:
        data = re.search(r"^(.+\(\d{4}\))", movie.text.strip())
        try:
            title = data[1].strip()
        except TypeError:
            logging.error(movie.text)
            continue

        logging.info(f"getting movie data for '{title}'")
        video_qualities = movie.find_all("a")[:-1]
        metadata = {
            "name": title,
            "catalog": "any_any",
            "video_qualities": {},
        }

        for video_quality in video_qualities:
            video_quality_name = video_quality.text.strip("[]")

            page_link = video_quality.get("href")
            response = scraper.get(page_link)
            movie_page = BeautifulSoup(response.content, "html.parser")
            magnet_link = None
            try:
                magnet_link = movie_page.find("a", class_="magnet-plugin").get("href")
                info_hash = re.search(r"urn:btih:(.{32,40})&", magnet_link)[1]
            except AttributeError:
                logging.warning(f"skipping due to, megnet link not found in {page_link}")
                continue
            except TypeError:
                logging.error(magnet_link)
                continue

            poster = movie_page.select_one("img[data-src]").get("data-src")
            metadata["created_at"] = movie_page.find("time").get("datetime")
            metadata["poster"] = poster
            metadata["video_qualities"][video_quality_name] = info_hash

        if all([metadata.get("created_at"), metadata.get("poster"), metadata.get("video_qualities")]):
            await crud.save_movie_metadata(metadata)


async def run_scraper(language: str = None, video_type: str = None, pages: int = None, start_page: int = None,
                      is_scrape_home: bool = True):
    await database.init()
    if is_scrape_home:
        await scrap_homepage()
    else:
        try:
            scrap_link = tamil_blaster_links[language][video_type]
        except KeyError:
            logging.error(f"Unsupported language or video type: {language}_{video_type}")
            return
        for page in range(start_page, pages + 1):
            scrap_link = f"{scrap_link}/page/{page}/"
            logging.info(f"Scrap page: {page}")
            await scrap_page(scrap_link, language, video_type)
    logging.info(f"Scrap completed for : {language}_{video_type}")


async def run_schedule_scrape():
    for language in tamil_blaster_links:
        for video_type in tamil_blaster_links[language]:
            await run_scraper(language, video_type, pages=1, start_page=1, is_scrape_home=False)
    await run_scraper(is_scrape_home=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Scrap Movie metadata from TamilBlasters")
    parser.add_argument("--home", action="store_true", help="scrap home page")
    parser.add_argument("-l", "--language", help="scrap movie language", default="tamil")
    parser.add_argument("-t", "--video-type", help="scrap movie video type", default="hdrip")
    parser.add_argument("-p", "--pages", type=int, default=1, help="number of scrap pages")
    parser.add_argument("-s", "--start-pages", type=int, default=1, help="page number to start scrap.")
    args = parser.parse_args()

    logging.basicConfig(format='%(levelname)s::%(asctime)s - %(message)s', datefmt='%d-%b-%y %H:%M:%S',
                        level=logging.INFO)
    asyncio.run(run_scraper(args.language, args.video_type, args.pages, args.start_pages, args.home))

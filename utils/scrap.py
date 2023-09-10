#!/usr/bin/env python3

import argparse
import asyncio
import logging
import re

import cloudscraper
import requests
from bs4 import BeautifulSoup
from dateutil.parser import parse as dateparser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from db import database, crud
from utils.site_data import HOMEPAGE, TAMIL_BLASTER_LINKS
from utils.torrent import get_info_hash_from_url


def get_scrapper_session():
    session = requests.session()
    session.headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 7.1.2; MI 5X; Flow) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/347.0.0.268 Mobile Safari/537.36"
    }
    adapter = HTTPAdapter(max_retries=Retry(total=10, read=10, connect=10, backoff_factor=0.3))
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "android", "desktop": False}, delay=10, sess=session
    )
    return scraper


def extract_info_hash(movie_page):
    try:
        magnet_link = movie_page.find("a", class_="magnet-plugin").get("href")
        info_hash = re.search(r"urn:btih:(.{32,40})&", magnet_link).group(1)
        return info_hash
    except AttributeError:
        logging.warning("magnet link not found")
        try:
            torrent_link = movie_page.select_one("a[data-fileext='torrent']").get("href")
            return get_info_hash_from_url(torrent_link)
        except AttributeError:
            logging.warning("Torrent link not found either")
    except TypeError:
        logging.error("Not able to parse magnet link")


async def scrap_page(url, language, video_type):
    scraper = get_scrapper_session()
    response = scraper.get(url)
    response.raise_for_status()

    tamil_blasters = BeautifulSoup(response.content, "html.parser")

    try:
        movies = tamil_blasters.find("ol").select("li[data-rowid]")
    except AttributeError:
        logging.error(f"No data found for {language}:{video_type}")
        return

    for movie in movies:
        movie = movie.find("a")
        season = episode = None
        try:
            if video_type == "series":
                data = re.search(
                    r"^(.+\(\d{4}\)).*S(\d+).*EP?\s?(\d+|\(\d+\s?-\s?\d+\))(.+)",
                    movie.text.strip(),
                )
                title, season, video_quality = (
                    data[1],
                    int(data[2]),
                    data[4].strip("[] "),
                )
                episode = str(int(data[3])) if data[3].isdigit() else data[3].strip("()")
                metadata = {"type": "series"}
            else:
                data = re.search(r"^(.+\(\d{4}\))(.+)", movie.text.strip())
                title, video_quality = re.sub(r"\s+", " ", data[1].strip()), data[2].strip("[] ")
                metadata = {"type": "movie"}
        except TypeError:
            logging.error(f"not able to parse: {movie.text.strip()}")
            continue
        logging.info(f"getting movie data for '{title}'")

        page_link = movie.get("href")
        response = scraper.get(page_link)
        movie_page = BeautifulSoup(response.content, "html.parser")
        info_hash = extract_info_hash(movie_page)
        if not info_hash:
            logging.error(f"info hash not found for {page_link}")
            continue

        poster = movie_page.select_one("div[data-commenttype='forums'] img[data-src]").get("data-src")
        created_at = dateparser(movie_page.find("time").get("datetime"))

        metadata.update(
            {
                "name": title,
                "catalog": f"{language}_{video_type}",
                "video_qualities": {video_quality: info_hash},
                "poster": poster,
                "created_at": created_at,
                "season": season,
                "episode": episode,
            }
        )
        await crud.save_movie_metadata(metadata)


async def scrap_homepage():
    scraper = get_scrapper_session()
    response = scraper.get(HOMEPAGE)
    response.raise_for_status()
    tamil_blasters = BeautifulSoup(response.content, "html.parser")
    movie_list_div = tamil_blasters.select(
        "div[id='ipsLayout_mainArea'] div[class='ipsWidget_inner ipsPad ipsType_richText']"
    )[2]
    movie_list = movie_list_div.find_all("p")[2:-2]

    for movie in movie_list:
        if re.search(r"S(\d+).*EP?\s?(\d+|\(\d+\s?-\s?\d+\))", movie.text.strip()):
            continue

        data = re.search(r"^(.+\(\d{4}\))", movie.text.strip())
        try:
            title = re.sub(r"\s+", " ", data[1].strip())
        except TypeError:
            logging.error(movie.text)
            continue

        logging.info(f"getting movie data for '{title}'")
        video_qualities = movie.find_all("a")[:-1]
        metadata = {
            "name": title,
            "catalog": "any_any",
            "video_qualities": {},
            "type": "movie",
            "season": None,
            "episode": None,
        }

        for video_quality in video_qualities:
            video_quality_name = video_quality.text.strip("[]")

            page_link = video_quality.get("href")
            response = scraper.get(page_link)
            movie_page = BeautifulSoup(response.content, "html.parser")
            info_hash = extract_info_hash(movie_page)
            if not info_hash:
                logging.error(f"info hash not found for {page_link}")
                continue

            poster = movie_page.select_one("div[data-commenttype='forums'] img[data-src]").get("data-src")
            metadata["created_at"] = dateparser(movie_page.find("time").get("datetime"))
            metadata["poster"] = poster
            metadata["video_qualities"][video_quality_name] = info_hash

        if all(
            [
                metadata.get("created_at"),
                metadata.get("poster"),
                metadata.get("video_qualities"),
            ]
        ):
            await crud.save_movie_metadata(metadata)


async def run_scraper(
    language: str = None,
    video_type: str = None,
    pages: int = None,
    start_page: int = None,
    is_scrape_home: bool = True,
):
    await database.init()
    if is_scrape_home:
        await scrap_homepage()
    else:
        try:
            scrap_link = TAMIL_BLASTER_LINKS[language][video_type]
        except KeyError:
            logging.error(f"Unsupported language or video type: {language}_{video_type}")
            return
        for page in range(start_page, pages + start_page):
            scrap_link = f"{scrap_link}/page/{page}/"
            logging.info(f"Scrap page: {page}")
            await scrap_page(scrap_link, language, video_type)
    logging.info(f"Scrap completed for : {language}_{video_type}")


async def run_schedule_scrape(pages: int = 1, start_page: int = 1):
    for language in TAMIL_BLASTER_LINKS:
        for video_type in TAMIL_BLASTER_LINKS[language]:
            await run_scraper(language, video_type, pages=pages, start_page=start_page, is_scrape_home=False)
    await run_scraper(is_scrape_home=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrap Movie metadata from TamilBlasters")
    parser.add_argument("--all", action="store_true", help="scrap all type of movies & series")
    parser.add_argument("--home", action="store_true", help="scrap home page")
    parser.add_argument("-l", "--language", help="scrap movie language", default="tamil")
    parser.add_argument("-t", "--video-type", help="scrap movie video type", default="hdrip")
    parser.add_argument("-p", "--pages", type=int, default=1, help="number of scrap pages")
    parser.add_argument("-s", "--start-pages", type=int, default=1, help="page number to start scrap.")
    args = parser.parse_args()

    logging.basicConfig(
        format="%(levelname)s::%(asctime)s - %(message)s",
        datefmt="%d-%b-%y %H:%M:%S",
        level=logging.INFO,
    )
    if args.all:
        asyncio.run(run_schedule_scrape(args.pages, args.start_pages))
    else:
        asyncio.run(run_scraper(args.language, args.video_type, args.pages, args.start_pages, args.home))

# Scrapping with Media Fusion

Currently, the project supports scraping from TamilBlasters. Here's how to use the scraper:

1. **Setup Playwright**:

   If you intend to use the scraper with Playwright, you need to ensure the Playwright browser binaries are installed. Specifically, for Firefox:

   ```bash
   pipenv run playwright install firefox
   ```

2. **Get Help on Available Options **:
   
   To understand the available options for the scraper, run:
    
   ```bash
   pipenv run python3 -m scrappers.tamil_blasters_scrapper --help
   ```
   
   This will display the available options:
    
   ```bash
   usage: tamil_blasters_scrapper.py [-h] [--all] [-l {tamil,malayalam,telugu,hindi,kannada,english}] [-t {hdrip,tcrip,dubbed,series}] [-p PAGES] [-s START_PAGES] [-k SEARCH_KEYWORD] [--scrap-with-playwright] [--proxy-url PROXY_URL]
   
   Scrap Movie metadata from TamilBlasters
   
   options:
     -h, --help            show this help message and exit
     --all                 scrap all type of movies & series
     -l {tamil,malayalam,telugu,hindi,kannada,english}, --language {tamil,malayalam,telugu,hindi,kannada,english}
                           scrap movie language
     -t {hdrip,tcrip,dubbed,series}, --video-type {hdrip,tcrip,dubbed,series}
                           scrap movie video type
     -p PAGES, --pages PAGES
                           number of scrap pages
     -s START_PAGES, --start-pages START_PAGES
                           page number to start scrap.
     -k SEARCH_KEYWORD, --search-keyword SEARCH_KEYWORD
                           search keyword to scrap movies & series. ex: 'bigg boss'
     --scrap-with-playwright
                           scrap with playwright
     --proxy-url PROXY_URL
                           proxy url to scrap. ex: socks5://127.0.0.1:1080
   ```

3. **Run the Scraper**:

   Execute the scraper with the desired options based on your requirements.

   e.g. To scrap all movies & series, run:
   
   ```bash
   pipenv run python3 -m scrappers.tamil_blasters_scrapper --all --scrap-with-playwright
   ```

   Note: You may have to solve the cloudflare validation challenge manually when its required.

# Scraping with Media Fusion

The project offers scraping capabilities for TamilBlasters and TamilMV. Here are the guidelines on how to use these scrapers:

## TamilBlasters

1. **Setup Playwright**:

   If you intend to use the scraper with Playwright, you need to ensure the Playwright browser binaries are installed. Specifically, for Firefox:

   ```bash
   pipenv run playwright install firefox
   ```

2. **Get Help on Available Options**:
   
   To understand the available options for the scraper, run:
    
   ```bash
   pipenv run python3 -m scrapers.tamil_blasters --help
   ```

3. **Run the Scraper**:

   Execute the scraper with the desired options based on your requirements.

   e.g. To scrap all movies & series, run:
   
   ```bash
   pipenv run python3 -m scrapers.tamil_blasters --all --scrap-with-playwright
   ```

   Note: You may have to solve the cloudflare validation challenge manually when its required.


## TamilMV

1. **Get Help on Available Options**:

    To understand the available options for the TamilMV scraper, run:

    ```bash
     pipenv run python3 -m scrapers.tamilmv --help
    ```

2. **Run the TamilMV Scraper**:

    Execute the scraper with the desired options based on your requirements.

    e.g. To scrap all movies & series from TamilMV for 5 pages, run:

    ```bash
     pipenv run python3 -m scrapers.tamilmv --all -p 5
    ```

    Note: Ensure you have Playwright set up as mentioned in the TamilBlasters section if you intend to use it with the TamilMV scraper.
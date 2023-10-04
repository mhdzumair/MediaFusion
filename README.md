# Media Fusion Stremio Addon :clapper:

![Media Fusion Logo](resources/images/mediafusion_logo.png?raw=true)

## :warning: Disclaimer :warning:

> The content of this script is created strictly for educational purposes. Use of the Add-on is at your own risk.
> This Add-on, written in Python, serves as an API for [stremio](https://www.stremio.com/).
> There is no affiliation with any scraping sites.

## :sparkles: Features

- Provides catalogs for multiple languages: Tamil, Hindi, Malayalam, Kannada, English, and dubbed movies & series.
  
  ![Media Fusion Catalog](resources/images/ss1.png?raw=true)

- Supports streams for playback with torrent, Real Debrid, and Seedr integration.
  
  ![Media Fusion Streams](resources/images/ss2.png?raw=true)

## :rocket: Installation

1. Install Stremio from [here](https://www.stremio.com/downloads).
2. Navigate to [Media Fusion](https://882b9915d0fe-mediafusion.baby-beamup.club) and click on the 'Configure Add-on' button.

## :hammer_and_wrench: Development

### Prerequisites

- **Python**: This project uses Python version 3.11. Ensure you have it installed.
- **MongoDB**: Set up a MongoDB server. You can use [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) to create a free MongoDB cluster.

### Setup

1. **Pipenv**: Use [Pipenv](https://pipenv.pypa.io/en/latest/) for managing project dependencies. If you don't have it installed, you can install it with:
   ```bash
   pip install pipenv
   ```
2. **Clone**: Clone this repository.
   ```bash
   git clone https://github.com/mhdzumair/MediaFusion
   ```
3. **Environment Variables**: Create a `.env` file in the root directory with the following variables:
    ```bash
    MONGO_URI=<Your_MongoDB_URI>
    SECRET_KEY=<Your_Random_32_Character_Secret>
    HOST_URL=http://127.0.0.1:8000
    ```
4. **For scraping instructions**: refer to the [scrapping README](/scrappers/README.md).

## :books: References

- [Stremio Generic Add-on Guide](https://stremio.github.io/stremio-addon-guide/basics)
- [Stremio Add-on SDK API docs](https://github.com/Stremio/stremio-addon-sdk/tree/master/docs/api)
- [Deploy Stremio Addon](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/deploying/beamup.md)
- [FastAPI](https://fastapi.tiangolo.com/)
- [beautifulsoup4](https://beautiful-soup-4.readthedocs.io/en/latest/)
- [cinemagoer](https://cinemagoer.readthedocs.io/en/latest/)
- [beanie](https://roman-right.github.io/beanie/)
- [parse-torrent-title](https://github.com/platelminto/parse-torrent-title)
- [torrentio-scraper Stremio Add-on](https://github.com/TheBeastLT/torrentio-scraper)

# 🎬 Media Fusion Stremio Addon

![Media Fusion Logo](resources/images/mediafusion_logo.png?raw=true)

## ⚠️ Disclaimer ⚠️

> Embark on a cinematic journey like never before! Please be aware that this script is crafted exclusively for educational purposes.
> Any use of this Add-on is an adventure you undertake at your own risk.
> This Python-powered marvel serves as a dedicated API for the sensational world of [Stremio](https://www.stremio.com/), but rest assured, there are no secret alliances with any scraping sites

## ✨ Features

- :globe_with_meridians: **An Expedition Through Languages**: Immerse yourself in an array of cinematic cultures with catalogs spanning Tamil, Hindi, Malayalam, Kannada, English, and even the enchanting
  realm of dubbed movies & series.

  ![Media Fusion Catalog](resources/images/ss1.png?raw=true)

- :tv: **Seamless Streaming**: Experience entertainment in its purest form with support for streaming via torrents, Real Debrid, and the magic of Seedr integration.

  ![Media Fusion Streams](resources/images/ss2.png?raw=true)

## 🚀 Installation

1. **Ready for Takeoff**: Launch your Stremio voyage by installing it from [here](https://www.stremio.com/downloads).
2. **Chart Your Course**: Navigate to [Media Fusion](https://882b9915d0fe-mediafusion.baby-beamup.club) and set sail with a single click on the `Configure Add-on` button.

## 🔧 Development

### Prerequisites

- **Python**: Assemble your coding crew with Python version 3.11. Ensure it's on board and ready for action.
- **MongoDB**: Secure a treasure chest in the form of a MongoDB server. You can enlist the services of [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) to create your very own cluster, free of charge.

### Setup

1. **Pipenv**: Equip your project with the finest tools using [Pipenv](https://pipenv.pypa.io/en/latest/), a trusty companion for managing dependencies. If it's not already in your arsenal, acquire it with a simple command:
   ```bash
   pip install pipenv
   ```
3. **Clone**: Clone this repository.
   ```bash
   git clone https://github.com/mhdzumair/MediaFusion
   ```
4. **Environment Variables**: Create a `.env` file in the root directory with the following variables:
    ```bash
    MONGO_URI=<Your_MongoDB_URI>
    SECRET_KEY=<Your_Random_32_Character_Secret>
    HOST_URL=http://127.0.0.1:8000
    ```
5. **For scraping instructions**: refer to the [scrapping README](/scrappers/README.md).

## 📚 References

<table>
    <tr><td><a href="https://stremio.github.io/stremio-addon-guide/basics">Stremio Generic Add-on Guide</a></td>
      <td><a href="https://github.com/Stremio/stremio-addon-sdk/tree/master/docs/api">Stremio Add-on SDK API docs</a></td></tr>
    <tr><td><a href="https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/deploying/beamup.md">Deploy Stremio Addon</a></td>
      <td><a href="https://fastapi.tiangolo.com/">FastAPI</a></td></tr>
    <tr><td><a href="https://beautiful-soup-4.readthedocs.io/en/latest/">beautifulsoup4</a></td>
      <td><a href="https://cinemagoer.readthedocs.io/en/latest/">cinemagoer</a></td></tr>
    <tr><td><a href="https://roman-right.github.io/beanie/">beanie</a></td>
      <td><a href="https://github.com/platelminto/parse-torrent-title">parse-torrent-title</a></td></tr>
    <tr><td colspan="2"><a href="https://github.com/TheBeastLT/torrentio-scraper">torrentio-scraper Stremio Add-on</a></td></tr>
</table>

# Media Fusion Stremio Addon 🎬

![Media Fusion Logo](resources/images/mediafusion_logo.png?raw=true)

## ⚠️ Disclaimer

> The content of this script is created strictly for educational purposes. Use of the Add-on is at your own risk. This Add-on, written in Python, serves as an API for [Stremio](https://www.stremio.com/). There is no affiliation with any scraping sites.

## ✨ Features

- **Rich Catalogs**: Offers extensive catalogs for multiple languages including Tamil, Hindi, Malayalam, Kannada, English, and dubbed movies, series & live tv.
  
  ![Media Fusion Catalog](resources/images/ss1.png?raw=true)

- **Enhanced Streaming with Various Providers**: Supports streams for playback, integrating various torrent and cloud storage services. Available providers include:
  - **Direct Torrent** (Free)
  - **PikPak** (Free Quota)
  - **Seedr.cc** (Free Quota)
  - **OffCloud** (Free Quota)
  - **Torbox** (Free Quota)
  - **Real-Debrid** (Premium)
  - **Debrid-Link** (Premium)
  - **Premiumize** (Premium)
  - **AllDebrid** - Local Only - (Premium) *(Note: AllDebrid works only when running the addon locally)*

  ![Media Fusion Streams](resources/images/ss2.png?raw=true)

- **scraper Support**:
  - **Prowlarr Integration**: Enhances scraping capabilities through Prowlarr integration.
  - **Torrentio Streams**: Supports scraping from Torrentio streams (disabled by default).
  - **Regional Content scraping**: Dedicated scraping for TamilMV and TamilBlasters movies and series, along with TamilUltra and MHDTVPlay for Live TV channels.

- **Additonal Features**:
  - **User Data Encryption**: User data is encrypted upon configuring the addon, ensuring privacy and security. Only the encrypted URL is stored on Stremio, and it is passed as an encrypted string for each request.
  - **Watchlist Catalog Support**: Integrates streaming provider watchlist directly into the catalog (only show when the movie's metadata available in MediaFusion DB)
  - **Stream Filters**: Users can filter streams based on file size and resolution type, allowing for a customized viewing experience.

## 🚀 Installation

1. **Stremio**: Install Stremio from [here](https://www.stremio.com/downloads).
2. **Media Fusion**: Navigate to [Media Fusion](https://mediafusion.fun) and click on the 'Configure Add-on' button.

## 🚀 Local Add-on Deployment

For detailed instructions on local deployment, check the [Local Deployment Guide](deployment/README.md).

## ✨ Contributors

A special thank you to all our contributors!

<a href="https://github.com/mhdzumair/MediaFusion/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=mhdzumair/MediaFusion" />
</a>

## 📚 References

- [Stremio Generic Add-on Guide](https://stremio.github.io/stremio-addon-guide/basics)
- [Stremio Add-on SDK API docs](https://github.com/Stremio/stremio-addon-sdk/tree/master/docs/api)
- [Deploy Stremio Addon](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/deploying/beamup.md)
- [FastAPI](https://fastapi.tiangolo.com/)
- [beautifulsoup4](https://beautiful-soup-4.readthedocs.io/en/latest/)
- [cinemagoer](https://cinemagoer.readthedocs.io/en/latest/)
- [beanie](https://roman-right.github.io/beanie/)
- [parse-torrent-title](https://github.com/platelminto/parse-torrent-title)
- [torrentio-scraper Stremio Add-on](https://github.com/TheBeastLT/torrentio-scraper)
- [kubernetes](https://kubernetes.io/)
- [docker](https://www.docker.com/)
- [dramatiq](https://dramatiq.io/)
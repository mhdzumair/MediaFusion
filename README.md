# Media Fusion Stremio Addon 🎬

![Media Fusion Logo](resources/images/mediafusion_logo.png?raw=true)

## ⚠️ Disclaimer

> The content of this script is created strictly for educational purposes. Use of the Add-on is at your own risk. This Add-on, written in Python, serves as an API for [Stremio](https://www.stremio.com/). There is no affiliation with any scraping sites.

## ✨ Features

- **Rich Catalogs**: Offers extensive catalogs for multiple languages including Tamil, Hindi, Malayalam, Kannada, English, and dubbed movies, series & live tv.
  
  ![Media Fusion Catalog](resources/images/ss1.png?raw=true)

- **Enhanced Streaming with Various Providers**: Seamless playback from a diverse array of torrent and cloud storage services:
  - 📥 **Direct Torrent** (Free)
  - 🌩️ **PikPak** (Free Quota)
  - 🌱 **Seedr.cc** (Free Quota)
  - ☁️ **OffCloud** (Free Quota)
  - 📦 **Torbox** (Free Quota)
  - 💎 **Real-Debrid** (Premium)
  - 🔗 **Debrid-Link** (Premium)
  - ✨ **Premiumize** (Premium)
  - 🏠 **AllDebrid** - Local Only / ElfHosted - (Premium) *(Note: AllDebrid works only when running the addon locally or use ElfHosted version)*
  - 🔒 **qBittorrent** - WebDav (Free/Premium)

  ![Media Fusion Streams](resources/images/ss2.png?raw=true)

- **Scraper Support**:
  - 🏎️ **Formula Racing**: Exclusive scraping from TorrentGalaxy for all your racing needs.
  - 🏈🏀⚾⚽🏒🏉🎾 **American Football, Basketball, Baseball, Football, Hockey, Rugby/AFL, and Other Sports**: Now all scraping through sport-video.org.ua for catchup videos.
  - 🏈🏀⚾⚽🏒🏉🎾🏏 **Sports Live Events**: Watch live sports events from streamed.su, mrgamingstreams and crictime.com
  - 🎥 **TamilMV**: Specialized scraping for regional contents.
  - 🌟 **TamilBlasters**: Dedicated access to an extensive library of regional content.
  - 📺 **TamilUltra & MHDTVPlay**: Get the best of Live TV channels right at your fingertips.
  - 🔄 **Prowlarr Integration**: Supercharge your scraping streams with Prowlarr's powerful integration.
  - 🌊 **Torrentio/KnightCrawler Streams**: Optional scraping streams directly from Torrentio/KnightCrawler streams for even more variety.

- **Additional Features**:
  - 🔒 **API Security**: Fortify your self-hosted API with a password to prevent unauthorized access.
  - 🔐 **User Data Encryption**: Encrypt user data for heightened privacy and security, storing only encrypted URLs on Stremio.
  - 📋 **Watchlist Catalog Support**: Sync your streaming provider's watchlist directly into the MediaFusion catalog for a personalized touch.
  - ⚙️ **Stream Filters**: Customize your viewing experience with filters that sort streams by file size, resolution, seeders and much more.
  - 🖼️ **Poster with Title**: Display the poster with the title for a more visually appealing catalog on sport events.
  - 📺 **M3U Playlist Import**: Import M3U playlists for a more personalized streaming experience.
  - ✨ **Manual Scraper Triggering UI**: Manage your scraping sources with a manual trigger UI for a more hands-on approach.
  - 🗑️ **Delete Watchlist**: Delete your watchlist from the stremio for quick control over your content.

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
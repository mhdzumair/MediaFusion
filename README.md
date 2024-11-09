# Media Fusion Add-On For Stremio & Kodi 🎬

![Media Fusion Logo](resources/images/mediafusion_logo.png?raw=true)

## ⚠️ Disclaimer

> The content of this script is created strictly for educational purposes. Use of the Add-on is at your own risk. This Add-on, written in Python, serves as an API for [Stremio](https://www.stremio.com/). There is no affiliation with any scraping sites.

## ✨ Features

- **Rich Catalogs**: Offers extensive catalogs for multiple languages including Tamil, Hindi, Malayalam, Kannada, English, and dubbed movies, series & live tv.
  
  ![Media Fusion Catalog](resources/images/ss1.png?raw=true)

- **Enhanced Streaming with Various Providers**: Seamless playback from a diverse array of torrent and cloud storage services:
  - 📥 **Direct Torrent** (Free)
  - 🌩️ **PikPak** (Free Quota / Premium)
  - 🌱 **Seedr.cc** (Free Quota / Premium)
  - ☁️ **OffCloud** (Free Quota / Premium)
  - 📦 **Torbox** (Free Quota / Premium)
  - 💎 **Real-Debrid** (Premium)
  - 🔗 **Debrid-Link** (Premium)
  - ✨ **Premiumize** (Premium)
  - 🏠 **AllDebrid** (Premium)
  - 🔒 **qBittorrent** - WebDav (Free/Premium)

  ![Media Fusion Streams](resources/images/ss2.png?raw=true)

- **Advanced Scraper Support**:
  - 🏎️ **Formula Racing**: Exclusive scraping from TorrentGalaxy for all your racing needs.
  - 🥊 **Fighting Sports**: Catch up on all the latest fighting sports content from UFC and WWE.
  - 🏈🏀⚾⚽🏒🏉🎾 **American Football, Basketball, Baseball, Football, Hockey, Rugby/AFL, and Other Sports**: Now all scraping through sport-video.org.ua for catchup videos.
  - 🏈🏀⚾⚽🏒🏉🎾🏏 **Sports Live Events**: Watch live sports events from streamed.su, streambtw.com
  - 🎥 **TamilMV**: Specialized scraping for regional contents.
  - 🌟 **TamilBlasters**: Dedicated access to an extensive library of regional content.
  - 📺 **TamilUltra & NowMeTV**: Get the best of Live TV channels right at your fingertips.
  - 🔄 **Prowlarr Integration**: Supercharge your scraping streams with Prowlarr's powerful integration.
  - 🔍 **Advanced Prowlarr Integration**: Improved Prowlarr feed scraping for more comprehensive content discovery with latest updates.
  - 🌊 **Torrentio/KnightCrawler Streams**: Optional scraping streams directly from Torrentio/KnightCrawler streams for even more variety.
  - 🔍 **Zilean DMM Search**: Search for movies and TV shows with [Zilean DMM](https://github.com/iPromKnight/zilean) for cached debrid contents.
  - 📺 **MPD DRM Scraping**: Scraping MPD & Support streaming functionality with MediaFlow MPD DRM support.


- **Additional Features**:
  - 🔒 **API Security**: Fortify your self-hosted API with a password to prevent unauthorized access.
  - 🔐 **User Data Encryption**: Encrypt user data for heightened privacy and security, storing only encrypted URLs on Stremio.
  - 📋 **Watchlist Catalog Support**: Sync your streaming provider's watchlist directly into the MediaFusion catalog for a personalized touch.
  - ⚙️ **Stream Filters**: Customize your viewing experience with filters that sort streams by file size, resolution, seeders and much more.
  - 🖼️ **Poster with Title**: Display the poster with the title for a more visually appealing catalog on sport events.
  - 📺 **M3U Playlist Import**: Import M3U playlists for a more personalized streaming experience.
  - ✨ **Manual Scraper Triggering UI**: Manage your scraping sources with a manual trigger UI for a more hands-on approach.
  - 🗑️ **Delete Watchlist**: Delete your watchlist from the stremio for quick control over your content.
  - 🔍 **Prowlarr Indexer Support**: Use [MediaFusion as an indexer in Prowlarr](/resources/yaml/mediafusion.yaml) for searching movies and TV shows with Radarr and Sonarr.
  - 🔞 **Parental Controls**: Filter content based on nudity and certification ratings.
  - 🎬 **IMDb Integration**: Display IMDb ratings with the logo for quick quality assessment.
  - 🕰️ **Sports Event Timing**: View the time for sports events directly on the poster for better planning.
  - 🌐 **MediaFlow Proxy**: Support for MediaFlow Proxy for Debrid and Live streams, enhancing accessibility.
  - 🎥 **RPDB Posters**: RPDB posters support with fallback to MediaFusion posters.
  - 📥 **Browser Download Support**: Support for downloading video files from debrid services directly in the browser.
  - 🚫 **Support DMCA Take Down**: Torrent blocking feature for DMCA compliance.

## 🚀 Installation Guide

### Stremio Add-on Installation

1. **Stremio**: Install Stremio from [here](https://www.stremio.com/downloads).
2. **MediaFusion Community Instance**: Navigate to [MediaFusion ElfHosted](https://mediafusion.elfhosted.com/) and click on the 'Configure Add-on' button.

### Kodi Add-on Installation

#### Method 1: Install via Repository (Recommended)
1. Launch Kodi
2. Go to Settings (⚙️ gear icon) → File manager
3. Click on "Add source"
4. In the "Add file source" dialog, click `<None>` and enter exactly:
   ```
   https://mhdzumair.github.io/MediaFusion
   ```
5. In the "Enter a name for this media source" field, enter `MediaFusion` and click "OK"
6. Go back to Kodi home screen
7. Click Add-ons
8. Click the Add-on browser (box icon) 
9. Click "Install from zip file"
10. Click "MediaFusion"
11. Select the repository zip file (e.g., `repository.mediafusion-4.1.1.zip`)
12. Wait for the "MediaFusion Repository add-on installed" notification
13. Click "Install from repository"
14. Select "MediaFusion Repository"
15. Go to "Video add-ons"
16. Select "MediaFusion"
17. Click "Install"
18. Configure the add-on by going to Add-ons → My add-ons → Video add-ons → MediaFusion → Configure

#### Method 2: Manual Installation
1. **Kodi**: Install Kodi from [here](https://kodi.tv/download)
2. **MediaFusion Zip**: Download the latest [plugin.video.mediafusion.zip](https://github.com/mhdzumair/MediaFusion/releases) from releases
3. Launch Kodi
4. Go to Add-ons → Add-on browser (box icon) → Install from zip file
5. Navigate to the downloaded zip file and select it
6. Wait for the "MediaFusion add-on installed" notification
7. Configure the add-on by going to Add-ons → My add-ons → Video add-ons → MediaFusion → Configure

> **Note**: Installing via repository (Method 1) is recommended as it enables automatic updates when new versions are released.

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
- [torrentio Stremio Add-on](https://github.com/TheBeastLT/torrentio-scraper)
- [kubernetes](https://kubernetes.io/)
- [docker](https://www.docker.com/)
- [dramatiq](https://dramatiq.io/)
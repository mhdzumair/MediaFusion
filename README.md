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
  - 🟩 **Torbox** (Free Quota / Premium)
  - 💎 **Real-Debrid** (Premium)
  - 🔗 **Debrid-Link** (Premium)
  - ✨ **Premiumize** (Premium)
  - 🏠 **AllDebrid** (Premium)
  - 🔒 **qBittorrent** - WebDav (Free/Premium)
  - 🪄 [**StremThru**](https://github.com/MunifTanjim/stremthru) (Interface)

  ![Media Fusion Streams](resources/images/ss2.png?raw=true)

- **Advanced Scraper Support**:
  - 🏎️ **Formula Racing**: Exclusive scraping from TorrentGalaxy for all your racing needs.
  - 🥊 **Fighting Sports**: Catch up on all the latest fighting sports content from UFC and WWE.
  - 🏈🏀⚾⚽🏒🏉🎾 **American Football, Basketball, Baseball, Football, Hockey, Rugby/AFL, and Other Sports**: Now all scraping through sport-video.org.ua for catchup videos.
  - 🏈🏀⚾⚽🏒🏉🎾🏏 **Sports Live Events**: Watch live sports events from DaddyLiveHD
  - 🎥 **TamilMV**: Specialized scraping for regional contents.
  - 🌟 **TamilBlasters**: Dedicated access to an extensive library of regional content.
  - 📺 **TamilUltra & NowMeTV**: Get the best of Live TV channels right at your fingertips.
  - 🔄 **Prowlarr Integration**: Supercharge your scraping streams with Prowlarr's powerful integration.
  - 🔍 **Advanced Prowlarr Integration**: Improved Prowlarr feed scraping for more comprehensive content discovery with latest updates.
  - 📰 **RSS Feed Scraper**: Automated RSS feed monitoring and scraping with intelligent catalog detection, and parsing with regex patterns.
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
  - 🤝 **Manual Torrent Contribution**: Add support for manual torrent contribution and private and webseeded torrent import support. [Webseed Creator Colab Link](https://colab.research.google.com/github/mhdzumair/MediaFusion/blob/main/docs/TorrentWebCreator.ipynb) <img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/>
  - 🔍 **Jackett Indexer Support**: Add support for Jackett indexer with AKA title searching and individual search.
  - 📰 **RSS Feed Manager**: Comprehensive RSS feed management system with custom parsing patterns, filtering options, and automated scheduling.

## 🚀 Installation Guide

### Stremio Add-on Installation

1. **Stremio**: Install Stremio from [here](https://www.stremio.com/downloads)
2. **MediaFusion Community Instance**: Navigate to [MediaFusion ElfHosted](https://mediafusion.elfhosted.com/) and click on the 'Configure Add-on' button

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
    > If it says "Could not connect to repository", close Kodi and open it again and go to add-ons and start from step 13
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

### Contribution Stream - Browser Extension Installation

🌐 **MediaFusion Browser Extension** - Easily contribute torrents to MediaFusion directly from any torrent site!

#### Firefox (Desktop & Android)
**Easy Installation:**
1. **Desktop**: Visit [MediaFusion Torrent Uploader](https://addons.mozilla.org/en-US/firefox/addon/mediafusion-torrent-uploader/) on Mozilla Add-ons
2. **Android**: Available on Firefox for Android! Install from the same Mozilla Add-ons link
3. Click "Add to Firefox"
4. Configure your MediaFusion instance URL in the extension settings

#### Chrome/Edge (Manual Installation)
**Note:** Chrome Web Store version is pending review. Until approved:
1. Download the extension package from [Releases](https://github.com/mhdzumair/MediaFusion/releases/tag/4.3.35)
2. Extract the downloaded ZIP file (`mediafusion-extension-chrome.zip`)
3. Open Chrome/Edge and go to Extensions page (`chrome://extensions/` or `edge://extensions/`)
4. Enable "Developer mode"
5. Click "Load unpacked" and select the extracted folder
6. Configure your MediaFusion instance URL in the extension settings

#### ✨ Key Features
- 🔍 **Auto-detection** of torrents on popular sites (1337x, PirateBay, YTS, etc.)
- ⚡ **Quick Import** - One-click upload with automatic metadata detection
- 📦 **Bulk Upload** - Select and upload multiple torrents at once from torrent sites
- 🎯 **Smart metadata matching** with IMDb integration
- 🌍 **Multi-language support** with 50+ languages
- 📱 **Mobile support** - Works on Firefox Android
- ⚙️ **Configurable settings** for different MediaFusion instances
- 🎬 **Support for movies, TV series, and sports content**

For detailed installation and usage instructions, see the [Browser Extension README](browser-extension/README.md).

## ⚙️ Configuration Guide

📺 For a detailed video guide on configuration, check out: https://www.youtube.com/watch?v=ctQY8r1KzPM&t=85s

### Setting Up MediaFusion in Stremio

1. Visit [MediaFusion ElfHosted](https://mediafusion.elfhosted.com/configure)
2. Configure your desired options
3. Install the addon using one of these methods:
   - **For Desktop/Mobile Stremio App Users**:
     - Click "Install in Stremio" button
     - Stremio will open automatically with installation prompt
   - **For Web/iOS Users**:
     - Click "Copy Manifest URL"
     - Open Stremio
     - Go to Addons
     - Click "+ Add Addon"
     - Paste the manifest URL
     - Click Install

### Setting Up MediaFusion in Kodi

1. Navigate to MediaFusion settings in Kodi
2. Select "Configure/Reconfigure Secret String"
3. Access the configuration page through one of these methods:
   - Scan the QR code
   - Visit https://mediafusion.elfhosted.com/configure
   - Open the configuration page directly from Kodi

### Configure Addon Options
1. In the configuration page:
   - Enable all necessary catalogs
   - Turn on "Enable IMDb Meta Data Response"
2. Click on "Setup Kodi Addon"
3. Enter the 6-digit key shown in Kodi
   > If the key expires, click "Configure Secret" in Kodi settings to refresh it
4. Submit the configuration
5. Verify that the "secret string" value is populated
6. Return to the main menu

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

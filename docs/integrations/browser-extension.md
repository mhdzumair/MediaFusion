# Browser Extension

The MediaFusion Browser Extension lets you contribute torrents to your MediaFusion instance directly from any torrent site with one click.

## Features

- Auto-detects torrent and magnet links on popular torrent sites
- One-click upload with automatic metadata detection (title, year, type)
- Bulk upload — select multiple torrents at once
- IMDb integration for smart metadata matching
- 50+ language support
- Works on Firefox Android

## Installation

=== "Firefox (recommended)"

    Install from the [Mozilla Add-ons store](https://addons.mozilla.org/en-US/firefox/addon/mediafusion-torrent-uploader/):

    1. Visit the add-on page
    2. Click **Add to Firefox**
    3. Confirm installation
    4. Click the MediaFusion icon in your toolbar and enter your instance URL

=== "Chrome / Edge"

    The Chrome Web Store version is pending review. Install manually:

    1. Download the extension package from [GitHub Releases](https://github.com/mhdzumair/MediaFusion/releases) (`mediafusion-extension-chrome.zip`)
    2. Extract the ZIP
    3. Open `chrome://extensions/` (or `edge://extensions/`)
    4. Enable **Developer mode**
    5. Click **Load unpacked** → select the extracted folder
    6. Configure your instance URL in the extension settings

=== "Firefox Android"

    Install from the same [Mozilla Add-ons link](https://addons.mozilla.org/en-US/firefox/addon/mediafusion-torrent-uploader/) on Firefox for Android.

## Configuration

After installation, click the extension icon in your browser toolbar:

- **MediaFusion URL**: the base URL of your instance (e.g. `https://mediafusion.yourdomain.com`)
- **API Password**: your instance's `API_PASSWORD`
- **Uploader name**: optional name shown alongside contributed torrents

## How to contribute a torrent

1. Browse to a torrent site
2. The extension detects magnet links and torrent files on the page
3. Click the extension icon to see detected torrents
4. Select one or more and click **Upload**
5. MediaFusion imports the torrent(s) and indexes them in your catalog

## Manual torrent import

You can also import torrents directly from the MediaFusion UI:

1. Open your instance → **Scraper** (admin page)
2. Use the **Manual Torrent Import** section to paste a magnet link or upload a `.torrent` file

## Webseed torrent creator

For creating webseeded torrent files (useful for HTTP-hosted content), use the [Colab notebook](https://colab.research.google.com/github/mhdzumair/MediaFusion/blob/main/docs/TorrentWebCreator.ipynb).

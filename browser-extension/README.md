# MediaFusion Browser Extension

A browser extension that allows users to easily contribute torrents to MediaFusion directly from torrent sites like 1337x, PirateBay, UIndex, and others.

## Features

- **Auto-detection**: Automatically detects torrent and magnet links on popular torrent sites
- **One-click upload**: Add torrents to MediaFusion with a single click
- **Smart metadata extraction**: Automatically extracts and analyzes torrent metadata
- **Configuration management**: Persistent settings for MediaFusion URL and uploader name
- **Multi-browser support**: Works on Chrome, Firefox, and other Chromium-based browsers
- **Site-specific handlers**: Optimized for popular torrent sites

## Supported Sites

- 1337x.to
- The Pirate Bay
- UIndex
- RARBG
- YTS
- EZTV
- LimeTorrents
- TorrentGalaxy
- And many more...

## Installation

### Chrome/Chromium Browsers
1. Download the extension files
2. Open Chrome and go to `chrome://extensions/`
3. Enable "Developer mode" in the top right
4. Click "Load unpacked" and select the extension folder
5. The MediaFusion extension icon should appear in your toolbar

### Firefox
1. Download the extension files
2. Open Firefox and go to `about:debugging`
3. Click "This Firefox"
4. Click "Load Temporary Add-on"
5. Select the `manifest.json` file from the extension folder

## Setup

1. Click the MediaFusion extension icon in your browser toolbar
2. Configure your settings:
   - **MediaFusion URL**: Enter your MediaFusion instance URL (e.g., `https://mediafusion.elfhosted.com`)
   - **Uploader Name**: Enter your preferred uploader name (optional, defaults to "Anonymous")
   - **API Password**: If your instance requires authentication

## Usage

### Automatic Detection
1. Visit any supported torrent site
2. Browse to a torrent page
3. Look for the MediaFusion upload button next to torrent/magnet links
4. Click the button to upload the torrent to MediaFusion

### Manual Upload
1. Click the MediaFusion extension icon
2. Use the popup interface to manually upload torrents:
   - Paste magnet links
   - Upload torrent files
   - Configure metadata

## Development

### Project Structure
```
browser-extension/
├── manifest.json          # Chrome extension manifest
├── manifest_firefox.json  # Firefox extension manifest
├── popup/
│   ├── popup.html         # Extension popup interface
│   ├── popup.js           # Popup functionality
│   └── popup.css          # Popup styling
├── content/
│   ├── content.js         # Main content script
│   └── site-handlers/     # Site-specific handlers
│       ├── 1337x.js
│       ├── piratebay.js
│       └── ...
├── background/
│   └── background.js      # Background script for API calls
├── icons/                 # Extension icons
└── styles/
    └── inject.css         # Injected styles
```

### Building
The extension is ready to use without building. Simply load the unpacked extension in your browser.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add support for new torrent sites by creating handlers in `content/site-handlers/`
4. Test thoroughly on different sites
5. Submit a pull request

## License

This project is licensed under the same license as MediaFusion.


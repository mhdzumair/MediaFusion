# MediaFusion Browser Extension - Installation Guide

This guide will help you install and set up the MediaFusion Browser Extension to easily contribute torrents from any torrent site.

## Prerequisites

- A MediaFusion instance (self-hosted or public)
- Chrome, Firefox, or any Chromium-based browser
- Basic knowledge of browser extension installation

## Installation Steps

### For Chrome/Chromium Browsers

1. **Download the Extension**
   - Download or clone the MediaFusion repository
   - Navigate to the `browser-extension` folder

2. **Prepare Extension Icons** (Optional)
   - Add icon files to the `icons/` directory:
     - `icon16.png` (16x16 pixels)
     - `icon32.png` (32x32 pixels) 
     - `icon48.png` (48x48 pixels)
     - `icon128.png` (128x128 pixels)
   - If you don't have icons, the extension will work with default placeholders

3. **Load the Extension**
   - Open Chrome and navigate to `chrome://extensions/`
   - Enable "Developer mode" by toggling the switch in the top right
   - Click "Load unpacked" button
   - Select the `browser-extension` folder
   - The MediaFusion extension should now appear in your extensions list

4. **Pin the Extension** (Recommended)
   - Click the puzzle piece icon in the Chrome toolbar
   - Find "MediaFusion Torrent Uploader" and click the pin icon
   - The extension icon will now be visible in your toolbar

### For Firefox

1. **Download the Extension**
   - Download or clone the MediaFusion repository
   - Navigate to the `browser-extension` folder

2. **Prepare for Firefox**
   - Rename `manifest_firefox.json` to `manifest.json`
   - Backup the original `manifest.json` if needed

3. **Load the Extension**
   - Open Firefox and navigate to `about:debugging`
   - Click "This Firefox" in the left sidebar
   - Click "Load Temporary Add-on"
   - Navigate to the `browser-extension` folder
   - Select the `manifest.json` file
   - The extension will be loaded temporarily

4. **Note for Firefox Users**
   - Temporary add-ons are removed when Firefox restarts
   - For permanent installation, you'll need to package and sign the extension
   - See Firefox's developer documentation for more details

### For Edge

1. **Enable Developer Mode**
   - Open Edge and navigate to `edge://extensions/`
   - Enable "Developer mode" in the left sidebar

2. **Load the Extension**
   - Click "Load unpacked"
   - Select the `browser-extension` folder
   - The extension will be loaded

## Initial Configuration

### Step 1: Open Extension Settings

1. Click the MediaFusion extension icon in your browser toolbar
2. Click the "Settings" tab in the popup

### Step 2: Configure MediaFusion URL

1. Enter your MediaFusion instance URL in the "MediaFusion URL" field
   - Examples:
     - `https://mediafusion.elfhosted.com`
     - `https://your-domain.com`
     - `http://localhost:8000` (for local development)

2. If your MediaFusion instance requires authentication:
   - Enter the API password in the "API Password" field
   - Leave blank if no authentication is required

### Step 3: Set Default Uploader Name

1. Enter your preferred uploader name (optional)
2. This will be used as the default for all uploads
3. You can change it for individual uploads if needed

### Step 4: Test Connection

1. Click "Test Connection" to verify your settings
2. You should see a "Connected successfully" message
3. If the test fails, check your URL and network connection

### Step 5: Save Settings

1. Click "Save Settings" to store your configuration
2. Settings are synced across your browser instances

## Using the Extension

### Automatic Detection

1. **Visit any supported torrent site** (1337x, PirateBay, etc.)
2. **Browse to a torrent page** or search results
3. **Look for MediaFusion upload buttons** next to torrent/magnet links
4. **Click the button** to upload the torrent to MediaFusion
5. **Watch the button status** change to show upload progress

### Manual Upload

1. **Click the extension icon** in your toolbar
2. **Go to the "Upload" tab**
3. **Paste a magnet link** or **upload a torrent file**
4. **Select content type** (Movie, TV Series, Sports)
5. **Click "Quick Upload"** for automatic processing
6. **Or click "Analyze"** first to see potential matches

### Advanced Upload

1. **Use "Analyze"** to see metadata and potential matches
2. **Select a match** if available for better metadata
3. **Use "Manual Upload"** if no matches are found
4. **Review the upload status** in the popup

## Supported Torrent Sites

The extension automatically detects torrents on these sites:

- **1337x** (1337x.to, 1337x.st)
- **The Pirate Bay** (thepiratebay.org, tpb.party)
- **RARBG** (rarbg.to)
- **YTS** (yts.mx, yts.am)
- **EZTV** (eztv.re, eztv.io)
- **LimeTorrents** (limetorrents.info)
- **TorrentGalaxy** (torrentgalaxy.to)
- **Zooqle** (zooqle.com)
- **Torlock** (torlock.com)
- **KickassTorrents** (kickasstorrents.to)
- **Nyaa** (nyaa.si, sukebei.nyaa.si)
- **RuTracker** (rutracker.org)

The extension also works on other torrent sites with generic detection.

## Troubleshooting

### Extension Not Loading

- **Check browser compatibility**: Ensure you're using a supported browser
- **Verify file permissions**: Make sure the extension folder is readable
- **Check console errors**: Open browser developer tools for error messages
- **Try incognito mode**: Test if the extension works in private browsing

### Connection Issues

- **Verify MediaFusion URL**: Ensure the URL is correct and accessible
- **Check network connectivity**: Test the URL in a regular browser tab
- **Verify HTTPS/HTTP**: Match the protocol with your MediaFusion instance
- **Check CORS settings**: MediaFusion may need CORS configuration for browser requests

### Upload Failures

- **Check API password**: Verify if your instance requires authentication
- **Test with different content**: Try uploading different types of torrents
- **Check file size limits**: Large torrent files may have upload limits
- **Verify torrent validity**: Ensure the torrent/magnet link is working

### Buttons Not Appearing

- **Refresh the page**: Try reloading the torrent site
- **Check site compatibility**: Some sites may have changed their layout
- **Disable other extensions**: Check for conflicts with other browser extensions
- **Clear browser cache**: Clear cache and cookies for the torrent site

### Performance Issues

- **Disable on unused sites**: Limit the extension to specific sites if needed
- **Check memory usage**: Monitor browser memory usage
- **Update browser**: Ensure you're using the latest browser version

## Advanced Configuration

### Custom Site Support

To add support for new torrent sites:

1. Edit `content/content.js`
2. Add a new handler function for the site
3. Register the handler in the `loadSiteHandlers()` method
4. Test on the target site

### API Customization

To modify API communication:

1. Edit `background/background.js`
2. Modify the `MediaFusionAPI` class methods
3. Update error handling and response processing

### UI Customization

To customize the popup interface:

1. Edit `popup/popup.html` for structure
2. Edit `popup/popup.css` for styling
3. Edit `popup/popup.js` for functionality

## Security Considerations

### Permissions

The extension requests these permissions:
- **Storage**: To save your settings
- **ActiveTab**: To detect torrents on the current page
- **Host permissions**: To communicate with your MediaFusion instance

### Data Privacy

- **Settings are stored locally** in your browser
- **No data is sent to third parties**
- **Only communicates with your configured MediaFusion instance**
- **Torrent data is sent directly to MediaFusion**

### Best Practices

- **Use HTTPS** for your MediaFusion instance when possible
- **Keep API passwords secure**
- **Regularly update the extension**
- **Only upload content you have rights to distribute**

## Getting Help

### Documentation

- **MediaFusion Documentation**: Check the main MediaFusion repository
- **Browser Extension APIs**: Refer to Chrome/Firefox extension documentation
- **Issue Tracker**: Report bugs on the MediaFusion GitHub repository

### Community Support

- **GitHub Issues**: Report bugs and request features
- **Discord/Forums**: Join MediaFusion community discussions
- **Stack Overflow**: Search for browser extension development help

### Contributing

- **Report Issues**: Help improve the extension by reporting bugs
- **Submit Pull Requests**: Contribute code improvements
- **Add Site Support**: Help add support for new torrent sites
- **Improve Documentation**: Help improve this guide

## Changelog

### Version 1.0.0
- Initial release
- Support for major torrent sites
- Automatic torrent detection
- Manual upload functionality
- Settings management
- Chrome and Firefox compatibility


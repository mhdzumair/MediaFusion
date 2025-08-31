// MediaFusion Browser Extension - Content Script
// Detects and adds upload buttons to torrent/magnet links

class MediaFusionContentScript {
    constructor() {
        this.processedLinks = new Set();
        this.siteHandlers = new Map();
        this.universalDetection = true; // Always enabled for maximum compatibility
        this.isProcessing = false;
        this.init();
    }

    async init() {
        this.loadSiteHandlers();
        this.startObserving();
        this.processPage();
    }

    loadSiteHandlers() {
        const hostname = window.location.hostname.replace('www.', '');
        
        // Site-specific handlers
        this.siteHandlers.set('1337x.to', this.handle1337x.bind(this));
        this.siteHandlers.set('1337x.st', this.handle1337x.bind(this));
        this.siteHandlers.set('thepiratebay.org', this.handlePirateBay.bind(this));
        this.siteHandlers.set('piratebay.org', this.handlePirateBay.bind(this));
        this.siteHandlers.set('tpb.party', this.handlePirateBay.bind(this));
        this.siteHandlers.set('thepiratebay10.org', this.handlePirateBay.bind(this));
        this.siteHandlers.set('rarbg.to', this.handleRARBG.bind(this));
        this.siteHandlers.set('yts.mx', this.handleYTS.bind(this));
        this.siteHandlers.set('yts.am', this.handleYTS.bind(this));
        this.siteHandlers.set('eztv.re', this.handleEZTV.bind(this));
        this.siteHandlers.set('eztv.io', this.handleEZTV.bind(this));
        this.siteHandlers.set('limetorrents.info', this.handleLimeTorrents.bind(this));
        this.siteHandlers.set('limetorrents.lol', this.handleLimeTorrents.bind(this));
        this.siteHandlers.set('torrentgalaxy.to', this.handleTorrentGalaxy.bind(this));
        this.siteHandlers.set('zooqle.com', this.handleZooqle.bind(this));
        this.siteHandlers.set('torlock.com', this.handleTorlock.bind(this));
        this.siteHandlers.set('kickasstorrents.to', this.handleKickass.bind(this));
        this.siteHandlers.set('nyaa.si', this.handleNyaa.bind(this));
        this.siteHandlers.set('sukebei.nyaa.si', this.handleNyaa.bind(this));
        this.siteHandlers.set('rutracker.org', this.handleRutracker.bind(this));
        this.siteHandlers.set('uindex.org', this.handleUIndex.bind(this));
    }

    startObserving() {
        // Observe DOM changes to catch dynamically loaded content
        const observer = new MutationObserver((mutations) => {
            let shouldProcess = false;
            mutations.forEach((mutation) => {
                if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
                    shouldProcess = true;
                }
            });
            if (shouldProcess) {
                setTimeout(() => this.processPage(), 500);
            }
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    processPage() {
        if (this.isProcessing) return;
        this.isProcessing = true;
        
        try {
            const hostname = window.location.hostname.replace('www.', '');
            const handler = this.siteHandlers.get(hostname);
            
            if (handler) {
                // Use site-specific handler for better accuracy
                handler();
            } else {
                // Universal detection for all other sites
                this.handleUniversal();
            }
        } finally {
            setTimeout(() => {
                this.isProcessing = false;
            }, 1000);
        }
    }

    handleUniversal() {
        // Universal magnet link detection
        const magnetLinks = document.querySelectorAll('a[href^="magnet:"]');
        magnetLinks.forEach(link => {
            if (!this.hasUploadButton(link)) {
                this.addUploadButton(link, 'magnet');
            }
        });

        // Universal torrent file detection
        const torrentLinks = document.querySelectorAll('a[href$=".torrent"], a[href*=".torrent?"], a[href*="/download/"], a[href*="/torrent/"]');
        torrentLinks.forEach(link => {
            // Skip if it's likely not a torrent download link
            if (link.href.includes('javascript:') || link.href.includes('#')) return;
            
            if (!this.hasUploadButton(link)) {
                this.addUploadButton(link, 'torrent');
            }
        });
    }

    // Generic handler that looks for magnet links and .torrent files
    handleGeneric() {
        // Find magnet links
        const magnetLinks = document.querySelectorAll('a[href^="magnet:"]');
        magnetLinks.forEach(link => this.addUploadButton(link, 'magnet'));

        // Find torrent file links
        const torrentLinks = document.querySelectorAll('a[href$=".torrent"], a[href*=".torrent?"]');
        torrentLinks.forEach(link => this.addUploadButton(link, 'torrent'));
    }

    // Site-specific handlers
    handle1337x() {
        // Torrent detail page
        if (window.location.pathname.includes('/torrent/')) {
            const magnetLink = document.querySelector('a[href^="magnet:"]');
            const torrentLink = document.querySelector('a[href$=".torrent"]');
            
            if (magnetLink) {
                this.addUploadButton(magnetLink, 'magnet');
            }
            if (torrentLink) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        }
        
        // Search results and category pages
        const tableRows = document.querySelectorAll('.table-list tbody tr');
        tableRows.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            const torrentLink = row.querySelector('a[href$=".torrent"]');
            
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
            if (torrentLink && !this.hasUploadButton(torrentLink)) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        });
    }

    handlePirateBay() {
        // Search results
        const searchResults = document.querySelectorAll('#searchResult tbody tr');
        searchResults.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
        });

        // Torrent detail page
        const detailMagnet = document.querySelector('.download a[href^="magnet:"]');
        if (detailMagnet && !this.hasUploadButton(detailMagnet)) {
            this.addUploadButton(detailMagnet, 'magnet');
        }
    }

    handleRARBG() {
        // Search results
        const tableRows = document.querySelectorAll('.lista2 tr');
        tableRows.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            const torrentLink = row.querySelector('a[href$=".torrent"]');
            
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
            if (torrentLink && !this.hasUploadButton(torrentLink)) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        });
    }

    handleYTS() {
        // Movie detail page
        const downloadButtons = document.querySelectorAll('.movie-actions a[href$=".torrent"]');
        downloadButtons.forEach(button => {
            if (!this.hasUploadButton(button)) {
                this.addUploadButton(button, 'torrent');
            }
        });

        // Browse page
        const movieItems = document.querySelectorAll('.browse-movie-wrap');
        movieItems.forEach(item => {
            const torrentLinks = item.querySelectorAll('a[href$=".torrent"]');
            torrentLinks.forEach(link => {
                if (!this.hasUploadButton(link)) {
                    this.addUploadButton(link, 'torrent');
                }
            });
        });
    }

    handleEZTV() {
        // Show detail page and episode listings
        const episodeRows = document.querySelectorAll('.forum_header_border tr');
        episodeRows.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            const torrentLink = row.querySelector('a[href$=".torrent"]');
            
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
            if (torrentLink && !this.hasUploadButton(torrentLink)) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        });
    }

    handleLimeTorrents() {
        // Search results and category pages
        const tableRows = document.querySelectorAll('.table2 tr');
        tableRows.forEach(row => {
            const torrentLink = row.querySelector('a[href*="download.php"]');
            if (torrentLink && !this.hasUploadButton(torrentLink)) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        });
    }

    handleTorrentGalaxy() {
        // Search results
        const torrentRows = document.querySelectorAll('.tgxtablerow');
        torrentRows.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            const torrentLink = row.querySelector('a[href$=".torrent"]');
            
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
            if (torrentLink && !this.hasUploadButton(torrentLink)) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        });
    }

    handleZooqle() {
        // Search results
        const tableRows = document.querySelectorAll('.table-torrents tbody tr');
        tableRows.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
        });
    }

    handleTorlock() {
        // Search results
        const tableRows = document.querySelectorAll('.table tbody tr');
        tableRows.forEach(row => {
            const torrentLink = row.querySelector('a[href*="/tor/"]');
            if (torrentLink && !this.hasUploadButton(torrentLink)) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        });
    }

    handleKickass() {
        // Search results
        const tableRows = document.querySelectorAll('.data tr');
        tableRows.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            const torrentLink = row.querySelector('a[href$=".torrent"]');
            
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
            if (torrentLink && !this.hasUploadButton(torrentLink)) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        });
    }

    handleNyaa() {
        // Search results
        const tableRows = document.querySelectorAll('.torrent-list tbody tr');
        tableRows.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            const torrentLink = row.querySelector('a[href$=".torrent"]');
            
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
            if (torrentLink && !this.hasUploadButton(torrentLink)) {
                this.addUploadButton(torrentLink, 'torrent');
            }
        });
    }

    handleRutracker() {
        // Topic pages
        const downloadLinks = document.querySelectorAll('a[href*="dl.php"]');
        downloadLinks.forEach(link => {
            if (!this.hasUploadButton(link)) {
                this.addUploadButton(link, 'torrent');
            }
        });
    }

    handleUIndex() {
        // UIndex.org - magnet links in table rows
        const tableRows = document.querySelectorAll('table tr');
        tableRows.forEach(row => {
            const magnetLink = row.querySelector('a[href^="magnet:"]');
            if (magnetLink && !this.hasUploadButton(magnetLink)) {
                this.addUploadButton(magnetLink, 'magnet');
            }
        });

        // Also check for any standalone magnet links
        const allMagnetLinks = document.querySelectorAll('a[href^="magnet:"]');
        allMagnetLinks.forEach(link => {
            if (!this.hasUploadButton(link)) {
                this.addUploadButton(link, 'magnet');
            }
        });
    }

    hasUploadButton(element) {
        const linkId = this.getLinkId(element);
        return this.processedLinks.has(linkId);
    }

    getLinkId(element) {
        return element.href || element.getAttribute('href') || element.outerHTML;
    }

    addUploadButton(linkElement, type) {
        const linkId = this.getLinkId(linkElement);
        if (this.processedLinks.has(linkId)) {
            return;
        }

        this.processedLinks.add(linkId);

        const button = this.createUploadButton(linkElement, type);
        
        // Try to insert the button next to the link
        if (linkElement.parentNode) {
            linkElement.parentNode.insertBefore(button, linkElement.nextSibling);
        }
    }

    createUploadButton(linkElement, type) {
        const button = document.createElement('button');
        button.className = 'mediafusion-upload-btn';
        button.innerHTML = `
            <svg class="icon" viewBox="0 0 24 24" fill="currentColor">
                <path d="M9,16V10H5L12,3L19,10H15V16H9M5,20V18H19V20H5Z" />
            </svg>
            <span class="text">MediaFusion</span>
            <div class="mediafusion-tooltip">Upload to MediaFusion</div>
        `;

        // Prevent multiple event listeners
        button.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            
            // Prevent double clicks
            if (button.disabled) return;
            
            this.handleUpload(linkElement, type, button);
        }, { once: false });

        return button;
    }

    async handleUpload(linkElement, type, button) {
        // Prevent double clicks
        if (button.disabled) return;
        button.disabled = true;
        
        try {
            // Show loading state
            button.classList.add('loading');
            button.innerHTML = `
                <div class="mediafusion-spinner"></div>
                <span class="text">Opening...</span>
            `;

            // Get the magnet link or torrent URL
            const magnetLink = type === 'magnet' ? linkElement.href : null;
            const torrentUrl = type === 'torrent' ? linkElement.href : null;
            const contentType = this.guessContentType(linkElement);
            
            // Send message to background script to open popup with data
            const response = await this.sendMessage({
                action: 'openPopupWithData',
                data: {
                    magnetLink: magnetLink,
                    torrentUrl: torrentUrl,
                    contentType: contentType,
                    sourceUrl: window.location.href,
                    title: document.title
                }
            });
            
            if (response && response.success) {
                // Show success state
                button.classList.remove('loading');
                button.classList.add('success');
                button.innerHTML = `
                    <svg class="icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M21,7L9,19L3.5,13.5L4.91,12.09L9,16.17L19.59,5.59L21,7Z" />
                    </svg>
                    <span class="text">Opened</span>
                `;
            } else {
                throw new Error('Failed to open popup');
            }
            
            // Reset after 2 seconds
            setTimeout(() => {
                this.resetButton(button);
            }, 2000);
            
        } catch (error) {
            
            // Show error state
            button.classList.remove('loading');
            button.classList.add('error');
            button.innerHTML = `
                <svg class="icon" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M13,13H11V7H13M13,17H11V15H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z" />
                </svg>
                <span class="text">Error</span>
            `;
            
            // Reset after 3 seconds
            setTimeout(() => {
                this.resetButton(button);
            }, 3000);
        }
    }

    resetButton(button) {
        button.classList.remove('loading', 'success', 'error');
        button.innerHTML = `
            <svg class="icon" viewBox="0 0 24 24" fill="currentColor">
                <path d="M9,16V10H5L12,3L19,10H15V16H9M5,20V18H19V20H5Z" />
            </svg>
            <span class="text">MediaFusion</span>
            <div class="mediafusion-tooltip">Upload to MediaFusion</div>
        `;
        button.disabled = false;
    }



    async downloadTorrent(url) {
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error('Failed to download torrent file');
        }
        return await response.blob();
    }

    guessContentType(linkElement) {
        const text = linkElement.textContent.toLowerCase() + ' ' + 
                    (linkElement.title || '').toLowerCase() + ' ' +
                    window.location.pathname.toLowerCase();

        if (text.includes('movie') || text.includes('film') || text.includes('cinema')) {
            return 'movie';
        } else if (text.includes('tv') || text.includes('series') || text.includes('episode') || 
                   text.includes('season') || text.includes('show')) {
            return 'series';
        } else if (text.includes('sport') || text.includes('match') || text.includes('game') ||
                   text.includes('racing') || text.includes('football') || text.includes('basketball')) {
            return 'sports';
        }

        // Default to movie
        return 'movie';
    }

    sendMessage(message) {
        return new Promise((resolve, reject) => {
            const callback = (response) => {
                if (response) {
                    resolve(response);
                } else {
                    reject(new Error('No response received'));
                }
            };

            if (typeof browser !== 'undefined' && browser.runtime) {
                // Firefox
                browser.runtime.sendMessage(message).then(callback).catch(reject);
            } else if (typeof chrome !== 'undefined' && chrome.runtime) {
                // Chrome
                chrome.runtime.sendMessage(message, callback);
            } else {
                reject(new Error('Extension runtime not available'));
            }
        });
    }
}

// Prevent multiple instances
if (!window.mediaFusionContentScriptLoaded) {
    window.mediaFusionContentScriptLoaded = true;
    
    // Initialize content script when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            new MediaFusionContentScript();
        });
    } else {
        new MediaFusionContentScript();
    }
}

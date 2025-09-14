// MediaFusion Browser Extension - Content Script
// Detects and adds upload buttons to torrent/magnet links

class MediaFusionContentScript {
  constructor() {
    this.processedLinks = new Set();
    this.siteHandlers = new Map();
    this.isProcessing = false;
    this.bulkUploadButton = null;
    this.detectedTorrents = new Map(); // Store all detected torrents
    this.init();
  }

  async init() {
    this.loadSiteHandlers();
    this.startObserving();
    this.processPage();
  }

  loadSiteHandlers() {
    // Site-specific handlers
    this.siteHandlers.set("1337x.to", this.handle1337x.bind(this));
    this.siteHandlers.set("1337x.st", this.handle1337x.bind(this));
    this.siteHandlers.set("thepiratebay.org", this.handlePirateBay.bind(this));
    this.siteHandlers.set("tpb.party", this.handlePirateBay.bind(this));
    this.siteHandlers.set(
      "thepiratebay10.xyz",
      this.handlePirateBay.bind(this)
    );
    this.siteHandlers.set("yts.mx", this.handleYTS.bind(this));
    this.siteHandlers.set("yts.am", this.handleYTS.bind(this));
    this.siteHandlers.set("eztvx.to", this.handleEZTV.bind(this));
    this.siteHandlers.set(
      "limetorrents.fun",
      this.handleLimeTorrents.bind(this)
    );
    this.siteHandlers.set(
      "kickass.torrentbay.st",
      this.handleKickass.bind(this)
    );
    this.siteHandlers.set("nyaa.si", this.handleNyaa.bind(this));
    this.siteHandlers.set("sukebei.nyaa.si", this.handleNyaa.bind(this));
    this.siteHandlers.set("rutracker.org", this.handleRutracker.bind(this));
    this.siteHandlers.set("uindex.org", this.handleUIndex.bind(this));
  }

  startObserving() {
    // Observe DOM changes to catch dynamically loaded content
    const observer = new MutationObserver((mutations) => {
      let shouldProcess = false;
      mutations.forEach((mutation) => {
        if (mutation.type === "childList" && mutation.addedNodes.length > 0) {
          shouldProcess = true;
        }
      });
      if (shouldProcess) {
        setTimeout(() => this.processPage(), 500);
      }
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  processPage() {
    if (this.isProcessing) return;
    this.isProcessing = true;

    try {
      const hostname = window.location.hostname.replace("www.", "");
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
        // Check for bulk upload opportunities after processing
        this.checkBulkUploadOpportunity();
      }, 1000);
    }
  }

  handleUniversal() {
    // Universal magnet link detection
    const magnetLinks = document.querySelectorAll('a[href^="magnet:"]');
    magnetLinks.forEach((link) => {
      if (!this.hasUploadButton(link)) {
        this.addUploadButton(link, "magnet");
      }
    });

    // Universal torrent file detection - definitive patterns
    const torrentLinks = document.querySelectorAll(
      'a[href$=".torrent"], a[href*=".torrent?"], a[data-fileext="torrent"]'
    );
    torrentLinks.forEach((link) => {
      // Skip obvious non-download links
      if (link.href.includes("javascript:") || link.href.includes("#")) return;

      if (!this.hasUploadButton(link)) {
        this.addUploadButton(link, "torrent");
      }
    });
  }

  handleUniversalFallback() {
    // Comprehensive fallback detection for magnet links
    const magnetSelectors = [
      'a[href^="magnet:"]',
      '.item-icons a[href^="magnet:"]',
      '.download-links a[href^="magnet:"]',
      ".magnet-link",
      "[data-magnet]",
      'span a[href^="magnet:"]',
      'div a[href^="magnet:"]',
      'td a[href^="magnet:"]',
    ];

    magnetSelectors.forEach((selector) => {
      const links = document.querySelectorAll(selector);
      links.forEach((link) => {
        if (!this.hasUploadButton(link)) {
          this.addUploadButton(link, "magnet");
        }
      });
    });

    // Comprehensive fallback detection for torrent files - definitive patterns
    const torrentSelectors = [
      'a[href$=".torrent"]',
      'a[href*=".torrent?"]',
      'a[data-fileext="torrent"]',
    ];

    torrentSelectors.forEach((selector) => {
      const links = document.querySelectorAll(selector);
      links.forEach((link) => {
        // Skip obvious non-download links
        if (link.href.includes("javascript:") || link.href.includes("#"))
          return;

        if (!this.hasUploadButton(link)) {
          this.addUploadButton(link, "torrent");
        }
      });
    });
  }

  // Generic handler that looks for magnet links and .torrent files
  handleGeneric() {
    // Find magnet links
    const magnetLinks = document.querySelectorAll('a[href^="magnet:"]');
    magnetLinks.forEach((link) => this.addUploadButton(link, "magnet"));

    // Find torrent file links
    const torrentLinks = document.querySelectorAll(
      'a[href$=".torrent"], a[href*=".torrent?"]'
    );
    torrentLinks.forEach((link) => this.addUploadButton(link, "torrent"));
  }

  // Site-specific handlers
  handle1337x() {
    // Torrent detail page
    if (window.location.pathname.includes("/torrent/")) {
      const magnetLink = document.querySelector('a[href^="magnet:"]');
      const torrentLink = document.querySelector('a[href$=".torrent"]');

      if (magnetLink) {
        this.addUploadButton(magnetLink, "magnet");
      }
      if (torrentLink) {
        this.addUploadButton(torrentLink, "torrent");
      }
    }

    // Search results and category pages
    const tableRows = document.querySelectorAll(".table-list tbody tr");
    tableRows.forEach((row) => {
      const magnetLink = row.querySelector('a[href^="magnet:"]');
      const torrentLink = row.querySelector('a[href$=".torrent"]');

      if (magnetLink && !this.hasUploadButton(magnetLink)) {
        this.addUploadButton(magnetLink, "magnet");
      }
      if (torrentLink && !this.hasUploadButton(torrentLink)) {
        this.addUploadButton(torrentLink, "torrent");
      }
    });

    // Fallback detection
    this.handleUniversalFallback();
  }

  handlePirateBay() {
    // Search results - multiple selectors to catch different layouts
    const searchResults = document.querySelectorAll(
      "#searchResult tbody tr, .list-entry, tr"
    );
    searchResults.forEach((row) => {
      const magnetLink = row.querySelector('a[href^="magnet:"]');
      if (magnetLink && !this.hasUploadButton(magnetLink)) {
        this.addUploadButton(magnetLink, "magnet");
      }
    });

    // Torrent detail page
    const detailMagnet = document.querySelector('.download a[href^="magnet:"]');
    if (detailMagnet && !this.hasUploadButton(detailMagnet)) {
      this.addUploadButton(detailMagnet, "magnet");
    }

    // Fallback: Look for magnet links in item-icons spans and other containers
    const itemIconsLinks = document.querySelectorAll(
      '.item-icons a[href^="magnet:"], span a[href^="magnet:"]'
    );
    itemIconsLinks.forEach((link) => {
      if (!this.hasUploadButton(link)) {
        this.addUploadButton(link, "magnet");
      }
    });

    // Additional fallback for any magnet links on the page
    this.handleUniversalFallback();
  }

  handleYTS() {
    // All YTS download links - both page and modal
    const allDownloadLinks = document.querySelectorAll(
      'a[href$=".torrent"], a[href*="/torrent/download/"]'
    );
    allDownloadLinks.forEach((link) => {
      if (!this.hasUploadButton(link)) {
        this.addUploadButton(link, "torrent");
      }
    });
    // Fallback detection
    this.handleUniversalFallback();
  }

  handleEZTV() {
    // Show detail page and episode listings
    const episodeRows = document.querySelectorAll(".forum_header_border tr");
    episodeRows.forEach((row) => {
      const magnetLink = row.querySelector('a[href^="magnet:"]');
      const torrentLink = row.querySelector('a[href$=".torrent"]');

      if (magnetLink && !this.hasUploadButton(magnetLink)) {
        this.addUploadButton(magnetLink, "magnet");
      }
      if (torrentLink && !this.hasUploadButton(torrentLink)) {
        this.addUploadButton(torrentLink, "torrent");
      }
    });

    // Fallback detection
    this.handleUniversalFallback();
  }

  handleLimeTorrents() {
    // Search results and category pages
    const tableRows = document.querySelectorAll(".table2 tr");
    tableRows.forEach((row) => {
      const torrentLink = row.querySelector('a[href*="download.php"]');
      if (torrentLink && !this.hasUploadButton(torrentLink)) {
        this.addUploadButton(torrentLink, "torrent");
      }
    });

    // Fallback detection
    this.handleUniversalFallback();
  }

  handleKickass() {
    // Search results - main table
    const tableRows = document.querySelectorAll(".data tr");
    tableRows.forEach((row) => {
      const magnetLink = row.querySelector('a[href^="magnet:"]');
      const torrentLink = row.querySelector('a[href$=".torrent"]');

      if (magnetLink && !this.hasUploadButton(magnetLink)) {
        this.addUploadButton(magnetLink, "magnet");
      }
      if (torrentLink && !this.hasUploadButton(torrentLink)) {
        this.addUploadButton(torrentLink, "torrent");
      }
    });

    // Fallback detection
    this.handleUniversalFallback();
  }

  handleNyaa() {
    // Search results
    const tableRows = document.querySelectorAll(".torrent-list tbody tr");
    tableRows.forEach((row) => {
      const magnetLink = row.querySelector('a[href^="magnet:"]');
      const torrentLink = row.querySelector('a[href$=".torrent"]');

      if (magnetLink && !this.hasUploadButton(magnetLink)) {
        this.addUploadButton(magnetLink, "magnet");
      }
      if (torrentLink && !this.hasUploadButton(torrentLink)) {
        this.addUploadButton(torrentLink, "torrent");
      }
    });

    // Fallback detection
    this.handleUniversalFallback();
  }

  handleRutracker() {
    // Topic pages
    const downloadLinks = document.querySelectorAll('a[href*="dl.php"]');
    downloadLinks.forEach((link) => {
      if (!this.hasUploadButton(link)) {
        this.addUploadButton(link, "torrent");
      }
    });

    // Fallback detection
    this.handleUniversalFallback();
  }

  handleUIndex() {
    // UIndex.org - magnet links in table rows
    const tableRows = document.querySelectorAll("table tr");
    tableRows.forEach((row) => {
      const magnetLink = row.querySelector('a[href^="magnet:"]');
      if (magnetLink && !this.hasUploadButton(magnetLink)) {
        this.addUploadButton(magnetLink, "magnet");
      }
    });

    // Also check for any standalone magnet links
    const allMagnetLinks = document.querySelectorAll('a[href^="magnet:"]');
    allMagnetLinks.forEach((link) => {
      if (!this.hasUploadButton(link)) {
        this.addUploadButton(link, "magnet");
      }
    });

    // Fallback detection
    this.handleUniversalFallback();
  }

  hasUploadButton(element) {
    const linkId = this.getLinkId(element);
    return this.processedLinks.has(linkId);
  }

  getLinkId(element) {
    const href =
      element.href || element.getAttribute("href") || element.outerHTML;

    // For YTS, allow same link in different containers by including parent info
    if (window.location.hostname.includes("yts.")) {
      const isInModal = element.closest(".modal, .popup, .download-popup");
      return isInModal ? `${href}::modal` : href;
    }

    // For other sites, use the original logic
    return href;
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
      // Check if we're in a constrained container (like PirateBay's item-icons)
      const constrainedContainer = linkElement.closest(
        ".item-icons, .constrained-width"
      );

      if (constrainedContainer) {
        // For constrained containers, try to insert after the container
        const insertionPoint = constrainedContainer.parentNode;
        if (insertionPoint) {
          insertionPoint.insertBefore(button, constrainedContainer.nextSibling);
        } else {
          // Fallback: insert after the link
          linkElement.parentNode.insertBefore(button, linkElement.nextSibling);
        }
      } else {
        // Normal insertion next to the link
        linkElement.parentNode.insertBefore(button, linkElement.nextSibling);
      }
    }
  }

  createUploadButton(linkElement, type) {
    const button = document.createElement("button");

    // Determine if this should be a compact button
    const isCompact = this.shouldUseCompactButton(linkElement);

    // Set base classes
    let className = "mediafusion-upload-btn";
    if (type === "magnet") {
      className += " magnet-type";
    } else if (type === "torrent") {
      className += " torrent-type";
    }
    if (isCompact) {
      className += " compact";
    }

    button.className = className;

    // Different icons and text based on type
    const iconSvg = this.getButtonIcon(type);
    const buttonText = this.getButtonText(type, isCompact);
    const tooltipText = this.getTooltipText(type);

    button.innerHTML = `
            ${iconSvg}
            ${buttonText}
            <div class="mediafusion-tooltip">${tooltipText}</div>
        `;

    // Prevent multiple event listeners
    button.addEventListener(
      "click",
      (e) => {
        e.preventDefault();
        e.stopPropagation();

        // Prevent double clicks
        if (button.disabled) return;

        this.handleUpload(linkElement, type, button);
      },
      { once: false }
    );

    return button;
  }

  shouldUseCompactButton(linkElement) {
    // Use compact buttons in constrained spaces
    const constrainedContainers = [
      ".item-icons", // PirateBay
      ".constrained-width", // Generic constrained
      ".torrent-actions", // Small action areas
      ".download-icons", // Icon-only areas
      "td", // Table cells (often cramped)
    ];

    return constrainedContainers.some((selector) =>
      linkElement.closest(selector)
    );
  }

  getButtonIcon(type) {
    if (type === "magnet") {
      // Clean horseshoe magnet icon
      return `<svg class="icon" viewBox="0 0 24 24" fill="currentColor">
                <path d="M4,2C2.89,2 2,2.89 2,4V11C2,16.5 6.5,21 12,21S22,16.5 22,11V4C22,2.89 21.11,2 20,2H18C16.89,2 16,2.89 16,4V11C16,13.21 14.21,15 12,15S8,13.21 8,11V4C8,2.89 7.11,2 6,2H4M4,4H6V11C6,14.31 8.69,17 12,17S18,14.31 18,11V4H20V11C20,15.42 16.42,19 12,19S4,15.42 4,11V4Z" />
            </svg>`;
    } else if (type === "torrent") {
      // Better torrent file icon - document with download arrow
      return `<svg class="icon" viewBox="0 0 24 24" fill="currentColor">
                <path d="M14,2H6A2,2 0 0,0 4,4V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V8L14,2M18,20H6V4H13V9H18V20M12,11L16,15H13.5V19H10.5V15H8L12,11Z" />
            </svg>`;
    }

    // Default upload icon
    return `<svg class="icon" viewBox="0 0 24 24" fill="currentColor">
            <path d="M9,16V10H5L12,3L19,10H15V16H9M5,20V18H19V20H5Z" />
        </svg>`;
  }

  getButtonText(type, isCompact) {
    if (isCompact) {
      return ""; // No text in compact mode
    }

    if (type === "magnet") {
      return '<span class="text">Upload Magnet Link</span>';
    } else if (type === "torrent") {
      return '<span class="text">Upload Torrent File</span>';
    }

    return '<span class="text">MediaFusion</span>';
  }

  getTooltipText(type) {
    if (type === "magnet") {
      return "Upload Magnet Link to MediaFusion";
    } else if (type === "torrent") {
      return "Upload Torrent File to MediaFusion";
    }

    return "Upload to MediaFusion";
  }

  async handleUpload(linkElement, type, button) {
    // Prevent double clicks
    if (button.disabled) return;
    button.disabled = true;

    try {
      // Show loading state
      button.classList.add("loading");
      button.innerHTML = `
                <div class="mediafusion-loader">
                    <div class="mediafusion-dot"></div>
                    <div class="mediafusion-dot"></div>
                    <div class="mediafusion-dot"></div>
                </div>
            `;

      // Get the magnet link or torrent URL
      const magnetLink = type === "magnet" ? linkElement.href : null;
      const torrentUrl = type === "torrent" ? linkElement.href : null;
      const contentType = this.guessContentType(linkElement);

      // Send message to background script to open popup with data
      const response = await this.sendMessage({
        action: "openPopupWithData",
        data: {
          magnetLink: magnetLink,
          torrentUrl: torrentUrl,
          contentType: contentType,
          sourceUrl: window.location.href,
          title: document.title,
        },
      });

      if (response && response.success) {
        // Show success state
        button.classList.remove("loading");
        button.classList.add("success");
        button.innerHTML = `
                    <svg class="icon" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M21,7L9,19L3.5,13.5L4.91,12.09L9,16.17L19.59,5.59L21,7Z" />
                    </svg>
                    <span class="text">Opened</span>
                `;
      } else {
        throw new Error("Failed to open popup");
      }

      // Reset after 2 seconds
      setTimeout(() => {
        this.resetButton(button);
      }, 2000);
    } catch (error) {
      // Show error state
      button.classList.remove("loading");
      button.classList.add("error");
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
    button.classList.remove("loading", "success", "error");

    // Determine button type from classes
    let type = "default";
    if (button.classList.contains("magnet-type")) {
      type = "magnet";
    } else if (button.classList.contains("torrent-type")) {
      type = "torrent";
    }

    const isCompact = button.classList.contains("compact");

    // Restore original appearance
    const iconSvg = this.getButtonIcon(type);
    const buttonText = this.getButtonText(type, isCompact);
    const tooltipText = this.getTooltipText(type);

    button.innerHTML = `
            ${iconSvg}
            ${buttonText}
            <div class="mediafusion-tooltip">${tooltipText}</div>
        `;
    button.disabled = false;
  }

  async downloadTorrent(url) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("Failed to download torrent file");
    }
    return await response.blob();
  }

  guessContentType(linkElement) {
    // Get text from multiple sources for better detection
    const linkText = linkElement.textContent.toLowerCase();
    const linkTitle = (linkElement.title || "").toLowerCase();
    const linkHref = linkElement.href.toLowerCase();
    const pageTitle = document.title.toLowerCase();
    const pageUrl = window.location.pathname.toLowerCase();

    // Extract and decode magnet link title if it's a magnet link
    let magnetTitle = "";
    if (linkHref.startsWith("magnet:")) {
      try {
        // Extract the dn (display name) parameter from magnet link
        const dnMatch = linkHref.match(/[&?]dn=([^&]*)/i);
        if (dnMatch && dnMatch[1]) {
          // URL decode the title
          magnetTitle = decodeURIComponent(dnMatch[1]).toLowerCase();
          console.log(`Extracted magnet title: ${magnetTitle}`);
        }
      } catch (error) {
        console.log("Error parsing magnet link:", error);
      }
    }

    // Combine all text sources including decoded magnet title
    const allText = `${linkText} ${linkTitle} ${linkHref} ${pageTitle} ${pageUrl} ${magnetTitle}`;

    // 1. Check for SPORTS first (most specific)
    const sportsPatterns = [
      /\b(nfl|nba|nhl|mlb|mls|ufc|wwe|aew|f1|formula\s*1)\b/i,
      /\b(premier\s*league|champions\s*league|europa\s*league)\b/i,
      /\b(world\s*cup|euro\s*\d+|olympics|olympic)\b/i,
      /\b(boxing|wrestling|mma|mixed\s*martial\s*arts)\b/i,
      /\b(football|soccer|basketball|baseball|hockey|tennis|golf)\b/i,
      /\b(cricket|rugby|volleyball|badminton|swimming|athletics)\b/i,
      /\b(racing|motogp|nascar|indycar|rally)\b/i,
      /\b(vs|versus|\bv\b)\s/i, // Team vs Team
      /\bfight\s*night\b/i, // Fight Night
      /\bpay\s*per\s*view\b/i, // Pay Per View
      /\bppv\b/i, // PPV
    ];

    for (const pattern of sportsPatterns) {
      if (pattern.test(allText)) {
        return "sports";
      }
    }

    // 2. Check for SERIES (definitive patterns only)
    const seriesPatterns = [
      /\bs\d+e\d+\b/i, // S04E15, s1e1
      /\bseason\s*\d+\b/i, // Season 4
      /\bs\s*\d+\b/i, // s04, s1
      /\bepisode\s*\d+\b/i, // Episode 15
      /\b\d{1,2}x\d{1,2}\b/, // 4x15, 1x01
      /complete\s+series/i, // Complete Series
      /season\s+complete/i, // Season Complete
      /all\s+episodes/i, // All Episodes
    ];

    for (const pattern of seriesPatterns) {
      if (pattern.test(allText)) {
        return "series";
      }
    }

    // 3. Default to MOVIE (no need for complex movie detection)
    return "movie";
  }

  sendMessage(message) {
    return new Promise((resolve, reject) => {
      const callback = (response) => {
        if (response) {
          resolve(response);
        } else {
          reject(new Error("No response received"));
        }
      };

      if (typeof browser !== "undefined" && browser.runtime) {
        // Firefox
        browser.runtime.sendMessage(message).then(callback).catch(reject);
      } else if (typeof chrome !== "undefined" && chrome.runtime) {
        // Chrome
        chrome.runtime.sendMessage(message, callback);
      } else {
        reject(new Error("Extension runtime not available"));
      }
    });
  }

  // Bulk Upload Methods
  checkBulkUploadOpportunity() {
    // Detect all torrent/magnet links on the page
    this.detectAllTorrents();

    // Show bulk upload button if we have multiple torrents
    if (this.detectedTorrents.size >= 2) {
      this.showBulkUploadButton();
    } else {
      this.hideBulkUploadButton();
    }
  }

  detectAllTorrents() {
    this.detectedTorrents.clear();

    // Detect magnet links
    const magnetLinks = document.querySelectorAll('a[href^="magnet:"]');
    magnetLinks.forEach((link, index) => {
      const id = `magnet_${index}_${Date.now()}`;
      this.detectedTorrents.set(id, {
        id: id,
        type: 'magnet',
        element: link, // DOM element for internal use only (not serialized)
        url: link.href,
        title: this.extractTorrentTitle(link),
        contentType: this.guessContentType(link)
      });
    });

    // Detect torrent file links
    const torrentLinks = document.querySelectorAll(
      'a[href$=".torrent"], a[href*=".torrent?"], a[data-fileext="torrent"]'
    );
    torrentLinks.forEach((link, index) => {
      // Skip obvious non-download links
      if (link.href.includes("javascript:") || link.href.includes("#")) return;

      const id = `torrent_${index}_${Date.now()}`;
      this.detectedTorrents.set(id, {
        id: id,
        type: 'torrent',
        element: link, // DOM element for internal use only (not serialized)
        url: link.href,
        title: this.extractTorrentTitle(link),
        contentType: this.guessContentType(link)
      });
    });

    console.log(`Detected ${this.detectedTorrents.size} torrents for bulk upload`);
  }

  extractTorrentTitle(linkElement) {
    // Try multiple sources for title
    let title = '';

    // 1. For magnet links, prioritize dn parameter over link text
    if (linkElement.href.startsWith('magnet:')) {
      try {
        const dnMatch = linkElement.href.match(/[&?]dn=([^&]*)/i);
        if (dnMatch && dnMatch[1]) {
          title = decodeURIComponent(dnMatch[1]);
          // Clean up the extracted title
          title = this.cleanMagnetTitle(title);
        }
      } catch (error) {
        console.log('Error extracting magnet title:', error);
      }
    }

    // 2. Link text content (if not a magnet or no dn found)
    if (!title && linkElement.textContent && linkElement.textContent.trim()) {
      const linkText = linkElement.textContent.trim();
      // Skip generic text like "Download", "Magnet", icons, etc.
      if (!this.isGenericLinkText(linkText)) {
        title = linkText;
      }
    }

    // 3. Title attribute
    if (!title && linkElement.title) {
      title = linkElement.title;
    }

    // 4. Try to get title from nearby elements (like table row)
    if (!title) {
      const row = linkElement.closest('tr, .torrent-item, .result-item');
      if (row) {
        const titleElement = row.querySelector('.torrent-title, .title, .name, h3, h4, strong');
        if (titleElement) {
          title = titleElement.textContent.trim();
        }
      }
    }

    // 5. Fallback to page title or generic
    if (!title) {
      title = document.title || 'Unknown Torrent';
    }

    // Clean up title (remove excessive whitespace, limit length)
    title = title.replace(/\s+/g, ' ').trim();
    if (title.length > 100) {
      title = title.substring(0, 97) + '...';
    }

    return title;
  }

  isGenericLinkText(text) {
    // List of generic text that shouldn't be used as torrent titles
    const genericTexts = [
      'download', 'magnet', 'torrent', 'get', 'click here', 'link',
      'dl', 'mag', 'tor', '⬇', '🧲', '📁', '⚡', '🔗', '↓', 'download magnet',
      'magnet link', 'torrent file', 'get torrent', 'click to download'
    ];

    const lowerText = text.toLowerCase().trim();

    // Check if text is too short (likely an icon or abbreviation)
    if (lowerText.length <= 2) {
      return true;
    }

    // Check against generic text list
    return genericTexts.some(generic => lowerText.includes(generic));
  }

  cleanMagnetTitle(title) {
    if (!title) return title;

    // Remove common suffixes and prefixes that clutter the title
    const cleanPatterns = [
      // Remove tracker/site tags at the end
      /\s*\[\s*[^\]]*\.(org|com|net|info|tv|me|to)\s*\]\s*$/i,
      /\s*\[\s*UIndex\.org\s*\]\s*$/i,
      /\s*\[\s*[^\]]*tracker[^\]]*\s*\]\s*$/i,
      // Remove multiple dots and spaces
      /\.{2,}/g,
      /\s{2,}/g,
    ];

    let cleanTitle = title;

    // Apply cleaning patterns
    cleanPatterns.forEach(pattern => {
      if (typeof pattern === 'object' && pattern.test) {
        cleanTitle = cleanTitle.replace(pattern, pattern.global ? ' ' : '');
      }
    });

    // Final cleanup
    cleanTitle = cleanTitle.trim();

    return cleanTitle;
  }

  showBulkUploadButton() {
    if (this.bulkUploadButton) {
      return; // Already showing
    }

    this.bulkUploadButton = document.createElement('div');
    this.bulkUploadButton.className = 'mediafusion-bulk-upload-btn';
    this.bulkUploadButton.innerHTML = `
      <div class="bulk-btn-content">
        <svg class="bulk-icon" viewBox="0 0 24 24" fill="currentColor">
          <path d="M14,2H6A2,2 0 0,0 4,4V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V8L14,2M18,20H6V4H13V9H18V20M12,11L16,15H13.5V19H10.5V15H8L12,11Z" />
        </svg>
        <span class="bulk-text">Bulk Upload (${this.detectedTorrents.size})</span>
        <div class="bulk-tooltip">Upload all ${this.detectedTorrents.size} torrents to MediaFusion</div>
      </div>
    `;

    // Add click handler
    this.bulkUploadButton.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.handleBulkUpload();
    });

    // Add to page
    document.body.appendChild(this.bulkUploadButton);
  }

  hideBulkUploadButton() {
    if (this.bulkUploadButton) {
      this.bulkUploadButton.remove();
      this.bulkUploadButton = null;
    }
  }

  async handleBulkUpload() {
    if (this.detectedTorrents.size === 0) {
      return;
    }

    try {
      // Convert Map to Array and remove DOM elements (not serializable)
      const torrentsArray = Array.from(this.detectedTorrents.values()).map(torrent => ({
        id: torrent.id,
        type: torrent.type,
        url: torrent.url,
        title: torrent.title,
        contentType: torrent.contentType
        // Note: Removed 'element' property as DOM elements cannot be serialized
      }));

      // Send to background script to open bulk upload popup
      const response = await this.sendMessage({
        action: "openBulkUploadPopup",
        data: {
          torrents: torrentsArray,
          sourceUrl: window.location.href,
          pageTitle: document.title,
          totalCount: torrentsArray.length
        }
      });

      if (response && response.success) {
        // Show success feedback
        this.showBulkUploadFeedback('success', 'Bulk upload window opened!');
      } else {
        throw new Error('Failed to open bulk upload window');
      }
    } catch (error) {
      console.error('Bulk upload error:', error);
      this.showBulkUploadFeedback('error', 'Failed to open bulk upload window');
    }
  }

  showBulkUploadFeedback(type, message) {
    if (!this.bulkUploadButton) return;

    const originalContent = this.bulkUploadButton.innerHTML;

    if (type === 'success') {
      this.bulkUploadButton.innerHTML = `
        <div class="bulk-btn-content success">
          <svg class="bulk-icon" viewBox="0 0 24 24" fill="currentColor">
            <path d="M21,7L9,19L3.5,13.5L4.91,12.09L9,16.17L19.59,5.59L21,7Z" />
          </svg>
          <span class="bulk-text">${message}</span>
        </div>
      `;
    } else {
      this.bulkUploadButton.innerHTML = `
        <div class="bulk-btn-content error">
          <svg class="bulk-icon" viewBox="0 0 24 24" fill="currentColor">
            <path d="M13,13H11V7H13M13,17H11V15H13M12,2A10,10 0 0,0 2,12A10,10 0 0,0 12,22A10,10 0 0,0 22,12A10,10 0 0,0 12,2Z" />
          </svg>
          <span class="bulk-text">${message}</span>
        </div>
      `;
    }

    // Reset after 3 seconds
    setTimeout(() => {
      if (this.bulkUploadButton) {
        this.bulkUploadButton.innerHTML = originalContent;
      }
    }, 3000);
  }
}

// Prevent multiple instances
if (!window.mediaFusionContentScriptLoaded) {
  window.mediaFusionContentScriptLoaded = true;

  // Initialize content script when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      new MediaFusionContentScript();
    });
  } else {
    new MediaFusionContentScript();
  }
}

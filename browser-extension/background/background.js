// MediaFusion Browser Extension - Background Script
// Handles API communication and cross-origin requests

class MediaFusionAPI {
  constructor() {
    this.baseUrl = "https://mediafusion.elfhosted.com";
    this.uploaderName = "Anonymous";
    this.init();
  }

  async init() {
    // Load settings from storage
    const settings = await this.getSettings();
    this.baseUrl = settings.baseUrl || "https://mediafusion.elfhosted.com";
    this.uploaderName = settings.uploaderName || "Anonymous";
  }

  async getSettings() {
    return new Promise((resolve) => {
      const defaultSettings = {
        baseUrl: "https://mediafusion.elfhosted.com",
        uploaderName: "Anonymous",
        theme: "auto",
      };

      if (typeof browser !== "undefined" && browser.storage) {
        // Firefox
        browser.storage.sync
          .get(["baseUrl", "uploaderName", "theme"])
          .then((result) => {
            resolve({ ...defaultSettings, ...result });
          });
      } else if (typeof chrome !== "undefined" && chrome.storage) {
        // Chrome
        chrome.storage.sync.get(
          ["baseUrl", "uploaderName", "theme"],
          (result) => {
            resolve({ ...defaultSettings, ...result });
          }
        );
      } else {
        resolve(defaultSettings);
      }
    });
  }

  async saveSettings(settings) {
    return new Promise((resolve) => {
      if (typeof browser !== "undefined" && browser.storage) {
        // Firefox
        browser.storage.sync.set(settings).then(resolve);
      } else if (typeof chrome !== "undefined" && chrome.storage) {
        // Chrome
        chrome.storage.sync.set(settings, resolve);
      } else {
        resolve();
      }
    });
  }

  async updateSettings(newSettings) {
    await this.saveSettings(newSettings);
    this.baseUrl = newSettings.baseUrl || this.baseUrl;
    this.uploaderName = newSettings.uploaderName || this.uploaderName;
  }

  validateUrl(url) {
    try {
      new URL(url);
      return true;
    } catch {
      return false;
    }
  }

  async testConnection() {
    if (!this.baseUrl) {
      throw new Error("MediaFusion URL not configured");
    }

    if (!this.validateUrl(this.baseUrl)) {
      throw new Error("Invalid MediaFusion URL format");
    }

    try {
      const response = await fetch(`${this.baseUrl}/scraper/`, {
        method: "GET",
        headers: {
          "Content-Type": "text/html",
        },
      });

      if (response.ok) {
        return { success: true, message: "Connection successful" };
      } else {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
    } catch (error) {
      throw new Error(`Connection failed: ${error.message}`);
    }
  }

  async analyzeTorrent(torrentData) {
    if (!this.baseUrl) {
      throw new Error("MediaFusion URL not configured");
    }

    const formData = new FormData();
    formData.append("meta_type", torrentData.metaType || "movie");

    if (torrentData.magnetLink) {
      formData.append("magnet_link", torrentData.magnetLink);
    } else if (torrentData.torrentFileData) {
      // Reconstruct File from transmitted data
      const uint8Array = new Uint8Array(torrentData.torrentFileData.data);
      const blob = new Blob([uint8Array], {
        type: torrentData.torrentFileData.type,
      });
      const file = new File([blob], torrentData.torrentFileData.name, {
        type: torrentData.torrentFileData.type,
      });
      formData.append("torrent_file", file);
    } else {
      throw new Error("Either magnet link or torrent file is required");
    }

    try {
      const response = await fetch(`${this.baseUrl}/scraper/analyze_torrent`, {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      if (data.status === "success") {
        return data;
      } else {
        throw new Error(data.message || "Failed to analyze torrent");
      }
    } catch (error) {
      throw new Error(`Analysis failed: ${error.message}`);
    }
  }

  async uploadTorrent(torrentData) {
    if (!this.baseUrl) {
      throw new Error("MediaFusion URL not configured");
    }

    const formData = new FormData();

    // Basic metadata
    formData.append("meta_type", torrentData.metaType || "movie");
    formData.append("uploader", torrentData.uploaderName || this.uploaderName);
    formData.append("torrent_type", torrentData.torrentType || "public");

    // Set created_at to today if not provided
    const createdAt =
      torrentData.createdAt || new Date().toISOString().split("T")[0];
    formData.append("created_at", createdAt);

    // Content metadata
    if (torrentData.metaId) {
      formData.append("meta_id", torrentData.metaId);
    }
    if (torrentData.title) {
      formData.append("title", torrentData.title);
    }
    if (torrentData.poster) {
      formData.append("poster", torrentData.poster);
    }
    if (torrentData.background) {
      formData.append("background", torrentData.background);
    }
    if (torrentData.logo) {
      formData.append("logo", torrentData.logo);
    }

    // Technical specifications
    if (torrentData.resolution) {
      formData.append("resolution", torrentData.resolution);
    }
    if (torrentData.quality) {
      formData.append("quality", torrentData.quality);
    }
    if (torrentData.codec) {
      formData.append("codec", torrentData.codec);
    }
    if (torrentData.audio) {
      formData.append("audio", torrentData.audio);
    }
    if (torrentData.hdr) {
      formData.append("hdr", torrentData.hdr);
    }
    if (torrentData.languages) {
      formData.append("languages", torrentData.languages);
    }

    // Catalogs
    if (torrentData.catalogs) {
      formData.append(
        "catalogs",
        Array.isArray(torrentData.catalogs)
          ? torrentData.catalogs.join(",")
          : torrentData.catalogs
      );
    }

    // Torrent data
    if (torrentData.magnetLink) {
      formData.append("magnet_link", torrentData.magnetLink);
    } else if (torrentData.torrentFileData) {
      // Reconstruct File from transmitted data (preferred method)
      const uint8Array = new Uint8Array(torrentData.torrentFileData.data);
      const blob = new Blob([uint8Array], {
        type: torrentData.torrentFileData.type,
      });
      const file = new File([blob], torrentData.torrentFileData.name, {
        type: torrentData.torrentFileData.type,
      });
      formData.append("torrent_file", file);
    } else if (torrentData.torrentFile) {
      // Fallback for direct File objects (shouldn't happen in normal flow)
      formData.append("torrent_file", torrentData.torrentFile);
    } else {
      throw new Error("Either magnet link or torrent file is required");
    }

    // Series-specific: Episode name parser for automatic episode detection
    if (torrentData.episode_name_parser) {
      console.log(
        "Adding episode_name_parser to formData:",
        torrentData.episode_name_parser
      );
      formData.append("episode_name_parser", torrentData.episode_name_parser);
    }

    // Additional options
    if (torrentData.forceImport) {
      formData.append("force_import", "true");
    }

    // Quick import flag
    if (torrentData.isQuickImport) {
      formData.append("is_quick_import", "true");
    }

    // File data for series with episode details
    if (torrentData.fileData) {
      formData.append("file_data", JSON.stringify(torrentData.fileData));
    }

    try {
      const response = await fetch(`${this.baseUrl}/scraper/torrent`, {
        method: "POST",
        body: formData,
      });

      const data = await response.json();

      // Always return the full data object, regardless of status
      // Let the popup handle different statuses appropriately
      return data;
    } catch (error) {
      // For network errors or JSON parsing errors, throw a proper error
      throw new Error(`Upload failed: ${error.message}`);
    }
  }

  async quickUpload(magnetLink, options = {}) {
    try {
      // First analyze the torrent
      const analysisResult = await this.analyzeTorrent({
        magnetLink: magnetLink,
        metaType: options.metaType || "movie",
      });

      // If we have matches, use the first one
      if (analysisResult.matches && analysisResult.matches.length > 0) {
        const match = analysisResult.matches[0];
        const torrentData = analysisResult.torrent_data;

        const uploadData = {
          magnetLink: magnetLink,
          metaType: match.type,
          metaId: match.imdb_id,
          title: match.title,
          poster: match.poster,
          background: match.background,
          logo: match.logo,
          uploaderName: options.uploaderName || this.uploaderName,
          torrentType: options.torrentType || "public",
          // Copy technical specs from torrent data
          resolution: torrentData.resolution,
          quality: torrentData.quality,
          codec: torrentData.codec,
          audio: torrentData.audio,
          hdr: torrentData.hdr,
          languages: torrentData.languages,
        };

        return await this.uploadTorrent(uploadData);
      } else {
        // No matches found, upload with basic data
        const torrentData = analysisResult.torrent_data;
        const uploadData = {
          magnetLink: magnetLink,
          metaType: options.metaType || "movie",
          title: torrentData.title,
          uploaderName: options.uploaderName || this.uploaderName,
          torrentType: options.torrentType || "public",
          resolution: torrentData.resolution,
          quality: torrentData.quality,
          codec: torrentData.codec,
          audio: torrentData.audio,
          hdr: torrentData.hdr,
          languages: torrentData.languages,
        };

        return await this.uploadTorrent(uploadData);
      }
    } catch (error) {
      throw new Error(`Quick upload failed: ${error.message}`);
    }
  }
}

// Initialize API instance
const mediaFusionAPI = new MediaFusionAPI();

// Message handling for communication with content scripts and popup
const handleMessage = async (message, sender, sendResponse) => {
  try {
    switch (message.action) {
      case "getSettings":
        const settings = await mediaFusionAPI.getSettings();
        sendResponse({ success: true, data: settings });
        break;

      case "saveSettings":
        await mediaFusionAPI.updateSettings(message.data);
        sendResponse({ success: true });
        break;

      case "testConnection":
        const connectionResult = await mediaFusionAPI.testConnection();
        sendResponse({ success: true, data: connectionResult });
        break;

      case "analyzeTorrent":
        const analysisResult = await mediaFusionAPI.analyzeTorrent(
          message.data
        );
        sendResponse({ success: true, data: analysisResult });
        break;

      case "uploadTorrent":
        const uploadResult = await mediaFusionAPI.uploadTorrent(message.data);
        sendResponse({ success: true, data: uploadResult });
        break;

      case "quickUpload":
        const quickResult = await mediaFusionAPI.quickUpload(
          message.data.magnetLink,
          message.data.options
        );
        sendResponse({ success: true, data: quickResult });
        break;

      case "openPopupWithData":
        try {
          const popupData = message.data;

          // Use URL parameters only (simpler and more reliable)
          const popupUrl =
            typeof browser !== "undefined" && browser.runtime
              ? browser.runtime.getURL("popup/popup.html")
              : chrome.runtime.getURL("popup/popup.html");
          const params = new URLSearchParams();

          if (popupData.magnetLink) {
            params.append("magnet", encodeURIComponent(popupData.magnetLink));
          }
          if (popupData.torrentUrl) {
            params.append("torrent", encodeURIComponent(popupData.torrentUrl));
          }
          if (popupData.contentType) {
            params.append("type", popupData.contentType);
          }
          if (popupData.sourceUrl) {
            params.append("source", encodeURIComponent(popupData.sourceUrl));
          }
          if (popupData.title) {
            params.append("title", encodeURIComponent(popupData.title));
          }

          const fullUrl = `${popupUrl}?${params.toString()}`;

          // Try to open in a popup window, fallback to new tab if windows API is not available
          let windowOpened = false;

          try {
            // Get the current window to position popup relative to it
            const getCurrentWindow = () => {
              return new Promise((resolve) => {
                if (typeof browser !== "undefined" && browser.windows) {
                  browser.windows.getCurrent().then(resolve);
                } else if (typeof chrome !== "undefined" && chrome.windows) {
                  chrome.windows.getCurrent(resolve);
                } else {
                  resolve(null);
                }
              });
            };

            const currentWindow = await getCurrentWindow();

            // Calculate centered position
            const popupWidth = 500;
            const popupHeight = 700;
            let left = 100;
            let top = 100;

            if (currentWindow) {
              left = Math.round(currentWindow.left + (currentWindow.width - popupWidth) / 2);
              top = Math.round(currentWindow.top + (currentWindow.height - popupHeight) / 2);
            }

            if (typeof browser !== "undefined" && browser.windows) {
              // Firefox with windows permission
              await browser.windows.create({
                url: fullUrl,
                type: "popup",
                width: popupWidth,
                height: popupHeight,
                left: left,
                top: top,
                focused: true,
              });
              windowOpened = true;
            } else if (typeof chrome !== "undefined" && chrome.windows) {
              // Chrome with windows permission
              chrome.windows.create({
                url: fullUrl,
                type: "popup",
                width: popupWidth,
                height: popupHeight,
                left: left,
                top: top,
                focused: true,
              });
              windowOpened = true;
            }
          } catch (error) {
            console.log(
              "Windows API not available, falling back to tab:",
              error.message
            );
          }

          // Fallback: open in new tab if popup window creation failed
          if (!windowOpened) {
            if (typeof browser !== "undefined" && browser.tabs) {
              browser.tabs.create({ url: fullUrl });
            } else if (typeof chrome !== "undefined" && chrome.tabs) {
              chrome.tabs.create({ url: fullUrl });
            }
          }

          sendResponse({ success: true });
        } catch (error) {
          sendResponse({ success: false, error: error.message });
        }
        break;

      default:
        sendResponse({ success: false, error: "Unknown action" });
    }
  } catch (error) {
    sendResponse({ success: false, error: error.message });
  }
};

// Set up message listeners - use browser API for Firefox, chrome API for Chrome
if (
  typeof browser !== "undefined" &&
  browser.runtime &&
  browser.runtime.onMessage
) {
  // Firefox
  browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    handleMessage(message, sender, sendResponse);
    return true; // Keep the message channel open for async response
  });
} else if (
  typeof chrome !== "undefined" &&
  chrome.runtime &&
  chrome.runtime.onMessage
) {
  // Chrome
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    handleMessage(message, sender, sendResponse);
    return true; // Keep the message channel open for async response
  });
}

// Installation handler
const handleInstall = (details) => {
  if (details.reason === "install") {
    // Set default settings
    mediaFusionAPI.saveSettings({
      baseUrl: "https://mediafusion.elfhosted.com",
      uploaderName: "Anonymous",
    });
  }
};

// Installation handler - use browser API for Firefox, chrome API for Chrome
if (
  typeof browser !== "undefined" &&
  browser.runtime &&
  browser.runtime.onInstalled
) {
  // Firefox
  browser.runtime.onInstalled.addListener(handleInstall);
} else if (
  typeof chrome !== "undefined" &&
  chrome.runtime &&
  chrome.runtime.onInstalled
) {
  // Chrome
  chrome.runtime.onInstalled.addListener(handleInstall);
}

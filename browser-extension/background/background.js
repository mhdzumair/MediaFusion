// MediaFusion Browser Extension - Background Script
// Handles API communication and cross-origin requests
// Updated for v2.0 with React popup and new API endpoints

const STORAGE_KEY = "mediafusion_settings";
const PREFILLED_KEY = "mediafusion_prefilled_data";

function hasFirefoxRuntime() {
  return typeof browser !== "undefined" && !!browser.runtime;
}

function hasChromeRuntime() {
  return typeof chrome !== "undefined" && !!chrome.runtime;
}

function isFirefoxMobile() {
  return (
    hasFirefoxRuntime() &&
    typeof navigator !== "undefined" &&
    /android/i.test(navigator.userAgent || "")
  );
}

function getExtensionUrl(path) {
  if (hasFirefoxRuntime()) {
    return browser.runtime.getURL(path);
  }
  if (hasChromeRuntime()) {
    return chrome.runtime.getURL(path);
  }
  throw new Error("Extension runtime not available");
}

function toTabViewUrl(url) {
  try {
    const parsedUrl = new URL(url);
    parsedUrl.searchParams.set("view", "tab");
    return parsedUrl.toString();
  } catch {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}view=tab`;
  }
}

async function getCurrentWindowInfo() {
  if (
    hasFirefoxRuntime() &&
    browser.windows &&
    typeof browser.windows.getCurrent === "function"
  ) {
    return browser.windows.getCurrent();
  }

  if (
    hasChromeRuntime() &&
    chrome.windows &&
    typeof chrome.windows.getCurrent === "function"
  ) {
    return new Promise((resolve, reject) => {
      chrome.windows.getCurrent((window) => {
        if (chrome.runtime && chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(window || null);
      });
    });
  }

  return null;
}

async function createPopupWindow(url, width, height) {
  const currentWindow = await getCurrentWindowInfo();
  let left = 100;
  let top = 100;

  if (currentWindow) {
    const currentLeft = typeof currentWindow.left === "number" ? currentWindow.left : 0;
    const currentTop = typeof currentWindow.top === "number" ? currentWindow.top : 0;
    const currentWidth =
      typeof currentWindow.width === "number" ? currentWindow.width : width;
    const currentHeight =
      typeof currentWindow.height === "number" ? currentWindow.height : height;

    left = Math.round(currentLeft + (currentWidth - width) / 2);
    top = Math.round(currentTop + (currentHeight - height) / 2);
  }

  const createData = {
    url: url,
    type: "popup",
    width: width,
    height: height,
    left: left,
    top: top,
    focused: true,
  };

  if (
    hasFirefoxRuntime() &&
    browser.windows &&
    typeof browser.windows.create === "function"
  ) {
    await browser.windows.create(createData);
    return;
  }

  if (
    hasChromeRuntime() &&
    chrome.windows &&
    typeof chrome.windows.create === "function"
  ) {
    await new Promise((resolve, reject) => {
      chrome.windows.create(createData, () => {
        if (chrome.runtime && chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve();
      });
    });
    return;
  }

  throw new Error("Windows API not available");
}

async function createTab(url) {
  if (hasFirefoxRuntime() && browser.tabs && typeof browser.tabs.create === "function") {
    await browser.tabs.create({ url: url });
    return;
  }

  if (hasChromeRuntime() && chrome.tabs && typeof chrome.tabs.create === "function") {
    await new Promise((resolve, reject) => {
      chrome.tabs.create({ url: url }, () => {
        if (chrome.runtime && chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve();
      });
    });
    return;
  }

  throw new Error("Tabs API not available");
}

async function openExtensionUi(url, options = {}) {
  const width = options.width || 500;
  const height = options.height || 700;
  const forceTab = options.forceTab === true;
  const tabUrl = toTabViewUrl(url);

  // Firefox Android does not support popup windows like desktop Firefox.
  if (forceTab || isFirefoxMobile()) {
    await createTab(tabUrl);
    return;
  }

  try {
    await createPopupWindow(url, width, height);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    console.log("Popup window unavailable, falling back to tab:", message);
    await createTab(tabUrl);
  }
}

function getBulkStorageTarget() {
  // Firefox mobile can be inconsistent with storage.session; local is safest here.
  if (hasFirefoxRuntime() && browser.storage && browser.storage.local) {
    return { area: browser.storage.local, mode: "promise" };
  }

  if (hasChromeRuntime() && chrome.storage) {
    if (chrome.storage.session) {
      return { area: chrome.storage.session, mode: "callback" };
    }
    if (chrome.storage.local) {
      return { area: chrome.storage.local, mode: "callback" };
    }
  }

  return null;
}

async function setBulkStorageData(target, data) {
  if (!target) {
    throw new Error("No storage API available");
  }

  if (target.mode === "promise") {
    await target.area.set(data);
    return;
  }

  await new Promise((resolve, reject) => {
    target.area.set(data, () => {
      if (chrome.runtime && chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve();
    });
  });
}

class MediaFusionAPI {
  constructor() {
    this.baseUrl = "";
    this.authToken = null;
    this.apiKey = null;
    this.init();
  }

  async init() {
    // Load settings from storage
    const settings = await this.getSettings();
    this.baseUrl = settings.instanceUrl || "";
    this.authToken = settings.authToken || null;
    this.apiKey = settings.apiKey || null;
  }

  async getSettings() {
    return new Promise((resolve) => {
      const defaultSettings = {
        instanceUrl: "",
        authToken: null,
        contributeAnonymously: false,
        autoAnalyze: true,
      };

      if (typeof browser !== "undefined" && browser.storage) {
        // Firefox
        browser.storage.sync
          .get([STORAGE_KEY])
          .then((result) => {
            const stored = result[STORAGE_KEY] || {};
            resolve({ ...defaultSettings, ...stored });
          });
      } else if (typeof chrome !== "undefined" && chrome.storage) {
        // Chrome
        chrome.storage.sync.get(
          [STORAGE_KEY],
          (result) => {
            const stored = result[STORAGE_KEY] || {};
            resolve({ ...defaultSettings, ...stored });
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
        browser.storage.sync.set({ [STORAGE_KEY]: settings }).then(resolve);
      } else if (typeof chrome !== "undefined" && chrome.storage) {
        // Chrome
        chrome.storage.sync.set({ [STORAGE_KEY]: settings }, resolve);
      } else {
        resolve();
      }
    });
  }

  async updateSettings(newSettings) {
    const current = await this.getSettings();
    const updated = { ...current, ...newSettings };
    await this.saveSettings(updated);
    this.baseUrl = updated.instanceUrl || this.baseUrl;
    this.authToken = updated.authToken || this.authToken;
    this.apiKey = updated.apiKey || this.apiKey;
  }

  // Save prefilled data for the popup
  async savePrefilledData(data) {
    return new Promise((resolve) => {
      const storageData = { [PREFILLED_KEY]: data };
      if (typeof browser !== "undefined" && browser.storage) {
        browser.storage.local.set(storageData).then(resolve);
      } else if (typeof chrome !== "undefined" && chrome.storage) {
        chrome.storage.local.set(storageData, resolve);
      } else {
        resolve();
      }
    });
  }

  // Get authorization headers
  getAuthHeaders() {
    const headers = {
      "Accept": "application/json",
    };
    if (this.authToken) {
      headers["Authorization"] = `Bearer ${this.authToken}`;
    }
    // Include API key for private instances
    if (this.apiKey) {
      headers["X-API-Key"] = this.apiKey;
    }
    return headers;
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
      const response = await fetch(`${this.baseUrl}/health`, {
        method: "GET",
        headers: this.getAuthHeaders(),
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
      // For quick upload, directly call add torrent endpoint
      const uploadData = {
        metaType: options.metaType || "movie",
        uploaderName: options.uploaderName || this.uploaderName,
        torrentType: options.torrentType || "public",
        isQuickImport: true,
      };

      // Add optional metadata
      if (options.metaId) {
        uploadData.metaId = options.metaId;
      }

      // Add either magnet link or torrent file data
      if (magnetLink) {
        uploadData.magnetLink = magnetLink;
      } else if (options.torrentFileData) {
        uploadData.torrentFileData = options.torrentFileData;
      } else {
        throw new Error("Either magnet link or torrent file data is required");
      }

      return await this.uploadTorrent(uploadData);
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
          // Save prefilled data to storage for the React popup to read
          await mediaFusionAPI.savePrefilledData({
            magnetLink: popupData.magnetLink,
            torrentUrl: popupData.torrentUrl,
            torrentFileData: popupData.torrentFileData,
            torrentPrefetchWarning: popupData.torrentPrefetchWarning,
            contentType: popupData.contentType,
            pageUrl: popupData.sourceUrl,
            pageTitle: popupData.title,
          });

          const popupUrl = getExtensionUrl("popup/index.html");
          await openExtensionUi(popupUrl, { width: 500, height: 700 });

          sendResponse({ success: true });
        } catch (error) {
          sendResponse({ success: false, error: error.message });
        }
        break;

      case "saveAuthFromWebsite":
        try {
          const authData = message.data;
          console.log('[MediaFusion Background] Received authData:', {
            hasUser: !!authData.user,
            contributeAnonymously: authData.contributeAnonymously,
            anonymousDisplayName: authData.anonymousDisplayName,
          });

          // Get current settings
          const currentSettings = await mediaFusionAPI.getSettings();
          
          // Map user for extension (username -> display_name)
          const user = authData.user
            ? {
                ...authData.user,
                display_name: authData.user.display_name ?? authData.user.username ?? authData.user.email,
              }
            : undefined;

          // Update settings with auth data (sync account's contribution preferences)
          const updatedSettings = {
            ...currentSettings,
            instanceUrl: authData.instanceUrl || currentSettings.instanceUrl,
            authToken: authData.token,
            user,
          };
          
          if (authData.apiKey) {
            updatedSettings.apiKey = authData.apiKey;
          }
          
          // Sync contribution preferences from account
          if (typeof authData.contributeAnonymously === "boolean") {
            updatedSettings.contributeAnonymously = authData.contributeAnonymously;
          }
          if (authData.anonymousDisplayName !== undefined) {
            updatedSettings.anonymousDisplayName = authData.anonymousDisplayName || "";
          }
          
          await mediaFusionAPI.saveSettings(updatedSettings);
          
          // Update the API instance
          mediaFusionAPI.baseUrl = updatedSettings.instanceUrl;
          mediaFusionAPI.authToken = updatedSettings.authToken;
          
          // Fetch user preferences from API (source of truth for contribute_anonymously)
          if (updatedSettings.instanceUrl && authData.token) {
            try {
              const meUrl = `${updatedSettings.instanceUrl.replace(/\/$/, "")}/api/v1/auth/me`;
              const meRes = await fetch(meUrl, {
                headers: { Authorization: `Bearer ${authData.token}` },
              });
              if (meRes.ok) {
                const meData = await meRes.json();
                const current = await mediaFusionAPI.getSettings();
                const prefsUpdate = {};
                if (typeof meData.contribute_anonymously === "boolean") {
                  prefsUpdate.contributeAnonymously = meData.contribute_anonymously;
                }
                // anonymousDisplayName from web app localStorage (event) - API doesn't have it
                if (authData.anonymousDisplayName !== undefined) {
                  prefsUpdate.anonymousDisplayName = authData.anonymousDisplayName || "";
                }
                if (Object.keys(prefsUpdate).length > 0) {
                  await mediaFusionAPI.saveSettings({ ...current, ...prefsUpdate });
                  console.log("[MediaFusion Background] Synced contribution prefs:", prefsUpdate);
                }
              }
            } catch (err) {
              console.warn("[MediaFusion Background] Could not fetch user prefs from API:", err);
            }
          }
          
          console.log('[MediaFusion Background] Auth saved for user:', authData.user?.email);
          
          sendResponse({ success: true });
        } catch (error) {
          console.error('[MediaFusion Background] Error saving auth:', error);
          sendResponse({ success: false, error: error.message });
        }
        break;

      case "openBulkUploadPopup":
        try {
          const bulkData = message.data;

          // Create bulk upload popup URL with data
          const popupUrl = getExtensionUrl("popup/index.html");

          const params = new URLSearchParams();
          params.append("bulk", "true");
          params.append("count", bulkData.totalCount.toString());
          params.append("source", encodeURIComponent(bulkData.sourceUrl));
          params.append("title", encodeURIComponent(bulkData.pageTitle));

          // Store bulk torrent data in session storage for the popup to access
          const bulkSessionData = {
            torrents: bulkData.torrents,
            sourceUrl: bulkData.sourceUrl,
            pageTitle: bulkData.pageTitle,
            timestamp: Date.now()
          };

          const storageTarget = getBulkStorageTarget();
          await setBulkStorageData(storageTarget, { bulkUploadData: bulkSessionData });

          const fullUrl = `${popupUrl}?${params.toString()}`;
          await openExtensionUi(fullUrl, { width: 800, height: 700 });

          sendResponse({ success: true });
        } catch (error) {
          console.error("Bulk upload popup error:", error);
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

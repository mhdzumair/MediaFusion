// MediaFusion Browser Extension - Popup Script

class PopupManager {
  constructor() {
    this.selectedMatch = null;
    this.currentTorrentData = null;
    this.isProcessing = false;
    this.requestInProgress = new Set(); // Track specific request types
    this.hasAnalyzed = false; // Prevent duplicate analysis
    this.currentTheme = "auto"; // Default theme
    this.init();
  }

  async init() {
    this.setupEventListeners();

    // Apply theme as early as possible to prevent flash
    await this.initializeTheme();

    // Load and display extension version
    this.loadExtensionVersion();

    // Populate language options
    this.populateLanguageOptions();

    await this.loadSettings(); // Wait for settings to load before continuing
    this.checkConnectionStatus();
    this.checkForPrefilledData();
  }

  setupEventListeners() {
    // Tab switching
    document.querySelectorAll(".tab-button").forEach((button) => {
      button.addEventListener("click", (e) => {
        this.switchTab(e.target.dataset.tab);
      });
    });

    // Auto-save settings on input changes
    this.setupAutoSaveListeners();

    document
      .getElementById("test-connection-btn")
      .addEventListener("click", () => {
        this.testConnection();
      });

    // Theme change listener - apply theme immediately when changed
    document.getElementById("theme-select").addEventListener("change", (e) => {
      this.applyTheme(e.target.value);
      // Auto-save settings including theme (no URL validation needed)
      this.autoSaveSettings("theme");
    });

    // Upload functionality with request deduplication
    document.getElementById("analyze-btn").addEventListener("click", () => {
      this.handleAnalyze();
    });

    document
      .getElementById("quick-import-btn")
      .addEventListener("click", () => {
        this.handleQuickImport();
      });

    document
      .getElementById("upload-with-match-btn")
      .addEventListener("click", () => {
        this.handleUploadWithMatch();
      });

    document
      .getElementById("upload-manual-btn")
      .addEventListener("click", () => {
        this.handleUploadManual();
      });

    document
      .getElementById("back-to-basic-btn")
      .addEventListener("click", () => {
        this.backToBasicUpload();
      });

    // File input handling
    document.getElementById("torrent-file").addEventListener("change", (e) => {
      if (e.target.files[0]) {
        document.getElementById("magnet-input").value = "";
      }
      this.updateClearButtonVisibility();
    });

    document.getElementById("magnet-input").addEventListener("input", (e) => {
      if (e.target.value.trim()) {
        document.getElementById("torrent-file").value = "";
      }
      this.updateClearButtonVisibility();
    });

    // Content type change handler
    document.getElementById("content-type").addEventListener("change", () => {
      // Show/hide sports category selection
      this.toggleSportsCategoryVisibility();

      // Update series options visibility when in advanced mode
      if (
        !document
          .getElementById("advanced-options")
          .classList.contains("hidden")
      ) {
        this.populateCatalogOptions();
      }
    });

    // Quality change handler for movie catalogs
    document.getElementById("quality").addEventListener("change", () => {
      // Update movie catalogs when quality changes
      if (
        !document
          .getElementById("advanced-options")
          .classList.contains("hidden") &&
        document.getElementById("content-type").value === "movie"
      ) {
        this.populateCatalogOptions();
      }
    });

    // Language selection modal handlers (only for advanced mode)
    document
      .getElementById("advanced-language-select-btn")
      .addEventListener("click", () => {
        this.showLanguageSelectionModal("advanced");
      });

    document
      .getElementById("close-language-modal")
      .addEventListener("click", () => {
        this.hideLanguageSelectionModal();
      });

    document.getElementById("clear-languages").addEventListener("click", () => {
      this.clearLanguageSelection();
    });

    document
      .getElementById("confirm-languages")
      .addEventListener("click", () => {
        this.confirmLanguageSelection();
      });

    document
      .getElementById("language-search")
      .addEventListener("input", (e) => {
        this.filterLanguages(e.target.value);
      });

    // File annotation modal handlers
    document
      .getElementById("close-annotation-modal")
      .addEventListener("click", () => {
        this.hideFileAnnotationModal();
      });

    document
      .getElementById("cancel-annotation")
      .addEventListener("click", () => {
        this.hideFileAnnotationModal();
      });

    document
      .getElementById("confirm-annotation")
      .addEventListener("click", () => {
        this.handleAnnotationConfirm();
      });
  }

  // Prevent duplicate requests with specific tracking
  async handleAnalyze() {
    const requestKey = "analyze";

    if (this.requestInProgress.has(requestKey)) {
      return;
    }
    this.requestInProgress.add(requestKey);
    try {
      await this.analyzeTorrent();
    } finally {
      this.requestInProgress.delete(requestKey);
    }
  }

  async handleUploadWithMatch() {
    const requestKey = "upload-match";
    if (this.requestInProgress.has(requestKey)) {
      return;
    }

    this.requestInProgress.add(requestKey);
    try {
      await this.uploadWithMatch();
    } finally {
      this.requestInProgress.delete(requestKey);
    }
  }

  async handleUploadManual() {
    const requestKey = "upload-manual";
    if (this.requestInProgress.has(requestKey)) {
      return;
    }

    this.requestInProgress.add(requestKey);
    try {
      await this.uploadManual();
    } finally {
      this.requestInProgress.delete(requestKey);
    }
  }

  async handleQuickImport() {
    const requestKey = "quick-import";
    if (this.requestInProgress.has(requestKey)) {
      return;
    }

    this.requestInProgress.add(requestKey);
    try {
      const basicData = this.collectBasicData();
      this.showLoading(true);

      // Prepare torrent data for quick import (let backend auto-detect everything)
      const torrentData = {
        // Basic required fields
        metaType: basicData.metaType,
        uploaderName: basicData.uploaderName,

        // Quick import mode - backend will auto-detect metadata
        isQuickImport: true,

        // Torrent source
        magnetLink: basicData.magnetLink,
        torrentFile: basicData.torrentFile,

        // Let backend auto-detect these fields
        languages: null, // Auto-detect from torrent name/files
        torrentType: null, // Auto-detect
        title: null, // Auto-detect from torrent name
        resolution: null, // Auto-detect
        quality: null, // Auto-detect
        codec: null, // Auto-detect
        audio: null, // Auto-detect
        catalogs: basicData.sportsCategory ? [basicData.sportsCategory] : null, // Use selected sports category or auto-select
        metaId: null, // Auto-search if possible
      };

      // Convert File object for transmission if needed
      await this.convertTorrentFileForTransmission(torrentData);

      this.showMessage("Uploading torrent to MediaFusion...", "info");

      const response = await this.sendMessage({
        action: "uploadTorrent",
        data: torrentData,
      });

      if (response.success) {
        await this.handleUploadResponse(response.data, torrentData);
      } else {
        this.showMessage(
          "Quick Import failed: " + (response.error || "Unknown error"),
          "error"
        );
      }
    } catch (error) {
      this.showMessage("Quick Import failed: " + error.message, "error");
    } finally {
      this.showLoading(false);
      this.requestInProgress.delete(requestKey);
    }
  }

  async checkForPrefilledData() {
    // Only check URL parameters (simpler and more reliable)
    const urlParams = new URLSearchParams(window.location.search);
    const magnetLink = urlParams.get("magnet");
    const torrentUrl = urlParams.get("torrent");
    const contentType = urlParams.get("type");
    const sourceUrl = urlParams.get("source");
    const title = urlParams.get("title");
    const isBulkMode = urlParams.get("bulk") === "true";

    // Handle bulk upload mode
    if (isBulkMode) {
      await this.initializeBulkUploadMode();
      return;
    }

    if (magnetLink) {
      document.getElementById("magnet-input").value =
        decodeURIComponent(magnetLink);
    } else if (torrentUrl) {
      // For torrent URLs, we need to download and convert to file
      this.handleTorrentUrl(decodeURIComponent(torrentUrl));
    }

    if (contentType) {
      document.getElementById("content-type").value = contentType;
      // Show sports category if content type is sports
      this.toggleSportsCategoryVisibility();
    }

    // Don't auto-analyze - let user choose what to do
    // This addresses the second issue about direct analysis
  }

  async handleTorrentUrl(torrentUrl) {
    try {
      // Show loading overlay
      this.showLoading(true);
      this.showMessage("Downloading torrent file...", "info");

      // Download the torrent file
      const response = await fetch(torrentUrl);
      if (!response.ok) {
        throw new Error("Failed to download torrent file");
      }

      const blob = await response.blob();
      const file = new File([blob], "downloaded.torrent", {
        type: "application/x-bittorrent",
      });

      // Create a file input element and set the file
      const fileInput = document.getElementById("torrent-file");
      const dataTransfer = new DataTransfer();
      dataTransfer.items.add(file);
      fileInput.files = dataTransfer.files;

      // Clear magnet input since we have a file
      document.getElementById("magnet-input").value = "";

      this.showMessage(
        "Torrent file downloaded and loaded successfully",
        "success"
      );
    } catch (error) {
      this.showMessage(
        "Failed to download torrent file: " + error.message,
        "error"
      );
    } finally {
      this.showLoading(false);
    }
  }

  switchTab(tabName) {
    document.querySelectorAll(".tab-button").forEach((button) => {
      button.classList.remove("active");
    });
    document.querySelector(`[data-tab="${tabName}"]`).classList.add("active");

    document.querySelectorAll(".tab-content").forEach((content) => {
      content.classList.remove("active");
    });
    document.getElementById(`${tabName}-tab`).classList.add("active");
  }

  showBasicUpload() {
    document.getElementById("basic-upload").classList.remove("hidden");
    document.getElementById("analysis-results").classList.add("hidden");
    document.getElementById("advanced-options").classList.add("hidden");
    this.clearForm();
  }

  backToBasicUpload() {
    // Go back to basic without clearing torrent/magnet input
    document.getElementById("basic-upload").classList.remove("hidden");
    document.getElementById("analysis-results").classList.add("hidden");
    document.getElementById("advanced-options").classList.add("hidden");

    // Clear only advanced fields, preserve basic inputs
    this.clearAdvancedFields();

    // Show clear button if there's input
    this.updateClearButtonVisibility();
  }

  clearAdvancedFields() {
    // Clear only advanced form fields, not basic inputs
    document.getElementById("imdb-id").value = "";
    document.getElementById("title").value = "";
    document.getElementById("resolution").value = "";
    document.getElementById("quality").value = "";
    document.getElementById("codec").value = "";
    document.getElementById("audio").value = "";
    document.getElementById("episode-name-parser").value = "";
    document.getElementById("sports-episode-parser").value = "";

    // Reset language selection (only advanced)
    this.selectedLanguages = [];
    document.getElementById("advanced-selected-languages").textContent =
      "Auto-detect";

    document
      .querySelectorAll('input[name="catalogs"]')
      .forEach((cb) => (cb.checked = false));

    this.selectedMatch = null;
    this.currentTorrentData = null;
  }

  updateClearButtonVisibility() {
    const magnetInput = document.getElementById("magnet-input").value.trim();
    const torrentFile = document.getElementById("torrent-file").files[0];
    const hasInput = magnetInput || torrentFile;

    // Show/hide clear button based on input
    let clearBtn = document.getElementById("clear-input-btn");
    if (hasInput && !clearBtn) {
      // Create clear button if it doesn't exist
      clearBtn = document.createElement("button");
      clearBtn.id = "clear-input-btn";
      clearBtn.className = "btn btn-secondary btn-sm";
      clearBtn.textContent = "Clear Input";
      clearBtn.style.marginTop = "10px";
      clearBtn.addEventListener("click", () => {
        this.clearBasicInput();
      });

      // Add after the file input
      const fileGroup = document.querySelector(
        ".form-group:has(#torrent-file)"
      );
      if (fileGroup) {
        fileGroup.appendChild(clearBtn);
      }
    } else if (!hasInput && clearBtn) {
      // Remove clear button if no input
      clearBtn.remove();
    }
  }

  clearBasicInput() {
    document.getElementById("magnet-input").value = "";
    document.getElementById("torrent-file").value = "";
    this.updateClearButtonVisibility();
    this.showMessage("Input cleared", "info");
  }

  toggleSportsCategoryVisibility() {
    const contentType = document.getElementById("content-type").value;
    const sportsCategoryRow = document.getElementById("sports-category-row");

    if (contentType === "sports") {
      sportsCategoryRow.style.display = "flex";
      // Try to auto-detect sports category
      this.autoDetectSportsCategory();
    } else {
      sportsCategoryRow.style.display = "none";
      // Reset the selection when hiding
      document.getElementById("sports-category").value = "";
    }
  }

  autoDetectSportsCategory() {
    // Get text sources for detection
    const magnetInput = document
      .getElementById("magnet-input")
      .value.toLowerCase();
    const torrentFile = document.getElementById("torrent-file").files[0];
    const torrentFileName = torrentFile ? torrentFile.name.toLowerCase() : "";

    // Combine all text sources
    const allText = `${magnetInput} ${torrentFileName}`;

    // Sports category patterns
    const sportsPatterns = [
      {
        category: "formula_racing",
        patterns: [
          /\bf1\b/i,
          /formula\s*1/i,
          /grand\s*prix/i,
          /motogp/i,
          /formula\s*racing/i,
        ],
      },
      {
        category: "american_football",
        patterns: [/\bnfl\b/i, /american\s*football/i, /super\s*bowl/i],
      },
      {
        category: "basketball",
        patterns: [/\bnba\b/i, /basketball/i, /\bncaa\b.*basketball/i],
      },
      {
        category: "football",
        patterns: [
          /premier\s*league/i,
          /champions\s*league/i,
          /europa\s*league/i,
          /world\s*cup/i,
          /euro\s*\d+/i,
          /\bfifa\b/i,
          /\buefa\b/i,
        ],
      },
      {
        category: "baseball",
        patterns: [/\bmlb\b/i, /baseball/i, /world\s*series/i],
      },
      {
        category: "hockey",
        patterns: [/\bnhl\b/i, /hockey/i, /stanley\s*cup/i],
      },
      {
        category: "fighting",
        patterns: [
          /\bufc\b/i,
          /\bwwe\b/i,
          /\baew\b/i,
          /\bmma\b/i,
          /mixed\s*martial\s*arts/i,
          /wrestling/i,
          /boxing/i,
          /fight\s*night/i,
          /pay\s*per\s*view/i,
          /\bppv\b/i,
        ],
      },
      {
        category: "rugby",
        patterns: [/rugby/i, /\bafl\b/i, /australian\s*football/i],
      },
      {
        category: "motogp_racing",
        patterns: [/motogp/i, /moto\s*gp/i, /motorcycle\s*racing/i],
      },
    ];

    // Try to match patterns
    for (const sport of sportsPatterns) {
      for (const pattern of sport.patterns) {
        if (pattern.test(allText)) {
          console.log(
            `Auto-detected sports category: ${sport.category} (pattern: ${pattern})`
          );
          document.getElementById("sports-category").value = sport.category;
          return;
        }
      }
    }

    // Default to empty if no match found
    console.log("No sports category auto-detected");
  }

  showAdvancedOptions() {
    document.getElementById("basic-upload").classList.add("hidden");
    document.getElementById("advanced-options").classList.remove("hidden");
    this.populateCatalogOptions();
  }

  populateCatalogOptions() {
    const contentType = document.getElementById("content-type").value;
    const catalogContainer = document.getElementById("catalog-selection");
    const seriesOptions = document.getElementById("series-options");
    const sportsOptions = document.getElementById("sports-options");

    // Show/hide content-specific options
    if (contentType === "series") {
      seriesOptions.style.display = "block";
      sportsOptions.style.display = "none";
    } else if (contentType === "sports") {
      seriesOptions.style.display = "none";
      sportsOptions.style.display = "block";
    } else {
      seriesOptions.style.display = "none";
      sportsOptions.style.display = "none";
    }

    // Clear existing content safely
    while (catalogContainer.firstChild) {
      catalogContainer.removeChild(catalogContainer.firstChild);
    }

    if (contentType === "movie") {
      this.populateMovieCatalogs(catalogContainer);
    } else if (contentType === "series") {
      const catalogGrid = document.createElement("div");
      catalogGrid.className = "catalog-grid";

      const seriesCatalogs = [
        { value: "anime_series", text: "Anime Series" },
        { value: "arabic_series", text: "Arabic Series" },
        { value: "bangla_series", text: "Bangla Series" },
        { value: "english_series", text: "English Series" },
        { value: "hindi_series", text: "Hindi Series" },
        { value: "kannada_series", text: "Kannada Series" },
        { value: "malayalam_series", text: "Malayalam Series" },
        { value: "punjabi_series", text: "Punjabi Series" },
        { value: "tamil_series", text: "Tamil Series" },
        { value: "telugu_series", text: "Telugu Series" },
      ];

      seriesCatalogs.forEach((catalog) => {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "catalogs";
        checkbox.value = catalog.value;
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(" " + catalog.text));
        catalogGrid.appendChild(label);
      });

      catalogContainer.appendChild(catalogGrid);
    } else if (contentType === "sports") {
      const select = document.createElement("select");
      select.id = "sports-catalog";
      select.className = "form-control";

      const sportsOptions = [
        { value: "", text: "Select Sports Category" },
        { value: "american_football", text: "American Football" },
        { value: "baseball", text: "Baseball" },
        { value: "basketball", text: "Basketball" },
        { value: "football", text: "Football" },
        { value: "formula_racing", text: "Formula Racing" },
        { value: "hockey", text: "Hockey" },
        { value: "motogp_racing", text: "MotoGP Racing" },
        { value: "rugby", text: "Rugby/AFL" },
        { value: "other_sports", text: "Other Sports" },
        { value: "fighting", text: "Fighting (WWE, UFC)" },
      ];

      sportsOptions.forEach((sport) => {
        const option = document.createElement("option");
        option.value = sport.value;
        option.textContent = sport.text;
        select.appendChild(option);
      });

      catalogContainer.appendChild(select);
    }
  }

  populateMovieCatalogs(catalogContainer) {
    const catalogGrid = document.createElement("div");
    catalogGrid.className = "catalog-grid";

    // Get current quality selection
    const selectedQuality = document
      .getElementById("quality")
      .value.toLowerCase();

    // Define quality categories
    const lowQualityTypes = ["cam", "telecine", "telesync", "scr", "screener"];
    const isLowQuality = lowQualityTypes.some((type) =>
      selectedQuality.includes(type)
    );

    // Categorize movie catalogs by quality type
    const movieCatalogs = {
      // High quality catalogs (HDRip)
      highQuality: [
        { value: "arabic_movies", text: "Arabic Movies" },
        { value: "bangla_movies", text: "Bangla Movies" },
        { value: "english_hdrip", text: "English HD Movies" },
        { value: "hindi_hdrip", text: "Hindi HD Movies" },
        { value: "kannada_hdrip", text: "Kannada HD Movies" },
        { value: "malayalam_hdrip", text: "Malayalam HD Movies" },
        { value: "punjabi_movies", text: "Punjabi Movies" },
        { value: "tamil_hdrip", text: "Tamil HD Movies" },
        { value: "telugu_hdrip", text: "Telugu HD Movies" },
      ],

      // Low quality catalogs (TCRip)
      lowQuality: [
        { value: "english_tcrip", text: "English TCRip Movies" },
        { value: "hindi_tcrip", text: "Hindi TCRip Movies" },
        { value: "kannada_tcrip", text: "Kannada TCRip Movies" },
        { value: "malayalam_tcrip", text: "Malayalam TCRip Movies" },
        { value: "tamil_tcrip", text: "Tamil TCRip Movies" },
        { value: "telugu_tcrip", text: "Telugu TCRip Movies" },
      ],

      // Quality-independent catalogs (always shown)
      independent: [
        { value: "anime_movies", text: "Anime Movies" },
        { value: "fighting", text: "Fighting (WWE, UFC)" },
        { value: "other_sports", text: "Other Sports" },
        { value: "hindi_dubbed", text: "Hindi Dubbed Movies" },
        { value: "hindi_old", text: "Hindi Old Movies" },
        { value: "kannada_dubbed", text: "Kannada Dubbed Movies" },
        { value: "kannada_old", text: "Kannada Old Movies" },
        { value: "malayalam_dubbed", text: "Malayalam Dubbed Movies" },
        { value: "malayalam_old", text: "Malayalam Old Movies" },
        { value: "tamil_dubbed", text: "Tamil Dubbed Movies" },
        { value: "tamil_old", text: "Tamil Old Movies" },
        { value: "telugu_dubbed", text: "Telugu Dubbed Movies" },
        { value: "telugu_old", text: "Telugu Old Movies" },
      ],
    };

    // Determine which catalogs to show
    let catalogsToShow = [...movieCatalogs.independent]; // Always include independent

    if (isLowQuality) {
      // Show low quality catalogs
      catalogsToShow = [...catalogsToShow, ...movieCatalogs.lowQuality];
    } else {
      // Show high quality catalogs (default)
      catalogsToShow = [...catalogsToShow, ...movieCatalogs.highQuality];
    }

    // Sort catalogs alphabetically for better organization
    catalogsToShow.sort((a, b) => a.text.localeCompare(b.text));

    // Create catalog checkboxes
    catalogsToShow.forEach((catalog) => {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.name = "catalogs";
      checkbox.value = catalog.value;
      label.appendChild(checkbox);
      label.appendChild(document.createTextNode(" " + catalog.text));
      catalogGrid.appendChild(label);
    });

    // Add quality indicator
    const qualityIndicator = document.createElement("div");
    qualityIndicator.className = "quality-indicator";

    if (selectedQuality) {
      if (isLowQuality) {
        qualityIndicator.classList.add("low-quality");
        qualityIndicator.textContent = `📹 Showing low quality (${selectedQuality.toUpperCase()}) categories`;
      } else {
        qualityIndicator.classList.add("high-quality");
        qualityIndicator.textContent = `🎬 Showing high quality categories`;
      }
    } else {
      qualityIndicator.classList.add("high-quality");
      qualityIndicator.textContent =
        "🎬 Showing high quality categories (default)";
    }

    catalogContainer.appendChild(catalogGrid);
    catalogContainer.appendChild(qualityIndicator);
  }

  async loadSettings() {
    try {
      const response = await this.sendMessage({ action: "getSettings" });

      if (response.success) {
        const settings = response.data;

        // Show actual stored values (including defaults from background)
        document.getElementById("mediafusion-url").value =
          settings.baseUrl || "https://mediafusion.elfhosted.com";
        document.getElementById("default-uploader").value =
          settings.uploaderName || "Anonymous";
        document.getElementById("uploader-name").value =
          settings.uploaderName || "Anonymous";
        document.getElementById("theme-select").value =
          settings.theme || "auto";
        this.applyTheme(settings.theme || "auto");
      }
    } catch (error) {
      this.showMessage("Failed to load settings: " + error.message, "error");
    }
  }

  async autoSaveSettings(fieldType = "all") {
    // Silent auto-save without success messages
    await this.saveSettings(true, fieldType);
  }

  async saveSettings(silent = false, fieldType = "all") {
    let baseUrl =
      document.getElementById("mediafusion-url").value.trim() ||
      "https://mediafusion.elfhosted.com";

    // Strip trailing slash from URL
    if (baseUrl.endsWith("/")) {
      baseUrl = baseUrl.slice(0, -1);
    }

    const settings = {
      baseUrl: baseUrl,
      uploaderName:
        document.getElementById("default-uploader").value.trim() || "Anonymous",
      theme: document.getElementById("theme-select").value || "auto",
    };

    try {
      const response = await this.sendMessage({
        action: "saveSettings",
        data: settings,
      });

      if (response.success) {
        if (!silent) {
          this.showMessage("Settings saved successfully", "success");
        }
        document.getElementById("uploader-name").value = settings.uploaderName;
        this.applyTheme(settings.theme);

        // Only validate connection when URL changes or when saving all settings
        if (fieldType === "url" || fieldType === "all") {
          this.checkConnectionStatus();
        }
      } else {
        if (!silent) {
          this.showMessage(
            "Failed to save settings: " + (response.error || "Unknown error"),
            "error"
          );
        }
      }
    } catch (error) {
      if (!silent) {
        this.showMessage("Failed to save settings: " + error.message, "error");
      }
    }
  }

  async initializeTheme() {
    try {
      // Get theme setting directly from storage for immediate application
      const response = await this.sendMessage({ action: "getSettings" });
      if (response.success && response.data && response.data.theme) {
        this.applyTheme(response.data.theme);
      } else {
        // Apply default theme
        this.applyTheme("auto");
      }
    } catch (error) {
      console.log("Failed to initialize theme:", error);
      // Apply default theme on error
      this.applyTheme("auto");
    }
  }

  applyTheme(theme) {
    const html = document.documentElement;

    // Remove existing theme attributes
    html.removeAttribute("data-theme");

    if (theme === "light") {
      html.setAttribute("data-theme", "light");
    } else if (theme === "dark") {
      html.setAttribute("data-theme", "dark");
    }
    // For 'auto', we don't set any attribute, letting CSS media queries handle it

    // Store theme preference for consistency
    this.currentTheme = theme;
  }

  loadExtensionVersion() {
    try {
      // Get manifest version using the appropriate API
      if (typeof browser !== "undefined" && browser.runtime) {
        // Firefox
        const manifest = browser.runtime.getManifest();
        document.getElementById(
          "extension-version"
        ).textContent = `v${manifest.version}`;
      } else if (typeof chrome !== "undefined" && chrome.runtime) {
        // Chrome
        const manifest = chrome.runtime.getManifest();
        document.getElementById(
          "extension-version"
        ).textContent = `v${manifest.version}`;
      }
    } catch (error) {
      console.log("Failed to load extension version:", error);
      // Keep the fallback version if API fails
    }
  }

  populateLanguageOptions() {
    // Initialize language selection state
    this.selectedLanguages = [];
    this.currentLanguageMode = "advanced"; // Only advanced mode has language selection

    // Supported languages from MediaFusion
    this.supportedLanguages = [
      "English",
      "Tamil",
      "Hindi",
      "Malayalam",
      "Kannada",
      "Telugu",
      "Chinese",
      "Russian",
      "Arabic",
      "Japanese",
      "Korean",
      "Taiwanese",
      "Latino",
      "French",
      "Spanish",
      "Portuguese",
      "Italian",
      "German",
      "Ukrainian",
      "Polish",
      "Czech",
      "Thai",
      "Indonesian",
      "Vietnamese",
      "Dutch",
      "Bengali",
      "Turkish",
      "Greek",
      "Swedish",
      "Romanian",
      "Hungarian",
      "Finnish",
      "Norwegian",
      "Danish",
      "Hebrew",
      "Lithuanian",
      "Punjabi",
      "Marathi",
      "Gujarati",
      "Bhojpuri",
      "Nepali",
      "Urdu",
      "Tagalog",
      "Filipino",
      "Malay",
      "Mongolian",
      "Armenian",
      "Georgian",
    ];

    // Language flags for better UX
    this.languageFlags = {
      English: "🇬🇧",
      Tamil: "🇮🇳",
      Hindi: "🇮🇳",
      Malayalam: "🇮🇳",
      Kannada: "🇮🇳",
      Telugu: "🇮🇳",
      Chinese: "🇨🇳",
      Russian: "🇷🇺",
      Arabic: "🇸🇦",
      Japanese: "🇯🇵",
      Korean: "🇰🇷",
      Taiwanese: "🇹🇼",
      Latino: "🇲🇽",
      French: "🇫🇷",
      Spanish: "🇪🇸",
      Portuguese: "🇵🇹",
      Italian: "🇮🇹",
      German: "🇩🇪",
      Ukrainian: "🇺🇦",
      Polish: "🇵🇱",
      Czech: "🇨🇿",
      Thai: "🇹🇭",
      Indonesian: "🇮🇩",
      Vietnamese: "🇻🇳",
      Dutch: "🇳🇱",
      Bengali: "🇧🇩",
      Turkish: "🇹🇷",
      Greek: "🇬🇷",
      Swedish: "🇸🇪",
      Romanian: "🇷🇴",
      Hungarian: "🇭🇺",
      Finnish: "🇫🇮",
      Norwegian: "🇳🇴",
      Danish: "🇩🇰",
      Hebrew: "🇮🇱",
      Lithuanian: "🇱🇹",
      Punjabi: "🇮🇳",
      Marathi: "🇮🇳",
      Gujarati: "🇮🇳",
      Bhojpuri: "🇮🇳",
      Nepali: "🇳🇵",
      Urdu: "🇵🇰",
      Tagalog: "🇵🇭",
      Filipino: "🇵🇭",
      Malay: "🇲🇾",
      Mongolian: "🇲🇳",
      Armenian: "🇦🇲",
      Georgian: "🇬🇪",
    };
  }

  showLanguageSelectionModal(mode) {
    this.currentLanguageMode = mode;
    const modal = document.getElementById("language-selection-modal");
    const checkboxContainer = document.getElementById("language-checkboxes");

    // Clear existing checkboxes
    while (checkboxContainer.firstChild) {
      checkboxContainer.removeChild(checkboxContainer.firstChild);
    }

    // Create checkboxes for each language
    this.supportedLanguages.forEach((language) => {
      const flag = this.languageFlags[language] || "";
      const displayText = flag ? `${flag} ${language}` : language;

      const div = document.createElement("div");
      div.className = "language-checkbox-item";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.id = `lang-${language.replace(/[^a-zA-Z0-9]/g, "")}`;
      checkbox.value = language;
      checkbox.checked = this.selectedLanguages.includes(language);

      const label = document.createElement("label");
      label.setAttribute("for", checkbox.id);
      label.textContent = displayText;

      div.appendChild(checkbox);
      div.appendChild(label);
      checkboxContainer.appendChild(div);
    });

    // Clear search
    document.getElementById("language-search").value = "";

    modal.classList.remove("hidden");
  }

  hideLanguageSelectionModal() {
    const modal = document.getElementById("language-selection-modal");
    modal.classList.add("hidden");
  }

  clearLanguageSelection() {
    document
      .querySelectorAll('#language-checkboxes input[type="checkbox"]')
      .forEach((checkbox) => {
        checkbox.checked = false;
      });
  }

  confirmLanguageSelection() {
    const selectedLanguages = [];
    document
      .querySelectorAll('#language-checkboxes input[type="checkbox"]:checked')
      .forEach((checkbox) => {
        selectedLanguages.push(checkbox.value);
      });

    this.selectedLanguages = selectedLanguages;
    this.updateLanguageDisplay();
    this.hideLanguageSelectionModal();
  }

  updateLanguageDisplay() {
    const displayText =
      this.selectedLanguages.length > 0
        ? this.selectedLanguages.join(", ")
        : "Auto-detect";

    // Only update advanced language display
    document.getElementById("advanced-selected-languages").textContent =
      displayText;
  }

  filterLanguages(searchTerm) {
    const checkboxItems = document.querySelectorAll(".language-checkbox-item");
    const searchLower = searchTerm.toLowerCase();

    checkboxItems.forEach((item) => {
      const label = item.querySelector("label");
      const languageName = label.textContent.toLowerCase();

      if (languageName.includes(searchLower)) {
        item.style.display = "block";
      } else {
        item.style.display = "none";
      }
    });
  }

  setupAutoSaveListeners() {
    // Debounce timer to avoid too many save operations
    let saveTimeout;

    const debouncedSave = (fieldType) => {
      clearTimeout(saveTimeout);
      saveTimeout = setTimeout(() => {
        this.autoSaveSettings(fieldType);
      }, 500); // Wait 500ms after user stops typing
    };

    // Auto-save on URL input changes
    document
      .getElementById("mediafusion-url")
      .addEventListener("input", () => debouncedSave("url"));
    document.getElementById("mediafusion-url").addEventListener("blur", () => {
      // Save immediately when field loses focus
      clearTimeout(saveTimeout);
      this.autoSaveSettings("url");
    });

    // Auto-save on uploader name input changes
    document
      .getElementById("default-uploader")
      .addEventListener("input", () => debouncedSave("uploader"));
    document.getElementById("default-uploader").addEventListener("blur", () => {
      // Save immediately when field loses focus
      clearTimeout(saveTimeout);
      this.autoSaveSettings("uploader");
    });
  }

  async testConnection() {
    this.setConnectionStatus("testing", "Testing connection...");

    try {
      const response = await this.sendMessage({ action: "testConnection" });
      if (response.success) {
        this.setConnectionStatus("connected", "Connected successfully");
        this.showMessage("Connection test successful", "success");
      } else {
        this.setConnectionStatus("error", "Connection failed");
        this.showMessage(response.error || "Connection test failed", "error");
      }
    } catch (error) {
      this.setConnectionStatus("error", "Connection failed");
      this.showMessage("Connection test failed: " + error.message, "error");
    }
  }

  async checkConnectionStatus() {
    const url = document.getElementById("mediafusion-url").value.trim();
    if (!url) {
      this.setConnectionStatus("error", "Not configured");
      return;
    }

    try {
      const response = await this.sendMessage({ action: "testConnection" });
      if (response.success) {
        this.setConnectionStatus("connected", "Connected");
      } else {
        this.setConnectionStatus("error", "Connection failed");
      }
    } catch (error) {
      this.setConnectionStatus("error", "Connection failed");
    }
  }

  setConnectionStatus(status, text) {
    const indicator = document.getElementById("connection-indicator");
    const textElement = document.getElementById("connection-text");

    indicator.className = `status-indicator ${status}`;
    textElement.textContent = text;
  }

  collectBasicData() {
    const magnetLink = document.getElementById("magnet-input").value.trim();
    const torrentFile = document.getElementById("torrent-file").files[0];

    if (!magnetLink && !torrentFile) {
      throw new Error("Please provide either a magnet link or torrent file");
    }

    const metaType = document.getElementById("content-type").value;
    const basicData = {
      metaType: metaType,
      uploaderName:
        document.getElementById("uploader-name").value.trim() || "Anonymous",
      magnetLink: magnetLink,
      torrentFile: torrentFile,
      // No languages in basic data - only in advanced
    };

    // Add sports category if content type is sports
    if (metaType === "sports") {
      const sportsCategory = document.getElementById("sports-category").value;
      if (!sportsCategory) {
        throw new Error("Please select a sports category");
      }
      basicData.sportsCategory = sportsCategory;
    }

    return basicData;
  }

  collectAdvancedData() {
    const basicData = this.collectBasicData();

    const advancedData = {
      ...basicData,
      languages:
        this.selectedLanguages.length > 0 ? this.selectedLanguages : null,
      torrentType: document.getElementById("torrent-type").value,
      title: document.getElementById("title").value.trim(),
      resolution: document.getElementById("resolution").value,
      quality: document.getElementById("quality").value,
      codec: document.getElementById("codec").value.trim(),
      audio: document.getElementById("audio").value.trim(),
    };

    // Add metaId if provided
    const imdbId = document.getElementById("imdb-id").value.trim();
    if (imdbId) {
      advancedData.metaId = imdbId;
    }

    // Add poster URL if provided
    const posterUrl = document.getElementById("poster-url").value.trim();
    if (posterUrl) {
      advancedData.posterUrl = posterUrl;
    }

    // Series-specific: Episode name parser
    if (advancedData.metaType === "series") {
      const episodeNameParser = document
        .getElementById("episode-name-parser")
        ?.value.trim();
      if (episodeNameParser) {
        advancedData.episode_name_parser = episodeNameParser;
        console.log("Series episode parser set:", episodeNameParser);
      }
    }

    // Sports-specific: Episode name parser (for racing sports)
    if (advancedData.metaType === "sports") {
      const sportsEpisodeParser = document
        .getElementById("sports-episode-parser")
        ?.value.trim();
      if (sportsEpisodeParser) {
        advancedData.episode_name_parser = sportsEpisodeParser;
        console.log("Sports episode parser set:", sportsEpisodeParser);
      }
    }

    // Collect catalogs
    const contentType = advancedData.metaType;
    if (contentType === "sports") {
      const sportsCatalog = document.getElementById("sports-catalog")?.value;
      if (sportsCatalog) {
        advancedData.catalogs = [sportsCatalog];
      }
    } else {
      const catalogCheckboxes = document.querySelectorAll(
        'input[name="catalogs"]:checked'
      );
      advancedData.catalogs = Array.from(catalogCheckboxes).map(
        (cb) => cb.value
      );
    }

    return advancedData;
  }

  async analyzeTorrent() {
    try {
      const basicData = this.collectBasicData();
      this.showLoading(true);

      const torrentData = {
        metaType: basicData.metaType,
      };

      if (basicData.magnetLink) {
        torrentData.magnetLink = basicData.magnetLink;
      } else if (basicData.torrentFile) {
        // Convert File to Blob for proper transmission to background script
        const fileBuffer = await basicData.torrentFile.arrayBuffer();
        torrentData.torrentFileData = {
          data: Array.from(new Uint8Array(fileBuffer)),
          name: basicData.torrentFile.name,
          type: basicData.torrentFile.type,
        };
      }

      const response = await this.sendMessage({
        action: "analyzeTorrent",
        data: torrentData,
      });

      if (response.success) {
        this.currentTorrentData = response.data;
        this.displayAnalysisResults(response.data);
        this.showAdvancedOptions();
        this.populateAdvancedFields(response.data);
        this.showMessage("Analysis completed successfully", "success");
      } else {
        this.showMessage(
          "Analysis failed: " + (response.error || "Unknown error"),
          "error"
        );
      }
    } catch (error) {
      this.showMessage("Analysis failed: " + error.message, "error");
    } finally {
      this.showLoading(false);
    }
  }

  populateAdvancedFields(data) {
    const torrent = data.torrent_data;

    // Populate fields with analyzed data
    if (torrent.title && !document.getElementById("title").value) {
      document.getElementById("title").value = torrent.title;
    }

    // Handle resolution with dynamic addition
    if (torrent.resolution && !document.getElementById("resolution").value) {
      this.setSelectValueOrAdd(
        "resolution",
        torrent.resolution,
        torrent.resolution
      );
    }

    // Handle quality with dynamic addition
    if (torrent.quality && !document.getElementById("quality").value) {
      this.setSelectValueOrAdd("quality", torrent.quality, torrent.quality);
    }

    // Handle codec with dynamic addition
    if (torrent.codec && !document.getElementById("codec").value) {
      this.setSelectValueOrAdd("codec", torrent.codec, torrent.codec);
    }

    // Handle audio with dynamic addition
    if (torrent.audio && !document.getElementById("audio").value) {
      const audioValue = Array.isArray(torrent.audio)
        ? torrent.audio[0]
        : torrent.audio;
      this.setSelectValueOrAdd("audio", audioValue, audioValue);
    }

    // Handle languages - populate from analyzed data
    if (torrent.languages && Array.isArray(torrent.languages) && torrent.languages.length > 0) {
      // Only populate if no languages are currently selected
      if (this.selectedLanguages.length === 0) {
        this.selectedLanguages = [...torrent.languages];
        this.updateLanguageDisplay();
      }
    }
  }

  // Helper method to set select value or add new option if not exists
  setSelectValueOrAdd(selectId, value, displayText) {
    const select = document.getElementById(selectId);
    const normalizedValue = value.toLowerCase();

    // First try to find exact match
    for (let option of select.options) {
      if (option.value.toLowerCase() === normalizedValue) {
        select.value = option.value;
        return;
      }
    }

    // Then try partial match
    for (let option of select.options) {
      if (
        option.text.toLowerCase().includes(normalizedValue) ||
        normalizedValue.includes(option.value.toLowerCase())
      ) {
        select.value = option.value;
        return;
      }
    }

    // If no match found, add new option dynamically
    const newOption = document.createElement("option");
    newOption.value = value;
    newOption.textContent = `${displayText} (detected)`;
    newOption.style.fontStyle = "italic";
    newOption.style.color = "#007bff"; // Blue color to indicate it's detected

    // Insert before the last option (usually empty "Auto-detect")
    if (select.options.length > 1) {
      select.insertBefore(newOption, select.options[1]);
    } else {
      select.appendChild(newOption);
    }

    // Select the newly added option
    select.value = value;

    // Show a message to user
    this.showMessage(
      `Detected ${selectId}: "${displayText}" - added to options`,
      "info"
    );

    // If quality was updated, refresh movie catalogs
    if (
      selectId === "quality" &&
      document.getElementById("content-type").value === "movie"
    ) {
      this.populateCatalogOptions();
    }
  }

  displayAnalysisResults(data) {
    const resultsContainer = document.getElementById("analysis-results");
    const torrentInfo = document.getElementById("torrent-info");
    const matchesContainer = document.getElementById("matches-container");

    // Clear previous results first safely
    while (torrentInfo.firstChild) {
      torrentInfo.removeChild(torrentInfo.firstChild);
    }
    while (matchesContainer.firstChild) {
      matchesContainer.removeChild(matchesContainer.firstChild);
    }

    // Display torrent info safely
    const torrent = data.torrent_data;
    const torrentDetails = document.createElement("div");
    torrentDetails.className = "torrent-details";

    const title = document.createElement("strong");
    title.textContent = `📁 ${torrent.title || "Unknown Title"}`;
    torrentDetails.appendChild(title);
    torrentDetails.appendChild(document.createElement("br"));

    const size = document.createElement("span");
    size.textContent = `📊 Size: ${this.formatBytes(torrent.total_size || 0)}`;
    torrentDetails.appendChild(size);
    torrentDetails.appendChild(document.createElement("br"));

    const quality = document.createElement("span");
    quality.textContent = `🎬 Quality: ${torrent.resolution || "Unknown"} ${
      torrent.quality || ""
    }`;
    torrentDetails.appendChild(quality);
    torrentDetails.appendChild(document.createElement("br"));

    const files = document.createElement("span");
    files.textContent = `📂 Files: ${
      torrent.file_data ? torrent.file_data.length : "Unknown"
    }`;
    torrentDetails.appendChild(files);

    torrentInfo.appendChild(torrentDetails);

    // Display matches
    if (data.matches && data.matches.length > 0) {
      const matchesHeader = document.createElement("h4");
      matchesHeader.textContent = "🎯 Found Matches:";
      matchesContainer.appendChild(matchesHeader);

      data.matches.forEach((match, index) => {
        const matchElement = this.createMatchElement(match, index);
        matchesContainer.appendChild(matchElement);
      });
      document
        .getElementById("upload-with-match-btn")
        .classList.remove("hidden");
    } else {
      const noMatchesDiv = document.createElement("div");
      noMatchesDiv.className = "no-matches";
      noMatchesDiv.textContent =
        "❌ No matches found. You can still upload manually.";
      matchesContainer.appendChild(noMatchesDiv);
      document.getElementById("upload-with-match-btn").classList.add("hidden");
    }

    resultsContainer.classList.remove("hidden");
  }

  createMatchElement(match, index) {
    const div = document.createElement("div");
    div.className = "match-item";
    div.dataset.index = index;

    // Create match content safely
    const matchTitle = document.createElement("div");
    matchTitle.className = "match-title";
    matchTitle.textContent = `🎬 ${match.title} (${match.year})`;
    div.appendChild(matchTitle);

    const matchDetails = document.createElement("div");
    matchDetails.className = "match-details";
    matchDetails.textContent = match.description || "No description available";
    div.appendChild(matchDetails);

    const matchMeta = document.createElement("div");
    matchMeta.className = "match-meta";

    const matchType = document.createElement("span");
    matchType.className = "match-type";
    matchType.textContent = match.type;
    matchMeta.appendChild(matchType);

    const matchRating = document.createElement("span");
    matchRating.className = "match-rating";
    matchRating.textContent = `⭐ ${match.imdb_rating || "N/A"}`;
    matchMeta.appendChild(matchRating);

    const matchId = document.createElement("span");
    matchId.className = "match-id";
    matchId.textContent = match.imdb_id;
    matchMeta.appendChild(matchId);

    div.appendChild(matchMeta);

    div.addEventListener("click", () => {
      document.querySelectorAll(".match-item").forEach((item) => {
        item.classList.remove("selected");
      });
      div.classList.add("selected");
      this.selectedMatch = match;

      // Populate IMDb ID when match is selected
      document.getElementById("imdb-id").value = match.imdb_id;
      if (!document.getElementById("title").value) {
        document.getElementById("title").value = match.title;
      }
    });

    return div;
  }

  async uploadWithMatch() {
    if (!this.selectedMatch || !this.currentTorrentData) {
      this.showMessage("Please select a match first", "error");
      return;
    }

    this.showLoading(true);

    try {
      const formData = this.collectAdvancedData();

      const torrentData = {
        ...formData,
        metaType: this.selectedMatch.type,
        metaId: this.selectedMatch.imdb_id,
        title: this.selectedMatch.title,
        poster: this.selectedMatch.poster,
        background: this.selectedMatch.background,
        logo: this.selectedMatch.logo,
      };

      // Debug: Log torrent data being sent for match upload
      console.log("Torrent data for match upload:", torrentData);

      // Convert File object for transmission
      await this.convertTorrentFileForTransmission(torrentData);

      const response = await this.sendMessage({
        action: "uploadTorrent",
        data: torrentData,
      });

      if (response.success) {
        await this.handleUploadResponse(response.data, torrentData);
      } else {
        this.showMessage(
          "Upload failed: " + (response.error || "Unknown error"),
          "error"
        );
      }
    } catch (error) {
      this.showMessage("Upload failed: " + error.message, "error");
    } finally {
      this.showLoading(false);
    }
  }

  async uploadManual() {
    if (!this.currentTorrentData) {
      this.showMessage("Please analyze the torrent first", "error");
      return;
    }

    this.showLoading(true);

    try {
      const torrentData = this.collectAdvancedData();

      // Debug: Log torrent data being sent for manual upload
      console.log("Torrent data for manual upload:", torrentData);

      // Convert File object for transmission
      await this.convertTorrentFileForTransmission(torrentData);

      const response = await this.sendMessage({
        action: "uploadTorrent",
        data: torrentData,
      });

      if (response.success) {
        await this.handleUploadResponse(response.data, torrentData);
      } else {
        this.showMessage(
          "Upload failed: " + (response.error || "Unknown error"),
          "error"
        );
      }
    } catch (error) {
      this.showMessage("Upload failed: " + error.message, "error");
    } finally {
      this.showLoading(false);
    }
  }

  async handleUploadResponse(data, originalTorrentData) {
    if (data.status === "needs_annotation") {
      // Show file annotation modal
      this.showFileAnnotationModal(data.files, originalTorrentData);
    } else if (data.status === "validation_failed") {
      // Show validation errors and ask if user wants to force import
      this.showValidationFailedDialog(data, originalTorrentData);
    } else if (data.status === "warning") {
      // Show warning message but treat as success
      this.showMessage(
        data.message || "Upload completed with warnings",
        "warning"
      );

      // Update bulk status if we're in advanced mode from bulk upload
      if (this.isAdvancedFromBulk && this.currentBulkTorrentIndex !== null) {
        this.updateTorrentStatus(this.currentBulkTorrentIndex, 'warning', data.message || "Uploaded with warnings");
      }

      this.clearForm();
      this.showBasicUpload();
    } else if (data.status === "error") {
      // Show error message
      this.showMessage(
        data.message || "Upload failed with unknown error",
        "error"
      );

      // Update bulk status if we're in advanced mode from bulk upload
      if (this.isAdvancedFromBulk && this.currentBulkTorrentIndex !== null) {
        this.updateTorrentStatus(this.currentBulkTorrentIndex, 'error', data.message || "Upload failed with error");
      }
    } else if (data.status === "success") {
      // Success
      const message = data.message || "Upload completed successfully!";
      this.showMessage(message, "success");

      // Update bulk status if we're in advanced mode from bulk upload
      if (this.isAdvancedFromBulk && this.currentBulkTorrentIndex !== null) {
        this.updateTorrentStatus(this.currentBulkTorrentIndex, 'success', data.message || "Uploaded successfully");
      }

      this.clearForm();
      this.showBasicUpload();
    } else {
      // Unknown status
      console.warn("Unknown response status:", data.status);
      this.showMessage(
        data.message || "Upload completed with unknown status: " + data.status,
        "warning"
      );

      // Update bulk status if we're in advanced mode from bulk upload
      if (this.isAdvancedFromBulk && this.currentBulkTorrentIndex !== null) {
        this.updateTorrentStatus(this.currentBulkTorrentIndex, 'warning', data.message || `Unknown status: ${data.status}`);
      }

      this.clearForm();
      this.showBasicUpload();
    }
  }

  clearForm() {
    document.getElementById("magnet-input").value = "";
    document.getElementById("torrent-file").value = "";
    document.getElementById("imdb-id").value = "";
    document.getElementById("title").value = "";
    document.getElementById("resolution").value = "";
    document.getElementById("quality").value = "";
    document.getElementById("codec").value = "";
    document.getElementById("audio").value = "";
    document.getElementById("episode-name-parser").value = "";
    document.getElementById("sports-episode-parser").value = "";

    // Reset language selection (only advanced)
    this.selectedLanguages = [];
    document.getElementById("advanced-selected-languages").textContent =
      "Auto-detect";

    document
      .querySelectorAll('input[name="catalogs"]')
      .forEach((cb) => (cb.checked = false));

    this.selectedMatch = null;
    this.currentTorrentData = null;
  }

  showLoading(show) {
    const overlay = document.getElementById("loading-overlay");
    if (show) {
      overlay.classList.remove("hidden");
    } else {
      overlay.classList.add("hidden");
    }
  }

  showMessage(message, type = "info") {
    const container = document.getElementById("status-messages");
    const messageDiv = document.createElement("div");
    messageDiv.className = `status-message ${type}`;
    messageDiv.textContent = message;

    container.appendChild(messageDiv);

    setTimeout(() => {
      if (messageDiv.parentNode) {
        messageDiv.parentNode.removeChild(messageDiv);
      }
    }, 8000);

    messageDiv.scrollIntoView({ behavior: "smooth" });
  }

  formatBytes(bytes) {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  }

  showFileAnnotationModal(files, originalTorrentData) {
    this.annotationFiles = files;
    this.originalTorrentData = originalTorrentData;

    const modal = document.getElementById("file-annotation-modal");
    const fileList = document.getElementById("file-annotation-list");

    // Clear previous content safely
    while (fileList.firstChild) {
      fileList.removeChild(fileList.firstChild);
    }

    const isSportsContent = originalTorrentData.metaType === "sports";

    // Sort files by filename
    files.sort((a, b) => {
      return a.filename.localeCompare(b.filename, undefined, {
        numeric: true,
        sensitivity: "base",
      });
    });

    files.forEach((file, index) => {
      const fileItem = document.createElement("div");
      fileItem.className = "file-item";
      fileItem.id = `file-row-${index}`;
      // Create file item content safely
      const fileHeader = document.createElement("div");
      fileHeader.className = "file-header";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "file-checkbox";
      checkbox.id = `include-file-${index}`;
      checkbox.checked = true;
      fileHeader.appendChild(checkbox);

      const fileName = document.createElement("div");
      fileName.className = "file-name";
      fileName.title = file.filename;
      fileName.textContent = file.filename;
      fileHeader.appendChild(fileName);

      fileItem.appendChild(fileHeader);

      // File inputs section
      const fileInputs = document.createElement("div");
      fileInputs.className = `file-inputs ${isSportsContent ? "sports" : ""}`;

      // Season input group
      const seasonGroup = document.createElement("div");
      seasonGroup.className = "input-group";

      const seasonLabel = document.createElement("label");
      seasonLabel.setAttribute("for", `season-${index}`);
      seasonLabel.textContent = "Season";
      seasonGroup.appendChild(seasonLabel);

      const seasonBtn = document.createElement("button");
      seasonBtn.type = "button";
      seasonBtn.className = "numbering-btn season-btn";
      seasonBtn.setAttribute("data-index", index.toString());

      const seasonIcon = document.createElement("span");
      seasonIcon.className = "icon";
      seasonIcon.textContent = "▶";
      seasonBtn.appendChild(seasonIcon);
      seasonBtn.appendChild(
        document.createTextNode(" Apply same season to all following files")
      );
      seasonGroup.appendChild(seasonBtn);

      const seasonInput = document.createElement("input");
      seasonInput.type = "number";
      seasonInput.id = `season-${index}`;
      seasonInput.value = file.season_number || "";
      seasonInput.min = "1";
      seasonGroup.appendChild(seasonInput);

      fileInputs.appendChild(seasonGroup);

      // Episode input group
      const episodeGroup = document.createElement("div");
      episodeGroup.className = "input-group";

      const episodeLabel = document.createElement("label");
      episodeLabel.setAttribute("for", `episode-${index}`);
      episodeLabel.textContent = "Episode";
      episodeGroup.appendChild(episodeLabel);

      const episodeBtn = document.createElement("button");
      episodeBtn.type = "button";
      episodeBtn.className = "numbering-btn episode-btn";
      episodeBtn.setAttribute("data-index", index.toString());

      const episodeIcon = document.createElement("span");
      episodeIcon.className = "icon";
      episodeIcon.textContent = "▶";
      episodeBtn.appendChild(episodeIcon);
      episodeBtn.appendChild(
        document.createTextNode(" Apply consecutive episode numbering")
      );
      episodeGroup.appendChild(episodeBtn);

      const episodeInput = document.createElement("input");
      episodeInput.type = "number";
      episodeInput.id = `episode-${index}`;
      episodeInput.value = file.episode_number || "";
      episodeInput.min = "1";
      episodeGroup.appendChild(episodeInput);

      fileInputs.appendChild(episodeGroup);
      fileItem.appendChild(fileInputs);

      // Sports metadata section (if applicable)
      if (isSportsContent) {
        const sportsMetadata = document.createElement("div");
        sportsMetadata.className = "sports-metadata";

        // Episode Title
        const titleGroup = document.createElement("div");
        titleGroup.className = "input-group";

        const titleLabel = document.createElement("label");
        titleLabel.setAttribute("for", `title-${index}`);
        titleLabel.textContent = "Episode Title";
        titleGroup.appendChild(titleLabel);

        const titleInput = document.createElement("input");
        titleInput.type = "text";
        titleInput.id = `title-${index}`;
        titleInput.value = file.episode_title || "";
        titleInput.placeholder = "Optional";
        titleGroup.appendChild(titleInput);

        sportsMetadata.appendChild(titleGroup);

        // Overview
        const overviewGroup = document.createElement("div");
        overviewGroup.className = "input-group";

        const overviewLabel = document.createElement("label");
        overviewLabel.setAttribute("for", `overview-${index}`);
        overviewLabel.textContent = "Overview";
        overviewGroup.appendChild(overviewLabel);

        const overviewInput = document.createElement("textarea");
        overviewInput.id = `overview-${index}`;
        overviewInput.placeholder = "Optional";
        overviewGroup.appendChild(overviewInput);

        sportsMetadata.appendChild(overviewGroup);

        // Thumbnail URL
        const thumbnailGroup = document.createElement("div");
        thumbnailGroup.className = "input-group";

        const thumbnailLabel = document.createElement("label");
        thumbnailLabel.setAttribute("for", `thumbnail-${index}`);
        thumbnailLabel.textContent = "Thumbnail URL";
        thumbnailGroup.appendChild(thumbnailLabel);

        const thumbnailInput = document.createElement("input");
        thumbnailInput.type = "url";
        thumbnailInput.id = `thumbnail-${index}`;
        thumbnailInput.placeholder = "Optional";
        thumbnailGroup.appendChild(thumbnailInput);

        sportsMetadata.appendChild(thumbnailGroup);

        // Release Date
        const releaseGroup = document.createElement("div");
        releaseGroup.className = "input-group";

        const releaseLabel = document.createElement("label");
        releaseLabel.setAttribute("for", `release-${index}`);
        releaseLabel.textContent = "Release Date";
        releaseGroup.appendChild(releaseLabel);

        const releaseInput = document.createElement("input");
        releaseInput.type = "date";
        releaseInput.id = `release-${index}`;
        releaseInput.value = file.release_date || "";
        releaseGroup.appendChild(releaseInput);

        sportsMetadata.appendChild(releaseGroup);

        fileItem.appendChild(sportsMetadata);
      }
      fileList.appendChild(fileItem);
    });

    modal.classList.remove("hidden");

    // Add event listeners for numbering buttons
    this.setupNumberingButtonListeners();
  }

  setupNumberingButtonListeners() {
    // Season numbering buttons
    document.querySelectorAll(".season-btn").forEach((button) => {
      button.addEventListener("click", (e) => {
        const index = parseInt(e.target.closest(".season-btn").dataset.index);
        this.applySeasonNumberingFrom(index);
      });
    });

    // Episode numbering buttons
    document.querySelectorAll(".episode-btn").forEach((button) => {
      button.addEventListener("click", (e) => {
        const index = parseInt(e.target.closest(".episode-btn").dataset.index);
        this.applyEpisodeNumberingFrom(index);
      });
    });
  }

  applySeasonNumberingFrom(startIndex) {
    let seasonValue = document.getElementById(`season-${startIndex}`).value;
    if (!seasonValue) {
      // Default to 1 if empty
      seasonValue = "1";
      document.getElementById(`season-${startIndex}`).value = seasonValue;
    }

    // Get all file items starting from the specified index
    const allFiles = document.querySelectorAll(".file-item");
    const relevantFiles = Array.from(allFiles).slice(startIndex);

    // Apply the same season number to all following files
    relevantFiles.forEach((fileRow, idx) => {
      const actualIndex = startIndex + idx;
      const seasonInput = document.getElementById(`season-${actualIndex}`);
      const includeCheckbox = document.getElementById(
        `include-file-${actualIndex}`
      );

      // Only apply to included files
      if (includeCheckbox && includeCheckbox.checked && seasonInput) {
        seasonInput.value = seasonValue;

        // Add visual highlight
        fileRow.classList.add("numbered-file");
        setTimeout(() => {
          fileRow.classList.remove("numbered-file");
        }, 1500);
      }
    });

    this.showMessage(
      `Applied season ${seasonValue} to ${relevantFiles.length} files`,
      "success"
    );
  }

  applyEpisodeNumberingFrom(startIndex) {
    const startEpisodeNumber =
      parseInt(document.getElementById(`episode-${startIndex}`).value) || 1;
    const resetOnSeasonChange = true;

    // Get all file items starting from the specified index
    const allFiles = document.querySelectorAll(".file-item");
    const relevantFiles = Array.from(allFiles).slice(startIndex);

    let episodeCounter = startEpisodeNumber;
    let lastSeason = null;

    // Apply consecutive episode numbers
    relevantFiles.forEach((fileRow, idx) => {
      const actualIndex = startIndex + idx;
      const episodeInput = document.getElementById(`episode-${actualIndex}`);
      const seasonInput = document.getElementById(`season-${actualIndex}`);
      const includeCheckbox = document.getElementById(
        `include-file-${actualIndex}`
      );

      // Only apply to included files
      if (includeCheckbox && includeCheckbox.checked && episodeInput) {
        const currentSeason = parseInt(seasonInput.value) || null;

        // Reset episode counter if season changes and reset option is enabled
        if (
          resetOnSeasonChange &&
          lastSeason !== null &&
          currentSeason !== null &&
          currentSeason !== lastSeason
        ) {
          episodeCounter = 1;
        }

        // Set the episode number
        episodeInput.value = episodeCounter++;

        // Store the current season for next iteration
        if (currentSeason !== null) {
          lastSeason = currentSeason;
        }

        // Add visual highlight
        fileRow.classList.add("numbered-file");
        setTimeout(() => {
          fileRow.classList.remove("numbered-file");
        }, 1500);
      }
    });

    this.showMessage(
      `Applied consecutive episode numbering starting from ${startEpisodeNumber}`,
      "success"
    );
  }

  hideFileAnnotationModal() {
    const modal = document.getElementById("file-annotation-modal");
    modal.classList.add("hidden");
    this.annotationFiles = null;
    this.originalTorrentData = null;
  }

  async handleAnnotationConfirm() {
    if (!this.annotationFiles || !this.originalTorrentData) {
      this.showMessage("Missing annotation data. Please try again.", "error");
      return;
    }

    const annotatedFiles = [];
    const isSportsContent = this.originalTorrentData.metaType === "sports";

    this.annotationFiles.forEach((file, index) => {
      // Only include files that are checked
      if (document.getElementById(`include-file-${index}`)?.checked) {
        const baseData = {
          ...file,
          season_number:
            parseInt(document.getElementById(`season-${index}`).value) || null,
          episode_number:
            parseInt(document.getElementById(`episode-${index}`).value) || null,
        };

        if (isSportsContent) {
          const releaseDate = document.getElementById(`release-${index}`).value;
          if (releaseDate) {
            baseData.release_date = releaseDate;
          }
          annotatedFiles.push({
            ...baseData,
            title: document.getElementById(`title-${index}`).value || null,
            overview:
              document.getElementById(`overview-${index}`).value || null,
            thumbnail:
              document.getElementById(`thumbnail-${index}`).value || null,
          });
        } else {
          annotatedFiles.push(baseData);
        }
      }
    });

    // Save original data before hiding modal (since hideFileAnnotationModal clears it)
    const originalData = this.originalTorrentData;

    // Hide modal
    this.hideFileAnnotationModal();

    // Show loading
    this.showLoading(true);

    try {
      // Upload with annotated files - explicitly copy all properties to avoid spread issues with File objects
      const torrentData = {
        // Basic data
        metaType: originalData.metaType,
        uploaderName: originalData.uploaderName,
        magnetLink: originalData.magnetLink,
        torrentFile: originalData.torrentFile,

        // Torrent metadata
        torrentType: originalData.torrentType,
        title: originalData.title,
        resolution: originalData.resolution,
        quality: originalData.quality,
        codec: originalData.codec,
        audio: originalData.audio,

        // Content metadata (if available)
        metaId: originalData.metaId,
        poster: originalData.poster,
        background: originalData.background,
        logo: originalData.logo,

        // Episode parser (if available)
        episode_name_parser: originalData.episode_name_parser,

        // Catalogs (if available)
        catalogs: originalData.catalogs,

        // New data for annotation
        fileData: annotatedFiles,
        forceImport: true,
      };

      // Debug: Log the final torrent data being sent
      console.log(
        "Final torrent data being sent after annotation:",
        torrentData
      );

      // Convert File object for transmission
      await this.convertTorrentFileForTransmission(torrentData);

      const response = await this.sendMessage({
        action: "uploadTorrent",
        data: torrentData,
      });

      if (response.success) {
        await this.handleUploadResponse(response.data, torrentData);
      } else {
        this.showMessage(
          "Upload failed: " + (response.error || "Unknown error"),
          "error"
        );
      }
    } catch (error) {
      this.showMessage("Upload failed: " + error.message, "error");
    } finally {
      this.showLoading(false);
    }
  }

  showValidationFailedDialog(data, originalTorrentData) {
    if (!data.errors || !Array.isArray(data.errors)) {
      this.showMessage("Unknown validation error occurred", "error");
      return;
    }

    // Check the types of validation errors
    const errorTypes = data.errors.map((error) => error.type);
    const hasTitleMismatch = errorTypes.includes("title_mismatch");
    const hasEpisodeIssues =
      errorTypes.includes("episodes_not_found") ||
      errorTypes.includes("seasons_not_found");

    if (hasEpisodeIssues) {
      // Show episode annotation dialog for missing episode/season data
      this.showEpisodeAnnotationDialog(data, originalTorrentData);
    } else if (hasTitleMismatch) {
      // Show title mismatch dialog with force import option
      this.showTitleMismatchDialog(data, originalTorrentData);
    } else {
      // Show generic validation error
      this.showGenericValidationError(data);
    }
  }

  showTitleMismatchDialog(data, originalTorrentData) {
    // Create custom dialog for title mismatch
    const dialog = document.createElement("div");
    dialog.className = "validation-dialog-overlay";
    dialog.innerHTML = `
            <div class="validation-dialog">
                <div class="validation-dialog-header">
                    <h3>Title Mismatch Detected</h3>
                </div>
                <div class="validation-dialog-body">
                    <div class="validation-error-details">
                        ${data.errors
                          .map((error) => {
                            if (error.type === "title_mismatch") {
                              return `<p class="error-message">${error.message}</p>`;
                            }
                            return "";
                          })
                          .join("")}
                    </div>
                    <p class="validation-explanation">
                        The torrent title doesn't match the expected content. This might happen if:
                    </p>
                    <ul class="validation-reasons">
                        <li>The torrent contains a different movie/series than expected</li>
                        <li>The title format is unusual or contains extra information</li>
                        <li>There's a typo in the torrent name</li>
                    </ul>
                    <p class="validation-question">
                        What would you like to do?
                    </p>
                    <div class="validation-options">
                        <div class="validation-option">
                            <strong>Recommended:</strong> Re-analyze the torrent and manually select the correct movie/series metadata.
                        </div>
                        <div class="validation-option">
                            <strong>Alternative:</strong> Force import with the current metadata (may result in incorrect matching).
                        </div>
                    </div>
                </div>
                <div class="validation-dialog-footer">
                    <button class="btn btn-secondary cancel-validation">Cancel</button>
                    <button class="btn btn-primary reanalyze-torrent">Re-analyze & Select Metadata</button>
                    <button class="btn btn-warning force-import">Force Import Anyway</button>
                </div>
            </div>
        `;

    document.body.appendChild(dialog);

    // Add event listeners
    dialog.querySelector(".cancel-validation").addEventListener("click", () => {
      document.body.removeChild(dialog);
    });

    dialog.querySelector(".reanalyze-torrent").addEventListener("click", () => {
      document.body.removeChild(dialog);
      this.reanalyzeTorrentForCorrectMetadata(originalTorrentData);
    });

    dialog.querySelector(".force-import").addEventListener("click", () => {
      document.body.removeChild(dialog);
      this.forceImportTorrent(originalTorrentData);
    });

    // Close on overlay click
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) {
        document.body.removeChild(dialog);
      }
    });
  }

  showEpisodeAnnotationDialog(data, originalTorrentData) {
    // Use the existing file annotation modal with the file data from the validation response
    this.showFileAnnotationModal(
      data.torrent_data.file_data,
      originalTorrentData
    );

    // Update the modal header to reflect the validation issue
    const modal = document.getElementById("file-annotation-modal");
    const modalHeader = modal.querySelector(".modal-header h3");
    const errorTypes = data.errors.map((error) => error.type);

    if (errorTypes.includes("episodes_not_found")) {
      modalHeader.textContent = "Episode Information Required";
    } else if (errorTypes.includes("seasons_not_found")) {
      modalHeader.textContent = "Season Information Required";
    } else {
      modalHeader.textContent = "Episode Details Setup";
    }
  }

  showGenericValidationError(data) {
    const errorMessages = data.errors
      .map((error) => error.message || `${error.type} error`)
      .join("\n");
    this.showMessage(`Validation failed:\n${errorMessages}`, "error");
  }

  reanalyzeTorrentForCorrectMetadata(originalTorrentData) {
    // Reset the form to analysis mode and populate with the torrent data
    this.showMessage("Re-analyzing torrent for metadata selection...", "info");

    // Switch to upload tab if not already there
    const uploadTab = document.getElementById("upload-tab");
    const settingsTab = document.getElementById("settings-tab");
    const uploadTabBtn = document.querySelector('[data-tab="upload"]');
    const settingsTabBtn = document.querySelector('[data-tab="settings"]');

    uploadTab.classList.add("active");
    settingsTab.classList.remove("active");
    uploadTabBtn.classList.add("active");
    settingsTabBtn.classList.remove("active");

    // Clear any existing data and populate with the torrent info
    if (originalTorrentData.magnetLink) {
      document.getElementById("magnet-input").value =
        originalTorrentData.magnetLink;
    }

    if (originalTorrentData.contentType) {
      document.getElementById("content-type").value =
        originalTorrentData.contentType;
    }

    // Preserve poster URL if it was provided
    if (originalTorrentData.posterUrl) {
      document.getElementById("poster-url").value =
        originalTorrentData.posterUrl;
    }

    // Clear file input since we're working with magnet link
    document.getElementById("torrent-file").value = "";

    // Hide advanced options and show basic upload
    document.getElementById("basic-upload").classList.remove("hidden");
    document.getElementById("analysis-results").classList.add("hidden");
    document.getElementById("advanced-options").classList.add("hidden");

    // Automatically trigger analysis
    setTimeout(() => {
      const analyzeBtn = document.getElementById("analyze-btn");
      if (analyzeBtn && !analyzeBtn.disabled) {
        analyzeBtn.click();
      }
    }, 500);

    this.showMessage(
      "Please select the correct metadata from the analysis results below.",
      "info"
    );
  }

  async forceImportTorrent(originalTorrentData) {
    // Show loading for retry
    this.showLoading(true);
    try {
      // Retry with force import
      const forceTorrentData = { ...originalTorrentData, forceImport: true };

      // Convert File object for transmission
      await this.convertTorrentFileForTransmission(forceTorrentData);

      const retryResponse = await this.sendMessage({
        action: "uploadTorrent",
        data: forceTorrentData,
      });
      if (retryResponse.success) {
        await this.handleUploadResponse(retryResponse.data, forceTorrentData);
      } else {
        this.showMessage(
          "Upload failed: " + (retryResponse.error || "Unknown error"),
          "error"
        );
      }
    } catch (error) {
      this.showMessage("Upload failed: " + error.message, "error");
    } finally {
      this.showLoading(false);
    }
  }

  async convertTorrentFileForTransmission(torrentData) {
    // Convert File object to transmittable format if present
    if (torrentData.torrentFile && torrentData.torrentFile instanceof File) {
      const fileBuffer = await torrentData.torrentFile.arrayBuffer();
      torrentData.torrentFileData = {
        data: Array.from(new Uint8Array(fileBuffer)),
        name: torrentData.torrentFile.name,
        type: torrentData.torrentFile.type,
      };
      // Keep the original File object for potential reuse (like annotation)
      // but mark it as converted so we know it has torrentFileData
      torrentData._hasFileData = true;
    }
    return torrentData;
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

  // Bulk Upload Response Handler
  async handleBulkUploadResponse(data, torrent) {
    if (data.status === "success") {
      return {
        success: true,
        message: data.message || "Uploaded successfully",
        analyzedData: data.analyzed_data || null
      };
    } else if (data.status === "warning") {
      return {
        success: true,
        message: data.message || "Uploaded with warnings",
        analyzedData: data.analyzed_data || null
      };
    } else if (data.status === "needs_annotation") {
      return {
        success: false,
        message: "Needs file annotation - skipped in bulk upload",
        analyzedData: data.analyzed_data || null
      };
    } else if (data.status === "validation_failed") {
      // Extract error messages for bulk display
      const errorMessages = data.errors
        ? data.errors.map(error => error.message || error.type).join(", ")
        : "Validation failed";
      return {
        success: false,
        message: `Validation failed: ${errorMessages}`,
        analyzedData: data.analyzed_data || null
      };
    } else if (data.status === "error") {
      return {
        success: false,
        message: data.message || "Upload failed with error",
        analyzedData: data.analyzed_data || null
      };
    } else {
      // Unknown status
      console.warn("Unknown bulk upload response status:", data.status);
      return {
        success: false,
        message: `Unknown status: ${data.status}`,
        analyzedData: data.analyzed_data || null
      };
    }
  }

  // Bulk Upload Methods
  async initializeBulkUploadMode() {
    try {
      // Load bulk upload data from storage
      const bulkData = await this.loadBulkUploadData();
      if (!bulkData) {
        this.showMessage("Bulk upload data not found", "error");
        return;
      }

      // Switch to bulk upload interface
      this.showBulkUploadInterface(bulkData);
    } catch (error) {
      console.error("Error initializing bulk upload mode:", error);
      this.showMessage("Failed to initialize bulk upload mode", "error");
    }
  }

  async loadBulkUploadData() {
    return new Promise((resolve) => {
      // Try session storage first, then local storage
      const storageAPI = (typeof chrome !== "undefined" && chrome.storage.session)
        ? chrome.storage.session
        : (typeof browser !== "undefined" && browser.storage.session)
        ? browser.storage.session
        : (typeof chrome !== "undefined" && chrome.storage.local)
        ? chrome.storage.local
        : browser.storage.local;

      storageAPI.get(['bulkUploadData'], (result) => {
        if (chrome.runtime.lastError) {
          console.error("Storage error:", chrome.runtime.lastError);
          resolve(null);
        } else {
          resolve(result.bulkUploadData);
        }
      });
    });
  }

  showBulkUploadInterface(bulkData) {
    // Store bulk data for filter operations
    this.bulkData = bulkData;

    // Hide normal interface
    const container = document.querySelector('.container');
    if (container) {
      container.style.display = 'none';
    }

    // Create bulk upload interface
    const bulkContainer = document.createElement('div');
    bulkContainer.className = 'bulk-upload-container';
    bulkContainer.innerHTML = `
      <div class="bulk-header">
        <h2>Bulk Upload to MediaFusion</h2>
        <p>Found ${bulkData.torrents.length} torrents from: <strong>${bulkData.pageTitle}</strong></p>
      </div>

      <div class="bulk-controls">
        <!-- Filter Controls -->
        <div class="bulk-filters">
          <div class="filter-section">
            <label class="filter-label">Filter by Type:</label>
            <div class="filter-buttons">
              <button id="filter-all-types" class="filter-btn active" data-filter="all">All (${bulkData.torrents.length})</button>
              <button id="filter-torrents" class="filter-btn" data-filter="torrent">Select Torrents (${this.countByType(bulkData.torrents, 'torrent')})</button>
              <button id="filter-magnets" class="filter-btn" data-filter="magnet">Select Magnets (${this.countByType(bulkData.torrents, 'magnet')})</button>
            </div>
          </div>

          <div class="filter-section">
            <label class="filter-label">Filter by Content:</label>
            <div class="filter-buttons">
              <button id="filter-all-content" class="filter-btn active" data-content="all">All</button>
              <button id="filter-movies" class="filter-btn" data-content="movie">Select Movies (${this.countByContent(bulkData.torrents, 'movie')})</button>
              <button id="filter-series" class="filter-btn" data-content="series">Select Series (${this.countByContent(bulkData.torrents, 'series')})</button>
              <button id="filter-sports" class="filter-btn" data-content="sports">Select Sports (${this.countByContent(bulkData.torrents, 'sports')})</button>
            </div>
          </div>
        </div>

        <!-- Optional Metadata -->
        <div class="bulk-metadata">
          <div class="metadata-section">
            <label class="metadata-label">Optional Metadata (applies to all selected torrents):</label>
            <div class="metadata-fields">
              <div class="metadata-field">
                <label for="bulk-imdb-id">IMDb ID:</label>
                <input type="text" id="bulk-imdb-id" placeholder="tt1234567" title="Optional: Apply this IMDb ID to all selected torrents" pattern="tt\\d{7,8}">
                <small class="field-help">Leave empty for auto-detection. Format: tt1234567</small>
              </div>
            </div>
          </div>
        </div>

        <!-- Selection Actions -->
        <div class="bulk-actions">
          <button id="select-all-btn" class="btn secondary">Select All Visible</button>
          <button id="deselect-all-btn" class="btn secondary">Deselect All</button>
          <button id="start-bulk-upload-btn" class="btn primary" disabled>Upload Selected (0)</button>
        </div>

        <div class="bulk-progress" id="bulk-progress" style="display: none;">
          <div class="progress-header">
            <div class="progress-info">
              <div class="progress-bar">
                <div class="progress-fill" id="progress-fill"></div>
              </div>
              <div class="progress-text" id="progress-text">0 / 0 completed</div>
            </div>
            <div class="progress-controls">
              <button id="auto-scroll-toggle" class="btn btn-sm btn-outline active" title="Auto-scroll to current upload">
                <svg class="auto-scroll-icon" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M7.41,8.58L12,13.17L16.59,8.58L18,10L12,16L6,10L7.41,8.58Z" />
                </svg>
                <span class="auto-scroll-text">Auto-scroll</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="bulk-list" id="bulk-list">
        ${this.generateBulkTorrentList(bulkData.torrents)}
      </div>
    `;

    // Add bulk container to body
    document.body.appendChild(bulkContainer);

    // Setup event listeners with error handling
    try {
      this.setupBulkUploadEventListeners(bulkData);
    } catch (error) {
      console.error("Error setting up bulk upload event listeners:", error);
      this.showMessage("Failed to setup bulk upload interface", "error");
    }
  }

  generateBulkTorrentList(torrents) {
    return torrents.map((torrent, index) => {
      // Get stored status or default to 'ready'
      const status = torrent.uploadStatus || 'ready';
      const message = torrent.uploadMessage || 'Ready';

      // Generate appropriate actions based on status
      let actionsHtml = '';
      if (status === 'error' || status === 'warning') {
        actionsHtml = `
          <button class="result-action-btn retry-quick" data-index="${index}" title="Retry Upload">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z" />
            </svg>
          </button>
          <button class="result-action-btn advanced-upload" data-index="${index}" title="Manual Upload">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M9,16V10H5L12,3L19,10H15V16H9M5,20V18H19V20H5Z" />
            </svg>
          </button>
        `;
      } else if (status === 'success') {
        actionsHtml = '<span class="result-success-indicator">✓</span>';
      } else if (status === 'processing') {
        actionsHtml = '';
      } else {
        // Default ready state
        actionsHtml = `
          <button class="result-action-btn advanced-upload" data-index="${index}" title="Manual Upload">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M9,16V10H5L12,3L19,10H15V16H9M5,20V18H19V20H5Z" />
            </svg>
          </button>
        `;
      }

      return `
        <div class="bulk-item ${status}" data-index="${index}" data-original-index="${index}">
          <div class="bulk-item-number">
            <span class="item-number">${index + 1}</span>
          </div>
          <div class="bulk-item-checkbox">
            <input type="checkbox" id="torrent-${index}" checked>
          </div>
          <div class="bulk-item-content">
            <div class="bulk-item-header">
              <label for="torrent-${index}" class="bulk-item-title">${torrent.title}</label>
              <span class="bulk-item-type ${torrent.type}">${torrent.type.toUpperCase()}</span>
            </div>
            <div class="bulk-item-details">
              <span class="bulk-item-content-type">${torrent.contentType}</span>
              <span class="bulk-item-url">${this.truncateUrl(torrent.url)}</span>
            </div>
            <div class="bulk-item-status-row">
              <div class="bulk-item-status ${status}" id="status-${index}">
                <span class="status-text">${message}</span>
              </div>
              <div class="bulk-item-actions" id="actions-${index}">
                ${actionsHtml}
              </div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  truncateUrl(url) {
    if (url.length <= 50) return url;
    return url.substring(0, 47) + '...';
  }

  countByType(torrents, type) {
    return torrents.filter(torrent => torrent.type === type).length;
  }

  countByContent(torrents, contentType) {
    return torrents.filter(torrent => torrent.contentType === contentType).length;
  }

  setupBulkUploadEventListeners(bulkData) {
    // Store current filter state
    this.currentTypeFilter = 'all';
    this.currentContentFilter = 'all';

    // Initialize auto-scroll state
    this.autoScrollEnabled = true;
    this.userScrollTimeout = null;

    // Select/Deselect all buttons with error handling
    const selectAllBtn = document.getElementById('select-all-btn');
    const deselectAllBtn = document.getElementById('deselect-all-btn');
    const startUploadBtn = document.getElementById('start-bulk-upload-btn');

    if (selectAllBtn) {
      selectAllBtn.addEventListener('click', () => {
        this.toggleVisibleTorrents(true);
      });
    }

    if (deselectAllBtn) {
      deselectAllBtn.addEventListener('click', () => {
        this.toggleAllTorrents(false);
      });
    }


    // Filter buttons - Type filters
    document.querySelectorAll('[data-filter]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.handleTypeFilter(e.target.dataset.filter);
      });
    });

    // Filter buttons - Content filters
    document.querySelectorAll('[data-content]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.handleContentFilter(e.target.dataset.content);
      });
    });

    // Individual checkbox listeners
    document.querySelectorAll('.bulk-item input[type="checkbox"]').forEach(checkbox => {
      checkbox.addEventListener('change', () => {
        this.updateBulkUploadButton();
      });
    });

    // Start bulk upload button
    if (startUploadBtn) {
      startUploadBtn.addEventListener('click', () => {
        this.startBulkUpload(bulkData);
      });
    }

    // Action buttons for individual torrents
    document.querySelectorAll('.bulk-item-actions .advanced-upload').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const index = parseInt(btn.dataset.index);
        this.handleAdvancedUpload(index);
      });
    });

    // Retry buttons for failed/warning torrents
    document.querySelectorAll('.bulk-item-actions .retry-quick').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const index = parseInt(btn.dataset.index);
        this.retryTorrentUpload(index);
      });
    });

    // Auto-scroll toggle button
    const autoScrollToggle = document.getElementById('auto-scroll-toggle');
    if (autoScrollToggle) {
      autoScrollToggle.addEventListener('click', () => {
        this.toggleAutoScroll();
      });
    }

    // Detect manual scrolling to disable auto-scroll
    this.setupScrollDetection();

    // Initial button state update
    this.updateBulkUploadButton();
  }

  toggleAllTorrents(select) {
    document.querySelectorAll('.bulk-item input[type="checkbox"]').forEach(checkbox => {
      checkbox.checked = select;
    });
    this.updateBulkUploadButton();
  }

  toggleVisibleTorrents(select) {
    document.querySelectorAll('.bulk-item:not([style*="display: none"]) input[type="checkbox"]').forEach(checkbox => {
      checkbox.checked = select;
    });
    this.updateBulkUploadButton();
  }

  selectFilteredItems() {
    // First deselect all items
    document.querySelectorAll('.bulk-item input[type="checkbox"]').forEach(checkbox => {
      checkbox.checked = false;
    });

    // Then select only visible (filtered) items
    document.querySelectorAll('.bulk-item').forEach(item => {
      const checkbox = item.querySelector('input[type="checkbox"]');
      const isVisible = !item.style.display || item.style.display !== 'none';

      if (isVisible && checkbox) {
        checkbox.checked = true;
      }
    });

    this.updateBulkUploadButton();
  }

  handleTypeFilter(filterType) {
    this.currentTypeFilter = filterType;

    // Update active filter button
    document.querySelectorAll('[data-filter]').forEach(btn => {
      btn.classList.remove('active');
    });
    document.querySelector(`[data-filter="${filterType}"]`).classList.add('active');

    this.applyFilters();

    // Auto-select filtered items
    this.selectFilteredItems();
  }

  handleContentFilter(contentType) {
    this.currentContentFilter = contentType;

    // Update active filter button
    document.querySelectorAll('[data-content]').forEach(btn => {
      btn.classList.remove('active');
    });
    document.querySelector(`[data-content="${contentType}"]`).classList.add('active');

    this.applyFilters();

    // Auto-select filtered items
    this.selectFilteredItems();
  }

  applyFilters() {
    document.querySelectorAll('.bulk-item').forEach((item, index) => {
      const torrentData = this.getCurrentBulkData().torrents[index];
      const matchesTypeFilter = this.currentTypeFilter === 'all' || torrentData.type === this.currentTypeFilter;
      const matchesContentFilter = this.currentContentFilter === 'all' || torrentData.contentType === this.currentContentFilter;

      if (matchesTypeFilter && matchesContentFilter) {
        item.style.display = 'flex';
      } else {
        item.style.display = 'none';
      }
    });

    // Renumber visible items
    this.renumberVisibleItems();

    this.updateFilterCounts();
    this.updateBulkUploadButton();
  }

  getCurrentBulkData() {
    // Store bulk data in instance for filter operations
    return this.bulkData || { torrents: [] };
  }

  renumberVisibleItems() {
    // Get all visible items and renumber them sequentially
    const visibleItems = document.querySelectorAll('.bulk-item:not([style*="display: none"])');
    visibleItems.forEach((item, visibleIndex) => {
      const numberElement = item.querySelector('.item-number');
      if (numberElement) {
        numberElement.textContent = visibleIndex + 1;
      }
    });
  }

  updateFilterCounts() {
    const visibleCount = document.querySelectorAll('.bulk-item:not([style*="display: none"])').length;
    const selectAllBtn = document.getElementById('select-all-btn');
    if (selectAllBtn) {
      selectAllBtn.textContent = `Select All Visible (${visibleCount})`;
    }
  }

  updateBulkUploadButton() {
    const selectedCount = document.querySelectorAll('.bulk-item input[type="checkbox"]:checked').length;
    const uploadButton = document.getElementById('start-bulk-upload-btn');

    if (uploadButton) {
      uploadButton.textContent = `Upload Selected (${selectedCount})`;
      uploadButton.disabled = selectedCount === 0;
    }
  }

  toggleAutoScroll() {
    this.autoScrollEnabled = !this.autoScrollEnabled;
    const toggleButton = document.getElementById('auto-scroll-toggle');

    if (toggleButton) {
      if (this.autoScrollEnabled) {
        toggleButton.classList.add('active');
        toggleButton.title = 'Auto-scroll to current upload (enabled)';
      } else {
        toggleButton.classList.remove('active');
        toggleButton.title = 'Auto-scroll to current upload (disabled)';
      }
    }
  }

  setupScrollDetection() {
    const bulkContainer = document.querySelector('.bulk-upload-container');
    if (!bulkContainer) return;

    let isScrolling = false;

    bulkContainer.addEventListener('scroll', () => {
      if (isScrolling) return; // Ignore programmatic scrolling

      // User is manually scrolling, disable auto-scroll temporarily
      if (this.autoScrollEnabled) {
        this.autoScrollEnabled = false;
        this.updateAutoScrollButton();

        // Clear existing timeout
        if (this.userScrollTimeout) {
          clearTimeout(this.userScrollTimeout);
        }

        // Re-enable auto-scroll after 3 seconds of no manual scrolling
        this.userScrollTimeout = setTimeout(() => {
          this.autoScrollEnabled = true;
          this.updateAutoScrollButton();
        }, 3000);
      }
    });

    // Store reference to control programmatic scrolling
    this.isScrolling = () => isScrolling;
    this.setScrolling = (value) => { isScrolling = value; };
  }

  updateAutoScrollButton() {
    const toggleButton = document.getElementById('auto-scroll-toggle');
    if (toggleButton) {
      if (this.autoScrollEnabled) {
        toggleButton.classList.add('active');
        toggleButton.title = 'Auto-scroll to current upload (enabled)';
      } else {
        toggleButton.classList.remove('active');
        toggleButton.title = 'Auto-scroll to current upload (disabled) - will re-enable after 3s';
      }
    }
  }

  scrollToTorrent(index) {
    if (!this.autoScrollEnabled) return;

    const torrentElement = document.querySelector(`[data-index="${index}"]`);
    const bulkContainer = document.querySelector('.bulk-upload-container');

    if (torrentElement && bulkContainer) {
      // Set flag to ignore this scroll event
      if (this.setScrolling) {
        this.setScrolling(true);
      }

      // Scroll to the torrent with smooth animation
      torrentElement.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
        inline: 'nearest'
      });

      // Add highlight effect
      torrentElement.classList.add('auto-scroll-highlight');
      setTimeout(() => {
        torrentElement.classList.remove('auto-scroll-highlight');
        // Reset scrolling flag after animation
        if (this.setScrolling) {
          this.setScrolling(false);
        }
      }, 1000);
    }
  }

  async startBulkUpload(bulkData) {
    const selectedTorrents = this.getSelectedTorrents(bulkData);
    if (selectedTorrents.length === 0) return;

    // Get optional IMDb ID from the bulk interface
    const bulkImdbId = document.getElementById('bulk-imdb-id')?.value?.trim() || null;

    // Show progress interface
    document.getElementById('bulk-progress').style.display = 'block';
    document.getElementById('start-bulk-upload-btn').disabled = true;

    // Ensure auto-scroll is enabled at the start of bulk upload
    this.autoScrollEnabled = true;
    this.updateAutoScrollButton();

    const results = {
      total: selectedTorrents.length,
      completed: 0,
      successful: 0,
      warnings: 0,
      failed: 0,
      details: []
    };

    // Process torrents one by one (to avoid overwhelming the server)
    for (let i = 0; i < selectedTorrents.length; i++) {
      const torrent = selectedTorrents[i];

      try {
        this.updateTorrentStatus(torrent.index, 'processing', 'Uploading...');

        let result;

        if (torrent.type === 'magnet') {
          // Use quick upload for magnet links
          const uploadOptions = {
            metaType: torrent.contentType,
            isQuickImport: true
          };

          // Add IMDb ID if provided
          if (bulkImdbId) {
            uploadOptions.metaId = bulkImdbId;
          }

          result = await this.sendMessage({
            action: "quickUpload",
            data: {
              magnetLink: torrent.url,
              options: uploadOptions
            }
          });
        } else {
          // For torrent URLs, download the file and use regular upload
          this.updateTorrentStatus(torrent.index, 'processing', 'Downloading torrent file...');

          const response = await fetch(torrent.url);
          if (!response.ok) {
            throw new Error('Failed to download torrent file');
          }

          const torrentBlob = await response.blob();
          const torrentFile = new File([torrentBlob], `${torrent.title}.torrent`, {
            type: 'application/x-bittorrent'
          });

          // Convert file to transmittable format
          const arrayBuffer = await torrentFile.arrayBuffer();
          const torrentData = {
            torrentFileData: {
              data: Array.from(new Uint8Array(arrayBuffer)),
              type: torrentFile.type,
              name: torrentFile.name
            },
            metaType: torrent.contentType
          };

          this.updateTorrentStatus(torrent.index, 'processing', 'Uploading torrent file...');

          // Use quick upload with torrent file data
          const uploadOptions = {
            metaType: torrent.contentType,
            torrentFileData: torrentData.torrentFileData,
            isQuickImport: true
          };

          // Add IMDb ID if provided
          if (bulkImdbId) {
            uploadOptions.metaId = bulkImdbId;
          }

          result = await this.sendMessage({
            action: "quickUpload",
            data: {
              magnetLink: null,
              options: uploadOptions
            }
          });
        }

        if (result.success) {
          // Handle different response types properly
          const uploadResult = await this.handleBulkUploadResponse(result.data, torrent);

          if (uploadResult.success) {
            this.updateTorrentStatus(torrent.index, 'success', uploadResult.message);
            results.successful++;
            results.details.push({
              title: torrent.title,
              status: 'success',
              message: uploadResult.message,
              analyzedData: uploadResult.analyzedData
            });
          } else {
            // This is a warning/skip case (needs_annotation, validation_failed, etc.)
            this.updateTorrentStatus(torrent.index, 'warning', uploadResult.message);
            results.warnings++;
            results.details.push({
              title: torrent.title,
              status: 'warning',
              message: uploadResult.message,
              analyzedData: uploadResult.analyzedData
            });
          }
        } else {
          throw new Error(result.error || 'Upload failed');
        }

      } catch (error) {
        console.error(`Failed to upload torrent ${torrent.title}:`, error);
        this.updateTorrentStatus(torrent.index, 'error', error.message);
        results.failed++;

        results.details.push({
          title: torrent.title,
          status: 'error',
          message: error.message,
          analyzedData: null
        });
      }

      results.completed++;
      this.updateBulkProgress(results);

      // Small delay between uploads to be nice to the server
      if (i < selectedTorrents.length - 1) {
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    }

    // Store results for later use in advanced upload
    this.bulkUploadResults = results.details;

    // Hide progress and show completion message
    document.getElementById('bulk-progress').style.display = 'none';
    document.getElementById('start-bulk-upload-btn').disabled = false;
    document.getElementById('start-bulk-upload-btn').textContent = 'Upload Again';

    this.showMessage(`Bulk upload completed: ${results.successful} successful, ${results.warnings} warnings, ${results.failed} failed`, "info");
  }

  getSelectedTorrents(bulkData) {
    const selected = [];
    document.querySelectorAll('.bulk-item input[type="checkbox"]:checked').forEach(checkbox => {
      const index = parseInt(checkbox.closest('.bulk-item').dataset.index);
      selected.push({
        ...bulkData.torrents[index],
        index: index
      });
    });
    return selected;
  }

  updateTorrentStatus(index, status, message) {
    const statusElement = document.getElementById(`status-${index}`);
    const bulkItem = document.querySelector(`[data-index="${index}"]`);
    const actionsElement = document.getElementById(`actions-${index}`);

    if (!statusElement || !bulkItem) return;

    // Store the status in the torrent data for persistence
    if (this.bulkData && this.bulkData.torrents && this.bulkData.torrents[index]) {
      this.bulkData.torrents[index].uploadStatus = status;
      this.bulkData.torrents[index].uploadMessage = message;
    }

    // Auto-scroll to torrent when it starts processing
    if (status === 'processing') {
      this.scrollToTorrent(index);
    }

    // Update status text
    statusElement.className = `bulk-item-status ${status}`;
    statusElement.innerHTML = `<span class="status-text">${message}</span>`;

    // Update bulk item highlighting
    bulkItem.className = `bulk-item ${status}`;

    // Show/hide action buttons based on status
    if (actionsElement) {
      if (status === 'error' || status === 'warning') {
        // Show retry and advanced buttons for failed/warning items
        actionsElement.innerHTML = `
          <button class="result-action-btn retry-quick" data-index="${index}" title="Retry Upload">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z" />
            </svg>
          </button>
          <button class="result-action-btn advanced-upload" data-index="${index}" title="Manual Upload">
            <svg viewBox="0 0 24 24" fill="currentColor">
              <path d="M9,16V10H5L12,3L19,10H15V16H9M5,20V18H19V20H5Z" />
            </svg>
          </button>
        `;
        // Re-setup event listeners for new buttons
        this.setupActionButtonListeners(actionsElement);
      } else if (status === 'success') {
        // Show success indicator
        actionsElement.innerHTML = '<span class="result-success-indicator">✓</span>';
      } else if (status === 'processing') {
        // Hide buttons during processing
        actionsElement.innerHTML = '';
      }
    }
  }

  setupActionButtonListeners(actionsElement) {
    const retryBtn = actionsElement.querySelector('.retry-quick');
    const advancedBtn = actionsElement.querySelector('.advanced-upload');

    if (retryBtn) {
      retryBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const index = parseInt(retryBtn.dataset.index);
        this.retryTorrentUpload(index);
      });
    }

    if (advancedBtn) {
      advancedBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const index = parseInt(advancedBtn.dataset.index);
        this.handleAdvancedUpload(index);
      });
    }
  }

  async retryTorrentUpload(index) {
    if (!this.bulkData || !this.bulkData.torrents[index]) {
      this.showMessage("Torrent data not found for retry", "error");
      return;
    }

    const torrent = this.bulkData.torrents[index];

    // Get optional IMDb ID from the bulk interface
    const bulkImdbId = document.getElementById('bulk-imdb-id')?.value?.trim() || null;

    try {
      this.updateTorrentStatus(index, 'processing', 'Retrying upload...');

      let result;
      if (torrent.type === 'magnet') {
        const uploadOptions = {
          metaType: torrent.contentType,
          isQuickImport: true
        };

        // Add IMDb ID if provided
        if (bulkImdbId) {
          uploadOptions.metaId = bulkImdbId;
        }

        result = await this.sendMessage({
          action: "quickUpload",
          data: {
            magnetLink: torrent.url,
            options: uploadOptions
          }
        });
      } else {
        // For torrent files, we need to re-download and upload
        this.updateTorrentStatus(index, 'processing', 'Downloading torrent file...');

        const response = await fetch(torrent.url);
        if (!response.ok) {
          throw new Error('Failed to download torrent file');
        }

        const torrentBlob = await response.blob();
        const torrentFile = new File([torrentBlob], `${torrent.title}.torrent`, {
          type: 'application/x-bittorrent'
        });

        const arrayBuffer = await torrentFile.arrayBuffer();
        const torrentFileData = {
          data: Array.from(new Uint8Array(arrayBuffer)),
          type: torrentFile.type,
          name: torrentFile.name
        };

        this.updateTorrentStatus(index, 'processing', 'Retrying upload...');

        const uploadOptions = {
          metaType: torrent.contentType,
          torrentFileData: torrentFileData,
          isQuickImport: true
        };

        // Add IMDb ID if provided
        if (bulkImdbId) {
          uploadOptions.metaId = bulkImdbId;
        }

        result = await this.sendMessage({
          action: "quickUpload",
          data: {
            magnetLink: null,
            options: uploadOptions
          }
        });
      }

      if (result.success) {
        const uploadResult = await this.handleBulkUploadResponse(result.data, torrent);

        if (uploadResult.success) {
          this.updateTorrentStatus(index, 'success', uploadResult.message);
          this.showMessage(`Successfully uploaded: ${torrent.title}`, "success");
        } else {
          this.updateTorrentStatus(index, 'warning', uploadResult.message);
          this.showMessage(`Upload completed with warnings: ${torrent.title}`, "warning");
        }
      } else {
        throw new Error(result.error || 'Upload failed');
      }

    } catch (error) {
      this.updateTorrentStatus(index, 'error', error.message);
      this.showMessage(`Retry failed for ${torrent.title}: ${error.message}`, "error");
    }
  }

  updateBulkProgress(results) {
    const progressFill = document.getElementById('progress-fill');
    const progressText = document.getElementById('progress-text');

    const percentage = (results.completed / results.total) * 100;
    progressFill.style.width = `${percentage}%`;
    progressText.textContent = `${results.completed} / ${results.total} completed (${results.successful} successful, ${results.warnings} warnings, ${results.failed} failed)`;
  }

  showBulkUploadResults(results) {
    const resultsContainer = document.getElementById('bulk-results');
    const summaryElement = document.getElementById('results-summary');
    const detailsElement = document.getElementById('results-details');

    summaryElement.innerHTML = `
      <div class="summary-stats">
        <div class="stat success">
          <span class="stat-number">${results.successful}</span>
          <span class="stat-label">Successful</span>
        </div>
        <div class="stat warning">
          <span class="stat-number">${results.warnings}</span>
          <span class="stat-label">Warnings</span>
        </div>
        <div class="stat error">
          <span class="stat-number">${results.failed}</span>
          <span class="stat-label">Failed</span>
        </div>
        <div class="stat total">
          <span class="stat-number">${results.total}</span>
          <span class="stat-label">Total</span>
        </div>
      </div>
    `;

    detailsElement.innerHTML = results.details.map((detail, index) => `
      <div class="result-item ${detail.status}" data-result-index="${index}">
        <div class="result-content">
          <div class="result-title">${detail.title}</div>
          <div class="result-message">${detail.message}</div>
        </div>
        <div class="result-actions">
          ${this.generateResultActions(detail, index)}
        </div>
      </div>
    `).join('');

    resultsContainer.style.display = 'block';

    // Setup event listeners for individual action buttons
    this.setupResultActionListeners();

    // Re-enable upload button for potential retry
    document.getElementById('start-bulk-upload-btn').disabled = false;
    document.getElementById('start-bulk-upload-btn').textContent = 'Upload Again';
  }

  generateResultActions(detail, index) {
    if (detail.status === 'success') {
      // No actions needed for successful uploads
      return '<span class="result-success-indicator">✓</span>';
    } else if (detail.status === 'warning') {
      // Actions for warnings (needs annotation, validation failed, etc.)
      return `
        <button class="result-action-btn retry-quick" data-index="${index}" title="Retry Quick Upload">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z" />
          </svg>
        </button>
        <button class="result-action-btn advanced-upload" data-index="${index}" title="Advanced Upload">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 12,8.5A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.98C19.47,12.66 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11.02L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.65 15.48,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.52,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11.02C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.66 4.57,12.98L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.52,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.48,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.98Z" />
          </svg>
        </button>
      `;
    } else if (detail.status === 'error') {
      // Actions for hard failures (network errors, etc.)
      return `
        <button class="result-action-btn retry-quick" data-index="${index}" title="Retry Quick Upload">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z" />
          </svg>
        </button>
        <button class="result-action-btn advanced-upload" data-index="${index}" title="Advanced Upload">
          <svg viewBox="0 0 24 24" fill="currentColor">
            <path d="M12,15.5A3.5,3.5 0 0,1 8.5,12A3.5,3.5 0 0,1 15.5,12A3.5,3.5 0 0,1 12,15.5M19.43,12.98C19.47,12.66 19.5,12.33 19.5,12C19.5,11.67 19.47,11.34 19.43,11.02L21.54,9.37C21.73,9.22 21.78,8.95 21.66,8.73L19.66,5.27C19.54,5.05 19.27,4.96 19.05,5.05L16.56,6.05C16.04,5.65 15.48,5.32 14.87,5.07L14.5,2.42C14.46,2.18 14.25,2 14,2H10C9.75,2 9.54,2.18 9.5,2.42L9.13,5.07C8.52,5.32 7.96,5.66 7.44,6.05L4.95,5.05C4.73,4.96 4.46,5.05 4.34,5.27L2.34,8.73C2.22,8.95 2.27,9.22 2.46,9.37L4.57,11.02C4.53,11.34 4.5,11.67 4.5,12C4.5,12.33 4.53,12.66 4.57,12.98L2.46,14.63C2.27,14.78 2.22,15.05 2.34,15.27L4.34,18.73C4.46,18.95 4.73,19.03 4.95,18.95L7.44,17.94C7.96,18.34 8.52,18.68 9.13,18.93L9.5,21.58C9.54,21.82 9.75,22 10,22H14C14.25,22 14.46,21.82 14.5,21.58L14.87,18.93C15.48,18.68 16.04,18.34 16.56,17.94L19.05,18.95C19.27,19.03 19.54,18.95 19.66,18.73L21.66,15.27C21.78,15.05 21.73,14.78 21.54,14.63L19.43,12.98Z" />
          </svg>
        </button>
      `;
    }
    return '';
  }

  setupResultActionListeners() {
    // Retry quick upload buttons
    document.querySelectorAll('.result-action-btn.retry-quick').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const index = parseInt(e.currentTarget.dataset.index);
        this.handleRetryQuickUpload(index);
      });
    });

    // Advanced upload buttons
    document.querySelectorAll('.result-action-btn.advanced-upload').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const index = parseInt(e.currentTarget.dataset.index);
        this.handleAdvancedUpload(index);
      });
    });
  }

  async handleRetryQuickUpload(resultIndex) {
    if (!this.bulkData || !this.bulkData.torrents[resultIndex]) {
      this.showMessage("Torrent data not found for retry", "error");
      return;
    }

    const torrent = this.bulkData.torrents[resultIndex];
    const resultItem = document.querySelector(`[data-result-index="${resultIndex}"]`);

    if (!resultItem) return;

    try {
      // Update UI to show retrying
      const actionsDiv = resultItem.querySelector('.result-actions');
      actionsDiv.innerHTML = '<span class="result-processing">Retrying...</span>';

      // Perform the same upload logic as bulk upload
      let result;

      if (torrent.type === 'magnet') {
        result = await this.sendMessage({
          action: "quickUpload",
          data: {
            magnetLink: torrent.url,
            options: {
              metaType: torrent.contentType
            }
          }
        });
      } else {
        // Download and upload torrent file
        const response = await fetch(torrent.url);
        if (!response.ok) {
          throw new Error('Failed to download torrent file');
        }

        const torrentBlob = await response.blob();
        const torrentFile = new File([torrentBlob], `${torrent.title}.torrent`, {
          type: 'application/x-bittorrent'
        });

        const arrayBuffer = await torrentFile.arrayBuffer();
        const torrentFileData = {
          data: Array.from(new Uint8Array(arrayBuffer)),
          type: torrentFile.type,
          name: torrentFile.name
        };

        result = await this.sendMessage({
          action: "quickUpload",
          data: {
            magnetLink: null,
            options: {
              metaType: torrent.contentType,
              torrentFileData: torrentFileData
            }
          }
        });
      }

      if (result.success) {
        const uploadResult = await this.handleBulkUploadResponse(result.data, torrent);

        if (uploadResult.success) {
          // Success - update the result item
          resultItem.className = 'result-item success';
          resultItem.querySelector('.result-message').textContent = uploadResult.message;
          actionsDiv.innerHTML = '<span class="result-success-indicator">✓</span>';
          this.showMessage(`Successfully uploaded: ${torrent.title}`, "success");
        } else {
          // Still has issues - restore action buttons
          resultItem.className = 'result-item warning';
          resultItem.querySelector('.result-message').textContent = uploadResult.message;
          actionsDiv.innerHTML = this.generateResultActions({status: 'warning'}, resultIndex);
          this.setupResultActionListeners(); // Re-setup listeners
        }
      } else {
        throw new Error(result.error || 'Upload failed');
      }

    } catch (error) {
      // Error - restore action buttons
      resultItem.className = 'result-item error';
      resultItem.querySelector('.result-message').textContent = error.message;
      const actionsDiv = resultItem.querySelector('.result-actions');
      actionsDiv.innerHTML = this.generateResultActions({status: 'error'}, resultIndex);
      this.setupResultActionListeners(); // Re-setup listeners

      this.showMessage(`Retry failed for ${torrent.title}: ${error.message}`, "error");
    }
  }

  async handleAdvancedUpload(resultIndex) {
    if (!this.bulkData || !this.bulkData.torrents[resultIndex]) {
      this.showMessage("Torrent data not found for advanced upload", "error");
      return;
    }

    const torrent = this.bulkData.torrents[resultIndex];

    try {
      // Store that we're in advanced mode from bulk upload
      this.isAdvancedFromBulk = true;
      this.currentBulkTorrentIndex = resultIndex;

      // Hide bulk upload interface
      document.querySelector('.bulk-upload-container').style.display = 'none';

      // Show normal interface
      const container = document.querySelector('.container');
      if (container) {
        container.style.display = 'block';
      }

      // Show back button in the normal interface
      this.showBackToBulkButton();

      // Switch to upload tab
      this.switchTab('upload');

      // Check if we have analyzed data from bulk upload results
      const bulkResult = this.bulkUploadResults && this.bulkUploadResults[resultIndex];
      if (bulkResult && bulkResult.analyzedData) {
        // Use the analyzed data from the failed bulk upload
        this.populateFormWithAnalyzedData(bulkResult.analyzedData, torrent);
      } else {
        // Fallback to original torrent data
        if (torrent.type === 'magnet') {
          document.getElementById('magnet-input').value = torrent.url;
        } else {
          // For torrent URLs, we need to download and set as file
          await this.handleTorrentUrl(torrent.url);
        }

        // Set content type
        document.getElementById('content-type').value = torrent.contentType;
        this.toggleSportsCategoryVisibility();
      }

      // Show message to user
      this.showMessage(`Loaded ${torrent.title} for advanced upload. Please review and configure as needed.`, "info");

    } catch (error) {
      this.showMessage(`Failed to load torrent for advanced upload: ${error.message}`, "error");
    }
  }

  populateFormWithAnalyzedData(analyzedData, originalTorrent) {
    try {
      // Clear existing form data
      this.clearForm();

      // Set the original torrent/magnet data
      if (originalTorrent.type === 'magnet') {
        document.getElementById('magnet-input').value = originalTorrent.url;
      } else if (originalTorrent.type === 'torrent') {
        // For torrent files, we need to show the original URL or file info
        const torrentInput = document.getElementById('torrent-input');
        if (torrentInput) {
          // Create a display element to show the torrent file info
          const torrentInfo = document.createElement('div');
          torrentInfo.className = 'torrent-file-info';
          torrentInfo.innerHTML = `
            <span class="file-name">${originalTorrent.title}</span>
            <span class="file-source">From: ${originalTorrent.url}</span>
          `;
          torrentInput.parentNode.appendChild(torrentInfo);
        }
      }

      // Populate analyzed data
      if (analyzedData.title) {
        document.getElementById('title').value = analyzedData.title;
      }

      if (analyzedData.description) {
        document.getElementById('description').value = analyzedData.description;
      }

      if (analyzedData.content_type) {
        document.getElementById('content-type').value = analyzedData.content_type;
        this.toggleSportsCategoryVisibility();
      }

      if (analyzedData.category && analyzedData.content_type === 'sports') {
        document.getElementById('category').value = analyzedData.category;
      }

      if (analyzedData.poster) {
        document.getElementById('poster').value = analyzedData.poster;
      }

      if (analyzedData.background) {
        document.getElementById('background').value = analyzedData.background;
      }

      // Set catalog fields if available
      if (analyzedData.catalog) {
        const catalogCheckboxes = document.querySelectorAll('input[name="catalog"]');
        analyzedData.catalog.forEach(cat => {
          const checkbox = Array.from(catalogCheckboxes).find(cb => cb.value === cat);
          if (checkbox) checkbox.checked = true;
        });
      }

      // Set quality if available
      if (analyzedData.quality) {
        const qualityCheckboxes = document.querySelectorAll('input[name="quality"]');
        analyzedData.quality.forEach(qual => {
          const checkbox = Array.from(qualityCheckboxes).find(cb => cb.value === qual);
          if (checkbox) checkbox.checked = true;
        });
      }

      // Set language if available
      if (analyzedData.language) {
        const languageCheckboxes = document.querySelectorAll('input[name="language"]');
        analyzedData.language.forEach(lang => {
          const checkbox = Array.from(languageCheckboxes).find(cb => cb.value === lang);
          if (checkbox) checkbox.checked = true;
        });
      }

      this.showMessage("Form populated with analyzed data from bulk upload", "success");

    } catch (error) {
      console.error('Error populating form with analyzed data:', error);
      this.showMessage("Error populating form with analyzed data", "error");
    }
  }

  showBackToBulkButton() {
    // Create back button in normal interface if it doesn't exist
    let backButton = document.getElementById('back-to-bulk-btn');
    if (!backButton) {
      backButton = document.createElement('button');
      backButton.id = 'back-to-bulk-btn';
      backButton.className = 'back-btn';
      backButton.innerHTML = `
        <svg viewBox="0 0 24 24" fill="currentColor">
          <path d="M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z" />
        </svg>
        Back to Bulk Upload
      `;
      backButton.addEventListener('click', () => {
        this.returnToBulkUpload();
      });

      // Update header structure to accommodate the back button
      const header = document.querySelector('.header');
      if (header) {
        // Wrap existing content in logo-section if not already wrapped
        let logoSection = header.querySelector('.logo-section');
        if (!logoSection) {
          logoSection = document.createElement('div');
          logoSection.className = 'logo-section';

          // Move existing header content to logo section
          const existingContent = Array.from(header.children);
          existingContent.forEach(child => {
            logoSection.appendChild(child);
          });

          header.appendChild(logoSection);
        }

        // Add back button to header
        header.appendChild(backButton);
      }
    }
    backButton.style.display = 'inline-flex';
  }

  returnToBulkUpload() {
    // Hide normal interface
    const container = document.querySelector('.container');
    if (container) {
      container.style.display = 'none';
    }

    // Hide back button in normal interface
    const backButton = document.getElementById('back-to-bulk-btn');
    if (backButton) {
      backButton.style.display = 'none';
    }

    // Show bulk upload interface
    const bulkContainer = document.querySelector('.bulk-upload-container');
    if (bulkContainer) {
      bulkContainer.style.display = 'block';
    }

    // Clear form data
    this.clearForm();

    // Reset flags
    this.isAdvancedFromBulk = false;
    this.currentBulkTorrentIndex = null;

    this.showMessage("Returned to bulk upload mode", "info");
  }

  async sendMessage(message) {
    return new Promise((resolve) => {
      if (typeof browser !== "undefined" && browser.runtime) {
        browser.runtime.sendMessage(message).then(resolve).catch(() => resolve({ success: false, error: "Communication error" }));
      } else if (typeof chrome !== "undefined" && chrome.runtime) {
        chrome.runtime.sendMessage(message, (response) => {
          resolve(response || { success: false, error: "No response" });
        });
      } else {
        resolve({ success: false, error: "Extension runtime not available" });
      }
    });
  }
}

// Initialize popup when DOM is loaded - prevent multiple instances
if (!window.mediaFusionPopupLoaded) {
  window.mediaFusionPopupLoaded = true;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      new PopupManager();
    });
  } else {
    new PopupManager();
  }
}

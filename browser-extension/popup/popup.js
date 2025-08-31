// MediaFusion Browser Extension - Popup Script

class PopupManager {
    constructor() {
        this.selectedMatch = null;
        this.currentTorrentData = null;
        this.isProcessing = false;
        this.requestInProgress = new Set(); // Track specific request types
        this.hasAnalyzed = false; // Prevent duplicate analysis
        this.currentTheme = 'auto'; // Default theme
        this.init();
    }

    async init() {
        this.setupEventListeners();

        // Apply theme as early as possible to prevent flash
        await this.initializeTheme();

        await this.loadSettings(); // Wait for settings to load before continuing
        this.checkConnectionStatus();
        this.checkForPrefilledData();
    }

    setupEventListeners() {
        // Tab switching
        document.querySelectorAll('.tab-button').forEach(button => {
            button.addEventListener('click', (e) => {
                this.switchTab(e.target.dataset.tab);
            });
        });

        // Settings
        document.getElementById('save-settings-btn').addEventListener('click', () => {
            this.saveSettings();
        });

        document.getElementById('test-connection-btn').addEventListener('click', () => {
            this.testConnection();
        });

        // Theme change listener - apply theme immediately when changed
        document.getElementById('theme-select').addEventListener('change', (e) => {
            this.applyTheme(e.target.value);
            // Save theme setting immediately
            this.saveThemeSetting(e.target.value);
        });

        // Upload functionality with request deduplication
        document.getElementById('analyze-btn').addEventListener('click', () => {
            this.handleAnalyze();
        });

        document.getElementById('quick-import-btn').addEventListener('click', () => {
            this.handleQuickImport();
        });

        document.getElementById('upload-with-match-btn').addEventListener('click', () => {
            this.handleUploadWithMatch();
        });

        document.getElementById('upload-manual-btn').addEventListener('click', () => {
            this.handleUploadManual();
        });

        document.getElementById('back-to-basic-btn').addEventListener('click', () => {
            this.showBasicUpload();
        });

        // File input handling
        document.getElementById('torrent-file').addEventListener('change', (e) => {
            if (e.target.files[0]) {
                document.getElementById('magnet-input').value = '';
            }
        });

        document.getElementById('magnet-input').addEventListener('input', (e) => {
            if (e.target.value.trim()) {
                document.getElementById('torrent-file').value = '';
            }
        });

        // Content type change handler
        document.getElementById('content-type').addEventListener('change', () => {
            // Update series options visibility when in advanced mode
            if (!document.getElementById('advanced-options').classList.contains('hidden')) {
                this.populateCatalogOptions();
            }
        });

        // File annotation modal handlers
        document.getElementById('close-annotation-modal').addEventListener('click', () => {
            this.hideFileAnnotationModal();
        });

        document.getElementById('cancel-annotation').addEventListener('click', () => {
            this.hideFileAnnotationModal();
        });

        document.getElementById('confirm-annotation').addEventListener('click', () => {
            this.handleAnnotationConfirm();
        });
    }

    // Prevent duplicate requests with specific tracking
    async handleAnalyze() {
        const requestKey = 'analyze';

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
        const requestKey = 'upload-match';
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
        const requestKey = 'upload-manual';
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
        try {
            const basicData = this.collectBasicData();
            const settings = await this.sendMessage({ action: 'getSettings' });

            if (!settings.success) {
                this.showMessage('Failed to get settings. Please configure MediaFusion URL first.', 'error');
                return;
            }

            const baseUrl = settings.data.baseUrl || 'https://mediafusion.elfhosted.com';

            // Construct the scraper URL with quick import parameters
            const scraperUrl = new URL('/scraper/', baseUrl);
            const params = new URLSearchParams();

            // Set scraper to quick_import
            params.append('scraper', 'quick_import');
            params.append('meta_type', basicData.metaType);
            params.append('uploader', basicData.uploaderName);

            if (basicData.magnetLink) {
                params.append('magnet_link', basicData.magnetLink);
            } else if (basicData.torrentFile) {
                // For torrent files, we'll need to store it temporarily or convert to magnet
                // For now, let's show a message that they should use the analyze option for files
                this.showMessage('For torrent files, please use the "Analyze Torrent" option instead. Quick Import works best with magnet links.', 'warning');
                return;
            }

            const fullUrl = `${scraperUrl.href}?${params.toString()}`;

            // Open the scraper page in a new tab
            if (typeof browser !== 'undefined' && browser.tabs) {
                // Firefox
                browser.tabs.create({ url: fullUrl });
            } else if (typeof chrome !== 'undefined' && chrome.tabs) {
                // Chrome
                chrome.tabs.create({ url: fullUrl });
            } else {
                // Fallback: open in current window
                window.open(fullUrl, '_blank');
            }

            this.showMessage('Opening MediaFusion Quick Import page...', 'success');

            // Close the popup after a short delay
            setTimeout(() => {
                window.close();
            }, 1000);

        } catch (error) {
            this.showMessage('Failed to open Quick Import: ' + error.message, 'error');
        }
    }

    async checkForPrefilledData() {
        // Only check URL parameters (simpler and more reliable)
        const urlParams = new URLSearchParams(window.location.search);
        const magnetLink = urlParams.get('magnet');
        const torrentUrl = urlParams.get('torrent');
        const contentType = urlParams.get('type');
        const sourceUrl = urlParams.get('source');
        const title = urlParams.get('title');

        if (magnetLink) {
            document.getElementById('magnet-input').value = decodeURIComponent(magnetLink);
        } else if (torrentUrl) {
            // For torrent URLs, we need to download and convert to file
            this.handleTorrentUrl(decodeURIComponent(torrentUrl));
        }

        if (contentType) {
            document.getElementById('content-type').value = contentType;
        }

        // Don't auto-analyze - let user choose what to do
        // This addresses the second issue about direct analysis
    }

        async handleTorrentUrl(torrentUrl) {
        try {
            // Show loading overlay
            this.showLoading(true);
            this.showMessage('Downloading torrent file...', 'info');

            // Download the torrent file
            const response = await fetch(torrentUrl);
            if (!response.ok) {
                throw new Error('Failed to download torrent file');
            }

            const blob = await response.blob();
            const file = new File([blob], 'downloaded.torrent', { type: 'application/x-bittorrent' });

            // Create a file input element and set the file
            const fileInput = document.getElementById('torrent-file');
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            fileInput.files = dataTransfer.files;

            // Clear magnet input since we have a file
            document.getElementById('magnet-input').value = '';

            this.showMessage('Torrent file downloaded and loaded successfully', 'success');
        } catch (error) {
            this.showMessage('Failed to download torrent file: ' + error.message, 'error');
        } finally {
            this.showLoading(false);
        }
    }

    switchTab(tabName) {
        document.querySelectorAll('.tab-button').forEach(button => {
            button.classList.remove('active');
        });
        document.querySelector(`[data-tab="${tabName}"]`).classList.add('active');

        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.remove('active');
        });
        document.getElementById(`${tabName}-tab`).classList.add('active');
    }

    showBasicUpload() {
        document.getElementById('basic-upload').classList.remove('hidden');
        document.getElementById('analysis-results').classList.add('hidden');
        document.getElementById('advanced-options').classList.add('hidden');
        this.clearForm();
    }

    showAdvancedOptions() {
        document.getElementById('basic-upload').classList.add('hidden');
        document.getElementById('advanced-options').classList.remove('hidden');
        this.populateCatalogOptions();
    }

        populateCatalogOptions() {
        const contentType = document.getElementById('content-type').value;
        const catalogContainer = document.getElementById('catalog-selection');
        const seriesOptions = document.getElementById('series-options');
        const sportsOptions = document.getElementById('sports-options');

        // Show/hide content-specific options
        if (contentType === 'series') {
            seriesOptions.style.display = 'block';
            sportsOptions.style.display = 'none';
        } else if (contentType === 'sports') {
            seriesOptions.style.display = 'none';
            sportsOptions.style.display = 'block';
        } else {
            seriesOptions.style.display = 'none';
            sportsOptions.style.display = 'none';
        }

        let catalogHTML = '';

        if (contentType === 'movie') {
            catalogHTML = `
                <div class="catalog-grid">
                    <label><input type="checkbox" name="catalogs" value="tamil_hdrip"> Tamil HDRip</label>
                    <label><input type="checkbox" name="catalogs" value="malayalam_hdrip"> Malayalam HDRip</label>
                    <label><input type="checkbox" name="catalogs" value="telugu_hdrip"> Telugu HDRip</label>
                    <label><input type="checkbox" name="catalogs" value="hindi_hdrip"> Hindi HDRip</label>
                    <label><input type="checkbox" name="catalogs" value="kannada_hdrip"> Kannada HDRip</label>
                    <label><input type="checkbox" name="catalogs" value="english_hdrip"> English HDRip</label>
                </div>
            `;
        } else if (contentType === 'series') {
            catalogHTML = `
                <div class="catalog-grid">
                    <label><input type="checkbox" name="catalogs" value="tamil_series"> Tamil Series</label>
                    <label><input type="checkbox" name="catalogs" value="malayalam_series"> Malayalam Series</label>
                    <label><input type="checkbox" name="catalogs" value="telugu_series"> Telugu Series</label>
                    <label><input type="checkbox" name="catalogs" value="hindi_series"> Hindi Series</label>
                    <label><input type="checkbox" name="catalogs" value="kannada_series"> Kannada Series</label>
                    <label><input type="checkbox" name="catalogs" value="english_series"> English Series</label>
                </div>
            `;
        } else if (contentType === 'sports') {
            catalogHTML = `
                <select id="sports-catalog" class="form-control">
                    <option value="">Select Sports Category</option>
                    <option value="american_football">American Football / NFL</option>
                    <option value="baseball">Baseball / MLB</option>
                    <option value="basketball">Basketball / NBA</option>
                    <option value="football">Football / Soccer</option>
                    <option value="formula_racing">🏎️ Formula Racing</option>
                    <option value="hockey">Hockey / NHL</option>
                    <option value="motogp_racing">🏍️ MotoGP Racing</option>
                    <option value="other_sports">Other Sports</option>
                    <option value="rugby">Rugby / AFL</option>
                    <option value="wwe">WWE</option>
                    <option value="ufc">UFC</option>
                    <option value="fighting">Fighting</option>
                </select>
            `;
        }

        catalogContainer.innerHTML = catalogHTML;
    }

    async loadSettings() {
        try {
            const response = await this.sendMessage({ action: 'getSettings' });

            if (response.success) {
                const settings = response.data;

                // Show actual stored values (including defaults from background)
                document.getElementById('mediafusion-url').value = settings.baseUrl || 'https://mediafusion.elfhosted.com';
                document.getElementById('api-password').value = settings.apiPassword || '';
                document.getElementById('default-uploader').value = settings.uploaderName || 'Anonymous';
                document.getElementById('uploader-name').value = settings.uploaderName || 'Anonymous';
                document.getElementById('theme-select').value = settings.theme || 'auto';
                this.applyTheme(settings.theme || 'auto');
            }
        } catch (error) {
            this.showMessage('Failed to load settings: ' + error.message, 'error');
        }
    }

        async saveSettings() {
        let baseUrl = document.getElementById('mediafusion-url').value.trim() || 'https://mediafusion.elfhosted.com';

        // Strip trailing slash from URL
        if (baseUrl.endsWith('/')) {
            baseUrl = baseUrl.slice(0, -1);
        }

        const settings = {
            baseUrl: baseUrl,
            apiPassword: document.getElementById('api-password').value.trim(),
            uploaderName: document.getElementById('default-uploader').value.trim() || 'Anonymous',
            theme: document.getElementById('theme-select').value || 'auto'
        };

        try {
            const response = await this.sendMessage({ action: 'saveSettings', data: settings });

            if (response.success) {
                this.showMessage('Settings saved successfully', 'success');
                document.getElementById('uploader-name').value = settings.uploaderName;
                this.applyTheme(settings.theme);
                this.checkConnectionStatus();
            } else {
                this.showMessage('Failed to save settings: ' + (response.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            this.showMessage('Failed to save settings: ' + error.message, 'error');
        }
    }

        async initializeTheme() {
        try {
            // Get theme setting directly from storage for immediate application
            const response = await this.sendMessage({ action: 'getSettings' });
            if (response.success && response.data && response.data.theme) {
                this.applyTheme(response.data.theme);
            } else {
                // Apply default theme
                this.applyTheme('auto');
            }
        } catch (error) {
            console.log('Failed to initialize theme:', error);
            // Apply default theme on error
            this.applyTheme('auto');
        }
    }

    applyTheme(theme) {
        const html = document.documentElement;

        // Remove existing theme attributes
        html.removeAttribute('data-theme');

        if (theme === 'light') {
            html.setAttribute('data-theme', 'light');
        } else if (theme === 'dark') {
            html.setAttribute('data-theme', 'dark');
        }
        // For 'auto', we don't set any attribute, letting CSS media queries handle it

        // Store theme preference for consistency
        this.currentTheme = theme;
    }

    async saveThemeSetting(theme) {
        try {
            // Get current settings first
            const response = await this.sendMessage({ action: 'getSettings' });
            if (response.success && response.data) {
                // Update only the theme setting
                const updatedSettings = {
                    ...response.data,
                    theme: theme
                };

                // Save updated settings
                await this.sendMessage({ action: 'saveSettings', data: updatedSettings });
            }
        } catch (error) {
            console.log('Failed to save theme setting:', error);
        }
    }

    async testConnection() {
        this.setConnectionStatus('testing', 'Testing connection...');

        try {
            const response = await this.sendMessage({ action: 'testConnection' });
            if (response.success) {
                this.setConnectionStatus('connected', 'Connected successfully');
                this.showMessage('Connection test successful', 'success');
            } else {
                this.setConnectionStatus('error', 'Connection failed');
                this.showMessage(response.error || 'Connection test failed', 'error');
            }
        } catch (error) {
            this.setConnectionStatus('error', 'Connection failed');
            this.showMessage('Connection test failed: ' + error.message, 'error');
        }
    }

    async checkConnectionStatus() {
        const url = document.getElementById('mediafusion-url').value.trim();
        if (!url) {
            this.setConnectionStatus('error', 'Not configured');
            return;
        }

        try {
            const response = await this.sendMessage({ action: 'testConnection' });
            if (response.success) {
                this.setConnectionStatus('connected', 'Connected');
            } else {
                this.setConnectionStatus('error', 'Connection failed');
            }
        } catch (error) {
            this.setConnectionStatus('error', 'Connection failed');
        }
    }

    setConnectionStatus(status, text) {
        const indicator = document.getElementById('connection-indicator');
        const textElement = document.getElementById('connection-text');

        indicator.className = `status-indicator ${status}`;
        textElement.textContent = text;
    }

    collectBasicData() {
        const magnetLink = document.getElementById('magnet-input').value.trim();
        const torrentFile = document.getElementById('torrent-file').files[0];

        if (!magnetLink && !torrentFile) {
            throw new Error('Please provide either a magnet link or torrent file');
        }

        return {
            metaType: document.getElementById('content-type').value,
            uploaderName: document.getElementById('uploader-name').value.trim() || 'Anonymous',
            magnetLink: magnetLink,
            torrentFile: torrentFile
        };
    }

    collectAdvancedData() {
        const basicData = this.collectBasicData();

        const advancedData = {
            ...basicData,
            torrentType: document.getElementById('torrent-type').value,
            title: document.getElementById('title').value.trim(),
            resolution: document.getElementById('resolution').value,
            quality: document.getElementById('quality').value,
            codec: document.getElementById('codec').value.trim(),
            audio: document.getElementById('audio').value.trim()
        };

        // Add metaId if provided
        const imdbId = document.getElementById('imdb-id').value.trim();
        if (imdbId) {
            advancedData.metaId = imdbId;
        }

        // Series-specific: Episode name parser
        if (advancedData.metaType === 'series') {
            const episodeNameParser = document.getElementById('episode-name-parser')?.value.trim();
            if (episodeNameParser) {
                advancedData.episode_name_parser = episodeNameParser;
                console.log('Series episode parser set:', episodeNameParser);
            }
        }

        // Sports-specific: Episode name parser (for racing sports)
        if (advancedData.metaType === 'sports') {
            const sportsEpisodeParser = document.getElementById('sports-episode-parser')?.value.trim();
            if (sportsEpisodeParser) {
                advancedData.episode_name_parser = sportsEpisodeParser;
                console.log('Sports episode parser set:', sportsEpisodeParser);
            }
        }

        // Collect catalogs
        const contentType = advancedData.metaType;
        if (contentType === 'sports') {
            const sportsCatalog = document.getElementById('sports-catalog')?.value;
            if (sportsCatalog) {
                advancedData.catalogs = [sportsCatalog];
            }
        } else {
            const catalogCheckboxes = document.querySelectorAll('input[name="catalogs"]:checked');
            advancedData.catalogs = Array.from(catalogCheckboxes).map(cb => cb.value);
        }

        return advancedData;
    }

    async analyzeTorrent() {
        try {
            const basicData = this.collectBasicData();
            this.showLoading(true);

            const torrentData = {
                metaType: basicData.metaType
            };

            if (basicData.magnetLink) {
                torrentData.magnetLink = basicData.magnetLink;
            } else if (basicData.torrentFile) {
                // Convert File to Blob for proper transmission to background script
                const fileBuffer = await basicData.torrentFile.arrayBuffer();
                torrentData.torrentFileData = {
                    data: Array.from(new Uint8Array(fileBuffer)),
                    name: basicData.torrentFile.name,
                    type: basicData.torrentFile.type
                };
            }

            const response = await this.sendMessage({ action: 'analyzeTorrent', data: torrentData });

            if (response.success) {
                this.currentTorrentData = response.data;
                this.displayAnalysisResults(response.data);
                this.showAdvancedOptions();
                this.populateAdvancedFields(response.data);
                this.showMessage('Analysis completed successfully', 'success');
            } else {
                this.showMessage('Analysis failed: ' + (response.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            this.showMessage('Analysis failed: ' + error.message, 'error');
        } finally {
            this.showLoading(false);
        }
    }

    populateAdvancedFields(data) {
        const torrent = data.torrent_data;

        // Populate fields with analyzed data
        if (torrent.title && !document.getElementById('title').value) {
            document.getElementById('title').value = torrent.title;
        }
        if (torrent.resolution && !document.getElementById('resolution').value) {
            document.getElementById('resolution').value = torrent.resolution;
        }
        if (torrent.quality && !document.getElementById('quality').value) {
            document.getElementById('quality').value = torrent.quality;
        }
        if (torrent.codec && !document.getElementById('codec').value) {
            // Try to match codec to dropdown options
            const codecSelect = document.getElementById('codec');
            const codecValue = torrent.codec.toLowerCase();
            for (let option of codecSelect.options) {
                if (option.value.toLowerCase() === codecValue || option.text.toLowerCase().includes(codecValue)) {
                    codecSelect.value = option.value;
                    break;
                }
            }
        }
        if (torrent.audio && !document.getElementById('audio').value) {
            // Try to match audio to dropdown options
            const audioSelect = document.getElementById('audio');
            const audioValue = Array.isArray(torrent.audio) ? torrent.audio[0] : torrent.audio;
            const audioValueLower = audioValue.toLowerCase();
            for (let option of audioSelect.options) {
                if (option.value.toLowerCase() === audioValueLower || option.text.toLowerCase().includes(audioValueLower)) {
                    audioSelect.value = option.value;
                    break;
                }
            }
        }
    }

        displayAnalysisResults(data) {
        const resultsContainer = document.getElementById('analysis-results');
        const torrentInfo = document.getElementById('torrent-info');
        const matchesContainer = document.getElementById('matches-container');

        // Clear previous results first
        torrentInfo.innerHTML = '';
        matchesContainer.innerHTML = '';

        // Display torrent info
        const torrent = data.torrent_data;
        torrentInfo.innerHTML = `
            <div class="torrent-details">
                <strong>📁 ${torrent.title || 'Unknown Title'}</strong><br>
                <span>📊 Size: ${this.formatBytes(torrent.total_size || 0)}</span><br>
                <span>🎬 Quality: ${torrent.resolution || 'Unknown'} ${torrent.quality || ''}</span><br>
                <span>📂 Files: ${torrent.file_data ? torrent.file_data.length : 'Unknown'}</span>
            </div>
        `;

        // Display matches
        if (data.matches && data.matches.length > 0) {
            const matchesHeader = document.createElement('h4');
            matchesHeader.textContent = '🎯 Found Matches:';
            matchesContainer.appendChild(matchesHeader);

            data.matches.forEach((match, index) => {
                const matchElement = this.createMatchElement(match, index);
                matchesContainer.appendChild(matchElement);
            });
            document.getElementById('upload-with-match-btn').classList.remove('hidden');
        } else {
            matchesContainer.innerHTML = '<div class="no-matches">❌ No matches found. You can still upload manually.</div>';
            document.getElementById('upload-with-match-btn').classList.add('hidden');
        }

        resultsContainer.classList.remove('hidden');
    }

    createMatchElement(match, index) {
        const div = document.createElement('div');
        div.className = 'match-item';
        div.dataset.index = index;

        div.innerHTML = `
            <div class="match-title">🎬 ${match.title} (${match.year})</div>
            <div class="match-details">${match.description || 'No description available'}</div>
            <div class="match-meta">
                <span class="match-type">${match.type}</span>
                <span class="match-rating">⭐ ${match.imdb_rating || 'N/A'}</span>
                <span class="match-id">${match.imdb_id}</span>
            </div>
        `;

        div.addEventListener('click', () => {
            document.querySelectorAll('.match-item').forEach(item => {
                item.classList.remove('selected');
            });
            div.classList.add('selected');
            this.selectedMatch = match;

            // Populate IMDb ID when match is selected
            document.getElementById('imdb-id').value = match.imdb_id;
            if (!document.getElementById('title').value) {
                document.getElementById('title').value = match.title;
            }
        });

        return div;
    }

    async uploadWithMatch() {
        if (!this.selectedMatch || !this.currentTorrentData) {
            this.showMessage('Please select a match first', 'error');
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
            console.log('Torrent data for match upload:', torrentData);

            // Convert File object for transmission
            await this.convertTorrentFileForTransmission(torrentData);

            const response = await this.sendMessage({ action: 'uploadTorrent', data: torrentData });

            if (response.success) {
                await this.handleUploadResponse(response.data, torrentData);
            } else {
                this.showMessage('Upload failed: ' + (response.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            this.showMessage('Upload failed: ' + error.message, 'error');
        } finally {
            this.showLoading(false);
        }
    }

    async uploadManual() {
        if (!this.currentTorrentData) {
            this.showMessage('Please analyze the torrent first', 'error');
            return;
        }

        this.showLoading(true);

        try {
            const torrentData = this.collectAdvancedData();

            // Debug: Log torrent data being sent for manual upload
            console.log('Torrent data for manual upload:', torrentData);

            // Convert File object for transmission
            await this.convertTorrentFileForTransmission(torrentData);

            const response = await this.sendMessage({ action: 'uploadTorrent', data: torrentData });

            if (response.success) {
                await this.handleUploadResponse(response.data, torrentData);
            } else {
                this.showMessage('Upload failed: ' + (response.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            this.showMessage('Upload failed: ' + error.message, 'error');
        } finally {
            this.showLoading(false);
        }
    }

    async handleUploadResponse(data, originalTorrentData) {
        if (data.status === 'needs_annotation') {
            // Show file annotation modal
            this.showFileAnnotationModal(data.files, originalTorrentData);
        } else if (data.status === 'validation_failed') {
            // Show validation errors and ask if user wants to force import
            this.showValidationFailedDialog(data, originalTorrentData);
        } else if (data.status === 'warning') {
            // Show warning message but treat as success
            this.showMessage(data.message || 'Upload completed with warnings', 'warning');
            this.clearForm();
            this.showBasicUpload();
        } else if (data.status === 'error') {
            // Show error message
            this.showMessage(data.message || 'Upload failed with unknown error', 'error');
        } else if (data.status === 'success') {
            // Success
            const message = data.message || 'Upload completed successfully!';
            this.showMessage(message, 'success');
            this.clearForm();
            this.showBasicUpload();
        } else {
            // Unknown status
            console.warn('Unknown response status:', data.status);
            this.showMessage(data.message || 'Upload completed with unknown status: ' + data.status, 'warning');
            this.clearForm();
            this.showBasicUpload();
        }
    }

    clearForm() {
        document.getElementById('magnet-input').value = '';
        document.getElementById('torrent-file').value = '';
        document.getElementById('imdb-id').value = '';
        document.getElementById('title').value = '';
        document.getElementById('resolution').value = '';
        document.getElementById('quality').value = '';
        document.getElementById('codec').value = '';
        document.getElementById('audio').value = '';
                document.getElementById('episode-name-parser').value = '';
        document.getElementById('sports-episode-parser').value = '';

        document.querySelectorAll('input[name="catalogs"]').forEach(cb => cb.checked = false);

        this.selectedMatch = null;
        this.currentTorrentData = null;
    }

    showLoading(show) {
        const overlay = document.getElementById('loading-overlay');
        if (show) {
            overlay.classList.remove('hidden');
        } else {
            overlay.classList.add('hidden');
        }
    }

    showMessage(message, type = 'info') {
        const container = document.getElementById('status-messages');
        const messageDiv = document.createElement('div');
        messageDiv.className = `status-message ${type}`;
        messageDiv.textContent = message;

        container.appendChild(messageDiv);

        setTimeout(() => {
            if (messageDiv.parentNode) {
                messageDiv.parentNode.removeChild(messageDiv);
            }
        }, 8000);

        messageDiv.scrollIntoView({ behavior: 'smooth' });
    }

    formatBytes(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    showFileAnnotationModal(files, originalTorrentData) {
        this.annotationFiles = files;
        this.originalTorrentData = originalTorrentData;

        // Debug: Log the original torrent data to see what we have
        console.log('Original torrent data for annotation:', originalTorrentData);

        const modal = document.getElementById('file-annotation-modal');
        const fileList = document.getElementById('file-annotation-list');

        // Clear previous content
        fileList.innerHTML = '';

        const isSportsContent = originalTorrentData.metaType === 'sports';

        // Sort files by filename
        files.sort((a, b) => {
            return a.filename.localeCompare(b.filename, undefined, {
                numeric: true,
                sensitivity: 'base'
            });
        });

        files.forEach((file, index) => {
            const fileItem = document.createElement('div');
            fileItem.className = 'file-item';
            fileItem.id = `file-row-${index}`;
            fileItem.innerHTML = `
                <div class="file-header">
                    <input type="checkbox" class="file-checkbox" id="include-file-${index}" checked>
                    <div class="file-name" title="${file.filename}">${file.filename}</div>
                </div>
                <div class="file-inputs ${isSportsContent ? 'sports' : ''}">
                    <div class="input-group">
                        <label for="season-${index}">Season</label>
                        <button type="button" class="numbering-btn season-btn" data-index="${index}">
                            <span class="icon">▶</span> Apply same season to all following files
                        </button>
                        <input type="number" id="season-${index}" value="${file.season_number || ''}" min="1">
                    </div>
                    <div class="input-group">
                        <label for="episode-${index}">Episode</label>
                        <button type="button" class="numbering-btn episode-btn" data-index="${index}">
                            <span class="icon">▶</span> Apply consecutive episode numbering
                        </button>
                        <input type="number" id="episode-${index}" value="${file.episode_number || ''}" min="1">
                    </div>
                </div>
                ${isSportsContent ? `
                <div class="sports-metadata">
                    <div class="input-group">
                        <label for="title-${index}">Episode Title</label>
                        <input type="text" id="title-${index}" value="${file.episode_title || ''}" placeholder="Optional">
                    </div>
                    <div class="input-group">
                        <label for="overview-${index}">Overview</label>
                        <textarea id="overview-${index}" placeholder="Optional"></textarea>
                    </div>
                    <div class="input-group">
                        <label for="thumbnail-${index}">Thumbnail URL</label>
                        <input type="url" id="thumbnail-${index}" placeholder="Optional">
                    </div>
                    <div class="input-group">
                        <label for="release-${index}">Release Date</label>
                        <input type="date" id="release-${index}" value="${file.release_date || ''}">
                    </div>
                </div>
                ` : ''}
            `;
            fileList.appendChild(fileItem);
        });

                modal.classList.remove('hidden');

        // Add event listeners for numbering buttons
        this.setupNumberingButtonListeners();
    }

    setupNumberingButtonListeners() {
        // Season numbering buttons
        document.querySelectorAll('.season-btn').forEach(button => {
            button.addEventListener('click', (e) => {
                const index = parseInt(e.target.closest('.season-btn').dataset.index);
                this.applySeasonNumberingFrom(index);
            });
        });

        // Episode numbering buttons
        document.querySelectorAll('.episode-btn').forEach(button => {
            button.addEventListener('click', (e) => {
                const index = parseInt(e.target.closest('.episode-btn').dataset.index);
                this.applyEpisodeNumberingFrom(index);
            });
        });
    }

    applySeasonNumberingFrom(startIndex) {
        let seasonValue = document.getElementById(`season-${startIndex}`).value;
        if (!seasonValue) {
            // Default to 1 if empty
            seasonValue = '1';
            document.getElementById(`season-${startIndex}`).value = seasonValue;
        }

        // Get all file items starting from the specified index
        const allFiles = document.querySelectorAll('.file-item');
        const relevantFiles = Array.from(allFiles).slice(startIndex);

        // Apply the same season number to all following files
        relevantFiles.forEach((fileRow, idx) => {
            const actualIndex = startIndex + idx;
            const seasonInput = document.getElementById(`season-${actualIndex}`);
            const includeCheckbox = document.getElementById(`include-file-${actualIndex}`);

            // Only apply to included files
            if (includeCheckbox && includeCheckbox.checked && seasonInput) {
                seasonInput.value = seasonValue;

                // Add visual highlight
                fileRow.classList.add('numbered-file');
                setTimeout(() => {
                    fileRow.classList.remove('numbered-file');
                }, 1500);
            }
        });

        this.showMessage(`Applied season ${seasonValue} to ${relevantFiles.length} files`, 'success');
    }

    applyEpisodeNumberingFrom(startIndex) {
        const startEpisodeNumber = parseInt(document.getElementById(`episode-${startIndex}`).value) || 1;
        const resetOnSeasonChange = true;

        // Get all file items starting from the specified index
        const allFiles = document.querySelectorAll('.file-item');
        const relevantFiles = Array.from(allFiles).slice(startIndex);

        let episodeCounter = startEpisodeNumber;
        let lastSeason = null;

        // Apply consecutive episode numbers
        relevantFiles.forEach((fileRow, idx) => {
            const actualIndex = startIndex + idx;
            const episodeInput = document.getElementById(`episode-${actualIndex}`);
            const seasonInput = document.getElementById(`season-${actualIndex}`);
            const includeCheckbox = document.getElementById(`include-file-${actualIndex}`);

            // Only apply to included files
            if (includeCheckbox && includeCheckbox.checked && episodeInput) {
                const currentSeason = parseInt(seasonInput.value) || null;

                // Reset episode counter if season changes and reset option is enabled
                if (resetOnSeasonChange && lastSeason !== null && currentSeason !== null && currentSeason !== lastSeason) {
                    episodeCounter = 1;
                }

                // Set the episode number
                episodeInput.value = episodeCounter++;

                // Store the current season for next iteration
                if (currentSeason !== null) {
                    lastSeason = currentSeason;
                }

                // Add visual highlight
                fileRow.classList.add('numbered-file');
                setTimeout(() => {
                    fileRow.classList.remove('numbered-file');
                }, 1500);
            }
        });

        this.showMessage(`Applied consecutive episode numbering starting from ${startEpisodeNumber}`, 'success');
    }

    hideFileAnnotationModal() {
        const modal = document.getElementById('file-annotation-modal');
        modal.classList.add('hidden');
        this.annotationFiles = null;
        this.originalTorrentData = null;
    }

        async handleAnnotationConfirm() {
        if (!this.annotationFiles || !this.originalTorrentData) {
            this.showMessage('Missing annotation data. Please try again.', 'error');
            return;
        }

        const annotatedFiles = [];
        const isSportsContent = this.originalTorrentData.metaType === 'sports';

        this.annotationFiles.forEach((file, index) => {
            // Only include files that are checked
            if (document.getElementById(`include-file-${index}`)?.checked) {
                const baseData = {
                    ...file,
                    season_number: parseInt(document.getElementById(`season-${index}`).value) || null,
                    episode_number: parseInt(document.getElementById(`episode-${index}`).value) || null,
                };

                if (isSportsContent) {
                    const releaseDate = document.getElementById(`release-${index}`).value;
                    if (releaseDate) {
                        baseData.release_date = releaseDate;
                    }
                    annotatedFiles.push({
                        ...baseData,
                        title: document.getElementById(`title-${index}`).value || null,
                        overview: document.getElementById(`overview-${index}`).value || null,
                        thumbnail: document.getElementById(`thumbnail-${index}`).value || null,
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
                forceImport: true
            };

            // Debug: Log the final torrent data being sent
            console.log('Final torrent data being sent after annotation:', torrentData);

            // Convert File object for transmission
            await this.convertTorrentFileForTransmission(torrentData);

            const response = await this.sendMessage({ action: 'uploadTorrent', data: torrentData });

            if (response.success) {
                await this.handleUploadResponse(response.data, torrentData);
            } else {
                this.showMessage('Upload failed: ' + (response.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            this.showMessage('Upload failed: ' + error.message, 'error');
        } finally {
            this.showLoading(false);
        }
    }

        showValidationFailedDialog(data, originalTorrentData) {

        // Format error messages
        let errorMessage = 'Validation failed:\n\n';
        if (data.errors && Array.isArray(data.errors)) {
            errorMessage += '• ' + data.errors.join('\n• ');
        } else if (data.message) {
            errorMessage += data.message;
        } else {
            errorMessage += 'Unknown validation error';
            // Debug: Show what we actually received
            errorMessage += '\nReceived data: ' + JSON.stringify(data, null, 2);
        }
        errorMessage += '\n\nDo you want to force import anyway?';

        const shouldForceImport = confirm(errorMessage);
        if (shouldForceImport) {
            this.forceImportTorrent(originalTorrentData);
        }
    }

    async forceImportTorrent(originalTorrentData) {
        // Show loading for retry
        this.showLoading(true);
        try {
            // Retry with force import
            const forceTorrentData = { ...originalTorrentData, forceImport: true };

            // Convert File object for transmission
            await this.convertTorrentFileForTransmission(forceTorrentData);

            const retryResponse = await this.sendMessage({ action: 'uploadTorrent', data: forceTorrentData });
            if (retryResponse.success) {
                await this.handleUploadResponse(retryResponse.data, forceTorrentData);
            } else {
                this.showMessage('Upload failed: ' + (retryResponse.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            this.showMessage('Upload failed: ' + error.message, 'error');
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
                type: torrentData.torrentFile.type
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

// Initialize popup when DOM is loaded - prevent multiple instances
if (!window.mediaFusionPopupLoaded) {
    window.mediaFusionPopupLoaded = true;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            new PopupManager();
        });
    } else {
        new PopupManager();
    }
}
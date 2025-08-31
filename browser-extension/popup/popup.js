// MediaFusion Browser Extension - Popup Script

class PopupManager {
    constructor() {
        this.selectedMatch = null;
        this.currentTorrentData = null;
        this.isProcessing = false;
        this.requestInProgress = new Set(); // Track specific request types
        this.hasAnalyzed = false; // Prevent duplicate analysis
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.loadSettings();
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

        // Upload functionality with request deduplication
        document.getElementById('analyze-btn').addEventListener('click', () => {
            this.handleAnalyze();
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

    async checkForPrefilledData() {
        // Only check URL parameters (simpler and more reliable)
        const urlParams = new URLSearchParams(window.location.search);
        const magnetLink = urlParams.get('magnet');
        const contentType = urlParams.get('type');
        
        if (magnetLink) {
            document.getElementById('magnet-input').value = decodeURIComponent(magnetLink);
            if (contentType) {
                document.getElementById('content-type').value = contentType;
            }
            // Auto-analyze if we have prefilled data (only once)
            if (!this.hasAnalyzed) {
                this.hasAnalyzed = true;
                setTimeout(() => {
                    this.handleAnalyze();
                }, 500);
            }
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
                    <option value="formula_racing">Formula Racing</option>
                    <option value="hockey">Hockey / NHL</option>
                    <option value="motogp_racing">MotoGP Racing</option>
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
            }
        } catch (error) {
            this.showMessage('Failed to load settings: ' + error.message, 'error');
        }
    }

    async saveSettings() {
        const settings = {
            baseUrl: document.getElementById('mediafusion-url').value.trim() || 'https://mediafusion.elfhosted.com',
            apiPassword: document.getElementById('api-password').value.trim(),
            uploaderName: document.getElementById('default-uploader').value.trim() || 'Anonymous'
        };

        try {
            const response = await this.sendMessage({ action: 'saveSettings', data: settings });
            
            if (response.success) {
                this.showMessage('Settings saved successfully', 'success');
                document.getElementById('uploader-name').value = settings.uploaderName;
                this.checkConnectionStatus();
            } else {
                this.showMessage('Failed to save settings: ' + (response.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            this.showMessage('Failed to save settings: ' + error.message, 'error');
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
            imdbId: document.getElementById('imdb-id').value.trim(),
            title: document.getElementById('title').value.trim(),
            resolution: document.getElementById('resolution').value,
            quality: document.getElementById('quality').value,
            codec: document.getElementById('codec').value.trim(),
            audio: document.getElementById('audio').value.trim()
        };

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
            } else {
                torrentData.torrentFile = basicData.torrentFile;
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
        matchesContainer.innerHTML = '';
        if (data.matches && data.matches.length > 0) {
            const matchesHTML = '<h4>🎯 Found Matches:</h4>';
            matchesContainer.innerHTML = matchesHTML;
            
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
                logo: this.selectedMatch.logo
            };

            const response = await this.sendMessage({ action: 'uploadTorrent', data: torrentData });

            if (response.success) {
                const message = response.data.message || 'Upload completed successfully!';
                this.showMessage(message, 'success');
                this.clearForm();
                this.showBasicUpload();
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
            const response = await this.sendMessage({ action: 'uploadTorrent', data: torrentData });

            if (response.success) {
                const message = response.data.message || 'Upload completed successfully!';
                this.showMessage(message, 'success');
                this.clearForm();
                this.showBasicUpload();
            } else {
                this.showMessage('Upload failed: ' + (response.error || 'Unknown error'), 'error');
            }
        } catch (error) {
            this.showMessage('Upload failed: ' + error.message, 'error');
        } finally {
            this.showLoading(false);
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
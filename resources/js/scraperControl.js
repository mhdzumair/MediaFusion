// ---- Helper Functions ----

function showNotification(message, type = 'info') {
    toastr.options = {
        closeButton: true,
        newestOnTop: true,
        progressBar: true,
        positionClass: "toast-top-center",
        preventDuplicates: true,
        onclick: null,
        showDuration: "1000",
        hideDuration: "3000",
        timeOut: "10000",
        extendedTimeOut: "1000",
        showEasing: "swing",
        hideEasing: "linear",
        showMethod: "fadeIn",
        hideMethod: "fadeOut",
    };

    toastr[type](message);
}

function addGenre() {
    const genreValue = document.getElementById('genreInput').value.trim();
    if (!genreValue) {
        showNotification('Please enter a valid genre.', 'error');
        return;
    }

    const genreIndex = document.getElementById('genreInputs').children.length;
    const genreHtml = `
        <div class="d-flex justify-content-between align-items-center mb-1" id="genre-${genreIndex}">
            <span>${genreValue}</span>
            <button type="button" class="btn btn-danger btn-sm" onclick="removeGenre('genre-${genreIndex}')">Remove</button>
        </div>
    `;

    document.getElementById('genreInputs').insertAdjacentHTML('beforeend', genreHtml);
    document.getElementById('genreInput').value = ''; // Clear input for next genre
}

function removeGenre(genreId) {
    document.getElementById(genreId).remove();
}


function addStreamInput() {
    const streamIndex = document.getElementById('streamInputs').children.length;
    // Adjusted HTML to include 'Optional' guidance in labels
    const streamInputHtml = `
        <div class="card mb-3" id="stream-${streamIndex}">
            <div class="card-body">
                <h5 class="card-title">Stream #${streamIndex + 1}</h5>
                <div class="mb-3">
                    <label for="streamName-${streamIndex}" class="form-label">Name</label>
                    <input type="text" class="form-control" id="streamName-${streamIndex}" name="streamName-${streamIndex}" required>
                </div>
                <div class="mb-3">
                    <label for="streamUrl-${streamIndex}" class="form-label">M3U8 or MPD URL</label>
                    <input type="url" class="form-control" id="streamUrl-${streamIndex}" name="streamUrl-${streamIndex}" oninput="toggleField('streamYtId-${streamIndex}', this.value)">
                </div>
                <div class="mb-3">
                    <label for="streamYtId-${streamIndex}" class="form-label">YouTube Live Stream ID</label>
                    <input type="text" class="form-control" id="streamYtId-${streamIndex}" name="streamYtId-${streamIndex}" oninput="toggleField('streamUrl-${streamIndex}', this.value)">
                </div>
                <div class="mb-3">
                    <label for="streamSource-${streamIndex}" class="form-label">Source</label>
                    <input type="text" class="form-control" id="streamSource-${streamIndex}" name="streamSource-${streamIndex}" required>
                </div>
                <div class="mb-3">
                    <label for="streamCountry-${streamIndex}" class="form-label">Country (Optional)</label>
                    <input type="text" class="form-control" id="streamCountry-${streamIndex}" name="streamCountry-${streamIndex}">
                </div>
                <div class="mb-3">
                    <label class="form-label">Proxy Headers (Optional)</label>
                    <textarea class="form-control" id="streamProxyHeaders-${streamIndex}" name="streamProxyHeaders-${streamIndex}" placeholder="Enter JSON format for proxy headers"></textarea>
                </div>
                <div class="mb-3">
                    <label for="streamDrmKeyId-${streamIndex}" class="form-label">DRM Key ID (Optional)</label>
                    <input type="text" class="form-control" id="streamDrmKeyId-${streamIndex}" name="streamDrmKeyId-${streamIndex}">
                </div>
                <div class="mb-3">
                    <label for="streamDrmKey-${streamIndex}" class="form-label">DRM Key (Optional)</label>
                    <input type="text" class="form-control" id="streamDrmKey-${streamIndex}" name="streamDrmKey-${streamIndex}">
                </div>
                <button type="button" class="btn btn-danger" onclick="removeStreamInput('stream-${streamIndex}')">Remove Stream</button>
            </div>
        </div>
    `;

    document.getElementById('streamInputs').insertAdjacentHTML('beforeend', streamInputHtml);
}


// Function to toggle the disabled state of one input based on the value of another
function toggleField(targetId, sourceValue) {
    const targetField = document.getElementById(targetId);
    targetField.disabled = !!sourceValue;
}


function removeStreamInput(streamId) {
    document.getElementById(streamId).remove();
}

function setElementDisplay(elementId, displayStatus) {
    document.getElementById(elementId).style.display = displayStatus;
}

function resetButton(submitBtn, loadingSpinner) {
    submitBtn.disabled = false;
    loadingSpinner.style.display = 'none';
}


document.querySelectorAll('input[name="m3uInputType"]').forEach(input => {
    input.addEventListener('change', function () {
        if (this.value === 'url') {
            setElementDisplay('m3uPlaylistUrlInput', '');
            setElementDisplay('m3uPlaylistFileInput', 'none');
            document.getElementById('m3uPlaylistFile').value = ''; // Clear file input
        } else {
            setElementDisplay('m3uPlaylistUrlInput', 'none');
            setElementDisplay('m3uPlaylistFileInput', '');
            document.getElementById('m3uPlaylistUrl').value = ''; // Clear URL input
        }
    });
});

function toggleSpiderSpecificFields() {
    const spiderName = document.getElementById('spiderName').value;
    const isTamil = spiderName === 'tamilmv' || spiderName === 'tamil_blasters';
    const isOtherSpider = !isTamil;

    // Display the specific options for TamilMV and TamilBlasters
    setElementDisplay('tamilmvTamilblastersParams', isTamil ? 'block' : 'none');
    setElementDisplay('scrapeAllOption', isOtherSpider ? 'block' : 'none');

    // Initially set to page scraping
    if (isTamil) {
        document.querySelector('input[name="mode"][value="page_scraping"]').checked = true;
        toggleModeSpecificFields();
    }

    // Reset fields when changing mode
    document.getElementById('keyword').value = '';
    document.getElementById('pages').value = '1';
    document.getElementById('startPage').value = '1';
    document.getElementById('totalPages').value = '1';
    document.getElementById('scrape_all').checked = false;
}

function setupPasswordToggle(passwordInputId, toggleButtonId, toggleIconId) {
    document.getElementById(toggleButtonId).addEventListener('click', function (_) {
        const passwordInput = document.getElementById(passwordInputId);
        const passwordIcon = document.getElementById(toggleIconId);
        if (passwordInput.type === "password") {
            passwordInput.type = "text";
            passwordIcon.className = "bi bi-eye-slash";
        } else {
            passwordInput.type = "password";
            passwordIcon.className = "bi bi-eye";
        }
    });
}

function toggleModeSpecificFields() {
    const selectedMode = document.querySelector('input[name="mode"]:checked').value;
    const displayKeywordSearch = selectedMode === 'keyword_search';
    const displayPageScraping = selectedMode === 'page_scraping';

    // Display the appropriate input fields
    setElementDisplay('keywordSearchInput', displayKeywordSearch ? 'block' : 'none');
    setElementDisplay('pageScrapingInput', displayPageScraping ? 'block' : 'none');
}

function showConfirmationDialog(validationErrors, torrentData, infoHash) {
    return new Promise((resolve) => {
        // Remove any existing modal
        const existingModal = document.getElementById('confirmationModal');
        if (existingModal) {
            existingModal.remove();
        }

        const errorMessages = validationErrors.map(error => `<li>${error.message}</li>`).join('');

        const modalHtml = `
            <div class="modal fade" id="confirmationModal" tabindex="-1">
                <div class="modal-dialog">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Validation Warning</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="alert alert-warning">
                                <strong>Community Guidelines:</strong>
                                <ul class="mb-0">
                                    <li>Do not upload adult or inappropriate content</li>
                                    <li>Only upload content that matches the IMDb title</li>
                                    <li>Avoid spamming</li>
                                </ul>
                            </div>

                            <p>Validation issues found:</p>
                            <ul class="text-danger">
                                ${errorMessages}
                            </ul>
                            
                            <div class="form-check mb-3">
                                <input class="form-check-input" type="checkbox" id="confirmGuidelines" required>
                                <label class="form-check-label" for="confirmGuidelines">
                                    I confirm this content follows community guidelines and the metadata is correct
                                </label>
                            </div>

                            <p>Do you want to proceed with the import?</p>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-primary" id="confirmImport" disabled>Confirm Import</button>
                        </div>
                    </div>
                </div>
            </div>`;

        // Add modal to document
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        const modalElement = document.getElementById('confirmationModal');
        const modal = new bootstrap.Modal(modalElement);

        // Enable/disable confirm button based on checkbox
        document.getElementById('confirmGuidelines').addEventListener('change', (e) => {
            document.getElementById('confirmImport').disabled = !e.target.checked;
        });

        // Handle confirmation button click
        document.getElementById('confirmImport').addEventListener('click', () => {
            if (document.getElementById('confirmGuidelines').checked) {
                modal.hide();
                resolve(true);
            }
        });

        // Handle modal hidden event
        modalElement.addEventListener('hidden.bs.modal', () => {
            modal.dispose();
            modalElement.remove();
            resolve(false);
        });

        modal.show();
    });
}

function updateContentType() {
    const metaType = document.getElementById('metaType').value;

    if (metaType === 'sports') {
        setElementDisplay('sportsMetadata', 'block');
        setElementDisplay('torrentImdbIdContainer', 'none');
        setElementDisplay('catalogsSelection', 'none');
    } else {
        setElementDisplay('sportsMetadata', 'none');
        setElementDisplay('torrentImdbIdContainer', 'block');
        setElementDisplay('catalogsSelection', 'block');
        if (metaType === 'movie') {
            setElementDisplay('catalogsSeries', 'none');
            setElementDisplay('catalogsMovie', 'block');
        } else {
            setElementDisplay('catalogsMovie', 'none');
            setElementDisplay('catalogsSeries', 'block');
        }
    }
}

function collectSportsMetadata() {
    return {
        title: document.getElementById('sportsTitle').value,
        year: document.getElementById('sportsYear').value,
        poster: document.getElementById('sportsPoster').value,
        background: document.getElementById('sportsBackground').value,
        logo: document.getElementById('sportsLogo').value,
        description: document.getElementById('sportsDescription').value,
        website: document.getElementById('sportsWebsite').value,
        is_add_title_to_poster: document.getElementById('addTitleToPoster').checked,
        catalogs: document.getElementById('sportsCatalog').value
    };
}

function parseSeasonsInput(input) {
    const seasons = [];
    const parts = input.split(',');

    for (const part of parts) {
        if (part.includes('-')) {
            const [start, end] = part.split('-').map(num => parseInt(num.trim()));
            for (let i = start; i <= end; i++) {
                seasons.push(i);
            }
        } else {
            const season = parseInt(part.trim());
            if (!isNaN(season)) {
                seasons.push(season);
            }
        }
    }

    return seasons;
}

function showFileAnnotationModal(files) {
    const modal = document.getElementById('fileAnnotationModal');
    const fileList = document.getElementById('fileAnnotationList');
    fileList.innerHTML = '';

    const isSportsContent = document.getElementById('metaType').value === 'sports';

    // Sort files by filename
    files.sort((a, b) => {
        return a.filename.localeCompare(b.filename, undefined, {
            numeric: true,
            sensitivity: 'base'
        });
    });

    files.forEach((file, index) => {
        const fileRow = `
            <div class="card mb-3">
                <div class="card-body">
                    <h6 class="card-subtitle mb-2 text-muted">${file.filename}</h6>
                    <div class="row">
                        <div class="col-md-6">
                            <label class="form-label">Season</label>
                            <input type="number" class="form-control season-input" 
                                   id="season-${index}" 
                                   data-index="${index}"
                                   value="${file.season_number || ''}" 
                                   min="1">
                        </div>
                        <div class="col-md-6">
                            <label class="form-label">Episode</label>
                            <input type="number" class="form-control" 
                                   id="episode-${index}" 
                                   value="${file.episode_number || ''}" 
                                   min="1">
                        </div>
                    </div>

                    ${isSportsContent ? `
                    <div class="episode-metadata mt-3">
                        <div class="mb-2">
                            <label class="form-label">Episode Title</label>
                            <input type="text" class="form-control" 
                                   id="title-${index}" 
                                   placeholder="Optional">
                        </div>
                        <div class="mb-2">
                            <label class="form-label">Episode Overview</label>
                            <textarea class="form-control" 
                                      id="overview-${index}" 
                                      rows="2" 
                                      placeholder="Optional"></textarea>
                        </div>
                        <div class="mb-2">
                            <label class="form-label">Thumbnail URL</label>
                            <input type="url" class="form-control" 
                                   id="thumbnail-${index}" 
                                   placeholder="Optional">
                        </div>
                        <div class="mb-2">
                            <label class="form-label">Release Date</label>
                            <input type="date" class="form-control" 
                                   id="release-${index}">
                        </div>
                    </div>
                    ` : `
                    `}
                </div>
            </div>`;
        fileList.insertAdjacentHTML('beforeend', fileRow);
    });

    // Set up bulk season assignment handler
    document.getElementById('applyBulkSeason').onclick = () => {
        const season = document.getElementById('bulkSeason').value;
        if (season) {
            document.querySelectorAll('.season-input').forEach(input => {
                input.value = season;
            });
        }
    };

    // Set up multiple seasons handler
    document.getElementById('applyMultiSeasons').onclick = () => {
        const seasonsInput = document.getElementById('multipleSeasons').value;
        if (!seasonsInput) return;

        const seasons = parseSeasonsInput(seasonsInput);
        if (seasons.length === 0) return;

        const fileGroupingOptions = document.getElementById('fileGroupingOptions');
        fileGroupingOptions.style.display = 'block';

        const applySeasons = () => {
            const distribution = document.querySelector('input[name="seasonDistribution"]:checked').value;
            const episodesPerSeason = parseInt(document.getElementById('episodeCount').value) || 0;

            const seasonInputs = document.querySelectorAll('.season-input');
            if (distribution === 'auto') {
                // Distribute episodes evenly across seasons
                const filesPerSeason = Math.ceil(seasonInputs.length / seasons.length);
                seasonInputs.forEach((input, index) => {
                    const seasonIndex = Math.floor(index / filesPerSeason);
                    input.value = seasons[Math.min(seasonIndex, seasons.length - 1)];
                });
            } else {
                // Manual distribution based on episodes per season
                if (episodesPerSeason > 0) {
                    seasonInputs.forEach((input, index) => {
                        const seasonIndex = Math.floor(index / episodesPerSeason);
                        input.value = seasons[Math.min(seasonIndex, seasons.length - 1)];
                    });
                }
            }
        };

        // Set up distribution method handlers
        document.querySelectorAll('input[name="seasonDistribution"]').forEach(radio => {
            radio.onchange = () => {
                document.getElementById('episodesPerSeason').style.display =
                    radio.value === 'manual' ? 'block' : 'none';
                if (radio.value === 'auto') {
                    applySeasons();
                }
            };
        });

        document.getElementById('episodeCount').onchange = () => {
            if (document.getElementById('manualGroup').checked) {
                applySeasons();
            }
        };

        // Initial application
        applySeasons();
    };

    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();

    return new Promise((resolve, reject) => {
        document.getElementById('confirmAnnotation').onclick = () => {
            const annotatedFiles = files.map((file, index) => {
                const baseData = {
                    ...file,
                    season_number: parseInt(document.getElementById(`season-${index}`).value) || null,
                    episode_number: parseInt(document.getElementById(`episode-${index}`).value) || null,
                };

                if (isSportsContent) {
                    return {
                        ...baseData,
                        title: document.getElementById(`title-${index}`).value || null,
                        overview: document.getElementById(`overview-${index}`).value || null,
                        thumbnail: document.getElementById(`thumbnail-${index}`).value || null,
                        release_date: document.getElementById(`release-${index}`).value || null,

                    };
                }
                return baseData;
            });
            bsModal.hide();
            resolve(annotatedFiles);
        };

        modal.addEventListener('hidden.bs.modal', () => {
            reject(new Error('Annotation cancelled'));
        }, {once: true});
    });
}


function toggleInput(disableId, input) {
    document.getElementById(disableId).disabled = !!input.value;
}


// Function to update form fields based on scraper selection
function updateFormFields() {
    // Hide all sections initially
    setElementDisplay('scrapyParameters', 'none');
    setElementDisplay('tvMetadataInput', 'none');
    setElementDisplay('m3uPlaylistInput', 'none');
    setElementDisplay("imdbDataParameters", "none");
    setElementDisplay("torrentUploadParameters", "none");
    setElementDisplay("apiPasswordContainer", "none");
    setElementDisplay('blockTorrentParameters', 'none');

    // Get the selected scraper type
    const scraperType = document.getElementById('scraperSelect').value;
    let authRequired = document.getElementById('apiPasswordEnabled').value === "true";

    // Show the relevant section based on the selected scraper type
    switch (scraperType) {
        case 'add_torrent':
            setElementDisplay("torrentUploadParameters", "block");
            authRequired = false;
            updateContentType();
            break;
        case 'scrapy':
            // Show Scrapy-specific parameters
            setElementDisplay('scrapyParameters', 'block');
            toggleSpiderSpecificFields();
            authRequired = false;
            break;
        case 'add_tv_metadata':
            // Show TV Metadata input form
            setElementDisplay('tvMetadataInput', 'block');
            // Ensure a stream input is displayed initially
            if (document.getElementById('streamInputs').children.length === 0) {
                addStreamInput();
            }
            break;
        case 'add_m3u_playlist':
            setElementDisplay('m3uPlaylistInput', 'block');
            break;
        case 'update_imdb_data':
            setElementDisplay("imdbDataParameters", "block");
            authRequired = false;
            break;
        case 'block_torrent':
            setElementDisplay("blockTorrentParameters", "block");
            break;
        default:
            // Optionally handle any default cases if needed
            break;
    }

    // Setup password toggle if the API password field is displayed
    if (authRequired) {
        setElementDisplay("apiPasswordContainer", "block");
        setupPasswordToggle('api_password', 'toggleApiPassword', 'toggleApiPasswordIcon');
    }

}

function constructTvMetadata() {
    // Basic TV Metadata collection
    let tvMetaData = {
        title: document.getElementById('title').value.trim(),
        poster: document.getElementById('poster').value.trim(),
        background: document.getElementById('background').value.trim(),
        country: document.getElementById('country').value.trim(),
        tv_language: document.getElementById('language').value.trim(),
        logo: document.getElementById('logo').value.trim(),
        genres: [],
        streams: []
    };

    // Collecting Genres
    document.querySelectorAll('#genreInputs div span').forEach(genre => {
        tvMetaData.genres.push(genre.textContent);
    });

    // Validating and Collecting Streams
    const streamContainers = document.querySelectorAll('#streamInputs .card');
    streamContainers.forEach((container, index) => {
        console.log(`streamName-${index}`);
        let stream = {
            name: document.getElementById(`streamName-${index}`).value.trim(),
            url: document.getElementById(`streamUrl-${index}`).value.trim(),
            ytId: document.getElementById(`streamYtId-${index}`).value.trim(),
            source: document.getElementById(`streamSource-${index}`).value.trim(),
            country: document.getElementById(`streamCountry-${index}`).value.trim(),
            drm_key_id: document.getElementById(`streamDrmKeyId-${index}`).value.trim(),
            drm_key: document.getElementById(`streamDrmKey-${index}`).value.trim(),
            behaviorHints: {
                proxyHeaders: document.getElementById(`streamProxyHeaders-${index}`) ? JSON.parse(document.getElementById(`streamProxyHeaders-${index}`).value || "{}") : {},
                notWebReady: true
            }
        };

        // Basic validation to ensure at least one of URL or YouTube ID is provided
        if (!stream.url && !stream.ytId) {
            throw new Error(`Stream #${index + 1}: Either a URL or a YouTube ID is required.`);
        }

        tvMetaData.streams.push(stream);
    });

    // Additional validation logic as necessary
    if (tvMetaData.title === '') {
        throw new Error('Title is required.');
    }

    // Assuming at least one stream is required
    if (tvMetaData.streams.length === 0) {
        throw new Error('At least one stream is required.');
    }

    return tvMetaData;
}

async function handleAddTorrent(submitBtn, loadingSpinner, forceImport = false, annotatedFiles = null) {
    let formData = new FormData();
    const metaType = document.getElementById('metaType').value;
    const isSportsContent = metaType === 'sports';

    // Handle sports content metadata
    if (isSportsContent) {
        const sportsMetadata = collectSportsMetadata();

        // Validate required fields
        if (!sportsMetadata.title || !sportsMetadata.poster || !sportsMetadata.catalogs) {
            showNotification('Title, poster, and sports category are required.', 'error');
            resetButton(submitBtn, loadingSpinner);
            return;
        }

        // Add sports metadata to formData
        Object.entries(sportsMetadata).forEach(([key, value]) => {
            if (value !== null && value !== '') {
                formData.append(key, value);
            }
        });

        // Set meta_type based on catalog
        const isSeriesType = ['formula_racing', 'motogp_racing'].includes(sportsMetadata.catalog);
        formData.append('meta_type', isSeriesType ? 'series' : 'movie');
        formData.append('is_sports_content', 'true');
    } else {
        // Handle regular IMDb content
        const imdbId = document.getElementById('torrentImdbId').value;
        const imdbIdNumeric = parseInt(imdbId.slice(2), 10);
        if (!imdbId.startsWith('tt') || imdbId.length < 3 || imdbId.length > 10 || isNaN(imdbIdNumeric)) {
            showNotification('Invalid IMDb ID', 'error');
            resetButton(submitBtn, loadingSpinner);
            return;
        }
        formData.append('meta_id', imdbId);
        formData.append('meta_type', metaType);

        // Handle optional catalogs
        const catalogInputs = metaType === 'movie'
            ? document.querySelectorAll('#catalogsMovie input[name="catalogs"]:checked')
            : document.querySelectorAll('#catalogsSeries input[name="catalogs"]:checked');

        const catalogs = Array.from(catalogInputs).map(el => el.value);
        if (catalogs.length > 0) {
            formData.append('catalogs', catalogs.join(','));
        }
        const selectedLanguages = Array.from(document.querySelectorAll('input[name="languages"]:checked'))
            .map(el => el.value);
        if (selectedLanguages.length > 0) {
            formData.append('languages', selectedLanguages.join(','));
        }
    }

    // Handle common form fields
    const createdAt = document.getElementById('createdAt').value;
    if (!createdAt) {
        showNotification('Created At is required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }
    formData.append('created_at', createdAt);

    // Handle torrent file/magnet
    const magnetLink = document.getElementById('magnetLink').value;
    const torrentFile = document.getElementById('torrentFile').files[0];
    const torrentType = document.getElementById('torrentType').value;

    if (!magnetLink && !torrentFile) {
        showNotification('Either Magnet Link or Torrent File is required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }

    if (torrentType !== 'public' && !torrentFile) {
        showNotification(`Torrent File is required for ${torrentType} torrents.`, 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }

    formData.append('torrent_type', torrentType);
    if (magnetLink) {
        formData.append('magnet_link', magnetLink);
    } else {
        formData.append('torrent_file', torrentFile);
    }

    // Add force import flag if needed
    if (forceImport) {
        formData.append('force_import', 'true');
    }

    // Add uploader name
    const uploaderName = document.getElementById('uploaderName').value.trim() || 'Anonymous';
    formData.append('uploader', uploaderName);

    // Add annotated files if available
    if (annotatedFiles) {
        formData.append('file_data', JSON.stringify(annotatedFiles));
    }

    try {
        const response = await fetch('/scraper/torrent', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (data.status === 'needs_annotation') {
            try {
                // Show modal for file annotation
                const newAnnotatedFiles = await showFileAnnotationModal(data.files);
                // Retry with the annotated files
                await handleAddTorrent(submitBtn, loadingSpinner, forceImport, newAnnotatedFiles);
                return;
            } catch (annotationError) {
                console.error('Error annotating files:', annotationError);
                showNotification('File annotation was cancelled', 'warning');
            }
        } else if (data.status === 'validation_failed' && !forceImport) {
            // Show confirmation dialog but preserve the annotated files
            const shouldForceImport = await showConfirmationDialog(
                data.errors,
                data.torrent_data,
                data.info_hash
            );

            if (shouldForceImport) {
                // Pass the existing annotated files to the next attempt
                await handleAddTorrent(submitBtn, loadingSpinner, true, annotatedFiles);
                return;
            }
        } else if (data.status === 'validation_failed' && forceImport) {
            showNotification(`Validation failed: ${JSON.stringify(data.errors)}`, 'error');
        } else if (data.detail) {
            showNotification(data.detail, 'error');
        } else {
            showNotification(data.status, 'success');
        }
    } catch (error) {
        console.error('Error submitting torrent:', error);
        showNotification(`Error submitting torrent: ${error.toString()}`, 'error');
    } finally {
        resetButton(submitBtn, loadingSpinner);
    }
}

async function handleAddTvMetadata(payload, submitBtn, loadingSpinner) {
    try {
        payload['tv_metadata'] = constructTvMetadata();
        const response = await fetch('/scraper/add_tv_metadata', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (data.detail) {
            showNotification(data.detail, 'error');
        } else {
            showNotification(data.status, 'success');
        }
    } catch (error) {
        console.error('Error constructing TV Metadata:', error);
        showNotification(error.message, 'error');
    } finally {
        resetButton(submitBtn, loadingSpinner);
    }
}

async function handleAddM3uPlaylist(apiPassword, submitBtn, loadingSpinner) {
    let formData = new FormData();
    formData.append('scraper_type', 'add_m3u_playlist');
    formData.append('api_password', apiPassword);
    formData.append('m3u_playlist_source', document.getElementById('m3uPlaylistSource').value);

    const inputType = document.querySelector('input[name="m3uInputType"]:checked').value;
    if (inputType === 'url') {
        const m3uUrl = document.getElementById('m3uPlaylistUrl').value.trim();
        if (!m3uUrl) {
            showNotification('M3U Playlist URL is required.', 'error');
            resetButton(submitBtn, loadingSpinner);
            return;
        }
        formData.append('m3u_playlist_url', m3uUrl);
    } else {
        const file = document.getElementById('m3uPlaylistFile').files[0];
        if (!file) {
            showNotification('M3U Playlist file is required.', 'error');
            resetButton(submitBtn, loadingSpinner);
            return;
        }
        formData.append('m3u_playlist_file', file);
    }

    try {
        const response = await fetch('/scraper/m3u_upload', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();
        if (data.detail) {
            showNotification(data.detail, 'error');
        } else {
            showNotification(data.status, 'success');
        }
    } catch (error) {
        console.error('Error submitting scraper form:', error);
        showNotification(`Error submitting scraper form. Error: ${error.stringify()}`, 'error');
    } finally {
        resetButton(submitBtn, loadingSpinner);
    }
}

async function handleUpdateImdbData(submitBtn, loadingSpinner) {
    const imdbId = document.getElementById('imdbId').value;
    const imdbIdNumeric = parseInt(imdbId.slice(2), 10);
    if (!imdbId.startsWith('tt') || imdbId.length < 3 || imdbId.length > 10 || isNaN(imdbIdNumeric)) {
        showNotification('Invalid IMDb ID', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }
    const imdbType = document.getElementById('imdbType').value;

    try {
        const response = await fetch(`/scraper/imdb_data?meta_id=${imdbId}&media_type=${imdbType}`, {
            method: 'GET',
            headers: {'Content-Type': 'application/json'}
        });

        const data = await response.json();
        if (data.detail) {
            showNotification(data.detail, 'error');
        } else {
            showNotification(data.status, 'success');
        }
    } catch (error) {
        console.error('Error submitting scraper form:', error);
        showNotification(`Error submitting scraper form. Error: ${error.stringify()}`, 'error');
    } finally {
        resetButton(submitBtn, loadingSpinner);
    }
}

async function handleScrapyParameters(payload, submitBtn, loadingSpinner) {
    document.querySelectorAll('#scrapyParameters input, #scrapyParameters select').forEach(input => {
        if (!input.disabled && input.type !== 'radio' && input.type !== 'checkbox') {
            payload[input.name] = input.value;
        } else if (input.type === 'radio' && input.checked) {
            payload[input.name] = input.value;
        } else if (input.type === 'checkbox') {
            payload[input.name] = input.checked;
        }
    });

    try {
        const response = await fetch('/scraper/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (data.detail) {
            showNotification(data.detail, 'error');
        } else {
            showNotification(data.status, 'success');
        }
    } catch (error) {
        console.error('Error submitting scraper form:', error);
        showNotification(`Error submitting scraper form. Error: ${error.stringify()}`, 'error');
    } finally {
        resetButton(submitBtn, loadingSpinner);
    }
}

async function handleBlockTorrent(apiPassword, submitBtn, loadingSpinner) {
    const infoHash = document.getElementById('blockTorrentInfoHash').value.trim();
    if (!infoHash) {
        showNotification('Torrent Info Hash is required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }

    try {
        const response = await fetch('/scraper/block_torrent', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                info_hash: infoHash,
                api_password: apiPassword
            })
        });

        const data = await response.json();
        if (data.detail) {
            showNotification(data.detail, 'error');
        } else {
            showNotification(data.status, 'success');
        }
    } catch (error) {
        console.error('Error blocking torrent:', error);
        showNotification(`Error blocking torrent. Error: ${error.toString()}`, 'error');
    } finally {
        resetButton(submitBtn, loadingSpinner);
    }
}

// Main function
async function submitScraperForm() {
    const apiPassword = document.getElementById('api_password').value;
    const scraperType = document.getElementById('scraperSelect').value;
    const submitBtn = document.getElementById('submitBtn');
    const loadingSpinner = document.getElementById('loadingSpinner');
    let payload = {scraper_type: scraperType, api_password: apiPassword};

    // Disable button and show loading spinner
    submitBtn.disabled = true;
    loadingSpinner.style.display = 'inline-block';

    // Call the appropriate handler based on scraper type
    switch (scraperType) {
        case 'add_torrent':
            await handleAddTorrent(submitBtn, loadingSpinner);
            break;
        case 'add_tv_metadata':
            await handleAddTvMetadata(payload, submitBtn, loadingSpinner);
            break;
        case 'add_m3u_playlist':
            await handleAddM3uPlaylist(apiPassword, submitBtn, loadingSpinner);
            break;
        case 'update_imdb_data':
            await handleUpdateImdbData(submitBtn, loadingSpinner);
            break;
        case 'block_torrent':
            await handleBlockTorrent(apiPassword, submitBtn, loadingSpinner);
            break;
        default:
            await handleScrapyParameters(payload, submitBtn, loadingSpinner);
            break;
    }
}


// Initial update for form fields on page load
document.addEventListener('DOMContentLoaded', function () {
    updateFormFields();
});
document.getElementById('spiderName').addEventListener('change', toggleSpiderSpecificFields);
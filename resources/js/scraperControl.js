// Define spec options
const SPEC_OPTIONS = {
    resolution: [
        {value: '480p', label: '480p (SD)'},
        {value: '576p', label: '576p (SD)'},
        {value: '720p', label: '720p (HD)'},
        {value: '1080p', label: '1080p (Full HD)'},
        {value: '1440p', label: '1440p (2K)'},
        {value: '2160p', label: '2160p (4K)'},
        {value: '4K', label: '4K UHD'}
    ],
    quality: [
        {value: 'BluRay', label: 'BluRay'},
        {value: 'BluRay REMUX', label: 'BluRay REMUX'},
        {value: 'BRRip', label: 'BRRip'},
        {value: 'BDRip', label: 'BDRip'},
        {value: 'WEB-DL', label: 'WEB-DL'},
        {value: 'HDRip', label: 'HDRip'},
        {value: 'DVDRip', label: 'DVDRip'},
        {value: 'HDTV', label: 'HDTV'},
        {value: 'CAM', label: 'CAM'},
        {value: 'TeleSync', label: 'TeleSync'},
        {value: 'SCR', label: 'SCR'}
    ],
    codec: [
        {value: 'x264', label: 'x264'},
        {value: 'x265', label: 'x265 (HEVC)'},
        {value: 'h.264', label: 'H.264 (AVC)'},
        {value: 'h.265', label: 'H.265 (HEVC)'},
        {value: 'hevc', label: 'HEVC'},
        {value: 'avc', label: 'AVC'},
        {value: 'mpeg-2', label: 'MPEG-2'},
        {value: 'mpeg-4', label: 'MPEG-4'},
        {value: 'vp9', label: 'VP9'}
    ],
    audio: [
        {value: 'AAC', label: 'AAC'},
        {value: 'AC3', label: 'AC3 (Dolby Digital)'},
        {value: 'DTS', label: 'DTS'},
        {value: 'DTS-HD MA', label: 'DTS-HD MA'},
        {value: 'TrueHD', label: 'Dolby TrueHD'},
        {value: 'Atmos', label: 'Dolby Atmos'},
        {value: 'DD+', label: 'DD+'},
        {value: 'Dolby Digital Plus', label: 'Dolby Digital Plus'},
        {value: 'DTS Lossless', label: 'DTS Lossless'}
    ],
    hdr: [
        {value: 'DV', label: 'Dolby Vision'},
        {value: 'HDR10+', label: 'HDR10+'},
        {value: 'HDR', label: 'HDR'},
        {value: 'SDR', label: 'SDR'}
    ]
};

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

    // Hide all catalog sections first
    setElementDisplay('movieCatalogs', 'none');
    setElementDisplay('seriesCatalogs', 'none');
    setElementDisplay('sportsCatalogs', 'none');

    // Show relevant catalog section based on content type
    if (metaType === 'movie') {
        setElementDisplay('movieCatalogs', 'block');
        setElementDisplay('metaIdContainer', 'block');
    } else if (metaType === 'series') {
        setElementDisplay('seriesCatalogs', 'block');
        setElementDisplay('metaIdContainer', 'block');
    } else if (metaType === 'sports') {
        setElementDisplay('sportsCatalogs', 'block');
        setElementDisplay('metaIdContainer', 'none');
    }
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
            <div class="card mb-3" id="file-row-${index}">
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <div class="d-flex align-items-center flex-grow-1">
                            <!-- Include/Exclude Toggle -->
                            <div class="form-check form-switch me-2">
                                <input class="form-check-input" type="checkbox" 
                                       id="include-file-${index}" 
                                       checked
                                       onchange="toggleFileInclusion(${index})">
                                <label class="form-check-label" for="include-file-${index}">
                                    Include
                                </label>
                            </div>
                            <h6 class="card-subtitle mb-0 text-muted text-truncate" 
                                style="max-width: 80%;" 
                                title="${file.filename}">
                                ${file.filename}
                            </h6>
                        </div>
                    </div>
                    <div class="row" id="file-inputs-${index}">
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
                            <input type="text" class="form-control" 
                                   id="release-${index}"
                                   placeholder="DD/MM/YYYY">
                        </div>
                    </div>
                    ` : ''}
                </div>
            </div>`;
        fileList.insertAdjacentHTML('beforeend', fileRow);
    });

    files.forEach((file, index) => {
        // Initialize date picker for each file
        setupDateInput(`release-${index}`, false);
    });

    // Set up bulk season assignment handler
    document.getElementById('applyBulkSeason').onclick = () => {
        const season = document.getElementById('bulkSeason').value;
        if (season) {
            document.querySelectorAll('.season-input:not([disabled])').forEach(input => {
                // Only apply to included files
                const index = input.dataset.index;
                const isIncluded = document.getElementById(`include-file-${index}`)?.checked;
                if (isIncluded) {
                    input.value = season;
                }
            });
        }
    };

    // Set up multiple seasons handler with the same included files logic
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

            // Filter for only included files
            const seasonInputs = Array.from(document.querySelectorAll('.season-input:not([disabled])'))
                .filter(input => {
                    const index = input.dataset.index;
                    return document.getElementById(`include-file-${index}`)?.checked;
                });

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

        // Distribution method handlers
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

        applySeasons();
    };

    const bsModal = new bootstrap.Modal(modal);
    bsModal.show();

    return new Promise((resolve, reject) => {
        document.getElementById('confirmAnnotation').onclick = () => {
            const annotatedFiles = [];
            files.forEach((file, index) => {
                // Only include files that are marked for inclusion
                if (document.getElementById(`include-file-${index}`)?.checked) {
                    const baseData = {
                        ...file,
                        season_number: parseInt(document.getElementById(`season-${index}`).value) || null,
                        episode_number: parseInt(document.getElementById(`episode-${index}`).value) || null,
                    };

                    if (isSportsContent) {
                        const releaseDate = document.getElementById(`release-${index}`).value;
                        if (releaseDate) {
                            const dateParts = releaseDate.split('/');
                            const date = new Date(`${dateParts[1]}/${dateParts[0]}/${dateParts[2]}`);
                            baseData.release_date = date.toISOString().split('T')[0]
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
            bsModal.hide();
            resolve(annotatedFiles);
        };

        modal.addEventListener('hidden.bs.modal', () => {
            reject(new Error('Annotation cancelled'));
        }, {once: true});
    });
}

// Function to toggle file inclusion
function toggleFileInclusion(index) {
    const checkbox = document.getElementById(`include-file-${index}`);
    const inputsContainer = document.getElementById(`file-inputs-${index}`);
    const fileRow = document.getElementById(`file-row-${index}`);

    if (checkbox.checked) {
        // File is included
        inputsContainer.style.opacity = '1';
        inputsContainer.style.pointerEvents = 'auto';
        fileRow.classList.remove('excluded-file');
        // Enable all inputs in the container
        inputsContainer.querySelectorAll('input, textarea').forEach(input => {
            input.disabled = false;
        });
    } else {
        // File is excluded
        inputsContainer.style.opacity = '0.5';
        inputsContainer.style.pointerEvents = 'none';
        fileRow.classList.add('excluded-file');
        // Disable all inputs in the container
        inputsContainer.querySelectorAll('input, textarea').forEach(input => {
            input.disabled = true;
        });
    }
}


function toggleInput(disableId, input) {
    document.getElementById(disableId).disabled = !!input.value;
}


// Function to update form fields based on scraper selection
function updateFormFields() {
    // Hide all sections initially
    setElementDisplay('quickImportParameters', 'none');
    setElementDisplay('scrapyParameters', 'none');
    setElementDisplay('tvMetadataInput', 'none');
    setElementDisplay('m3uPlaylistInput', 'none');
    setElementDisplay("imdbDataParameters", "none");
    setElementDisplay("torrentUploadParameters", "none");
    setElementDisplay("apiPasswordContainer", "none");
    setElementDisplay('blockTorrentParameters', 'none');
    setElementDisplay('migrationParameters', 'none');
    setElementDisplay('updateImagesParameters', 'none');

    // Get the selected scraper type
    const scraperType = document.getElementById('scraperSelect').value;
    let authRequired = document.getElementById('apiPasswordEnabled').value === "true";

    // Show the relevant section based on the selected scraper type
    switch (scraperType) {
        case 'quick_import':
            setElementDisplay('quickImportParameters', 'block');
            authRequired = false;
            break;
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
        case 'migrate_id':
            setElementDisplay('migrationParameters', 'block');
            authRequired = false;
            break;
        case 'update_images':
            setElementDisplay('updateImagesParameters', 'block');
            authRequired = false;
            break;
    }

    // Setup password toggle if the API password field is displayed
    if (authRequired) {
        setElementDisplay("apiPasswordContainer", "block");
        setupPasswordToggle('api_password', 'toggleApiPassword', 'toggleApiPasswordIcon');
    }

}

function handleInitialSetup() {
    const urlParams = new URLSearchParams(window.location.search);
    const action = urlParams.get('action');

    // Set initial scraper type based on action
    if (action) document.getElementById('scraperSelect').value = action;

    // Update form fields based on initial selection
    updateFormFields();
}


function setupDateInput(inputId, defaultToToday = false, initialDate = null) {
    const fp = flatpickr(`#${inputId}`, {
        dateFormat: "d/m/Y",
        altInput: true,
        altFormat: "d/m/Y",
        allowInput: true,
        defaultHour: 12,
        maxDate: "today",
        locale: {
            firstDayOfWeek: 1
        },
        theme: "dark",
        defaultDate: initialDate // Set initial date if provided
    });

    if (defaultToToday && !initialDate) {
        const today = new Date();
        fp.setDate(today);
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

async function handleQuickImport() {
    const metaType = document.getElementById('quickMetaType').value;
    const torrentFile = document.getElementById('quickTorrentFile').files[0];
    const magnetLink = document.getElementById('quickMagnetLink').value.trim();

    if (!torrentFile && !magnetLink) {
        showNotification('Please provide either a torrent file or magnet link', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('meta_type', metaType);

    if (torrentFile) {
        formData.append('torrent_file', torrentFile);
    } else {
        formData.append('magnet_link', magnetLink);
    }

    try {
        setElementDisplay('analysisLoading', 'block');
        setElementDisplay('matchResults', 'none');

        const response = await fetch('/scraper/analyze_torrent', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();
        if (response.ok) {
            if (data.matches && data.matches.length > 0) {
                displayMatchResults(data.matches, data.torrent_data);
            } else {
                showNotification('No matches found. Try manual import.', 'warning');
                switchToManualImport(data.torrent_data);
            }
        } else {
            showNotification(data.detail || 'Failed to analyze torrent', 'error');
        }
    } catch (error) {
        showNotification('Error analyzing torrent: ' + error.message, 'error');
    } finally {
        setElementDisplay('analysisLoading', 'none');
    }
}

function displayMatchResults(matches, torrentData) {
    const container = document.getElementById('matchResultsContent');
    container.innerHTML = '';

    if (matches && matches.length > 0) {
        matches.forEach(match => {
            const safeMatchData = btoa(encodeURIComponent(JSON.stringify(match)));
            const safeTorrentData = btoa(encodeURIComponent(JSON.stringify(torrentData)));

            // Format runtime nicely
            const runtime = match.runtime ? match.runtime.replace('min', '').trim() + ' minutes' : 'N/A';

            // Format rating with stars
            const rating = match.imdb_rating ?
                `<i class="bi bi-star-fill text-warning"></i> ${match.imdb_rating}/10` :
                '<span class="badge bg-secondary">No rating</span>';

            // Handle multiple AKA titles
            const akaTitles = match.aka_titles && match.aka_titles.length > 0 ?
                `<div class="small mb-1">Also known as: ${match.aka_titles.join(' • ')}</div>` : '';

            const matchHtml = `
                <div class="list-group-item list-group-item-action p-3">
                    <div class="d-flex">
                        <!-- Poster Section -->
                        <div class="flex-shrink-0 me-3">
                            <img src="${match.poster || '/static/img/placeholder.jpg'}" 
                                 alt="${match.title}"
                                 class="rounded shadow-sm" 
                                 style="width: 120px; height: 180px; object-fit: cover;">
                        </div>
                        
                        <!-- Content Section -->
                        <div class="flex-grow-1">
                            <!-- Header -->
                            <div class="d-flex justify-content-between align-items-start mb-2">
                                <div>
                                    <h5 class="mb-1">${match.title} 
                                        <span class="text-muted">(${match.year})</span>
                                    </h5>
                                    ${akaTitles}
                                </div>
                                <div>
                                    ${rating}
                                </div>
                            </div>

                            <!-- Meta Information -->
                            <div class="d-flex flex-wrap gap-3 mb-2">
                                <div class="d-flex align-items-center">
                                    <i class="bi bi-film me-1"></i>
                                    <span>${match.type.charAt(0).toUpperCase() + match.type.slice(1)}</span>
                                </div>
                                <div class="d-flex align-items-center">
                                    <i class="bi bi-clock me-1"></i>
                                    <span>${runtime}</span>
                                </div>
                                <div class="d-flex align-items-center">
                                    <i class="bi bi-fingerprint me-1"></i>
                                    <span>${match.imdb_id}</span>
                                </div>
                            </div>

                            <!-- Description -->
                            <p class="mb-2 text-muted">${match.description || 'No description available.'}</p>

                            <!-- Tags Section -->
                            <div class="mb-3">
                                <div class="d-flex flex-wrap gap-2">
                                    ${match.genres.map(genre =>
                `<span class="badge bg-primary bg-opacity-25 text-primary">${genre}</span>`
            ).join('')}
                                </div>
                            </div>

                            <!-- Additional Information -->
                            <div class="row g-3 mb-3">
                                <div class="col-md-6">
                                    <div class="d-flex align-items-center">
                                        <i class="bi bi-globe2 me-2"></i>
                                        <small>${match.countries.join(', ') || 'N/A'}</small>
                                    </div>
                                </div>
                                <div class="col-md-6">
                                    <div class="d-flex align-items-center">
                                        <i class="bi bi-translate me-2"></i>
                                        <small>${match.languages.join(', ') || 'N/A'}</small>
                                    </div>
                                </div>
                            </div>

                            <!-- Stars/Cast -->
                            ${match.stars && match.stars.length > 0 ? `
                            <div class="mb-3">
                                <div class="d-flex align-items-center gap-2">
                                    <i class="bi bi-person-circle"></i>
                                    <small class="text-muted">${match.stars.join(', ')}</small>
                                </div>
                            </div>
                            ` : ''}

                            <!-- Action Buttons -->
                            <div class="d-flex justify-content-end gap-2">
                                <button class="btn btn-primary btn-sm"
                                        data-match='${safeMatchData}'
                                        data-torrent='${safeTorrentData}'
                                        onclick="selectMatchFromData(this)">
                                    <i class="bi bi-check2 me-1"></i>
                                    Select This Match
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `;
            container.insertAdjacentHTML('beforeend', matchHtml);
        });
        setElementDisplay('matchResults', 'block');
    } else {
        showNotification('No matches found. Try manual import.', 'warning');
        switchToManualImport(torrentData);
    }
}

function selectMatchFromData(button) {
    try {
        const matchData = button.getAttribute('data-match');
        const torrentData = button.getAttribute('data-torrent');

        // Decode using modern approach
        const match = JSON.parse(decodeURIComponent(atob(matchData)));
        const torrent = JSON.parse(decodeURIComponent(atob(torrentData)));

        selectMatch(match, torrent);
    } catch (error) {
        console.error('Error parsing match data:', error);
        showNotification('Error processing match data', 'error');
    }
}

function formatTechnicalSpec(value, type) {
    if (!value) return '<span class="not-available">Not Set</span>';

    switch (type) {
        case 'resolution':
            return `<span class="spec-value">${value}</span>`;
        case 'audio' || 'languages' || 'hdr':
            return Array.isArray(value)
                ? value.join(', ')
                : value.split(',').join(', ');
        default:
            return value;
    }
}

function updateBasicTechnicalSpecs(torrentData = {}) {
    const specsContainer = document.querySelector('.technical-specs-basic');
    if (!specsContainer) return;

    const specs = [
        {icon: 'bi-display', label: 'Resolution', value: torrentData.resolution, type: 'resolution'},
        {icon: 'bi-camera-reels', label: 'Quality', value: torrentData.quality, type: 'quality'},
        {icon: 'bi-film', label: 'Video Codec', value: torrentData.codec, type: 'codec'},
        {icon: 'bi-music-note-beamed', label: 'Audio', value: torrentData.audio, type: 'audio'},
        {icon: 'bi-translate', label: 'Languages', value: torrentData.languages, type: 'languages'},
        {icon: 'bi-tv', label: 'HDR', value: torrentData.hdr, type: 'hdr'},
    ];

    specsContainer.innerHTML = specs.map(spec => `
        <div class="spec-item" data-spec-type="${spec.type}">
            <i class="bi ${spec.icon}"></i>
            <div>
                <div class="spec-label">${spec.label}</div>
                <div class="spec-value" id="${spec.type}Spec">${formatTechnicalSpec(spec.value, spec.type)}</div>
            </div>
        </div>
    `).join('');

    // Add click handlers to each spec item
    specsContainer.querySelectorAll('.spec-item').forEach(item => {
        item.addEventListener('click', function () {
            const specType = this.dataset.specType;
            const modal = new bootstrap.Modal(document.getElementById('specSelectionModal'));
            const modalTitle = document.getElementById('specModalTitle');
            const singleSelection = document.getElementById('singleSpecSelection');
            const multipleSelection = document.getElementById('multipleSpecSelection');

            // Determine if this is a multi-select specification
            const isMultiSelect = ['audio', 'hdr', 'languages'].includes(specType);

            // Update modal title
            modalTitle.textContent = `Select ${this.querySelector('.spec-label').textContent}`;

            // Show appropriate selection interface
            singleSelection.style.display = isMultiSelect ? 'none' : 'block';
            multipleSelection.style.display = isMultiSelect ? 'block' : 'none';

            // Get current value
            const currentValue = this.querySelector('.spec-value').textContent;

            // Populate options based on spec type
            if (isMultiSelect) {
                populateMultiSelect(specType, currentValue);
            } else {
                populateSingleSelect(specType, currentValue);
            }

            // Set up confirmation handler
            document.getElementById('confirmSpecSelection').onclick = () => {
                let selectedValue;
                if (isMultiSelect) {
                    selectedValue = Array.from(document.querySelectorAll('.spec-checkboxes input:checked'))
                        .map(cb => cb.value)
                        .join(', ');
                } else {
                    selectedValue = document.getElementById('specSelectSingle').value;
                }

                if (selectedValue) {
                    // Update the spec display
                    updateSpecField(specType, selectedValue);
                }
                modal.hide();
            };

            modal.show();
        });
    });
}

// Update populateSingleSelect to use new options format
function populateSingleSelect(specType, currentValue) {
    const select = document.getElementById('specSelectSingle');
    select.innerHTML = '<option value="">Select an option</option>';

    const options = getOptionsForSpecType(specType);
    options.forEach(option => {
        const opt = document.createElement('option');
        opt.value = option.value;
        opt.textContent = option.label;
        opt.selected = currentValue === option.value;
        select.appendChild(opt);
    });
}

// Update populateMultiSelect to use new options format
function populateMultiSelect(specType, currentValue) {
    const container = document.querySelector('.spec-checkboxes');
    container.innerHTML = '';

    const currentValues = currentValue.split(', ').map(v => v.trim());
    const options = getOptionsForSpecType(specType);

    options.forEach(option => {
        const div = document.createElement('div');
        div.className = 'form-check';
        div.innerHTML = `
            <input class="form-check-input" type="checkbox" value="${option.value}" 
                   id="spec-${option.value.replace(/[^a-zA-Z0-9]/g, '')}"
                   ${currentValues.includes(option.value) ? 'checked' : ''}>
            <label class="form-check-label" for="spec-${option.value.replace(/[^a-zA-Z0-9]/g, '')}">
                ${option.label}
            </label>
        `;
        container.appendChild(div);
    });
}

// Helper function to get current spec values
function getSpecValues() {
    return {
        resolution: document.getElementById('resolutionSpec')?.textContent.trim(),
        quality: document.getElementById('qualitySpec')?.textContent.trim(),
        codec: document.getElementById('codecSpec')?.textContent.trim(),
        audio: document.getElementById('audioSpec')?.textContent.trim(),
        languages: document.getElementById('languagesSpec')?.textContent.trim(),
        hdr: document.getElementById('hdrSpec')?.textContent.trim()
    };
}

// Helper function to get options for a spec type
function getOptionsForSpecType(specType) {
    if (specType === 'languages') {
        // Get languages from hidden input
        try {
            const supportedLanguages = JSON.parse(document.getElementById('supportedLanguages').value);
            return supportedLanguages.map(lang => ({value: lang, label: lang}));
        } catch (e) {
            console.error('Error parsing supported languages:', e);
            return [];
        }
    }
    return SPEC_OPTIONS[specType] || [];
}

function switchToQuickImport() {
    document.getElementById('scraperSelect').value = 'quick_import';
    updateFormFields();
    formUtils.resetForm();
    hideSuccessAlert();
    window.scrollTo(0, 0);
}

// Function to show success message with Import New button
function showSuccessWithImportNew(message) {
    const alert = document.getElementById('successAlert');
    const messageElement = document.getElementById('successMessage');

    // If alert doesn't exist, use notification
    if (!alert) {
        showNotification(message, 'success');
        return;
    }

    messageElement.textContent = message;
    alert.style.display = 'block';
    alert.classList.add('show');

    // Scroll to the top to make the alert visible
    window.scrollTo(0, 0);
}

function hideSuccessAlert() {
    const alert = document.getElementById('successAlert');
    if (alert) {
        alert.classList.remove('show');
        alert.style.display = 'none';

        // Clear the message
        const messageElement = document.getElementById('successMessage');
        if (messageElement) {
            messageElement.textContent = '';
        }
    }
}


function updateSpecField(fieldId, value) {
    const targetInput = document.getElementById(`${fieldId}Spec`);
    if (targetInput) {
        targetInput.innerHTML = formatTechnicalSpec(value, fieldId);
    }
}

function setupFieldChangeHandlers() {
    // Audio codecs handler
    const audioCodecsInputs = document.querySelectorAll('input[name="audioCodecs"]');
    audioCodecsInputs.forEach(input => {
        input.addEventListener('change', () => {
            const selectedAudioCodecs = Array.from(audioCodecsInputs)
                .filter(input => input.checked)
                .map(input => input.value)
                .join(', ');
            updateSpecField('audio', selectedAudioCodecs);
        });
    });

    // HDR formats handler
    const hdrFormatsInputs = document.querySelectorAll('input[name="hdrFormats"]');
    hdrFormatsInputs.forEach(input => {
        input.addEventListener('change', () => {
            const selectedHdrFormats = Array.from(hdrFormatsInputs)
                .filter(input => input.checked)
                .map(input => input.value)
                .join(', ');
            updateSpecField('hdr', selectedHdrFormats);
        });
    });

    // Languages handler
    const languageInputs = document.querySelectorAll('input[name="languages"]');
    languageInputs.forEach(input => {
        input.addEventListener('change', () => {
            const selectedLanguages = Array.from(languageInputs)
                .filter(input => input.checked)
                .map(input => input.value)
                .join(', ');
            updateSpecField('languages', selectedLanguages);
        });
    });
}

// Utility functions for form handling
const formUtils = {
    resetForm(preserveFileAndMagnet = false) {
        // Store torrent file and magnet link if needed
        let storedFile, storedMagnet;
        if (preserveFileAndMagnet) {
            const quickFile = document.getElementById('quickTorrentFile');
            const quickMagnet = document.getElementById('quickMagnetLink');
            storedFile = quickFile.files[0];
            storedMagnet = quickMagnet.value;
        }

        // Reset basic fields
        const basicFields = ['torrentImdbId', 'metaType', 'title', 'poster', 'background', 'logo',
            'resolution', 'quality', 'videoCodec', 'createdAt'];
        basicFields.forEach(fieldId => {
            const element = document.getElementById(fieldId);
            if (element) element.value = '';
        });

        // Reset checkboxes (audio, HDR, languages)
        ['audio-', 'hdr-', 'lang-'].forEach(prefix => {
            document.querySelectorAll(`input[id^="${prefix}"]`)
                .forEach(checkbox => checkbox.checked = false);
        });

        // Reset catalog checkboxes for both movies and series
        ['movie', 'series'].forEach(type => {
            const container = document.getElementById(`${type}Catalogs`);
            if (container) {
                container.querySelectorAll('input[type="checkbox"]')
                    .forEach(cb => cb.checked = false);
            }
        });

        // Reset other options
        const addTitleCheckbox = document.getElementById('addTitleToPoster');
        if (addTitleCheckbox) addTitleCheckbox.checked = false;

        // Restore torrent file and magnet link if needed
        if (preserveFileAndMagnet) {
            const quickFile = document.getElementById('quickTorrentFile');
            const quickMagnet = document.getElementById('quickMagnetLink');

            if (storedFile) {
                const dt = new DataTransfer();
                dt.items.add(storedFile);
                quickFile.files = dt.files;
            }
            if (storedMagnet) {
                quickMagnet.value = storedMagnet;
            }
        }
    },

    transferTorrentFile() {
        const quickFile = document.getElementById('quickTorrentFile');
        const torrentFile = document.getElementById('torrentFile');
        const magnetLink = document.getElementById('magnetLink');
        const quickMagnet = document.getElementById('quickMagnetLink');

        if (quickFile && quickFile.files[0]) {
            const dt = new DataTransfer();
            dt.items.add(quickFile.files[0]);
            torrentFile.files = dt.files;
        } else if (quickMagnet) {
            magnetLink.value = quickMagnet.value.trim();
        }
    }
};

// Main functions for handling torrent imports
function selectMatch(match, torrentData) {
    if (!match || !torrentData) return;

    // Reset all form fields while preserving torrent file/magnet
    formUtils.resetForm(true);

    // Set scraperSelect first to ensure proper form state
    document.getElementById('scraperSelect').value = 'add_torrent';

    // Apply match data
    const matchFields = {
        'imdb_id': 'torrentImdbId',
        'type': 'metaType',
        'title': 'title',
        'poster': 'poster',
        'background': 'background',
        'logo': 'logo'
    };

    Object.entries(matchFields).forEach(([matchKey, fieldId]) => {
        const element = document.getElementById(fieldId);
        if (element && match[matchKey]) {
            element.value = match[matchKey];
        }
    });

    // Set 'Add title to poster' option
    document.getElementById('addTitleToPoster').checked = match.is_add_title_to_poster || false;

    // Apply torrent specific data
    applyTorrentData(torrentData);

    updateFormFields();
    showNotification('Match selected! Please review and confirm the details.', 'success');
}

function switchToManualImport(torrentData) {
    // Reset all form fields while preserving torrent file/magnet
    formUtils.resetForm(true);

    document.getElementById('scraperSelect').value = 'add_torrent';

    if (torrentData) {
        // Apply all torrent data
        applyTorrentData(torrentData);
    }

    updateFormFields();
}

function applyTorrentData(torrentData) {
    if (!torrentData) return;

    // Update title if empty
    if (!document.getElementById('title').value && torrentData.title) {
        document.getElementById('title').value = torrentData.title;
    }

    // Set release date
    if (torrentData.created_at) {
        const date = new Date(torrentData.created_at);
        setupDateInput('createdAt', false, date);
    }

    // Map torrent data fields to spec types
    const specMapping = {
        resolution: torrentData.resolution,
        quality: torrentData.quality,
        codec: torrentData.codec,
        audio: torrentData.audio,
        hdr: torrentData.hdr,
        languages: torrentData.languages
    };

    // Helper function to format value based on spec type
    function formatSpecValue(type, value) {
        if (!value) return 'Not Set';

        // For array-like values (could be array or comma-separated string)
        if (typeof value === 'string' && value.includes(',') || Array.isArray(value)) {
            const valueArray = Array.isArray(value) ? value : value.split(',').map(v => v.trim());
            return valueArray.join(', ');
        }

        // For single values, validate against available options
        const options = getOptionsForSpecType(type);
        const option = options.find(opt => opt.value.toLowerCase() === value.toLowerCase());
        return option ? option.value : value;
    }

    // Update basic specs display
    Object.entries(specMapping).forEach(([type, value]) => {
        const formattedValue = formatSpecValue(type, value);
        updateSpecField(type, formattedValue);
    });

    // Update basic technical specs display
    updateBasicTechnicalSpecs({
        resolution: specMapping.resolution,
        quality: specMapping.quality,
        codec: specMapping.codec,
        audio: specMapping.audio,
        hdr: specMapping.hdr,
        languages: specMapping.languages
    });

    // Handle content type specific settings
    document.getElementById('metaType').value = torrentData.type;
    if (torrentData.type === 'movie' || torrentData.type === 'series') {
        if (torrentData.catalog) {
            const catalogs = Array.isArray(torrentData.catalog)
                ? torrentData.catalog
                : torrentData.catalog.split(',').map(c => c.trim());

            const container = document.getElementById(`${torrentData.type}Catalogs`);
            if (container) {
                catalogs.forEach(catalog => {
                    const checkbox = container.querySelector(`input[value="${catalog}"]`);
                    if (checkbox) checkbox.checked = true;
                });
            }
        }
    }

    // Transfer torrent file or magnet link
    formUtils.transferTorrentFile();

    // Update UI to reflect content type
    updateContentType();
}

async function handleAddTorrent(submitBtn, loadingSpinner, forceImport = false, annotatedFiles = null) {
    let formData = new FormData();
    const metaType = document.getElementById('metaType').value;
    const torrentType = document.getElementById('torrentType').value;
    const isSportsContent = metaType === 'sports';

    // Get current spec values
    const specValues = getSpecValues();

    // Add basic metadata to formData
    formData.append('meta_type', metaType);
    formData.append('torrent_type', torrentType);
    formData.append('uploader', document.getElementById('uploaderName').value.trim() || 'Anonymous');

    const createdAt = document.getElementById('createdAt').value;
    if (createdAt) {
        const dateParts = createdAt.split('/');
        const date = new Date(`${dateParts[1]}/${dateParts[0]}/${dateParts[2]}`);
        formData.append('created_at', date.toISOString().split('T')[0])
    } else {
        showNotification("Release Date is required.", 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }

    // Add content metadata
    if (!isSportsContent && document.getElementById('torrentImdbId')?.value) {
        formData.append('meta_id', document.getElementById('torrentImdbId').value);
    }

    const title = document.getElementById('title').value;
    if (!title && isSportsContent) {
        showNotification('Title is required for sports content.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }
    if (title) formData.append('title', title);

    // Add optional metadata
    ['poster', 'background', 'logo'].forEach(field => {
        const value = document.getElementById(field)?.value;
        if (value) formData.append(field, value);
    });

    // Add technical specifications
    Object.entries(specValues).forEach(([key, value]) => {
        if (value && value !== 'Not Set') {
            formData.append(key, value);
        }
    });

    // Handle catalogs
    if (isSportsContent) {
        const sportsCatalog = document.getElementById('sportsCatalog').value;
        if (!sportsCatalog) {
            showNotification('Sports category is required.', 'error');
            resetButton(submitBtn, loadingSpinner);
            return;
        }
        formData.append('catalogs', sportsCatalog);
    } else {
        const catalogInputs = document.querySelectorAll(`#${metaType}Catalogs input[name="catalogs"]:checked`);
        const catalogs = Array.from(catalogInputs).map(el => el.value);
        if (catalogs.length > 0) {
            formData.append('catalogs', catalogs.join(','));
        }
    }

    // Handle add title to poster option
    formData.append('is_add_title_to_poster',
        document.getElementById('addTitleToPoster').checked.toString());

    // Handle torrent file/magnet
    const magnetLink = document.getElementById('magnetLink').value;
    const torrentFile = document.getElementById('torrentFile').files[0];

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

    if (magnetLink) {
        formData.append('magnet_link', magnetLink);
    } else {
        formData.append('torrent_file', torrentFile);
    }

    // Add force import and annotated files if present
    if (forceImport) formData.append('force_import', 'true');
    if (annotatedFiles) formData.append('file_data', JSON.stringify(annotatedFiles));

    // Submit the form
    try {
        const response = await fetch('/scraper/torrent', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (data.status === 'needs_annotation') {
            try {
                const newAnnotatedFiles = await showFileAnnotationModal(data.files);
                await handleAddTorrent(submitBtn, loadingSpinner, forceImport, newAnnotatedFiles);
                return;
            } catch (annotationError) {
                console.error('Error annotating files:', annotationError);
                showNotification('File annotation was cancelled', 'warning');
            }
        } else if (data.status === 'validation_failed' && !forceImport) {
            const shouldForceImport = await showConfirmationDialog(
                data.errors,
                data.torrent_data,
                data.info_hash
            );

            if (shouldForceImport) {
                await handleAddTorrent(submitBtn, loadingSpinner, true, annotatedFiles);
                return;
            }
        } else if (data.detail) {
            showNotification(data.detail, 'error');
        } else {
            showSuccessWithImportNew(data.status);
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

async function handleMigration(apiPassword, submitBtn, loadingSpinner) {
    const mediafusionId = document.getElementById('mediafusionId').value.trim();
    const imdbId = document.getElementById('migrationImdbId').value.trim();
    const mediaType = document.getElementById('mediaType').value;

    if (!mediafusionId || !imdbId) {
        showNotification('Both MediaFusion ID and IMDb ID are required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }

    try {
        const response = await fetch('/scraper/migrate_id', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                mediafusion_id: mediafusionId,
                imdb_id: imdbId,
                media_type: mediaType,
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
        console.error('Error migrating ID:', error);
        showNotification(`Error migrating ID: ${error.toString()}`, 'error');
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


async function handleUpdateImages(apiPassword, submitBtn, loadingSpinner) {
    const metaId = document.getElementById('imageUpdateMetaId').value.trim();
    const poster = document.getElementById('imageUpdatePoster').value.trim();
    const background = document.getElementById('imageUpdateBackground').value.trim();
    const logo = document.getElementById('imageUpdateLogo').value.trim();

    // Validate required fields
    if (!metaId) {
        showNotification('Content ID is required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }

    // Validate at least one image URL is provided
    if (!poster && !background && !logo) {
        showNotification('At least one image URL must be provided.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }

    const formData = new FormData();
    formData.append('meta_id', metaId);
    formData.append('api_password', apiPassword);

    if (poster) formData.append('poster', poster);
    if (background) formData.append('background', background);
    if (logo) formData.append('logo', logo);

    try {
        const response = await fetch('/scraper/update_images', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();
        if (data.detail) {
            showNotification(data.detail, 'error');
        } else {
            showNotification(data.status, 'success');
            // Clear form fields on success
            document.getElementById('imageUpdatePoster').value = '';
            document.getElementById('imageUpdateBackground').value = '';
            document.getElementById('imageUpdateLogo').value = '';
        }
    } catch (error) {
        console.error('Error updating images:', error);
        showNotification(`Error updating images: ${error.toString()}`, 'error');
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
        case 'quick_import':
            resetButton(submitBtn, loadingSpinner);
            await handleQuickImport(submitBtn, loadingSpinner);
            break;
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
        case 'migrate_id':
            await handleMigration(apiPassword, submitBtn, loadingSpinner);
            break;
        case 'update_images':
            await handleUpdateImages(apiPassword, submitBtn, loadingSpinner);
            break;
        default:
            await handleScrapyParameters(payload, submitBtn, loadingSpinner);
            break;
    }
}


// Initial update for form fields on page load
document.addEventListener('DOMContentLoaded', function () {
    handleInitialSetup();
    updateBasicTechnicalSpecs();
    setupFieldChangeHandlers();
    setupDateInput('createdAt', true);
});
document.getElementById('spiderName').addEventListener('change', toggleSpiderSpecificFields);
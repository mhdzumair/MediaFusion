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
                    <label for="streamUrl-${streamIndex}" class="form-label">M3U8 URL</label>
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

function updateMetaType() {
    const metaType = document.getElementById('metaType').value;
    if (metaType === 'movie') {
        setElementDisplay('catalogsSeries', 'none');
        setElementDisplay('catalogsMovie', 'block');
        setElementDisplay('seriesParameters', 'none');
    } else {
        setElementDisplay('catalogsMovie', 'none');
        setElementDisplay('catalogsSeries', 'block');
        setElementDisplay('seriesParameters', 'block');
    }
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

    // Get the selected scraper type
    const scraperType = document.getElementById('scraperSelect').value;
    let authRequired = document.getElementById('apiPasswordEnabled').value === "true";

    // Show the relevant section based on the selected scraper type
    switch (scraperType) {
        case 'add_torrent':
            setElementDisplay("torrentUploadParameters", "block");
            authRequired = false;
            updateMetaType();
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


async function handleAddTorrent(submitBtn, loadingSpinner) {
    let formData = new FormData();
    const imdbId = document.getElementById('torrentImdbId').value;
    const metaType = document.getElementById('metaType').value;
    const imdbIdNumeric = parseInt(imdbId.slice(2), 10);
    if (!imdbId.startsWith('tt') || imdbId.length < 3 || imdbId.length > 10 || isNaN(imdbIdNumeric)) {
        showNotification('Invalid IMDb ID', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }
    formData.append('meta_id', imdbId);
    formData.append('meta_type', metaType);
    const source = document.getElementById('source').value;
    if (!source) {
        showNotification('Source is required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }
    formData.append('source', source);
    let catalogs;
    if (metaType === 'movie') {
        catalogs = Array.from(document.querySelectorAll('#catalogsMovie input[name="catalogs"]:checked')).map(el => el.value);
    } else {
        catalogs = Array.from(document.querySelectorAll('#catalogsSeries input[name="catalogs"]:checked')).map(el => el.value);
    }
    if (catalogs.length === 0) {
        showNotification('At least one catalog is required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }
    formData.append('catalogs', catalogs.join(','));
    const createdAt = document.getElementById('createdAt').value;
    if (!createdAt) {
        showNotification('Created At is required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }
    formData.append('created_at', createdAt);

    if (metaType === 'series') {
        const season = document.getElementById('season').value;
        let episodes = document.getElementById('episodes').value;
        if (!season || !episodes) {
            showNotification('Season and Episodes are required for TV Series.', 'error');
            resetButton(submitBtn, loadingSpinner);
            return;
        }
        if (episodes.includes('-')) {
            const [start, end] = episodes.split('-');
            episodes = Array.from({length: end - start + 1}, (_, i) => parseInt(start) + i).join(',');
        } else {
            episodes = episodes.split(',').map(e => e.trim());
        }

        formData.append('season', season);
        formData.append('episodes', episodes);
    }

    const magnetLink = document.getElementById('magnetLink').value;
    const torrentFile = document.getElementById('torrentFile').files[0];
    if (!magnetLink && !torrentFile) {
        showNotification('Either Magnet Link or Torrent File is required.', 'error');
        resetButton(submitBtn, loadingSpinner);
        return;
    }

    if (magnetLink) {
        formData.append('magnet_link', magnetLink);
    } else {
        formData.append('torrent_file', torrentFile);
    }

    try {
        const response = await fetch('/scraper/torrent', {
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

    try {
        const response = await fetch(`/scraper/imdb_data?meta_id=${imdbId}`, {
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
        default:
            await handleScrapyParameters(payload, submitBtn, loadingSpinner);
            break;
    }
}


// Initial update for form fields on page load
document.addEventListener('DOMContentLoaded', updateFormFields);
document.getElementById('spiderName').addEventListener('change', toggleSpiderSpecificFields);
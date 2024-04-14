// ---- Helper Functions ----

function showNotification(message, type = 'info') {
    toastr.options = {
        closeButton: true,
        newestOnTop: true,
        progressBar: true,
        positionClass: "toast-top-center",
        preventDuplicates: true,
        onclick: null,
        showDuration: "300",
        hideDuration: "1000",
        timeOut: "5000",
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
    if (sourceValue) {
        targetField.disabled = true;
    } else {
        targetField.disabled = false;
    }
}


function removeStreamInput(streamId) {
    document.getElementById(streamId).remove();
}

function setElementDisplay(elementId, displayStatus) {
    document.getElementById(elementId).style.display = displayStatus;
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

// Function to update form fields based on scraper selection
function updateFormFields() {
    // Check for API Password if authentication is required
    if (document.getElementById('api_password')) {
        setupPasswordToggle('api_password', 'toggleApiPassword', 'toggleApiPasswordIcon');
    }

    // Hide all sections initially
    setElementDisplay('scrapyParameters', 'none');
    setElementDisplay('tvMetadataInput', 'none');
    setElementDisplay('m3uPlaylistInput', 'none');

    // Get the selected scraper type
    const scraperType = document.getElementById('scraperSelect').value;

    // Show the relevant section based on the selected scraper type
    switch (scraperType) {
        case 'scrapy':
            // Show Scrapy-specific parameters
            setElementDisplay('scrapyParameters', 'block');
            toggleSpiderSpecificFields();
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
        default:
            // Optionally handle any default cases if needed
            break;
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


// Function to submit the form and display the response using Toastr
async function submitScraperForm() {
    const apiPassword = document.getElementById('api_password').value;
    const scraperType = document.getElementById('scraperSelect').value;
    let payload = {};
    let endpoint = "/scraper/run";
    let headers = {};
    let body = null;

    // Append common fields
    payload['scraper_type'] = scraperType;
    payload['api_password'] = apiPassword;

    // Handling different scraper types
    if (scraperType === 'add_tv_metadata') {
        try {
            payload['tv_metadata'] = constructTvMetadata(); // Ensure this method returns the correct object
            headers['Content-Type'] = 'application/json';
            endpoint = "/scraper/add_tv_metadata";
            body = JSON.stringify(payload);
        } catch (error) {
            console.error('Error constructing TV Metadata:', error);
            showNotification(error.message, 'error');
            return;
        }
    } else if (scraperType === 'add_m3u_playlist') {
        // Switching to form data for potential file upload
        let formData = new FormData();
        formData.append('scraper_type', scraperType);
        formData.append('api_password', apiPassword);
        formData.append('m3u_playlist_source', document.getElementById('m3uPlaylistSource').value);

        const inputType = document.querySelector('input[name="m3uInputType"]:checked').value;
        if (inputType === 'url') {
            formData.append('m3u_playlist_url', document.getElementById('m3uPlaylistUrl').value);
        } else { // File upload case
            const file = document.getElementById('m3uPlaylistFile').files[0];
            if (!file) {
                showNotification('M3U Playlist file is required.', 'error');
                return;
            }
            formData.append('m3u_playlist_file', file);
        }
        endpoint = "/scraper/m3u_upload";
        body = formData; // FormData will set the correct Content-Type header
    } else {
        headers['Content-Type'] = 'application/json';
        // Collect all scrapyParameters input fields that are not disabled and visible
        document.querySelectorAll('#scrapyParameters input, #scrapyParameters select').forEach(input => {
            // Ensuring the input is visible and not disabled
            if (!input.disabled && input.type !== 'radio' && input.type !== 'checkbox') {
                payload[input.name] = input.value;
            } else if (input.type === 'radio' && input.checked) {
                payload[input.name] = input.value;
            } else if (input.type === 'checkbox') {
                payload[input.name] = input.checked;
            }
        });
        body = JSON.stringify(payload);
    }

    // Making the request
    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: headers,
            body: body
        });

        const data = await response.json();
        if (response.ok) {
            showNotification(data.status, 'success');
        } else {
            if (data.detail) {
                showNotification(data.detail, 'error');
            } else {
                showNotification('Error submitting scraper form. Please check the console for more details.', 'error');
            }
        }
    } catch (error) {
        console.error('Error submitting scraper form:', error);
        showNotification('Error submitting scraper form. Please check the console for more details.', 'error');
    }
}


// Initial update for form fields on page load
document.addEventListener('DOMContentLoaded', updateFormFields);
document.getElementById('spiderName').addEventListener('change', toggleSpiderSpecificFields);
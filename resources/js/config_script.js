// ---- Variables ----
// Removed provider-related variables and functions
let mdbListUI;

// ---- Removed OAuth-related Functions ----
// generateUniqueToken, initiateOAuthFlow, checkAuthorization

// Function to update the file size output display
function updateSizeOutput() {
    const slider = document.getElementById('max_size_slider');
    const output = document.getElementById('max_size_output');
    const value = slider.value;
    const maxSize = slider.max;
    output.textContent = value === maxSize ? 'Unlimited' : formatBytes(value);
    updateSliderTrack(slider);
}


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

function setElementDisplay(elementId, displayStatus) {
    document.getElementById(elementId).style.display = displayStatus;
}

function changeTooltipContent(elementId, text) {
    document.getElementById(elementId).dataset.bsOriginalTitle = text;
}

function validateUrl(url) {
    // This regex supports domain names, IPv4, and IPv6 addresses
    const urlPattern = /^(https?:\/\/)?(([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}|localhost|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|\[(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\])(:?\d+)?(\/[-a-z\d%_.~+]*)*(\?[;&a-z\d%_.~+=-]*)?(\#[-a-z\d_]*)?$/i;
    return urlPattern.test(url);
}

// Function to format bytes into a human-readable format
function formatBytes(bytes, decimals = 2) {
    if (bytes === "0") return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// Function to update the slider track based on the current value
function updateSliderTrack(slider) {
    const percentage = (slider.value / slider.max) * 100;
    slider.style.background = `linear-gradient(to right, #4a47a3 ${percentage}%, #ddd ${percentage}%, #ddd 100%)`;
}

// Removed setOAuthBtnTextContent function
// Removed adjustOAuthSectionDisplay function
// Removed updateProviderFields function

// Function to show loading widget
function showLoadingWidget(message = "Processing your configuration...") {
    const loadingWidget = document.getElementById('loadingWidget');
    const loadingMessage = document.getElementById('loadingMessage');

    if (loadingMessage) {
        loadingMessage.textContent = message;
    }

    if (loadingWidget) {
        loadingWidget.style.display = 'flex';
        // Prevent background scrolling while loading
        document.body.style.overflow = 'hidden';
    }
}

// Function to hide loading widget
function hideLoadingWidget() {
    const loadingWidget = document.getElementById('loadingWidget');
    if (loadingWidget) {
        loadingWidget.style.display = 'none';
        // Restore background scrolling
        document.body.style.overflow = '';
    }
}


// Function to get installation URL
async function getInstallationUrl(isRedirect = false) {
    try {
        showLoadingWidget();

        const userData = getUserData();
        const existingConfig = document.getElementById('existing_config').value;
        let urlPrefix = window.location.protocol + "//";
        if (isRedirect) {
            urlPrefix = "stremio://";
        }

        if (!userData) {
            hideLoadingWidget();
            showNotification('Validation failed. Please check your input.', 'error');
            return null;
        }
        const encryptUrl = '/encrypt-user-data' + (existingConfig ? `/${existingConfig}` : '');

        const response = await fetch(encryptUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(userData)
        });

        const data = await response.json();

        if (data.status === 'error') {
            hideLoadingWidget();
            showNotification(data.message, 'error');
            return null;
        }

        if (data.detail) {
            hideLoadingWidget();
            showNotification(data.detail[0].msg, 'error');
            return null;
        }

        if (!data.encrypted_str) {
            hideLoadingWidget();
            showNotification('An error occurred while encrypting user data', 'error');
            return null;
        }

        const installationUrl = urlPrefix + window.location.host + "/" + data.encrypted_str + "/manifest.json";
        hideLoadingWidget();
        return installationUrl;

    } catch (error) {
        hideLoadingWidget();
        showNotification('An error occurred while encrypting user data', 'error');
        console.error('Error encrypting user data:', error);
        return null;
    }
}

function getUserData() {
    let isValid = true;

    const validateInput = (elementId, condition) => {
        const element = document.getElementById(elementId);
        if (condition) {
            element.classList.remove('is-invalid');
        } else {
            element.classList.add('is-invalid');
            if (isValid) {
                element.focus(); // Set focus on the first invalid input
            }
            isValid = false;
        }
    };

    // Removed streaming provider data collection and validation logic

    const mediaflowEnabled = document.getElementById('enable_mediaflow').checked
    let mediaflowConfig = null;
    if (mediaflowEnabled) {
        mediaflowConfig = {
            proxy_url: document.getElementById('mediaflow_proxy_url').value,
            api_password: document.getElementById('mediaflow_api_password').value,
            public_ip: document.getElementById('mediaflow_public_ip').value,
            proxy_live_streams: document.getElementById('proxy_live_streams').checked,
            // Removed proxy_debrid_streams
        };
        // Removed check for servicesNotNeedingDebridProxy
        validateInput('mediaflow_proxy_url', mediaflowConfig.proxy_url.trim() !== '');
        validateInput('mediaflow_api_password', mediaflowConfig.api_password.trim() !== '');
    }

    let rpdbConfig = null;
    if (document.getElementById('enable_rpdb').checked) {
        rpdbConfig = {
            api_key: document.getElementById('rpdb_api_key').value,
        };
        validateInput('rpdb_api_key', rpdbConfig.api_key.trim() !== '');
    }

    let mdblistConfig = null;
    if (document.getElementById('enable_mdblist').checked) {
        mdblistConfig = {
            api_key: document.getElementById('mdblist_api_key').value,
            lists: mdbListUI.getSelectedListsData()
        };
    }

    // Removed collection/validation for max_size, max_streams_per_resolution

    let apiPassword = null;
    // Check for API Password if authentication is required
    if (document.getElementById('api_password')) {
        validateInput('api_password', document.getElementById('api_password').value);
        apiPassword = document.getElementById('api_password').value;
    }

    if (!isValid) {
        return null; // Return null if validation fails
    }

    // Removed collection of stream-related preferences (sorting, display, language flag)

    // Collect nudity filter data
    let selectedNudityFilters = Array.from(document.querySelectorAll('input[name="nudity_filter"]:checked')).map(el => el.value);
    if (selectedNudityFilters.length === 0) {
        selectedNudityFilters = ['Disable'];
    }

    // Collect certification filter data
    let selectedCertificationFilters = Array.from(document.querySelectorAll('input[name="certification_filter"]:checked')).map(el => el.value);
    if (selectedCertificationFilters.length === 0) {
        selectedCertificationFilters = ['Disable'];
    }

    // Removed collection of language sorting and quality filters

    return {
        streaming_provider: null, // Set to null as it's removed
        selected_catalogs: Array.from(document.querySelectorAll('input[name="selected_catalogs"]:checked')).map(el => el.value),
        // Removed stream-related fields: selected_resolutions, max_size, max_streams_per_resolution, torrent_sorting_priority, show_full_torrent_name, show_language_country_flag, language_sorting, quality_filter, live_search_streams, contribution_streams
        enable_catalogs: document.getElementById('enable_catalogs').checked,
        enable_imdb_metadata: document.getElementById('enable_imdb_metadata').checked,
        nudity_filter: selectedNudityFilters,
        certification_filter: selectedCertificationFilters,
        api_password: apiPassword,
        mediaflow_config: mediaflowConfig,
        rpdb_config: rpdbConfig,
        mdblist_config: mdblistConfig,
    };
}

// Function to display the installation URL in a textarea for manual copying
function displayFallbackUrl(url) {
    const container = document.getElementById('fallbackUrlContainer');
    const textarea = document.getElementById('fallbackUrl');
    textarea.value = url;
    container.style.display = 'block'; // Make the container visible
    textarea.focus();
}

// Configuration Mode Handling
function setConfigMode(mode) {
    // Update button states
    document.querySelectorAll('.mode-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.getElementById(mode + '_mode').classList.add('active');

    // Update description
    const description = document.querySelector('.mode-description');
    description.textContent = mode === 'pro'
        ? 'Pro Mode: Access to all advanced configuration options'
        : 'Newbie Mode: Quick setup with essential options for new users';

    // Toggle visibility of pro sections
    const proSections = document.querySelectorAll('.pro-mode-section');
    proSections.forEach(section => {
        section.style.display = mode === 'pro' ? 'block' : 'none';
    });

    // Handle specific settings visibility
    const streamingSection = document.querySelector('.streaming-preferences');
    if (streamingSection) {
        const advancedOptions = streamingSection.querySelectorAll('.advanced-option');
        advancedOptions.forEach(option => {
            option.style.display = mode === 'pro' ? 'block' : 'none';
        });
    }

    // Save preference with error handling
    try {
        localStorage.setItem('configMode', mode);
    } catch (e) {
        console.warn('Failed to save config mode preference:', e);
    }
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

// Function to handle configured credential fields
function handleConfiguredFields(field, isConfigured = false) {
    const inputField = document.getElementById(field);
    const resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'btn btn-outline-secondary reset-config-btn';
    resetBtn.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i>';
    resetBtn.title = 'Reset Configuration';

    if (isConfigured) {
        inputField.setAttribute('readonly', true);
        inputField.classList.add('configured-field');

        // Add reset button next to the field
        if (!inputField.nextElementSibling?.classList.contains('reset-config-btn')) {
            inputField.parentElement.appendChild(resetBtn);
        }

        // Handle reset button click
        resetBtn.onclick = () => {
            inputField.value = '';
            inputField.removeAttribute('readonly');
            inputField.classList.remove('configured-field');
            resetBtn.remove();
        };
    }
}

// Function to initialize configured fields
function initConfiguredFields(configuredFields) {
    // Removed provider-related sensitive fields from check
    const sensitiveFields = [
        'mediaflow_api_password',
        'rpdb_api_key'
        // Removed: 'provider_token', 'password', 'qbittorrent_password', 'webdav_password'
    ];

    sensitiveFields.forEach(field => {
        if (configuredFields.includes(field)) {
            handleConfiguredFields(field, true);
        }
    });
}

async function initiateKodiSetup() {
    // Show modal to input Kodi code
    const kodiCodeModal = new bootstrap.Modal(document.getElementById('kodiCodeModal'));
    kodiCodeModal.show();
}

async function submitKodiCodeAndSetup() {
    const kodiCode = document.getElementById('kodiCodeInput').value;
    if (kodiCode && kodiCode.length === 6) {
        const kodiCodeModal = bootstrap.Modal.getInstance(document.getElementById('kodiCodeModal'));
        kodiCodeModal.hide();
        await setupKodiAddon(kodiCode);
    } else {
        showNotification('Please enter a valid 6-digit code.', 'error');
    }
}

async function setupKodiAddon(kodiCode) {
    showLoadingWidget('Preparing Kodi setup...')
    const installationUrl = await getInstallationUrl();

    if (installationUrl) {
        showLoadingWidget('Setting up Kodi addon...')
        try {
            const response = await fetch('/kodi/associate_manifest', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    code: kodiCode,
                    manifest_url: installationUrl
                }),
            });
            const data = await response.json();

            if (response.ok) {
                showNotification('Kodi addon setup successful!', 'success');
            } else {
                showNotification(`An error occurred while setting up the Kodi addon. ${data.detail}`, 'error');
            }
        } catch (error) {
            console.error('Error setting up Kodi addon:', error);
            showNotification('An error occurred while setting up the Kodi addon.', 'error');
        } finally {
            hideLoadingWidget();
        }
    }
}
/**
 * Add select/deselect buttons and search input for a container
 *
 * @param {string} containerId - ID of the container element
 * @param {string} sectionName - Name of the section for labels and placeholders
 * @param {string} checkboxSelector - CSS selector for checkboxes in the container
 */
function addSelectDeselectSearch(containerId, sectionName, checkboxSelector) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Create a controls container
    const controlsContainer = document.createElement('div');
    controlsContainer.className = 'mb-3';

    // Create search input
    const searchGroup = document.createElement('div');
    searchGroup.className = 'input-group mb-2';

    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'form-control form-control-sm';
    searchInput.placeholder = `Search ${sectionName}...`;
    searchInput.id = `${containerId}-search`;
    searchInput.setAttribute('autocomplete', 'off');

    const searchClearBtn = document.createElement('button');
    searchClearBtn.className = 'btn btn-outline-secondary btn-sm';
    searchClearBtn.type = 'button';
    searchClearBtn.innerHTML = '<i class="bi bi-x"></i>';
    searchClearBtn.setAttribute('data-bs-toggle', 'tooltip');
    searchClearBtn.setAttribute('data-bs-placement', 'top');
    searchClearBtn.setAttribute('title', 'Clear Search');

    searchGroup.appendChild(searchInput);
    searchGroup.appendChild(searchClearBtn);

    // Create button group
    const buttonGroup = document.createElement('div');
    buttonGroup.className = 'btn-group btn-group-sm w-100';

    const selectAllBtn = document.createElement('button');
    selectAllBtn.type = 'button';
    selectAllBtn.className = 'btn btn-outline-primary';
    selectAllBtn.innerHTML = '<i class="bi bi-check-all"></i> Select All';

    const deselectAllBtn = document.createElement('button');
    deselectAllBtn.type = 'button';
    deselectAllBtn.className = 'btn btn-outline-secondary';
    deselectAllBtn.innerHTML = '<i class="bi bi-x-lg"></i> Deselect All';

    buttonGroup.appendChild(selectAllBtn);
    buttonGroup.appendChild(deselectAllBtn);

    // Add elements to controls container
    controlsContainer.appendChild(searchGroup);
    controlsContainer.appendChild(buttonGroup);

    // Insert controls at the beginning of the container
    container.parentNode.insertBefore(controlsContainer, container);

    // Add event listeners
    selectAllBtn.addEventListener('click', function() {
        const visibleCheckboxes = getVisibleCheckboxes(container, checkboxSelector);
        visibleCheckboxes.forEach(checkbox => {
            checkbox.checked = true;
        });
    });

    deselectAllBtn.addEventListener('click', function() {
        const visibleCheckboxes = getVisibleCheckboxes(container, checkboxSelector);
        visibleCheckboxes.forEach(checkbox => {
            checkbox.checked = false;
        });
    });

    searchInput.addEventListener('input', function() {
        filterItems(container, this.value.toLowerCase());
    });

    searchClearBtn.addEventListener('click', function() {
        searchInput.value = '';
        filterItems(container, '');
    });
}

/**
 * Get all visible checkboxes in a container
 *
 * @param {HTMLElement} container - Container element
 * @param {string} checkboxSelector - CSS selector for checkboxes
 * @returns {HTMLElement[]} Array of visible checkbox elements
 */
function getVisibleCheckboxes(container, checkboxSelector) {
    const allCheckboxes = container.querySelectorAll(checkboxSelector);
    return Array.from(allCheckboxes).filter(checkbox => {
        const item = getItemContainer(checkbox);
        return item && !item.classList.contains('d-none');
    });
}

/**
 * Get the container element of a checkbox
 *
 * @param {HTMLElement} checkbox - Checkbox element
 * @returns {HTMLElement|null} Container element or null
 */
function getItemContainer(checkbox) {
    // Navigate up to find the draggable container or column
    let parent = checkbox.parentElement;
    while (parent && !parent.classList.contains('draggable-catalog') &&
           !parent.classList.contains('draggable-language') &&
           !parent.classList.contains('col-12') &&
           !parent.classList.contains('col-md-6') &&
           !parent.classList.contains('col-lg-4')) {
        parent = parent.parentElement;
    }
    return parent;
}

/**
 * Filter items in a container based on search text
 *
 * @param {HTMLElement} container - Container element
 * @param {string} searchText - Text to search for
 */
function filterItems(container, searchText) {
    // Find all items (columns or containers)
    const items = container.querySelectorAll('.draggable-catalog, .draggable-language, .col-12.col-md-6.col-lg-4');

    if (searchText === '') {
        // Show all items if search is empty
        items.forEach(item => {
            item.classList.remove('d-none');
        });
    } else {
        // Show/hide items based on label text
        items.forEach(item => {
            const label = item.querySelector('label');
            if (label && label.textContent.toLowerCase().includes(searchText)) {
                item.classList.remove('d-none');
            } else {
                item.classList.add('d-none');
            }
        });
    }
}

// ---- Event Listeners ----

// Removed provider_token event listener

document.getElementById('enable_mediaflow').addEventListener('change', function () {
    setElementDisplay('mediaflow_config', this.checked ? 'block' : 'none');
});

document.getElementById('enable_rpdb').addEventListener('change', function () {
    setElementDisplay('rpdb_config', this.checked ? 'block' : 'none');
});

document.getElementById('enable_mdblist').addEventListener('change', function() {
    setElementDisplay('mdblist_config', this.checked ? 'block' : 'none');
});


// Removed max_size_slider event listener
// Removed oAuthBtn event listener

document.getElementById('configForm').addEventListener('submit', async function (event) {
    event.preventDefault();
    showLoadingWidget('Preparing Stremio installation...');
    const installationUrl = await getInstallationUrl(true);
    if (installationUrl) {
        window.location.href = installationUrl;
    }
});

document.getElementById('shareBtn').addEventListener('click', async function (event) {
    event.preventDefault();
    showLoadingWidget('Preparing share URL...');
    const manifestUrl = await getInstallationUrl();
    if (manifestUrl) {
        try {
            await navigator.share({
                title: 'MediaFusion Addon Manifest',
                url: manifestUrl,
            });
            showNotification('Manifest URL shared successfully. Do not share this URL with unknown persons.', 'success');
        } catch (error) {
            displayFallbackUrl(manifestUrl);
            showNotification('Unable to use Share API. URL is ready to be copied manually.', 'info');
        }
    }
});

document.getElementById('copyBtn').addEventListener('click', async function (event) {
    event.preventDefault();
    showLoadingWidget('Generating installation URL...');
    const manifestUrl = await getInstallationUrl();
    if (manifestUrl) {
        try {
            await navigator.clipboard.writeText(manifestUrl);
            showNotification('Manifest URL copied to clipboard. Do not share this URL with unknown persons.', 'success');
        } catch (error) {
            displayFallbackUrl(manifestUrl);
            showNotification('Unable to access clipboard. URL is ready to be copied manually.', 'info');
        }
    }
});


// ---- Initial Setup ----

document.addEventListener('DOMContentLoaded', function () {
    mdbListUI = new MDBListUI();
    // Removed updateProviderFields() and updateSizeOutput() calls
});

document.addEventListener('DOMContentLoaded', function () {
    let tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    let tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl)
    });

    if (navigator.share) {
        document.getElementById('shareBtn').style.display = 'block';
    } else {
        document.getElementById('copyBtn').style.display = 'block';
    }

    // Removed password toggles for provider fields

    // Check for API Password if authentication is required
    if (document.getElementById('api_password')) {
        setupPasswordToggle('api_password', 'toggleApiPassword', 'toggleApiPasswordIcon');
    }
    setupPasswordToggle('mediaflow_api_password', 'toggleMediaFlowPassword', 'toggleMediaFlowPasswordIcon');
    setupPasswordToggle('rpdb_api_key', 'toggleRPDBApiKey', 'toggleRPDBApiKeyIcon');
    setupPasswordToggle('mdblist_api_key', 'toggleMDBListApiKey', 'toggleMDBListApiKeyIcon');
});

document.addEventListener('DOMContentLoaded', function () {

    // Removed sort direction toggle logic
});

document.addEventListener('DOMContentLoaded', function () {
    // Initialize Sortable on the catalog container
    new Sortable(document.getElementById('catalogs'), {
        handle: '.draggable-catalog',
        animation: 150,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        delay: 200,
        delayOnTouchOnly: true,
        filter: '.form-check-input',
        preventOnFilter: false,
    });
    // Removed Sortable initialization for streamSortOrder and languageSortOrder
});


// Add event listeners for mode switching
document.addEventListener('DOMContentLoaded', function () {
    let storedMode = 'newbie';
    try {
        const savedMode = localStorage.getItem('configMode');
        if (savedMode) {
            storedMode = savedMode;
        }
    } catch (e) {
        console.warn('Failed to read config mode preference:', e);
    }
    setConfigMode(storedMode);
});


document.addEventListener('DOMContentLoaded', function () {
    const kodiSetupBtn = document.getElementById('kodiSetupBtn');
    if (kodiSetupBtn) {
        kodiSetupBtn.addEventListener('click', initiateKodiSetup);
    }

    const submitKodiCode = document.getElementById('submitKodiCode');
    if (submitKodiCode) {
        submitKodiCode.addEventListener('click', submitKodiCodeAndSetup);
    }
});

document.addEventListener('DOMContentLoaded', function () {
    // Initialize configured fields if they exist
    const configuredFields = JSON.parse(document.getElementById('configured_fields')?.value || '[]');
    initConfiguredFields(configuredFields);

    // Removed language sort section visibility logic and addSelectDeselectSearch call

    // Add UI elements for catalogs
    addSelectDeselectSearch('catalogs', 'Catalogs', 'input[name="selected_catalogs"]');

    // Initialize the parental guide checkboxes
    const parentalGuideCheckboxes = document.querySelectorAll('.parental-guide-checkbox');

    parentalGuideCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', function () {
            const category = this.name;
            if (this.value === 'Disable' && this.checked) {
                parentalGuideCheckboxes.forEach(cb => {
                    if (cb !== this && cb.name === category) {
                        cb.checked = false;
                        cb.parentNode.classList.add('disabled-checkbox');
                    }
                });
            } else if (this.value === 'Disable' && !this.checked) {
                parentalGuideCheckboxes.forEach(cb => {
                    if (cb.name === category) {
                        cb.parentNode.classList.remove('disabled-checkbox');
                    }
                });
            } else if (this.checked) {
                const disableCheckbox = document.querySelector(`input[name="${category}"][value="Disable"]`);
                if (disableCheckbox) {
                    disableCheckbox.checked = false;
                    disableCheckbox.parentNode.classList.add('disabled-checkbox');
                }
            } else {
                const anyChecked = Array.from(parentalGuideCheckboxes).some(cb => cb.checked && cb.name === category);
                if (!anyChecked) {
                    const disableCheckbox = document.querySelector(`input[name="${category}"][value="Disable"]`);
                    if (disableCheckbox) {
                        disableCheckbox.parentNode.classList.remove('disabled-checkbox');
                    }
                }
            }
        });
    });
});

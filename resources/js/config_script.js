// ---- Variables ----
const oAuthBtn = document.getElementById('oauth_btn');
let currentAuthorizationToken = null;
let mdbListUI;
const servicesRequiringCredentials = ['pikpak',];
const servicesRequiringUrl = ['stremthru'];
const servicesNotNeedingDebridProxy = ['stremthru'];
const providerSignupLinks = {
    pikpak: 'https://mypikpak.com/drive/activity/invited?invitation-code=52875535',
    seedr: 'https://www.seedr.cc/?r=2726511',
    offcloud: 'https://offcloud.com/?=9932cd9f',
    realdebrid: ['http://real-debrid.com/?id=9490816', 'http://real-debrid.com/?id=3351376'],
    debridlink: 'https://debrid-link.com/id/kHgZs',
    alldebrid: 'https://alldebrid.com/?uid=3ndha&lang=en',
    torbox: ['https://torbox.app/subscription?referral=339b923e-fb23-40e7-8031-4af39c212e3c', 'https://torbox.app/subscription?referral=e2a28977-99ed-43cd-ba2c-e90dc398c49c', 'https://torbox.app/subscription?referral=38f1c266-8a6c-40b2-a6d2-2148e77dafc9'],
    easydebrid: 'https://paradise-cloud.com/products/easydebrid',
    debrider: 'https://debrider.app/pricing',
    premiumize: 'https://www.premiumize.me',
    qbittorrent: 'https://github.com/mhdzumair/MediaFusion/tree/main/streaming_providers/qbittorrent#qbittorrent-webdav-setup-options-with-mediafusion',
    stremthru: 'https://github.com/MunifTanjim/stremthru?tab=readme-ov-file#configuration',
};
const providerTokenTooltip = {
    '*': `Enter Encoded Token previously generated or Click 'Authorize' to generate a new token or Provide your Private Token.`,
    stremthru: `Enter Credential ('store_name:store_token' or base64 encoded basic token from 'STREMTHRU_PROXY_AUTH' config)`,
};

// ---- OAuth-related Functions ----

function generateUniqueToken() {
    return Date.now().toString() + Math.random().toString();
}

async function initiateOAuthFlow(getDeviceCodeUrl, authorizeUrl) {
    const provider = document.getElementById('provider_service').value;

    currentAuthorizationToken = generateUniqueToken();
    setOAuthBtnTextContent(provider);

    try {
        const response = await fetch(getDeviceCodeUrl);
        const data = await response.json();
        if (data.device_code) {
            document.getElementById('device_code_display').textContent = data.user_code;
            if (data.verification_url) {
                const verificationLinkElement = document.getElementById('verification_link');
                verificationLinkElement.href = data.verification_url;
                verificationLinkElement.textContent = data.verification_url;
            }
            setElementDisplay('device_code_section', 'block');
            checkAuthorization(data.device_code, authorizeUrl);
        } else {
            // Reset the button's text if there's an error or if the device code isn't returned
            setOAuthBtnTextContent(provider);
        }
    } catch (error) {
        console.error('Error fetching device code:', error);
        alert('An error occurred. Please try again.');
        setOAuthBtnTextContent(provider)
    }
}


function checkAuthorization(deviceCode, authorizeUrl) {
    const thisAuthorizationToken = currentAuthorizationToken;

    setTimeout(async function () {
        // Exit if the token has changed
        if (thisAuthorizationToken !== currentAuthorizationToken) {
            return;
        }

        try {
            const response = await fetch(authorizeUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({device_code: deviceCode})
            });
            const data = await response.json();
            if (data.token) {
                const tokenInput = document.getElementById('provider_token');
                tokenInput.value = data.token;
                oAuthBtn.disabled = false;
                tokenInput.disabled = false;
                setElementDisplay('oauth_section', 'none');
                setElementDisplay('device_code_section', 'none');
            } else {
                checkAuthorization(deviceCode, authorizeUrl);
            }
        } catch (error) {
            console.error('Error checking authorization:', error);
            oAuthBtn.disabled = false;
        }
    }, 5000);
}

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

function setOAuthBtnTextContent(provider) {
    if (currentAuthorizationToken) {
        oAuthBtn.textContent = 'Authorizing...';
    } else {
        oAuthBtn.textContent = 'Authorize with ' + provider.charAt(0).toUpperCase() + provider.slice(1);
    }
}

function adjustOAuthSectionDisplay() {
    const provider = document.getElementById('provider_service').value;
    const providerToken = document.getElementById('provider_token').value;
    setOAuthBtnTextContent(provider)

    if ((provider === 'seedr' || provider === 'realdebrid' || provider === 'debridlink' || provider === "premiumize") && !providerToken) {
        setElementDisplay('oauth_section', 'block');
    } else {
        setElementDisplay('oauth_section', 'none');
    }
}

function updateProviderFields(isChangeEvent = false) {
    const provider = document.getElementById('provider_service').value;
    const serviceUrlInput = document.getElementById('service_url');
    const tokenInput = document.getElementById('provider_token');
    const watchlistLabel = document.getElementById('watchlist_label');

    if (servicesRequiringUrl.includes(provider)) {
        setElementDisplay('service_url_section', 'block');
    } else {
        setElementDisplay('service_url_section', 'none');
    }

    if (servicesNotNeedingDebridProxy.includes(provider)) {
        setElementDisplay('proxy_debrid_streams_section', 'none');
    } else {
        setElementDisplay('proxy_debrid_streams_section', 'block');
    }

    if (provider in providerSignupLinks) {
        // if providerSignupLinks[provider] is an array, randomly select one of the links
        if (Array.isArray(providerSignupLinks[provider])) {
            document.getElementById('signup_link').href = providerSignupLinks[provider][Math.floor(Math.random() * providerSignupLinks[provider].length)];
        } else {
            document.getElementById('signup_link').href = providerSignupLinks[provider];
        }
        setElementDisplay('signup_section', 'block');
    } else {
        setElementDisplay('signup_section', 'none');
    }

    // Toggle visibility of credentials and token input based on provider
    setElementDisplay('stremthru_config', provider === 'stremthru' ? 'block' : 'none');
    if (provider) {
        if (servicesRequiringCredentials.includes(provider)) {
            setElementDisplay('credentials', 'block');
            setElementDisplay('token_input', 'none');
            setElementDisplay('qbittorrent_config', 'none');
        } else if (provider === 'qbittorrent') {
            setElementDisplay('qbittorrent_config', 'block');
            setElementDisplay('credentials', 'none');
            setElementDisplay('token_input', 'none');
        } else {
            setElementDisplay('credentials', 'none');
            setElementDisplay('token_input', 'block');
            changeTooltipContent('provider_token_tooltip', providerTokenTooltip[provider] || providerTokenTooltip['*'])
            setElementDisplay('qbittorrent_config', 'none');
        }
        setElementDisplay('streaming_provider_options', 'block');
        watchlistLabel.textContent = `Enable ${provider.charAt(0).toUpperCase() + provider.slice(1)} Watchlist`;
    } else {
        setElementDisplay('credentials', 'none');
        setElementDisplay('token_input', 'none');
        setElementDisplay('streaming_provider_options', 'none');
        setElementDisplay('qbittorrent_config', 'none');
    }

    // Reset the fields only if this is triggered by an onchange event
    if (isChangeEvent) {
        serviceUrlInput.value = '';
        tokenInput.value = '';
        tokenInput.disabled = false;
        currentAuthorizationToken = null;
        oAuthBtn.disabled = false;
        setElementDisplay('device_code_section', 'none');
    }

    adjustOAuthSectionDisplay();
}

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
    const provider = document.getElementById('provider_service').value;
    let streamingProviderData = {};

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

    // Validate and collect streaming provider data
    if (provider) {
        if (servicesRequiringUrl.includes(provider)) {
            const serviceUrl = document.getElementById('service_url').value.trim();
            validateInput('service_url', validateUrl(serviceUrl));
            streamingProviderData.url = serviceUrl;
        }
        if (provider === 'stremthru') {
            streamingProviderData.stremthru_store_name = document.getElementById('stremthru_store_name').value.trim() || null;
        }
        if (servicesRequiringCredentials.includes(provider)) {
            validateInput('email', document.getElementById('email').value);
            validateInput('password', document.getElementById('password').value);
            streamingProviderData.email = document.getElementById('email').value;
            streamingProviderData.password = document.getElementById('password').value;
        } else if (provider === 'qbittorrent') {
            streamingProviderData.qbittorrent_config = {
                qbittorrent_url: document.getElementById('qbittorrent_url').value,
                qbittorrent_username: document.getElementById('qbittorrent_username').value,
                qbittorrent_password: document.getElementById('qbittorrent_password').value,
                seeding_time_limit: parseInt(document.getElementById('seeding_time_limit').value, 10),
                seeding_ratio_limit: parseFloat(document.getElementById('seeding_ratio_limit').value),
                play_video_after: parseInt(document.getElementById('play_video_after_download').value, 10),
                category: document.getElementById('category').value,
                webdav_url: document.getElementById('webdav_url').value,
                webdav_username: document.getElementById('webdav_username').value,
                webdav_password: document.getElementById('webdav_password').value,
                webdav_downloads_path: document.getElementById('webdav_downloads_path').value,
            };
            // Validate qBittorrent-specific inputs
            validateInput('qbittorrent_url', streamingProviderData.qbittorrent_config.qbittorrent_url);
            validateInput('seeding_time_limit', !isNaN(streamingProviderData.qbittorrent_config.seeding_time_limit));
            validateInput('seeding_ratio_limit', !isNaN(streamingProviderData.qbittorrent_config.seeding_ratio_limit));
            validateInput('play_video_after_download', !isNaN(streamingProviderData.qbittorrent_config.play_video_after));
            validateInput('category', streamingProviderData.qbittorrent_config.category);
        } else {
            validateInput('provider_token', document.getElementById('provider_token').value);
            streamingProviderData.token = document.getElementById('provider_token').value;
        }
        streamingProviderData.service = provider;
        streamingProviderData.enable_watchlist_catalogs = document.getElementById('enable_watchlist').checked;
        if (document.getElementById('download_via_browser')) {
            streamingProviderData.download_via_browser = document.getElementById('download_via_browser').checked;
        }
        streamingProviderData.only_show_cached_streams = document.getElementById('only_show_cached_streams').checked;
    } else {
        streamingProviderData = null;
    }

    const mediaflowEnabled = document.getElementById('enable_mediaflow').checked
    let mediaflowConfig = null;
    if (mediaflowEnabled) {
        mediaflowConfig = {
            proxy_url: document.getElementById('mediaflow_proxy_url').value,
            api_password: document.getElementById('mediaflow_api_password').value,
            public_ip: document.getElementById('mediaflow_public_ip').value,
            proxy_live_streams: document.getElementById('proxy_live_streams').checked,
            proxy_debrid_streams: document.getElementById('proxy_debrid_streams').checked
        };
        if (servicesNotNeedingDebridProxy.includes(provider)) {
            mediaflowConfig.proxy_debrid_streams = false;
        }
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

    // Collect and validate other user data
    const maxSizeSlider = document.getElementById('max_size_slider');
    const maxSizeValue = maxSizeSlider.value;
    const maxSize = maxSizeSlider.max;
    const maxSizeBytes = maxSizeValue === maxSize ? 'inf' : maxSizeValue;
    const maxStreamsPerResolution = document.getElementById('maxStreamsPerResolution').value;
    validateInput('maxStreamsPerResolution', maxStreamsPerResolution && !isNaN(maxStreamsPerResolution) && maxStreamsPerResolution > 0);

    let apiPassword = null;
    // Check for API Password if authentication is required
    if (document.getElementById('api_password')) {
        validateInput('api_password', document.getElementById('api_password').value);
        apiPassword = document.getElementById('api_password').value;
    }

    if (!isValid) {
        return null; // Return null if validation fails
    }

    // Collect sorting options with their directions
    const selectedSortingOptions = Array.from(
        document.querySelectorAll('#streamSortOrder .form-check-input:checked')
    ).map(el => ({
        key: el.value,
        direction: document.getElementById(`direction_${el.value}`).value
    }));

    // Collect and return the rest of the user data
    const torrentDisplayOption = document.querySelector('input[name="torrentDisplayOption"]:checked').value;

    const showTorrentLanguageFlag = document.getElementById('showTorrentLanguageFlag').checked;

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

    // Collect language sorting order
    const languageSorting = Array.from(document.querySelectorAll('input[name="selected_languages"]:checked')).map(el => el.value || null);

    // Collect quality filter data
    const selectedQualityFilters = Array.from(document.querySelectorAll('input[name="quality_filter"]:checked')).map(el => el.value);

    return {
        streaming_provider: streamingProviderData,
        selected_catalogs: Array.from(document.querySelectorAll('input[name="selected_catalogs"]:checked')).map(el => el.value),
        selected_resolutions: Array.from(document.querySelectorAll('input[name="selected_resolutions"]:checked')).map(el => el.value || null),
        enable_catalogs: document.getElementById('enable_catalogs').checked,
        enable_imdb_metadata: document.getElementById('enable_imdb_metadata').checked,
        max_size: maxSizeBytes,
        max_streams_per_resolution: maxStreamsPerResolution,
        torrent_sorting_priority: selectedSortingOptions,
        show_full_torrent_name: torrentDisplayOption === 'fullName',
        show_language_country_flag: showTorrentLanguageFlag,
        nudity_filter: selectedNudityFilters,
        certification_filter: selectedCertificationFilters,
        language_sorting: languageSorting,
        quality_filter: selectedQualityFilters,
        api_password: apiPassword,
        mediaflow_config: mediaflowConfig,
        rpdb_config: rpdbConfig,
        live_search_streams: document.getElementById('liveSearchStreams').checked,
        contribution_streams: document.getElementById('contributionStreams').checked,
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
    const sensitiveFields = [
        'provider_token',
        'password',
        'qbittorrent_password',
        'webdav_password',
        'mediaflow_api_password',
        'rpdb_api_key'
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

document.getElementById('provider_token').addEventListener('input', function () {
    adjustOAuthSectionDisplay();
});

document.getElementById('enable_mediaflow').addEventListener('change', function () {
    setElementDisplay('mediaflow_config', this.checked ? 'block' : 'none');
});

document.getElementById('enable_rpdb').addEventListener('change', function () {
    setElementDisplay('rpdb_config', this.checked ? 'block' : 'none');
});

document.getElementById('enable_mdblist').addEventListener('change', function() {
    setElementDisplay('mdblist_config', this.checked ? 'block' : 'none');
});


// Event listener for the slider
document.getElementById('max_size_slider').addEventListener('input', updateSizeOutput);

oAuthBtn.addEventListener('click', async function () {
    const provider = document.getElementById('provider_service').value;
    oAuthBtn.disabled = true;
    document.getElementById('provider_token').disabled = true;

    if (provider === 'seedr') {
        await initiateOAuthFlow('/streaming_provider/seedr/get-device-code', '/streaming_provider/seedr/authorize');
    } else if (provider === 'realdebrid') {
        await initiateOAuthFlow('/streaming_provider/realdebrid/get-device-code', '/streaming_provider/realdebrid/authorize');
    } else if (provider === 'debridlink') {
        await initiateOAuthFlow('/streaming_provider/debridlink/get-device-code', '/streaming_provider/debridlink/authorize')
    } else if (provider === 'premiumize') {
        return window.location.href = "/streaming_provider/premiumize/authorize";
    }
});


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
    updateProviderFields();
    updateSizeOutput();
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

    setupPasswordToggle('password', 'togglePassword', 'togglePasswordIcon');
    // Check for API Password if authentication is required
    if (document.getElementById('api_password')) {
        setupPasswordToggle('api_password', 'toggleApiPassword', 'toggleApiPasswordIcon');
    }
    setupPasswordToggle('qbittorrent_password', 'toggleQbittorrentPassword', 'toggleQbittorrentPasswordIcon');
    setupPasswordToggle('webdav_password', 'toggleWebdavPassword', 'toggleWebdavPasswordIcon');
    setupPasswordToggle('mediaflow_api_password', 'toggleMediaFlowPassword', 'toggleMediaFlowPasswordIcon');
    setupPasswordToggle('rpdb_api_key', 'toggleRPDBApiKey', 'toggleRPDBApiKeyIcon');
    setupPasswordToggle('mdblist_api_key', 'toggleMDBListApiKey', 'toggleMDBListApiKeyIcon');
});

document.addEventListener('DOMContentLoaded', function () {

    // Initialize sort direction toggles
    const toggleButtons = document.querySelectorAll('.sort-direction-toggle');
    toggleButtons.forEach(button => {
        button.addEventListener('click', function () {
            const sortId = this.dataset.sortId;
            const directionInput = document.getElementById(`direction_${sortId}`);
            const currentDirection = directionInput.value;
            const newDirection = currentDirection === 'desc' ? 'asc' : 'desc';

            // Update direction
            directionInput.value = newDirection;

            // Update button text and icon
            const iconContainer = this.querySelector('.sort-text');
            const tooltipTexts = this.attributes['sorting_info'].value.split('|');
            const newTooltipText = tooltipTexts[newDirection === 'asc' ? 1 : 0];

            this.title = newTooltipText;
            this.dataset.bsOriginalTitle = newTooltipText;

            iconContainer.innerHTML = `
                <i class="bi bi-sort-${newDirection === 'asc' ? 'up-alt' : 'down'}"></i>
                <span class="d-sm-inline ms-1">${newTooltipText}</span>
            `;
        });
    });

    // Enable/disable sort direction buttons based on checkbox state
    const sortCheckboxes = document.querySelectorAll('[name="selected_sorting_options"]');
    sortCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', function () {
            const sortId = this.value;
            const toggleButton = document.querySelector(`[data-sort-id="${sortId}"]`);
            const sortItem = this.closest('.sort-item');

            toggleButton.disabled = !this.checked;
            if (this.checked) {
                sortItem.classList.add('active');
            } else {
                sortItem.classList.remove('active');
            }
        });
    });
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
    new Sortable(document.getElementById('streamSortOrder'), {
        handle: '.sortable-list',
        animation: 150,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        delay: 200,
        delayOnTouchOnly: true,
        filter: '.form-check-input',
        preventOnFilter: false,
    });

    new Sortable(document.getElementById('languageSortOrder'), {
        handle: '.draggable-language',
        animation: 150,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        delay: 200,
        delayOnTouchOnly: true,
        filter: '.form-check-input',
        preventOnFilter: false,
    });
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

    // Show or hide the language sort section based on the sorting options
    document.querySelectorAll('input[name="selected_sorting_options"]').forEach(checkbox => {
        checkbox.addEventListener('change', function () {
            const languageSortSection = document.getElementById('languageSortSection');
            if (document.querySelector('input[name="selected_sorting_options"][value="language"]').checked) {
                languageSortSection.style.display = 'block';
            } else {
                languageSortSection.style.display = 'none';
            }
        });
    });
    // Initial check to show/hide the language sort section
    if (document.querySelector('input[name="selected_sorting_options"][value="language"]').checked) {
        document.getElementById('languageSortSection').style.display = 'block';
    }

    // Add UI elements for catalogs
    addSelectDeselectSearch('catalogs', 'Catalogs', 'input[name="selected_catalogs"]');

    // Add UI elements for languages
    addSelectDeselectSearch('languageSortOrder', 'Languages', 'input[name="selected_languages"]');

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

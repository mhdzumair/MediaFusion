// ---- Variables ----
const oAuthBtn = document.getElementById('oauth_btn');
let currentAuthorizationToken = null;
const servicesRequiringCredentials = ['pikpak',];
const providerSignupLinks = {
    pikpak: 'https://mypikpak.com/drive/activity/invited?invitation-code=52875535',
    seedr: 'https://www.seedr.cc/?r=2726511',
    offcloud: 'https://offcloud.com/?=9932cd9f',
    realdebrid: 'http://real-debrid.com/?id=9490816',
    debridlink: 'https://debrid-link.com/id/kHgZs',
    alldebrid: 'https://alldebrid.com/?uid=3ndha&lang=en',
    torbox: 'https://torbox.app/subscription?referral=339b923e-fb23-40e7-8031-4af39c212e3c',
    premiumize: 'https://www.premiumize.me',
    qbittorrent: 'https://github.com/mhdzumair/MediaFusion/tree/main/streaming_providers/qbittorrent#qbittorrent-webdav-setup-options-with-mediafusion',
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
    const tokenInput = document.getElementById('provider_token');
    const watchlistLabel = document.getElementById('watchlist_label');


    if (provider in providerSignupLinks) {
        document.getElementById('signup_link').href = providerSignupLinks[provider];
        setElementDisplay('signup_section', 'block');
    } else {
        setElementDisplay('signup_section', 'none');
    }

    // Toggle visibility of credentials and token input based on provider
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
        tokenInput.value = '';
        tokenInput.disabled = false;
        currentAuthorizationToken = null;
        oAuthBtn.disabled = false;
        setElementDisplay('device_code_section', 'none');
    }

    adjustOAuthSectionDisplay();
}

// Function to get installation URL
async function getInstallationUrl(isRedirect = false) {
    const userData = getUserData();
    let urlPrefix = window.location.protocol + "//";
    if (isRedirect) {
        urlPrefix = "stremio://";
    }

    if (!userData) {
        showNotification('Validation failed. Please check your input.', 'error');
        return null;
    }

    try {
        const response = await fetch('/encrypt-user-data', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(userData)
        });
        const data = await response.json();
        if (!data.encrypted_str) {
            showNotification('An error occurred while encrypting user data', 'error');
            return null;
        }
        return urlPrefix + window.location.host + "/" + data.encrypted_str + "/manifest.json";
    } catch (error) {
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
        validateInput('mediaflow_proxy_url', validateUrl(mediaflowConfig.proxy_url));
        validateInput('mediaflow_api_password', mediaflowConfig.api_password.trim() !== '');
    }

    let rpdbConfig = null;
    if (document.getElementById('enable_rpdb').checked) {
        rpdbConfig = {
            api_key: document.getElementById('rpdb_api_key').value,
        };
        validateInput('rpdb_api_key', rpdbConfig.api_key.trim() !== '');
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

    // Collect and return the rest of the user data
    const selectedSortingOptions = Array.from(document.querySelectorAll('#streamSortOrder .form-check-input:checked')).map(el => el.value);
    const torrentDisplayOption = document.querySelector('input[name="torrentDisplayOption"]:checked').value;

    // Collect nudity filter data
    const selectedNudityFilters = Array.from(document.querySelectorAll('input[name="nudity_filter"]:checked')).map(el => el.value);

    // Collect certification filter data
    const selectedCertificationFilters = Array.from(document.querySelectorAll('input[name="certification_filter"]:checked')).map(el => el.value);

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
        nudity_filter: selectedNudityFilters,
        certification_filter: selectedCertificationFilters,
        language_sorting: languageSorting,
        quality_filter: selectedQualityFilters,
        api_password: apiPassword,
        mediaflow_config: mediaflowConfig,
        rpdb_config: rpdbConfig,
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
    const installationUrl = await getInstallationUrl();

    if (installationUrl) {
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
        }
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

    const installationUrl = await getInstallationUrl(true);
    if (installationUrl) {
        window.location.href = installationUrl;
    }
});

document.getElementById('shareBtn').addEventListener('click', async function (event) {
    event.preventDefault();
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
    updateProviderFields();
    updateSizeOutput();
});

document.addEventListener('DOMContentLoaded', function () {
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
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

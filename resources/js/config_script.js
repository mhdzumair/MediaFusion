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
    torbox: 'https://torbox.app/login?ref=mediafusion',
    premiumize: 'https://www.premiumize.me',
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
        } else {
            setElementDisplay('credentials', 'none');
            setElementDisplay('token_input', 'block');
        }
        setElementDisplay('watchlist_section', 'block');
        watchlistLabel.textContent = `Enable ${provider.charAt(0).toUpperCase() + provider.slice(1)} Watchlist`;
    } else {
        setElementDisplay('credentials', 'none');
        setElementDisplay('token_input', 'none');
        setElementDisplay('watchlist_section', 'none');
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
async function getInstallationUrl(userData) {
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
        return "stremio://" + window.location.host + "/" + data.encrypted_str + "/manifest.json";
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

    if (provider) {
        if (servicesRequiringCredentials.includes(provider)) {
            validateInput('username', document.getElementById('username').value);
            validateInput('password', document.getElementById('password').value);
            streamingProviderData.username = document.getElementById('username').value;
            streamingProviderData.password = document.getElementById('password').value;
        } else {
            validateInput('provider_token', document.getElementById('provider_token').value);
            streamingProviderData.token = document.getElementById('provider_token').value;
        }
        streamingProviderData.service = provider;
        streamingProviderData.enable_watchlist_catalogs = document.getElementById('enable_watchlist').checked;
    } else {
        streamingProviderData = null;
    }

    const maxSizeSlider = document.getElementById('max_size_slider');
    const maxSizeValue = maxSizeSlider.value;
    const maxSize = maxSizeSlider.max;
    const maxSizeBytes = maxSizeValue === maxSize ? 'inf' : maxSizeValue;

    const maxStreamsPerResolution = document.getElementById('maxStreamsPerResolution').value;
    validateInput('maxStreamsPerResolution', maxStreamsPerResolution && !isNaN(maxStreamsPerResolution) && maxStreamsPerResolution > 0);

    if (!isValid) {
        return null; // Return null if validation fails
    }

    const selectedSortingOptions = Array.from(document.querySelectorAll('#streamSortOrder .form-check-input:checked')).map(el => el.value);
    const torrentDisplayOption = document.querySelector('input[name="torrentDisplayOption"]:checked').value;

    // Return user data if all validations pass
    return {
        streaming_provider: streamingProviderData,
        selected_catalogs: Array.from(document.querySelectorAll('input[name="selected_catalogs"]:checked')).map(el => el.value),
        selected_resolutions: Array.from(document.querySelectorAll('input[name="selected_resolutions"]:checked')).map(el => el.value),
        enable_catalogs: document.getElementById('enable_catalogs').checked,
        max_size: maxSizeBytes,
        max_streams_per_resolution: maxStreamsPerResolution,
        torrent_sorting_priority: selectedSortingOptions,
        show_full_torrent_name: torrentDisplayOption === 'fullName',
    };
}

// ---- Event Listeners ----

document.getElementById('provider_token').addEventListener('input', function () {
    adjustOAuthSectionDisplay();
});

// Event listener for the slider
document.getElementById('max_size_slider').addEventListener('input', updateSizeOutput);

document.getElementById('togglePassword').addEventListener('click', function (e) {
    const passwordInput = document.getElementById('password');
    const passwordIcon = document.getElementById('togglePasswordIcon');
    if (passwordInput.type === "password") {
        passwordInput.type = "text";
        passwordIcon.className = "bi bi-eye-slash";
    } else {
        passwordInput.type = "password";
        passwordIcon.className = "bi bi-eye";
    }
});

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
    const userData = getUserData();

    if (!userData) {
        showNotification('Validation failed. Please check your input.', 'error');
        return;
    }
    const installationUrl = await getInstallationUrl(userData);
    if (installationUrl) {
        window.location.href = installationUrl;
    }
});

document.getElementById('shareBtn').addEventListener('click', async function (event) {
    event.preventDefault();

    const userData = getUserData();

    if (!userData) {
        showNotification('Validation failed. Please check your input.', 'error');
        return;
    }

    // If userData is valid, get the installation URL
    const installationUrl = await getInstallationUrl(userData);
    if (installationUrl) {
        // Check if the Web Share API is available
        if (navigator.share) {
            navigator.share({
                title: 'MediaFusion Addon Installation',
                url: installationUrl,
            }).then(() => {
                showNotification('Installation URL shared successfully. Do not share this URL with unknown person', 'success');
            }).catch((error) => {
                showNotification('An error occurred while sharing the installation URL', 'error');
            });
        } else {
            // Copy the installation URL to the clipboard
            navigator.clipboard.writeText(installationUrl).then(() => {
                showNotification('Installation URL copied to clipboard. Do not share this URL with unknown person', 'success');
                // You can display a message to the user indicating that the URL has been copied.
            }).catch((error) => {
                showNotification('An error occurred while copying the installation URL to the clipboard', 'error');
            });
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

});

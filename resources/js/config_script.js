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
    alldebrid: 'https://alldebrid.com/?uid=3ndha&lang=en'
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


// ---- Helper Functions ----

function setElementDisplay(elementId, displayStatus) {
    document.getElementById(elementId).style.display = displayStatus;
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

    if ((provider === 'seedr' || provider === 'realdebrid' || provider === 'debridlink') && !providerToken) {
        setElementDisplay('oauth_section', 'block');
    } else {
        setElementDisplay('oauth_section', 'none');
    }
}

function updateProviderFields(isChangeEvent = false) {
    const provider = document.getElementById('provider_service').value;
    const tokenInput = document.getElementById('provider_token');

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
    } else {
        setElementDisplay('credentials', 'none');
        setElementDisplay('token_input', 'none');
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

// ---- Event Listeners ----

document.getElementById('provider_token').addEventListener('input', function () {
    adjustOAuthSectionDisplay();
});

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
        await initiateOAuthFlow('/seedr/get-device-code', '/seedr/authorize');
    } else if (provider === 'realdebrid') {
        await initiateOAuthFlow('/realdebrid/get-device-code', '/realdebrid/authorize');
    } else if (provider === 'debridlink') {
        await initiateOAuthFlow('/debridlink/get-device-code', '/debridlink/authorize')
    }
});


document.getElementById('configForm').addEventListener('submit', async function (event) {
    event.preventDefault();

    const provider = document.getElementById('provider_service').value;
    let isValid = true;

    const validateInput = (elementId, condition) => {
        const element = document.getElementById(elementId);
        if (condition) {
            element.classList.remove('is-invalid');
        } else {
            element.classList.add('is-invalid');
            isValid = false;
        }
    };

    // Conditional validation based on provider
    if (provider && !servicesRequiringCredentials.includes(provider)) {
        // Validate token for providers that use token-based authentication
        validateInput('provider_token', document.getElementById('provider_token').value);
    } else if (servicesRequiringCredentials.includes(provider)) {
        // Validate username and password for providers requiring credentials
        validateInput('username', document.getElementById('username').value);
        validateInput('password', document.getElementById('password').value);
    }

    if (isValid) {
        let streamingProviderData = {};
        if (provider) {
            if (servicesRequiringCredentials.includes(provider)) {
                // Include username and password
                streamingProviderData.username = document.getElementById('username').value;
                streamingProviderData.password = document.getElementById('password').value;
            } else {
                // Include token
                streamingProviderData.token = document.getElementById('provider_token').value;
            }
            streamingProviderData.service = provider;
        } else {
            streamingProviderData = null;
        }

        const userData = {
            streaming_provider: streamingProviderData,
            selected_catalogs: Array.from(document.querySelectorAll('input[name="selected_catalogs"]:checked')).map(el => el.value),
            selected_resolutions: Array.from(document.querySelectorAll('input[name="selected_resolutions"]:checked')).map(el => el.value),
        };

        try {
            const response = await fetch('/encrypt-user-data', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(userData)
            });

            const data = await response.json();
            window.location.href = "stremio://" + window.location.host + "/" + data.encrypted_str + "/manifest.json";
        } catch (error) {
            console.error('Error encrypting user data:', error);
        }
    }
});

// ---- Initial Setup ----

document.addEventListener('DOMContentLoaded', function () {
    updateProviderFields();
});

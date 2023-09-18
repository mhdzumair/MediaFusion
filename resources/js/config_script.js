// ---- Variables ----
const oAuthBtn = document.getElementById('oauth_btn');
let currentAuthorizationToken = null;

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

    if ((provider === 'seedr' || provider === 'realdebrid') && !providerToken) {
        setElementDisplay('oauth_section', 'block');
    } else {
        setElementDisplay('oauth_section', 'none');
    }
}

function updateProviderFields() {
    const provider = document.getElementById('provider_service').value;
    const tokenInput = document.getElementById('provider_token');

    if (provider) {
        setOAuthBtnTextContent(provider);
        setElementDisplay('token_input', 'block');
    } else {
        setElementDisplay('token_input', 'none');
    }

    // Reset on provider change
    tokenInput.value = '';
    tokenInput.disabled = false;
    currentAuthorizationToken = null;
    oAuthBtn.disabled = false;
    setElementDisplay('device_code_section', 'none');

    adjustOAuthSectionDisplay();
}

// ---- Event Listeners ----

document.getElementById('provider_token').addEventListener('input', function () {
    adjustOAuthSectionDisplay();
});

oAuthBtn.addEventListener('click', async function () {
    const provider = document.getElementById('provider_service').value;
    oAuthBtn.disabled = true;
    document.getElementById('provider_token').disabled = true;

    if (provider === 'seedr') {
        await initiateOAuthFlow('/seedr/get-device-code', '/seedr/authorize');
    } else if (provider === 'realdebrid') {
        await initiateOAuthFlow('/realdebrid/get-device-code', '/realdebrid/authorize');
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

    if (provider) {
        validateInput('provider_token', document.getElementById('provider_token').value);
    }

    if (isValid) {
        let streamingProviderData = null;
        let tokenValue = document.getElementById('provider_token').value;

        if (provider) {
            streamingProviderData = {
                service: provider,
                token: tokenValue ? tokenValue : null,
            };
        }
        const userData = {
            streaming_provider: streamingProviderData,
            preferred_movie_languages: Array.from(document.querySelectorAll('input[name="preferred_movie_languages"]:checked')).map(el => el.value),
            preferred_series_languages: Array.from(document.querySelectorAll('input[name="preferred_series_languages"]:checked')).map(el => el.value)
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

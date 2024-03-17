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


// Function to update form fields based on scraper selection
function updateFormFields() {
    const scraperType = document.getElementById('scraperSelect').value;
    let parametersHtml = '';
    const scraperParametersDiv = document.getElementById('scraperParameters');

    if (scraperType === 'tamilmv' || scraperType === 'tamilblasters') {
        parametersHtml += `
            <div class="mb-3">
                <label for="pages" class="form-label">Number of Pages</label>
                <input type="number" class="form-control" id="pages" name="pages" value="1">
            </div>
            <div class="mb-3">
                <label for="startPage" class="form-label">Start Page</label>
                <input type="number" class="form-control" id="startPage" name="start_page" value="1">
            </div>
        `;
    } else if (scraperType === 'scrapy') {
        parametersHtml += `
            <div class="mb-3">
                <label for="spiderName" class="form-label">Spider Name</label>
                <select class="form-select" id="spiderName" name="spider_name">
                    <option value="formula_tgx">Formula TGX</option>
                    <option value="mhdtvworld">MHDTV World</option>
                    <option value="mhdtvsports">MHDTV Sports</option>
                    <option value="tamilultra">Tamil Ultra</option>
                </select>
            </div>
        `;
    }

    // Update the form fields based on the selection
    scraperParametersDiv.innerHTML = parametersHtml;
}

// Function to submit the form and display the response using Toastr
async function submitScraperForm() {
    const apiPassword = document.getElementById('api_password') ? document.getElementById('api_password').value : '';
    const scraperType = document.getElementById('scraperSelect').value;
    let payload = {
        scraper_type: scraperType,
        api_password: apiPassword,
        pages: document.getElementById('pages') ? document.getElementById('pages').value : 1,
        start_page: document.getElementById('startPage') ? document.getElementById('startPage').value : 1,
        spider_name: document.getElementById('spiderName') ? document.getElementById('spiderName').value : ''
    };

    try {
        const response = await fetch("/scraper/run", {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (response.ok) {
            showNotification(data.status, 'success');
        } else {
            showNotification(data.detail, 'error');
        }
    } catch (error) {
        console.error('Error submitting scraper form:', error);
        showNotification('Error submitting scraper form. Please check the console for more details.', 'error');
    }
}


// Initial update for form fields on page load
document.addEventListener('DOMContentLoaded', updateFormFields);

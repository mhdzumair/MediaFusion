// RSS Feed Manager Script

let API_PASSWORD = localStorage.getItem('api_password') || '';

// Initialize on document ready
document.addEventListener('DOMContentLoaded', function () {
    // Check if API password is stored
    if (!API_PASSWORD) {
        showLoginForm();
    } else {
        showMainContent();
    }

    // Setup login form event listener
    document.getElementById('loginForm').addEventListener('submit', handleLogin);

    // Setup logout button
    document.getElementById('logoutBtn').addEventListener('click', handleLogout);

    // Setup event listeners for main content
    document.getElementById('rssForm').addEventListener('submit', handleRssFormSubmit);
    document.getElementById('testFeedBtn').addEventListener('click', testRssFeed);
    document.getElementById('bulkActionBtn').addEventListener('click', handleBulkAction);
    document.getElementById('selectAllCheckbox').addEventListener('change', toggleSelectAll);
    document.getElementById('runRssScraper').addEventListener('click', runRssScraper);
    document.getElementById('feedUrl').addEventListener('change', clearTestResults);
    document.getElementById('toggleFormBtn').addEventListener('click', toggleRssForm);

    // Initialize new functionalities
    initRegexTesting();
    initCatalogSelection();
    initCatalogPatterns();
});

// Handle login form submission
async function handleLogin(event) {
    event.preventDefault();

    const password = document.getElementById('apiPassword').value;
    const remember = document.getElementById('rememberPassword').checked;
    const errorElement = document.getElementById('loginError');

    errorElement.style.display = 'none';

    try {
        // Verify the password is correct
        const response = await fetch('/rss/feeds', {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': password
            }
        });

        if (!response.ok) {
            throw new Error('Invalid API password');
        }

        // Store password if remember is checked
        if (remember) {
            localStorage.setItem('api_password', password);
        }

        // Update global variable
        API_PASSWORD = password;

        // Show main content
        showMainContent();

    } catch (error) {
        errorElement.textContent = error.message;
        errorElement.style.display = 'block';
    }
}

// Handle logout
function handleLogout() {
    // Clear stored password
    localStorage.removeItem('api_password');
    API_PASSWORD = '';

    // Show login form
    showLoginForm();
}

// Show login form
function showLoginForm() {
    document.getElementById('loginOverlay').style.display = 'flex';
    document.getElementById('mainContent').style.display = 'none';
}

// Show main content
function showMainContent() {
    document.getElementById('loginOverlay').style.display = 'none';
    document.getElementById('mainContent').style.display = 'block';

    // Load RSS feeds now that we're authenticated
    loadRssFeeds();
}


// Toggle RSS form visibility
function toggleRssForm(option = null) {
    const formContainer = document.getElementById('rssFormContainer');
    const toggleIcon = document.getElementById('toggleFormIcon');
    const toggleText = document.getElementById('toggleFormText');
    if (typeof option !== 'string') {
        option = formContainer.style.display === 'none' ? 'show' : 'hide';
    }

    formContainer.style.display = option === 'show' ? 'block' : 'none';
    toggleIcon.className = option === 'show' ? 'bi bi-chevron-up' : 'bi bi-chevron-down';
    toggleText.textContent = option === 'show' ? 'Hide Form' : 'Show Form';
}

// Notification helper function
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

// Load and display all RSS feeds
async function loadRssFeeds() {
    try {
        const response = await fetch('/rss/feeds', {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': API_PASSWORD
            }
        });

        if (!response.ok) {
            if (response.status === 401) {
                // Invalid API password, show login
                showLoginForm();
                return;
            }
            throw new Error(`Failed to load RSS feeds: ${response.statusText}`);
        }

        const feeds = await response.json();
        displayRssFeeds(feeds);
    } catch (error) {
        console.error('Error loading RSS feeds:', error);
        showNotification(`Error loading RSS feeds: ${error.message}`, 'error');
    }
}

// Display feeds in the table
function displayRssFeeds(feeds) {
    const tableBody = document.getElementById('rssFeedsTableBody');
    tableBody.innerHTML = '';

    if (feeds.length === 0) {
        // Show empty state
        tableBody.innerHTML = `
            <tr>
                <td colspan="5" class="text-center p-5">
                    <div class="empty-state">
                        <i class="bi bi-rss fs-1 text-muted"></i>
                        <p class="mt-3">No RSS feeds found</p>
                        <p class="text-muted small">Add your first RSS feed using the form above</p>
                    </div>
                </td>
            </tr>
        `;
        return;
    }

    feeds.forEach(feed => {
        const row = document.createElement('tr');

        // Determine status class
        const statusClass = feed.active ? 'bg-success' : 'bg-secondary';

        // Format last scraped date
        const lastScraped = feed.last_scraped
            ? new Date(feed.last_scraped).toLocaleString()
            : 'Never';

        // Display catalog assignment method
        const getCatalogDisplayText = (feed) => {
            if (feed.auto_detect_catalog) {
                const patternCount = (feed.catalog_patterns || []).filter(p => p.enabled).length;
                if (patternCount > 0) {
                    return `🎯 Smart Detection (${patternCount} patterns)`;
                } else {
                    return `🤖 Auto-Detect (Built-in)`;
                }
            } else {
                return `📄 RSS Feed Only`;
            }
        };

        const catalogs = getCatalogDisplayText(feed);

        // Format status display
        const sourceDisplay = feed.source || 'RSS';
        const torrentType = feed.torrent_type || 'public';
        const statusText = `${sourceDisplay} (${torrentType})`;

        // Format catalog info
        const catalogInfo = getCatalogDisplayText(feed);

        row.innerHTML = `
            <td>
                <div class="form-check">
                    <input class="form-check-input feed-checkbox" type="checkbox" value="${feed._id}" id="feed_${feed._id}">
                    <label class="form-check-label" for="feed_${feed._id}"></label>
                </div>
            </td>
            <td>
                <div class="d-flex align-items-center">
                    <span class="status-dot ${statusClass} me-2"></span>
                    <span class="feed-name">${feed.name}</span>
                </div>
            </td>
            <td>
                <div class="d-flex flex-column">
                    <span class="text-muted small">${statusText}</span>
                    <span class="text-info small">${catalogInfo}</span>
                </div>
            </td>
            <td>${lastScraped}</td>
            <td>
                <div class="d-flex gap-2">
                    <button class="btn btn-sm btn-outline-primary" onclick="editFeed('${feed._id}')">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteFeed('${feed._id}')">
                        <i class="bi bi-trash"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-${feed.active ? 'warning' : 'success'}" onclick="toggleFeedStatus('${feed._id}', ${!feed.active})">
                        <i class="bi bi-${feed.active ? 'pause-fill' : 'play-fill'}"></i>
                    </button>
                </div>
            </td>
        `;

        tableBody.appendChild(row);
    });

}

// Handle RSS form submission
async function handleRssFormSubmit(event) {
    event.preventDefault();

    const feedId = document.getElementById('feedId').value;
    const isEdit = !!feedId;

    // Get form data with additional fields
    const formData = {
        name: document.getElementById('feedName').value,
        url: document.getElementById('feedUrl').value,
        active: document.getElementById('feedActive').checked,

        // Source and torrent type
        source: document.getElementById('feedSource').value,
        torrent_type: document.getElementById('torrentType').value,

        // Auto-detect catalog settings
        auto_detect_catalog: document.getElementById('autoDetectCatalog').checked,
        catalog_patterns: document.getElementById('autoDetectCatalog').checked ? collectCatalogPatterns() : [],

        // Parsing patterns
        parsing_patterns: {
            title: document.getElementById('patternTitle').value,
            description: document.getElementById('patternDescription').value,
            pubDate: document.getElementById('patternPubDate').value,
            poster: document.getElementById('patternPoster').value,
            background: document.getElementById('patternBackground').value,
            logo: document.getElementById('patternLogo').value,
            category: document.getElementById('patternCategory').value,
            magnet: document.getElementById('patternMagnet').value || null,
            magnet_regex: document.getElementById('patternMagnetRegex').value || null,
            torrent: document.getElementById('patternTorrent').value || null,
            torrent_regex: document.getElementById('patternTorrentRegex').value || null,
            size: document.getElementById('patternSize').value || null,
            size_regex: document.getElementById('patternSizeRegex').value || null,
            seeders: document.getElementById('patternSeeders').value || null,
            seeders_regex: document.getElementById('patternSeedersRegex').value || null,
            category_regex: document.getElementById('patternCategoryRegex').value || null,
            episode_name_parser: document.getElementById('patternEpisodeNameParser').value || null,

        },

        // Filtering options
        filters: {
            title_filter: document.getElementById('titleFilter').value || null,
            title_exclude_filter: document.getElementById('titleExcludeFilter').value || null,
            min_size_mb: document.getElementById('minSizeFilter').value ? parseInt(document.getElementById('minSizeFilter').value) : null,
            max_size_mb: document.getElementById('maxSizeFilter').value ? parseInt(document.getElementById('maxSizeFilter').value) : null,
            min_seeders: document.getElementById('minSeedersFilter').value ? parseInt(document.getElementById('minSeedersFilter').value) : null,
            category_filter: document.getElementById('categoryFilter').value ? document.getElementById('categoryFilter').value.split(',').map(s => s.trim()) : null,
        }
    };

    // Validate required fields
    if (!formData.name || !formData.url) {
        showNotification('Please fill all required fields', 'error');
        return;
    }

    try {
        const url = isEdit ? `/rss/feeds/${feedId}` : '/rss/feeds';
        const method = isEdit ? 'PUT' : 'POST';

        const response = await fetch(url, {
            method,
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': API_PASSWORD
            },
            body: JSON.stringify(formData)
        });

        if (!response.ok) {
            if (response.status === 401) {
                showNotification('Your session has expired. Please log in again.', 'error');
                handleLogout();
                return;
            }

            const error = await response.json();
            throw new Error(error.detail || 'Failed to save RSS feed');
        }

        // Show success message
        showNotification(`RSS feed ${isEdit ? 'updated' : 'created'} successfully`, 'success');

        // Reset form and reload feeds
        resetForm();
        loadRssFeeds();
    } catch (error) {
        console.error('Error saving RSS feed:', error);
        showNotification(`Error: ${error.message}`, 'error');
    }
}

// Load feed data for editing
async function editFeed(feedId) {
    try {
        const response = await fetch(`/rss/feeds/${feedId}`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': API_PASSWORD
            }
        });

        if (!response.ok) {
            if (response.status === 401) {
                showNotification('Your session has expired. Please log in again.', 'error');
                handleLogout();
                return;
            }
            throw new Error(`Failed to load feed: ${response.statusText}`);
        }

        const feed = await response.json();

        // Populate basic form fields
        document.getElementById('feedId').value = feed._id;
        document.getElementById('feedName').value = feed.name;
        document.getElementById('feedUrl').value = feed.url;
        document.getElementById('feedActive').checked = feed.active;
        document.getElementById('feedSource').value = feed.source || '';
        document.getElementById('torrentType').value = feed.torrent_type || 'public';

        // No need to populate static catalog checkboxes since we removed them

        // Auto-detect catalog settings
        document.getElementById('autoDetectCatalog').checked = feed.auto_detect_catalog || false;
        document.getElementById('catalogPatternContainer').style.display = feed.auto_detect_catalog ? 'block' : 'none';

        // Load catalog patterns
        if (feed.catalog_patterns && feed.catalog_patterns.length > 0) {
            loadCatalogPatterns(feed.catalog_patterns);
        }

        // Populate parsing patterns
        if (feed.parsing_patterns) {
            document.getElementById('patternTitle').value = feed.parsing_patterns.title || '';
            document.getElementById('patternDescription').value = feed.parsing_patterns.description || '';
            document.getElementById('patternPubDate').value = feed.parsing_patterns.pubDate || '';
            document.getElementById('patternPoster').value = feed.parsing_patterns.poster || feed.parsing_patterns.image || '';
            document.getElementById('patternBackground').value = feed.parsing_patterns.background || '';
            document.getElementById('patternLogo').value = feed.parsing_patterns.logo || '';
            document.getElementById('patternCategory').value = feed.parsing_patterns.category || '';
            document.getElementById('patternMagnet').value = feed.parsing_patterns.magnet || '';
            document.getElementById('patternMagnetRegex').value = feed.parsing_patterns.magnet_regex || '';
            document.getElementById('patternTorrent').value = feed.parsing_patterns.torrent || '';
            document.getElementById('patternTorrentRegex').value = feed.parsing_patterns.torrent_regex || '';
            document.getElementById('patternSize').value = feed.parsing_patterns.size || '';
            document.getElementById('patternSizeRegex').value = feed.parsing_patterns.size_regex || '';
            document.getElementById('patternSeeders').value = feed.parsing_patterns.seeders || '';
            document.getElementById('patternSeedersRegex').value = feed.parsing_patterns.seeders_regex || '';
            document.getElementById('patternCategoryRegex').value = feed.parsing_patterns.category_regex || '';
            document.getElementById('patternEpisodeNameParser').value = feed.parsing_patterns.episode_name_parser || '';

        }

        // Populate filters
        if (feed.filters) {
            document.getElementById('titleFilter').value = feed.filters.title_filter || '';
            document.getElementById('titleExcludeFilter').value = feed.filters.title_exclude_filter || '';
            document.getElementById('minSizeFilter').value = feed.filters.min_size_mb || '';
            document.getElementById('maxSizeFilter').value = feed.filters.max_size_mb || '';
            document.getElementById('minSeedersFilter').value = feed.filters.min_seeders || '';
            document.getElementById('categoryFilter').value = feed.filters.category_filter ? feed.filters.category_filter.join(', ') : '';
        }

        // Update form title and button text
        document.getElementById('formTitle').textContent = 'Edit RSS Feed';
        document.getElementById('submitBtn').textContent = 'Update Feed';

        // Show cancel button
        document.getElementById('cancelEditBtn').style.display = 'inline-block';

        // Show form
        toggleRssForm('show');

        // Scroll to form
        document.getElementById('rssForm').scrollIntoView({ behavior: 'smooth' });
    } catch (error) {
        console.error('Error loading feed for edit:', error);
        showNotification(`Error: ${error.message}`, 'error');
    }
}

// Reset the form to add new state
function resetForm() {
    document.getElementById('rssForm').reset();
    document.getElementById('feedId').value = '';
    document.getElementById('formTitle').textContent = 'Add RSS Feed';
    document.getElementById('submitBtn').textContent = 'Add Feed';
    document.getElementById('cancelEditBtn').style.display = 'none';
    document.getElementById('testResults').innerHTML = '';
    document.getElementById('testResultsCard').style.display = 'none';
}

// Delete a feed
async function deleteFeed(feedId) {
    // Confirm deletion
    if (!confirm('Are you sure you want to delete this RSS feed?')) {
        return;
    }

    try {
        const response = await fetch(`/rss/feeds/${feedId}`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': API_PASSWORD
            }
        });

        if (!response.ok) {
            if (response.status === 401) {
                showNotification('Your session has expired. Please log in again.', 'error');
                handleLogout();
                return;
            }
            throw new Error(`Failed to delete feed: ${response.statusText}`);
        }

        showNotification('RSS feed deleted successfully', 'success');
        loadRssFeeds();
    } catch (error) {
        console.error('Error deleting feed:', error);
        showNotification(`Error: ${error.message}`, 'error');
    }
}

// Toggle feed active status
async function toggleFeedStatus(feedId, active) {
    try {
        const response = await fetch(`/rss/feeds/${feedId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': API_PASSWORD
            },
            body: JSON.stringify({ active })
        });

        if (!response.ok) {
            if (response.status === 401) {
                showNotification('Your session has expired. Please log in again.', 'error');
                handleLogout();
                return;
            }
            throw new Error(`Failed to update feed status: ${response.statusText}`);
        }

        showNotification(`RSS feed ${active ? 'activated' : 'deactivated'} successfully`, 'success');
        loadRssFeeds();
    } catch (error) {
        console.error('Error updating feed status:', error);
        showNotification(`Error: ${error.message}`, 'error');
    }
}

// Test an RSS feed
async function testRssFeed() {
    const url = document.getElementById('feedUrl').value;

    if (!url) {
        showNotification('Please enter a feed URL to test', 'error');
        return;
    }

    // Show loading indicator
    document.getElementById('testResults').innerHTML = `
        <div class="text-center my-4">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="mt-2">Testing feed...</p>
        </div>
    `;
    document.getElementById('testResultsCard').style.display = 'block';

    try {
        // Get custom patterns if any are set
        const patterns = {
            title: document.getElementById('patternTitle').value || undefined,
            description: document.getElementById('patternDescription').value || undefined,
            pubDate: document.getElementById('patternPubDate').value || undefined,
            poster: document.getElementById('patternPoster').value || undefined,
            background: document.getElementById('patternBackground').value || undefined,
            logo: document.getElementById('patternLogo').value || undefined,
            category: document.getElementById('patternCategory').value || undefined,
            magnet: document.getElementById('patternMagnet').value || undefined,
            magnet_regex: document.getElementById('patternMagnetRegex').value || undefined,
            torrent: document.getElementById('patternTorrent').value || undefined,
            torrent_regex: document.getElementById('patternTorrentRegex').value || undefined,
            size: document.getElementById('patternSize').value || undefined,
            size_regex: document.getElementById('patternSizeRegex').value || undefined,
            seeders: document.getElementById('patternSeeders').value || undefined,
            seeders_regex: document.getElementById('patternSeedersRegex').value || undefined,
            episode_name_parser: document.getElementById('patternEpisodeNameParser').value || undefined,
            category_regex: document.getElementById('patternCategoryRegex').value || undefined,

        };

        // Filter out undefined values
        const filteredPatterns = Object.fromEntries(
            Object.entries(patterns).filter(([_, v]) => v !== undefined)
        );

        const response = await fetch('/rss/feeds/test-feed', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': API_PASSWORD
            },
            body: JSON.stringify({
                url,
                patterns: Object.keys(filteredPatterns).length > 0 ? filteredPatterns : undefined
            })
        });

        if (!response.ok) {
            if (response.status === 401) {
                showNotification('Your session has expired. Please log in again.', 'error');
                handleLogout();
                return;
            }
            throw new Error(`Failed to test feed: ${response.statusText}`);
        }

        const result = await response.json();

        if (result.status === 'error') {
            document.getElementById('testResults').innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>
                    ${result.message}
                </div>
            `;
            return;
        }

        // Store sample data globally for regex testing
        window.lastTestSample = result.sample_item;

        // Update regex test button states after new sample data is available
        updateRegexTestButtonStates();

        // Update the form with detected patterns
        if (result.detected_patterns) {
            document.getElementById('patternTitle').value = result.detected_patterns.title || '';
            document.getElementById('patternDescription').value = result.detected_patterns.description || '';
            document.getElementById('patternPubDate').value = result.detected_patterns.pubDate || '';
            document.getElementById('patternPoster').value = result.detected_patterns.poster || result.detected_patterns.image || '';
            document.getElementById('patternBackground').value = result.detected_patterns.background || '';
            document.getElementById('patternLogo').value = result.detected_patterns.logo || '';
            document.getElementById('patternCategory').value = result.detected_patterns.category || '';
            document.getElementById('patternMagnet').value = result.detected_patterns.magnet || '';
            document.getElementById('patternTorrent').value = result.detected_patterns.torrent || '';
            document.getElementById('patternSize').value = result.detected_patterns.size || '';
            document.getElementById('patternSeeders').value = result.detected_patterns.seeders || '';
        }

        // Update regex test button states after patterns are populated
        updateRegexTestButtonStates();

        // Display test results
        let resultsHtml = `
            <div class="alert alert-success mb-3">
                <i class="bi bi-check-circle-fill me-2"></i>
                ${result.message}
            </div>
        `;

        if (result.sample_item) {
            resultsHtml += `
                <div class="sample-item-preview">
                    <h6>Sample Item Data</h6>
                    <pre class="code-block">${syntaxHighlight(JSON.stringify(result.sample_item, null, 2))}</pre>
                </div>
            `;
        }

        if (result.detected_patterns) {
            resultsHtml += createDetectedPatternsDisplay(result.detected_patterns);
        }

        document.getElementById('testResults').innerHTML = resultsHtml;
    } catch (error) {
        console.error('Error testing feed:', error);
        document.getElementById('testResults').innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                Error testing feed: ${error.message}
            </div>
        `;
    }
}



// Create detected patterns display
function createDetectedPatternsDisplay(patterns) {
    let html = `
        <div class="detected-patterns">
            <h6>Detected Patterns</h6>
    `;

    Object.entries(patterns).forEach(([key, value]) => {
        if (value) {
            html += `
                <div class="pattern-item">
                    <div class="pattern-name">${key}</div>
                    <div class="pattern-value">${escapeHtml(value)}</div>
                </div>
            `;
        }
    });

    html += `</div>`;
    return html;
}

// Helper function to escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Clear test results when URL changes
function clearTestResults() {
    document.getElementById('testResults').innerHTML = '';
    document.getElementById('testResultsCard').style.display = 'none';
    window.lastTestSample = null;

    // Update regex test button states when sample data is cleared
    if (typeof updateRegexTestButtonStates === 'function') {
        updateRegexTestButtonStates();
    }
}

// Syntax highlighting for JSON
function syntaxHighlight(json) {
    json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
        let cls = 'json-number';
        if (/^"/.test(match)) {
            if (/:$/.test(match)) {
                cls = 'json-key';
            } else {
                cls = 'json-string';
            }
        } else if (/true|false/.test(match)) {
            cls = 'json-boolean';
        } else if (/null/.test(match)) {
            cls = 'json-null';
        }
        return '<span class="' + cls + '">' + match + '</span>';
    });
}

// Toggle select all checkboxes
function toggleSelectAll(event) {
    const checked = event.target.checked;
    document.querySelectorAll('.feed-checkbox').forEach(checkbox => {
        checkbox.checked = checked;
    });
}

// Handle bulk actions
async function handleBulkAction() {
    const action = document.getElementById('bulkActionSelect').value;
    const selectedFeeds = Array.from(document.querySelectorAll('.feed-checkbox:checked')).map(cb => cb.value);

    if (selectedFeeds.length === 0) {
        showNotification('Please select at least one feed', 'warning');
        return;
    }

    try {
        let response;

        if (action === 'activate' || action === 'deactivate') {
            response = await fetch('/rss/feeds/activate-deactivate-feeds', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': API_PASSWORD
                },
                body: JSON.stringify({
                    feed_ids: selectedFeeds,
                    activate: action === 'activate'
                })
            });
        } else if (action === 'delete') {
            // Confirm deletion
            if (!confirm(`Are you sure you want to delete ${selectedFeeds.length} RSS feeds?`)) {
                return;
            }

            // Delete each feed
            const promises = selectedFeeds.map(feedId =>
                fetch(`/rss/feeds/${feedId}`, {
                    method: 'DELETE',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': API_PASSWORD
                    }
                })
            );

            await Promise.all(promises);

            showNotification(`Successfully deleted ${selectedFeeds.length} RSS feeds`, 'success');
            loadRssFeeds();
            return;
        }

        if (!response.ok) {
            if (response.status === 401) {
                showNotification('Your session has expired. Please log in again.', 'error');
                handleLogout();
                return;
            }
            throw new Error(`Failed to perform bulk action: ${response.statusText}`);
        }

        const result = await response.json();
        showNotification(result.detail, 'success');
        loadRssFeeds();
    } catch (error) {
        console.error('Error performing bulk action:', error);
        showNotification(`Error: ${error.message}`, 'error');
    }
}

// Run RSS scraper
async function runRssScraper() {
    try {
        const response = await fetch('/rss/feeds/run', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-API-Key': API_PASSWORD
            }
        });

        if (!response.ok) {
            if (response.status === 401) {
                showNotification('Your session has expired. Please log in again.', 'error');
                handleLogout();
                return;
            }
            throw new Error(`Failed to run RSS scraper: ${response.statusText}`);
        }

        const result = await response.json();
        showNotification(result.detail, 'success');
    } catch (error) {
        console.error('Error running RSS scraper:', error);
        showNotification(`Error: ${error.message}`, 'error');
    }
}

// Initialize regex test functionality
function initRegexTesting() {
    // Setup regex test buttons
    document.querySelectorAll('.regex-test-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const sourceField = e.target.closest('button').dataset.source;
            const targetField = e.target.closest('button').dataset.target;
            const fieldType = e.target.closest('button').dataset.field;

            // Get the source content by extracting using the main pattern field
            let sourceValue = 'No sample data available';

            if (window.lastTestSample) {
                // For regex testing, we need to extract the content using the corresponding main pattern
                // For example, if testing size_regex, we should use the patternSize field to extract content
                const mainPatternFieldId = `pattern${fieldType.charAt(0).toUpperCase() + fieldType.slice(1)}`;
                const mainPatternValue = document.getElementById(mainPatternFieldId)?.value;

                if (mainPatternValue) {
                    // Use the backend extract_value logic to get the source content
                    sourceValue = extractValueFromSample(window.lastTestSample, mainPatternValue);
                } else {
                    // Fallback to description if no main pattern is set
                    sourceValue = window.lastTestSample.description ||
                                window.lastTestSample.summary ||
                                window.lastTestSample.content ||
                                window.lastTestSample['content:encoded'] ||
                                'No main pattern set. Please set the main pattern field first.';
                }
            }

            // Create and show the regex test modal
            showRegexTestModal(sourceValue, targetField, fieldType);
        });
    });

    // Update button states when pattern fields or sample data changes
    updateRegexTestButtonStates();

    // Add listeners to pattern input fields to update button states
    document.querySelectorAll('#patternSize, #patternSeeders, #patternMagnet, #patternTorrent, #patternCategory').forEach(input => {
        input.addEventListener('input', updateRegexTestButtonStates);
        input.addEventListener('change', updateRegexTestButtonStates);
    });
}

// Function to update regex test button states
function updateRegexTestButtonStates() {
    document.querySelectorAll('.regex-test-btn').forEach(btn => {
        const fieldType = btn.dataset.field;
        const mainPatternFieldId = `pattern${fieldType.charAt(0).toUpperCase() + fieldType.slice(1)}`;
        const mainPatternValue = document.getElementById(mainPatternFieldId)?.value;

        // Enable button only if we have sample data and a main pattern value
        const shouldEnable = window.lastTestSample && mainPatternValue && mainPatternValue.trim() !== '';
        btn.disabled = !shouldEnable;
        btn.title = shouldEnable ? 'Test regex pattern' : 'Set the main pattern field first and test the feed';
    });
}

// JavaScript implementation of extract_value to mirror backend logic
function extractValueFromSample(item, path) {
    console.log('extractValueFromSample called with:', { path, itemKeys: Object.keys(item || {}) });

    if (!path || !item) {
        return 'No data available';
    }

    try {
        // Handle complex array search pattern like: torznab:attr[@name="seeders"]@value
        if (path.includes('[@') && path.includes(']')) {
            console.log('Using complex array search for path:', path);
            return extractWithArraySearch(item, path);
        }

        // Handle basic dot notation and array indexing
        const parts = path.split('.');
        let current = item;
        console.log('Path parts:', parts);

        for (let i = 0; i < parts.length; i++) {
            const part = parts[i];
            console.log(`Processing part ${i}: "${part}". Current value:`, current);

            if (!current) {
                return `Path not found at part "${part}"`;
            }

            if (part.includes('$')) {
                // Handle array with wildcard
                const arrayPart = part.replace('$', '').replace('.', '');
                console.log('Array part:', arrayPart);
                if (arrayPart) {
                    current = current[arrayPart];
                }
                if (!current || !Array.isArray(current) || current.length === 0) {
                    return `Array not found or empty for part "${part}"`;
                }
                // Get the first item
                current = current[0];
                console.log('First array item:', current);
            } else {
                const oldCurrent = current;
                current = current[part];
                console.log(`Accessing "${part}" on:`, oldCurrent, 'Result:', current);
            }

            if (current === null || current === undefined) {
                return `Field "${part}" not found`;
            }
        }

        const result = typeof current === 'object' ? JSON.stringify(current, null, 2) : String(current);
        console.log('Final extracted value:', result);
        return result;
    } catch (error) {
        console.error('Error in extractValueFromSample:', error);
        return `Error extracting value: ${error.message}`;
    }
}

// JavaScript implementation of complex array search
function extractWithArraySearch(item, path) {
    console.log('extractWithArraySearch called with:', { path, itemKeys: Object.keys(item || {}) });

    try {
        // Parse the pattern: torznab:attr[@name="seeders"]@value
        // First, find the bracket section
        const bracketStart = path.indexOf('[@');
        const bracketEnd = path.indexOf(']', bracketStart);

        if (bracketStart === -1 || bracketEnd === -1) {
            return 'Invalid bracket syntax';
        }

        const basePath = path.substring(0, bracketStart);
        const searchCondition = path.substring(bracketStart + 2, bracketEnd);
        const remainingPath = path.substring(bracketEnd + 1);

        // The remaining path should start with @ for attribute access
        let targetField = remainingPath;
        // Keep the @ prefix if present, as it's part of the actual key name in XML attributes
        // Don't remove it since the actual data has keys like "@value", "@name", etc.

        console.log('Parsed components:', { basePath, searchCondition, targetField, remainingPath });

        // Parse search condition: @name="seeders" or name="seeders"
        if (!searchCondition.includes('=')) {
            return 'Invalid search condition';
        }

        const equalIndex = searchCondition.indexOf('=');
        let searchKey = searchCondition.substring(0, equalIndex);
        const searchValue = searchCondition.substring(equalIndex + 1);
        const cleanSearchValue = searchValue.replace(/["']/g, ''); // Remove quotes

        // Add @ prefix to search key if not already present (to match XML attribute format)
        if (!searchKey.startsWith('@')) {
            searchKey = '@' + searchKey;
        }

        console.log('Search params:', { searchKey, searchValue, cleanSearchValue });

        // Navigate to the array
        let current = item;
        if (basePath) {
            console.log('Navigating base path:', basePath);
            for (const part of basePath.split('.')) {
                if (!part) continue; // Skip empty parts
                console.log(`Accessing part "${part}" on:`, current);
                if (!current || !current[part]) {
                    return `Base path part "${part}" not found`;
                }
                current = current[part];
            }
        }

        console.log('Array to search in:', current, 'Is array:', Array.isArray(current));

        // Search in the array
        if (!Array.isArray(current)) {
            return `Expected array but found ${typeof current}`;
        }

        console.log('Searching array with', current.length, 'items');
        for (let i = 0; i < current.length; i++) {
            const arrayItem = current[i];
            console.log(`Array item ${i}:`, arrayItem, `Has key "${searchKey}":`, arrayItem[searchKey]);

            if (typeof arrayItem === 'object' && arrayItem[searchKey] === cleanSearchValue) {
                const result = arrayItem[targetField];
                console.log(`Found match! Target field "${targetField}":`, result);
                return result || 'Target field not found';
            }
        }

        return 'No matching item found in array';
    } catch (error) {
        console.error('Error in extractWithArraySearch:', error);
        return `Error parsing complex path: ${error.message}`;
    }
}

function showRegexTestModal(sourceValue, targetField, fieldType) {
    // Remove existing modal if it exists to ensure clean state
    const existingModal = document.getElementById('regexTestModal');
    if (existingModal) {
        existingModal.remove();
    }

    // Create fresh modal
    const modal = createRegexTestModal();
    document.body.appendChild(modal);
    console.log('Modal created and appended to body');

    // Get the main pattern field info
    const mainPatternFieldId = `pattern${fieldType.charAt(0).toUpperCase() + fieldType.slice(1)}`;
    const mainPatternElement = document.getElementById(mainPatternFieldId);
    const mainPatternValue = mainPatternElement?.value || 'Not set';

    console.log('Field type:', fieldType);
    console.log('Main pattern field ID:', mainPatternFieldId);
    console.log('Main pattern value:', mainPatternValue);
    console.log('Source value length:', sourceValue.length);

    // Populate the modal
    document.getElementById('regexSourceContent').value = sourceValue;
    document.getElementById('regexPattern').value = document.getElementById(targetField)?.value || '';
    document.getElementById('regexMainPattern').value = mainPatternValue;

    // Update pattern info if element exists
    const patternInfoElement = document.getElementById('regexPatternInfo');
    if (patternInfoElement) {
        const infoHtml = `
            <strong>Field:</strong> ${fieldType.charAt(0).toUpperCase() + fieldType.slice(1)}<br>
            <strong>Target Regex Field:</strong> <code>${targetField}</code><br>
            <strong>Extracted Data:</strong> ${sourceValue.length} characters
        `;
        patternInfoElement.innerHTML = infoHtml;
        console.log('Pattern info updated for field:', fieldType);
    } else {
        console.warn('Pattern info element not found');

        // Try to wait a bit and update again
        setTimeout(() => {
            const retryElement = document.getElementById('regexPatternInfo');
            if (retryElement) {
                const infoHtml = `
                    <strong>Field:</strong> ${fieldType.charAt(0).toUpperCase() + fieldType.slice(1)}<br>
                    <strong>Target Regex Field:</strong> <code>${targetField}</code><br>
                    <strong>Extracted Data:</strong> ${sourceValue.length} characters
                `;
                retryElement.innerHTML = infoHtml;
                console.log('Pattern info updated on retry');
            }
        }, 100);
    }

    // Store data for later use
    modal.dataset.targetField = targetField;
    modal.dataset.fieldType = fieldType;

    // Show the modal
    const bootstrapModal = new bootstrap.Modal(modal);
    bootstrapModal.show();
}

function createRegexTestModal() {
    const modalHtml = `
        <div class="modal fade" id="regexTestModal" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">Test Regex Pattern</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="alert alert-info mb-3">
                            <i class="bi bi-info-circle-fill me-2"></i>
                            <div id="regexPatternInfo">Pattern information will appear here</div>
                            <small class="text-muted mt-1 d-block">This content was extracted from the RSS feed using the main pattern above. Test your regex against this extracted data.</small>
                        </div>
                        <div class="mb-3">
                            <label for="regexMainPattern" class="form-label">Main Pattern Used for Extraction</label>
                            <input type="text" class="form-control" id="regexMainPattern" readonly>
                            <small class="text-muted">This is the pattern that was used to extract the content below</small>
                        </div>
                        <div class="mb-3">
                            <label for="regexSourceContent" class="form-label">Extracted Source Content</label>
                            <textarea class="form-control" id="regexSourceContent" rows="6" readonly></textarea>
                            <small class="text-muted">Content extracted from RSS feed item using the main pattern above</small>
                        </div>
                        <div class="mb-3">
                            <label for="regexPattern" class="form-label">Regex Pattern to Test</label>
                            <input type="text" class="form-control" id="regexPattern" placeholder="Enter JavaScript regex pattern (e.g., (\\d+) for numbers)">
                            <div class="alert alert-warning mt-2" style="font-size: 0.875rem;">
                                <i class="bi bi-exclamation-triangle-fill me-1"></i>
                                <strong>Note:</strong> Enter a JavaScript regex pattern, not a backend extraction pattern.
                                <br><strong>Regex Examples:</strong> <code>(\\d+)</code> for numbers, <code>([\\d.]+)\\s*(GB|MB)</code> for size values, <code>Seeders: (\\d+)</code> for seeders.
                                <br><strong>Extraction Pattern Examples (for main fields):</strong> <code>torznab:attr[@name="seeders"]@value</code>, <code>description</code>, <code>title</code>
                            </div>
                        </div>
                        <div class="mb-3">
                            <label for="regexGroup" class="form-label">Capture Group (0 for full match)</label>
                            <input type="number" class="form-control" id="regexGroup" value="1" min="0">
                        </div>
                        <div id="regexTestResult" style="display: none;">
                            <h6>Test Result:</h6>
                            <div id="regexMatchResult"></div>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                        <button type="button" class="btn btn-primary" id="testRegexBtn">Test Pattern</button>
                        <button type="button" class="btn btn-success" id="applyRegexBtn">Apply Pattern</button>
                    </div>
                </div>
            </div>
        </div>
    `;

    const div = document.createElement('div');
    div.innerHTML = modalHtml;
    const modalElement = div.firstElementChild;

    // Verify elements exist
    const regexPatternInfo = modalElement.querySelector('#regexPatternInfo');
    const regexMainPattern = modalElement.querySelector('#regexMainPattern');
    const regexSourceContent = modalElement.querySelector('#regexSourceContent');
    const regexPattern = modalElement.querySelector('#regexPattern');

    console.log('Modal elements found:', {
        regexPatternInfo: !!regexPatternInfo,
        regexMainPattern: !!regexMainPattern,
        regexSourceContent: !!regexSourceContent,
        regexPattern: !!regexPattern
    });

    // Add event listeners
    const testBtn = modalElement.querySelector('#testRegexBtn');
    const applyBtn = modalElement.querySelector('#applyRegexBtn');

    if (testBtn) {
        testBtn.addEventListener('click', testRegexPattern);
        console.log('Test button event listener added');
    } else {
        console.error('Test button not found');
    }

    if (applyBtn) {
        applyBtn.addEventListener('click', applyRegexPattern);
        console.log('Apply button event listener added');
    } else {
        console.error('Apply button not found');
    }

    return modalElement;
}

// Test regex pattern against source content
function testRegexPattern() {
    console.log('testRegexPattern called');
    const sourceContent = document.getElementById('regexSourceContent').value;
    const pattern = document.getElementById('regexPattern').value;
    const group = parseInt(document.getElementById('regexGroup').value) || 0;

    console.log('Source content:', sourceContent);
    console.log('Source content type:', typeof sourceContent);
    console.log('Source content length:', sourceContent.length);
    console.log('Pattern:', pattern);
    console.log('Group:', group);
    console.log('Source content as string:', JSON.stringify(sourceContent));

    if (!pattern) {
        document.getElementById('regexMatchResult').innerHTML = `
            <div class="alert alert-warning">Please enter a regex pattern.</div>
        `;
        document.getElementById('regexTestResult').style.display = 'block';
        return;
    }

    // Check if user entered a backend extraction pattern instead of regex
    if (pattern.includes('@name=') || pattern.includes('[@') || pattern.includes(']@')) {
        document.getElementById('regexMatchResult').innerHTML = `
            <div class="alert alert-danger">
                <strong>Invalid Pattern Type!</strong><br>
                You entered a backend extraction pattern: <code>${pattern}</code><br>
                This field requires a JavaScript regex pattern. Examples:<br>
                • <code>(\\d+)</code> - Extract numbers<br>
                • <code>Seeders: (\\d+)</code> - Extract seeders with label<br>
                • <code>Size: ([\\d.]+)\\s*(GB|MB)</code> - Extract size with unit
            </div>
        `;
        document.getElementById('regexTestResult').style.display = 'block';
        return;
    }

    try {
        const regex = new RegExp(pattern);
        console.log('Created regex:', regex);
        const match = regex.exec(sourceContent);
        console.log('Regex match result:', match);

        if (match) {
            const result = group > 0 && match[group] ? match[group] : match[0];
            document.getElementById('regexMatchResult').innerHTML = `
                <div class="alert alert-success">
                    <strong>✅ Match found!</strong><br>
                    <strong>Extracted Value:</strong> <code class="bg-success bg-opacity-25 px-1">${result}</code><br>
                    <small class="text-muted">Full match: "${match[0]}"</small>
                    ${match.length > 1 ? `<br><small class="text-muted">All groups: [${match.slice(1).map(g => `"${g}"`).join(', ')}]</small>` : ''}
                </div>
            `;
        } else {
            document.getElementById('regexMatchResult').innerHTML = `
                <div class="alert alert-warning">
                    <strong>❌ No match found</strong><br>
                    Pattern: <code>${pattern}</code><br>
                    <small class="text-muted">The regex pattern didn't match anything in the content. Try adjusting your pattern.</small>
                </div>
            `;
        }

        document.getElementById('regexTestResult').style.display = 'block';
    } catch (e) {
        document.getElementById('regexMatchResult').innerHTML = `
            <div class="alert alert-danger">Regex Error: ${e.message}</div>
        `;
        document.getElementById('regexTestResult').style.display = 'block';
    }
}

// Apply regex pattern to target field
function applyRegexPattern() {
    console.log('applyRegexPattern called');
    const modal = document.getElementById('regexTestModal');
    const targetField = modal.dataset.targetField;
    const pattern = document.getElementById('regexPattern').value;

    console.log('Target field:', targetField);
    console.log('Pattern to apply:', pattern);

    if (!pattern) {
        alert('Please enter a regex pattern before applying.');
        return;
    }

    const targetElement = document.getElementById(targetField);
    if (targetElement) {
        targetElement.value = pattern;
        console.log('Pattern applied successfully');

        // Show success message
        showNotification('Regex pattern applied successfully!', 'success');

        // Hide the modal
        const bootstrapModal = bootstrap.Modal.getInstance(modal);
        if (bootstrapModal) {
            bootstrapModal.hide();
        }
    } else {
        console.error('Target field element not found:', targetField);
        alert('Error: Could not find target field to apply pattern.');
    }
}

// These functions are no longer needed since we removed custom patterns

// Initialize catalog selection functionality
function initCatalogSelection() {
    // Toggle catalog detection container
    const autoDetectCatalog = document.getElementById('autoDetectCatalog');
    const catalogPatternContainer = document.getElementById('catalogPatternContainer');

    if (autoDetectCatalog && catalogPatternContainer) {
        autoDetectCatalog.addEventListener('change', (e) => {
            catalogPatternContainer.style.display = e.target.checked ? 'block' : 'none';
        });
    }
}

// Custom patterns function removed since we consolidated the UI

// Note: collectSelectedCatalogs removed since we no longer use static catalog selection

// Initialize catalog patterns functionality
function initCatalogPatterns() {
    const addPatternBtn = document.getElementById('addCatalogPatternBtn');
    if (addPatternBtn) {
        addPatternBtn.addEventListener('click', addCatalogPattern);
    }
}

// Add a new catalog pattern
function addCatalogPattern() {
    const template = document.getElementById('catalogPatternTemplate');
    const clone = template.content.cloneNode(true);

    // Generate unique IDs for this pattern instance
    const timestamp = Date.now();
    const caseSensitiveId = `case-sensitive-${timestamp}`;
    const enabledId = `enabled-${timestamp}`;

    // Add proper IDs and labels to Case Sensitive and Enabled checkboxes
    const caseSensitiveCheckbox = clone.querySelector('.catalog-pattern-case-sensitive');
    const caseSensitiveLabel = caseSensitiveCheckbox.nextElementSibling;
    caseSensitiveCheckbox.id = caseSensitiveId;
    caseSensitiveLabel.setAttribute('for', caseSensitiveId);

    const enabledCheckbox = clone.querySelector('.catalog-pattern-enabled');
    const enabledLabel = enabledCheckbox.nextElementSibling;
    enabledCheckbox.id = enabledId;
    enabledLabel.setAttribute('for', enabledId);

    // Populate catalog checkboxes in the pattern
    const checkboxContainer = clone.querySelector('.catalog-checkboxes-container');
    populateCatalogCheckboxes(checkboxContainer);

    // Add event listeners
    const removeBtn = clone.querySelector('.remove-catalog-pattern-btn');
    removeBtn.addEventListener('click', function() {
        this.closest('.catalog-pattern-item').remove();
        updateCatalogPatternsVisibility();
    });

    const testBtn = clone.querySelector('.catalog-pattern-test-btn');
    testBtn.addEventListener('click', function() {
        testCatalogPattern(this.closest('.catalog-pattern-item'));
    });

    // Add to container
    const container = document.getElementById('catalogPatternsContainer');
    container.appendChild(clone);

    updateCatalogPatternsVisibility();
}

// Populate catalog checkboxes for a pattern
function populateCatalogCheckboxes(container) {
    // Available catalogs (static list since we removed the main form checkboxes)
    const availableCatalogs = [
        // Movies
        { id: 'tamil_hdrip', name: 'Tamil HD Movies' },
        { id: 'tamil_tcrip', name: 'Tamil TC Movies' },
        { id: 'tamil_dubbed', name: 'Tamil Dubbed Movies' },
        { id: 'hindi_hdrip', name: 'Hindi HD Movies' },
        { id: 'hindi_tcrip', name: 'Hindi TC Movies' },
        { id: 'hindi_dubbed', name: 'Hindi Dubbed Movies' },
        { id: 'telugu_hdrip', name: 'Telugu HD Movies' },
        { id: 'telugu_tcrip', name: 'Telugu TC Movies' },
        { id: 'telugu_dubbed', name: 'Telugu Dubbed Movies' },
        { id: 'malayalam_hdrip', name: 'Malayalam HD Movies' },
        { id: 'malayalam_tcrip', name: 'Malayalam TC Movies' },
        { id: 'malayalam_dubbed', name: 'Malayalam Dubbed Movies' },
        { id: 'kannada_hdrip', name: 'Kannada HD Movies' },
        { id: 'kannada_tcrip', name: 'Kannada TC Movies' },
        { id: 'english_hdrip', name: 'English HD Movies' },
        { id: 'english_tcrip', name: 'English TC Movies' },

        // Series
        { id: 'tamil_series', name: 'Tamil Series' },
        { id: 'hindi_series', name: 'Hindi Series' },
        { id: 'telugu_series', name: 'Telugu Series' },
        { id: 'malayalam_series', name: 'Malayalam Series' },
        { id: 'kannada_series', name: 'Kannada Series' },
        { id: 'english_series', name: 'English Series' },

        // Sports
        { id: 'sports.football', name: 'Football' },
        { id: 'sports.cricket', name: 'Cricket' },
        { id: 'sports.f1', name: 'Formula 1' },
        { id: 'sports.nfl', name: 'NFL' },
        { id: 'sports.afl', name: 'AFL' },
        { id: 'sports.wwe', name: 'WWE' }
    ];

    // Group catalogs by type for better organization
    const groups = {
        'Movies': availableCatalogs.filter(cat => cat.id.includes('hdrip') || cat.id.includes('tcrip') || cat.id.includes('dubbed')),
        'Series': availableCatalogs.filter(cat => cat.id.includes('series')),
        'Sports': availableCatalogs.filter(cat => cat.id.startsWith('sports.'))
    };

    // Create checkboxes grouped by type
    Object.entries(groups).forEach(([groupName, catalogs]) => {
        if (catalogs.length > 0) {
            container.insertAdjacentHTML('beforeend', `<h6 class="mt-2 mb-2 text-primary">${groupName}</h6>`);
            catalogs.forEach(catalog => {
                const checkboxHtml = `
                    <div class="form-check">
                        <input class="form-check-input catalog-pattern-target" type="checkbox" value="${catalog.id}" id="pattern_${catalog.id}_${Date.now()}">
                        <label class="form-check-label" for="pattern_${catalog.id}_${Date.now()}">
                            ${catalog.name}
                        </label>
                    </div>
                `;
                container.insertAdjacentHTML('beforeend', checkboxHtml);
            });
        }
    });
}

// Test a catalog pattern against sample data
function testCatalogPattern(patternElement) {
    const regex = patternElement.querySelector('.catalog-pattern-regex').value;
    const caseSensitive = patternElement.querySelector('.catalog-pattern-case-sensitive').checked;

    if (!regex) {
        showNotification('Please enter a regex pattern to test', 'warning');
        return;
    }

    // Use test results if available
    const testResults = document.getElementById('testResults');
    if (!testResults || testResults.style.display === 'none') {
        showNotification('Please test the RSS feed first to get sample data', 'warning');
        return;
    }

        // Find sample item from test results
    const sampleItem = window.lastTestSample;
    if (!sampleItem) {
        showNotification('No sample data available. Please test the RSS feed first.', 'warning');
        return;
    }
    const testFields = ['title', 'description', 'category'];

    try {
        const flags = caseSensitive ? 'g' : 'gi';
        const regexPattern = new RegExp(regex, flags);
        const matches = [];

        testFields.forEach(field => {
            const value = sampleItem[field];
            if (value && regexPattern.test(value)) {
                matches.push(`${field}: "${value}"`);
            }
        });

        if (matches.length > 0) {
            showNotification(`Pattern matched! Fields: ${matches.join(', ')}`, 'success');
        } else {
            showNotification('Pattern did not match any fields in the sample data', 'warning');
        }
    } catch (error) {
        showNotification(`Invalid regex pattern: ${error.message}`, 'error');
    }
}

// Collect all catalog patterns
function collectCatalogPatterns() {
    const patterns = [];
    const patternElements = document.querySelectorAll('.catalog-pattern-item');

    patternElements.forEach(element => {
        const name = element.querySelector('.catalog-pattern-name').value;
        const regex = element.querySelector('.catalog-pattern-regex').value;
        const caseSensitive = element.querySelector('.catalog-pattern-case-sensitive').checked;
        const enabled = element.querySelector('.catalog-pattern-enabled').checked;

        const targetCatalogs = [];
        element.querySelectorAll('.catalog-pattern-target:checked').forEach(checkbox => {
            targetCatalogs.push(checkbox.value);
        });

        if (name && regex && targetCatalogs.length > 0) {
            patterns.push({
                name: name,
                regex: regex,
                case_sensitive: caseSensitive,
                enabled: enabled,
                target_catalogs: targetCatalogs
            });
        }
    });

    return patterns;
}

// Load catalog patterns into the form
function loadCatalogPatterns(patterns) {
    // Clear existing patterns
    const container = document.getElementById('catalogPatternsContainer');
    container.innerHTML = '';

    patterns.forEach(pattern => {
        addCatalogPattern();

        // Get the last added pattern element
        const patternElements = document.querySelectorAll('.catalog-pattern-item');
        const lastPattern = patternElements[patternElements.length - 1];

        // Populate the pattern data
        lastPattern.querySelector('.catalog-pattern-name').value = pattern.name || '';
        lastPattern.querySelector('.catalog-pattern-regex').value = pattern.regex || '';
        lastPattern.querySelector('.catalog-pattern-case-sensitive').checked = pattern.case_sensitive || false;
        lastPattern.querySelector('.catalog-pattern-enabled').checked = pattern.enabled !== false;

        // Select target catalogs
        pattern.target_catalogs.forEach(catalogId => {
            const checkbox = lastPattern.querySelector(`.catalog-pattern-target[value="${catalogId}"]`);
            if (checkbox) {
                checkbox.checked = true;
            }
        });
    });

    updateCatalogPatternsVisibility();
}

// Update visibility of catalog patterns UI elements
function updateCatalogPatternsVisibility() {
    const container = document.getElementById('catalogPatternsContainer');
    const noPatternMessage = document.getElementById('noCatalogPatternsMessage');
    const hasPatterns = container.children.length > 0;

    if (noPatternMessage) {
        noPatternMessage.style.display = hasPatterns ? 'none' : 'block';
    }
}

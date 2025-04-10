const MDBList = {
    BASE_URL: 'https://api.mdblist.com',

    /**
     * Makes an API request with error handling
     */
    async makeRequest(endpoint, apiKey, options = {}) {
        const url = endpoint.startsWith('http') ? endpoint : `${MDBList.BASE_URL}${endpoint}`;
        const finalUrl = new URL(url);
        finalUrl.searchParams.append('apikey', apiKey);

        try {
            const response = await fetch(finalUrl, options);

            // Handle different response statuses
            switch (response.status) {
                case 200:
                    return await response.json();
                case 403:
                    throw new Error('Invalid API key');
                case 404:
                    throw new Error('Resource not found');
                case 429:
                    throw new Error('Rate limit exceeded');
                default:
                    if (!response.ok) {
                        throw new Error(`API request failed with status ${response.status}`);
                    }
            }
        } catch (error) {
            // Convert fetch network errors to user-friendly messages
            if (error.name === 'TypeError' && error.message === 'Failed to fetch') {
                throw new Error('Network error - please check your connection');
            }
            throw error;
        }
    },

    async verifyApiKey(apiKey) {
        if (!apiKey?.trim()) {
            return false;
        }

        try {
            await MDBList.makeRequest('/user', apiKey);
            return true;
        } catch (error) {
            console.error('API key verification failed:', error);
            return false;
        }
    },

    async getUserLists(apiKey) {
        try {
            return await MDBList.makeRequest('/lists/user', apiKey);
        } catch (error) {
            console.error('Error fetching user lists:', error);
            throw MDBList.handleApiError(error);
        }
    },

    async getTopLists(apiKey) {
        try {
            return await MDBList.makeRequest('/lists/top', apiKey);
        } catch (error) {
            console.error('Error fetching top lists:', error);
            throw MDBList.handleApiError(error);
        }
    },

    async searchLists(apiKey, query) {
        if (!query?.trim()) {
            return [];
        }

        try {
            const url = '/lists/search?query=' + encodeURIComponent(query.trim());
            return await MDBList.makeRequest(url, apiKey);
        } catch (error) {
            console.error('Error searching lists:', error);
            throw MDBList.handleApiError(error);
        }
    },

    async getListDetails(apiKey, listId) {
        if (!listId) {
            throw new Error('List ID is required');
        }

        try {
            return await MDBList.makeRequest(`/lists/${listId}`, apiKey);
        } catch (error) {
            console.error('Error fetching list details:', error);
            throw MDBList.handleApiError(error);
        }
    },

    handleApiError(error) {
        const errorMessages = {
            'Invalid API key': 'Your API key is invalid or has expired. Please verify your API key.',
            'Rate limit exceeded': 'You have made too many requests. Please try again later.',
            'Network error': 'Unable to connect to MDBList. Please check your internet connection.',
            'Resource not found': 'The requested list could not be found.'
        };

        const message = errorMessages[error.message] || 'An error occurred while fetching data from MDBList';
        return new Error(message);
    }
};

// MDBList UI handler
class MDBListUI {
    constructor() {
        this.selectedLists = new Map();
        this.editModal = new bootstrap.Modal(document.getElementById('editListModal'));
        this.apiKey = document.getElementById('mdblist_api_key').value;
        this.setupEventListeners();
        this.initializeFromConfig();
        setElementDisplay('mdblist_management', this.apiKey ? 'block' : 'none');
    }

    // Helper functions for consistent ID management
    generateListId(baseId, mediaType) {
        return `${baseId}_${mediaType}`;
    }

    parseListId(listId) {
        const [baseId, mediaType] = String(listId).split('_');
        return {baseId: parseInt(baseId), mediaType};
    }

    isListAdded(baseId) {
        // Check if the list is added in either movie or show format
        const movieId = this.generateListId(baseId, 'movie');
        const showId = this.generateListId(baseId, 'show');

        return this.selectedLists.has(movieId) || this.selectedLists.has(showId);
    }

    initializeFromConfig() {
        const configElement = document.getElementById('mdblist_configured_lists');
        if (!configElement?.value) return;

        try {
            const config = JSON.parse(configElement.value);
            config.forEach(list => {
                const mediaType = list.catalog_type === 'series' ? 'show' : 'movie';
                const listId = this.generateListId(list.id, mediaType);

                this.selectedLists.set(listId, {
                    id: listId,
                    baseId: list.id,
                    title: list.title,
                    catalogType: list.catalog_type,
                    useFilters: list.use_filters,
                    sort: list.sort,
                    order: list.order,
                    // Preserve other properties if available
                    items: list.items || 0,
                    owner: list.owner || '',
                    slug: list.slug || ''
                });
            });

            this.renderSelectedLists();
            this.updateCatalogs();
        } catch (error) {
            console.error('Error initializing from config:', error);
            showNotification('Error loading configured lists', 'error');
        }
    }

    setupEventListeners() {
        // API key verification
        document.getElementById('verifyMDBListApiKey')?.addEventListener('click',
            () => this.verifyApiKey());
        document.getElementById('mdblist_api_key')?.addEventListener('change',
            (e) => this.handleApiKeyChange(e));

        // Tab navigation
        document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(tab => {
            tab.addEventListener('shown.bs.tab', (e) => this.handleTabChange(e.target.id));
        });

        // Search functionality
        document.getElementById('search-lists-btn')?.addEventListener('click',
            () => this.handleSearch());
        document.getElementById('list-search-input')?.addEventListener('keypress',
            (e) => e.key === 'Enter' && this.handleSearch());

        // Manual list addition
        document.getElementById('add-list-btn')?.addEventListener('click',
            () => this.handleManualAdd());

        // Edit modal
        document.getElementById('save-list-edit')?.addEventListener('click',
            () => this.saveListEdit());
    }

    async verifyApiKey() {
        const apiKeyInput = document.getElementById('mdblist_api_key');
        const apiKey = apiKeyInput.value.trim();

        if (!apiKey) {
            showNotification('Please enter an API key', 'error');
            return;
        }

        showLoadingWidget('Verifying API key...');
        try {
            const isValid = await MDBList.verifyApiKey(apiKey);
            if (isValid) {
                this.apiKey = apiKey;
                setElementDisplay('mdblist_management', 'block');
                showNotification('API key verified successfully', 'success');
                await this.loadUserLists();
            } else {
                showNotification('Invalid API key', 'error');
            }
        } catch (error) {
            showNotification(error.message, 'error');
        } finally {
            hideLoadingWidget();
        }
    }

    handleApiKeyChange(e) {
        // Update the API key
        this.apiKey = e.target?.value?.trim() || '';

        // Toggle management section visibility based on API key presence
        setElementDisplay('mdblist_management', this.apiKey ? 'block' : 'none');

        // Clear lists when API key is removed
        if (!this.apiKey) {
            this.selectedLists.clear();
            this.renderSelectedLists();
            this.updateCatalogs();
        }
    }

    async handleTabChange(tabId) {
        if (!this.apiKey) return;

        const tabContent = {
            'my-lists-tab': () => this.loadUserLists(),
            'top-lists-tab': () => this.loadTopLists()
        };

        await (tabContent[tabId] || (() => {
        }))();
    }

    async loadLists(fetchFunction, containerId, loadingId) {
        const container = document.getElementById(containerId);
        document.getElementById(loadingId);
        if (!container) return;

        setElementDisplay(loadingId, 'block');
        container.innerHTML = '';

        try {
            const lists = await fetchFunction(this.apiKey);
            setElementDisplay(loadingId, 'none');

            if (lists?.length > 0) {
                lists.forEach(list => this.renderListItem(container, list));
            } else {
                container.innerHTML = '<div class="alert alert-info">No lists found</div>';
            }
        } catch (error) {
            console.error('Error loading lists:', error);
            container.innerHTML = '<div class="alert alert-danger">Error loading lists</div>';
            setElementDisplay(loadingId, 'none');
        }
    }

    async loadUserLists() {
        await this.loadLists(
            MDBList.getUserLists,
            'my-lists-content',
            'my-lists-loading'
        );
    }

    async loadTopLists() {
        await this.loadLists(
            MDBList.getTopLists,
            'top-lists-content',
            'top-lists-loading'
        );
    }

    async handleSearch() {
        const query = document.getElementById('list-search-input')?.value.trim();
        if (!query) return;

        const container = document.getElementById('search-lists-content');
        if (!container) return;

        container.innerHTML = '<div class="text-center"><div class="spinner-border"></div></div>';

        try {
            const results = await MDBList.searchLists(this.apiKey, query);
            container.innerHTML = '';

            if (results?.length > 0) {
                results.forEach(list => this.renderListItem(container, list));
            } else {
                container.innerHTML = '<div class="alert alert-info">No lists found</div>';
            }
        } catch (error) {
            console.error('Error searching lists:', error);
            container.innerHTML = '<div class="alert alert-danger">Error searching lists</div>';
        }
    }

    renderListItem(container, list) {
        const template = document.getElementById('list-item-template');
        if (!template) return;

        const listItem = template.content.cloneNode(true);
        const listElement = listItem.querySelector('.list-group-item');

        // Set basic information
        listItem.querySelector('.list-title').textContent = list.name;
        listElement.dataset.listId = list.id;

        // Set counts and badges
        this.setItemBadges(listItem, list);

        // Set link and owner info
        this.setListLinks(listItem, list);

        // Setup add button
        const addBtn = listItem.querySelector('.add-list-btn');
        if (addBtn) {
            addBtn.addEventListener('click', () => this.handleAddList(list));
            if (this.isListAdded(list.id)) {
                this.markListAsAdded(addBtn);
            }
        }

        container.appendChild(listItem);
    }

    setItemBadges(listItem, list) {
        // Item count badge
        listItem.querySelector('.item-count').innerHTML = `
            <i class="bi bi-collection"></i> 
            <span class="ms-1">${list.items || 0}</span>
        `;

        // Likes badge
        listItem.querySelector('.like-count').innerHTML = `
            <i class="bi bi-heart${list.likes ? '-fill' : ''}"></i>
            <span class="ms-1">${list.likes || 0}</span>
        `;

        // Media type badge
        const mediaTypeEl = listItem.querySelector('.media-type');
        const mediaIcon = list.mediatype === 'show' ? 'tv' :
            list.mediatype === 'movie' ? 'film' : 'collection-play';
        const mediaText = list.mediatype === 'show' ? 'Series' :
            list.mediatype === 'movie' ? 'Movies' : 'Mixed';

        mediaTypeEl.innerHTML = `
            <i class="bi bi-${mediaIcon}"></i>
            <span class="ms-1">${mediaText}</span>
        `;
    }

    setListLinks(listItem, list) {
        const linkView = listItem.querySelector('.list-link');
        const ownerEl = listItem.querySelector('.list-owner');

        if (list.user_name && list.slug) {
            ownerEl.textContent = `by ${list.user_name}`;
            linkView.href = `https://mdblist.com/lists/${list.user_name}/${list.slug}`;
        } else {
            ownerEl.textContent = 'My List';
            linkView.href = 'https://mdblist.com/mylists/';
        }

        linkView.title = 'View on MDBList';
    }

    markListAsAdded(button) {
        button.classList.replace('btn-outline-primary', 'btn-success');
        button.innerHTML = '<i class="bi bi-check"></i> Added';
        button.disabled = true;
    }

    async handleManualAdd() {
        const input = document.getElementById('list-url-input');
        if (!input?.value) return;

        const urlOrId = input.value.trim();
        showLoadingWidget('Fetching list details...');

        try {
            // Extract list ID from URL if needed
            const listId = urlOrId.includes('mdblist.com/lists/') ?
                urlOrId.split('mdblist.com/lists/')[1].replace(/\/$/, '') :
                urlOrId;

            const listDetails = await MDBList.getListDetails(this.apiKey, listId);
            if (listDetails) {
                this.handleAddList(listDetails);
            } else {
                throw new Error('Failed to fetch list details');
            }
        } catch (error) {
            console.error('Error adding list:', error);
            showNotification('Failed to fetch list details', 'error');
        } finally {
            hideLoadingWidget();
        }
    }

    editList(list) {
        const modal = document.getElementById('editListModal');
        if (!modal) return;

        // Populate form fields
        const fields = {
            'edit-list-title': list.title || list.name,
            'edit-list-id': list.id,
            'edit-list-original-id': list.baseId || list.id,
            'edit-list-sort': list.sort || 'rank',
            'edit-list-order': list.order || 'desc'
        };

        Object.entries(fields).forEach(([id, value]) => {
            const element = modal.querySelector(`#${id}`);
            if (element) element.value = value;
        });

        // Handle checkboxes
        const movieCheck = modal.querySelector('#edit-list-type-movie');
        const showCheck = modal.querySelector('#edit-list-type-show');
        const useFiltersCheck = modal.querySelector('#edit-list-use-filters');

        if (movieCheck && showCheck) {
            const isMovie = list.catalogType === 'movie';
            const isShow = list.catalogType === 'series';

            movieCheck.checked = isMovie || list.allowBothTypes;
            showCheck.checked = isShow || list.allowBothTypes;

            // Handle disabled states
            const shouldDisable = !list.allowBothTypes;
            movieCheck.disabled = shouldDisable && !isMovie;
            showCheck.disabled = shouldDisable && !isShow;
        }

        if (useFiltersCheck) {
            useFiltersCheck.checked = list.useFilters || false;
        }

        this.editModal.show();
    }


    handleAddList(list) {
        const lists = Array.isArray(list) ? list : [list];
        let listId;
        let originalId;

        // Group lists by mediatype
        const movieList = lists.find(l => l.mediatype === 'movie');
        const showList = lists.find(l => l.mediatype === 'show');

        if (!movieList && !showList) {
            // If no specific mediatype, show edit modal to choose
            listId = lists[0].id;
            originalId = lists[0].id;
            this.editList({
                id: lists[0].id,
                name: lists[0].name,
                originalTitle: lists[0].name,
                items: lists[0].items,
                owner: lists[0].user_name,
                slug: lists[0].slug,
                likes: lists[0].likes,
                allowBothTypes: true
            });
        } else {
            // Add specific media type lists
            if (movieList) {
                listId = this.generateListId(movieList.id, 'movie');
                originalId = movieList.id;
                this.addListToSelected(listId, {
                    id: listId,
                    baseId: movieList.id,
                    title: movieList.name,
                    catalogType: 'movie',
                    items: movieList.items,
                    owner: movieList.user_name,
                    slug: movieList.slug,
                    likes: movieList.likes,
                    useFilters: false // default value
                });
            }
            if (showList) {
                listId = this.generateListId(showList.id, 'show');
                originalId = showList.id;
                this.addListToSelected(listId, {
                    id: listId,
                    baseId: showList.id,
                    title: showList.name,
                    catalogType: 'series',
                    items: showList.items,
                    owner: showList.user_name,
                    slug: showList.slug,
                    likes: showList.likes,
                    useFilters: false // default value
                });
            }
        }

        // Update UI immediately
        const addBtn = document.querySelector(`[data-list-id="${originalId}"] .add-list-btn`);
        if (addBtn) {
            this.markListAsAdded(addBtn);
        }
        this.updateCatalogs();
    }

    addListToSelected(listId, listData) {
        // Validate required data
        if (!listId || !listData.title || !listData.catalogType) {
            console.error('Invalid list data:', listData);
            return;
        }

        // Store with consistent format
        this.selectedLists.set(listId, {
            ...listData,
            id: listId,
            baseId: this.parseListId(listId).baseId,
            useFilters: listData.useFilters || false,
            items: listData.items || 0
        });

        // Update UI
        this.renderSelectedLists();
        this.updateCatalogs();
    }

    saveListEdit() {
        const modal = document.getElementById('editListModal');
        if (!modal) return;

        // Get form values
        const baseId = parseInt(modal.querySelector('#edit-list-original-id').value);
        const title = modal.querySelector('#edit-list-title').value;
        const movieSelected = modal.querySelector('#edit-list-type-movie').checked;
        const showSelected = modal.querySelector('#edit-list-type-show').checked;
        const useFilters = modal.querySelector('#edit-list-use-filters').checked;
        const sort = modal.querySelector('#edit-list-sort').value;
        const order = modal.querySelector('#edit-list-order').value;

        // Remove existing entries for this base ID
        this.removeList(baseId, false);

        // Add new entries based on selection
        if (movieSelected) {
            const movieId = this.generateListId(baseId, 'movie');
            this.addListToSelected(movieId, {
                baseId,
                title,
                catalogType: 'movie',
                useFilters,
                sort,
                order
            });
        }

        if (showSelected) {
            const showId = this.generateListId(baseId, 'show');
            this.addListToSelected(showId, {
                baseId,
                title,
                catalogType: 'series',
                useFilters,
                sort,
                order
            });
        }

        this.updateCatalogs();
        this.editModal.hide();
    }

    removeList(listId, isUpdateCatalogs = true) {
        this.selectedLists.delete(listId);
        this.renderSelectedLists();
        if (isUpdateCatalogs) this.updateCatalogs();

        // Update the add button state in the list view
        const addBtn = document.querySelector(`[data-list-id="${this.parseListId(listId).baseId}"] .add-list-btn`);
        if (addBtn) {
            addBtn.classList.replace('btn-success', 'btn-outline-primary');
            addBtn.innerHTML = '<i class="bi bi-plus-circle"></i> Add';
            addBtn.disabled = false;
        }
    }

    getCurrentCatalogPositions() {
        const positions = new Map();
        const catalogs = document.querySelectorAll('.draggable-catalog');

        catalogs.forEach((catalog, index) => {
            const checkbox = catalog.querySelector('input[type="checkbox"]');
            positions.set(catalog.dataset.id, {
                index,
                checked: checkbox?.checked ?? true,
                isGeneral: !catalog.dataset.id.startsWith('mdblist_')
            });
        });

        return positions;
    }

    updateCatalogs() {
        const catalogsContainer = document.getElementById('catalogs');
        if (!catalogsContainer) return;

        // Get current positions and states
        const currentState = this.getCurrentCatalogPositions();
        const currentCatalogs = Array.from(catalogsContainer.children);

        // Store all existing catalogs with their data
        const existingCatalogs = new Map(
            currentCatalogs.map(catalog => [
                catalog.dataset.id,
                {
                    element: catalog,
                    isMDBList: catalog.dataset.id.startsWith('mdblist_')
                }
            ])
        );

        // Clear container
        catalogsContainer.innerHTML = '';

        // Create new MDBList catalogs
        this.selectedLists.forEach(list => {
            const catalogId = `mdblist_${list.catalogType}_${list.baseId}`;
            if (!existingCatalogs.has(catalogId)) {
                existingCatalogs.set(catalogId, {
                    element: this.createCatalogElement(catalogId, list.title),
                    isMDBList: true
                });
            }
        });

        // Sort catalogs based on the selected state and original position
        const sortedCatalogIds = Array.from(currentState.keys())
            .sort((a, b) => {
                const posA = currentState.get(a);
                const posB = currentState.get(b);
                return (posA.checked === posB.checked) ? posA.index - posB.index : posB.checked - posA.checked;
            });

        // Add catalogs in the correct order
        sortedCatalogIds.forEach(catalogId => {
            const catalogInfo = existingCatalogs.get(catalogId);
            if (!catalogInfo) return; // Skip if catalog no longer exists

            // Skip MDBList catalogs that aren't in selectedLists
            if (catalogInfo.isMDBList) {
                const [, type, baseId] = catalogId.split('_');
                const listId = this.generateListId(baseId, type === 'series' ? 'show' : 'movie');
                if (!this.selectedLists.has(listId)) return;
            }

            // Clone the element to avoid reference issues
            const newElement = catalogInfo.element.cloneNode(true);

            // Restore checkbox state
            const state = currentState.get(catalogId);
            if (state) {
                const checkbox = newElement.querySelector('input[type="checkbox"]');
                if (checkbox) checkbox.checked = state.checked;
            }

            catalogsContainer.appendChild(newElement);
        });

        // Add any new MDBList catalogs that weren't in the previous state
        this.selectedLists.forEach(list => {
            const catalogId = `mdblist_${list.catalogType}_${list.baseId}`;
            if (!currentState.has(catalogId)) {
                const catalogDiv = this.createCatalogElement(catalogId, list.title);
                catalogsContainer.appendChild(catalogDiv);
            }
        });
    }

    createCatalogElement(catalogId, title) {
        const div = document.createElement('div');
        div.className = 'col-12 col-md-6 col-lg-4 draggable-catalog';
        div.dataset.id = catalogId;

        div.innerHTML = `
            <div class="form-check">
                <input class="form-check-input" type="checkbox" 
                       name="selected_catalogs" 
                       value="${catalogId}" 
                       id="${catalogId}" 
                       checked>
                <label class="form-check-label" for="${catalogId}">
                    <span class="label-text">MDBList: ${title}</span>
                </label>
            </div>
        `;

        return div;
    }

    renderSelectedLists() {
        const container = document.getElementById('selected-lists');
        if (!container) return;

        container.innerHTML = '';

        // Render each list separately
        this.selectedLists.forEach(list => {
            const template = document.getElementById('selected-list-item-template');
            if (!template) return;

            const item = template.content.cloneNode(true);

            // Set list title
            item.querySelector('.list-title').textContent = list.title;

            // Set media type
            const mediaType = item.querySelector('.media-type');
            mediaType.textContent = list.catalogType === 'series' ? 'Series' : 'Movies';

            // Set filters status
            const useFilters = item.querySelector('.use-filters');
            useFilters.textContent = list.useFilters ? 'Filters enabled' : 'No filters';

            // Set sort info
            const sortInfo = item.querySelector('.sort-info');
            if (sortInfo) {
                const sortText = list.sort || 'rank';
                const orderText = list.order || 'desc';
                sortInfo.textContent = `Sort: ${sortText} (${orderText})`;
            }

            // Setup buttons with individual list data
            const editBtn = item.querySelector('.edit-btn');
            const removeBtn = item.querySelector('.remove-btn');

            editBtn?.addEventListener('click', () => this.editList({
                ...list,
                allowBothTypes: false // Since we're managing them separately
            }));

            removeBtn?.addEventListener('click', () => this.removeList(list.id));

            container.appendChild(item);
        });


    }

    getSelectedListsData() {
        // Group by baseId to combine movie/show pairs
        const groupedLists = new Map();

        this.selectedLists.forEach(list => {
            const {baseId} = this.parseListId(list.id);
            if (!groupedLists.has(baseId)) {
                groupedLists.set(baseId, {
                    id: baseId,
                    title: list.title,
                    catalog_types: new Set(),
                    use_filters: list.useFilters,
                    sort: list.sort || 'rank',
                    order: list.order || 'desc'
                });
            }

            const group = groupedLists.get(baseId);
            group.catalog_types.add(list.catalogType);
            group.use_filters = group.use_filters || list.useFilters;
            group.sort = list.sort || group.sort;
            group.order = list.order || group.order;
        });

        // Convert to array format
        return Array.from(groupedLists.values()).flatMap(group => {
            const types = Array.from(group.catalog_types);
            if (types.length === 1) {
                // Single type list
                return [{
                    id: group.id,
                    title: group.title,
                    catalog_type: types[0],
                    use_filters: group.use_filters,
                    sort: group.sort,
                    order: group.order
                }];
            } else {
                // Split into separate entries for each type
                return types.map(type => ({
                    id: group.id,
                    title: group.title,
                    catalog_type: type,
                    use_filters: group.use_filters,
                    sort: group.sort,
                    order: group.order
                }));
            }
        });
    }
}
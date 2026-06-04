class Gallery extends BaseGallery {
    constructor() {
        super({
            gridSelector: '#gallery-grid',
            defaultSort: 'uploaded_at'
        });

        if (this.elements.grid) {
            this.init();
        }
    }

    init() {
        this.initCommon();

        // Get page from URL
        this.currentPage = parseInt(this.getUrlParam('page', 1));

        this.loadContent();
    }

    async loadContent() {
        if (this.isLoading) return;

        this.isLoading = true;
        this.showLoading();

        // Clear gallery for new page
        this.elements.grid.innerHTML = '';
        this.tagCounts.clear();

        try {
            // Construct params explicitly to ensure clean API call
            const apiParams = new URLSearchParams();

            // 1. Basic pagination and filters
            apiParams.set('page', this.currentPage);
            apiParams.set('sort', this.getSortValue());
            apiParams.set('order', this.getOrderValue());

            // 2. Content class filter
            const contentClassMap = {
                'anime_unknown': 'anime,unknown',
                'anime': 'anime',
                'non_anime': 'non_anime',
                'unknown': 'unknown'
            };
            if (this.currentContentClass && this.currentContentClass !== 'all') {
                const mapped = contentClassMap[this.currentContentClass];
                if (mapped) {
                    apiParams.set('content_class', mapped);
                }
            }

            // 3. Handle Search vs Browse
            const urlParams = new URLSearchParams(window.location.search);
            const searchQuery = urlParams.get('q');
            const sourceAssertions = urlParams.getAll('source_assertion').filter(Boolean);
            const sourceTags = urlParams.getAll('source_tag').filter(Boolean);
            const includeSourceNeedsReview = urlParams.get('include_source_needs_review') === '1';
            const hasSourceFilters = sourceAssertions.length > 0 || sourceTags.length > 0;

            let endpoint = '/api/media/';

            // Combine URL search query with custom filter
            let combinedQuery = '';
            if (searchQuery) combinedQuery = searchQuery;
            if (this.currentCustomFilter) {
                combinedQuery = combinedQuery ? `${combinedQuery} ${this.currentCustomFilter}` : this.currentCustomFilter;
            }

            if (combinedQuery || hasSourceFilters) {
                endpoint = '/api/search';
                if (combinedQuery) {
                    apiParams.set('q', combinedQuery);
                }
                sourceAssertions.forEach(value => apiParams.append('source_assertion', value));
                sourceTags.forEach(value => apiParams.append('source_tag', value));
                if (includeSourceNeedsReview) {
                    apiParams.set('include_source_needs_review', '1');
                }
            }

            console.log('Loading gallery:', endpoint, apiParams.toString());

            const response = await fetch(`${endpoint}?${apiParams.toString()}`, {
                credentials: 'include'
            });

            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
                throw new Error(error.detail || `HTTP ${response.status}`);
            }

            const data = await response.json();
            this.totalPages = data.pages || 1;
            this.renderSourceSearchSummary(data, combinedQuery, sourceAssertions, sourceTags, includeSourceNeedsReview);

            if (data.items && data.items.length > 0) {
                this.processTagCounts(data.items);
                this.renderItems(data.items);
                await this.renderPopularTags();
                this.renderPagination();
            } else if (this.currentPage === 1) {
                this.showEmptyState((searchQuery || hasSourceFilters) ? window.i18n.t('gallery.no_results_found') : window.i18n.t('gallery.no_media_found'));
            }

        } catch (error) {
            console.error('Error loading gallery:', error);
            this.showError(error.message);
        } finally {
            this.isLoading = false;
            this.hideLoading();
        }
    }

    renderItems(items) {
        items.forEach(item => {
            const element = this.createGalleryItem(item);
            this.elements.grid.appendChild(element);
        });
    }

    escapeHtml(str) {
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    renderSourceSearchSummary(data, combinedQuery, sourceAssertions, sourceTags, includeSourceNeedsReview = false) {
        const panel = document.getElementById('source-search-summary');
        if (!panel) return;

        const normalTags = (combinedQuery || '').split(/\s+/).map(v => v.trim()).filter(Boolean);
        const sourceFilters = data?.source_filters || { source_assertions: [], source_tags: [] };
        const hasFilters = normalTags.length || sourceAssertions.length || sourceTags.length;

        if (!hasFilters) {
            panel.classList.add('hidden');
            panel.innerHTML = '';
            return;
        }

        const chips = [];
        normalTags.forEach(tag => {
            chips.push({
                label: tag,
                param: 'q',
                value: tag,
                className: 'normal-search-chip',
                marker: window.i18n.t('common.tags')
            });
        });
        (sourceFilters.source_assertions || []).forEach(chip => {
            chips.push({
                label: chip.display_name || chip.search_value,
                param: 'source_assertion',
                value: chip.search_value,
                className: 'source-search-chip',
                marker: window.i18n.t('media.source_layer.source_assertion_marker')
            });
        });
        (sourceFilters.source_tags || []).forEach(chip => {
            chips.push({
                label: chip.display_name || chip.search_value,
                param: 'source_tag',
                value: chip.search_value,
                className: 'source-search-chip',
                marker: window.i18n.t('media.source_layer.source_tag_marker')
            });
        });

        const removeUrl = (chip) => {
            const params = new URLSearchParams(window.location.search);
            if (chip.param === 'q') {
                const nextTags = normalTags.filter(tag => tag !== chip.value);
                if (nextTags.length) params.set('q', nextTags.join(' '));
                else params.delete('q');
            } else {
                const values = params.getAll(chip.param).filter(value => value !== chip.value);
                params.delete(chip.param);
                values.forEach(value => params.append(chip.param, value));
                if (!params.getAll('source_assertion').length) {
                    params.delete('include_source_needs_review');
                } else if (includeSourceNeedsReview) {
                    params.set('include_source_needs_review', '1');
                }
            }
            params.delete('page');
            const query = params.toString();
            return query ? `/?${query}` : '/';
        };

        const title = window.i18n.t('media.source_layer.active_search_title');
        const note = window.i18n.t('media.source_layer.active_search_note');
        panel.innerHTML = `
            <div class="flex items-start justify-between gap-3 mb-2">
                <div>
                    <div class="source-layer-eyebrow">${this.escapeHtml(title)}</div>
                    <div class="text-[11px] text-secondary">${this.escapeHtml(note)}</div>
                </div>
                <a href="/" class="text-[11px] text-secondary hover:text-primary">${this.escapeHtml(window.i18n.t('media.source_layer.clear_selected'))}</a>
            </div>
            <div class="tag-list">
                ${chips.map(chip => `
                    <a href="${removeUrl(chip)}" class="tag selected-search-chip ${chip.className}" title="${this.escapeHtml(window.i18n.t('media.source_layer.remove_selected'))}">
                        <span>${this.escapeHtml(chip.label)}</span>
                        <span class="source-chip-marker">${this.escapeHtml(chip.marker)}</span>
                        <span class="selected-search-chip-x">x</span>
                    </a>
                `).join('')}
            </div>
        `;
        panel.classList.remove('hidden');
    }
}

// Initialize
if (document.getElementById('gallery-grid')) {
    window.gallery = new Gallery();
}

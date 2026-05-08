class AdminPanel {
    constructor() {
        this.aliasCache = new Set();
        this.tagInputHelper = new TagInputHelper();
        this.validationTimeout = null;
        this.themeSelect = null;
        this.languageSelect = null;
        this.statsModule = null;
        window.adminPanel = this;
        this.init();
    }

    async init() {
        await this.checkAuth();

        this.setupTagAutocomplete();
        this.setupEventListeners();
        this.loadSettings();
        this.setupTagManagement();
        this.setupAlbumManagement();
        this.loadTagStats();
        this.loadMediaStats();
        this.loadAlbumStats();
        this.setupCustomSelects();
        this.loadThemes();
        this.loadLanguages();
        this.setupApiKeyManagement();
        this.setupSystemUpdate();
        this.setupStats();
        this.setupTabs();
    }

    // Helper to escape HTML and prevent XSS
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    setupTagAutocomplete() {
        const tagsSearch = document.getElementById('tag-search-input');
        if (tagsSearch && typeof TagAutocomplete !== 'undefined') {
            new TagAutocomplete(tagsSearch, {
                multipleValues: true,
                appendSpace: false
            });
        }
    }

    setupCustomSelects() {
        const themeSelectElement = document.getElementById('theme-select');
        if (themeSelectElement) {
            this.themeSelect = new CustomSelect(themeSelectElement);
        }

        const languageSelectElement = document.getElementById('language-select');
        if (languageSelectElement) {
            this.languageSelect = new CustomSelect(languageSelectElement);
        }

        const defaultSortElement = document.getElementById('default-sort');
        if (defaultSortElement) {
            this.defaultSortSelect = new CustomSelect(defaultSortElement);
        }

        const defaultOrderElement = document.getElementById('default-order');
        if (defaultOrderElement) {
            this.defaultOrderSelect = new CustomSelect(defaultOrderElement);
        }

        const sidebarFilterModeElement = document.getElementById('sidebar-filter-mode');
        if (sidebarFilterModeElement) {
            this.sidebarFilterModeSelect = new CustomSelect(sidebarFilterModeElement);
            this.customButtons = [];
            this.customButtonInstances = [];

            // Show/hide custom buttons container based on mode
            sidebarFilterModeElement.addEventListener('change', (e) => {
                const container = document.getElementById('custom-buttons-container');
                if (container) {
                    container.style.display = e.detail.value === 'custom' ? 'block' : 'none';
                }
            });
        }

        const addCustomButtonBtn = document.getElementById('add-custom-button-btn');
        if (addCustomButtonBtn) {
            addCustomButtonBtn.addEventListener('click', () => this.addCustomButton());
        }

        // Media type tag inputs
        const mediaTypeTagIds = ['media-type-tags-image', 'media-type-tags-gif', 'media-type-tags-video'];
        mediaTypeTagIds.forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            this.tagInputHelper.setupTagInput(el, id, { onValidate: () => { } });
            if (typeof TagAutocomplete !== 'undefined') {
                new TagAutocomplete(el, { multipleValues: true });
            }
        });
    }

    cleanupCustomButtons() {
        if (this.customButtonInstances) {
            this.customButtonInstances.forEach(instance => {
                if (instance.autocomplete) instance.autocomplete.destroy();
            });
            this.customButtonInstances = [];
        }
    }

    addCustomButton() {
        this.customButtons.push({ title: '', tags: '' });
        this.renderCustomButtons();
    }

    removeCustomButton(index) {
        this.customButtons.splice(index, 1);
        this.renderCustomButtons();
    }

    updateCustomButton(index, field, value) {
        if (this.customButtons[index]) {
            this.customButtons[index][field] = value;
        }
    }

    renderCustomButtons() {
        const container = document.getElementById('custom-buttons-list');
        if (!container) return;

        this.cleanupCustomButtons();
        container.innerHTML = '';

        this.customButtons.forEach((btn, index) => {
            const row = document.createElement('div');
            row.className = 'flex gap-2 items-start mb-3';

            const titleInput = document.createElement('input');
            titleInput.type = 'text';
            titleInput.placeholder = window.i18n.t('admin.settings.button_title');
            titleInput.value = btn.title || '';
            titleInput.className = 'w-1/3 bg px-3 py-2 border text-xs focus:outline-none hover:border-primary transition-colors focus:border-primary';
            titleInput.addEventListener('change', (e) => {
                this.updateCustomButton(index, 'title', e.target.value);
            });

            const tagContainer = document.createElement('div');
            tagContainer.className = 'flex-1 relative';

            const tagInput = document.createElement('div');
            tagInput.contentEditable = true;
            tagInput.className = 'w-full bg px-3 py-2 border text-xs focus:outline-none hover:border-primary transition-colors focus:border-primary';
            tagInput.setAttribute('data-placeholder', window.i18n.t('admin.settings.button_tags'));
            tagInput.style.minHeight = '34px';
            tagInput.style.maxHeight = '100px';
            tagInput.style.overflowY = 'auto';
            tagInput.style.whiteSpace = 'pre-wrap';
            tagInput.style.overflowWrap = 'break-word';
            tagInput.textContent = btn.tags || '';

            if (!btn.tags) tagInput.classList.add('empty');

            const instance = { autocomplete: null };

            tagInput.addEventListener('input', () => {
                const text = tagInput.textContent || '';
                this.updateCustomButton(index, 'tags', text);
                if (!text) tagInput.classList.add('empty');
                else tagInput.classList.remove('empty');
            });

            // Prevent Enter key from adding new lines
            tagInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                }
            });

            tagContainer.appendChild(tagInput);

            if (typeof TagAutocomplete !== 'undefined') {
                const autocomplete = new TagAutocomplete(tagInput, {
                    multipleValues: true,
                    containerClasses: 'max-h-40 overflow-y-auto w-full bg border border-primary shadow-lg z-10'
                });
                instance.autocomplete = autocomplete;
            }

            this.customButtonInstances.push(instance);

            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'px-3 py-2 bg-danger tag-text text-xs hover:bg-danger transition-colors h-[34px]';
            removeBtn.textContent = '×';
            removeBtn.onclick = () => this.removeCustomButton(index);

            row.appendChild(titleInput);
            row.appendChild(tagContainer);
            row.appendChild(removeBtn);
            container.appendChild(row);
        });
    }

    async checkAuth() {
        try {
            const response = await fetch('/api/admin/settings');
            if (response.ok) {
                document.getElementById('settings-section').style.display = 'block';

                app.updateAuthStatus(true);

                return true;
            } else {
                window.location.href = '/login?return=/admin';
                return false;
            }
        } catch (error) {
            console.error('Error checking auth:', error);
            window.location.href = '/login?return=/admin';
            return false;
        }
    }

    setupEventListeners() {
        // Settings form
        const settingsForm = document.getElementById('settings-form');
        if (settingsForm) {
            settingsForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.saveSettings();
            });
        }

        // Scan media button
        const scanBtn = document.getElementById('scan-media-btn');
        if (scanBtn) {
            scanBtn.addEventListener('click', () => this.scanMedia());
        }

        // Thumbnail management buttons
        const generateMissingBtn = document.getElementById('generate-missing-thumbnails-btn');
        if (generateMissingBtn) {
            generateMissingBtn.addEventListener('click', () => this.generateMissingThumbnails());
        }

        const regenerateAllBtn = document.getElementById('regenerate-all-thumbnails-btn');
        if (regenerateAllBtn) {
            regenerateAllBtn.addEventListener('click', () => this.regenerateAllThumbnails());
        }

        // Add tags form
        const addTagsForm = document.getElementById('add-tags-form');
        if (addTagsForm) {
            addTagsForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.addNewTags();
            });
        }

        // Change password form
        const changePasswordForm = document.getElementById('change-admin-password-form');
        if (changePasswordForm) {
            changePasswordForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.changePassword();
            });
        }

        // Change username form
        const changeUsernameForm = document.getElementById('change-admin-username-form');
        if (changeUsernameForm) {
            changeUsernameForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.changeUsername();
            });
        }

        // Redis settings toggle
        const redisEnabled = document.getElementById('redis-enabled');
        if (redisEnabled) {
            redisEnabled.addEventListener('change', (e) => {
                const container = document.getElementById('redis-settings-container');
                if (container) container.style.display = e.target.checked ? 'block' : 'none';
            });
        }

        // Redis test button
        const testRedisBtn = document.getElementById('test-redis-btn');
        if (testRedisBtn) {
            testRedisBtn.addEventListener('click', () => this.testRedisConnection());
        }

        // Shared tags settings toggle
        const sharedTagsEnabled = document.getElementById('shared-tags-enabled');
        if (sharedTagsEnabled) {
            sharedTagsEnabled.addEventListener('change', (e) => {
                const container = document.getElementById('shared-tags-settings-container');
                if (container) container.style.display = e.target.checked ? 'block' : 'none';
            });
        }

        // Shared tags test button
        const testSharedTagsBtn = document.getElementById('test-shared-tags-btn');
        if (testSharedTagsBtn) {
            testSharedTagsBtn.addEventListener('click', () => this.testSharedTagsConnection());
        }

        // Shared tags sync button
        const syncSharedTagsBtn = document.getElementById('sync-shared-tags-btn');
        if (syncSharedTagsBtn) {
            syncSharedTagsBtn.addEventListener('click', () => this.syncSharedTags());
        }

        // Local library scan buttons
        const localScanBtn = document.getElementById('local-scan-btn');
        if (localScanBtn) {
            localScanBtn.addEventListener('click', () => this.startScanJob());
        }
        const localScanCancelBtn = document.getElementById('local-scan-cancel-btn');
        if (localScanCancelBtn) {
            localScanCancelBtn.addEventListener('click', () => this.cancelScanJob());
        }
        const localScanPreflightBtn = document.getElementById('local-scan-preflight-btn');
        if (localScanPreflightBtn) {
            localScanPreflightBtn.addEventListener('click', () => this.startPreflightJob());
        }

        this._scanPollTimer = null;
        this._currentJobId = null;
        this._initHydratedOnlyDefault();
        this.loadScanHistory();

        // AI Tagging buttons
        const aiTagRefreshBtn = document.getElementById('ai-tag-refresh-status');
        if (aiTagRefreshBtn) {
            aiTagRefreshBtn.addEventListener('click', () => this.loadAITagStatus());
        }
        const aiTagSingleBtn = document.getElementById('ai-tag-single-btn');
        if (aiTagSingleBtn) {
            aiTagSingleBtn.addEventListener('click', () => this.runAITagSingle());
        }
        const aiTagBatchBtn = document.getElementById('ai-tag-batch-btn');
        if (aiTagBatchBtn) {
            aiTagBatchBtn.addEventListener('click', () => this.runAITagBatch());
        }
        this.loadAITagStatus();

        // AI Tag Review
        const reviewLoadBtn = document.getElementById('review-load-btn');
        if (reviewLoadBtn) {
            reviewLoadBtn.addEventListener('click', () => this.loadReviewSuggestions());
        }
        const reviewBulkConfirmBtn = document.getElementById('review-bulk-confirm-btn');
        if (reviewBulkConfirmBtn) {
            reviewBulkConfirmBtn.addEventListener('click', () => this.bulkReviewAction('confirm'));
        }
        const reviewBulkRejectBtn = document.getElementById('review-bulk-reject-btn');
        if (reviewBulkRejectBtn) {
            reviewBulkRejectBtn.addEventListener('click', () => this.bulkReviewAction('reject'));
        }
        const reviewSelectAll = document.getElementById('review-select-all');
        if (reviewSelectAll) {
            reviewSelectAll.addEventListener('change', (e) => this._toggleReviewSelectAll(e.target.checked));
        }
        const reviewPrevBtn = document.getElementById('review-prev-btn');
        if (reviewPrevBtn) {
            reviewPrevBtn.addEventListener('click', () => this._reviewPageNav(-1));
        }
        const reviewNextBtn = document.getElementById('review-next-btn');
        if (reviewNextBtn) {
            reviewNextBtn.addEventListener('click', () => this._reviewPageNav(1));
        }
        this._reviewOffset = 0;
        this._reviewLimit = 50;
        this._reviewTotal = 0;

        const reviewTbody = document.getElementById('review-tbody');
        if (reviewTbody) {
            reviewTbody.addEventListener('click', (e) => {
                const btn = e.target.closest('.review-action-btn');
                if (btn) {
                    const action = btn.dataset.action;
                    const mediaId = parseInt(btn.dataset.mediaId);
                    const tagId = parseInt(btn.dataset.tagId);
                    this.reviewSingleAction(action, mediaId, tagId);
                }
            });
            reviewTbody.addEventListener('change', (e) => {
                if (e.target.classList.contains('review-item-cb')) {
                    this._updateReviewSelectionCount();
                }
            });
        }

        // AI Tagging Jobs (Phase 2.3)
        this._aiJobPollTimer = null;
        this._currentAiJobId = null;
        const aiJobCreateBtn = document.getElementById('ai-job-create-btn');
        if (aiJobCreateBtn) {
            aiJobCreateBtn.addEventListener('click', () => this.createAITagJob());
        }
        const aiJobCancelBtn = document.getElementById('ai-job-cancel-btn');
        if (aiJobCancelBtn) {
            aiJobCancelBtn.addEventListener('click', () => this.cancelAITagJob());
        }
        const aiJobsRefreshConfig = document.getElementById('ai-jobs-refresh-config');
        if (aiJobsRefreshConfig) {
            aiJobsRefreshConfig.addEventListener('click', () => this.loadAutoTagConfig());
        }
        const aiJobsRefreshHistory = document.getElementById('ai-jobs-refresh-history');
        if (aiJobsRefreshHistory) {
            aiJobsRefreshHistory.addEventListener('click', () => this.loadAIJobHistory());
        }
        this.loadAutoTagConfig();
        this.loadAIJobHistory();

        // Tag Localization
        this._tlReviewOffset = 0;
        this._tlReviewLimit = 50;
        this._tlReviewTotal = 0;
        this.loadTagLocalizationStats();
        this.loadLLMStatus();
        this.loadWorkerStatus();
        this.loadEntityStatus();

        const tlSaveBtn = document.getElementById('tl-save-btn');
        if (tlSaveBtn) {
            tlSaveBtn.addEventListener('click', () => this.saveTagTranslation());
        }
        const tlBatchBtn = document.getElementById('tl-batch-btn');
        if (tlBatchBtn) {
            tlBatchBtn.addEventListener('click', () => this.runBatchTranslation());
        }
        const tlTestLlmBtn = document.getElementById('tl-test-llm-btn');
        if (tlTestLlmBtn) {
            tlTestLlmBtn.addEventListener('click', () => this.testLLMTranslation());
        }
        const tlRefreshMissingBtn = document.getElementById('tl-refresh-missing-btn');
        if (tlRefreshMissingBtn) {
            tlRefreshMissingBtn.addEventListener('click', () => { this.loadTagLocalizationStats(); this.loadLLMStatus(); });
        }
        const tlLoadMissingBtn = document.getElementById('tl-load-missing-btn');
        if (tlLoadMissingBtn) {
            tlLoadMissingBtn.addEventListener('click', () => this.loadMissingTranslations());
        }
        const tlLoadTransBtn = document.getElementById('tl-load-translations-btn');
        if (tlLoadTransBtn) {
            tlLoadTransBtn.addEventListener('click', () => this.loadTranslationReview());
        }
        const tlReviewPrevBtn = document.getElementById('tl-review-prev-btn');
        if (tlReviewPrevBtn) {
            tlReviewPrevBtn.addEventListener('click', () => this._tlReviewPageNav(-1));
        }
        const tlReviewNextBtn = document.getElementById('tl-review-next-btn');
        if (tlReviewNextBtn) {
            tlReviewNextBtn.addEventListener('click', () => this._tlReviewPageNav(1));
        }
        const tlWorkerRunNow = document.getElementById('tl-worker-run-now-btn');
        if (tlWorkerRunNow) {
            tlWorkerRunNow.addEventListener('click', () => this.workerRunNow());
        }
        const tlWorkerPause = document.getElementById('tl-worker-pause-btn');
        if (tlWorkerPause) {
            tlWorkerPause.addEventListener('click', () => this.workerPause());
        }
        const tlWorkerResume = document.getElementById('tl-worker-resume-btn');
        if (tlWorkerResume) {
            tlWorkerResume.addEventListener('click', () => this.workerResume());
        }
        const tlWorkerRefresh = document.getElementById('tl-worker-refresh-btn');
        if (tlWorkerRefresh) {
            tlWorkerRefresh.addEventListener('click', () => this.loadWorkerStatus());
        }
        const tlEntityResolveBtn = document.getElementById('tl-entity-resolve-btn');
        if (tlEntityResolveBtn) {
            tlEntityResolveBtn.addEventListener('click', () => this.resolveEntities());
        }
        const tlEntityRefreshBtn = document.getElementById('tl-entity-refresh-btn');
        if (tlEntityRefreshBtn) {
            tlEntityRefreshBtn.addEventListener('click', () => this.loadEntityStatus());
        }
        const tlEntityLoadPendingBtn = document.getElementById('tl-entity-load-pending-btn');
        if (tlEntityLoadPendingBtn) {
            tlEntityLoadPendingBtn.addEventListener('click', () => this.loadEntityPending());
        }

        const tlMissingTbody = document.getElementById('tl-missing-tbody');
        if (tlMissingTbody) {
            tlMissingTbody.addEventListener('click', (e) => {
                const btn = e.target.closest('.tl-edit-btn');
                if (btn) {
                    document.getElementById('tl-edit-canonical').value = btn.dataset.name || '';
                    document.getElementById('tl-edit-display').value = '';
                    document.getElementById('tl-edit-aliases').value = '';
                    document.getElementById('tl-edit-category').value = btn.dataset.category || '';
                    document.getElementById('tl-edit-canonical').scrollIntoView({ behavior: 'smooth' });
                }
            });
        }
        const tlReviewTbody = document.getElementById('tl-review-tbody');
        if (tlReviewTbody) {
            tlReviewTbody.addEventListener('click', (e) => {
                const btn = e.target.closest('.tl-action-btn');
                if (btn) {
                    const action = btn.dataset.action;
                    const id = parseInt(btn.dataset.id);
                    this.tagTranslationAction(action, id);
                }
                const editBtn = e.target.closest('.tl-review-edit-btn');
                if (editBtn) {
                    document.getElementById('tl-edit-canonical').value = editBtn.dataset.name || '';
                    document.getElementById('tl-edit-display').value = editBtn.dataset.display || '';
                    document.getElementById('tl-edit-aliases').value = editBtn.dataset.aliases || '';
                    document.getElementById('tl-edit-category').value = editBtn.dataset.category || '';
                    document.getElementById('tl-edit-canonical').scrollIntoView({ behavior: 'smooth' });
                }
            });
        }

        // Developer / E2E Tools (Phase 2.3a)
        const devRefreshConfig = document.getElementById('dev-refresh-config');
        if (devRefreshConfig) {
            devRefreshConfig.addEventListener('click', () => this.loadDevConfigDiagnostics());
        }
        const devShowRecommended = document.getElementById('dev-show-recommended');
        if (devShowRecommended) {
            devShowRecommended.addEventListener('click', () => this.loadRecommendedE2EConfig());
        }
        const devResetDryrunBtn = document.getElementById('dev-reset-dryrun-btn');
        if (devResetDryrunBtn) {
            devResetDryrunBtn.addEventListener('click', () => this.resetE2ETestData(true));
        }
        const devResetRealBtn = document.getElementById('dev-reset-real-btn');
        if (devResetRealBtn) {
            devResetRealBtn.addEventListener('click', () => this.resetE2ETestData(false));
        }
        this.loadDevConfigDiagnostics();
    }

    setupTabs() {
        const tabButtons = document.querySelectorAll('.tab-btn');
        const tabContents = document.querySelectorAll('.tab-content');

        if (tabButtons.length === 0) return;

        const switchTab = (tabId) => {
            tabButtons.forEach(btn => {
                if (btn.dataset.tab === tabId) {
                    btn.classList.add('text-primary', 'border-primary');
                    btn.classList.remove('border-transparent');
                } else {
                    btn.classList.remove('text-primary', 'border-primary');
                    btn.classList.add('border-transparent');
                }
            });

            tabContents.forEach(content => {
                if (content.id === `tab-${tabId}`) {
                    content.classList.remove('hidden');
                } else {
                    content.classList.add('hidden');
                }
            });

            if (tabId === 'stats' && this.statsModule && !this.statsModule.isInitialized) {
                this.statsModule.init();
            }

            localStorage.setItem('admin_active_tab', tabId);
        };

        tabButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                switchTab(btn.dataset.tab);
            });
        });

        // Initialize from URL parameters or local storage
        const urlParams = new URLSearchParams(window.location.search);
        const tabParam = urlParams.get('tab');
        const savedTab = localStorage.getItem('admin_active_tab');
        const defaultTab = 'content';
        let initialTab = defaultTab;

        if (tabParam && document.querySelector(`.tab-btn[data-tab="${tabParam}"]`)) {
            initialTab = tabParam;
            urlParams.delete('tab');
            const newSearch = urlParams.toString();
            const newUrl = window.location.pathname + (newSearch ? '?' + newSearch : '') + window.location.hash;
            window.history.replaceState({}, '', newUrl);
        } else if (savedTab && document.querySelector(`.tab-btn[data-tab="${savedTab}"]`)) {
            initialTab = savedTab;
        }

        switchTab(initialTab);

        // If stats tab is active after initialization, ensure it's loaded
        if (initialTab === 'stats' && this.statsModule) {
            setTimeout(() => {
                if (this.statsModule && !this.statsModule.isInitialized) {
                    this.statsModule.init();
                }
            }, 100);
        }

        // Handle scrolling to hash element if present
        if (window.location.hash) {
            setTimeout(() => {
                try {
                    const targetElement = document.getElementById(window.location.hash.substring(1));
                    if (targetElement) {
                        targetElement.scrollIntoView();
                    }
                } catch (e) {
                    console.error("Error scrolling to hash:", e);
                }
            }, 150);
        }
    }

    setupStats() {
        if (typeof AdminStats !== 'undefined') {
            this.statsModule = new AdminStats();
        }
    }

    async testRedisConnection() {
        const btn = document.getElementById('test-redis-btn');
        const resultDiv = document.getElementById('redis-test-result');
        const originalText = btn.textContent;

        const data = {
            host: document.getElementById('redis-host').value,
            port: parseInt(document.getElementById('redis-port').value),
            db: parseInt(document.getElementById('redis-db').value),
            password: document.getElementById('redis-password').value
        };

        btn.disabled = true;
        btn.textContent = window.i18n.t('admin.actions.testing');
        resultDiv.style.display = 'none';

        try {
            const result = await app.apiCall('/api/admin/test-redis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            resultDiv.style.display = 'block';
            if (result.success) {
                resultDiv.className = 'mt-2 text-xs text-success';
                const message = result.message_key ? window.i18n.t(result.message_key) : result.message;
                resultDiv.textContent = message;
            } else {
                resultDiv.className = 'mt-2 text-xs text-danger';
                const message = result.message_key ? window.i18n.t(result.message_key, { error: result.error }) : result.message;
                resultDiv.textContent = message;
            }
        } catch (error) {
            resultDiv.style.display = 'block';
            resultDiv.className = 'mt-2 text-xs text-danger';
            resultDiv.textContent = 'Error: ' + error.message;
        } finally {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }

    async testSharedTagsConnection() {
        const btn = document.getElementById('test-shared-tags-btn');
        const resultDiv = document.getElementById('shared-tags-test-result');
        const originalText = btn.textContent;

        const data = {
            host: document.getElementById('shared-tags-host').value,
            port: parseInt(document.getElementById('shared-tags-port').value || '5432'),
            name: document.getElementById('shared-tags-name').value,
            user: document.getElementById('shared-tags-user').value,
            password: document.getElementById('shared-tags-password').value
        };

        btn.disabled = true;
        btn.textContent = window.i18n.t('admin.actions.testing');
        resultDiv.style.display = 'none';

        try {
            const result = await app.apiCall('/api/admin/test-shared-tag-db', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            resultDiv.style.display = 'block';
            if (result.success) {
                resultDiv.className = 'mt-2 text-xs text-success';
                resultDiv.textContent = result.message || window.i18n.t('common.connection_successful');
            } else {
                resultDiv.className = 'mt-2 text-xs text-danger';
                resultDiv.textContent = result.message || window.i18n.t('common.connection_failed');
            }
        } catch (error) {
            resultDiv.style.display = 'block';
            resultDiv.className = 'mt-2 text-xs text-danger';
            resultDiv.textContent = 'Error: ' + error.message;
        } finally {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }

    async syncSharedTags() {
        const btn = document.getElementById('sync-shared-tags-btn');
        const resultDiv = document.getElementById('shared-tags-test-result');
        const originalText = btn.textContent;

        btn.disabled = true;
        btn.textContent = window.i18n.t('admin.shared_tags.syncing');
        resultDiv.style.display = 'none';

        try {
            const result = await app.apiCall('/api/admin/shared-tags/sync', {
                method: 'POST'
            });

            resultDiv.style.display = 'block';
            if (result.success) {
                resultDiv.className = 'mt-2 text-xs text-success';
                resultDiv.innerHTML = `
                    ${window.i18n.t('admin.shared_tags.sync_complete')}<br>
                    ${window.i18n.t('admin.shared_tags.imported_count', { tags: result.tags_imported, aliases: result.aliases_imported })}<br>
                    ${window.i18n.t('admin.shared_tags.exported_count', { tags: result.tags_exported, aliases: result.aliases_exported })}
                `;
            } else {
                resultDiv.className = 'mt-2 text-xs text-danger';
                resultDiv.textContent = result.errors?.join(', ') || window.i18n.t('admin.shared_tags.sync_failed');
            }

            // Refresh status
            this.loadSharedTagsStatus();
        } catch (error) {
            resultDiv.style.display = 'block';
            resultDiv.className = 'mt-2 text-xs text-danger';
            resultDiv.textContent = window.i18n.t('common.error', { error: error.message });
        } finally {
            btn.disabled = false;
            btn.textContent = originalText;
        }
    }

    async loadSharedTagsStatus() {
        try {
            const result = await app.apiCall('/api/admin/shared-tags/status');
            const statusDiv = document.getElementById('shared-tags-status');
            const connectionStatus = document.getElementById('shared-tags-connection-status');
            const counts = document.getElementById('shared-tags-counts');

            if (statusDiv && result.enabled) {
                statusDiv.style.display = 'block';
                if (result.connected) {
                    connectionStatus.innerHTML = `<span class="text-success">● ${window.i18n.t('common.connected')}</span>`;
                    counts.textContent = `${window.i18n.t('admin.shared_tags.shared_count')}: ${result.shared_tags || 0} tags, ${result.shared_aliases || 0} aliases`;
                } else {
                    connectionStatus.innerHTML = `<span class="text-danger">● ${window.i18n.t('common.disconnected')}</span>`;
                    counts.textContent = result.error || '';
                }
            }
        } catch (error) {
            console.error('Error loading shared tags status:', error);
        }
    }

    async changePassword() {
        const newPassword = document.getElementById('new-admin-password').value;
        const statusDiv = document.getElementById('change-password-status');
        const resultDiv = document.getElementById('change-password-result');

        try {
            const result = await app.apiCall('/api/admin/update-admin-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_password: newPassword })
            });

            statusDiv.style.display = 'block';
            resultDiv.className = 'text-success';
            resultDiv.textContent = window.i18n.t('notifications.admin.password_updated');

            // Clear the password field
            document.getElementById('new-admin-password').value = '';

            // Hide success message after 3 seconds
            setTimeout(() => {
                statusDiv.style.display = 'none';
            }, 3000);

        } catch (error) {
            statusDiv.style.display = 'block';
            resultDiv.className = 'text-danger';
            resultDiv.textContent = error.message;
        }
    }

    async changeUsername() {
        const newUsername = document.getElementById('new-admin-username').value;
        const statusDiv = document.getElementById('change-username-status');
        const resultDiv = document.getElementById('change-username-result');

        try {
            const result = await app.apiCall('/api/admin/update-admin-username', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_username: newUsername })
            });

            statusDiv.style.display = 'block';
            resultDiv.className = 'text-success';
            resultDiv.textContent = window.i18n.t('notifications.admin.username_updated', { username: result.new_username });

            // Update displayed username if you show it anywhere
            app.showNotification(window.i18n.t('notifications.admin.username_updated', { username: result.new_username }), 'success');

            // Clear the username field
            document.getElementById('new-admin-username').value = '';

            // Hide success message after 3 seconds
            setTimeout(() => {
                statusDiv.style.display = 'none';
            }, 3000);

        } catch (error) {
            statusDiv.style.display = 'block';
            resultDiv.className = 'text-danger';
            resultDiv.textContent = error.message;
        }
    }

    // Helper methods for tag validation
    parseTagWithCategory(tagString) {
        const prefixes = ['artist:', 'copyright:', 'character:', 'meta:'];
        const normalized = tagString.trim().toLowerCase();

        for (const prefix of prefixes) {
            if (normalized.startsWith(prefix)) {
                const category = prefix.slice(0, -1); // Remove the colon
                const tagName = normalized.slice(prefix.length).trim();
                return { tagName, category };
            }
        }

        // No prefix, default to general
        return { tagName: normalized, category: 'general' };
    }

    async validateAndStyleNewTags() {
        const tagsInput = document.getElementById('new-tags-input');
        if (!tagsInput) return;

        await this.tagInputHelper.validateAndStyleTags(tagsInput, {
            validationCache: this.tagInputHelper.tagValidationCache,
            checkFunction: (tag) => {
                const { tagName } = this.parseTagWithCategory(tag);
                return this.tagInputHelper.checkTagOrAliasExists(tagName);
            },
            invertLogic: true
        });
    }

    setupNewTagsInput() {
        const tagsInput = document.getElementById('new-tags-input');
        if (!tagsInput) return;

        this.tagInputHelper.setupTagInput(tagsInput, 'new-tags-input', {
            onValidate: () => { },
            checkFunction: (tag) => {
                const { tagName } = this.parseTagWithCategory(tag);
                return this.tagInputHelper.checkTagOrAliasExists(tagName);
            },
            invertLogic: true
        });
    }

    async addNewTags() {
        const tagsInput = document.getElementById('new-tags-input');
        const statusDiv = document.getElementById('add-tags-status');
        const resultDiv = document.getElementById('add-tags-result');

        const text = this.tagInputHelper.getPlainTextFromDiv(tagsInput);
        const tagStrings = text.split(/\s+/).filter(t => t.length > 0);

        if (tagStrings.length === 0) {
            app.showNotification(window.i18n.t('notifications.admin.enter_at_least_one_tag'), 'error');
            return;
        }

        // Parse and filter tags
        const tagsToCreate = [];
        const ignoredTags = [];

        for (const tagString of tagStrings) {
            const { tagName, category } = this.parseTagWithCategory(tagString);
            const shouldIgnore = this.tagInputHelper.tagValidationCache.get(tagName);

            if (shouldIgnore) {
                ignoredTags.push(tagString);
            } else {
                tagsToCreate.push({ name: tagName, category });
            }
        }

        if (tagsToCreate.length === 0) {
            app.showNotification(window.i18n.t('notifications.admin.tags_already_exist'), 'error', window.i18n.t('notifications.admin.nothing_to_add'));
            return;
        }

        // Show loading
        statusDiv.style.display = 'block';
        resultDiv.innerHTML = `
            <div class="bg-primary primary-text p-3 mb-2">
                <strong>${window.i18n.t('admin.messages.adding_tags')}</strong>
            </div>
        `;

        try {
            const response = await app.apiCall('/api/admin/bulk-create-tags', {
                method: 'POST',
                body: JSON.stringify({ tags: tagsToCreate })
            });

            let html = `
                <div class="bg-success p-3 mb-2 tag-text">
                    <strong>${window.i18n.t('notifications.admin.tags_added_successfully')}</strong>
                </div>
                <div class="text-secondary space-y-1">
                    <div>${window.i18n.t('notifications.admin.tags_created')} <strong class="text">${response.created}</strong></div>
                    <div>${window.i18n.t('notifications.admin.tags_skipped')} <strong class="text">${response.skipped}</strong></div>
                    <div>${window.i18n.t('notifications.admin.tags_errors')} <strong class="text">${response.errors.length}</strong></div>
                </div>
            `;

            if (ignoredTags.length > 0) {
                html += `
                    <div class="mt-2 p-2 surface-light border text-xs">
                        <strong>${window.i18n.t('notifications.admin.tags_ignored')}</strong><br>
                        ${ignoredTags.join(', ')}
                    </div>
                `;
            }

            if (response.errors.length > 0) {
                html += `
                    <div class="mt-2 p-2 bg-warning tag-text text-xs">
                        <strong>${window.i18n.t('notifications.admin.tags_errors')}</strong><br>
                        ${response.errors.slice(0, 5).map(app.translateError).join('<br>')}
                    </div>
                `;
            }

            resultDiv.innerHTML = html;

            // Clear input and cache
            tagsInput.textContent = '';
            this.tagInputHelper.clearCache();

            // Reload stats
            await this.loadTagStats();

        } catch (error) {
            resultDiv.innerHTML = `
                <div class="bg-danger p-3 tag-text">
                    <strong>Error:</strong> ${error.message}
                </div>
            `;
        }
    }

    async loadSettings() {
        try {
            const response = await fetch('/api/admin/settings');

            if (!response.ok) {
                console.log('Not authenticated or settings not available');
                return;
            }

            const settings = await response.json();

            // Populate form fields
            if (settings.app_name) {
                const appNameInput = document.getElementById('app-name');
                if (appNameInput) appNameInput.value = settings.app_name;
            }

            if (settings.items_per_page) {
                const itemsPerPageInput = document.getElementById('items-per-page');
                if (itemsPerPageInput) itemsPerPageInput.value = settings.items_per_page;
            }

            if (settings.external_share_url) {
                const externalShareUrlInput = document.getElementById('external-share-url');
                if (externalShareUrlInput) externalShareUrlInput.value = settings.external_share_url;
            }

            if (settings.default_sort && this.defaultSortSelect) {
                this.defaultSortSelect.setValue(settings.default_sort);
            }

            if (settings.default_order && this.defaultOrderSelect) {
                this.defaultOrderSelect.setValue(settings.default_order);
            }

            if (settings.require_auth !== undefined) {
                const requireAuthCheckbox = document.getElementById('require-auth');
                if (requireAuthCheckbox) requireAuthCheckbox.checked = settings.require_auth;
            }

            if (settings.redis) {
                const redisEnabled = document.getElementById('redis-enabled');
                if (redisEnabled) {
                    redisEnabled.checked = settings.redis.enabled;
                    const container = document.getElementById('redis-settings-container');
                    if (container) container.style.display = settings.redis.enabled ? 'block' : 'none';
                }

                const hostInput = document.getElementById('redis-host');
                if (hostInput) hostInput.value = settings.redis.host || 'redis';

                const portInput = document.getElementById('redis-port');
                if (portInput) portInput.value = settings.redis.port || 6379;

                const dbInput = document.getElementById('redis-db');
                if (dbInput) dbInput.value = settings.redis.db || 0;

                const passwordInput = document.getElementById('redis-password');
                if (passwordInput) passwordInput.value = settings.redis.password || '';
            }

            if (settings.sidebar_filter_mode && this.sidebarFilterModeSelect) {
                this.sidebarFilterModeSelect.setValue(settings.sidebar_filter_mode);
                const container = document.getElementById('custom-buttons-container');
                if (container) container.style.display = settings.sidebar_filter_mode === 'custom' ? 'block' : 'none';
            }

            if (settings.sidebar_custom_buttons) {
                this.customButtons = settings.sidebar_custom_buttons;
                this.renderCustomButtons();
            }

            // Load shared tags settings
            if (settings.shared_tags) {
                const sharedTagsEnabled = document.getElementById('shared-tags-enabled');
                if (sharedTagsEnabled) {
                    sharedTagsEnabled.checked = settings.shared_tags.enabled;
                    const container = document.getElementById('shared-tags-settings-container');
                    if (container) container.style.display = settings.shared_tags.enabled ? 'block' : 'none';
                }

                // Show/hide sync button based on whether shared tags are enabled
                const syncBtn = document.getElementById('sync-shared-tags-btn');
                if (syncBtn) syncBtn.style.display = settings.shared_tags.enabled ? 'inline-block' : 'none';

                const hostInput = document.getElementById('shared-tags-host');
                if (hostInput) hostInput.value = settings.shared_tags.host || 'shared-tag-db';

                const portInput = document.getElementById('shared-tags-port');
                if (portInput) portInput.value = settings.shared_tags.port || 5432;

                const nameInput = document.getElementById('shared-tags-name');
                if (nameInput) nameInput.value = settings.shared_tags.name || 'shared_tags';

                const userInput = document.getElementById('shared-tags-user');
                if (userInput) userInput.value = settings.shared_tags.user || 'postgres';

                const passwordInput = document.getElementById('shared-tags-password');
                if (passwordInput) passwordInput.value = settings.shared_tags.password || '';

                // Load status if enabled
                if (settings.shared_tags.enabled) {
                    this.loadSharedTagsStatus();
                }
            }

            // Load media_type_tags settings
            if (settings.media_type_tags) {
                const types = ['image', 'gif', 'video'];
                types.forEach(type => {
                    const el = document.getElementById(`media-type-tags-${type}`);
                    if (el) {
                        const tags = settings.media_type_tags[type];
                        if (Array.isArray(tags) && tags.length > 0) {
                            el.textContent = tags.join(' ');
                            setTimeout(() => this.tagInputHelper.validateAndStyleTags(el), 100);
                        }
                    }
                });
            }
        } catch (error) {
            console.error('Error loading settings:', error);
        }
    }

    async saveSettings() {
        const appName = document.getElementById('app-name').value.trim();
        const themeSelectElement = document.getElementById('theme-select');
        const theme = themeSelectElement?.dataset.value;
        const languageSelectElement = document.getElementById('language-select');
        const language = languageSelectElement?.dataset.value;
        const itemsPerPage = document.getElementById('items-per-page')?.value;
        const externalShareUrl = document.getElementById('external-share-url')?.value;

        if (!appName || !theme || !itemsPerPage) {
            app.showNotification(window.i18n.t('notifications.admin.fill_all_settings'), 'error');
            return;
        }

        if (appName.length < 1 || appName.length > 25) {
            app.showNotification(window.i18n.t('notifications.admin.app_name_length'), 'error');
            return;
        }

        const itemsPerPageNum = parseInt(itemsPerPage);
        if (isNaN(itemsPerPageNum) || itemsPerPageNum < 20 || itemsPerPageNum > 200) {
            app.showNotification(window.i18n.t('notifications.admin.items_per_page_range'), 'error');
            return;
        }

        const defaultSort = this.defaultSortSelect ? this.defaultSortSelect.getValue() : null;
        const defaultOrder = this.defaultOrderSelect ? this.defaultOrderSelect.getValue() : null;
        const requireAuth = document.getElementById('require-auth')?.checked || false;

        const redisSettings = {
            enabled: document.getElementById('redis-enabled')?.checked || false,
            host: document.getElementById('redis-host')?.value || 'redis',
            port: parseInt(document.getElementById('redis-port')?.value || '6379'),
            db: parseInt(document.getElementById('redis-db')?.value || '0'),
            password: document.getElementById('redis-password')?.value || ''
        };

        const sidebarMode = this.sidebarFilterModeSelect ? this.sidebarFilterModeSelect.getValue() : 'rating';

        // Filter out empty buttons (must have both title and tags)
        let validButtons = [];
        if (this.customButtons) {
            validButtons = this.customButtons.filter(btn => {
                const title = (btn.title || '').trim();
                const tags = (btn.tags || '').trim();
                return title.length > 0 && tags.length > 0;
            });
        }

        // Require at least one valid button
        if (sidebarMode === 'custom' && validButtons.length === 0) {
            app.showNotification(window.i18n.t('notifications.admin.error_custom_button_required'), 'error');
            return;
        }

        // Collect media_type_tags
        const getMediaTypeTags = (id) => {
            const el = document.getElementById(id);
            if (!el) return [];
            const text = this.tagInputHelper.getPlainTextFromDiv(el);
            return text.split(/\s+/).filter(t => t.length > 0);
        };

        const settings = {
            app_name: appName,
            theme: theme,
            language: language || 'en',
            items_per_page: itemsPerPageNum,
            default_sort: defaultSort,
            default_order: defaultOrder,
            external_share_url: externalShareUrl || null,
            require_auth: requireAuth,
            redis: redisSettings,
            shared_tags: {
                enabled: document.getElementById('shared-tags-enabled')?.checked || false,
                host: document.getElementById('shared-tags-host')?.value || 'shared-tag-db',
                port: parseInt(document.getElementById('shared-tags-port')?.value || '5432'),
                name: document.getElementById('shared-tags-name')?.value || 'shared_tags',
                user: document.getElementById('shared-tags-user')?.value || 'postgres',
                password: document.getElementById('shared-tags-password')?.value || ''
            },
            sidebar_filter_mode: sidebarMode,
            sidebar_custom_buttons: validButtons,
            media_type_tags: {
                image: getMediaTypeTags('media-type-tags-image'),
                gif: getMediaTypeTags('media-type-tags-gif'),
                video: getMediaTypeTags('media-type-tags-video')
            }
        };

        try {
            await app.apiCall('/api/admin/settings', {
                method: 'PATCH',
                body: JSON.stringify(settings)
            });

            location.reload();
        } catch (error) {
            app.showNotification(error.message, 'error', window.i18n.t('notifications.admin.error_saving_settings'));
        }
    }

    async scanMedia() {
        const scanBtn = document.getElementById('scan-media-btn');
        const originalText = scanBtn.textContent;
        scanBtn.disabled = true;
        scanBtn.textContent = window.i18n.t('admin.actions.scanning');

        try {
            const result = await app.apiCall('/api/admin/scan-media', {
                method: 'POST'
            });

            if (result.new_files === 0) {
                app.showNotification(window.i18n.t('notifications.admin.no_untracked_media'), 'info');
                scanBtn.disabled = false;
                scanBtn.textContent = originalText;
                return;
            }

            // Show loading message
            scanBtn.textContent = window.i18n.t('admin.messages.scan_loading', { count: result.new_files });

            // Get the uploader instance
            const uploader = window.uploaderInstance;
            if (!uploader) {
                app.showNotification(window.i18n.t('notifications.admin.refresh_and_retry'), 'error', window.i18n.t('notifications.admin.uploader_not_initialized'));
                scanBtn.disabled = false;
                scanBtn.textContent = originalText;
                return;
            }

            // Fetch and add each file to the uploader
            let loadedCount = 0;
            let skippedCount = 0;
            let duplicateCount = 0;

            for (const filePath of result.files) {
                try {
                    // Check if file is already in the upload queue
                    if (uploader.isFileQueued(filePath)) {
                        duplicateCount++;
                        continue;
                    }

                    scanBtn.textContent = window.i18n.t('admin.messages.scan_progress', { current: loadedCount + 1, total: result.new_files });

                    // Fetch the file from the server
                    const response = await fetch(`/api/admin/get-untracked-file?path=${encodeURIComponent(filePath)}`);

                    if (!response.ok) {
                        console.error(`Failed to fetch file: ${filePath}`);
                        skippedCount++;
                        continue;
                    }

                    const blob = await response.blob();
                    const filename = filePath.split('/').pop().split('\\').pop(); // Handle both Unix and Windows paths
                    const file = new File([blob], filename, { type: blob.type });

                    // Add to uploader
                    await uploader.addScannedFile(file, filePath);
                    loadedCount++;

                } catch (error) {
                    console.error(`Error loading file ${filePath}:`, error);
                    skippedCount++;
                }
            }

            // Show results
            let message = '';
            if (loadedCount > 0) {
                message = `Loaded ${loadedCount} file(s) into the editor.`;
            }
            if (duplicateCount > 0) {
                message += `${message ? '\n' : ''}${duplicateCount} file(s) already in queue.`;
            }
            if (skippedCount > 0) {
                message += `${message ? '\n' : ''}${skippedCount} file(s) skipped due to errors.`;
            }
            if (loadedCount > 0) {
                message += '\n\nYou can now edit tags and ratings before submitting.';
            }

            const notificationType = loadedCount > 0 ? 'success' : (duplicateCount > 0 ? 'info' : 'warning');
            app.showNotification(message, notificationType);

        } catch (error) {
            console.error('Scan error:', error);
            app.showNotification(error.message, 'error', window.i18n.t('notifications.admin.error_scanning_media'));
        } finally {
            scanBtn.disabled = false;
            scanBtn.textContent = originalText;
        }
    }

    // ---- Local Library Scan (job-based) ----

    async _initHydratedOnlyDefault() {
        const checkbox = document.getElementById('local-scan-hydrated-only');
        if (!checkbox) return;
        try {
            const data = await app.apiCall('/api/admin/dev/config-diagnostics', { method: 'GET' });
            const serverDefault = data?.scan?.hydrated_only_default;
            if (typeof serverDefault === 'boolean') {
                checkbox.checked = serverDefault;
            }
        } catch (_) {
            // Fallback: keep checkbox as-is (HTML default)
        }
    }

    _readScanFormParams() {
        const pathInput = document.getElementById('local-scan-path').value.trim();
        const maxFilesInput = document.getElementById('local-scan-max-files').value;
        const dryRun = document.getElementById('local-scan-dry-run').checked;
        const hydratedOnly = document.getElementById('local-scan-hydrated-only')?.checked ?? true;
        const body = {};
        if (pathInput) body.paths = [pathInput];
        if (dryRun) body.dry_run = true;
        if (maxFilesInput && parseInt(maxFilesInput) > 0) body.max_files = parseInt(maxFilesInput);
        body.hydrated_only = hydratedOnly;
        return body;
    }

    async startScanJob() {
        const body = this._readScanFormParams();

        const btn = document.getElementById('local-scan-btn');
        btn.disabled = true;

        try {
            const job = await app.apiCall('/api/admin/scan-local-library/jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            this._currentJobId = job.id;
            this._showJobProgress(job);
            this._startPolling(job.id);
        } catch (error) {
            console.error('Failed to start scan job:', error);
            app.showNotification(error.message || 'Failed to start scan', 'error');
            btn.disabled = false;
        }
    }

    async cancelScanJob() {
        if (!this._currentJobId) return;
        const cancelBtn = document.getElementById('local-scan-cancel-btn');
        cancelBtn.disabled = true;
        try {
            await app.apiCall(`/api/admin/scan-local-library/jobs/${this._currentJobId}/cancel`, {
                method: 'POST'
            });
            app.showNotification('Cancel requested — scan will stop shortly', 'info');
        } catch (error) {
            console.error('Failed to cancel scan job:', error);
            app.showNotification(error.message || 'Failed to cancel', 'error');
        } finally {
            cancelBtn.disabled = false;
        }
    }

    async startPreflightJob() {
        const body = this._readScanFormParams();
        const btn = document.getElementById('local-scan-preflight-btn');
        if (btn) btn.disabled = true;

        try {
            const result = await app.apiCall('/api/admin/scan-local-library/preflight', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (result && result.id) {
                this._currentJobId = result.id;
                this._showJobProgress(result, {
                    estimated_size_bytes: result.estimated_size_bytes,
                    largest_file_bytes: result.largest_file_bytes,
                    extensions: result.extensions,
                });
            }
            app.showNotification('Preflight analysis complete', 'success');
            this.loadScanHistory();
        } catch (error) {
            console.error('Failed to run preflight:', error);
            app.showNotification(error.message || 'Preflight failed', 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    _startPolling(jobId) {
        this._stopPolling();
        this._scanPollTimer = setInterval(() => this._pollJob(jobId), 1500);
    }

    _stopPolling() {
        if (this._scanPollTimer) {
            clearInterval(this._scanPollTimer);
            this._scanPollTimer = null;
        }
    }

    async _pollJob(jobId) {
        try {
            const job = await app.apiCall(`/api/admin/scan-local-library/jobs/${jobId}`, { method: 'GET' });
            this._showJobProgress(job);
            if (['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
                this._stopPolling();
                this._onJobFinished(job);
            }
        } catch (err) {
            console.error('Poll error:', err);
        }
    }

    _showJobProgress(job, preflightExtra) {
        const progressDiv = document.getElementById('local-scan-progress');
        progressDiv.style.display = 'block';

        const btn = document.getElementById('local-scan-btn');
        const cancelBtn = document.getElementById('local-scan-cancel-btn');
        const preflightBtn = document.getElementById('local-scan-preflight-btn');
        const isActive = ['pending', 'running', 'cancelling'].includes(job.status);
        btn.disabled = isActive;
        if (preflightBtn) preflightBtn.disabled = isActive;
        cancelBtn.style.display = isActive ? 'inline-block' : 'none';

        const badge = document.getElementById('local-scan-status-badge');
        const colors = {
            pending: 'text-secondary border-secondary',
            running: 'text-primary border-primary',
            cancelling: 'text-warning border-warning',
            completed: 'text-green-500 border-green-500',
            failed: 'text-red-500 border-red-500',
            cancelled: 'text-warning border-warning',
            interrupted: 'text-red-400 border-red-400',
        };
        badge.className = `text-xs font-bold px-2 py-1 border ${colors[job.status] || ''}`;
        badge.textContent = job.status.toUpperCase();

        const dryBadge = document.getElementById('local-scan-dry-badge');
        dryBadge.style.display = job.dry_run ? 'inline-block' : 'none';

        const preflightBadge = document.getElementById('local-scan-preflight-badge');
        if (preflightBadge) {
            preflightBadge.style.display = job.is_preflight ? 'inline-block' : 'none';
        }

        const bar = document.getElementById('local-scan-progress-bar');
        if (job.max_files && job.max_files > 0) {
            const pct = Math.min(100, Math.round((job.processed / job.max_files) * 100));
            bar.style.width = pct + '%';
        } else if (['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
            bar.style.width = '100%';
        } else {
            bar.style.width = '';
            bar.classList.add('animate-pulse');
        }
        if (!['pending', 'running', 'cancelling'].includes(job.status)) {
            bar.classList.remove('animate-pulse');
        }

        document.getElementById('scan-total-seen').textContent = job.total_seen;
        document.getElementById('scan-processed').textContent = job.processed;
        document.getElementById('scan-imported').textContent = job.imported;
        document.getElementById('scan-skipped-dup').textContent = job.skipped_duplicate;
        document.getElementById('scan-skipped-unsup').textContent = job.skipped_unsupported;
        document.getElementById('scan-failed').textContent = job.failed;
        document.getElementById('scan-limit-reached').textContent = job.limit_reached ? 'Yes' : '—';

        const errorEl = document.getElementById('scan-error');
        errorEl.textContent = job.error_message || '—';
        errorEl.title = job.error_message || '';

        const failuresDiv = document.getElementById('local-scan-failures');
        const failuresTbody = document.getElementById('local-scan-failures-tbody');
        if (job.failed_files && job.failed_files.length > 0) {
            failuresDiv.style.display = 'block';
            failuresTbody.innerHTML = job.failed_files.map(f =>
                `<tr class="border-b text-[10px]">
                    <td class="py-1 px-2 font-mono break-all">${this.escapeHtml(f.path)}</td>
                    <td class="py-1 px-2">${this.escapeHtml(f.reason)}</td>
                </tr>`
            ).join('');
        } else {
            failuresDiv.style.display = 'none';
            failuresTbody.innerHTML = '';
        }

        const extStats = document.getElementById('scan-extended-stats');
        if (extStats) {
            const hasAny = (job.skipped_cloud_placeholder || 0) + (job.skipped_zero_byte || 0)
                + (job.skipped_timeout || 0) + (job.skipped_unreadable || 0)
                + (job.skipped_hidden || 0) + (job.skipped_too_large || 0) > 0;
            extStats.style.display = hasAny || job.is_preflight ? 'grid' : 'none';
            const set = (id, val) => {
                const e = document.getElementById(id);
                if (e) e.textContent = val ?? 0;
            };
            set('scan-cloud-placeholder', job.skipped_cloud_placeholder);
            set('scan-zero-byte', job.skipped_zero_byte);
            set('scan-timeout', job.skipped_timeout);
            set('scan-unreadable', job.skipped_unreadable);
            set('scan-hidden', job.skipped_hidden);
            set('scan-too-large', job.skipped_too_large);
        }

        const pfExtra = document.getElementById('scan-preflight-extra');
        if (pfExtra) {
            const extra = preflightExtra || {};
            const hasPf = job.is_preflight && (extra.estimated_size_bytes != null);
            pfExtra.style.display = hasPf ? 'grid' : 'none';
            if (hasPf) {
                const fmtSize = (bytes) => {
                    if (bytes == null) return '—';
                    if (bytes < 1024) return bytes + ' B';
                    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
                    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
                    return (bytes / 1073741824).toFixed(2) + ' GB';
                };
                const esEl = document.getElementById('scan-estimated-size');
                if (esEl) esEl.textContent = fmtSize(extra.estimated_size_bytes);
                const lfEl = document.getElementById('scan-largest-file');
                if (lfEl) lfEl.textContent = fmtSize(extra.largest_file_bytes);
                const extEl = document.getElementById('scan-extensions');
                if (extEl) {
                    const exts = extra.extensions || {};
                    const sorted = Object.entries(exts).sort((a, b) => b[1] - a[1]).slice(0, 10);
                    extEl.textContent = sorted.map(([k, v]) => `${k}: ${v}`).join(', ') || '—';
                }
            }
        }
    }

    _onJobFinished(job) {
        if (job.is_preflight) {
            app.showNotification(
                `Preflight ${job.status}: ${job.total_seen} files found`,
                job.status === 'completed' ? 'success' : 'warning'
            );
        } else {
            const label = job.dry_run ? 'would import' : 'imported';
            const statusLabel = job.status === 'completed' ? 'complete' : job.status;
            app.showNotification(
                `Scan ${statusLabel}: ${job.imported} ${label}, ${job.skipped_duplicate} dup skipped, ${job.failed} failed`,
                job.status === 'completed' ? (job.failed > 0 ? 'warning' : 'success') : 'warning'
            );
        }
        if (!job.dry_run && !job.is_preflight && job.imported > 0) this.loadMediaStats();
        this.loadScanHistory();
    }

    async loadScanHistory() {
        try {
            const jobs = await app.apiCall('/api/admin/scan-local-library/jobs', { method: 'GET' });
            const tbody = document.getElementById('local-scan-history-tbody');
            if (!jobs || jobs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="p-3 text-center text-xs text-secondary">No scan history</td></tr>';
                return;
            }

            tbody.innerHTML = jobs.map(j => {
                const mode = j.is_preflight ? 'preflight' : (j.dry_run ? 'dry-run' : 'import');
                const time = j.created_at ? new Date(j.created_at).toLocaleString() : '—';
                const statusCls = {
                    completed: 'text-green-500', failed: 'text-red-500',
                    cancelled: 'text-warning', interrupted: 'text-red-400',
                    running: 'text-primary', pending: 'text-secondary',
                    cancelling: 'text-warning',
                }[j.status] || '';
                return `<tr class="border-b text-[10px] cursor-pointer hover:surface" data-job-id="${j.id}">
                    <td class="py-1 px-2">${j.id}</td>
                    <td class="py-1 px-2 font-bold ${statusCls}">${j.status}</td>
                    <td class="py-1 px-2">${mode}${j.max_files ? ' (' + j.max_files + ')' : ''}</td>
                    <td class="py-1 px-2">${j.imported}</td>
                    <td class="py-1 px-2">${j.skipped_duplicate}</td>
                    <td class="py-1 px-2">${j.failed}</td>
                    <td class="py-1 px-2">${time}</td>
                </tr>`;
            }).join('');

            tbody.querySelectorAll('tr[data-job-id]').forEach(row => {
                row.addEventListener('click', () => {
                    const jid = parseInt(row.dataset.jobId);
                    const job = jobs.find(j => j.id === jid);
                    if (job) {
                        this._currentJobId = job.id;
                        this._showJobProgress(job);
                        if (['pending', 'running', 'cancelling'].includes(job.status)) {
                            this._startPolling(job.id);
                        }
                    }
                });
            });

            const activeJob = jobs.find(j => ['pending', 'running', 'cancelling'].includes(j.status));
            if (activeJob) {
                this._currentJobId = activeJob.id;
                this._showJobProgress(activeJob);
                this._startPolling(activeJob.id);
            }
        } catch (err) {
            console.error('Failed to load scan history:', err);
        }
    }

    // ---- AI Auto Tagging ----

    async loadAITagStatus() {
        const el = document.getElementById('ai-tag-status-content');
        if (!el) return;
        el.textContent = 'Checking...';
        try {
            const data = await app.apiCall('/api/admin/ai-tagging/model-status', { method: 'GET' });
            const enabledBadge = data.enabled
                ? '<span class="text-green-500 font-bold">Enabled</span>'
                : '<span class="text-red-500 font-bold">Disabled</span>';
            const availableBadge = data.available
                ? '<span class="text-green-500">Available</span>'
                : '<span class="text-red-500">Unavailable</span>';
            const loadedBadge = data.loaded
                ? '<span class="text-green-500">Loaded</span>'
                : '<span class="text-secondary">Not loaded</span>';
            const downloadedBadge = data.model_downloaded
                ? '<span class="text-green-500">Downloaded</span>'
                : '<span class="text-warning">Not downloaded (will download on first use)</span>';

            let html = `<div class="space-y-1">
                <div>AI Tagging: ${enabledBadge}</div>
                <div>Model: <span class="font-bold">${data.model_name || '—'}</span></div>
                <div>Dependencies: ${availableBadge} | Model: ${downloadedBadge} | Runtime: ${loadedBadge}</div>`;
            if (data.config) {
                html += `<div class="mt-2 text-[10px] text-secondary">
                    Thresholds — General: ${data.config.general_threshold} | Character: ${data.config.character_threshold}
                    | Rating: ${data.config.rating_threshold} | Suggestion: ${data.config.suggestion_threshold}
                    | Batch max: ${data.config.batch_max_items}
                </div>`;
            }
            if (data.error) {
                html += `<div class="text-red-500 mt-1">Error: ${data.error}</div>`;
            }
            html += '</div>';
            el.innerHTML = html;
        } catch (err) {
            el.innerHTML = `<span class="text-red-500">Failed to check: ${err.message || err}</span>`;
        }
    }

    _showAITagResults(data, isBatch) {
        const resultsDiv = document.getElementById('ai-tag-results');
        const summaryDiv = document.getElementById('ai-tag-summary');
        const tbody = document.getElementById('ai-tag-details-tbody');
        resultsDiv.style.display = '';

        const results = isBatch ? (data.results || []) : [data];
        const summary = isBatch ? data : {
            processed: 1,
            tags_added: data.tags_added || 0,
            suggestions_added: data.suggestions_added || 0,
            skipped_locked: data.skipped_locked || 0,
            ignored_low_confidence: data.ignored_low_confidence || 0,
            failed: data.error ? 1 : 0,
            dry_run: data.dry_run || false,
        };

        const dryLabel = summary.dry_run ? ' <span class="text-warning font-bold">(DRY RUN)</span>' : '';
        summaryDiv.innerHTML = `
            <div class="bg p-2 border text-center">
                <div class="text-[10px] text-secondary">Processed</div>
                <div class="text-sm font-bold">${summary.processed || results.length}${dryLabel}</div>
            </div>
            <div class="bg p-2 border text-center">
                <div class="text-[10px] text-secondary">Tags Added</div>
                <div class="text-sm font-bold text-green-500">${summary.tags_added}</div>
            </div>
            <div class="bg p-2 border text-center">
                <div class="text-[10px] text-secondary">Suggestions</div>
                <div class="text-sm font-bold text-blue-500">${summary.suggestions_added}</div>
            </div>
            <div class="bg p-2 border text-center">
                <div class="text-[10px] text-secondary">Skipped (Locked)</div>
                <div class="text-sm font-bold">${summary.skipped_locked}</div>
            </div>
            <div class="bg p-2 border text-center">
                <div class="text-[10px] text-secondary">Ignored (Low)</div>
                <div class="text-sm font-bold">${summary.ignored_low_confidence}</div>
            </div>
            <div class="bg p-2 border text-center">
                <div class="text-[10px] text-secondary">Failed</div>
                <div class="text-sm font-bold text-red-500">${summary.failed || 0}</div>
            </div>
        `;

        tbody.innerHTML = results.map(r => `
            <tr class="border-b text-[10px]">
                <td class="py-1 px-2"><a href="/media/${r.media_id}" class="text-primary hover:underline">${r.media_id}</a></td>
                <td class="py-1 px-2 text-green-500">${r.tags_added || 0}</td>
                <td class="py-1 px-2 text-blue-500">${r.suggestions_added || 0}</td>
                <td class="py-1 px-2">${r.skipped_locked || 0}</td>
                <td class="py-1 px-2">${r.ignored_low_confidence || 0}</td>
                <td class="py-1 px-2 text-red-500">${r.error || '—'}</td>
            </tr>
        `).join('');
    }

    async runAITagSingle() {
        const mediaIdInput = document.getElementById('ai-tag-media-id');
        const mediaId = parseInt(mediaIdInput.value);
        if (!mediaId || mediaId < 1) {
            app.showNotification('Please enter a valid Media ID', 'error');
            return;
        }
        const dryRun = document.getElementById('ai-tag-single-dryrun').checked;
        const btn = document.getElementById('ai-tag-single-btn');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Running...';

        try {
            const data = await app.apiCall(
                `/api/admin/ai-tagging/media/${mediaId}?dry_run=${dryRun}`,
                { method: 'POST' }
            );
            this._showAITagResults(data, false);
            const label = dryRun ? 'Dry-run' : 'Tagging';
            app.showNotification(
                `${label} complete: ${data.tags_added} added, ${data.suggestions_added} suggestions`,
                'success'
            );
        } catch (err) {
            app.showNotification(`AI tagging failed: ${err.message || err}`, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    }

    async runAITagBatch() {
        const idsInput = document.getElementById('ai-tag-batch-ids').value.trim();
        const maxItems = parseInt(document.getElementById('ai-tag-batch-max').value) || 5;
        const dryRun = document.getElementById('ai-tag-batch-dryrun').checked;

        const body = {
            max_items: maxItems,
            dry_run: dryRun,
            only_without_ai_tags: true,
        };
        if (idsInput) {
            body.media_ids = idsInput.split(',').map(s => parseInt(s.trim())).filter(n => n > 0);
            if (body.media_ids.length === 0) {
                app.showNotification('Invalid Media IDs', 'error');
                return;
            }
        }

        const btn = document.getElementById('ai-tag-batch-btn');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Running...';

        try {
            const data = await app.apiCall('/api/admin/ai-tagging/batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            this._showAITagResults(data, true);
            const label = dryRun ? 'Dry-run' : 'Batch tagging';
            app.showNotification(
                `${label} complete: ${data.processed} processed, ${data.tags_added} added, ${data.suggestions_added} suggestions`,
                'success'
            );
        } catch (err) {
            app.showNotification(`Batch AI tagging failed: ${err.message || err}`, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    }

    // ---- AI Tag Review ----

    async loadReviewSuggestions() {
        const tbody = document.getElementById('review-tbody');
        const emptyDiv = document.getElementById('review-empty');
        const statsDiv = document.getElementById('review-stats');
        const paginationDiv = document.getElementById('review-pagination');
        if (!tbody) return;

        const params = new URLSearchParams();
        params.set('limit', this._reviewLimit);
        params.set('offset', this._reviewOffset);

        const minConf = document.getElementById('review-min-confidence')?.value;
        const maxConf = document.getElementById('review-max-confidence')?.value;
        const tagName = document.getElementById('review-tag-name')?.value?.trim();
        const mediaId = document.getElementById('review-media-id')?.value;

        if (minConf) params.set('min_confidence', minConf);
        if (maxConf) params.set('max_confidence', maxConf);
        if (tagName) params.set('tag_name', tagName);
        if (mediaId) params.set('media_id', mediaId);
        params.set('order', 'confidence_desc');

        try {
            const data = await app.apiCall(`/api/admin/ai-tags/review?${params}`, { method: 'GET' });
            this._reviewTotal = data.total || 0;

            if (data.items && data.items.length > 0) {
                emptyDiv.style.display = 'none';
                statsDiv.style.display = '';
                statsDiv.textContent = `Showing ${data.items.length} of ${data.total} suggestions`;
                tbody.innerHTML = data.items.map(item => this._renderReviewRow(item)).join('');
                paginationDiv.style.display = '';
                this._updateReviewPagination();
            } else {
                tbody.innerHTML = '';
                emptyDiv.style.display = '';
                statsDiv.style.display = 'none';
                paginationDiv.style.display = 'none';
            }
            this._updateReviewSelectionCount();
        } catch (err) {
            app.showNotification(`Failed to load suggestions: ${err.message || err}`, 'error');
        }
    }

    _renderReviewRow(item) {
        const thumbHtml = item.thumbnail_url
            ? `<img src="${item.thumbnail_url}" class="w-8 h-8 object-cover border" loading="lazy">`
            : '<div class="w-8 h-8 bg border flex items-center justify-center text-[8px] text-secondary">N/A</div>';
        const conf = item.confidence !== null ? (item.confidence * 100).toFixed(1) + '%' : '—';
        return `<tr class="border-b text-[10px] hover:bg-primary/5" data-media-id="${item.media_id}" data-tag-id="${item.tag_id}">
            <td class="py-1 px-2">
                <input type="checkbox" class="review-item-cb w-3.5 h-3.5 accent-primary" data-media-id="${item.media_id}" data-tag-id="${item.tag_id}">
            </td>
            <td class="py-1 px-2">${thumbHtml}</td>
            <td class="py-1 px-2"><a href="/media/${item.media_id}" class="text-primary hover:underline">${item.media_id}</a></td>
            <td class="py-1 px-2 font-medium">${this.escapeHtml(item.tag_name)}</td>
            <td class="py-1 px-2"><span class="tag-category-${item.tag_category}">${item.tag_category}</span></td>
            <td class="py-1 px-2 font-mono">${conf}</td>
            <td class="py-1 px-2 text-secondary">${item.source || '—'}</td>
            <td class="py-1 px-2">
                <div class="flex gap-1">
                    <button class="review-action-btn text-green-600 hover:text-green-800 font-bold px-1" data-action="confirm" data-media-id="${item.media_id}" data-tag-id="${item.tag_id}" title="Confirm">✓</button>
                    <button class="review-action-btn text-red-600 hover:text-red-800 font-bold px-1" data-action="reject" data-media-id="${item.media_id}" data-tag-id="${item.tag_id}" title="Reject">✗</button>
                    <button class="review-action-btn text-blue-600 hover:text-blue-800 font-bold px-1" data-action="lock" data-media-id="${item.media_id}" data-tag-id="${item.tag_id}" title="Lock">🔒</button>
                </div>
            </td>
        </tr>`;
    }

    _updateReviewPagination() {
        const prevBtn = document.getElementById('review-prev-btn');
        const nextBtn = document.getElementById('review-next-btn');
        const pageInfo = document.getElementById('review-page-info');
        if (!prevBtn || !nextBtn) return;

        const currentPage = Math.floor(this._reviewOffset / this._reviewLimit) + 1;
        const totalPages = Math.max(1, Math.ceil(this._reviewTotal / this._reviewLimit));

        prevBtn.disabled = this._reviewOffset === 0;
        nextBtn.disabled = (this._reviewOffset + this._reviewLimit) >= this._reviewTotal;
        pageInfo.textContent = `Page ${currentPage} of ${totalPages}`;
    }

    _reviewPageNav(direction) {
        if (direction < 0) {
            this._reviewOffset = Math.max(0, this._reviewOffset - this._reviewLimit);
        } else {
            this._reviewOffset += this._reviewLimit;
        }
        this.loadReviewSuggestions();
    }

    _toggleReviewSelectAll(checked) {
        document.querySelectorAll('.review-item-cb').forEach(cb => { cb.checked = checked; });
        this._updateReviewSelectionCount();
    }

    _updateReviewSelectionCount() {
        const checked = document.querySelectorAll('.review-item-cb:checked');
        const countEl = document.getElementById('review-selection-count');
        const bulkConfirm = document.getElementById('review-bulk-confirm-btn');
        const bulkReject = document.getElementById('review-bulk-reject-btn');
        if (countEl) countEl.textContent = `${checked.length} selected`;
        if (bulkConfirm) bulkConfirm.disabled = checked.length === 0;
        if (bulkReject) bulkReject.disabled = checked.length === 0;
    }

    _getSelectedReviewItems() {
        const items = [];
        document.querySelectorAll('.review-item-cb:checked').forEach(cb => {
            items.push({ media_id: parseInt(cb.dataset.mediaId), tag_id: parseInt(cb.dataset.tagId) });
        });
        return items;
    }

    async reviewSingleAction(action, mediaId, tagId) {
        try {
            let url, method;
            if (action === 'delete') {
                url = `/api/admin/ai-tags/${mediaId}/${tagId}`;
                method = 'DELETE';
            } else {
                url = `/api/admin/ai-tags/${mediaId}/${tagId}/${action}`;
                method = 'POST';
            }
            await app.apiCall(url, { method });
            app.showNotification(`Tag ${action}ed successfully`, 'success');
            this.loadReviewSuggestions();
        } catch (err) {
            app.showNotification(`Action failed: ${err.message || err}`, 'error');
        }
    }

    async bulkReviewAction(action) {
        const items = this._getSelectedReviewItems();
        if (items.length === 0) {
            app.showNotification('No items selected', 'error');
            return;
        }
        if (items.length > 100) {
            app.showNotification('Max 100 items per bulk operation', 'error');
            return;
        }

        try {
            const data = await app.apiCall('/api/admin/ai-tags/bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, items }),
            });
            app.showNotification(
                `Bulk ${action}: ${data.success} succeeded, ${data.failed} failed`,
                data.failed > 0 ? 'warning' : 'success'
            );
            this.loadReviewSuggestions();
        } catch (err) {
            app.showNotification(`Bulk action failed: ${err.message || err}`, 'error');
        }
    }

    async generateMissingThumbnails() {
        const btn = document.getElementById('generate-missing-thumbnails-btn');
        const allBtn = document.getElementById('regenerate-all-thumbnails-btn');
        const statusDiv = document.getElementById('thumbnail-regen-status');
        const resultDiv = document.getElementById('thumbnail-regen-result');
        const originalText = btn.textContent;

        btn.disabled = true;
        allBtn.disabled = true;
        btn.textContent = window.i18n.t('admin.media_management.thumbnails.generating');
        statusDiv.style.display = 'block';
        resultDiv.innerHTML = `<div class="bg-primary primary-text p-3"><strong>${window.i18n.t('admin.media_management.thumbnails.generating')}</strong></div>`;

        try {
            const result = await app.apiCall('/api/admin/generate-missing-thumbnails', { method: 'POST' });
            const msg = window.i18n.t('admin.media_management.thumbnails.done_missing', {
                orphans_deleted: result.orphans_deleted,
                generated: result.generated,
                skipped: result.skipped,
                failed: result.failed,
            });
            resultDiv.innerHTML = `<div class="bg-success p-3 tag-text"><strong>${msg}</strong></div>`;
        } catch (error) {
            resultDiv.innerHTML = `<div class="bg-danger p-3 tag-text"><strong>Error:</strong> ${error.message}</div>`;
        } finally {
            btn.disabled = false;
            allBtn.disabled = false;
            btn.textContent = originalText;
        }
    }

    async regenerateAllThumbnails() {
        const modal = new ModalHelper({
            id: 'regenerate-thumbnails-modal',
            type: 'danger',
            title: window.i18n.t('modal.regenerate_thumbnails.title'),
            message: window.i18n.t('modal.regenerate_thumbnails.message'),
            confirmText: window.i18n.t('modal.regenerate_thumbnails.confirm'),
            cancelText: window.i18n.t('common.cancel'),
            confirmId: 'regenerate-thumbnails-confirm-yes',
            cancelId: 'regenerate-thumbnails-confirm-no',
            onConfirm: async () => {
                const btn = document.getElementById('regenerate-all-thumbnails-btn');
                const missingBtn = document.getElementById('generate-missing-thumbnails-btn');
                const statusDiv = document.getElementById('thumbnail-regen-status');
                const resultDiv = document.getElementById('thumbnail-regen-result');
                const originalText = btn.textContent;

                btn.disabled = true;
                missingBtn.disabled = true;
                btn.textContent = window.i18n.t('admin.media_management.thumbnails.generating');
                statusDiv.style.display = 'block';
                resultDiv.innerHTML = `<div class="bg-primary primary-text p-3"><strong>${window.i18n.t('admin.media_management.thumbnails.generating')}</strong></div>`;

                try {
                    const result = await app.apiCall('/api/admin/regenerate-all-thumbnails', { method: 'POST' });
                    const msg = window.i18n.t('admin.media_management.thumbnails.done_all', {
                        deleted: result.deleted,
                        generated: result.generated,
                        failed: result.failed,
                    });
                    resultDiv.innerHTML = `<div class="bg-success p-3 tag-text"><strong>${msg}</strong></div>`;
                } catch (error) {
                    resultDiv.innerHTML = `<div class="bg-danger p-3 tag-text"><strong>Error:</strong> ${error.message}</div>`;
                } finally {
                    btn.disabled = false;
                    missingBtn.disabled = false;
                    btn.textContent = originalText;
                }
            }
        });

        modal.show();
    }

    setupTagManagement() {
        // Setup new tags input validation
        this.setupNewTagsInput();

        // CSV upload
        const uploadArea = document.getElementById('csv-upload-area');
        const fileInput = document.getElementById('csv-file-input');

        uploadArea?.addEventListener('click', () => fileInput?.click());

        uploadArea?.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('drag-over');
        });

        uploadArea?.addEventListener('dragleave', () => {
            uploadArea.classList.remove('drag-over');
        });

        uploadArea?.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('drag-over');

            const files = e.dataTransfer.files;
            if (files.length > 0) {
                this.uploadCSV(files[0]);
            }
        });

        fileInput?.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                this.uploadCSV(e.target.files[0]);
            }
        });

        // Full Backup Import
        const fullImportArea = document.getElementById('full-import-area');
        const fullImportInput = document.getElementById('full-import-input');

        fullImportArea?.addEventListener('click', () => fullImportInput?.click());

        fullImportArea?.addEventListener('dragover', (e) => {
            e.preventDefault();
            fullImportArea.classList.add('drag-over');
        });

        fullImportArea?.addEventListener('dragleave', () => {
            fullImportArea.classList.remove('drag-over');
        });

        fullImportArea?.addEventListener('drop', (e) => {
            e.preventDefault();
            fullImportArea.classList.remove('drag-over');

            if (e.dataTransfer.files.length > 0) {
                this.uploadFullBackup(e.dataTransfer.files[0]);
            }
        });

        fullImportInput?.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                this.uploadFullBackup(e.target.files[0]);
            }
        });

        // Tag search
        const searchBtn = document.getElementById('tag-search-btn');
        const searchInput = document.getElementById('tag-search-input');

        searchInput?.addEventListener('input', (e) => {
            e.target.value = e.target.value.replace(/\s+/g, '_');
        });

        searchInput?.addEventListener('keydown', (e) => {
            if (e.key === ' ') {
                e.preventDefault();
                e.target.value += '_';
            }
        });

        searchBtn?.addEventListener('click', () => this.searchTags());
        searchInput?.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.target.value = e.target.value.replace(/\s+/g, '_').trim();
                this.searchTags();
            }
        });

        // Clear tags
        const clearBtn = document.getElementById('clear-tags-btn');
        clearBtn?.addEventListener('click', () => this.clearAllTags());
    }

    async loadTagStats() {
        try {
            const response = await fetch('/api/admin/tag-stats');
            const stats = await response.json();

            const totalTagsEl = document.getElementById('total-tags');
            const totalAliasesEl = document.getElementById('total-aliases');

            if (totalTagsEl) totalTagsEl.textContent = stats.total_tags;
            if (totalAliasesEl) totalAliasesEl.textContent = stats.total_aliases;
        } catch (error) {
            console.error('Error loading tag stats:', error);
        }
    }

    async loadMediaStats() {
        try {
            const response = await fetch('/api/admin/media-stats');
            const stats = await response.json();

            const totalMediaEl = document.getElementById('total-media');
            const totalImagesEl = document.getElementById('total-images');
            const totalGifsEl = document.getElementById('total-gifs');
            const totalVideosEl = document.getElementById('total-videos');

            if (totalMediaEl) totalMediaEl.textContent = stats.total_media;
            if (totalImagesEl) totalImagesEl.textContent = stats.total_images;
            if (totalGifsEl) totalGifsEl.textContent = stats.total_gifs;
            if (totalVideosEl) totalVideosEl.textContent = stats.total_videos;
        } catch (error) {
            console.error('Error loading media stats:', error);
        }
    }

    async uploadCSV(file) {
        const statusDiv = document.getElementById('csv-import-status');
        const progressDiv = document.getElementById('csv-import-progress');

        statusDiv.style.display = 'block';
        progressDiv.innerHTML = `
            <div class="bg-primary primary-text p-3 mb-2">
                <strong>${window.i18n.t('admin.messages.uploading_processing')}</strong><br>
                <span class="text-xs">${window.i18n.t('admin.messages.upload_warning')}</span>
            </div>
        `;

        try {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch('/api/admin/import-tags-csv', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Upload failed');
            }

            const result = await response.json();

            let html = `
                <div class="bg-success p-3 mb-2 tag-text">
                    <strong>${result.message_key ? window.i18n.t(result.message_key) : result.message}</strong>
                </div>
                <div class="text-secondary space-y-1">
                    <div>Rows processed: <strong class="text">${result.rows_processed}</strong></div>
                    <div>Tags created: <strong class="text">${result.tags_created}</strong></div>
                    <div>Tags updated: <strong class="text">${result.tags_updated}</strong></div>
                    <div>Aliases created: <strong class="text">${result.aliases_created}</strong></div>
                </div>
            `;

            if (result.skipped_long_tags > 0 || result.skipped_long_aliases > 0) {
                html += `
                    <div class="pt-2 border-t mt-2">
                        <div class="text-warning">${window.i18n.t('admin.messages.skipped_too_long')}</div>
                        ${result.skipped_long_tags > 0 ? `<div>Tags: <strong class="text">${result.skipped_long_tags}</strong></div>` : ''}
                        ${result.skipped_long_aliases > 0 ? `<div>Aliases: <strong class="text">${result.skipped_long_aliases}</strong></div>` : ''}
                    </div>
                `;
            }

            html += '</div>';

            if (result.errors && result.errors.length > 0) {
                html += `
                    <div class="bg-warning p-3 mt-2 tag-text text-xs">
                        <strong>${window.i18n.t('admin.messages.warnings_total', { total: result.total_errors })}</strong><br>
                        ${result.errors.slice(0, 5).map(app.translateError).join('<br>')}
                    </div>
                `;
            }

            progressDiv.innerHTML = html;

            // Reload stats
            await this.loadTagStats();

        } catch (error) {
            progressDiv.innerHTML = `
                <div class="bg-danger p-3 tag-text">
                    <strong>Error:</strong> ${error.message}
                </div>
            `;
        }
    }

    async uploadFullBackup(file) {
        const statusDiv = document.getElementById('full-import-status');
        const progressDiv = document.getElementById('full-import-progress');

        statusDiv.style.display = 'block';
        progressDiv.innerHTML = `
            <div class="bg-primary primary-text p-3 mb-2">
                <strong>${window.i18n.t('admin.messages.uploading_importing_backup')}</strong><br>
                <div class="loader mt-2"></div>
                <span class="text-xs">${window.i18n.t('admin.messages.upload_warning_strong')}</span>
            </div>
            `;

        try {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch('/api/admin/import/full', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Import failed');
            }

            const result = await response.json();

            progressDiv.innerHTML = `
                <div class="bg-success p-3 mb-2 tag-text">
                    <strong>${window.i18n.t('admin.messages.import_completed')}</strong>
                </div>
            `;

            // Reload all stats
            this.loadTagStats();
            this.loadMediaStats();
            this.loadAlbumStats();

        } catch (error) {
            progressDiv.innerHTML = `
                <div class="bg-danger p-3 tag-text">
                    <strong>Error:</strong> ${error.message}
                </div>
                `;
        }
    }

    async searchTags() {
        const query = document.getElementById('tag-search-input').value;
        const resultsDiv = document.getElementById('tag-search-results');

        if (!query) {
            resultsDiv.innerHTML = '';
            return;
        }

        try {
            const response = await fetch(`/api/admin/search-tags?q=${encodeURIComponent(query)}`);
            const data = await response.json();

            if (data.tags.length === 0) {
                resultsDiv.innerHTML = '<p class="bg border text-xs text-secondary p-3">' + window.i18n.t('gallery.no_tags_found') + '</p>';
                return;
            }

            resultsDiv.innerHTML = data.tags.map((tag, i, arr) => `
                <div class="bg p-3 ${arr.length === 1 ? 'border' : (i === arr.length - 1 ? '' : 'border-b')} flex justify-between items-center">
                    <div>
                        <button class="delete-tag-btn text-xs bg-danger hover:bg-danger tag-text px-2 py-1 mr-2" data-tag-id="${tag.id}">&#x2715;</button>
                        <a href="/?q=${encodeURIComponent(tag.name)}" class="tag ${tag.category} tag-text">${tag.name}</a>
                        <span class="text-xs text-secondary ml-2">(${tag.post_count} posts)</span>
                    </div>
                    <span class="text-xs text-secondary uppercase">${tag.category}</span>
                </div>
                `).join('');

            // Add event listeners to delete buttons
            resultsDiv.querySelectorAll('.delete-tag-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const tagId = btn.dataset.tagId;
                    this.deleteTag(tagId);
                });
            });

        } catch (error) {
            console.error('Error searching tags:', error);
            resultsDiv.innerHTML = '<p class="text-xs text-danger p-3">Error searching tags</p>';
        }
    }

    async deleteTag(tagId) {
        const modal = new ModalHelper({
            id: 'delete-tag-modal',
            type: 'danger',
            title: window.i18n.t('modal.delete_tag.title'),
            message: window.i18n.t('modal.delete_tag.message'),
            confirmText: window.i18n.t('common.yes_delete'),
            cancelText: window.i18n.t('common.cancel'),
            confirmId: 'delete-tag-confirm-yes',
            cancelId: 'delete-tag-confirm-no',
            onConfirm: async () => {
                try {
                    const result = await app.apiCall(`/api/admin/tags/${tagId}`, { method: 'DELETE' });
                    app.showNotification(window.i18n.t('notifications.admin.tag_deleted', { tag_name: result.tag_name }), 'success');
                    await this.searchTags();
                    await this.loadTagStats();
                } catch (e) {
                    app.showNotification(e.message, 'error', window.i18n.t('notifications.admin.error_deleting_tag'));
                }
            }
        });

        modal.show();
    }

    async clearAllTags() {
        const firstModal = new ModalHelper({
            id: 'clear-tags-first-modal',
            type: 'danger',
            title: window.i18n.t('modal.clear_tags.title'),
            message: window.i18n.t('modal.clear_tags.message'),
            confirmText: window.i18n.t('modal.clear_tags.confirm'),
            cancelText: window.i18n.t('common.cancel'),
            confirmId: 'clear-tags-first-confirm-yes',
            cancelId: 'clear-tags-first-confirm-no',
            onConfirm: () => {
                // Show second confirmation
                const secondModal = new ModalHelper({
                    id: 'clear-tags-second-modal',
                    type: 'danger',
                    title: window.i18n.t('modal.clear_tags_confirm.title'),
                    message: window.i18n.t('modal.clear_tags_confirm.message'),
                    confirmText: window.i18n.t('modal.clear_tags_confirm.confirm'),
                    cancelText: window.i18n.t('common.cancel'),
                    confirmId: 'clear-tags-second-confirm-yes',
                    cancelId: 'clear-tags-second-confirm-no',
                    onConfirm: async () => {
                        try {
                            await app.apiCall('/api/admin/clear-tags', { method: 'DELETE' });
                            app.showNotification(window.i18n.t('common.all_tags_cleared'), 'success');
                            await this.loadTagStats();
                            document.getElementById('tag-search-results').innerHTML = '';
                        } catch (error) {
                            app.showNotification(error.message, 'error', window.i18n.t('notifications.admin.error_clearing_tags'));
                        }
                    }
                });
                secondModal.show();
            }
        });

        firstModal.show();
    }

    async loadThemes() {
        try {
            const response = await fetch('/api/admin/themes');
            const data = await response.json();

            if (!this.themeSelect) {
                console.error('themeSelect is not initialized!');
                return;
            }

            const options = data.themes.map(theme => {
                const emoji = theme.is_dark ? '🌙 ' : '☀️ ';
                return {
                    value: theme.id,
                    text: emoji + theme.name,
                    selected: theme.id === data.current_theme
                };
            });

            this.themeSelect.setOptions(options);

        } catch (error) {
            console.error('Error loading themes:', error);
        }
    }

    async loadLanguages() {
        try {
            const response = await fetch('/api/admin/languages');
            const data = await response.json();

            if (!this.languageSelect) {
                console.error('languageSelect is not initialized!');
                return;
            }

            const options = data.languages.map(lang => ({
                value: lang.id,
                text: lang.native_name,
                selected: lang.id === data.current_language
            }));

            this.languageSelect.setOptions(options);

        } catch (error) {
            console.error('Error loading languages:', error);
        }
    }

    // Album Management Methods

    setupAlbumManagement() {
        // Create album form
        const createAlbumForm = document.getElementById('create-album-form');
        if (createAlbumForm) {
            createAlbumForm.addEventListener('submit', (e) => {
                e.preventDefault();
                this.createAlbum();
            });
        }

        // Album search
        const albumSearchBtn = document.getElementById('album-search-btn');
        const albumSearchInput = document.getElementById('album-search-input');

        albumSearchBtn?.addEventListener('click', () => this.searchAlbums());
        albumSearchInput?.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                this.searchAlbums();
            }
        });

        // Setup parent album select
        const parentAlbumSelectElement = document.getElementById('parent-album-select');
        if (parentAlbumSelectElement) {
            this.parentAlbumSelect = new CustomSelect(parentAlbumSelectElement);
        }

        // Load albums for parent select
        this.loadAlbums();
    }

    async loadAlbumStats() {
        try {
            const response = await fetch('/api/albums?limit=1000');
            const data = await response.json();

            const totalAlbumsEl = document.getElementById('total-albums');
            const rootAlbumsEl = document.getElementById('root-albums');

            if (totalAlbumsEl) totalAlbumsEl.textContent = data.total || 0;

            // Count root albums (albums with no parents)
            let rootCount = 0;
            if (data.items) {
                for (const album of data.items) {
                    const parentsResponse = await fetch(`/api/albums/${album.id}/parents`);
                    const parentsData = await parentsResponse.json();
                    if (!parentsData.parents || parentsData.parents.length === 0) {
                        rootCount++;
                    }
                }
            }
            if (rootAlbumsEl) rootAlbumsEl.textContent = rootCount;
        } catch (error) {
            console.error('Error loading album stats:', error);
        }
    }

    async loadAlbums() {
        try {
            const response = await fetch('/api/albums?limit=1000&sort=name&order=asc');
            const data = await response.json();

            // Update parent album select dropdown
            const parentAlbumSelect = document.getElementById('parent-album-select');
            const parentAlbumContainer = document.getElementById('parent-album-container');

            if (parentAlbumSelect && this.parentAlbumSelect) {
                const items = data.items || [];

                if (items.length === 0) {
                    if (parentAlbumContainer) parentAlbumContainer.style.display = 'none';
                } else {
                    if (parentAlbumContainer) parentAlbumContainer.style.display = 'block';

                    const options = [
                        { value: '', text: window.i18n.t('admin.albums_management.none_root'), selected: true }
                    ];

                    for (const album of items) {
                        options.push({
                            value: album.id.toString(),
                            text: album.name
                        });
                    }

                    this.parentAlbumSelect.setOptions(options);
                }
            }
        } catch (error) {
            console.error('Error loading albums:', error);
        }
    }

    async createAlbum() {
        const nameInput = document.getElementById('album-name-input');
        const parentSelectElement = document.getElementById('parent-album-select');
        const parentId = parentSelectElement?.dataset.value || '';

        const albumName = nameInput.value.trim();
        if (!albumName) {
            app.showNotification(window.i18n.t('notifications.admin.enter_album_name'), 'error');
            return;
        }

        try {
            const albumData = {
                name: albumName,
                parent_album_id: parentId ? parseInt(parentId) : null
            };

            await app.apiCall('/api/albums', {
                method: 'POST',
                body: JSON.stringify(albumData)
            });

            app.showNotification(window.i18n.t('notifications.admin.album_created'), 'success');

            // Clear form
            nameInput.value = '';
            if (this.parentAlbumSelect) {
                this.parentAlbumSelect.setValue('');
            }

            // Reload data
            await this.loadAlbumStats();
            await this.loadAlbums();

        } catch (error) {
            app.showNotification(error.message, 'error', window.i18n.t('notifications.admin.error_creating_album'));
        }
    }

    async searchAlbums() {
        const query = document.getElementById('album-search-input').value;
        const resultsDiv = document.getElementById('album-search-results');

        if (!query) {
            resultsDiv.innerHTML = '';
            return;
        }

        try {
            const response = await fetch('/api/albums?limit=100&sort=name&order=asc');
            const data = await response.json();

            // Filter albums by name
            const filtered = (data.items || []).filter(album =>
                album.name.toLowerCase().includes(query.toLowerCase())
            );

            if (filtered.length === 0) {
                resultsDiv.innerHTML = '<p class="bg border text-xs text-secondary p-3">' + window.i18n.t('album_picker.no_albums') + '</p>';
                return;
            }

            // Build results HTML
            let html = '';
            for (let i = 0; i < filtered.length; i++) {
                const album = filtered[i];
                // Get parent info
                const parentsResponse = await fetch(`/api/albums/${album.id}/parents`);
                const parentsData = await parentsResponse.json();
                const parentChain = parentsData.parents.map(p => p.name).join(' > ');
                const immediateParentId = parentsData.parents.length > 0
                    ? parentsData.parents[parentsData.parents.length - 1].id
                    : null;

                const borderClass = filtered.length === 1 ? 'border' : (i === filtered.length - 1 ? '' : 'border-b');

                html += `
                    <div class="bg p-3 ${borderClass} flex justify-between items-center">
                        <div class="flex-1">
                            <div class="flex items-center gap-2 mb-1">
                                <a href="/album/${album.id}" class="text-sm font-bold hover:text-primary">${this.escapeHtml(album.name)}</a>
                                <span class="text-xs text-secondary">(${album.media_count || 0} media)</span>
                            </div>
                            ${parentChain ? `<div class="text-xs text-secondary">Path: ${this.escapeHtml(parentChain)}</div>` : '<div class="text-xs text-secondary">' + window.i18n.t('albums.root_album') + '</div>'}
                        </div>
                        <div class="flex gap-2">
                            <button class="manage-album-btn btn-primary px-3 py-1"
                                data-album-id="${album.id}"
                                data-album-name="${this.escapeHtml(album.name)}"
                                data-parent-id="${immediateParentId || ''}">${window.i18n.t('common.manage')}</button>
                        </div>
                    </div>
                `;
            }

            resultsDiv.innerHTML = html;

            // Add event listeners for Manage buttons
            resultsDiv.querySelectorAll('.manage-album-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const albumId = btn.dataset.albumId;
                    const albumName = btn.dataset.albumName;
                    const parentId = btn.dataset.parentId || null;
                    this.showAlbumManageModal(albumId, albumName, parentId);
                });
            });

        } catch (error) {
            console.error('Error searching albums:', error);
            resultsDiv.innerHTML = '<p class="text-xs text-danger p-3">Error searching albums</p>';
        }
    }

    showAlbumManageModal(albumId, albumName, currentParentId) {
        // Remove existing modal if any
        const existingModal = document.getElementById('album-manage-modal');
        if (existingModal) {
            existingModal.remove();
        }

        const modal = document.createElement('div');
        modal.id = 'album-manage-modal';
        modal.className = 'age-verification-overlay';
        modal.style.display = 'flex';

        modal.innerHTML = `
            <div class="surface border-2 border-primary p-8 max-w-md w-full text-center">
                <svg class="mx-auto mb-4" width="48" height="48" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z" fill="var(--primary)"/>
                </svg>
                <h2 class="text-xl font-bold mb-2 text-primary">${window.i18n.t('admin.albums_management.manage_album')}</h2>
                <p class="text-base mb-6 text font-medium">${this.escapeHtml(albumName)}</p>
                <div class="flex flex-col gap-3">
                    <button id="album-manage-rename" class="btn-dark px-6 py-3 font-bold text-sm flex items-center justify-center gap-2">
                        <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24">
                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                        </svg>
                        ${window.i18n.t('admin.albums_management.rename_album')}
                    </button>
                    <button id="album-manage-parent" class="btn-dark px-6 py-3 font-bold text-sm flex items-center justify-center gap-2">
                        <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24">
                            <path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/>
                        </svg>
                        ${window.i18n.t('admin.albums_management.change_parent_album')}
                    </button>
                    <button id="album-manage-delete" class="px-6 py-3 transition-colors bg border border-danger text-danger hover:bg-danger hover:tag-text font-bold text-sm flex items-center justify-center gap-2">
                        <svg width="16" height="16" fill="currentColor" viewBox="0 0 24 24">
                            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                        </svg>
                        ${window.i18n.t('common.delete_album')}
                    </button>
                    <button id="album-manage-cancel" class="btn-dark px-6 py-3 font-bold text-sm flex items-center justify-center gap-2">
                        ${window.i18n.t('common.close')}
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Event listeners
        document.getElementById('album-manage-rename').addEventListener('click', () => {
            modal.remove();
            this.showRenameAlbumModal(albumId, albumName, currentParentId);
        });

        document.getElementById('album-manage-parent').addEventListener('click', () => {
            modal.remove();
            this.showChangeParentModal(albumId, albumName, currentParentId);
        });

        document.getElementById('album-manage-delete').addEventListener('click', () => {
            modal.remove();
            this.deleteAlbum(albumId, albumName, currentParentId);
        });

        document.getElementById('album-manage-cancel').addEventListener('click', () => {
            modal.remove();
        });

        // Close on outside click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });

        // Close on Escape
        const handleEscape = (e) => {
            if (e.key === 'Escape') {
                modal.remove();
                document.removeEventListener('keydown', handleEscape);
            }
        };
        document.addEventListener('keydown', handleEscape);
    }

    showRenameAlbumModal(albumId, currentName, currentParentId) {
        // Remove existing modal if any
        const existingModal = document.getElementById('album-rename-modal');
        if (existingModal) {
            existingModal.remove();
        }

        const modal = document.createElement('div');
        modal.id = 'album-rename-modal';
        modal.className = 'age-verification-overlay';
        modal.style.display = 'flex';

        modal.innerHTML = `
            <div class="surface border-2 border-primary p-8 max-w-md w-full">
                <h2 class="text-xl font-bold mb-4 text-primary text-center">Rename Album</h2>
                <div class="mb-6">
                    <label class="block text-xs font-bold mb-2">New Name</label>
                    <input type="text" id="new-album-name" value="${this.escapeHtml(currentName)}"
                        class="w-full bg px-3 py-2 border text-sm focus:outline-none focus:border-primary">
                </div>
                <div class="flex gap-4 justify-center">
                    <button id="album-rename-confirm" class="btn-primary px-6 py-3 font-bold text-sm">
                        Save
                    </button>
                    <button id="album-rename-cancel" class="btn px-6 py-3 font-bold text-sm">
                        Cancel
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Focus the input and select all text
        const input = document.getElementById('new-album-name');
        input.focus();
        input.select();

        // Event listeners
        const confirmRename = async () => {
            const newName = input.value.trim();
            if (!newName) {
                app.showNotification(window.i18n.t('notifications.admin.enter_name'), 'error');
                return;
            }
            if (newName === currentName) {
                modal.remove();
                this.showAlbumManageModal(albumId, currentName, currentParentId);
                return;
            }

            modal.remove();

            try {
                await app.apiCall(`/api/albums/${albumId}`, {
                    method: 'PUT',
                    body: JSON.stringify({ name: newName })
                });

                app.showNotification(window.i18n.t('notifications.admin.album_renamed'), 'success');
                await this.searchAlbums();
                await this.loadAlbums();
                await this.loadAlbumStats();

                // Return to manage modal with updated name
                this.showAlbumManageModal(albumId, newName, currentParentId);

            } catch (error) {
                app.showNotification(error.message, 'error', window.i18n.t('notifications.admin.error_renaming_album'));
                // Return to manage modal on error
                this.showAlbumManageModal(albumId, currentName, currentParentId);
            }
        };

        document.getElementById('album-rename-confirm').addEventListener('click', confirmRename);

        document.getElementById('album-rename-cancel').addEventListener('click', () => {
            modal.remove();
            this.showAlbumManageModal(albumId, currentName, currentParentId);
        });

        // Handle Enter key in input
        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                confirmRename();
            }
        });

        // Close on outside click - return to manage modal
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
                this.showAlbumManageModal(albumId, currentName, currentParentId);
            }
        });

        // Close on Escape - return to manage modal
        const handleEscape = (e) => {
            if (e.key === 'Escape') {
                modal.remove();
                document.removeEventListener('keydown', handleEscape);
                this.showAlbumManageModal(albumId, currentName, currentParentId);
            }
        };
        document.addEventListener('keydown', handleEscape);
    }

    async getAlbumDescendantIds(albumId) {
        const descendantIds = new Set();

        const fetchChildren = async (parentId) => {
            try {
                const response = await fetch(`/api/albums/${parentId}/children`);
                if (!response.ok) return;
                const children = await response.json();

                for (const child of children) {
                    descendantIds.add(child.id.toString());
                    await fetchChildren(child.id);
                }
            } catch (error) {
                console.error('Error fetching children:', error);
            }
        };

        await fetchChildren(albumId);
        return descendantIds;
    }

    async showChangeParentModal(albumId, albumName, currentParentId) {
        // Remove existing modal if any
        const existingModal = document.getElementById('album-parent-modal');
        if (existingModal) {
            existingModal.remove();
        }

        // Load all albums for the dropdown
        let albums = [];
        try {
            const response = await fetch('/api/albums?limit=1000&sort=name&order=asc');
            const data = await response.json();
            albums = data.items || [];
        } catch (error) {
            console.error('Error loading albums:', error);
            app.showNotification(window.i18n.t('notifications.admin.error_loading_albums'), 'error');
            this.showAlbumManageModal(albumId, albumName, currentParentId);
            return;
        }

        // Get all descendant IDs to prevent circular references
        const descendantIds = await this.getAlbumDescendantIds(albumId);

        // Filter out the current album and all its descendants
        const validAlbums = albums.filter(a => {
            const id = a.id.toString();
            return id !== albumId.toString() && !descendantIds.has(id);
        });

        const modal = document.createElement('div');
        modal.id = 'album-parent-modal';
        modal.className = 'age-verification-overlay';
        modal.style.display = 'flex';

        // Build options HTML for custom select
        let optionsHtml = `
            <div class="custom-select-option px-3 py-2 cursor-pointer hover:surface text-xs ${!currentParentId ? 'selected' : ''}"
                data-value="">${window.i18n.t('admin.albums_management.none_root')}</div>
        `;
        for (const album of validAlbums) {
            const isSelected = currentParentId && album.id.toString() === currentParentId.toString();
            optionsHtml += `
                <div class="custom-select-option px-3 py-2 cursor-pointer hover:surface text-xs ${isSelected ? 'selected' : ''}"
                    data-value="${album.id}">${this.escapeHtml(album.name)}</div>
            `;
        }

        // Determine initial display text
        let initialDisplayText = window.i18n.t('admin.albums_management.none_root');
        if (currentParentId) {
            const currentParent = validAlbums.find(a => a.id.toString() === currentParentId.toString());
            if (currentParent) {
                initialDisplayText = currentParent.name;
            }
        }

        modal.innerHTML = `
            <div class="surface border-2 border-primary p-8 max-w-md w-full">
                <h2 class="text-xl font-bold mb-2 text-primary text-center">Change Parent Album</h2>
                <p class="text-sm mb-4 text-secondary text-center">Album: <span class="text font-medium">${this.escapeHtml(albumName)}</span></p>
                <div class="mb-6">
                    <label class="block text-xs font-bold mb-2">New Parent Album</label>
                    <div id="change-parent-select" class="custom-select" data-value="${currentParentId || ''}">
                        <button
                            class="custom-select-trigger w-full flex items-center justify-between gap-3 px-3 py-2 bg border text-xs cursor-pointer focus:outline-none focus:border-primary"
                            type="button">
                            <span class="custom-select-value text">${this.escapeHtml(initialDisplayText)}</span>
                            <svg class="custom-select-arrow flex-shrink-0 transition-transform duration-200 text-secondary"
                                width="12" height="12" viewBox="0 0 12 12">
                                <path fill="currentColor" d="M6 9L1 4h10z" />
                            </svg>
                        </button>
                        <div class="custom-select-dropdown bg border border-primary max-h-60 overflow-y-auto shadow-lg">
                            ${optionsHtml}
                        </div>
                    </div>
                </div>
                <div class="flex gap-4 justify-center">
                    <button id="album-parent-confirm" class="btn-primary px-6 py-3 font-bold text-sm">
                        Save
                    </button>
                    <button id="album-parent-cancel" class="btn px-6 py-3 font-bold text-sm">
                        Cancel
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        // Initialize the custom select
        const selectElement = document.getElementById('change-parent-select');
        const changeParentSelect = new CustomSelect(selectElement);

        // Event listeners
        document.getElementById('album-parent-confirm').addEventListener('click', async () => {
            const newParentId = selectElement.dataset.value;
            modal.remove();

            // Check if parent actually changed
            const oldParentId = currentParentId || '';
            if (newParentId === oldParentId) {
                this.showAlbumManageModal(albumId, albumName, currentParentId);
                return;
            }

            try {
                await app.apiCall(`/api/albums/${albumId}`, {
                    method: 'PUT',
                    body: JSON.stringify({
                        parent_album_id: newParentId ? parseInt(newParentId) : null
                    })
                });

                app.showNotification(window.i18n.t('notifications.admin.parent_album_updated'), 'success');
                await this.searchAlbums();
                await this.loadAlbums();
                await this.loadAlbumStats();

                // Return to manage modal with updated parent
                this.showAlbumManageModal(albumId, albumName, newParentId || null);

            } catch (error) {
                app.showNotification(error.message, 'error', window.i18n.t('notifications.admin.error_updating_parent'));
                // Return to manage modal on error
                this.showAlbumManageModal(albumId, albumName, currentParentId);
            }
        });

        document.getElementById('album-parent-cancel').addEventListener('click', () => {
            modal.remove();
            this.showAlbumManageModal(albumId, albumName, currentParentId);
        });

        // Close on outside click - return to manage modal
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
                this.showAlbumManageModal(albumId, albumName, currentParentId);
            }
        });

        // Close on Escape - return to manage modal
        const handleEscape = (e) => {
            if (e.key === 'Escape') {
                modal.remove();
                document.removeEventListener('keydown', handleEscape);
                this.showAlbumManageModal(albumId, albumName, currentParentId);
            }
        };
        document.addEventListener('keydown', handleEscape);
    }

    async deleteAlbum(albumId, albumName, currentParentId) {
        const modal = new ModalHelper({
            id: 'delete-album-modal',
            type: 'danger',
            title: window.i18n.t('common.delete_album'),
            message: window.i18n.t('modal.delete_album.message', { albumName: this.escapeHtml(albumName) }),
            confirmText: window.i18n.t('common.yes_delete'),
            cancelText: window.i18n.t('common.cancel'),
            confirmId: 'delete-album-confirm-yes',
            cancelId: 'delete-album-confirm-no',
            onConfirm: async () => {
                try {
                    await app.apiCall(`/api/albums/${albumId}?cascade=false`, {
                        method: 'DELETE'
                    });
                    app.showNotification(window.i18n.t('notifications.admin.album_deleted'), 'success');
                    await this.searchAlbums();
                    await this.loadAlbums();
                    await this.loadAlbumStats();
                    // Don't return to manage modal since album is deleted
                } catch (e) {
                    app.showNotification(e.message, 'error', window.i18n.t('notifications.admin.error_deleting_album'));
                    // Return to manage modal on error
                    this.showAlbumManageModal(albumId, albumName, currentParentId);
                }
            },
            onCancel: () => {
                // Return to manage modal when cancelled
                this.showAlbumManageModal(albumId, albumName, currentParentId);
            }
        });

        modal.show();
    }
    showApiKeyNameModal() {
        const inputId = 'api-key-name-input';
        const modal = new ModalHelper({
            id: 'api-key-name-modal',
            title: window.i18n.t('modal.api_key_name.title'),
            message: `
                <div class="text-left">
                    <input type="text" id="${inputId}" 
                        class="w-full bg px-3 py-2 mb-4 border text-xs hover:border-primary transition-colors focus:outline-none focus:border-primary"
                        placeholder="${window.i18n.t('modal.api_key_name.placeholder')}"
                        autocomplete="off">
                </div>
            `,
            confirmText: window.i18n.t('modal.api_key_name.confirm'),
            cancelText: window.i18n.t('common.cancel'),
            onConfirm: () => {
                const nameInput = document.getElementById(inputId);
                const name = nameInput ? nameInput.value.trim() : '';
                this.generateApiKey(name);
                modal.destroy();
            },
            onCancel: () => {
                modal.destroy();
            }
        });

        modal.show();

        // Focus and clear input
        const input = document.getElementById(inputId);
        if (input) {
            input.value = '';
            input.focus();
            input.addEventListener('keypress', (e) => {
                if (e.key === 'Enter') {
                    modal.confirm();
                }
            });
        }
    }

    setupApiKeyManagement() {
        this.loadApiKeys();

        document.getElementById('generate-api-key-btn')?.addEventListener('click', () => {
            this.showApiKeyNameModal();
        });

        document.getElementById('copy-api-key-btn')?.addEventListener('click', () => {
            const input = document.getElementById('new-api-key-value');
            input.select();
            document.execCommand('copy');
            app.showNotification(window.i18n.t('notifications.admin.api_key_copied'), 'success');
        });
    }

    async loadApiKeys() {
        try {
            const response = await fetch('/api/admin/api-keys');
            if (response.ok) {
                const keys = await response.json();
                this.renderApiKeys(keys);
            }
        } catch (e) { console.error(e); }
    }

    renderApiKeys(keys) {
        const listContainer = document.getElementById('api-keys-list');
        if (!listContainer) return;

        if (keys.length === 0) {
            listContainer.innerHTML = `<div class="bg p-6 text-center text-xs text-secondary opacity-70">${window.i18n.t('notifications.admin.no_active_api_keys')}</div>`;
            return;
        }

        listContainer.innerHTML = keys.map(key => `
            <div class="bg p-4 border-b last:border-b-0 flex justify-between items-center hover:surface transition-colors">
                <div class="flex-1 min-w-0 pr-4">
                    <div class="font-bold text-xs truncate mb-1 text">${this.escapeHtml(key.name || 'Unnamed Key')}</div>
                    <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-secondary opacity-80">
                        <span class="font-mono bg surface px-1 border border-primary border-opacity-20">${this.escapeHtml(key.key_prefix)}...</span>
                        <span>Created: <strong>${new Date(key.created_at).toLocaleDateString()}</strong></span>
                        <span>Last used: <strong>${key.last_used_at ? new Date(key.last_used_at).toLocaleDateString() : 'Never'}</strong></span>
                    </div>
                </div>
                <div class="flex-shrink-0">
                    <button class="btn-danger px-3 py-1 text-[10px] uppercase font-bold tracking-wider" 
                        onclick="window.adminPanel.revokeApiKey(${key.id})">
                        Revoke
                    </button>
                </div>
            </div>
        `).join('');
    }

    async generateApiKey(name) {
        try {
            const response = await app.apiCall('/api/admin/api-keys', {
                method: 'POST',
                body: JSON.stringify({ name: name })
            });

            // Show result
            const display = document.getElementById('new-api-key-display');
            if (display) display.style.display = 'block';

            const input = document.getElementById('new-api-key-value');
            if (input) input.value = response.key;

            // Reload list
            this.loadApiKeys();

            app.showNotification(window.i18n.t('notifications.admin.api_key_generated'), 'success');
        } catch (error) {
            app.showNotification(error.message, 'error', window.i18n.t('notifications.admin.error_generating_api_key'));
        }
    }

    async revokeApiKey(keyId) {
        const modal = new ModalHelper({
            id: 'revoke-api-key-modal',
            type: 'danger',
            title: window.i18n.t('modal.revoke_api_key.title'),
            message: window.i18n.t('modal.revoke_api_key.message'),
            confirmText: window.i18n.t('modal.revoke_api_key.confirm'),
            cancelText: window.i18n.t('common.cancel'),
            onConfirm: async () => {
                try {
                    await app.apiCall(`/api/admin/api-keys/${keyId}`, {
                        method: 'DELETE'
                    });

                    this.loadApiKeys();
                    app.showNotification(window.i18n.t('notifications.admin.api_key_revoked'), 'success');
                } catch (error) {
                    app.showNotification(error.message, 'error', window.i18n.t('notifications.admin.error_revoking_api_key'));
                } finally {
                    modal.destroy();
                }
            },
            onCancel: () => {
                modal.destroy();
            }
        });

        modal.show();
    }

    setupSystemUpdate() {
        if (document.getElementById('btn-check-updates')) {
            document.getElementById('btn-check-updates').addEventListener('click', () => this.checkUpdateStatus());
        }
        document.getElementById('btn-update-now')?.addEventListener('click', () => this.performUpdate());
        document.getElementById('btn-view-changelog')?.addEventListener('click', () => this.showChangelog());

        this.updateData = null;
    }

    async checkUpdateStatus() {
        const initialState = document.getElementById('update-initial-state');
        const loading = document.getElementById('update-loading');
        const statusDiv = document.getElementById('update-status');

        if (!loading || !statusDiv) return;

        if (initialState) initialState.classList.add('hidden');
        loading.classList.remove('hidden');
        loading.style.display = 'block';
        statusDiv.style.display = 'none';

        try {
            const response = await fetch('/api/system/update/check');
            if (!response.ok) throw new Error('Failed to check for updates');

            const status = await response.json();
            this.updateData = status;

            loading.style.display = 'none';
            loading.classList.add('hidden');
            statusDiv.style.display = 'block';

            const currentEl = document.getElementById('current-version-display');
            if (currentEl) currentEl.textContent = status.current_version;

            const latestEl = document.getElementById('latest-version-display');
            if (latestEl) latestEl.textContent = status.latest_version;

            const noticesDiv = document.getElementById('update-notices');
            if (noticesDiv) {
                if (status.notices && status.notices.length > 0) {
                    noticesDiv.classList.remove('hidden');
                    noticesDiv.innerHTML = status.notices.map(n => {
                        const translated = window.i18n ? window.i18n.t(n) : n;
                        return `<div class="bg text-xs p-2 mb-1 border-l-4 border-warning text-warning font-bold">${this.escapeHtml(translated)}</div>`;
                    }).join('');
                } else {
                    noticesDiv.classList.add('hidden');
                }
            }

            // Update instructions for Docker users
            const instructionsDiv = document.getElementById('update-instructions');
            const commandText = document.getElementById('update-command-text');
            if (instructionsDiv && commandText && status.update_available) {
                if (status.deployment_type === 'ghcr') {
                    instructionsDiv.classList.remove('hidden');
                    commandText.textContent = 'docker compose up -d --pull always';
                } else if (status.deployment_type === 'docker_local') {
                    instructionsDiv.classList.remove('hidden');
                    commandText.textContent = 'docker compose down && docker compose -f docker-compose.dev.yml up --build';
                } else {
                    instructionsDiv.classList.add('hidden');
                }
            } else if (instructionsDiv) {
                instructionsDiv.classList.add('hidden');
            }

            // Config files notice
            const configNotice = document.getElementById('config-files-notice');
            const configMessage = document.getElementById('config-files-message');
            const configLinks = document.getElementById('config-files-links');
            if (configNotice && status.config_files_changed && status.update_available) {
                configNotice.classList.remove('hidden');
                const fileNames = (status.changed_config_files || []).join(', ');
                const msg = window.i18n ? window.i18n.t('admin.update.config_files_changed', { files: fileNames }) : `Configuration files changed: ${fileNames}`;
                if (configMessage) configMessage.textContent = msg;
                if (configLinks) {
                    configLinks.innerHTML = Object.entries(status.asset_urls || {}).map(([name, url]) =>
                        `<a href="${this.escapeHtml(url)}" target="_blank" class="btn-dark px-3 py-1 text-[10px]">${this.escapeHtml(name)}</a>`
                    ).join('');
                }
            } else if (configNotice) {
                configNotice.classList.add('hidden');
            }

            // Update Now button (only for local/non-Docker)
            const updateBtn = document.getElementById('btn-update-now');
            if (updateBtn) {
                if (status.update_available && status.deployment_type === 'local') {
                    updateBtn.style.display = 'block';
                    updateBtn.disabled = false;
                } else if (!status.update_available && status.deployment_type === 'local') {
                    updateBtn.style.display = 'block';
                    updateBtn.disabled = true;
                    updateBtn.textContent = window.i18n ? window.i18n.t('admin.messages.already_latest') : 'Already up to date';
                } else {
                    updateBtn.style.display = 'none';
                }
            }

            // View Release button
            const releaseBtn = document.getElementById('btn-view-release');
            if (releaseBtn && status.release_url) {
                releaseBtn.style.display = 'inline-block';
                releaseBtn.href = status.release_url;
            }

            // Up to date message
            if (!status.update_available) {
                const noticesDiv2 = document.getElementById('update-notices');
                if (noticesDiv2) {
                    noticesDiv2.classList.remove('hidden');
                    const msg = window.i18n ? window.i18n.t('admin.update.up_to_date') : 'You are running the latest version.';
                    noticesDiv2.innerHTML = `<div class="text-xs tag-text p-2 border border-success bg-success bg-opacity-10">${this.escapeHtml(msg)}</div>`;
                }
            }

            // Changelog button
            const changelogBtn = document.getElementById('btn-view-changelog');
            if (changelogBtn) {
                const hasContent = (status.releases && status.releases.length > 0) || (status.commits && status.commits.length > 0);
                if (hasContent && status.update_available) {
                    changelogBtn.style.display = 'block';
                    const count = (status.commits || []).length;
                    changelogBtn.textContent = window.i18n ? window.i18n.t('admin.messages.view_changelog', { count }) : `View Changelog (${count})`;
                } else {
                    changelogBtn.style.display = 'none';
                }
            }

        } catch (e) {
            console.error(e);
            if (loading) {
                loading.textContent = window.i18n ? window.i18n.t('admin.messages.update_error', { error: e.message }) : `Error: ${e.message}`;
                loading.classList.remove('hidden');
                loading.classList.add('text-danger');
            }
        }
    }

    showChangelog() {
        if (!this.updateData) return;
        const { releases, commits, compare_url, current_version, latest_version } = this.updateData;
        if ((!releases || releases.length === 0) && (!commits || commits.length === 0)) return;

        if (typeof ModalHelper === 'undefined') {
            console.error('ModalHelper not available');
            return;
        }

        const t = (key, params) => window.i18n ? window.i18n.t(key, params) : key;

        const extractWhatsChanged = (body) => {
            if (!body) return '';
            const marker = "## What's Changed";
            const idx = body.indexOf(marker);
            if (idx === -1) return '';
            const afterMarker = body.substring(idx + marker.length);
            const nextHeading = afterMarker.indexOf('\n## ');
            let section = nextHeading !== -1 ? afterMarker.substring(0, nextHeading) : afterMarker;
            // Strip the "**Full Changelog**: ..." line
            section = section.replace(/\*\*Full Changelog\*\*:.*$/gm, '').trim();
            return section;
        };

        const parseMarkdownList = (text) => {
            const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
            const listItems = lines.map(line => {
                // Strip leading "* " or "- "
                const cleaned = line.replace(/^[\*\-]\s+/, '');
                return `<li class="mb-1 last:mb-0">${this.escapeHtml(cleaned)}</li>`;
            });
            return `<ul class="list-disc list-inside text-xs">${listItems.join('')}</ul>`;
        };

        // What's Changed Tab
        let changesHtml = '';
        if (releases && releases.length > 0) {
            for (const rel of releases) {
                const changes = extractWhatsChanged(rel.body);
                if (!changes) continue;
                changesHtml += `
                <div class="bg p-2 border-b last:border-0 text-left">
                    <div class="mb-2">
                        <a href="${this.escapeHtml(rel.url)}" target="_blank" class="font-mono text-xs bg-primary primary-text px-1 hover:bg-primary transition-colors">${this.escapeHtml(rel.tag)}</a>
                    </div>
                    ${parseMarkdownList(changes)}
                </div>`;
            }
        }

        // Commits Tab
        let commitsHtml = '';
        if (commits && commits.length > 0) {
            for (const c of commits) {
                commitsHtml += `
                <div class="border-b last:border-0 text-left">
                    <div class="flex items-center bg p-2 gap-2">
                        <a href="https://github.com/mrblomblo/blombooru/commit/${this.escapeHtml(c.hash)}" target="_blank" class="font-mono text-xs bg-primary primary-text px-1 hover:bg-primary transition-colors">${this.escapeHtml(c.hash)}</a>
                        <span class="text-xs">${this.escapeHtml(c.message)}</span>
                    </div>
                </div>`;
            }
        }

        const hasChanges = changesHtml.length > 0;
        const hasCommits = commitsHtml.length > 0;

        const tabLabelChanges = t('admin.update.tab_whats_changed');
        const tabLabelCommits = t('admin.update.tab_commits', { count: (commits || []).length });
        const noReleasesMsg = t('admin.update.no_release_notes');
        const noCommitsMsg = t('admin.update.no_commits');
        const fullChangelogMsg = t('admin.update.view_full_changelog', { current: current_version, latest: latest_version });

        // Build tabbed UI
        const tabsHtml = `
        <div>
            ${(hasChanges && hasCommits) ? `
            <div class="flex border-b mb-3" id="changelog-tabs">
                <button class="px-3 py-1 text-xs font-bold border-b-2 border-primary text-primary" data-tab="changes">${this.escapeHtml(tabLabelChanges)}</button>
                <button class="px-3 py-1 text-xs text-secondary hover:text-primary" data-tab="commits">${this.escapeHtml(tabLabelCommits)}</button>
            </div>` : ''}
            <div class="max-h-80 overflow-y-auto custom-scrollbar">
                <div id="tab-changes" ${!hasChanges ? 'style="display:none"' : ''}>${changesHtml || `<div class="text-xs text-secondary">${this.escapeHtml(noReleasesMsg)}</div>`}</div>
                <div id="tab-commits" style="display:${hasChanges ? 'none' : 'block'}">${commitsHtml || `<div class="text-xs text-secondary">${this.escapeHtml(noCommitsMsg)}</div>`}</div>
            </div>
            ${compare_url ? `<div class="mt-3 pt-2 border-t text-center"><a href="${this.escapeHtml(compare_url)}" target="_blank" class="text-xs text-primary hover:text-primary transition-colors">${this.escapeHtml(fullChangelogMsg)}</a></div>` : ''}
        </div>`;

        const modal = new ModalHelper({
            id: 'changelog-modal',
            type: 'info',
            title: window.i18n ? window.i18n.t('modal.changelog.title') : 'Changelog',
            message: tabsHtml,
            confirmText: window.i18n ? window.i18n.t('common.got_it') : 'Got it',
            cancelText: '',
            onConfirm: () => {
                modal.destroy();
            }
        });

        modal.show();

        // Wire up tab switching
        const tabContainer = document.getElementById('changelog-tabs');
        if (tabContainer) {
            tabContainer.querySelectorAll('button').forEach(btn => {
                btn.addEventListener('click', () => {
                    const tabName = btn.dataset.tab;
                    // Update active tab styles
                    tabContainer.querySelectorAll('button').forEach(b => {
                        b.classList.remove('border-b-2', 'border-primary', 'text-primary');
                        b.classList.add('text-secondary');
                    });
                    btn.classList.add('border-b-2', 'border-primary', 'text-primary');
                    btn.classList.remove('text-secondary');
                    // Show/hide tab content
                    document.getElementById('tab-changes').style.display = tabName === 'changes' ? 'block' : 'none';
                    document.getElementById('tab-commits').style.display = tabName === 'commits' ? 'block' : 'none';
                });
            });
        }
    }

    async performUpdate() {
        if (typeof ModalHelper === 'undefined') {
            console.error('ModalHelper not available');
            if (!confirm(window.i18n ? window.i18n.t('notifications.admin.update_confirm', { target: 'latest' }) : 'Update now?')) return;
            this._execute_update();
            return;
        }

        const modal = new ModalHelper({
            id: 'update-confirm-modal',
            type: 'warning',
            title: window.i18n ? window.i18n.t('common.system_update') : 'System Update',
            message: window.i18n ? window.i18n.t('modal.system_update.message', { target: 'latest' }) : 'Are you sure you want to update?',
            confirmText: window.i18n ? window.i18n.t('modal.system_update.confirm') : 'Update Now',
            cancelText: window.i18n ? window.i18n.t('common.cancel') : 'Cancel',
            onConfirm: () => {
                this._execute_update();
                modal.destroy();
            },
            onCancel: () => {
                modal.destroy();
            }
        });

        modal.show();
    }

    async _execute_update() {
        const resultDiv = document.getElementById('update-result');
        const resultLog = document.getElementById('update-result-log');
        if (resultDiv) resultDiv.classList.remove('hidden');
        if (resultLog) resultLog.textContent = window.i18n ? window.i18n.t('admin.messages.update_started') : 'Starting update...';

        try {
            const response = await app.apiCall('/api/system/update/perform', {
                method: 'POST',
                body: JSON.stringify({ target: 'latest' })
            });

            if (resultLog) {
                resultLog.textContent = (response.log || '') + "\n\n" + (response.message || '');
            }

            if (response.success) {
                app.showNotification(
                    window.i18n ? window.i18n.t('notifications.admin.update_initiated') : 'Update initiated',
                    'success'
                );
            }
        } catch (e) {
            if (resultLog) resultLog.textContent += `\nError: ${e.message}`;
            app.showNotification(
                window.i18n ? window.i18n.t('notifications.admin.update_failed', { error: e.message }) : `Update failed: ${e.message}`,
                'error'
            );
        }
    }
    // ---- AI Tagging Jobs (Phase 2.3) ----

    async loadAutoTagConfig() {
        const el = document.getElementById('ai-jobs-config-content');
        if (!el) return;
        try {
            const data = await app.apiCall('/api/admin/ai-tagging/auto-config', { method: 'GET' });
            const badge = (val) => val
                ? '<span class="text-green-400 font-bold">开启</span>'
                : '<span class="text-red-400 font-bold">关闭</span>';
            el.innerHTML = `
                <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
                    <div>AI 打标总开关：${badge(data.ai_tagging_enabled)}</div>
                    <div>导入后自动打标：${badge(data.auto_tag_after_import)}</div>
                    <div>自动打标最大数量：<span class="font-bold text-yellow-300">${data.auto_tag_max_items}</span></div>
                    <div>仅处理新导入：${badge(data.auto_tag_only_new)}</div>
                    <div>Dry Run 模式：${badge(data.auto_tag_dry_run)}</div>
                    <div>强制为建议：${badge(data.auto_tag_force_suggestions)}</div>
                    <div>Batch 上限 (AI_TAGGING_BATCH_MAX_ITEMS)：<span class="font-bold text-yellow-300">${data.batch_max_items}</span></div>
                    <div>自动翻译：${badge(data.tag_translation_auto)}</div>
                    <div>LLM 翻译：${badge(data.tag_translation_llm)}</div>
                </div>
                <p class="text-xs text-secondary mt-2">配置通过 .env 文件管理，修改后需重启服务。详细诊断见 System → 开发者工具。</p>
            `;
        } catch (e) {
            el.textContent = `加载失败: ${e.message || e}`;
        }
    }

    async createAITagJob() {
        const btn = document.getElementById('ai-job-create-btn');
        const idsInput = document.getElementById('ai-job-media-ids').value.trim();
        const maxItems = parseInt(document.getElementById('ai-job-max-items').value) || 10;
        const dryRun = document.getElementById('ai-job-dry-run').checked;
        const forceSuggestions = document.getElementById('ai-job-force-suggestions').checked;
        const onlyUntagged = document.getElementById('ai-job-only-untagged').checked;

        let mediaIds = null;
        if (idsInput) {
            mediaIds = idsInput.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n));
            if (mediaIds.length === 0) {
                app.showNotification('无效的 Media IDs', 'error');
                return;
            }
        }

        btn.disabled = true;
        btn.textContent = '创建中…';
        try {
            const body = {
                max_items: maxItems,
                dry_run: dryRun,
                only_without_ai_tags: onlyUntagged,
                force_suggestions: forceSuggestions,
            };
            if (mediaIds) body.media_ids = mediaIds;

            const data = await app.apiCall('/api/admin/ai-tagging/jobs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            app.showNotification(`AI 打标任务 #${data.id} 已创建`, 'success');
            this._currentAiJobId = data.id;
            this._startAiJobPolling(data.id);
        } catch (e) {
            app.showNotification(`创建失败: ${e.message || e}`, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '创建任务';
        }
    }

    async cancelAITagJob() {
        if (!this._currentAiJobId) return;
        try {
            await app.apiCall(`/api/admin/ai-tagging/jobs/${this._currentAiJobId}/cancel`, { method: 'POST' });
            app.showNotification('取消请求已发送', 'success');
        } catch (e) {
            app.showNotification(`取消失败: ${e.message || e}`, 'error');
        }
    }

    _startAiJobPolling(jobId) {
        if (this._aiJobPollTimer) clearInterval(this._aiJobPollTimer);
        const container = document.getElementById('ai-job-progress-container');
        if (container) container.style.display = '';
        this._aiJobPollTimer = setInterval(() => this._pollAiJob(jobId), 1500);
        this._pollAiJob(jobId);
    }

    async _pollAiJob(jobId) {
        try {
            const data = await app.apiCall(`/api/admin/ai-tagging/jobs/${jobId}`, { method: 'GET' });
            this._renderAiJobProgress(data);
            if (['completed', 'failed', 'cancelled', 'interrupted'].includes(data.status)) {
                clearInterval(this._aiJobPollTimer);
                this._aiJobPollTimer = null;
                this._currentAiJobId = null;
                this.loadAIJobHistory();
            }
        } catch (e) {
            clearInterval(this._aiJobPollTimer);
            this._aiJobPollTimer = null;
        }
    }

    _renderAiJobProgress(job) {
        const statsEl = document.getElementById('ai-job-progress-stats');
        const statusEl = document.getElementById('ai-job-progress-status');
        const fillEl = document.getElementById('ai-job-progress-fill');
        const cancelBtn = document.getElementById('ai-job-cancel-btn');

        if (!statsEl) return;

        const total = job.max_items || 1;
        const pct = Math.min(100, Math.round((job.processed / total) * 100));
        if (fillEl) fillEl.style.width = pct + '%';
        if (cancelBtn) cancelBtn.disabled = !['pending', 'running'].includes(job.status);

        const statusMap = {
            pending: '等待中', running: '运行中', completed: '已完成',
            failed: '失败', cancelled: '已取消', cancelling: '取消中', interrupted: '已中断'
        };

        statsEl.innerHTML = `
            <div class="bg p-2 border text-center"><div class="font-bold">${job.processed}</div><div class="text-secondary">已处理</div></div>
            <div class="bg p-2 border text-center"><div class="font-bold text-green-400">${job.tags_added}</div><div class="text-secondary">确认标签</div></div>
            <div class="bg p-2 border text-center"><div class="font-bold text-yellow-400">${job.suggestions_added}</div><div class="text-secondary">建议标签</div></div>
            <div class="bg p-2 border text-center"><div class="font-bold">${job.skipped_locked}</div><div class="text-secondary">跳过锁定</div></div>
            <div class="bg p-2 border text-center"><div class="font-bold">${job.ignored_low_confidence}</div><div class="text-secondary">忽略低信度</div></div>
            <div class="bg p-2 border text-center"><div class="font-bold text-red-400">${job.failed}</div><div class="text-secondary">失败</div></div>
        `;
        let statusText = `状态: ${statusMap[job.status] || job.status}`;
        if (job.trigger_source === 'scan_job' && job.scan_job_id) {
            statusText += ` | 关联 Scan #${job.scan_job_id}`;
        }
        if (job.dry_run) statusText += ' | Dry Run';
        if (job.force_suggestions) statusText += ' | 强制建议';
        if (job.localization_status) statusText += ` | 本地化: ${job.localization_status}`;
        if (job.error_message) statusText += ` | ${job.error_message}`;
        if (statusEl) statusEl.textContent = statusText;
    }

    async loadAIJobHistory() {
        const tbody = document.getElementById('ai-jobs-history-tbody');
        const emptyEl = document.getElementById('ai-jobs-history-empty');
        if (!tbody) return;
        try {
            const jobs = await app.apiCall('/api/admin/ai-tagging/jobs', { method: 'GET' });
            if (!jobs || jobs.length === 0) {
                tbody.innerHTML = '';
                if (emptyEl) emptyEl.style.display = '';
                return;
            }
            if (emptyEl) emptyEl.style.display = 'none';

            // Auto-resume polling if a job is running
            const runningJob = jobs.find(j => ['pending', 'running', 'cancelling'].includes(j.status));
            if (runningJob && !this._aiJobPollTimer) {
                this._currentAiJobId = runningJob.id;
                this._startAiJobPolling(runningJob.id);
            }

            const statusMap = {
                pending: '⏳ 等待', running: '▶️ 运行', completed: '✅ 完成',
                failed: '❌ 失败', cancelled: '⏹ 取消', cancelling: '⏸ 取消中', interrupted: '⚠️ 中断'
            };
            tbody.innerHTML = jobs.map(j => `
                <tr class="border-b hover:bg-gray-800/30 text-[11px] cursor-pointer" onclick="adminPanel._showAiJobDetail(${j.id})">
                    <td class="py-1 px-2">${j.id}</td>
                    <td class="py-1 px-2">${statusMap[j.status] || j.status}</td>
                    <td class="py-1 px-2">${j.trigger_source === 'scan_job' ? '扫描触发' : '手动'}</td>
                    <td class="py-1 px-2">${j.scan_job_id || '-'}</td>
                    <td class="py-1 px-2">${j.processed}</td>
                    <td class="py-1 px-2 text-green-400">${j.tags_added}</td>
                    <td class="py-1 px-2 text-yellow-400">${j.suggestions_added}</td>
                    <td class="py-1 px-2 text-red-400">${j.failed}</td>
                    <td class="py-1 px-2 text-xs">${j.localization_status || '-'}</td>
                    <td class="py-1 px-2">${j.created_at ? new Date(j.created_at).toLocaleString('zh-CN') : '-'}</td>
                </tr>
            `).join('');
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="10" class="py-2 px-2 text-red-400">加载失败: ${e.message || e}</td></tr>`;
        }
    }

    async _showAiJobDetail(jobId) {
        try {
            const job = await app.apiCall(`/api/admin/ai-tagging/jobs/${jobId}`, { method: 'GET' });
            this._renderAiJobProgress(job);
            const container = document.getElementById('ai-job-progress-container');
            if (container) container.style.display = '';
            if (['pending', 'running', 'cancelling'].includes(job.status)) {
                this._currentAiJobId = job.id;
                this._startAiJobPolling(job.id);
            }
        } catch (e) {
            app.showNotification(`加载任务详情失败: ${e.message || e}`, 'error');
        }
    }

    // ---- Tag Localization ----

    async loadTagLocalizationStats() {
        try {
            const data = await app.apiCall('/api/admin/tag-localization/stats');
            const el = (id) => document.getElementById(id);
            if (el('tl-total-tags')) el('tl-total-tags').textContent = data.total_tags || 0;
            if (el('tl-translated')) el('tl-translated').textContent = data.total_covered || 0;
            if (el('tl-missing')) el('tl-missing').textContent = data.missing || 0;
            if (el('tl-needs-review')) el('tl-needs-review').textContent = data.needs_review || 0;

            const breakdown = data.source_breakdown || {};
            const parts = [];
            const t = (k) => window.i18n ? window.i18n.t(k) : k;
            if (breakdown.static) parts.push(`${t('admin.tag_localization.source_static')}: ${breakdown.static}`);
            if (breakdown.manual) parts.push(`${t('admin.tag_localization.source_manual')}: ${breakdown.manual}`);
            if (breakdown.llm) parts.push(`${t('admin.tag_localization.source_llm')}: ${breakdown.llm}`);
            if (breakdown.imported) parts.push(`${t('admin.tag_localization.source_imported')}: ${breakdown.imported}`);
            const bd = el('tl-source-breakdown');
            if (bd) bd.textContent = parts.length ? `${t('admin.tag_localization.source_breakdown')}: ${parts.join(' | ')}` : '';
        } catch (e) {
            console.error('Failed to load tag localization stats:', e);
        }
    }

    async loadLLMStatus() {
        try {
            const data = await app.apiCall('/api/admin/tag-localization/llm-status');
            const el = document.getElementById('tl-llm-status');
            if (!el) return;
            const t = (k) => window.i18n ? window.i18n.t(k) : k;
            if (!data.enabled) {
                el.innerHTML = `<span class="text-warning">${t('admin.tag_localization.llm_not_configured')}</span>`;
            } else {
                const statusText = data.available ? t('admin.tag_localization.llm_available') : t('admin.tag_localization.llm_unavailable');
                const statusClass = data.available ? 'text-success' : 'text-warning';
                const apiKeyStatus = data.api_key_configured
                    ? `<span class="text-success">${t('admin.tag_localization.api_key_yes')}</span>`
                    : `<span class="text-warning">${t('admin.tag_localization.api_key_no')}</span>`;
                const autoStatus = data.auto_enabled
                    ? `<span class="text-success">${t('admin.tag_localization.auto_enabled_yes')}</span>`
                    : `<span class="text-secondary">${t('admin.tag_localization.auto_enabled_no')}</span>`;
                el.innerHTML = `
                    <div>${t('admin.tag_localization.llm_status')}: <span class="${statusClass}">${statusText}</span></div>
                    ${data.model ? `<div>Model: ${this.escapeHtml(data.model)}</div>` : ''}
                    <div>${t('admin.tag_localization.api_key_configured')}: ${apiKeyStatus}</div>
                    <div>${t('admin.tag_localization.auto_enabled')}: ${autoStatus}${data.auto_enabled ? ` (max: ${data.auto_max_items})` : ''}</div>
                `;
            }
        } catch (e) {
            console.error('Failed to load LLM status:', e);
        }
    }

    async testLLMTranslation() {
        const resultEl = document.getElementById('tl-test-result');
        if (!resultEl) return;
        resultEl.classList.remove('hidden');
        resultEl.innerHTML = '<span class="text-secondary">Testing...</span>';
        try {
            const data = await app.apiCall('/api/admin/tag-localization/test-llm', {method: 'POST'});
            const t = (k) => window.i18n ? window.i18n.t(k) : k;
            if (data.success) {
                const r = data.result;
                resultEl.innerHTML = `<span class="text-success">${t('admin.tag_localization.test_success')}</span>: ${this.escapeHtml(r.canonical_name)} → ${this.escapeHtml(r.display_name_zh)} (aliases: ${this.escapeHtml(JSON.stringify(r.aliases_zh))})`;
            } else {
                resultEl.innerHTML = `<span class="text-warning">${t('admin.tag_localization.test_failed')}</span>: ${this.escapeHtml(data.error || 'Unknown error')}`;
            }
        } catch (e) {
            resultEl.innerHTML = `<span class="text-error">Error: ${this.escapeHtml(e.message)}</span>`;
        }
    }

    async saveTagTranslation() {
        const canonical = document.getElementById('tl-edit-canonical').value.trim();
        const display = document.getElementById('tl-edit-display').value.trim();
        if (!canonical || !display) {
            app.showNotification('Please fill in canonical tag and display name', 'error');
            return;
        }
        const aliasStr = document.getElementById('tl-edit-aliases').value.trim();
        const aliases = aliasStr ? aliasStr.split(',').map(s => s.trim()).filter(Boolean) : [];
        const category = document.getElementById('tl-edit-category').value || null;
        const reviewed = document.getElementById('tl-edit-reviewed').checked;

        try {
            await app.apiCall('/api/admin/tag-localization/translations', {
                method: 'POST',
                body: JSON.stringify({
                    canonical_name: canonical,
                    display_name: display,
                    aliases: aliases,
                    category: category,
                    source: 'manual',
                    status: reviewed ? 'reviewed' : 'translated',
                    needs_review: !reviewed,
                })
            });
            const t = (k) => window.i18n ? window.i18n.t(k) : k;
            app.showNotification(t('admin.tag_localization.translation_saved'), 'success');
            document.getElementById('tl-edit-canonical').value = '';
            document.getElementById('tl-edit-display').value = '';
            document.getElementById('tl-edit-aliases').value = '';
            this.loadTagLocalizationStats();
        } catch (e) {
            app.showNotification(`Error: ${e.message || e}`, 'error');
        }
    }

    async runBatchTranslation() {
        const dryRun = document.getElementById('tl-batch-dryrun').checked;
        const maxItems = parseInt(document.getElementById('tl-batch-max').value) || 200;
        const category = document.getElementById('tl-batch-category').value || null;

        const resultDiv = document.getElementById('tl-batch-result');
        resultDiv.classList.remove('hidden');
        resultDiv.innerHTML = '<span class="text-secondary text-xs">Processing...</span>';

        try {
            const body = { dry_run: dryRun, max_items: maxItems };
            if (category) body.category = category;
            const data = await app.apiCall('/api/admin/tag-localization/batch-translate', {
                method: 'POST',
                body: JSON.stringify(body)
            });
            const t = (k) => window.i18n ? window.i18n.t(k) : k;
            let html = `<div class="bg p-3 border text-xs">`;
            html += `<p class="font-bold mb-2">${t('admin.tag_localization.batch_result')}</p>`;
            if (data.effective_max !== undefined && data.requested_max !== undefined && data.requested_max > data.effective_max) {
                html += `<p class="text-warning mb-1">请求: ${data.requested_max} → 实际上限: ${data.effective_max} (受后端 TAG_TRANSLATION_BATCH_MAX_ITEMS 限制)</p>`;
            }
            html += `<p>候选: ${data.candidates || 0} | `;
            html += `${t('admin.tag_localization.batch_translated')}: ${data.translated || 0} | `;
            html += `${t('admin.tag_localization.batch_failed')}: ${data.failed || 0} | `;
            html += `${t('admin.tag_localization.batch_skipped')}: ${data.skipped || 0}</p>`;
            if (data.dry_run) {
                html += `<p class="text-secondary mt-1">🔍 模拟运行 — 不会写入数据库</p>`;
            }
            if (data.errors && data.errors.length) {
                html += `<p class="text-warning mt-1">Errors: ${data.errors.map(e => this.escapeHtml(e)).join('; ')}</p>`;
            }
            if (data.translations && data.translations.length) {
                html += `<table class="w-full mt-2"><thead><tr><th class="text-left p-1">Tag</th><th class="text-left p-1">中文名</th><th class="text-left p-1">Review</th></tr></thead><tbody>`;
                const displayLimit = Math.min(data.translations.length, 50);
                for (let idx = 0; idx < displayLimit; idx++) {
                    const tr = data.translations[idx];
                    html += `<tr><td class="p-1">${this.escapeHtml(tr.canonical_name)}</td>`;
                    html += `<td class="p-1">${this.escapeHtml(tr.display_name_zh)}</td>`;
                    html += `<td class="p-1">${tr.needs_review ? '⚠️' : '✓'}</td></tr>`;
                }
                if (data.translations.length > displayLimit) {
                    html += `<tr><td colspan="3" class="p-1 text-secondary">... 和其他 ${data.translations.length - displayLimit} 条翻译</td></tr>`;
                }
                html += `</tbody></table>`;
            }
            html += `</div>`;
            resultDiv.innerHTML = html;
            this.loadTagLocalizationStats();
        } catch (e) {
            resultDiv.innerHTML = `<span class="text-warning text-xs">Error: ${this.escapeHtml(e.message || String(e))}</span>`;
        }
    }

    async loadMissingTranslations() {
        const category = document.getElementById('tl-missing-category').value || '';
        const tbody = document.getElementById('tl-missing-tbody');
        const thead = document.getElementById('tl-missing-thead');
        const emptyDiv = document.getElementById('tl-missing-empty');

        try {
            let url = '/api/admin/tag-localization/missing?limit=100';
            if (category) url += `&category=${category}`;
            const items = await app.apiCall(url);
            tbody.innerHTML = '';
            if (!items || items.length === 0) {
                thead.classList.add('hidden');
                emptyDiv.classList.remove('hidden');
                return;
            }
            thead.classList.remove('hidden');
            emptyDiv.classList.add('hidden');
            const t = (k) => window.i18n ? window.i18n.t(k) : k;
            for (const item of items) {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="p-2">${this.escapeHtml(item.canonical_name)}</td>
                    <td class="p-2"><span class="tag ${this.escapeHtml(item.category)} tag-text text-xs">${this.escapeHtml(item.category)}</span></td>
                    <td class="p-2">${item.post_count}</td>
                    <td class="p-2"><button class="btn btn-sm px-2 py-0.5 text-xs tl-edit-btn" data-name="${this.escapeHtml(item.canonical_name)}" data-category="${this.escapeHtml(item.category)}">${t('admin.tag_localization.edit')}</button></td>
                `;
                tbody.appendChild(tr);
            }
        } catch (e) {
            app.showNotification(`Error: ${e.message || e}`, 'error');
        }
    }

    async loadTranslationReview() {
        const search = document.getElementById('tl-review-search').value.trim();
        const source = document.getElementById('tl-review-source').value;
        const status = document.getElementById('tl-review-status').value;
        const needsReview = document.getElementById('tl-review-needs-review').checked;
        const tbody = document.getElementById('tl-review-tbody');
        const thead = document.getElementById('tl-review-thead');
        const emptyDiv = document.getElementById('tl-review-empty');
        const pagination = document.getElementById('tl-review-pagination');

        try {
            let url = `/api/admin/tag-localization/translations?limit=${this._tlReviewLimit}&offset=${this._tlReviewOffset}`;
            if (search) url += `&search=${encodeURIComponent(search)}`;
            if (source) url += `&source=${source}`;
            if (status) url += `&status=${status}`;
            if (needsReview) url += `&needs_review=true`;
            const data = await app.apiCall(url);
            this._tlReviewTotal = data.total || 0;
            tbody.innerHTML = '';
            if (!data.items || data.items.length === 0) {
                thead.classList.add('hidden');
                emptyDiv.classList.remove('hidden');
                pagination.style.display = 'none';
                return;
            }
            thead.classList.remove('hidden');
            emptyDiv.classList.add('hidden');
            pagination.style.display = 'flex';
            const t = (k) => window.i18n ? window.i18n.t(k) : k;
            for (const item of data.items) {
                const aliases = (item.aliases || []).join(', ');
                const reviewBadge = item.needs_review ? ' <span class="text-warning" title="needs review">⚠️</span>' : '';
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="p-2">${this.escapeHtml(item.canonical_name)}</td>
                    <td class="p-2">${this.escapeHtml(item.display_name)}${reviewBadge}</td>
                    <td class="p-2 text-secondary">${this.escapeHtml(aliases)}</td>
                    <td class="p-2"><span class="text-xs">${this.escapeHtml(item.source)}</span></td>
                    <td class="p-2"><span class="text-xs">${this.escapeHtml(item.status)}</span></td>
                    <td class="p-2">
                        <button class="btn btn-sm px-1 py-0.5 text-xs tl-action-btn" data-action="approve" data-id="${item.id}" title="${t('admin.tag_localization.approve')}">✓</button>
                        <button class="btn btn-sm px-1 py-0.5 text-xs tl-action-btn" data-action="reject" data-id="${item.id}" title="${t('admin.tag_localization.reject')}">✗</button>
                        <button class="btn btn-sm px-1 py-0.5 text-xs tl-action-btn" data-action="delete" data-id="${item.id}" title="${t('admin.tag_localization.delete')}">🗑</button>
                        <button class="btn btn-sm px-1 py-0.5 text-xs tl-review-edit-btn" data-name="${this.escapeHtml(item.canonical_name)}" data-display="${this.escapeHtml(item.display_name)}" data-aliases="${this.escapeHtml(aliases)}" data-category="${this.escapeHtml(item.category || '')}" title="${t('admin.tag_localization.edit')}">✏</button>
                    </td>
                `;
                tbody.appendChild(tr);
            }
            const pageInfo = document.getElementById('tl-review-page-info');
            const start = this._tlReviewOffset + 1;
            const end = Math.min(this._tlReviewOffset + data.items.length, this._tlReviewTotal);
            pageInfo.textContent = `${start}-${end} / ${this._tlReviewTotal}`;
            document.getElementById('tl-review-prev-btn').disabled = this._tlReviewOffset === 0;
            document.getElementById('tl-review-next-btn').disabled = end >= this._tlReviewTotal;
        } catch (e) {
            app.showNotification(`Error: ${e.message || e}`, 'error');
        }
    }

    _tlReviewPageNav(dir) {
        if (dir < 0) {
            this._tlReviewOffset = Math.max(0, this._tlReviewOffset - this._tlReviewLimit);
        } else {
            this._tlReviewOffset += this._tlReviewLimit;
        }
        this.loadTranslationReview();
    }

    async tagTranslationAction(action, id) {
        const t = (k) => window.i18n ? window.i18n.t(k) : k;
        try {
            if (action === 'approve' || action === 'reject') {
                await app.apiCall(`/api/admin/tag-localization/translations/${id}/review?action=${action}`, { method: 'POST' });
                const msg = action === 'approve' ? t('admin.tag_localization.translation_approved') : t('admin.tag_localization.translation_rejected');
                app.showNotification(msg, 'success');
            } else if (action === 'delete') {
                await app.apiCall(`/api/admin/tag-localization/translations/${id}`, { method: 'DELETE' });
                app.showNotification(t('admin.tag_localization.translation_deleted'), 'success');
            }
            this.loadTranslationReview();
            this.loadTagLocalizationStats();
        } catch (e) {
            app.showNotification(`Error: ${e.message || e}`, 'error');
        }
    }

    // ---- Background Translation Worker (Phase 2.3d) ----

    async loadWorkerStatus() {
        try {
            const data = await app.apiCall('/api/admin/tag-localization/worker/status');
            const el = (id) => document.getElementById(id);
            const statusMap = {
                disabled: t('admin.tag_localization.worker_status_disabled'),
                idle: t('admin.tag_localization.worker_status_idle'),
                running: t('admin.tag_localization.worker_status_running'),
                paused: t('admin.tag_localization.worker_status_paused'),
                stopped: t('admin.tag_localization.worker_status_stopped'),
            };
            const statusEl = el('tl-worker-status');
            if (statusEl) {
                const statusText = statusMap[data.status] || data.status;
                const cls = data.status === 'running' ? 'text-green-400' :
                            data.status === 'paused' ? 'text-yellow-400' :
                            data.status === 'disabled' ? 'text-secondary' : '';
                statusEl.innerHTML = `<span class="${cls}">${statusText}</span>`;
            }
            if (el('tl-worker-missing')) el('tl-worker-missing').textContent = data.missing_count ?? '-';
            if (el('tl-worker-today')) el('tl-worker-today').textContent = `${data.processed_today ?? 0} / ${data.daily_limit ?? '-'}`;
            if (el('tl-worker-daily-limit')) el('tl-worker-daily-limit').textContent = data.daily_limit ?? '-';
            const cfg = data.config || {};
            if (el('tl-worker-batch-size')) el('tl-worker-batch-size').textContent = cfg.batch_size ?? '-';
            if (el('tl-worker-max-per-run')) el('tl-worker-max-per-run').textContent = cfg.max_per_run ?? '-';
            if (el('tl-worker-last-run')) el('tl-worker-last-run').textContent = data.last_run_at ? new Date(data.last_run_at).toLocaleString() : '-';
            if (el('tl-worker-next-run')) el('tl-worker-next-run').textContent = data.next_run_at ? new Date(data.next_run_at).toLocaleString() : '-';

            const errEl = el('tl-worker-error');
            if (errEl) {
                if (data.last_error) {
                    errEl.textContent = data.last_error;
                    errEl.classList.remove('hidden');
                } else {
                    errEl.classList.add('hidden');
                }
            }

            const pauseBtn = el('tl-worker-pause-btn');
            const resumeBtn = el('tl-worker-resume-btn');
            if (pauseBtn && resumeBtn) {
                if (data.paused) {
                    pauseBtn.classList.add('hidden');
                    resumeBtn.classList.remove('hidden');
                } else {
                    pauseBtn.classList.remove('hidden');
                    resumeBtn.classList.add('hidden');
                }
            }

            this.loadWorkerJobs();
        } catch (e) {
            console.error('Failed to load worker status:', e);
        }
    }

    async loadWorkerJobs() {
        try {
            const data = await app.apiCall('/api/admin/tag-localization/worker/jobs?limit=10');
            const container = document.getElementById('tl-worker-jobs');
            if (!container) return;
            if (!data.jobs || data.jobs.length === 0) {
                container.innerHTML = '<span class="text-secondary">-</span>';
                return;
            }
            let html = '<table class="w-full text-xs"><thead><tr class="border-b">';
            html += '<th class="py-1 px-2 text-left">ID</th>';
            html += '<th class="py-1 px-2 text-left">' + t('admin.tag_localization.status') + '</th>';
            html += '<th class="py-1 px-2 text-left">' + t('admin.tag_localization.source') + '</th>';
            html += '<th class="py-1 px-2 text-right">' + t('admin.tag_localization.batch_translated') + '</th>';
            html += '<th class="py-1 px-2 text-right">' + t('admin.tag_localization.batch_failed') + '</th>';
            html += '<th class="py-1 px-2 text-right">' + t('admin.tag_localization.batch_skipped') + '</th>';
            html += '<th class="py-1 px-2 text-left">' + t('admin.tag_localization.worker_last_run') + '</th>';
            html += '</tr></thead><tbody>';
            for (const j of data.jobs) {
                const cls = j.status === 'completed' ? 'text-green-400' :
                            j.status === 'failed' || j.status === 'rate_limited' ? 'text-red-400' : '';
                html += `<tr class="border-b border-dashed">`;
                html += `<td class="py-1 px-2">${j.id}</td>`;
                html += `<td class="py-1 px-2 ${cls}">${j.status}</td>`;
                html += `<td class="py-1 px-2">${j.source}</td>`;
                html += `<td class="py-1 px-2 text-right">${j.translated}</td>`;
                html += `<td class="py-1 px-2 text-right">${j.failed}</td>`;
                html += `<td class="py-1 px-2 text-right">${j.skipped}</td>`;
                html += `<td class="py-1 px-2">${j.started_at ? new Date(j.started_at).toLocaleString() : '-'}</td>`;
                html += `</tr>`;
            }
            html += '</tbody></table>';
            container.innerHTML = html;
        } catch (e) {
            console.error('Failed to load worker jobs:', e);
        }
    }

    async workerRunNow() {
        try {
            await app.apiCall('/api/admin/tag-localization/worker/run-now', { method: 'POST' });
            app.showNotification(t('admin.tag_localization.worker_run_triggered'), 'success');
            setTimeout(() => this.loadWorkerStatus(), 2000);
        } catch (e) {
            app.showNotification(`Error: ${e.message || e}`, 'error');
        }
    }

    async workerPause() {
        try {
            await app.apiCall('/api/admin/tag-localization/worker/pause', { method: 'POST' });
            app.showNotification(t('admin.tag_localization.worker_paused_msg'), 'success');
            this.loadWorkerStatus();
        } catch (e) {
            app.showNotification(`Error: ${e.message || e}`, 'error');
        }
    }

    async workerResume() {
        try {
            await app.apiCall('/api/admin/tag-localization/worker/resume', { method: 'POST' });
            app.showNotification(t('admin.tag_localization.worker_resumed_msg'), 'success');
            this.loadWorkerStatus();
        } catch (e) {
            app.showNotification(`Error: ${e.message || e}`, 'error');
        }
    }

    // ---- Entity Alias Resolver (Phase 2.3e) ----

    async loadEntityStatus() {
        try {
            const data = await app.apiCall('/api/admin/tag-localization/entity/status', { method: 'GET' });
            const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
            const statusEl = document.getElementById('tl-entity-status');
            if (statusEl) {
                statusEl.textContent = data.enabled ? t('admin.tag_localization.entity_enabled') : t('admin.tag_localization.entity_disabled');
                statusEl.className = data.enabled ? 'text-green-400 font-bold' : 'text-red-400 font-bold';
            }
            setEl('tl-entity-total', data.total_proper_noun_tags);
            setEl('tl-entity-resolved', data.resolved);
            setEl('tl-entity-needs-review', data.needs_review);
            setEl('tl-entity-no-translation', data.no_translation);
            if (data.config) {
                setEl('tl-entity-batch-size', data.config.batch_size);
                setEl('tl-entity-max-per-run', data.config.max_per_run);
            }
            const llmEl = document.getElementById('tl-entity-llm');
            if (llmEl) {
                llmEl.textContent = data.llm_available ? t('admin.tag_localization.entity_enabled') : t('admin.tag_localization.entity_disabled');
                llmEl.className = data.llm_available ? 'text-green-400 font-bold' : 'text-red-400 font-bold';
            }
        } catch (e) {
            console.error('Failed to load entity status:', e);
        }
    }

    async resolveEntities() {
        const btn = document.getElementById('tl-entity-resolve-btn');
        if (btn) btn.disabled = true;
        const resultEl = document.getElementById('tl-entity-result');
        try {
            const data = await app.apiCall('/api/admin/tag-localization/entity/resolve', { method: 'POST' });
            app.showNotification(t('admin.tag_localization.entity_resolve_triggered'), 'success');
            if (resultEl) {
                const msg = t('admin.tag_localization.entity_result')
                    .replace('{resolved}', data.resolved || 0)
                    .replace('{kept}', data.kept_original || 0)
                    .replace('{failed}', data.failed || 0);
                resultEl.textContent = msg;
                resultEl.classList.remove('hidden');
            }
            this.loadEntityStatus();
            this.loadTagLocalizationStats();
        } catch (e) {
            app.showNotification(`Error: ${e.message || e}`, 'error');
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async loadEntityPending() {
        const tbody = document.getElementById('tl-entity-pending-tbody');
        const thead = document.getElementById('tl-entity-pending-thead');
        const emptyEl = document.getElementById('tl-entity-pending-empty');
        if (!tbody) return;
        try {
            const data = await app.apiCall('/api/admin/tag-localization/entity/pending?limit=100', { method: 'GET' });
            if (!data || data.length === 0) {
                tbody.innerHTML = '';
                if (thead) thead.classList.add('hidden');
                if (emptyEl) emptyEl.classList.remove('hidden');
                return;
            }
            if (thead) thead.classList.remove('hidden');
            if (emptyEl) emptyEl.classList.add('hidden');
            tbody.innerHTML = data.map(item => {
                const catBadge = `<span class="px-1.5 py-0.5 rounded text-xs bg-gray-700">${item.category}</span>`;
                const reviewBadge = item.has_unreviewed_llm
                    ? `<span class="text-yellow-400 text-xs">${t('admin.tag_localization.entity_has_unreviewed')}</span>`
                    : '';
                const currentDisplay = item.current_display
                    ? `<span class="text-xs text-gray-400">${t('admin.tag_localization.entity_current_display')}: ${item.current_display}</span>`
                    : '';
                return `<tr class="border-t border-gray-700">
                    <td class="py-1 px-2 font-mono text-sm">${item.canonical_name}</td>
                    <td class="py-1 px-2">${catBadge}</td>
                    <td class="py-1 px-2 text-right">${item.post_count}</td>
                    <td class="py-1 px-2">${reviewBadge} ${currentDisplay}</td>
                </tr>`;
            }).join('');
        } catch (e) {
            console.error('Failed to load entity pending:', e);
            app.showNotification(`Error: ${e.message || e}`, 'error');
        }
    }

    // ---- Developer / E2E Tools (Phase 2.3a) ----

    async loadDevConfigDiagnostics() {
        const el = document.getElementById('dev-config-content');
        if (!el) return;
        try {
            const data = await app.apiCall('/api/admin/dev/config-diagnostics', { method: 'GET' });
            const badge = (val) => val
                ? '<span class="text-green-400 font-bold">ON</span>'
                : '<span class="text-red-400 font-bold">OFF</span>';
            const ai = data.ai_tagging || {};
            const auto = data.auto_tag_after_import || {};
            const loc = data.tag_localization || {};
            const paths = data.paths || {};

            el.innerHTML = `
                <div class="mb-3">
                    <div class="font-bold text-xs mb-1 text-primary">AI Tagging</div>
                    <div class="grid grid-cols-2 sm:grid-cols-3 gap-1">
                        <div>启用：${badge(ai.enabled)}</div>
                        <div>Batch 上限：<span class="font-bold text-yellow-300">${ai.batch_max_items}</span></div>
                        <div>模型：${ai.model_name || 'N/A'}</div>
                    </div>
                </div>
                <div class="mb-3">
                    <div class="font-bold text-xs mb-1 text-primary">导入后自动打标</div>
                    <div class="grid grid-cols-2 sm:grid-cols-3 gap-1">
                        <div>启用：${badge(auto.enabled)}</div>
                        <div>最大数量：<span class="font-bold text-yellow-300">${auto.max_items}</span></div>
                        <div>仅新导入：${badge(auto.only_new)}</div>
                        <div>Dry Run：${badge(auto.dry_run)}</div>
                        <div>强制建议：${badge(auto.force_suggestions)}</div>
                    </div>
                </div>
                <div class="mb-3">
                    <div class="font-bold text-xs mb-1 text-primary">标签本地化</div>
                    <div class="grid grid-cols-2 sm:grid-cols-3 gap-1">
                        <div>LLM 翻译：${badge(loc.llm_enabled)}</div>
                        <div>自动翻译：${badge(loc.auto_enabled)}</div>
                        <div>自动上限：<span class="font-bold">${loc.auto_max_items}</span></div>
                        <div>批量上限：<span class="font-bold">${loc.batch_max_items}</span></div>
                        <div>API Key：${badge(loc.api_key_configured)}</div>
                        <div>模型：${loc.model || 'N/A'}</div>
                    </div>
                </div>
                <div class="mb-3">
                    <div class="font-bold text-xs mb-1 text-primary">后台自动翻译</div>
                    <div class="grid grid-cols-2 sm:grid-cols-3 gap-1">
                        <div>启用：${badge(loc.background_enabled)}</div>
                        <div>间隔：<span class="font-bold">${loc.background_interval || '-'}s</span></div>
                        <div>批量大小：<span class="font-bold">${loc.background_batch_size || '-'}</span></div>
                        <div>每轮上限：<span class="font-bold">${loc.background_max_per_run || '-'}</span></div>
                        <div>每日上限：<span class="font-bold">${loc.background_daily_limit || '-'}</span></div>
                        <div>错误阈值：<span class="font-bold">${loc.background_error_limit || '-'}</span></div>
                        <div>优先级：<span class="font-bold">${loc.background_priority || '-'}</span></div>
                    </div>
                </div>
                <div class="mb-2">
                    <div class="font-bold text-xs mb-1 text-primary">路径</div>
                    <div>LOCAL_LIBRARY_PATHS：<span class="font-mono">${(paths.local_library_paths || []).join(', ') || '(未配置)'}</span></div>
                </div>
                <div class="text-[10px] text-secondary mt-2">
                    .env 路径：${data.env_file || '(unknown)'}　|　修改 .env 后需重启服务
                </div>
            `;

            const serverInfoDiv = document.getElementById('dev-server-info');
            const serverGrid = document.getElementById('dev-server-info-grid');
            if (serverInfoDiv && serverGrid && data.server) {
                const srv = data.server;
                const scn = data.scan || {};
                serverInfoDiv.style.display = 'block';
                serverGrid.innerHTML = `
                    <div>PID：<span class="font-bold">${srv.pid || '—'}</span></div>
                    <div>Python：<span class="font-bold">${srv.python_version ? srv.python_version.split(' ')[0] : '—'}</span></div>
                    <div>版本：<span class="font-bold">${srv.app_version || '—'}</span></div>
                    <div>平台：<span class="font-bold">${srv.platform || '—'}</span></div>
                    <div>Debug：<span class="font-bold">${srv.debug ? 'ON' : 'OFF'}</span></div>
                    <div>根目录：<span class="font-mono text-[10px]">${srv.base_dir || '—'}</span></div>
                    <div class="col-span-full border-t border-base mt-1 pt-1 font-bold text-xs text-primary">扫描配置</div>
                    <div>仅已下载：<span class="font-bold">${scn.hydrated_only_default ? 'ON' : 'OFF'}</span></div>
                    <div>超时：<span class="font-bold">${scn.file_open_timeout_seconds ?? '—'}s</span></div>
                    <div>文件上限：<span class="font-bold">${scn.max_file_size_mb ?? '—'} MB</span></div>
                `;
            }
        } catch (e) {
            el.textContent = `加载失败: ${e.message || e}`;
        }
    }

    async loadRecommendedE2EConfig() {
        const container = document.getElementById('dev-recommended-config');
        const snippet = document.getElementById('dev-recommended-snippet');
        if (!container || !snippet) return;

        if (!container.classList.contains('hidden')) {
            container.classList.add('hidden');
            return;
        }

        try {
            const data = await app.apiCall('/api/admin/dev/recommended-e2e-config', { method: 'GET' });
            snippet.textContent = data.snippet || '';
            container.classList.remove('hidden');
        } catch (e) {
            snippet.textContent = `加载失败: ${e.message || e}`;
            container.classList.remove('hidden');
        }
    }

    async resetE2ETestData(dryRun) {
        const sourcePath = document.getElementById('dev-reset-source-path')?.value?.trim();
        const resultDiv = document.getElementById('dev-reset-result');
        if (!sourcePath) {
            app.showNotification('请输入源目录路径', 'error');
            return;
        }
        if (!resultDiv) return;

        if (!dryRun) {
            if (!confirm('确定要执行真实重置吗？这将删除所有从该目录导入的数据。原始文件不受影响。')) {
                return;
            }
        }

        resultDiv.classList.remove('hidden');
        resultDiv.innerHTML = '<span class="text-secondary">处理中...</span>';

        try {
            const data = await app.apiCall('/api/admin/dev/reset-e2e-test-data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    source_path: sourcePath,
                    dry_run: dryRun,
                    confirm: !dryRun,
                }),
            });
            const s = data.summary || {};
            const label = dryRun ? '🔍 Dry Run 预览' : '✅ 重置完成';
            let html = `<div class="font-bold mb-2">${label}</div>`;

            if (dryRun) {
                html += `<div class="grid grid-cols-2 sm:grid-cols-4 gap-1">
                    <div>媒体：${s.media_count || 0}</div>
                    <div>复制文件：${s.copied_files_count || 0}</div>
                    <div>缩略图：${s.thumbnail_files_count || 0}</div>
                    <div>标签关联：${s.tag_associations_count || 0}</div>
                    <div>受影响标签：${s.affected_tags_count || 0}</div>
                    <div>扫描任务：${s.scan_job_count || 0}</div>
                    <div>AI 打标任务：${s.ai_tag_job_count || 0}</div>
                    <div>导入链接：${s.scan_job_media_count || 0}</div>
                </div>`;
                if (s.media_count === 0) {
                    html += '<div class="text-secondary mt-2">未找到从该目录导入的数据。</div>';
                }
            } else {
                html += `<div class="grid grid-cols-2 sm:grid-cols-4 gap-1">
                    <div>删除媒体：${s.media_deleted || 0}</div>
                    <div>删除文件：${s.files_deleted || 0}</div>
                    <div>删除缩略图：${s.thumbnails_deleted || 0}</div>
                    <div>删除标签关联：${s.tag_associations_deleted || 0}</div>
                    <div>删除扫描任务：${s.scan_jobs_deleted || 0}</div>
                    <div>删除 AI 任务：${s.ai_tag_jobs_deleted || 0}</div>
                    <div>标签重算：${s.tags_recalculated || 0}</div>
                </div>`;
                html += '<div class="text-green-400 mt-2">重置完成，可以重新运行 E2E 测试。</div>';
            }

            resultDiv.innerHTML = html;
            if (!dryRun) {
                app.showNotification('E2E 测试数据已重置', 'success');
            }
        } catch (e) {
            resultDiv.innerHTML = `<span class="text-red-500">失败: ${e.message || e}</span>`;
            app.showNotification(`重置失败: ${e.message || e}`, 'error');
        }
    }
}

// Initialize admin panel
if (document.getElementById('admin-panel')) {
    const adminPanel = new AdminPanel();

}

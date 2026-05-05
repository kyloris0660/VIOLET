/**
 * Tag Localization Helper
 * Loads Chinese display names for Danbooru canonical tags.
 * Priority: backend DB translations > static JSON dictionary > canonical tag.
 * Canonical tags in the database remain English; this is UI-only.
 */
(function() {
    const TagLocalization = {
        _dict: null,
        _loading: false,
        _loaded: false,
        _apiCache: {},
        _pendingNames: new Set(),
        _batchTimer: null,

        async load() {
            if (this._loaded || this._loading) return;
            this._loading = true;
            try {
                const resp = await fetch('/static/data/tag_translations_zh.json');
                if (resp.ok) {
                    const data = await resp.json();
                    this._dict = data.tags || {};
                }
            } catch (e) {
                console.warn('Tag localization dictionary not available:', e.message);
                this._dict = {};
            }
            this._loaded = true;
            this._loading = false;
        },

        getDisplayName(canonicalName) {
            if (this._apiCache[canonicalName]) return this._apiCache[canonicalName];
            if (!this._dict) return canonicalName;
            return this._dict[canonicalName] || canonicalName;
        },

        getCanonicalName(displayName) {
            for (const [name, cached] of Object.entries(this._apiCache)) {
                if (cached === displayName) return name;
            }
            if (!this._dict) return displayName;
            for (const [canonical, zh] of Object.entries(this._dict)) {
                if (zh === displayName) return canonical;
            }
            return displayName;
        },

        getDisplayWithCanonical(canonicalName) {
            const zh = this.getDisplayName(canonicalName);
            if (zh !== canonicalName) {
                return `${zh} (${canonicalName})`;
            }
            return canonicalName;
        },

        async fetchBatchTranslations(names) {
            if (!names || names.length === 0) return;
            const uncached = names.filter(n => !(n in this._apiCache));
            if (uncached.length === 0) return;

            try {
                const resp = await fetch(`/api/tags/translations/batch?names=${encodeURIComponent(uncached.join(','))}`);
                if (resp.ok) {
                    const data = await resp.json();
                    Object.assign(this._apiCache, data);
                }
            } catch (e) {
                console.warn('Failed to fetch tag translations from API:', e.message);
            }
        },

        queueForTranslation(canonicalName) {
            if (canonicalName in this._apiCache) return;
            this._pendingNames.add(canonicalName);
            if (this._batchTimer) clearTimeout(this._batchTimer);
            this._batchTimer = setTimeout(() => this._flushPending(), 100);
        },

        async _flushPending() {
            if (this._pendingNames.size === 0) return;
            const names = Array.from(this._pendingNames);
            this._pendingNames.clear();
            await this.fetchBatchTranslations(names);
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => TagLocalization.load());
    } else {
        TagLocalization.load();
    }

    window.TagLocalization = TagLocalization;
})();

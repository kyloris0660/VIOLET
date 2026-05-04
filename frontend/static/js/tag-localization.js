/**
 * Tag Localization Helper
 * Loads Chinese display names for Danbooru canonical tags.
 * Canonical tags in the database remain English; this is UI-only.
 */
(function() {
    const TagLocalization = {
        _dict: null,
        _loading: false,
        _loaded: false,

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
            if (!this._dict) return canonicalName;
            return this._dict[canonicalName] || canonicalName;
        },

        getCanonicalName(displayName) {
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
        }
    };

    // Auto-load on DOMContentLoaded
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => TagLocalization.load());
    } else {
        TagLocalization.load();
    }

    window.TagLocalization = TagLocalization;
})();

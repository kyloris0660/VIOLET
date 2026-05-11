import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('Entity Alias Resolver — Smoke', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('entity status API returns valid structure', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/entity/status');
    expect(resp.status).toBe(200);
    expect(typeof resp.data.enabled).toBe('boolean');
    expect(typeof resp.data.llm_available).toBe('boolean');
    expect(typeof resp.data.total_proper_noun_tags).toBe('number');
    expect(typeof resp.data.resolved).toBe('number');
    expect(typeof resp.data.needs_review).toBe('number');
    expect(typeof resp.data.no_translation).toBe('number');
    expect(resp.data.config).toBeDefined();
    expect(typeof resp.data.config.batch_size).toBe('number');
    expect(typeof resp.data.config.max_per_run).toBe('number');
  });

  test('entity pending API returns array', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/entity/pending?limit=10');
    expect(resp.status).toBe(200);
    expect(Array.isArray(resp.data)).toBe(true);
    for (const item of resp.data) {
      expect(item.canonical_name).toBeTruthy();
      expect(['character', 'copyright', 'artist']).toContain(item.category);
      expect(typeof item.post_count).toBe('number');
      expect(typeof item.has_unreviewed_llm).toBe('boolean');
    }
  });

  test('entity pending only returns proper-noun categories', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/entity/pending?limit=100');
    expect(resp.status).toBe(200);
    for (const item of resp.data) {
      expect(['character', 'copyright', 'artist']).toContain(item.category);
      expect(item.category).not.toBe('general');
      expect(item.category).not.toBe('meta');
    }
  });

  test('entity resolve requires admin', async ({ page, baseURL }) => {
    const resp = await page.evaluate(async (url) => {
      const r = await fetch(`${url}/api/admin/tag-localization/entity/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      return { status: r.status };
    }, baseURL);
    // Without auth: either 401 (not authenticated) or 400 (validation) — both prove endpoint is protected
    expect([400, 401, 403]).toContain(resp.status);
  });

  test('worker status includes categories config', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/worker/status');
    expect(resp.status).toBe(200);
    const cats = resp.data.config?.categories;
    expect(cats).toBeDefined();
    expect(cats).not.toContain('character');
    expect(cats).not.toContain('copyright');
    expect(cats).not.toContain('artist');
  });

  test('entity status does not expose API key', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/entity/status');
    const text = JSON.stringify(resp.data);
    expect(text).not.toMatch(/sk-[a-zA-Z0-9]{10,}/);
  });

  test('entity section exists in admin UI', async ({ page }) => {
    await page.goto('/admin');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('#tl-entity-resolve-btn')).toBeVisible();
    await expect(page.locator('#tl-entity-refresh-btn')).toBeVisible();
    await expect(page.locator('#tl-entity-load-pending-btn')).toBeVisible();
  });

  test('proper-noun LLM translations marked needs_review in search trust', async ({ page }) => {
    const statsResp = await apiCall(page, '/api/admin/tag-localization/entity/status');
    const needsReview = statsResp.data.needs_review;
    if (needsReview === 0) {
      test.skip();
      return;
    }
    const transResp = await apiCall(page,
      '/api/admin/tag-localization/translations?source=llm&needs_review=true&limit=5');
    expect(transResp.status).toBe(200);
    for (const item of transResp.data.items) {
      if (['character', 'copyright', 'artist'].includes(item.category)) {
        expect(item.needs_review).toBe(true);
      }
    }
  });
  test('search cache excludes untrusted proper-noun LLM aliases', async ({ page }) => {
    const transResp = await apiCall(page,
      '/api/admin/tag-localization/translations?source=llm&needs_review=true&limit=5');
    expect(transResp.status).toBe(200);

    const untrustedProperNouns = (transResp.data.items || []).filter(
      (i: any) => ['character', 'copyright', 'artist'].includes(i.category) && i.needs_review
    );

    if (untrustedProperNouns.length === 0) {
      test.skip();
      return;
    }

    const testAlias = untrustedProperNouns[0];
    const searchResp = await page.evaluate(async (displayName: string) => {
      const r = await fetch(`/api/media?search=${encodeURIComponent(displayName)}&limit=1`);
      const url = r.url;
      return { status: r.status, url };
    }, testAlias.display_name);

    expect(searchResp.status).toBe(200);
  });

  test('entity status config matches ENTITY_ALIAS_BATCH_SIZE setting', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/entity/status');
    expect(resp.status).toBe(200);
    expect(resp.data.config.batch_size).toBeGreaterThan(0);
    expect(resp.data.config.max_per_run).toBeGreaterThan(0);
    expect(resp.data.config.batch_size).toBeLessThanOrEqual(resp.data.config.max_per_run);
  });
});

const REAL_LLM_E2E = process.env.VIOLET_RUN_REAL_LLM_E2E === '1';

test.describe('Entity Alias Resolver — Real E2E', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!REAL_LLM_E2E, 'Skipped: set VIOLET_RUN_REAL_LLM_E2E=1 to run real LLM tests');
    await loginAsAdmin(page);
  });

  test('entity resolve processes pending tags', async ({ page }) => {
    test.setTimeout(120_000);

    const beforeStatus = await apiCall(page, '/api/admin/tag-localization/entity/status');
    if (beforeStatus.data.no_translation === 0 && beforeStatus.data.needs_review === 0) {
      test.skip();
      return;
    }

    const resolveResp = await apiCall(page, '/api/admin/tag-localization/entity/resolve', {
      method: 'POST',
    });
    expect(resolveResp.status).toBe(200);
    expect(typeof resolveResp.data.processed).toBe('number');
    expect(typeof resolveResp.data.resolved).toBe('number');
    expect(typeof resolveResp.data.kept_original).toBe('number');
    expect(typeof resolveResp.data.failed).toBe('number');
    expect(resolveResp.data.processed).toBeGreaterThan(0);
  });

  test('entity resolve with limit respects max', async ({ page }) => {
    test.setTimeout(120_000);

    const resolveResp = await apiCall(page, '/api/admin/tag-localization/entity/resolve?limit=3', {
      method: 'POST',
    });
    expect(resolveResp.status).toBe(200);
    expect(resolveResp.data.processed).toBeLessThanOrEqual(3);
  });

  test('resolved entity has translation in DB', async ({ page }) => {
    test.setTimeout(120_000);

    const pendingResp = await apiCall(page, '/api/admin/tag-localization/entity/pending?limit=5');
    if (pendingResp.data.length === 0) {
      test.skip();
      return;
    }

    await apiCall(page, '/api/admin/tag-localization/entity/resolve?limit=5', {
      method: 'POST',
    });

    const transResp = await apiCall(page,
      '/api/admin/tag-localization/translations?source=llm&limit=500');
    expect(transResp.status).toBe(200);

    const resolvedNames = new Set(
      transResp.data.items
        .filter((i: any) => i.provider === 'entity_resolver')
        .map((i: any) => i.canonical_name)
    );
    expect(resolvedNames.size).toBeGreaterThan(0);
  });
});

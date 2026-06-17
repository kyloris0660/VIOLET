import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('Phase 4.7-S2 browser validation contract', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('server identity and gallery smoke stay on the validation environment', async ({ page }) => {
    const identity = await apiCall(page, '/api/system/server-identity');
    expect(identity.status).toBe(200);
    expect(identity.data.app_name).toBe('V.I.O.L.E.T.');
    expect(identity.data.violet_env).toBe('test');
    expect(identity.data.db_name).toBe('blombooru_test');
    expect(identity.data.storage_root_explicitly_set).toBe(true);
    expect(identity.data.git_branch).toContain('phase47-s2');

    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#gallery-grid')).toBeVisible({ timeout: 15_000 });

    const bodyText = await page.locator('body').innerText();
    expect(bodyText).not.toMatch(/[A-Z]:\\Users\\/);
    expect(bodyText).not.toContain('\\\\192.168.');
    expect(bodyText).not.toContain('Z:\\');
  });

  test('tag localization and proper-noun safeguards are visible without LLM calls', async ({ page }) => {
    const stats = await apiCall(page, '/api/admin/tag-localization/stats');
    expect(stats.status).toBe(200);
    expect(typeof stats.data.total_tags).toBe('number');
    expect(typeof stats.data.missing).toBe('number');

    const entityStatus = await apiCall(page, '/api/admin/tag-localization/entity/status');
    expect(entityStatus.status).toBe(200);
    expect(typeof entityStatus.data.total_proper_noun_tags).toBe('number');
    expect(typeof entityStatus.data.needs_review).toBe('number');

    const workerStatus = await apiCall(page, '/api/admin/tag-localization/worker/status');
    expect(workerStatus.status).toBe(200);
    const categories = workerStatus.data.config?.categories || [];
    expect(categories).not.toContain('character');
    expect(categories).not.toContain('copyright');
    expect(categories).not.toContain('artist');

    const pending = await apiCall(page, '/api/admin/tag-localization/entity/pending?limit=25');
    expect(pending.status).toBe(200);
    expect(Array.isArray(pending.data)).toBe(true);
    for (const item of pending.data) {
      expect(['character', 'copyright', 'artist']).toContain(item.category);
      expect(typeof item.has_unreviewed_llm).toBe('boolean');
    }

    await page.goto('/admin?tab=content#tag-localization-section');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#tl-entity-resolve-btn')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('#tl-entity-refresh-btn')).toBeVisible();
    await expect(page.locator('#tl-entity-load-pending-btn')).toBeVisible();
  });
});

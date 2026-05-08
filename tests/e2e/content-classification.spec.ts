import { test, expect } from '@playwright/test';
import { loginAsAdmin, switchToTab, apiCall } from './helpers/auth';

test.describe('Content Classification — Smoke Tests', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await switchToTab(page, '内容');
  });

  test('content classification section visible', async ({ page }) => {
    await expect(
      page.locator('h3:has-text("内容分类"), h2:has-text("内容分类")').first()
    ).toBeVisible();
  });

  test('stats grid elements present', async ({ page }) => {
    for (const id of ['#cls-total', '#cls-classified', '#cls-unclassified', '#cls-locked']) {
      await expect(page.locator(id)).toBeAttached();
    }
  });

  test('config panel visible with refresh button', async ({ page }) => {
    await expect(page.locator('#cls-config-content')).toBeAttached();
    await expect(page.locator('#cls-refresh-config')).toBeVisible();
  });

  test('job creation form elements present', async ({ page }) => {
    await expect(page.locator('#cls-job-max-items')).toBeAttached();
    await expect(page.locator('#cls-job-create-btn')).toBeVisible();
  });

  test('job history section present', async ({ page }) => {
    await expect(page.locator('#cls-jobs-history-tbody')).toBeAttached();
    await expect(page.locator('#cls-jobs-refresh-history')).toBeVisible();
  });

  test('no undefined or object Object in classification section', async ({ page }) => {
    const section = await page.locator('#classification-section, [data-section="classification"]').first();
    if (await section.count() > 0) {
      const text = await section.innerText();
      expect(text).not.toContain('[object Object]');
      expect(text).not.toContain('undefined');
    }
  });

  test('breakdown container present', async ({ page }) => {
    await expect(page.locator('#cls-breakdown')).toBeAttached();
  });
});

test.describe('Content Classification — API Tests', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('GET /api/admin/content-classification/stats returns valid data', async ({ page }) => {
    const res = await apiCall(page, '/api/admin/content-classification/stats');
    expect(res.status).toBe(200);
    expect(res.data).toHaveProperty('total_media');
    expect(res.data).toHaveProperty('classified');
    expect(res.data).toHaveProperty('unclassified');
  });

  test('GET /api/admin/content-classification/config returns settings', async ({ page }) => {
    const res = await apiCall(page, '/api/admin/content-classification/config');
    expect(res.status).toBe(200);
    expect(res.data).toHaveProperty('enabled');
    expect(res.data).toHaveProperty('anime_tag_threshold');
    expect(res.data).toHaveProperty('anime_confidence_threshold');
  });

  test('GET /api/admin/content-classification/jobs returns list', async ({ page }) => {
    const res = await apiCall(page, '/api/admin/content-classification/jobs');
    expect(res.status).toBe(200);
    expect(Array.isArray(res.data)).toBe(true);
  });

  test('search with class:anime filter via API', async ({ page }) => {
    const res = await apiCall(page, '/api/search?q=class%3Aanime&limit=5');
    expect([200, 422]).toContain(res.status);
    if (res.status === 200) {
      expect(res.data).toHaveProperty('items');
    }
  });

  test('search with class:none filter via API', async ({ page }) => {
    const res = await apiCall(page, '/api/search?q=class%3Anone&limit=5');
    expect([200, 422]).toContain(res.status);
    if (res.status === 200) {
      expect(res.data).toHaveProperty('items');
    }
  });
});

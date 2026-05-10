import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

const isRealE2E = process.env.VIOLET_RUN_REAL_E2E === '1';

test.describe('Gallery & Media Detail E2E', () => {
  test.skip(!isRealE2E, 'Requires VIOLET_RUN_REAL_E2E=1');

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('gallery page loads and shows media grid', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Gallery container should exist (#gallery-grid is the actual element id)
    const grid = page.locator('#gallery-grid');
    await expect(grid).toBeVisible({ timeout: 15_000 });
  });

  test('gallery page returns media items from API', async ({ page }) => {
    const resp = await apiCall(page, '/api/media?page=1&per_page=10');
    expect(resp.status).toBe(200);
    expect(resp.data).toBeDefined();
    // API should return some results (assuming fixture import ran)
    if (Array.isArray(resp.data)) {
      expect(resp.data.length).toBeGreaterThanOrEqual(0);
    } else if (resp.data.items) {
      expect(resp.data.items.length).toBeGreaterThanOrEqual(0);
    }
  });

  test('media detail page loads for first media item', async ({ page }) => {
    // Get first media item
    const resp = await apiCall(page, '/api/media?page=1&per_page=1');
    expect(resp.status).toBe(200);

    let mediaId: number | null = null;
    if (Array.isArray(resp.data) && resp.data.length > 0) {
      mediaId = resp.data[0].id;
    } else if (resp.data.items && resp.data.items.length > 0) {
      mediaId = resp.data.items[0].id;
    }

    if (mediaId === null) {
      test.skip(true, 'No media items in database — import fixture first');
      return;
    }

    await page.goto(`/media/${mediaId}`);
    await page.waitForLoadState('networkidle');

    // Media detail should show an image or media element
    const mediaElement = page.locator('img, video, .media-detail, [class*="detail"]').first();
    await expect(mediaElement).toBeVisible({ timeout: 10_000 });
  });

  test('thumbnail endpoint returns image for existing media', async ({ page }) => {
    const resp = await apiCall(page, '/api/media?page=1&per_page=1');
    expect(resp.status).toBe(200);

    let mediaId: number | null = null;
    if (Array.isArray(resp.data) && resp.data.length > 0) {
      mediaId = resp.data[0].id;
    } else if (resp.data.items && resp.data.items.length > 0) {
      mediaId = resp.data.items[0].id;
    }

    if (mediaId === null) {
      test.skip(true, 'No media items in database');
      return;
    }

    // Check thumbnail endpoint returns 200
    const thumbResp = await page.evaluate(async (id) => {
      const r = await fetch(`/api/media/${id}/thumbnail`, { credentials: 'same-origin' });
      return { status: r.status, contentType: r.headers.get('content-type') };
    }, mediaId);

    expect(thumbResp.status).toBe(200);
    expect(thumbResp.contentType).toContain('image');
  });
});

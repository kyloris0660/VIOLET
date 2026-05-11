import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

const isRealE2E = process.env.VIOLET_RUN_REAL_E2E === '1';

test.describe('Gallery Content-Class Filter E2E', () => {
  test.skip(!isRealE2E, 'Requires VIOLET_RUN_REAL_E2E=1');

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('content-class filter is visible on gallery page', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const filter = page.locator('.content-class-filter-input').first();
    await expect(filter).toBeAttached({ timeout: 10_000 });
  });

  test('default selection is "all"', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const allRadio = page.locator('.content-class-filter-input[value="all"]').first();
    await expect(allRadio).toBeChecked();
  });

  test('API returns 200 for content_class=anime', async ({ page }) => {
    const resp = await apiCall(page, '/api/media?page=1&content_class=anime');
    expect(resp.status).toBe(200);
    expect(resp.data).toBeDefined();
    expect(resp.data.items).toBeDefined();
  });

  test('API returns 200 for content_class=non_anime', async ({ page }) => {
    const resp = await apiCall(page, '/api/media?page=1&content_class=non_anime');
    expect(resp.status).toBe(200);
    expect(resp.data.items).toBeDefined();
  });

  test('API returns 200 for content_class=unknown (includes NULL)', async ({ page }) => {
    const resp = await apiCall(page, '/api/media?page=1&content_class=unknown');
    expect(resp.status).toBe(200);
    expect(resp.data.items).toBeDefined();
  });

  test('API returns 200 for content_class=anime,unknown', async ({ page }) => {
    const resp = await apiCall(page, '/api/media?page=1&content_class=anime,unknown');
    expect(resp.status).toBe(200);
    expect(resp.data.items).toBeDefined();
  });

  test('API returns 400 for invalid content_class', async ({ page }) => {
    const resp = await apiCall(page, '/api/media?page=1&content_class=bogus');
    expect(resp.status).toBe(400);
    expect(resp.data.detail).toContain('bogus');
  });

  test('search API supports content_class parameter', async ({ page }) => {
    const resp = await apiCall(page, '/api/search?q=*&content_class=anime');
    expect(resp.status).toBe(200);
    expect(resp.data).toBeDefined();
  });

  test('clicking filter sends correct API request', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const animeLabel = page.locator('span.content-class-filter-label').filter({ hasText: /Anime Only|仅动漫/ });
    const count = await animeLabel.count();
    if (count > 0) {
      const reqPromise = page.waitForRequest(req =>
        req.url().includes('/api/') && req.url().includes('content_class=anime')
      );
      await animeLabel.last().click();
      const req = await reqPromise;
      expect(req.url()).toContain('content_class=anime');
    }
  });

  test('filter persists in localStorage', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const animeLabel = page.locator('span.content-class-filter-label').filter({ hasText: /Anime Only|仅动漫/ });
    if (await animeLabel.count() > 0) {
      await animeLabel.last().click();
      await page.waitForTimeout(500);

      const stored = await page.evaluate(() => localStorage.getItem('selectedContentClass'));
      expect(stored).toBe('anime');
    }
  });

  test('filter selection survives page reload', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.evaluate(() => localStorage.setItem('selectedContentClass', 'non_anime'));
    await page.reload();
    await page.waitForLoadState('networkidle');

    const nonAnimeRadio = page.locator('.content-class-filter-input[value="non_anime"]').first();
    if (await nonAnimeRadio.count() > 0) {
      await expect(nonAnimeRadio).toBeChecked();
    }
  });

  test('no console errors on gallery with filter', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    const jsErrors = errors.filter(e =>
      !e.includes('favicon') && !e.includes('404') && !e.includes('Failed to fetch')
    );
    expect(jsErrors).toEqual([]);
  });
});

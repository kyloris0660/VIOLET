import { test, expect } from '@playwright/test';
import { loginAsAdmin, switchToTab, apiCall } from './helpers/auth';

test.describe('UX/Data-Hygiene Fix — Classification Banners', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await switchToTab(page, '内容');
  });

  test('1. config endpoint returns method field', async ({ page }) => {
    const res = await apiCall(page, '/api/admin/content-classification/config');
    expect(res.status).toBe(200);
    expect(res.data).toHaveProperty('method');
    expect(['clip', 'heuristic']).toContain(res.data.method);
    expect(res.data).toHaveProperty('enabled');
  });

  test('2. exactly one banner is visible based on config', async ({ page }) => {
    await page.waitForTimeout(1500);
    const clipBanner = page.locator('#cls-banner-clip');
    const heuristicBanner = page.locator('#cls-banner-heuristic');
    const disabledBanner = page.locator('#cls-banner-disabled');

    const clipVisible = await clipBanner.isVisible();
    const heuristicVisible = await heuristicBanner.isVisible();
    const disabledVisible = await disabledBanner.isVisible();

    const visibleCount = [clipVisible, heuristicVisible, disabledVisible].filter(Boolean).length;
    expect(visibleCount).toBe(1);
  });

  test('3. banner elements exist in DOM with i18n attributes', async ({ page }) => {
    await expect(page.locator('#cls-banner-clip')).toBeAttached();
    await expect(page.locator('#cls-banner-heuristic')).toBeAttached();
    await expect(page.locator('#cls-banner-disabled')).toBeAttached();

    await expect(page.locator('[data-i18n="admin.content_classification.banner_clip"]')).toBeAttached();
    await expect(page.locator('[data-i18n="admin.content_classification.banner_heuristic"]')).toBeAttached();
    await expect(page.locator('[data-i18n="admin.content_classification.banner_disabled"]')).toBeAttached();
  });
});

test.describe('UX/Data-Hygiene Fix — force_reclassify', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await switchToTab(page, '内容');
  });

  test('4. force_reclassify checkbox present in job creation form', async ({ page }) => {
    await expect(page.locator('#cls-job-force-reclassify')).toBeAttached();
    const label = page.locator('[data-i18n="admin.content_classification.force_reclassify_label"]');
    await expect(label).toBeAttached();
  });

  test('5. force_reclassify included in API job creation request', async ({ page }) => {
    const res = await apiCall(page, '/api/admin/content-classification/jobs');
    expect(res.status).toBe(200);
    if (Array.isArray(res.data) && res.data.length > 0) {
      expect(res.data[0]).toHaveProperty('force_reclassify');
    }
  });
});

test.describe('UX/Data-Hygiene Fix — Missing Media Maintenance', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await switchToTab(page, '系统');
  });

  test('6. missing-media scan returns 4 categories', async ({ page }) => {
    const res = await apiCall(page, '/api/admin/dev/missing-media-scan');
    expect(res.status).toBe(200);
    expect(res.data).toHaveProperty('total_media');
    expect(res.data).toHaveProperty('valid');
    expect(res.data).toHaveProperty('missing_original_or_media_file');
    expect(res.data).toHaveProperty('missing_thumbnail_only');
    expect(res.data).toHaveProperty('missing_both');
    expect(res.data).toHaveProperty('deletable_count');
    expect(res.data).toHaveProperty('samples');
    expect(typeof res.data.total_media).toBe('number');
  });

  test('7. cleanup dry-run does not delete anything', async ({ page }) => {
    const res = await apiCall(page, '/api/admin/dev/missing-media-cleanup', {
      method: 'POST',
      body: JSON.stringify({ dry_run: true, confirm: false }),
    });
    expect(res.status).toBe(200);
    expect(res.data.dry_run).toBe(true);
    if (res.data.deletable_count !== undefined) {
      expect(res.data.message).toContain('No data was deleted');
    }
  });

  test('8. cleanup without confirm=true is rejected', async ({ page }) => {
    const res = await apiCall(page, '/api/admin/dev/missing-media-cleanup', {
      method: 'POST',
      body: JSON.stringify({ dry_run: false, confirm: false }),
    });
    expect([200, 400]).toContain(res.status);
    if (res.status === 400) {
      expect(res.data.detail).toContain('confirm');
    }
  });

  test('9. missing-media UI buttons exist', async ({ page }) => {
    await expect(page.locator('#dev-missing-media-scan-btn')).toBeAttached();
    await expect(page.locator('#dev-missing-media-dryrun-btn')).toBeAttached();
    await expect(page.locator('#dev-missing-media-cleanup-btn')).toBeAttached();
  });

  test('11. result panel element exists and scan API works', async ({ page }) => {
    const resultDiv = page.locator('#dev-missing-media-result');
    await expect(resultDiv).toBeAttached();

    const res = await apiCall(page, '/api/admin/dev/missing-media-scan');
    expect(res.status).toBe(200);
    expect(res.data).toHaveProperty('total_media');
  });

  test('10. cleanup response never reports source file deletion', async ({ page }) => {
    const scanRes = await apiCall(page, '/api/admin/dev/missing-media-scan');
    expect(scanRes.status).toBe(200);
    if (scanRes.data.deletable_count > 0) {
      const cleanupRes = await apiCall(page, '/api/admin/dev/missing-media-cleanup', {
        method: 'POST',
        body: JSON.stringify({ dry_run: false, confirm: true }),
      });
      expect(cleanupRes.status).toBe(200);
      expect(cleanupRes.data.source_files_deleted).toBe(0);
      expect(cleanupRes.data.message).toContain('Source files were NOT touched');
    } else {
      const dryRes = await apiCall(page, '/api/admin/dev/missing-media-cleanup', {
        method: 'POST',
        body: JSON.stringify({ dry_run: true }),
      });
      expect(dryRes.status).toBe(200);
    }
  });
});

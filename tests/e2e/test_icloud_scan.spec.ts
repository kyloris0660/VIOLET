import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall, switchToTab } from './helpers/auth';

test.describe('iCloud Safe Scan (Phase 2.4)', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('preflight endpoint returns stats without opening files', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/scan-local-library/preflight', {
      method: 'POST',
      body: JSON.stringify({
        paths: ['C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest'],
        max_files: 10,
        hydrated_only: true,
      }),
    });
    expect(resp.status).toBe(200);
    const data = resp.data;
    expect(data.job).toBeDefined();
    expect(data.job.is_preflight).toBe(true);
    expect(data.job.status).toBe('completed');
    expect(typeof data.estimated_size_bytes).toBe('number');
    expect(typeof data.largest_file_bytes).toBe('number');
    expect(typeof data.extensions).toBe('object');
  });

  test('scan job with hydrated_only passes through', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/scan-local-library/jobs', {
      method: 'POST',
      body: JSON.stringify({
        paths: ['C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest'],
        max_files: 3,
        dry_run: true,
        hydrated_only: true,
      }),
    });
    expect(resp.status).toBe(200);
    expect(resp.data.hydrated_only).toBe(true);
  });

  test('config diagnostics includes server and scan sections', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const data = resp.data;
    expect(data.server).toBeDefined();
    expect(typeof data.server.pid).toBe('number');
    expect(typeof data.server.python_version).toBe('string');
    expect(typeof data.server.app_version).toBe('string');
    expect(data.scan).toBeDefined();
    expect(typeof data.scan.hydrated_only_default).toBe('boolean');
    expect(typeof data.scan.file_open_timeout_seconds).toBe('number');
    expect(typeof data.scan.max_file_size_mb).toBe('number');
  });

  test('serialized job includes extended stat fields', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/scan-local-library/jobs', {
      method: 'POST',
      body: JSON.stringify({
        paths: ['C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest'],
        max_files: 2,
        dry_run: true,
      }),
    });
    expect(resp.status).toBe(200);
    const job = resp.data;
    expect(typeof job.skipped_cloud_placeholder).toBe('number');
    expect(typeof job.skipped_zero_byte).toBe('number');
    expect(typeof job.skipped_timeout).toBe('number');
    expect(typeof job.skipped_unreadable).toBe('number');
    expect(typeof job.skipped_hidden).toBe('number');
    expect(typeof job.skipped_too_large).toBe('number');
    expect(typeof job.hydrated_only).toBe('boolean');
    expect(typeof job.is_preflight).toBe('boolean');
  });

  test('admin UI shows preflight button and hydrated-only checkbox', async ({ page }) => {
    await switchToTab(page, '本地图库');
    await page.waitForTimeout(500);

    const hydratedCheckbox = page.locator('#local-scan-hydrated-only');
    await expect(hydratedCheckbox).toBeVisible();
    await expect(hydratedCheckbox).toBeChecked();

    const preflightBtn = page.locator('#local-scan-preflight-btn');
    await expect(preflightBtn).toBeVisible();
  });

  test('preflight button runs and shows results in UI', async ({ page }) => {
    await switchToTab(page, '本地图库');
    await page.waitForTimeout(500);

    const pathInput = page.locator('#local-scan-path');
    await pathInput.fill('C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest');

    const maxFiles = page.locator('#local-scan-max-files');
    await maxFiles.fill('5');

    const preflightBtn = page.locator('#local-scan-preflight-btn');
    await preflightBtn.click();

    await page.waitForTimeout(3000);

    const progress = page.locator('#local-scan-progress');
    await expect(progress).toBeVisible();

    const preflightBadge = page.locator('#local-scan-preflight-badge');
    await expect(preflightBadge).toBeVisible();
  });

  test('scan history shows preflight mode', async ({ page }) => {
    // First create a preflight job via API
    await apiCall(page, '/api/admin/scan-local-library/preflight', {
      method: 'POST',
      body: JSON.stringify({
        paths: ['C:\\Users\\kyloris\\Pictures\\AnimeLocalBooruTest'],
        max_files: 3,
      }),
    });

    await switchToTab(page, '本地图库');
    await page.waitForTimeout(1000);

    const historyTable = page.locator('#local-scan-history-tbody');
    const preflightRow = historyTable.locator('td:has-text("preflight")');
    await expect(preflightRow.first()).toBeVisible({ timeout: 5000 });
  });
});

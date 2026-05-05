import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('Reset E2E Test Data', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('dry-run returns summary without deleting', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/reset-e2e-test-data', {
      method: 'POST',
      body: JSON.stringify({
        source_path: 'C:\\Users\\kyloris\\Pictures\\VioletTest100_2',
        dry_run: true,
        confirm: false,
      }),
    });
    expect(resp.status).toBe(200);
    expect(resp.data.dry_run).toBe(true);
    expect(resp.data.summary).toBeDefined();
    expect(typeof resp.data.summary.media_count).toBe('number');
  });

  test('rejects empty path', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/reset-e2e-test-data', {
      method: 'POST',
      body: JSON.stringify({ source_path: '', dry_run: true }),
    });
    expect(resp.status).toBe(400);
  });

  test('rejects root path', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/reset-e2e-test-data', {
      method: 'POST',
      body: JSON.stringify({ source_path: 'C:\\', dry_run: true }),
    });
    expect(resp.status).toBe(400);
  });

  test('rejects data/ path', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/reset-e2e-test-data', {
      method: 'POST',
      body: JSON.stringify({ source_path: 'data/', dry_run: true }),
    });
    expect(resp.status).toBe(400);
  });

  test('rejects iCloud Photos path', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/reset-e2e-test-data', {
      method: 'POST',
      body: JSON.stringify({
        source_path: 'C:\\Users\\kyloris\\Pictures\\iCloud Photos',
        dry_run: true,
      }),
    });
    expect(resp.status).toBe(400);
  });
});

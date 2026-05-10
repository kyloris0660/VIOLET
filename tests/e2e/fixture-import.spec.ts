import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

const isRealE2E = process.env.VIOLET_RUN_REAL_E2E === '1';
const fixturePath = process.env.VIOLET_TEST_FIXTURE_PATH || '';

test.describe('Fixture Import E2E Workflow', () => {
  test.skip(!isRealE2E, 'Requires VIOLET_RUN_REAL_E2E=1');
  test.skip(!fixturePath, 'Requires VIOLET_TEST_FIXTURE_PATH');

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('config diagnostics returns valid structure', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    expect(resp.data.environment).toBeDefined();
    expect(resp.data.database).toBeDefined();
    expect(resp.data.storage).toBeDefined();
    expect(resp.data.server).toBeDefined();
  });

  test('preflight scan of anime subfolder returns file counts', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/scan-local-library/preflight', {
      method: 'POST',
      body: JSON.stringify({ paths: [fixturePath + '\\anime'] }),
    });
    expect(resp.status).toBe(200);
    expect(typeof resp.data.total_seen).toBe('number');
    expect(resp.data.total_seen).toBeGreaterThan(0);
    expect(typeof resp.data.processed).toBe('number');
  });

  test('dry-run scan reports what would be imported without persisting', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/scan-local-library', {
      method: 'POST',
      body: JSON.stringify({
        paths: [fixturePath + '\\anime'],
        max_files: 5,
        dry_run: true,
      }),
    });
    expect(resp.status).toBe(200);
    expect(resp.data.dry_run).toBe(true);
    expect(typeof resp.data.total_seen).toBe('number');
    // dry_run counts what *would* be imported — imported_media_ids should be empty
    expect(resp.data.imported_media_ids?.length ?? 0).toBe(0);
  });

  test('real import of anime subfolder succeeds', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/scan-local-library', {
      method: 'POST',
      body: JSON.stringify({
        paths: [fixturePath + '\\anime'],
        max_files: 5,
        dry_run: false,
      }),
    });
    expect(resp.status).toBe(200);
    expect(resp.data.dry_run).toBe(false);
    expect(typeof resp.data.imported).toBe('number');
    // Either imported new files or skipped duplicates (idempotent)
    const total = (resp.data.imported || 0) + (resp.data.skipped_duplicate || 0);
    expect(total).toBeGreaterThan(0);
  });

  test('duplicate import is idempotent', async ({ page }) => {
    // First import
    const resp1 = await apiCall(page, '/api/admin/scan-local-library', {
      method: 'POST',
      body: JSON.stringify({
        paths: [fixturePath + '\\anime'],
        max_files: 3,
        dry_run: false,
      }),
    });
    expect(resp1.status).toBe(200);

    // Second import — same files should be skipped as duplicates
    const resp2 = await apiCall(page, '/api/admin/scan-local-library', {
      method: 'POST',
      body: JSON.stringify({
        paths: [fixturePath + '\\anime'],
        max_files: 3,
        dry_run: false,
      }),
    });
    expect(resp2.status).toBe(200);
    expect(resp2.data.skipped_duplicate).toBeGreaterThan(0);
  });
});

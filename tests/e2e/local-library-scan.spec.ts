import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('Local Library Scan', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('dry-run scan returns preview', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/scan-local-library', {
      method: 'POST',
      body: JSON.stringify({
        path: 'C:\\Users\\kyloris\\Pictures\\VioletTest100_2',
        max_files: 5,
        dry_run: true,
      }),
    });
    expect(resp.status).toBe(200);
    expect(resp.data.dry_run).toBe(true);
    expect(typeof resp.data.total_seen).toBe('number');
    expect(typeof resp.data.processed).toBe('number');
  });
});

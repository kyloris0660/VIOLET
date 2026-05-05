import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('AI Tag Review', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('can load AI tag review suggestions', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/ai-tags/review?limit=10');
    expect(resp.status).toBe(200);
  });
});

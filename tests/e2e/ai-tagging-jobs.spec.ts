import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('AI Tagging Jobs', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('can list AI tagging jobs', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/ai-tagging/jobs');
    expect(resp.status).toBe(200);
  });

  test('can view auto tag config', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/ai-tagging/auto-config');
    expect(resp.status).toBe(200);
    expect(resp.data.ai_tagging_enabled).toBe(true);
    expect(resp.data.auto_tag_max_items).toBe(200);
  });
});

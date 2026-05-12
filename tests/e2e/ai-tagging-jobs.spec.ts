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
    expect(typeof resp.data.ai_tagging_enabled).toBe('boolean');
    expect(typeof resp.data.auto_tag_max_items).toBe('number');
    expect(resp.data.auto_tag_max_items).toBeGreaterThanOrEqual(1);
    expect(resp.data.auto_tag_max_items).toBeLessThanOrEqual(10000);
    expect(typeof resp.data.batch_max_items).toBe('number');
    expect(resp.data.batch_max_items).toBeGreaterThanOrEqual(1);
    expect(resp.data.batch_max_items).toBeLessThanOrEqual(10000);
  });
});

import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

function expectPositiveInteger(value: unknown) {
  expect(typeof value).toBe('number');
  expect(Number.isFinite(value)).toBe(true);
  expect(Number.isInteger(value)).toBe(true);
  expect(value as number).toBeGreaterThanOrEqual(1);
}

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
    expectPositiveInteger(resp.data.auto_tag_max_items);
    expectPositiveInteger(resp.data.batch_max_items);
  });
});

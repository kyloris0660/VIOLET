import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

function expectPositiveInteger(value: unknown) {
  expect(typeof value).toBe('number');
  expect(Number.isFinite(value)).toBe(true);
  expect(Number.isInteger(value)).toBe(true);
  expect(value as number).toBeGreaterThanOrEqual(1);
}

const REAL_LLM =
  process.env.VIOLET_RUN_REAL_LLM_E2E === '1' ||
  process.env.VIOLET_RUN_REAL_LLM_TESTS === '1';

test.describe('Tag Localization', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('LLM status shows available', async ({ page }) => {
    test.skip(!REAL_LLM, 'Skipped: set VIOLET_RUN_REAL_LLM_E2E=1 to run');
    const resp = await apiCall(page, '/api/admin/tag-localization/llm-status');
    expect(resp.status).toBe(200);
    expect(resp.data.enabled).toBe(true);
    expect(resp.data.available).toBe(true);
    expect(resp.data.api_key_configured).toBe(true);
    expectPositiveInteger(resp.data.batch_max_items);
    expectPositiveInteger(resp.data.auto_max_items);
  });

  test('translation stats are available', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/stats');
    expect(resp.status).toBe(200);
    expect(typeof resp.data.total_tags).toBe('number');
    expect(typeof resp.data.total_covered).toBe('number');
    expect(typeof resp.data.missing).toBe('number');
  });

  test('test LLM translation works', async ({ page }) => {
    test.skip(!REAL_LLM, 'Skipped: set VIOLET_RUN_REAL_LLM_E2E=1 to run');
    const resp = await apiCall(page, '/api/admin/tag-localization/test-llm', {
      method: 'POST',
    });
    expect(resp.status).toBe(200);
    expect(resp.data.success).toBe(true);
    expect(resp.data.result.display_name_zh).toBeTruthy();
  });

  test('batch translate dry-run returns candidates', async ({ page }) => {
    test.skip(!REAL_LLM, 'Skipped: set VIOLET_RUN_REAL_LLM_E2E=1 to run');
    const resp = await apiCall(page, '/api/admin/tag-localization/batch-translate', {
      method: 'POST',
      body: JSON.stringify({ dry_run: true, max_items: 5 }),
    });
    expect(resp.status).toBe(200);
    expect(resp.data.dry_run).toBe(true);
    if (resp.data.candidates > 0) {
      expect(resp.data.translated).toBeGreaterThan(0);
    }
  });

  test('LLM status does not expose API key', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/llm-status');
    const text = JSON.stringify(resp.data);
    expect(text).not.toMatch(/sk-[a-zA-Z0-9]+/);
  });
});

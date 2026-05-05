import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('Developer / E2E Tools Config Diagnostics', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('config diagnostics show correct values', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const d = resp.data;

    expect(d.ai_tagging.enabled).toBe(true);
    expect(d.ai_tagging.batch_max_items).toBe(200);
    expect(d.auto_tag_after_import.enabled).toBe(true);
    expect(d.auto_tag_after_import.max_items).toBe(200);
    expect(d.tag_localization.llm_enabled).toBe(true);
    expect(d.tag_localization.auto_enabled).toBe(true);
    expect(d.tag_localization.batch_max_items).toBe(200);
    expect(d.tag_localization.auto_max_items).toBe(200);
    expect(d.tag_localization.api_key_configured).toBe(true);
    expect(typeof d.tag_localization.background_enabled).toBe('boolean');
    expect(typeof d.tag_localization.background_interval).toBe('number');
    expect(typeof d.tag_localization.background_batch_size).toBe('number');
    expect(typeof d.tag_localization.background_max_per_run).toBe('number');
    expect(typeof d.tag_localization.background_daily_limit).toBe('number');
    expect(typeof d.tag_localization.background_error_limit).toBe('number');
    expect(typeof d.tag_localization.background_priority).toBe('string');
    expect(JSON.stringify(d.paths.local_library_paths)).toContain('VioletTest100');
  });

  test('config diagnostics do not expose actual API key value', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    const text = JSON.stringify(resp.data);
    expect(text).not.toMatch(/sk-[a-zA-Z0-9]{10,}/);
    expect(resp.data.tag_localization.api_key_configured).toBe(true);
  });
});

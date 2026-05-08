import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('Developer / E2E Tools Config Diagnostics', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('config diagnostics show correct structure and plausible values', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const d = resp.data;

    // AI tagging section — validate structure and plausible range
    expect(typeof d.ai_tagging.enabled).toBe('boolean');
    expect(d.ai_tagging.batch_max_items).toBeGreaterThan(0);
    expect(d.ai_tagging.batch_max_items).toBeLessThanOrEqual(1_000_000);

    // Auto-tag after import
    expect(typeof d.auto_tag_after_import.enabled).toBe('boolean');
    expect(d.auto_tag_after_import.max_items).toBeGreaterThan(0);
    expect(d.auto_tag_after_import.max_items).toBeLessThanOrEqual(1_000_000);

    // Tag localization
    expect(typeof d.tag_localization.llm_enabled).toBe('boolean');
    expect(typeof d.tag_localization.auto_enabled).toBe('boolean');
    expect(d.tag_localization.batch_max_items).toBeGreaterThan(0);
    expect(d.tag_localization.batch_max_items).toBeLessThanOrEqual(1_000_000);
    expect(d.tag_localization.auto_max_items).toBeGreaterThan(0);
    expect(d.tag_localization.auto_max_items).toBeLessThanOrEqual(1_000_000);
    expect(typeof d.tag_localization.api_key_configured).toBe('boolean');

    // Background worker settings
    expect(typeof d.tag_localization.background_enabled).toBe('boolean');
    expect(typeof d.tag_localization.background_interval).toBe('number');
    expect(typeof d.tag_localization.background_batch_size).toBe('number');
    expect(typeof d.tag_localization.background_max_per_run).toBe('number');
    expect(typeof d.tag_localization.background_daily_limit).toBe('number');
    expect(typeof d.tag_localization.background_error_limit).toBe('number');
    expect(typeof d.tag_localization.background_priority).toBe('string');

    // Paths section exists
    expect(d.paths).toBeDefined();
    expect(d.paths.local_library_paths).toBeDefined();
  });

  test('config diagnostics do not expose actual API key value', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    const text = JSON.stringify(resp.data);
    expect(text).not.toMatch(/sk-[a-zA-Z0-9]{10,}/);
    expect(resp.data.tag_localization.api_key_configured).toBe(true);
  });
});

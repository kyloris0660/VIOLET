import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

const isRealE2E = process.env.VIOLET_RUN_REAL_E2E === '1';

test.describe('Config Diagnostics E2E — Test Environment Verification', () => {
  test.skip(!isRealE2E, 'Requires VIOLET_RUN_REAL_E2E=1');

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('config diagnostics reports environment section', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const env = resp.data.environment;
    expect(env).toBeDefined();
    expect(typeof env.VIOLET_ENV).toBe('string');
    expect(typeof env.IS_TEST_ENV).toBe('boolean');
    expect(typeof env.IS_PRODUCTION_ENV).toBe('boolean');
  });

  test('config diagnostics reports database section', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const db = resp.data.database;
    expect(db).toBeDefined();
    expect(typeof db.DB_NAME).toBe('string');
    expect(typeof db.DB_HOST).toBe('string');
    expect(typeof db.DB_PORT).toBe('number');
    // DB name should not be a production name when in test env
    if (resp.data.environment.IS_TEST_ENV) {
      expect(db.DB_NAME).not.toBe('blombooru');
      expect(db.DB_NAME).not.toBe('production');
      expect(db.DB_NAME).not.toBe('postgres');
    }
  });

  test('config diagnostics reports storage section', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const storage = resp.data.storage;
    expect(storage).toBeDefined();
    expect(typeof storage.CODE_ROOT).toBe('string');
    expect(typeof storage.STORAGE_ROOT).toBe('string');
    expect(typeof storage.STORAGE_ROOT_EXPLICITLY_SET).toBe('boolean');
    expect(typeof storage.MEDIA_DIR).toBe('string');
  });

  test('config diagnostics reports destructive ops gate', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const gate = resp.data.destructive_ops;
    expect(gate).toBeDefined();
    expect(gate.conditions).toBeDefined();
    expect(typeof gate.gate_would_pass).toBe('boolean');
    expect(gate.values).toBeDefined();
    expect(typeof gate.values.VIOLET_ENV).toBe('string');
    expect(typeof gate.values.DB_NAME).toBe('string');
  });

  test('config diagnostics does not leak secrets', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const text = JSON.stringify(resp.data);
    // Should not contain raw API keys
    expect(text).not.toMatch(/sk-[a-zA-Z0-9]{20,}/);
    // Password fields should not appear in config diagnostics
    expect(text).not.toMatch(/"password"\s*:\s*"[^"]+"/);
  });

  test('config diagnostics reports server info', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const server = resp.data.server;
    expect(server).toBeDefined();
    expect(typeof server.pid).toBe('number');
    expect(typeof server.python_version).toBe('string');
    expect(typeof server.app_version).toBe('string');
    expect(typeof server.platform).toBe('string');
  });
});

import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('Tag Translation Worker - Smoke', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('worker status API returns valid structure', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/worker/status');
    expect(resp.status).toBe(200);
    expect(typeof resp.data.enabled).toBe('boolean');
    expect(typeof resp.data.status).toBe('string');
    expect(typeof resp.data.paused).toBe('boolean');
    expect(typeof resp.data.daily_limit).toBe('number');
    expect(typeof resp.data.missing_count).toBe('number');
    expect(typeof resp.data.processed_today).toBe('number');
    expect(resp.data.config).toBeDefined();
    expect(typeof resp.data.config.interval_seconds).toBe('number');
    expect(typeof resp.data.config.batch_size).toBe('number');
    expect(typeof resp.data.config.max_per_run).toBe('number');
    expect(typeof resp.data.config.error_limit).toBe('number');
    expect(typeof resp.data.config.priority).toBe('string');
  });

  test('worker status does not expose API key', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/worker/status');
    const text = JSON.stringify(resp.data);
    expect(text).not.toMatch(/sk-[a-zA-Z0-9]{10,}/);
  });

  test('worker jobs API returns valid structure', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/worker/jobs');
    expect(resp.status).toBe(200);
    expect(Array.isArray(resp.data.jobs)).toBe(true);
    expect(typeof resp.data.total).toBe('number');
  });

  test('pause and resume worker', async ({ page }) => {
    const pauseResp = await apiCall(page, '/api/admin/tag-localization/worker/pause', {
      method: 'POST',
    });
    expect(pauseResp.status).toBe(200);
    expect(pauseResp.data.paused).toBe(true);

    const statusAfterPause = await apiCall(page, '/api/admin/tag-localization/worker/status');
    expect(statusAfterPause.data.paused).toBe(true);

    const resumeResp = await apiCall(page, '/api/admin/tag-localization/worker/resume', {
      method: 'POST',
    });
    expect(resumeResp.status).toBe(200);
    expect(resumeResp.data.paused).toBe(false);

    const statusAfterResume = await apiCall(page, '/api/admin/tag-localization/worker/status');
    expect(statusAfterResume.data.paused).toBe(false);
  });

  test('worker status panel exists in admin UI', async ({ page }) => {
    await page.goto('http://localhost:8000/admin');
    await page.waitForLoadState('domcontentloaded');
    const section = page.locator('#tl-worker-section');
    await expect(section).toBeVisible();
    await expect(page.locator('#tl-worker-run-now-btn')).toBeVisible();
    await expect(page.locator('#tl-worker-pause-btn')).toBeVisible();
    await expect(page.locator('#tl-worker-refresh-btn')).toBeVisible();
  });

  test('config diagnostics include background worker settings', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/dev/config-diagnostics');
    expect(resp.status).toBe(200);
    const loc = resp.data.tag_localization;
    expect(typeof loc.background_enabled).toBe('boolean');
    expect(typeof loc.background_interval).toBe('number');
    expect(typeof loc.background_batch_size).toBe('number');
    expect(typeof loc.background_max_per_run).toBe('number');
    expect(typeof loc.background_daily_limit).toBe('number');
    expect(typeof loc.background_error_limit).toBe('number');
    expect(typeof loc.background_priority).toBe('string');
  });
});

const REAL_E2E = process.env.VIOLET_RUN_REAL_E2E === '1';

test.describe('Tag Translation Worker - Real E2E', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!REAL_E2E, 'Skipped: set VIOLET_RUN_REAL_E2E=1 to run');
    await loginAsAdmin(page);
  });

  test('run-now translates tags and updates stats', async ({ page }) => {
    test.setTimeout(180_000);

    const beforeStats = await apiCall(page, '/api/admin/tag-localization/stats');
    const beforeMissing = beforeStats.data.missing;
    const beforeLlm = beforeStats.data.source_breakdown?.llm || 0;

    if (beforeMissing === 0) {
      test.skip();
      return;
    }

    const runResp = await apiCall(page, '/api/admin/tag-localization/worker/run-now', {
      method: 'POST',
    });
    expect(runResp.status).toBe(200);

    let jobCompleted = false;
    for (let i = 0; i < 60; i++) {
      await page.waitForTimeout(3000);
      const status = await apiCall(page, '/api/admin/tag-localization/worker/status');
      if (!status.data.running && status.data.processed_today > 0) {
        jobCompleted = true;
        break;
      }
    }
    expect(jobCompleted).toBe(true);

    const afterStats = await apiCall(page, '/api/admin/tag-localization/stats');
    expect(afterStats.data.missing).toBeLessThan(beforeMissing);
    expect((afterStats.data.source_breakdown?.llm || 0)).toBeGreaterThan(beforeLlm);

    const jobsResp = await apiCall(page, '/api/admin/tag-localization/worker/jobs');
    expect(jobsResp.data.jobs.length).toBeGreaterThan(0);
    const latestJob = jobsResp.data.jobs[0];
    expect(latestJob.translated).toBeGreaterThan(0);
    expect(latestJob.status).toBe('completed');
  });

  test('second run-now continues translating remaining', async ({ page }) => {
    test.setTimeout(180_000);

    const beforeStatus = await apiCall(page, '/api/admin/tag-localization/worker/status');
    if (beforeStatus.data.missing_count === 0) {
      test.skip();
      return;
    }

    await apiCall(page, '/api/admin/tag-localization/worker/run-now', { method: 'POST' });

    let done = false;
    for (let i = 0; i < 60; i++) {
      await page.waitForTimeout(3000);
      const s = await apiCall(page, '/api/admin/tag-localization/worker/status');
      if (!s.data.running) {
        done = true;
        break;
      }
    }
    expect(done).toBe(true);

    const jobs = await apiCall(page, '/api/admin/tag-localization/worker/jobs');
    expect(jobs.data.jobs.length).toBeGreaterThanOrEqual(2);
  });

  test('pause prevents automatic runs', async ({ page }) => {
    await apiCall(page, '/api/admin/tag-localization/worker/pause', { method: 'POST' });
    const status = await apiCall(page, '/api/admin/tag-localization/worker/status');
    expect(status.data.paused).toBe(true);
    expect(status.data.status).toBe('paused');

    await apiCall(page, '/api/admin/tag-localization/worker/resume', { method: 'POST' });
    const resumed = await apiCall(page, '/api/admin/tag-localization/worker/status');
    expect(resumed.data.paused).toBe(false);
  });
});

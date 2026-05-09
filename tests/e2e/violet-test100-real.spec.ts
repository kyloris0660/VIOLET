import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

const REAL_E2E = process.env.VIOLET_RUN_REAL_E2E === '1';
const LIB_PATH = process.env.VIOLET_TEST_LIBRARY_PATH || 'C:\\Users\\kyloris\\Pictures\\VioletTest100_2';

test.describe('VioletTest100 Real E2E', () => {
  test.skip(!REAL_E2E, 'Set VIOLET_RUN_REAL_E2E=1 to run real E2E tests');

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('reset test data', async ({ page }) => {
    test.skip(
      !process.env.VIOLET_ALLOW_DESTRUCTIVE_E2E,
      'Destructive reset requires VIOLET_ALLOW_DESTRUCTIVE_E2E=1'
    );
    const resp = await apiCall(page, '/api/admin/dev/reset-e2e-test-data', {
      method: 'POST',
      body: JSON.stringify({
        source_path: LIB_PATH,
        dry_run: false,
        confirm: true,
        confirm_phrase: 'RESET_E2E_DATA',
      }),
    });
    expect(resp.status).toBe(200);
  });

  test('scan and import test library', async ({ page }) => {
    test.setTimeout(600_000);

    const createResp = await apiCall(page, '/api/admin/scan-local-library/jobs', {
      method: 'POST',
      body: JSON.stringify({ path: LIB_PATH, max_files: 200, dry_run: false }),
    });
    expect(createResp.status).toBe(200);
    const jobId = createResp.data.id;
    expect(jobId).toBeDefined();

    let status = 'pending';
    let jobData: any;
    for (let i = 0; i < 120; i++) {
      await page.waitForTimeout(5000);
      const poll = await apiCall(page, `/api/admin/scan-local-library/jobs/${jobId}`);
      if (poll.status !== 200) continue;
      jobData = poll.data;
      status = jobData.status;
      if (['completed', 'failed', 'cancelled'].includes(status)) break;
    }
    expect(status).toBe('completed');
    expect(jobData.imported).toBeGreaterThan(0);
  });

  test('verify auto AI tagging job completed', async ({ page }) => {
    test.setTimeout(1200_000);

    let aiJobId: number | undefined;
    for (let attempt = 0; attempt < 30; attempt++) {
      const resp = await apiCall(page, '/api/admin/ai-tagging/jobs');
      expect(resp.status).toBe(200);
      const jobs: any[] = resp.data.jobs || resp.data;
      const scanJobs = jobs
        .filter((j: any) => j.trigger_source === 'scan_job')
        .sort((a: any, b: any) => b.id - a.id);
      if (scanJobs.length > 0) {
        aiJobId = scanJobs[0].id;
        break;
      }
      await page.waitForTimeout(3000);
    }
    expect(aiJobId).toBeDefined();

    for (let i = 0; i < 240; i++) {
      const poll = await apiCall(page, `/api/admin/ai-tagging/jobs/${aiJobId}`);
      const st = poll.data.status;
      if (st === 'completed') {
        expect(poll.data.processed).toBeGreaterThan(20);
        return;
      }
      if (st === 'failed' || st === 'cancelled') {
        expect(st).toBe('completed');
        return;
      }
      await page.waitForTimeout(5000);
    }
    throw new Error('AI tagging job did not complete in time');
  });

  test('tag localization stats updated', async ({ page }) => {
    const resp = await apiCall(page, '/api/admin/tag-localization/stats');
    expect(resp.status).toBe(200);
    expect(resp.data.total_tags).toBeGreaterThan(0);
  });

  test('batch translate real run', async ({ page }) => {
    test.setTimeout(600_000);

    const before = await apiCall(page, '/api/admin/tag-localization/stats');

    const resp = await apiCall(page, '/api/admin/tag-localization/batch-translate', {
      method: 'POST',
      body: JSON.stringify({ dry_run: false, max_items: 200 }),
    });
    expect(resp.status).toBe(200);

    if (resp.data.candidates > 0) {
      expect(resp.data.translated).toBeGreaterThan(0);
    }

    const after = await apiCall(page, '/api/admin/tag-localization/stats');
    if (before.data.missing > 0 && resp.data.candidates > 0) {
      expect(after.data.total_covered).toBeGreaterThanOrEqual(before.data.total_covered);
    }
  });

  test('background worker run-now works', async ({ page }) => {
    test.setTimeout(180_000);

    const beforeStats = await apiCall(page, '/api/admin/tag-localization/stats');
    const beforeMissing = beforeStats.data.missing;

    if (beforeMissing === 0) {
      test.skip();
      return;
    }

    const runResp = await apiCall(page, '/api/admin/tag-localization/worker/run-now', {
      method: 'POST',
    });
    expect(runResp.status).toBe(200);

    for (let i = 0; i < 60; i++) {
      await page.waitForTimeout(3000);
      const s = await apiCall(page, '/api/admin/tag-localization/worker/status');
      if (!s.data.running) break;
    }

    const afterStats = await apiCall(page, '/api/admin/tag-localization/stats');
    if (beforeMissing > 0) {
      expect(afterStats.data.missing).toBeLessThan(beforeMissing);
    }
  });
});

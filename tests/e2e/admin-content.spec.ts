import { test, expect } from '@playwright/test';
import { loginAsAdmin, switchToTab } from './helpers/auth';

test.describe('Admin Content Tab', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await switchToTab(page, 'content');
  });

  async function openContentSection(page, id: string) {
    await page.locator(`#content-section-nav a[href="#${id}"]`).click();
    await expect(page.locator(`#${id}`)).toBeVisible();
  }

  test('content sections are reachable from left navigation', async ({ page }) => {
    const sections = [
      'media-management',
      'local-library-scan',
      'dynamic-library-sync-section',
      'ai-tag-review-section',
      'entity-metadata-section',
      'ai-tagging-jobs-section',
      'tag-localization-section',
      'tags-management-section',
      'tag-implications-section',
      'content-classification-section',
      'albums-management-section',
    ];

    await expect(page.locator('#content-section-nav')).toBeVisible();
    for (const id of sections) {
      await openContentSection(page, id);
    }
  });

  test('thumbnail buttons have visible text', async ({ page }) => {
    await openContentSection(page, 'media-management');
    const missingBtn = page.locator('#generate-missing-thumbnails-btn');
    const regenBtn = page.locator('#regenerate-all-thumbnails-btn');

    await expect(missingBtn).toBeVisible();
    await expect(regenBtn).toBeVisible();

    const missingText = (await missingBtn.innerText()).trim();
    const regenText = (await regenBtn.innerText()).trim();
    expect(missingText.length).toBeGreaterThan(0);
    expect(regenText.length).toBeGreaterThan(0);
  });

  test('upload area displays correctly', async ({ page }) => {
    await openContentSection(page, 'media-management');
    await expect(page.locator('#upload-area')).toBeVisible();
    await expect(page.locator('#file-input')).toBeAttached();
  });

  test('booru import form is visible', async ({ page }) => {
    await openContentSection(page, 'media-management');
    await expect(page.locator('#booru-url-input')).toBeVisible();
    await expect(page.locator('#booru-fetch-btn')).toBeVisible();
  });

  test('no undefined or [object Object] in page content', async ({ page }) => {
    const body = await page.locator('body').innerText();
    expect(body).not.toContain('[object Object]');
  });

  test('dynamic library sync panel exposes default-off controls', async ({ page }) => {
    await openContentSection(page, 'dynamic-library-sync-section');
    await expect(page.locator('#dynamic-sync-pending-new')).toBeVisible();
    await expect(page.locator('#dynamic-sync-threshold')).toHaveText(/100|\d+/);
    await expect(page.locator('#dynamic-sync-start-btn')).toBeVisible();
    await expect(page.locator('#dynamic-sync-progress')).toBeAttached();
    await expect(page.locator('#dynamic-sync-advanced-controls')).toBeVisible();
    await expect(page.locator('#dynamic-sync-check-btn')).toBeHidden();
    await page.locator('#dynamic-sync-advanced-controls summary').click();
    await expect(page.locator('#dynamic-sync-check-btn')).toBeVisible();
    await expect(page.locator('#dynamic-sync-dry-run-btn')).toBeVisible();
    await expect(page.locator('#dynamic-sync-ai-localization')).toBeVisible();
  });

  test('dynamic library sync normal path shows staged operator workflow', async ({ page }) => {
    await openContentSection(page, 'dynamic-library-sync-section');
    await expect(page.locator('#dynamic-sync-start-btn')).toBeVisible();
    await expect(page.locator('#dynamic-sync-confirm-actions')).toBeHidden();
    await expect(page.locator('#dynamic-sync-stage-strip')).toBeAttached();

    const progressText = await page.locator('#dynamic-sync-progress').innerText();
    for (const label of ['Plan', 'Import', 'Classification', 'AI tagging', 'Localization', 'Complete']) {
      expect(progressText).toContain(label);
    }

    const advancedText = await page.locator('#dynamic-sync-advanced-controls').innerText();
    expect(advancedText).toMatch(/Advanced \/ Diagnostics|高级 \/ 诊断控件/);
    await page.locator('#dynamic-sync-advanced-controls summary').click();
    await expect(page.locator('#dynamic-sync-confirm-actions')).toContainText('Advanced/diagnostic execute control');
  });

  test('dynamic library sync normal confirmation starts full pipeline without advanced phrase', async ({ page }) => {
    const guiSession = {
      gui_validation_session_id: 'gui-e2e-session',
      gui_validation_session_token: 'gui-e2e-token',
      client_route: '/admin?tab=content#dynamic-library-sync-section',
    };
    const plan = {
      plan_request_id: 'gui-plan-e2e',
      job: { created_at: '2026-06-30T00:00:00+00:00' },
      source: { plan_source: 'source_delta' },
      counts: {
        total_seen: 2,
        plan_items: 2,
        estimated_import_count: 2,
        estimated_downstream_followup_count: 0,
        partial_scan: false,
        batch_executable: true,
        state_counts: { import_planned: 2 },
      },
      limits: {
        max_files: 100,
        hydrated_only: false,
        plan_mode: 'incremental',
        batch_executable: true,
        cap_semantics: 'unique_importable_or_downstream_followup_candidates_not_unchanged_or_existing_media',
        hydration_policy: 'cloud_aware_non_destructive_read',
        continuation: { more_batches_remain: false },
        source_delta_workset: {
          scan_order: 'source-ledger-followup-then-filesystem-metadata',
          incremental_source_ledger_used: true,
          fast_skip_identity: ['root_id', 'relative_path_hash', 'size', 'mtime_ns'],
          filesystem_walk_completed: true,
        },
        root_scan_state: {
          model: 'incremental_watermark_metadata_candidate_discovery',
          current_scan_start_basis: 'mtime watermark + safety lookback + ledger follow-up',
        },
      },
      integrity: {
        plan_hash: '44beb57e2770e2eplan',
        confirmation_phrase: 'I APPROVE S3A-M2 DEV MANUAL SYNC EXECUTE 44beb57e2770',
        operator_confirmation_statement: 'I understand this will import, classify, AI-tag, localize, and report 2 items.',
        expires_at: '2026-06-30T01:00:00+00:00',
      },
      gui_provenance: guiSession,
    };
    let executeBody: any = null;

    await page.route('**/api/admin/dynamic-library-sync', async route => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          pending_summary: {
            pending_new: 2,
            pending_changed: 0,
            pending_deferred: 4658,
            pending_deferred_includes_historical: true,
            threshold: 100,
            threshold_reached: true,
          },
          readiness: {
            production_settings: { violet_env: 'test' },
            warnings: [],
            manual_sync_operator_readiness: {
              manual_execute_ready: true,
              manual_execute_blockers: [],
              manual_execute_warnings: [],
              background_warnings: [
                { code: 'background_workers_off', label: 'Background workers are OFF; expected for manual-only sync.' },
              ],
            },
            ai_localization_readiness: {},
          },
          source_roots: [{ id: 2, label: 'icloud-photos-production', root_path_hash: '153684ac', is_active: true }],
          default_off_policy: {
            manual_sync_execution_enabled: true,
            manual_sync_execute_enabled: true,
            automatic_production_writes_enabled: false,
            manual_execute_max_files_cap: 1000,
            manual_execute_default_max_files: 100,
          },
          runtime_provenance: { violet_env: 'test', db_name: 'blombooru_test', git_head: 'e2e' },
          last_sync_run: null,
        }),
      });
    });
    await page.route('**/api/admin/dynamic-library-sync/manual-sync/jobs/latest', async route => {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({}) });
    });
    await page.route('**/api/admin/dynamic-library-sync/manual-sync/gui-session', async route => {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(guiSession) });
    });
    await page.route('**/api/admin/dynamic-library-sync/manual-sync/plan-progress/**', async route => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ plan_request_id: 'gui-plan-e2e', status: 'completed', phase: 'completed', counts: {} }),
      });
    });
    await page.route('**/api/admin/dynamic-library-sync/manual-sync/plan', async route => {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(plan) });
    });
    await page.route('**/api/admin/dynamic-library-sync/manual-sync/execute', async route => {
      executeBody = JSON.parse(route.request().postData() || '{}');
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          id: 42,
          status: 'running',
          run_type: 'manual_sync_execute',
          total_seen: 2,
          new_items: 0,
          failed_items: 0,
          summary_json: {
            manual_sync_execute: {
              status: 'running',
              current_stage: 'import',
              stage_rows: [
                { name: 'candidate_discovery', status: 'completed', processed: 2, failed: 0 },
                { name: 'import', status: 'running', processed: 0, failed: 0 },
              ],
              outcome_counts: {},
            },
          },
        }),
      });
    });
    await page.route('**/api/admin/dynamic-library-sync/manual-sync/jobs/42', async route => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ id: 42, status: 'completed', total_seen: 2, new_items: 2, failed_items: 0, summary_json: {} }),
      });
    });

    await page.reload();
    await switchToTab(page, 'content');
    await openContentSection(page, 'dynamic-library-sync-section');

    page.once('dialog', async dialog => {
      expect(dialog.message()).toContain('2');
      await dialog.accept();
    });
    await page.locator('#dynamic-sync-start-btn').click();

    await expect.poll(() => executeBody, { timeout: 10_000 }).not.toBeNull();
    expect(executeBody.confirmation_phrase).toBe('');
    expect(executeBody.operator_confirmation_statement).toContain('import, classify, AI-tag');
    expect(executeBody.gui_validation_session_id).toBe(guiSession.gui_validation_session_id);
    expect(executeBody.gui_validation_session_token).toBe(guiSession.gui_validation_session_token);
    expect(executeBody.client_route).toBe('/admin?tab=content#dynamic-library-sync-section');
    await expect(page.locator('#dynamic-sync-confirm-actions')).toBeHidden();
    await expect(page.locator('#dynamic-sync-progress-label')).toContainText(/Running job #42|Starting full manual sync/);
  });
});

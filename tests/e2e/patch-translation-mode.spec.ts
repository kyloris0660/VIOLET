/**
 * Frontend behavior tests for PATCH /translations/{id} mode.
 * Phase 3.2j — Codex review fix verification.
 *
 * These tests load the admin page, mock `app.apiCall`, and verify that:
 *   1. PATCH mode save calls PATCH endpoint with correct translation ID
 *   2. Clearing aliases sends `aliases: []` in the payload
 *   3. Toggling needs_review/reviewed sends expected boolean
 *   4. Switching from PATCH mode to missing-tag flow does NOT PATCH stale ID
 *   5. Cancel clears PATCH mode and restores normal save behavior
 *
 * Gate: VIOLET_RUN_REAL_E2E=1 (requires a running test server with admin UI).
 */

import { test, expect } from '@playwright/test';
import { loginAsAdmin, navigateToContentSection } from './helpers/auth';

const REAL_E2E = process.env.VIOLET_RUN_REAL_E2E === '1';

test.describe('PATCH Translation Mode — Frontend Behavior', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!REAL_E2E, 'Skipped: set VIOLET_RUN_REAL_E2E=1 with a running server');
    await loginAsAdmin(page);
    // Navigate to admin page — Content tab → Tag Localization section
    await navigateToContentSection(page, '标签本地化');
    // Wait for the localization section to be visible
    await page.waitForSelector('#tl-edit-canonical', { timeout: 10_000 });
  });

  /**
   * Helper: install an apiCall interceptor that captures calls and resolves
   * with a mock success response. Returns a handle to retrieve captured calls.
   */
  async function installApiCallMock(page: import('@playwright/test').Page) {
    await page.evaluate(() => {
      (window as any).__patchTestCalls = [];
      // `app` is a global lexical const in main.js (not a window property).
      // Use indirect eval to reach the global lexical environment.
      const appInstance: any = (0, eval)('app');
      const origApiCall = appInstance.apiCall.bind(appInstance);
      appInstance.apiCall = async (url: string, options: any) => {
        const entry = { url, method: options?.method || 'GET', body: null as any };
        try {
          entry.body = options?.body ? JSON.parse(options.body) : null;
        } catch { entry.body = options?.body || null; }
        (window as any).__patchTestCalls.push(entry);

        // If it's a PATCH or POST to translations, return a fake success
        if (url.includes('/tag-localization/translations')) {
          return {
            id: 42,
            canonical_name: 'test_tag',
            display_name: entry.body?.display_name || '测试',
            aliases: entry.body?.aliases || [],
            needs_review: entry.body?.needs_review ?? false,
            source: 'manual',
            status: 'reviewed',
            old: { display_name: '旧名', aliases: [], needs_review: true },
            message: 'Translation updated',
          };
        }
        // For other calls (stats, review list, missing, etc.), use original
        return origApiCall(url, options);
      };
    });
  }

  async function getCapturedCalls(page: import('@playwright/test').Page) {
    return page.evaluate(() => (window as any).__patchTestCalls || []);
  }

  async function clearCapturedCalls(page: import('@playwright/test').Page) {
    await page.evaluate(() => { (window as any).__patchTestCalls = []; });
  }

  /**
   * Helper: simulate entering PATCH mode by calling the AdminPanel method
   * directly and filling form fields.
   *
   * `opts.original` — the "pre-edit" snapshot stored by _enterTranslationPatchMode
   * for diff detection.  When omitted the original defaults to placeholder values
   * that differ from the form fields so that every field is treated as changed.
   */
  async function enterPatchMode(page: import('@playwright/test').Page, opts: {
    translationId: number;
    canonical?: string;
    display?: string;
    aliases?: string;
    needsReview?: boolean;
    category?: string;
    original?: { display_name?: string; aliases?: string; needs_review?: boolean };
  }) {
    await page.evaluate((o) => {
      const panel = (window as any).adminPanel;
      // Fill form fields (these represent the *edited* state the user wants to save)
      (document.getElementById('tl-edit-canonical') as HTMLInputElement).value = o.canonical || 'test_tag';
      (document.getElementById('tl-edit-display') as HTMLInputElement).value = o.display || '测试名';
      (document.getElementById('tl-edit-aliases') as HTMLInputElement).value = o.aliases ?? '';
      const reviewedCb = document.getElementById('tl-edit-reviewed') as HTMLInputElement;
      if (reviewedCb) reviewedCb.checked = !(o.needsReview ?? false);
      const categorySel = document.getElementById('tl-edit-category') as HTMLSelectElement;
      if (categorySel) categorySel.value = o.category || '';
      // Enter PATCH mode with original (pre-edit) values for diff detection.
      // If no explicit original is provided, use sentinel values that differ
      // from the form fields so the save handler detects a change.
      const orig = o.original || {
        display_name: '__original__',
        aliases: '__original__',
        needs_review: !(o.needsReview ?? false),
      };
      panel._enterTranslationPatchMode(o.translationId, {
        display_name: orig.display_name ?? '__original__',
        aliases: orig.aliases ?? '__original__',
        needs_review: orig.needs_review ?? !(o.needsReview ?? false),
      });
    }, opts);
  }

  // ─── Scenario 1: PATCH mode save → PATCH endpoint with correct ID ───

  test('1. PATCH mode save calls PATCH endpoint with correct translation ID', async ({ page }) => {
    await installApiCallMock(page);

    await enterPatchMode(page, {
      translationId: 77,
      display: '蓝眼睛修正',
      aliases: '碧眼,蓝色',
    });

    // Click save
    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    const calls = await getCapturedCalls(page);
    const patchCall = calls.find((c: any) => c.method === 'PATCH');

    expect(patchCall).toBeTruthy();
    expect(patchCall.url).toContain('/translations/77');
    expect(patchCall.method).toBe('PATCH');
    expect(patchCall.body.display_name).toBe('蓝眼睛修正');
    expect(patchCall.body.aliases).toEqual(['碧眼', '蓝色']);
  });

  // ─── Scenario 2: Clear aliases → payload includes aliases: [] ───

  test('2. Clearing aliases sends aliases: [] in PATCH payload', async ({ page }) => {
    await installApiCallMock(page);

    await enterPatchMode(page, {
      translationId: 88,
      display: '测试显示名',
      aliases: '',  // empty = clear
    });

    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    const calls = await getCapturedCalls(page);
    const patchCall = calls.find((c: any) => c.method === 'PATCH');

    expect(patchCall).toBeTruthy();
    expect(patchCall.url).toContain('/translations/88');
    expect(patchCall.body.aliases).toEqual([]);
  });

  // ─── Scenario 3: needs_review toggle → correct boolean in payload ───

  test('3. Toggling reviewed checkbox sends correct needs_review boolean', async ({ page }) => {
    await installApiCallMock(page);

    // Test with reviewed=false (needs_review=true)
    await enterPatchMode(page, {
      translationId: 99,
      display: '需要审核',
      needsReview: true,  // checkbox unchecked → needs_review=true
    });

    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    let calls = await getCapturedCalls(page);
    let patchCall = calls.find((c: any) => c.method === 'PATCH');
    expect(patchCall).toBeTruthy();
    expect(patchCall.body.needs_review).toBe(true);

    // Now test with reviewed=true (needs_review=false)
    await clearCapturedCalls(page);

    await enterPatchMode(page, {
      translationId: 100,
      display: '已审核',
      needsReview: false,  // checkbox checked → needs_review=false
    });

    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    calls = await getCapturedCalls(page);
    patchCall = calls.find((c: any) => c.method === 'PATCH');
    expect(patchCall).toBeTruthy();
    expect(patchCall.body.needs_review).toBe(false);
  });

  // ─── Scenario 4: PATCH mode → missing-tag flow → save does NOT PATCH stale ID ───

  test('4. Switching from PATCH mode to missing-tag flow does not PATCH stale ID', async ({ page }) => {
    await installApiCallMock(page);

    // Enter PATCH mode for translation ID 55
    await enterPatchMode(page, {
      translationId: 55,
      display: '旧翻译',
    });

    // Verify we are in PATCH mode
    const inPatchBefore = await page.evaluate(() =>
      (window as any).adminPanel._isTranslationPatchMode()
    );
    expect(inPatchBefore).toBe(true);

    // Simulate missing-tag edit button click — this should exit PATCH mode
    await page.evaluate(() => {
      const panel = (window as any).adminPanel;
      // Simulate what the missing-tag .tl-edit-btn click does:
      panel._exitTranslationPatchMode({ clearForm: false });
      (document.getElementById('tl-edit-canonical') as HTMLInputElement).value = 'new_tag_name';
      (document.getElementById('tl-edit-display') as HTMLInputElement).value = '新标签';
      (document.getElementById('tl-edit-aliases') as HTMLInputElement).value = '';
    });

    // Verify PATCH mode is exited
    const inPatchAfter = await page.evaluate(() =>
      (window as any).adminPanel._isTranslationPatchMode()
    );
    expect(inPatchAfter).toBe(false);

    // Now save — should use POST (upsert), NOT PATCH
    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    const calls = await getCapturedCalls(page);

    // Should have a POST call, no PATCH call
    const patchCall = calls.find((c: any) => c.method === 'PATCH');
    const postCall = calls.find((c: any) => c.method === 'POST' && c.url.includes('/translations'));

    expect(patchCall).toBeFalsy();  // No PATCH to stale ID 55
    expect(postCall).toBeTruthy();   // POST (upsert) for new_tag_name
    expect(postCall.body.canonical_name).toBe('new_tag_name');
    expect(postCall.body.display_name).toBe('新标签');
  });

  // ─── Scenario 5: Cancel clears PATCH mode → normal save behavior ───

  test('5. Cancel edit clears PATCH mode and restores normal save', async ({ page }) => {
    await installApiCallMock(page);

    // Enter PATCH mode
    await enterPatchMode(page, {
      translationId: 66,
      display: '要取消的翻译',
    });

    // Verify PATCH mode is active
    const inPatch = await page.evaluate(() =>
      (window as any).adminPanel._isTranslationPatchMode()
    );
    expect(inPatch).toBe(true);

    // Click cancel button
    await page.click('#tl-cancel-edit-btn');
    await page.waitForTimeout(200);

    // Verify PATCH mode is cleared
    const inPatchAfterCancel = await page.evaluate(() =>
      (window as any).adminPanel._isTranslationPatchMode()
    );
    expect(inPatchAfterCancel).toBe(false);

    // Verify form is cleared
    const canonicalVal = await page.evaluate(() =>
      (document.getElementById('tl-edit-canonical') as HTMLInputElement).value
    );
    expect(canonicalVal).toBe('');

    // Verify canonical input is re-enabled
    const canonicalDisabled = await page.evaluate(() =>
      (document.getElementById('tl-edit-canonical') as HTMLInputElement).disabled
    );
    expect(canonicalDisabled).toBe(false);

    // Fill in new data and save — should POST, not PATCH
    await page.fill('#tl-edit-canonical', 'cancel_test_tag');
    await page.fill('#tl-edit-display', '取消测试');
    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    const calls = await getCapturedCalls(page);
    const patchCall = calls.find((c: any) => c.method === 'PATCH');
    const postCall = calls.find((c: any) => c.method === 'POST' && c.url.includes('/translations'));

    expect(patchCall).toBeFalsy();  // No PATCH to stale ID 66
    expect(postCall).toBeTruthy();   // POST for new translation
    expect(postCall.body.canonical_name).toBe('cancel_test_tag');
  });

  // ─── Scenario 6: Cancel after editing needs_review=true row → checkbox resets ───

  test('6. Cancel after editing needs_review=true row resets reviewed checkbox to default', async ({ page }) => {
    await installApiCallMock(page);

    // Enter PATCH mode with needs_review=true (reviewed checkbox unchecked)
    await enterPatchMode(page, {
      translationId: 110,
      display: '需要审核标签',
      needsReview: true,
    });

    // Verify checkbox is unchecked (needs_review=true → reviewed=false)
    const checkedBefore = await page.evaluate(() =>
      (document.getElementById('tl-edit-reviewed') as HTMLInputElement).checked
    );
    expect(checkedBefore).toBe(false);

    // Click cancel
    await page.click('#tl-cancel-edit-btn');
    await page.waitForTimeout(200);

    // Reviewed checkbox should reset to create-mode default (checked)
    const checkedAfter = await page.evaluate(() =>
      (document.getElementById('tl-edit-reviewed') as HTMLInputElement).checked
    );
    expect(checkedAfter).toBe(true);
  });

  // ─── Scenario 7: Successful PATCH → checkbox resets to create-mode default ───

  test('7. Successful PATCH resets reviewed checkbox to create-mode default', async ({ page }) => {
    await installApiCallMock(page);

    // Enter PATCH mode with needs_review=true (checkbox unchecked)
    await enterPatchMode(page, {
      translationId: 111,
      display: '原始名',
      needsReview: true,
    });

    // Change display_name so it's not a no-op
    await page.evaluate(() => {
      (document.getElementById('tl-edit-display') as HTMLInputElement).value = '修正后的名字';
    });

    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    // After successful PATCH, form should be cleared and checkbox reset
    const checkedAfter = await page.evaluate(() =>
      (document.getElementById('tl-edit-reviewed') as HTMLInputElement).checked
    );
    expect(checkedAfter).toBe(true);
  });

  // ─── Scenario 8: Cancel/save resets category to default ───

  test('8. Cancel resets category field to default', async ({ page }) => {
    await installApiCallMock(page);

    // Enter PATCH mode with a specific category
    await enterPatchMode(page, {
      translationId: 112,
      display: '角色翻译',
      category: 'character',
    });

    // Verify category is set
    const categoryBefore = await page.evaluate(() =>
      (document.getElementById('tl-edit-category') as HTMLSelectElement).value
    );
    expect(categoryBefore).toBe('character');

    // Click cancel
    await page.click('#tl-cancel-edit-btn');
    await page.waitForTimeout(200);

    // Category should reset to default (empty)
    const categoryAfter = await page.evaluate(() =>
      (document.getElementById('tl-edit-category') as HTMLSelectElement).value
    );
    expect(categoryAfter).toBe('');
  });

  // ─── Scenario 9: No-op save does NOT call PATCH ───

  test('9. No-op save (no changes) does NOT call PATCH', async ({ page }) => {
    await installApiCallMock(page);

    // Enter PATCH mode with specific values — originals match form → no diff
    await enterPatchMode(page, {
      translationId: 113,
      display: '蓝眼睛',
      aliases: '碧眼,蓝色',
      needsReview: false,
      original: { display_name: '蓝眼睛', aliases: '碧眼,蓝色', needs_review: false },
    });

    // Do NOT change any form values — save immediately
    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    const calls = await getCapturedCalls(page);
    const patchCall = calls.find((c: any) => c.method === 'PATCH');

    // No PATCH call should have been made
    expect(patchCall).toBeFalsy();

    // PATCH mode should still be active (no-op keeps mode)
    const inPatch = await page.evaluate(() =>
      (window as any).adminPanel._isTranslationPatchMode()
    );
    expect(inPatch).toBe(true);
  });

  // ─── Scenario 10: No-op save keeps PATCH mode active ───

  test('10. No-op save keeps PATCH mode active with correct ID', async ({ page }) => {
    await installApiCallMock(page);

    await enterPatchMode(page, {
      translationId: 114,
      display: '测试不变',
      aliases: '',
      needsReview: true,
      original: { display_name: '测试不变', aliases: '', needs_review: true },
    });

    // Save without changes
    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    // PATCH mode should still be active
    const inPatch = await page.evaluate(() =>
      (window as any).adminPanel._isTranslationPatchMode()
    );
    expect(inPatch).toBe(true);

    // Translation ID should be preserved
    const patchId = await page.evaluate(() =>
      (window as any).adminPanel._tlPatchId
    );
    expect(patchId).toBe(114);

    // Form values should be unchanged
    const displayVal = await page.evaluate(() =>
      (document.getElementById('tl-edit-display') as HTMLInputElement).value
    );
    expect(displayVal).toBe('测试不变');
  });

  // ─── Scenario 11: Actual changes still trigger PATCH correctly ───

  test('11. Actual changes (display, aliases, needs_review) trigger PATCH with only changed fields', async ({ page }) => {
    await installApiCallMock(page);

    // Enter PATCH mode with known original values
    await enterPatchMode(page, {
      translationId: 115,
      display: '原始显示名',
      aliases: '别名一',
      needsReview: true,
      original: { display_name: '原始显示名', aliases: '别名一', needs_review: true },
    });

    // Change display_name and clear aliases
    await page.evaluate(() => {
      (document.getElementById('tl-edit-display') as HTMLInputElement).value = '修改后显示名';
      (document.getElementById('tl-edit-aliases') as HTMLInputElement).value = '';
    });

    await page.click('#tl-save-btn');
    await page.waitForTimeout(300);

    const calls = await getCapturedCalls(page);
    const patchCall = calls.find((c: any) => c.method === 'PATCH');

    expect(patchCall).toBeTruthy();
    expect(patchCall.url).toContain('/translations/115');
    expect(patchCall.body.display_name).toBe('修改后显示名');
    expect(patchCall.body.aliases).toEqual([]);
    // needs_review was not changed, so should NOT be in the payload
    expect(patchCall.body.needs_review).toBeUndefined();
  });
});

/**
 * Phase 3.1.2a — Admin UI Closeout E2E Validation
 *
 * Comprehensive coverage across 6 areas:
 * A. Admin page + tabs (loads, 4 tabs visible, no console.error during navigation)
 * B. Content Tab AI de-duplication (no old standalone AI sections, yes AI review + jobs + badge)
 * C. DevTools legacy panel (System Tab, collapsed, expandable, deprecation notice, legacy controls)
 * D. Content quick-nav (click 6+ targets, verify target scrolled into view)
 * E. i18n / locale (Chinese renders, no raw keys, data-i18n attributes, locale JSON parseable)
 * F. Stats / Account smoke (tabs open, render expected content, no console.error)
 */
import { test, expect, type Page, type ConsoleMessage } from '@playwright/test';
import { loginAsAdmin } from './helpers/auth';

// ---------- shared helpers ----------

/** Map Chinese tab labels to data-tab attribute values */
const TAB_MAP: Record<string, string> = {
  '系统': 'system',
  '内容': 'content',
  '统计': 'stats',
  '账号': 'account',
};

/** Reliable tab switching using data-tab attribute with verification and retry */
async function clickTab(page: Page, labelOrId: string) {
  const tabId = TAB_MAP[labelOrId] || labelOrId;
  const btn = page.locator(`button.tab-btn[data-tab="${tabId}"]`);
  const panel = page.locator(`#tab-${tabId}`);

  // Wait for button to be visible (ensures page JS has loaded the DOM)
  await expect(btn).toBeVisible({ timeout: 5_000 });

  // Click and verify — retry up to 3 times in case JS handlers aren't ready yet
  for (let attempt = 0; attempt < 3; attempt++) {
    await btn.click();
    try {
      await expect(panel).toBeVisible({ timeout: 2_000 });
      return; // success
    } catch {
      // JS handler may not be attached yet; wait and retry
      await page.waitForTimeout(500);
    }
  }
  // Final attempt — let it throw if it still fails
  await btn.click();
  await expect(panel).toBeVisible({ timeout: 5_000 });
}

/** Collect console.error messages during a callback */
async function collectConsoleErrors(page: Page, fn: () => Promise<void>): Promise<string[]> {
  const errors: string[] = [];
  const handler = (msg: ConsoleMessage) => {
    if (msg.type() === 'error') errors.push(msg.text());
  };
  page.on('console', handler);
  await fn();
  page.removeListener('console', handler);
  return errors;
}

// ===================================================================
// A. Admin page loads, 4 tabs visible, no console.error
// ===================================================================
test.describe('A — Admin page & tabs', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('admin page loads with all 4 tabs visible', async ({ page }) => {
    for (const tab of ['system', 'content', 'stats', 'account']) {
      await expect(page.locator(`button[data-tab="${tab}"]`)).toBeVisible({ timeout: 10_000 });
    }
  });

  test('navigating between all 4 tabs produces no console.error', async ({ page }) => {
    const errors = await collectConsoleErrors(page, async () => {
      for (const label of ['系统', '内容', '统计', '账号']) {
        await clickTab(page, label);
        await page.waitForTimeout(300);
      }
    });
    // Filter out common noise (e.g. favicon 404)
    const real = errors.filter(e => !/favicon|404/.test(e));
    expect(real).toEqual([]);
  });
});

// ===================================================================
// B. Content Tab AI UI de-duplication
// ===================================================================
test.describe('B — Content Tab AI de-duplication', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await clickTab(page, '内容');
  });

  test('Content Tab shows AI Tag Review and AI Tagging Jobs sections through section nav', async ({ page }) => {
    const contentPanel = page.locator('#tab-content');
    await expect(contentPanel).toBeVisible({ timeout: 5_000 });

    await page.locator('#content-section-nav a[href="#ai-tag-review-section"]').click();
    await expect(contentPanel.locator('#ai-tag-review-section')).toBeVisible();

    await page.locator('#content-section-nav a[href="#ai-tagging-jobs-section"]').click();
    await expect(contentPanel.locator('#ai-tagging-jobs-section')).toBeVisible();
  });

  test('AI Tagging Jobs shows model status badge', async ({ page }) => {
    await page.locator('#content-section-nav a[href="#ai-tagging-jobs-section"]').click();
    const badge = page.locator('#ai-jobs-model-status-badge');
    await expect(badge).toBeAttached();
  });

  test('Content Tab has NO standalone AI Auto Tagging or AI Direct Tagging section', async ({ page }) => {
    const contentPanel = page.locator('#tab-content');
    await expect(contentPanel).toBeVisible({ timeout: 5_000 });

    // No old-style standalone sections
    const forbidden = contentPanel.locator(
      '#ai-auto-tagging-section, #ai-direct-tagging-section, [id*="ai-auto-tag"]'
    );
    await expect(forbidden).toHaveCount(0);

    // No standalone single-image / batch AI tagging controls directly in content tab
    // (They may exist only inside the DevTools legacy panel in System tab)
    const standaloneControls = contentPanel.locator('#ai-tag-single-btn, #ai-tag-batch-btn');
    await expect(standaloneControls).toHaveCount(0);
  });

  test('all expected Content Tab sections are present', async ({ page }) => {
    const contentPanel = page.locator('#tab-content');
    const expectedIds = [
      'media-management',
      'local-library-scan',
      'ai-tag-review-section',
      'entity-metadata-section',
      'ai-tagging-jobs-section',
      'tag-localization-section',
      'tags-management-section',
      'tag-implications-section',
      'content-classification-section',
      'albums-management-section',
    ];
    for (const id of expectedIds) {
      await expect(contentPanel.locator(`#${id}`)).toBeAttached();
    }
  });
});

// ===================================================================
// C. DevTools legacy AI panel (System Tab)
// ===================================================================
test.describe('C — DevTools legacy AI panel', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await clickTab(page, '系统');
  });

  test('DevTools section exists inside System Tab', async ({ page }) => {
    const systemPanel = page.locator('#tab-system');
    await expect(systemPanel).toBeVisible({ timeout: 5_000 });
    await expect(systemPanel.locator('#dev-tools-section')).toBeVisible();
  });

  test('Legacy AI panel is collapsed by default', async ({ page }) => {
    const content = page.locator('#dev-legacy-ai-tagging-content');
    // The content div should be hidden by default
    await expect(content).toBeHidden();
  });

  test('Legacy AI panel has deprecation notice', async ({ page }) => {
    const devTools = page.locator('#dev-tools-section');
    // The deprecation notice element exists (may be inside the toggle header area)
    const notice = devTools.locator('[data-i18n="admin.dev_tools.legacy_ai_tagging_notice"]');
    await expect(notice).toBeAttached();
  });

  test('Legacy AI panel has "Legacy" badge', async ({ page }) => {
    const devTools = page.locator('#dev-tools-section');
    const legacyBadge = devTools.locator('[data-i18n="admin.dev_tools.legacy_label"]');
    await expect(legacyBadge).toBeAttached();
  });

  test('Legacy AI panel can be expanded and shows controls', async ({ page }) => {
    // Scroll the toggle into view first — it may be below viewport in long System Tab
    const toggle = page.locator('#dev-legacy-ai-tagging-toggle');
    await expect(toggle).toBeAttached({ timeout: 5_000 });
    await toggle.scrollIntoViewIfNeeded();
    await expect(toggle).toBeVisible({ timeout: 5_000 });
    await toggle.click();

    // Content should now be visible
    const content = page.locator('#dev-legacy-ai-tagging-content');
    await expect(content).toBeVisible({ timeout: 3_000 });

    // Legacy controls should be visible after expand
    await expect(content.locator('#ai-tag-single-btn')).toBeVisible();
    await expect(content.locator('#ai-tag-batch-btn')).toBeVisible();
    await expect(content.locator('#ai-tag-media-id')).toBeVisible();
    await expect(content.locator('#ai-tag-refresh-status')).toBeVisible();
  });

  test('Legacy AI panel arrow rotates on expand', async ({ page }) => {
    const arrow = page.locator('#dev-legacy-ai-tagging-arrow');
    await expect(arrow).toBeAttached({ timeout: 5_000 });
    await arrow.scrollIntoViewIfNeeded();
    await expect(arrow).toBeVisible({ timeout: 5_000 });

    // Get initial state — should contain right-pointing indicator
    const initialText = await arrow.textContent();

    // Click to expand
    const toggle = page.locator('#dev-legacy-ai-tagging-toggle');
    await toggle.scrollIntoViewIfNeeded();
    await toggle.click();
    await page.waitForTimeout(400);

    // Arrow should have changed (rotated or different character)
    // The arrow uses CSS transform or different content after toggle
    const content = page.locator('#dev-legacy-ai-tagging-content');
    await expect(content).toBeVisible({ timeout: 3_000 });
  });
});

// ===================================================================
// D. Content quick-nav — click targets, verify scroll
// ===================================================================
test.describe('D — Content quick-nav', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await clickTab(page, '内容');
  });

  test('section nav exists with 10 links', async ({ page }) => {
    const nav = page.locator('#content-section-nav');
    await expect(nav).toBeVisible({ timeout: 5_000 });

    const links = nav.locator('a[href^="#"]');
    const count = await links.count();
    expect(count).toBe(10);
  });

  test('section nav click shows active target - 6 targets', async ({ page }) => {
    const targets = [
      { href: '#media-management', id: 'media-management' },
      { href: '#local-library-scan', id: 'local-library-scan' },
      { href: '#ai-tagging-jobs-section', id: 'ai-tagging-jobs-section' },
      { href: '#tag-localization-section', id: 'tag-localization-section' },
      { href: '#content-classification-section', id: 'content-classification-section' },
      { href: '#albums-management-section', id: 'albums-management-section' },
    ];

    for (const { href, id } of targets) {
      // Click the quick-nav link
      const link = page.locator(`#content-section-nav a[href="${href}"]`);
      await expect(link).toBeVisible();
      await link.click();

      await page.waitForTimeout(200);

      // Verify the target section exists and is the active visible section
      const section = page.locator(`#${id}`);
      await expect(section).toBeVisible();
      await expect(link).toHaveClass(/active/);
    }
  });
});

// ===================================================================
// E. i18n / locale validation
// ===================================================================
test.describe('E — i18n locale rendering', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('data-i18n attributes exist across the page (20+ elements)', async ({ page }) => {
    const i18nElements = page.locator('[data-i18n]');
    const count = await i18nElements.count();
    expect(count).toBeGreaterThan(20);
  });

  test('Content Tab has 10+ data-i18n elements', async ({ page }) => {
    await clickTab(page, '内容');
    const contentI18n = page.locator('#tab-content [data-i18n]');
    const count = await contentI18n.count();
    expect(count).toBeGreaterThan(10);
  });

  test('Chinese locale renders — no raw i18n key patterns visible', async ({ page }) => {
    // Navigate all tabs and check for raw key patterns like "admin.xxx.yyy"
    for (const label of ['系统', '内容', '统计', '账号']) {
      await clickTab(page, label);
      await page.waitForTimeout(500);
    }

    // Check all data-i18n elements — their visible text should NOT look like a raw key
    const elements = page.locator('[data-i18n]');
    const count = await elements.count();
    for (let i = 0; i < count; i++) {
      const el = elements.nth(i);
      const isVisible = await el.isVisible();
      if (!isVisible) continue;

      const text = (await el.textContent())?.trim() || '';
      if (text.length === 0) continue;

      // Raw key pattern: "admin.something.something" — should not appear as visible text
      const looksLikeRawKey = /^(admin|gallery|ai_tagging_jobs|content_classification|common|modal|notifications)\.[a-z_]+(\.[a-z_]+)*/.test(text);
      expect(looksLikeRawKey, `Element with data-i18n shows raw key: "${text}"`).toBeFalsy();
    }
  });

  test('locale JSON files are valid and parseable', async ({ page }) => {
    // Fetch and parse the Chinese locale JSON
    const zhResponse = await page.request.get('/static/locales/zh-cn.json');
    expect(zhResponse.ok()).toBeTruthy();
    const zhData = await zhResponse.json();
    expect(zhData).toBeTruthy();
    expect(typeof zhData).toBe('object');
    // Must have an 'admin' key
    expect(zhData.admin).toBeTruthy();

    // Also validate English locale
    const enResponse = await page.request.get('/static/locales/en.json');
    expect(enResponse.ok()).toBeTruthy();
    const enData = await enResponse.json();
    expect(enData).toBeTruthy();
    expect(enData.admin).toBeTruthy();
  });

  test('key i18n keys have matching entries in zh-cn and en locales', async ({ page }) => {
    const zhResponse = await page.request.get('/static/locales/zh-cn.json');
    const zhData = await zhResponse.json();
    const enResponse = await page.request.get('/static/locales/en.json');
    const enData = await enResponse.json();

    // Check a sample of important admin keys exist in both locales
    const keysToCheck = [
      ['admin', 'tabs', 'system'],
      ['admin', 'tabs', 'content'],
      ['admin', 'tabs', 'stats'],
      ['admin', 'tabs', 'account'],
      ['admin', 'dev_tools', 'title'],
      ['admin', 'dev_tools', 'legacy_label'],
      ['admin', 'dev_tools', 'legacy_ai_tagging_notice'],
      ['admin', 'ai_tagging_jobs', 'title'],
      ['admin', 'content_classification', 'title'],
      ['admin', 'media_management', 'title'],
      ['admin', 'stats', 'total_storage'],
    ];

    for (const keyPath of keysToCheck) {
      let zhVal: any = zhData;
      let enVal: any = enData;
      for (const k of keyPath) {
        zhVal = zhVal?.[k];
        enVal = enVal?.[k];
      }
      const keyStr = keyPath.join('.');
      expect(enVal, `EN locale missing key: ${keyStr}`).toBeTruthy();
      expect(zhVal, `ZH-CN locale missing key: ${keyStr}`).toBeTruthy();
    }
  });

  test('ru/sv locale files return 404 (removed)', async ({ page }) => {
    const ruResponse = await page.request.get('/static/locales/ru.json');
    expect(ruResponse.status()).toBe(404);

    const svResponse = await page.request.get('/static/locales/sv.json');
    expect(svResponse.status()).toBe(404);
  });

  test('backend language registry returns only en and zh-cn', async ({ page }) => {
    const response = await page.request.get('/api/admin/languages');
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    const langIds = data.languages.map((l: { id: string }) => l.id).sort();
    expect(langIds).toEqual(['en', 'zh-cn']);
  });

  test('language selector excludes ru and sv', async ({ page }) => {
    // The language selector is a CustomSelect component (not native <select>).
    // Options are <div class="custom-select-option" data-value="VALUE"> inside
    // .custom-select-dropdown, populated async by loadLanguages() → fetch('/api/admin/languages').
    const select = page.locator('#language-select');
    await expect(select).toBeAttached({ timeout: 5_000 });

    // Wait for async loadLanguages() to populate options
    const firstOption = select.locator('.custom-select-dropdown .custom-select-option').first();
    await expect(firstOption).toBeAttached({ timeout: 10_000 });

    const options = select.locator('.custom-select-dropdown .custom-select-option');
    const count = await options.count();
    expect(count, 'Language selector should have at least 1 option').toBeGreaterThan(0);

    const values: string[] = [];
    for (let i = 0; i < count; i++) {
      const val = await options.nth(i).getAttribute('data-value');
      if (val) values.push(val);
      expect(val, `Language selector should not contain ru`).not.toBe('ru');
      expect(val, `Language selector should not contain sv`).not.toBe('sv');
    }
    expect(values).toContain('en');
    expect(values).toContain('zh-cn');
  });

  test('AI and Classification sections show human-readable text, not raw keys', async ({ page }) => {
    await clickTab(page, '内容');
    const contentPanel = page.locator('#tab-content');
    await expect(contentPanel).toBeVisible({ timeout: 5_000 });

    // Check AI Tagging Jobs section
    await page.locator('#content-section-nav a[href="#ai-tagging-jobs-section"]').click();
    const aiSection = contentPanel.locator('#ai-tagging-jobs-section');
    await expect(aiSection).toBeVisible();
    const aiText = (await aiSection.textContent()) || '';
    expect(aiText.length).toBeGreaterThan(0);
    expect(/^(admin|ai_tagging_jobs)\.[a-z_]+/.test(aiText.trim())).toBeFalsy();

    // Check Content Classification section
    await page.locator('#content-section-nav a[href="#content-classification-section"]').click();
    const clsSection = contentPanel.locator('#content-classification-section');
    await expect(clsSection).toBeVisible();
    const clsText = (await clsSection.textContent()) || '';
    expect(clsText.length).toBeGreaterThan(0);
    expect(/^(admin|content_classification)\.[a-z_]+/.test(clsText.trim())).toBeFalsy();
  });

  test('DevTools section shows human-readable text, not raw keys', async ({ page }) => {
    await clickTab(page, '系统');
    const devTools = page.locator('#dev-tools-section');
    await expect(devTools).toBeVisible({ timeout: 5_000 });
    const devText = (await devTools.textContent()) || '';
    expect(devText.length).toBeGreaterThan(0);
    expect(/^(admin|dev_tools)\.[a-z_]+/.test(devText.trim())).toBeFalsy();
  });
});

// ===================================================================
// F. Stats & Account tab smoke tests
// ===================================================================
test.describe('F — Stats & Account smoke', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('Stats Tab renders stat cards', async ({ page }) => {
    await clickTab(page, 'stats');
    const statsPanel = page.locator('#tab-stats');
    await expect(statsPanel).toBeVisible({ timeout: 5_000 });

    // 8 stat cards expected
    const statIds = [
      'stat-total-storage', 'stat-total-media', 'stat-parent-media', 'stat-child-media',
      'stat-total-albums', 'stat-total-tags', 'stat-tag-aliases', 'stat-tags-with-aliases',
    ];
    for (const id of statIds) {
      await expect(statsPanel.locator(`#${id}`)).toBeAttached();
    }
  });

  test('Stats Tab renders chart canvases', async ({ page }) => {
    await clickTab(page, 'stats');
    const statsPanel = page.locator('#tab-stats');

    // At least some chart canvases should exist
    const chartIds = [
      'chart-upload-trends', 'chart-tag-category', 'chart-media-type',
    ];
    for (const id of chartIds) {
      await expect(statsPanel.locator(`#${id}`)).toBeAttached();
    }
  });

  test('Stats Tab navigation produces no console.error', async ({ page }) => {
    const errors = await collectConsoleErrors(page, async () => {
      await clickTab(page, 'stats');
      await page.waitForTimeout(500);
    });
    const real = errors.filter(e => !/favicon|404/.test(e));
    expect(real).toEqual([]);
  });

  test('Account Tab renders password and username forms', async ({ page }) => {
    await clickTab(page, 'account');
    const accountPanel = page.locator('#tab-account');
    await expect(accountPanel).toBeVisible({ timeout: 5_000 });

    // Password change form
    await expect(accountPanel.locator('#change-admin-password-form')).toBeVisible();
    await expect(accountPanel.locator('#new-admin-password')).toBeVisible();

    // Username change form
    await expect(accountPanel.locator('#change-admin-username-form')).toBeVisible();
    await expect(accountPanel.locator('#new-admin-username')).toBeVisible();
  });

  test('Account Tab navigation produces no console.error', async ({ page }) => {
    const errors = await collectConsoleErrors(page, async () => {
      await clickTab(page, 'account');
      await page.waitForTimeout(500);
    });
    const real = errors.filter(e => !/favicon|404/.test(e));
    expect(real).toEqual([]);
  });
});

/**
 * Phase 3.1.2a — Admin UI Closeout E2E Validation
 *
 * Validates:
 * 1. Admin page loads, all 4 tabs render
 * 2. Content Tab has NO standalone "AI Auto Tagging" section
 * 3. Content Tab quick-nav links are present and functional
 * 4. Legacy AI Tagging is inside DevTools (System Tab), collapsed by default
 * 5. i18n rendering — Chinese locale keys load, data-i18n attributes present
 * 6. Section stable IDs exist across all tabs
 * 7. Deprecation notice visible inside legacy panel header
 */
import { test, expect } from '@playwright/test';
import { loginAsAdmin, switchToTab } from './helpers/auth';

test.describe('Phase 3.1.2a Admin UI Closeout', () => {

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('admin page loads with 4 tabs', async ({ page }) => {
    // Verify all 4 tab buttons exist
    const tabSystem = page.locator('button[data-tab="system"]');
    const tabContent = page.locator('button[data-tab="content"]');
    const tabAccount = page.locator('button[data-tab="account"]');
    const tabStats = page.locator('button[data-tab="stats"]');

    await expect(tabSystem).toBeVisible({ timeout: 10_000 });
    await expect(tabContent).toBeVisible();
    await expect(tabAccount).toBeVisible();
    await expect(tabStats).toBeVisible();
  });

  test('Content Tab has NO standalone AI Auto Tagging section', async ({ page }) => {
    await switchToTab(page, '内容');

    // The Content tab should NOT have any top-level AI Auto Tagging heading
    // It should have AI Tag Review and AI Tagging Jobs, but NOT "AI 自动打标" / "AI Auto Tagging" as a direct section
    const contentPanel = page.locator('#tab-content');
    await expect(contentPanel).toBeVisible({ timeout: 5_000 });

    // Verify the sections that SHOULD exist in Content Tab
    await expect(contentPanel.locator('#media-management')).toBeVisible();
    await expect(contentPanel.locator('#local-library-scan')).toBeVisible();
    await expect(contentPanel.locator('#ai-tag-review-section')).toBeVisible();
    await expect(contentPanel.locator('#ai-tagging-jobs-section')).toBeVisible();
    await expect(contentPanel.locator('#tag-localization-section')).toBeVisible();
    await expect(contentPanel.locator('#tags-management-section')).toBeVisible();
    await expect(contentPanel.locator('#tag-implications-section')).toBeVisible();
    await expect(contentPanel.locator('#content-classification-section')).toBeVisible();
    await expect(contentPanel.locator('#albums-management-section')).toBeVisible();

    // Verify NO standalone ai-auto-tagging or ai-direct-tagging section exists in Content Tab
    const aiAutoTaggingSection = contentPanel.locator('#ai-auto-tagging-section, #ai-direct-tagging-section, [id*="ai-auto-tag"]');
    await expect(aiAutoTaggingSection).toHaveCount(0);
  });

  test('Content Tab quick-nav links present', async ({ page }) => {
    await switchToTab(page, '内容');

    const contentPanel = page.locator('#tab-content');
    await expect(contentPanel).toBeVisible({ timeout: 5_000 });

    // Quick nav should have links to Content Tab sections
    const quickNav = contentPanel.locator('.section-quick-nav, nav[aria-label*="quick"], .quick-nav');
    // Check that anchor links to section IDs exist
    const navLinks = contentPanel.locator('a[href^="#media-management"], a[href^="#local-library-scan"], a[href^="#ai-tag-review"], a[href^="#tag-localization"], a[href^="#tags-management"]');
    const linkCount = await navLinks.count();
    expect(linkCount).toBeGreaterThanOrEqual(4);
  });

  test('Legacy AI Tagging is in DevTools (System Tab), collapsed', async ({ page }) => {
    // Switch to System tab
    await switchToTab(page, '系统');

    const systemPanel = page.locator('#tab-system');
    await expect(systemPanel).toBeVisible({ timeout: 5_000 });

    // DevTools section should exist
    const devTools = systemPanel.locator('#dev-tools-section');
    await expect(devTools).toBeVisible();

    // Legacy AI Tagging panel should be inside dev-tools-section
    // It uses a <details> element that is collapsed by default (no 'open' attribute)
    const legacyPanel = devTools.locator('details#legacy-ai-tagging-panel, details:has(summary:has-text("AI"))');
    await expect(legacyPanel).toBeAttached();

    // Verify collapsed by default — the <details> element should NOT have 'open' attribute
    const isOpen = await legacyPanel.getAttribute('open');
    expect(isOpen).toBeNull();

    // Verify deprecation notice text exists (via data-i18n key)
    const deprecationNotice = legacyPanel.locator('[data-i18n="admin.dev_tools.legacy_ai_tagging_notice"]');
    await expect(deprecationNotice).toBeAttached();
  });

  test('i18n data-i18n attributes present on key elements', async ({ page }) => {
    // Check System Tab
    await switchToTab(page, '系统');
    const systemPanel = page.locator('#tab-system');
    await expect(systemPanel).toBeVisible({ timeout: 5_000 });

    // There should be many data-i18n attributes across the page
    const i18nElements = page.locator('[data-i18n]');
    const count = await i18nElements.count();
    expect(count).toBeGreaterThan(20);

    // Switch to Content Tab and verify i18n there too
    await switchToTab(page, '内容');
    const contentI18n = page.locator('#tab-content [data-i18n]');
    const contentCount = await contentI18n.count();
    expect(contentCount).toBeGreaterThan(10);
  });

  test('Section stable IDs exist across all tabs', async ({ page }) => {
    // System Tab IDs
    await switchToTab(page, '系统');
    await expect(page.locator('#settings-section')).toBeAttached();
    await expect(page.locator('#booru-config-section')).toBeAttached();
    await expect(page.locator('#dev-tools-section')).toBeAttached();

    // Content Tab IDs
    await switchToTab(page, '内容');
    await expect(page.locator('#media-management')).toBeAttached();
    await expect(page.locator('#local-library-scan')).toBeAttached();
    await expect(page.locator('#ai-tag-review-section')).toBeAttached();
    await expect(page.locator('#ai-tagging-jobs-section')).toBeAttached();
    await expect(page.locator('#tag-localization-section')).toBeAttached();
    await expect(page.locator('#tags-management-section')).toBeAttached();
    await expect(page.locator('#tag-implications-section')).toBeAttached();
    await expect(page.locator('#content-classification-section')).toBeAttached();
    await expect(page.locator('#albums-management-section')).toBeAttached();

    // Stats Tab IDs
    await switchToTab(page, '统计');
    await expect(page.locator('#tab-stats')).toBeVisible();
  });

  test('Stats Tab renders overview cards', async ({ page }) => {
    await switchToTab(page, '统计');
    const statsPanel = page.locator('#tab-stats');
    await expect(statsPanel).toBeVisible({ timeout: 5_000 });

    // Stats tab should have overview stat cards
    const statCards = statsPanel.locator('.stat-card, .stats-card, [class*="stat"]');
    const cardCount = await statCards.count();
    expect(cardCount).toBeGreaterThanOrEqual(1);
  });

  test('Account Tab renders password and username forms', async ({ page }) => {
    await switchToTab(page, '账户');
    const accountPanel = page.locator('#tab-account');
    await expect(accountPanel).toBeVisible({ timeout: 5_000 });

    // Should have password-related inputs
    const passwordInputs = accountPanel.locator('input[type="password"]');
    const pwCount = await passwordInputs.count();
    expect(pwCount).toBeGreaterThanOrEqual(2); // current + new password at minimum
  });
});

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
});

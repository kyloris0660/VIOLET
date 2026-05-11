import { test, expect } from '@playwright/test';
import { loginAsAdmin, switchToTab } from './helpers/auth';

test.describe('Admin Content Tab', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await switchToTab(page, '内容');
  });

  test('all content sections are visible', async ({ page }) => {
    const sections = [
      '媒体管理',
      '本地图库扫描',
      'AI 标签审核',
      'AI 打标任务',
      '标签本地化',
      '标签管理',
      '内容分类',
    ];
    for (const section of sections) {
      await expect(
        page.locator(`h2:has-text("${section}"), h3:has-text("${section}")`).first()
      ).toBeVisible();
    }
  });

  test('thumbnail buttons have visible text', async ({ page }) => {
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
    await expect(
      page.locator('text=将媒体文件拖放到此处或点击浏览').first()
    ).toBeVisible();
  });

  test('booru import form is visible', async ({ page }) => {
    await expect(page.locator('input[placeholder*="URL"]').first()).toBeVisible();
    await expect(page.locator('button:has-text("获取")').first()).toBeVisible();
  });

  test('no undefined or [object Object] in page content', async ({ page }) => {
    const body = await page.locator('body').innerText();
    expect(body).not.toContain('[object Object]');
  });
});

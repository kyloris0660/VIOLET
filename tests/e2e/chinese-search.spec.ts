import { test, expect } from '@playwright/test';

test.describe('Chinese Search', () => {
  test('homepage loads with V.I.O.L.E.T. branding', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/V\.I\.O\.L\.E\.T\./);
    await expect(page.locator('a:has-text("V.I.O.L.E.T.")')).toBeVisible();
  });

  test('english tag search works', async ({ page }) => {
    await page.goto('/?q=1girl');
    await page.waitForLoadState('networkidle');
    const body = await page.locator('body').innerText();
    expect(body).not.toContain('Error');
  });

  test('chinese alias search does not crash', async ({ page }) => {
    await page.goto('/?q=蓝眼睛');
    await page.waitForLoadState('networkidle');
    const body = await page.locator('body').innerText();
    expect(body).not.toContain('500 Internal Server Error');
  });

  test('negative chinese search does not crash', async ({ page }) => {
    await page.goto('/?q=-蓝眼睛');
    await page.waitForLoadState('networkidle');
    const body = await page.locator('body').innerText();
    expect(body).not.toContain('500 Internal Server Error');
  });
});

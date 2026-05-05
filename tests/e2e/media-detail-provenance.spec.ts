import { test, expect } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

test.describe('Media Detail Provenance', () => {
  test('media detail page loads without errors', async ({ page }) => {
    await page.goto('/');
    const firstLink = page.locator('a[href^="/media/"]').first();
    if (await firstLink.count() > 0) {
      await firstLink.click();
      await page.waitForLoadState('networkidle');
      const body = await page.locator('body').innerText();
      expect(body).not.toContain('undefined');
      expect(body).not.toContain('[object Object]');
      expect(body).not.toContain('500 Internal Server Error');
    }
  });
});

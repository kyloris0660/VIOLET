import { type Page, expect } from '@playwright/test';

const ADMIN_USER = process.env.VIOLET_ADMIN_USER || 'admin';
const ADMIN_PASS = process.env.VIOLET_ADMIN_PASS || 'admin123';

export async function loginAsAdmin(page: Page) {
  await page.goto('/admin');

  if (page.url().includes('/login')) {
    await page.locator('input[name="username"], input[placeholder*="用户名"], #username').first().fill(ADMIN_USER);
    await page.locator('input[type="password"], input[placeholder*="密码"], #password').first().fill(ADMIN_PASS);
    await page.locator('button:has-text("登录"), button:has-text("Login")').first().click();

    await page.waitForURL(/\/admin/, { timeout: 15_000 });
  }
  await expect(page.locator('h1').first()).toContainText(/管理面板|Admin Panel/, { timeout: 10_000 });
}

export async function switchToTab(page: Page, tabName: string) {
  await page.locator(`button:has-text("${tabName}")`).first().click();
  await page.waitForTimeout(500);
}

export async function apiCall(page: Page, path: string, options?: {
  method?: string;
  body?: string;
}) {
  return await page.evaluate(async ({ path, options }) => {
    const cookies = document.cookie.split(';').reduce((acc, c) => {
      const [k, ...rest] = c.trim().split('=');
      acc[k] = rest.join('=');
      return acc;
    }, {} as Record<string, string>);

    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (cookies['access_token']) {
      headers['Authorization'] = `Bearer ${cookies['access_token']}`;
    }

    const r = await fetch(path, {
      method: options?.method || 'GET',
      headers,
      body: options?.body,
      credentials: 'same-origin',
    });
    let data: any;
    try { data = await r.json(); } catch { data = null; }
    return { status: r.status, data };
  }, { path, options });
}

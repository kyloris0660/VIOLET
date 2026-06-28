import { type Page, expect } from '@playwright/test';

const ADMIN_USER = process.env.VIOLET_ADMIN_USER || 'admin';
const ADMIN_PASS = process.env.VIOLET_ADMIN_PASS || 'admin123';

export async function loginAsAdmin(page: Page) {
  await page.goto('/admin');

  const loginForm = page.locator('input[name="username"], #username').first();
  if (page.url().includes('/login') || await loginForm.isVisible().catch(() => false)) {
    await loginForm.fill(ADMIN_USER);
    await page.locator('input[type="password"], #password').first().fill(ADMIN_PASS);
    await page.locator('#login-btn, button:has-text("Login"), button:has-text("登录")').first().click();

    await expect(page.locator('h1').first()).toContainText(/管理面板|Admin Panel/, { timeout: 15_000 });
    return;
  }

  await expect(page.locator('h1').first()).toContainText(/管理面板|Admin Panel/, { timeout: 10_000 });
}

export async function switchToTab(page: Page, tabName: string) {
  const byId = page.locator(`button.tab-btn[data-tab="${tabName}"]`).first();
  if (await byId.count()) {
    await byId.click();
  } else {
    await page.locator(`button:has-text("${tabName}")`).first().click();
  }
  await page.waitForTimeout(500);
}

export async function navigateToContentSection(page: Page, sectionName: string) {
  await switchToTab(page, 'content');
  await page.locator(`#content-section-nav a:has-text("${sectionName}")`).first().click();
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
    if (cookies.access_token) {
      headers.Authorization = `Bearer ${cookies.access_token}`;
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

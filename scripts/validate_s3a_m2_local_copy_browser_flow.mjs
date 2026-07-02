#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright';

function argValue(name, fallback = '') {
  const index = process.argv.indexOf(name);
  if (index >= 0 && index + 1 < process.argv.length) return process.argv[index + 1];
  return fallback;
}

function utcNow() {
  return new Date().toISOString();
}

async function login(page, baseUrl) {
  await page.goto(`${baseUrl}/login?return=${encodeURIComponent('/admin?tab=content#dynamic-library-sync-section')}`, { waitUntil: 'domcontentloaded' });
  const username = page.locator('input[name="username"], #username').first();
  await username.waitFor({ state: 'visible', timeout: 30000 }).catch(() => {});
  if (await username.isVisible().catch(() => false)) {
    await username.click();
    await username.fill('admin');
    const password = page.locator('input[name="password"], #password').first();
    await password.click();
    await password.fill('admin123');
    await Promise.all([
      page.waitForLoadState('domcontentloaded').catch(() => {}),
      page.locator('#login-btn, button:has-text("Login"), button:has-text("登录")').first().click(),
    ]);
    await page.waitForTimeout(1000);
    if (page.url().includes('/login')) {
      const errorText = await page.locator('#login-error').textContent().catch(() => '');
      throw new Error(`login_failed_or_still_on_login:${errorText || 'no_error_text'}`);
    }
  }
  await page.context().addCookies([
    {
      name: 'admin_mode',
      value: 'true',
      url: baseUrl,
    },
  ]);
}

async function openManualSync(page, baseUrl) {
  await page.goto(`${baseUrl}/admin?tab=content#dynamic-library-sync-section`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#dynamic-library-sync-section', { timeout: 30000 });
  await page.waitForSelector('#dynamic-sync-start-btn', { timeout: 30000 });
}

async function selectRootAndCap(page, rootLabel, cap) {
  const rootSelect = page.locator('#dynamic-sync-plan-root');
  await rootSelect.waitFor({ state: 'visible', timeout: 30000 });
  await page.waitForFunction(
    (label) => {
      const select = document.querySelector('#dynamic-sync-plan-root');
      return !!select && Array.from(select.options).some((option) => option.textContent.includes(label));
    },
    rootLabel,
    { timeout: 30000 },
  );
  const value = await rootSelect.evaluate((select, label) => {
    const option = Array.from(select.options).find((entry) => entry.textContent.includes(label));
    return option ? option.value : '';
  }, rootLabel);
  if (!value) throw new Error(`source_root_option_not_found:${rootLabel}`);
  await rootSelect.selectOption(value);
  const capInput = page.locator('#dynamic-sync-execute-max-files');
  await capInput.fill(String(cap));
}

async function waitForTerminalJob(page, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await page.evaluate(async () => {
      const response = await fetch('/api/admin/dynamic-library-sync/manual-sync/jobs/latest', { credentials: 'same-origin' });
      if (!response.ok) return { ok: false, status: response.status };
      return response.json();
    });
    const job = latest?.job || latest?.data?.job || latest?.latest_job || latest;
    const status = String(job?.status || '').toLowerCase();
    if (['completed', 'completed_with_followup_required', 'failed', 'cancelled', 'stopped'].includes(status)) {
      return { latest, job, status };
    }
    await page.waitForTimeout(2000);
  }
  throw new Error(`job_terminal_timeout:${JSON.stringify(latest).slice(0, 300)}`);
}

async function getLatestJob(page) {
  const latest = await page.evaluate(async () => {
    const response = await fetch('/api/admin/dynamic-library-sync/manual-sync/jobs/latest', { credentials: 'same-origin' });
    if (!response.ok) return { ok: false, status: response.status };
    return response.json();
  });
  return latest?.job || latest?.data?.job || latest?.latest_job || latest;
}

async function waitForNewTerminalJob(page, minJobId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await getLatestJob(page);
    const id = Number(latest?.id || 0);
    const status = String(latest?.status || '').toLowerCase();
    if (id > minJobId && ['completed', 'completed_with_followup_required', 'completed_with_failures', 'failed', 'cancelled', 'stopped'].includes(status)) {
      return { latest, job: latest, status };
    }
    await page.waitForTimeout(2000);
  }
  throw new Error(`new_job_terminal_timeout:min=${minJobId}:latest=${JSON.stringify(latest).slice(0, 300)}`);
}

async function waitForRequestCount(getCount, minimum, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (getCount() >= minimum) return getCount();
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`${label}_timeout:expected_at_least=${minimum}:actual=${getCount()}`);
}

async function main() {
  const baseUrl = argValue('--base-url', process.env.VIOLET_BASE_URL || 'http://127.0.0.1:8024');
  const artifactDir = argValue('--artifact-dir', '.local_manifests/s3a_m2_delta_e2e/local_copy_incremental_e2e/browser_flow');
  const rootLabel = argValue('--root-label', 's3a-m2-local-copy-e2e');
  const cap = Number(argValue('--cap', '20'));
  const timeoutMs = Number(argValue('--timeout-ms', '900000'));
  fs.mkdirSync(artifactDir, { recursive: true });

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const requests = [];
  const dialogs = [];
  page.on('request', (request) => {
    const url = request.url();
    if (url.includes('/api/admin/dynamic-library-sync/manual-sync/')) {
      requests.push({ method: request.method(), url, postData: request.postData() || '' });
    }
  });
  page.on('dialog', async (dialog) => {
    dialogs.push({ type: dialog.type(), message: dialog.message() });
    await dialog.accept();
  });

  const evidence = {
    schema: 's3a_m2_local_copy_browser_flow_v1',
    generated_at: utcNow(),
    base_url: baseUrl,
    root_label: rootLabel,
    cap,
    started_at: utcNow(),
    status: 'running',
  };

  try {
    await login(page, baseUrl);
    await openManualSync(page, baseUrl);
    await selectRootAndCap(page, rootLabel, cap);

    const latestBefore = await getLatestJob(page).catch(() => null);
    const latestBeforeId = Number(latestBefore?.id || 0);
    const normalVisible = await page.locator('#dynamic-sync-operator-card').isVisible();
    const advancedHiddenBefore = await page.locator('#dynamic-sync-confirm-actions').evaluate((el) => el.classList.contains('hidden')).catch(() => false);
    await page.locator('#dynamic-sync-start-btn').click();
    await page.waitForSelector('#dynamic-sync-progress:not(.hidden)', { timeout: 30000 });
    await page.waitForFunction(() => document.querySelector('#dynamic-sync-start-btn')?.disabled === true, { timeout: 30000 });
    await waitForRequestCount(
      () => requests.filter((entry) => entry.url.includes('/manual-sync/execute')).length,
      1,
      180000,
      'normal_flow_execute_request',
    );

    const terminal = await waitForNewTerminalJob(page, latestBeforeId, timeoutMs);
    await page.screenshot({ path: path.join(artifactDir, 'browser-flow-terminal.png'), fullPage: true });

    await openManualSync(page, baseUrl);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await openManualSync(page, baseUrl);
    await selectRootAndCap(page, rootLabel, cap);
    const planRequestCountBeforeRefreshRetry = requests.filter((entry) => entry.url.includes('/manual-sync/plan')).length;
    await page.locator('#dynamic-sync-start-btn').click();
    await page.waitForSelector('#dynamic-sync-progress:not(.hidden)', { timeout: 30000 });
    await waitForRequestCount(
      () => requests.filter((entry) => entry.url.includes('/manual-sync/plan')).length,
      planRequestCountBeforeRefreshRetry + 1,
      30000,
      'refresh_retry_plan_request',
    );
    await page.waitForTimeout(3000);
    await page.screenshot({ path: path.join(artifactDir, 'browser-flow-refresh-retry.png'), fullPage: true });

    const executeRequests = requests.filter((entry) => entry.url.includes('/manual-sync/execute'));
    const planRequests = requests.filter((entry) => entry.url.includes('/manual-sync/plan'));
    const advancedInputValue = await page.locator('#dynamic-sync-confirmation').inputValue().catch(() => '');
    evidence.status = 'completed';
    evidence.finished_at = utcNow();
    evidence.normal_operator_card_visible = normalVisible;
    evidence.advanced_execute_hidden_before_normal_flow = advancedHiddenBefore;
    evidence.latest_job_before_start_id = latestBeforeId;
    evidence.plan_request_count = planRequests.length;
    evidence.execute_request_count = executeRequests.length;
    evidence.dialogs = dialogs;
    evidence.latest_job_status = terminal.status;
    evidence.latest_job = terminal.job;
    evidence.advanced_confirmation_input_after_flow = advancedInputValue;
    evidence.requests = requests.map((entry) => ({
      method: entry.method,
      url_path: new URL(entry.url).pathname,
      has_body: !!entry.postData,
      body_preview: entry.postData ? entry.postData.slice(0, 500) : '',
    }));
    evidence.pass = Boolean(
      normalVisible
      && advancedHiddenBefore
      && planRequests.length >= 1
      && executeRequests.length >= 1
      && ['completed', 'completed_with_followup_required'].includes(terminal.status)
      && !advancedInputValue,
    );
  } catch (error) {
    evidence.status = 'failed';
    evidence.finished_at = utcNow();
    evidence.error = error?.stack || String(error);
    await page.screenshot({ path: path.join(artifactDir, 'browser-flow-failed.png'), fullPage: true }).catch(() => {});
  } finally {
    evidence.requests = evidence.requests || requests.map((entry) => ({
      method: entry.method,
      url_path: new URL(entry.url).pathname,
      has_body: !!entry.postData,
      body_preview: entry.postData ? entry.postData.slice(0, 500) : '',
    }));
    evidence.dialogs = evidence.dialogs || dialogs;
    fs.writeFileSync(path.join(artifactDir, 'browser-flow-evidence.json'), JSON.stringify(evidence, null, 2), 'utf8');
    await browser.close();
  }
  console.log(JSON.stringify(evidence, null, 2));
  process.exit(evidence.pass ? 0 : 2);
}

main().catch((error) => {
  console.error(error);
  process.exit(2);
});

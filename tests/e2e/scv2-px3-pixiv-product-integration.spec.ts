import { test, expect, type Page } from '@playwright/test';

const enabled = process.env.VIOLET_RUN_PX3_SYNTHETIC_E2E === '1';

async function openProductSection(page: Page) {
  await page.goto('/admin?tab=content#pixiv-product-integration-section');
  await page.locator('button.tab-btn[data-tab="content"]').click();
  await page.locator('#content-section-nav a[href="#pixiv-product-integration-section"]').click();
  await expect(page.getByTestId('pixiv-product-integration')).toBeVisible();
  await expect(page.getByTestId('pixiv-product-boundary')).toContainText('real provider: disabled');
}

test.describe('SCV2-PX3 Pixiv product integration', () => {
  test.skip(!enabled, 'Requires the task-owned PX3 synthetic UI server.');

  test('dry-run, apply, inspect, filter, rollback, and replay remain auditable', async ({ page }) => {
    const pageErrors: string[] = [];
    const consoleErrors: string[] = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    await openProductSection(page);

    await expect(page.locator('#pixiv-product-cluster-count')).toHaveText('0');
    await page.locator('#pixiv-product-synthetic-dry-run-btn').click();
    await expect(page.locator('#pixiv-product-message')).toContainText('Dry-run 完成');
    await expect(page.locator('#pixiv-product-cluster-count')).toHaveText('20');
    await expect(page.locator('#pixiv-product-candidate-count')).toHaveText('59');
    await expect(page.locator('#pixiv-product-ambiguity-count')).toHaveText('29');
    await expect(page.locator('#pixiv-product-run-select')).toHaveValue('');

    page.once('dialog', dialog => dialog.accept());
    await page.locator('#pixiv-product-synthetic-apply-btn').click();
    await expect(page.locator('#pixiv-product-run-status')).toHaveText('active');
    await expect(page.locator('#pixiv-product-run-select option')).toHaveCount(1);
    await expect(page.getByTestId('pixiv-product-clusters').locator('tr')).toHaveCount(20);
    await expect(page.getByTestId('pixiv-product-candidates').locator('tr')).toHaveCount(59);
    await expect(page.getByTestId('pixiv-product-ambiguity').locator('tr')).toHaveCount(29);

    await page.getByTestId('pixiv-product-clusters').locator('button').first().click();
    await expect(page.getByTestId('pixiv-product-cluster-detail')).toContainText('member_signal_keys');
    await expect(page.getByTestId('pixiv-product-cluster-detail')).toContainText('provenance');

    await page.locator('#pixiv-product-candidate-filter').selectOption('cannot_link');
    await expect(page.getByTestId('pixiv-product-candidates').locator('tr')).toHaveCount(4);
    await page.locator('#pixiv-product-ambiguity-filter').selectOption('context_conflict');
    await expect(page.getByTestId('pixiv-product-ambiguity').locator('tr')).toHaveCount(1);

    const runKey = await page.locator('#pixiv-product-run-select').inputValue();
    const detailResponse = await page.request.get(
      `/api/admin/pixiv-product-integration/runs/${encodeURIComponent(runKey)}`,
    );
    expect(detailResponse.status()).toBe(200);
    const detail = await detailResponse.json();
    expect(detail.operation_receipt.provider_network_activity).toBe(0);
    expect(detail.operation_receipt.existing_database_or_app_storage_activity).toBe(0);
    expect(detail.candidate_dispositions).toHaveLength(59);
    expect(detail.ambiguity_records).toHaveLength(29);

    page.once('dialog', dialog => dialog.accept());
    await page.locator('#pixiv-product-rollback-btn').click();
    await expect(page.locator('#pixiv-product-run-status')).toHaveText('rolled_back');
    await expect(page.locator('#pixiv-product-rollback-btn')).toBeDisabled();

    page.once('dialog', dialog => dialog.accept());
    await page.locator('#pixiv-product-synthetic-apply-btn').click();
    await expect(page.locator('#pixiv-product-run-status')).toHaveText('active');
    await expect(page.locator('#pixiv-product-run-select option')).toHaveCount(1);

    const visibleProductText = await page.getByTestId('pixiv-product-integration').innerText();
    expect(visibleProductText).not.toMatch(/C:\\Users\\|file:\/\/|raw_metadata_json|Authorization:\s*Bearer/i);
    expect(pageErrors).toEqual([]);
    expect(consoleErrors).toEqual([]);
  });
});

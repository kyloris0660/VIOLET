import { test, expect, type Page, type ConsoleMessage } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

const isRealE2E = process.env.VIOLET_RUN_REAL_E2E === '1';

type SourceLayerFixture = {
  mediaId: number;
  sourceAssertions: any[];
  sourceTags: any[];
};

async function findSourceLayerFixture(page: Page): Promise<SourceLayerFixture | null> {
  const mediaResp = await apiCall(page, '/api/media?page=1&limit=80');
  const items = Array.isArray(mediaResp.data) ? mediaResp.data : (mediaResp.data?.items || []);

  for (const item of items) {
    const layerResp = await apiCall(page, `/api/source-assertions/media/${item.id}`);
    if (layerResp.status !== 200) continue;
    const sourceAssertions = [
      ...(layerResp.data?.source_assertions || []),
      ...(layerResp.data?.needs_review_assertions || []),
    ];
    const sourceTags = layerResp.data?.source_tags || [];
    if (sourceAssertions.length >= 2 && sourceTags.length >= 1 && (item.tags || []).length >= 2) {
      return { mediaId: item.id, sourceAssertions, sourceTags };
    }
  }
  return null;
}

async function collectConsoleErrors(page: Page, fn: () => Promise<void>): Promise<string[]> {
  const errors: string[] = [];
  const handler = (msg: ConsoleMessage) => {
    if (msg.type() === 'error') errors.push(msg.text());
  };
  page.on('console', handler);
  await fn();
  page.removeListener('console', handler);
  return errors.filter(e => !/favicon|404/.test(e));
}

test.describe('F6 source-layer media detail and search', () => {
  test.skip(!isRealE2E, 'Requires VIOLET_RUN_REAL_E2E=1');

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(200);
  });

  test('media detail shows distinct source assertions near tags and click search works', async ({ page }) => {
    const fixture = await findSourceLayerFixture(page);
    test.skip(!fixture, 'No media with at least two source assertions and normal tags in test DB');

    const errors = await collectConsoleErrors(page, async () => {
      await page.goto(`/media/${fixture!.mediaId}`);
      await page.waitForLoadState('networkidle');

      await expect(page.locator('#tags-container')).toBeVisible();
      await expect(page.locator('#source-layer-container')).toBeVisible();
      await expect(page.locator('#source-layer-container .source-assertion-chip').first()).toBeVisible();
      await expect(page.locator('#source-layer-container .source-chip-marker').first()).toBeVisible();

      await page.locator('#source-layer-container .source-assertion-chip').first().click();
      await page.waitForURL(/source_assertion=/);
      await expect(page.locator('#source-search-summary')).toBeVisible();
    });

    expect(errors).toEqual([]);
  });

  test('visual multi-select searches normal tags plus source assertions without manual AND input', async ({ page }) => {
    const fixture = await findSourceLayerFixture(page);
    test.skip(!fixture, 'No media with enough source-layer chips in test DB');

    const errors = await collectConsoleErrors(page, async () => {
      await page.goto(`/media/${fixture!.mediaId}`);
      await page.waitForLoadState('networkidle');

      await page.locator('#tag-select-mode-toggle').click();
      await expect(page.locator('#tag-select-tray')).toBeVisible();

      const normalTags = page.locator('#tags-container [data-search-chip="true"][data-chip-type="tag"]');
      const sourceAssertions = page.locator('#source-layer-container [data-search-chip="true"][data-chip-type="source_assertion"]');

      await normalTags.nth(0).click();
      await normalTags.nth(1).click();
      await sourceAssertions.nth(0).click();
      await sourceAssertions.nth(1).click();

      await expect(page.locator('#tag-select-selected-list .selected-search-chip')).toHaveCount(4);
      await page.locator('#tag-select-selected-list .selected-search-chip').first().click();
      await expect(page.locator('#tag-select-selected-list .selected-search-chip')).toHaveCount(3);
      await page.locator('#tag-select-clear').click();
      await expect(page.locator('#tag-select-search')).toBeDisabled();

      await normalTags.nth(0).click();
      await sourceAssertions.nth(0).click();
      await expect(page.locator('#tag-select-search')).toBeEnabled();
      await page.locator('#tag-select-search').click();

      await page.waitForURL(/source_assertion=/);
      expect(page.url()).toContain('q=');
      await expect(page.locator('#source-search-summary')).toBeVisible();
    });

    expect(errors).toEqual([]);
  });

  test('multiple source assertions and source tags can be searched as intersection filters', async ({ page }) => {
    const fixture = await findSourceLayerFixture(page);
    test.skip(!fixture, 'No media with enough source-layer chips in test DB');

    const params = new URLSearchParams();
    params.append('source_assertion', fixture!.sourceAssertions[0].search_value);
    params.append('source_assertion', fixture!.sourceAssertions[1].search_value);
    params.append('source_tag', fixture!.sourceTags[0].search_value);
    params.set('include_source_needs_review', '1');

    await page.goto(`/?${params.toString()}`);
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#source-search-summary')).toBeVisible();

    const apiResp = await apiCall(page, `/api/search?${params.toString()}`);
    expect(apiResp.status).toBe(200);
    expect(apiResp.data.total).toBeGreaterThanOrEqual(1);
    expect(apiResp.data.items.map((item: any) => item.id)).toContain(fixture!.mediaId);
  });

  test('random search preserves needs-review source assertion flag', async ({ page }) => {
    const fixture = await findSourceLayerFixture(page);
    test.skip(!fixture, 'No media with enough source-layer chips in test DB');

    const needsReviewAssertion = fixture!.sourceAssertions.find(
      (chip: any) => chip.include_source_needs_review === true
    );
    test.skip(!needsReviewAssertion, 'No needs-review source assertion in test DB fixture');

    const params = new URLSearchParams();
    params.append('source_assertion', needsReviewAssertion.search_value);
    params.set('include_source_needs_review', '1');

    await page.goto(`/?${params.toString()}`);
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#source-search-summary')).toBeVisible();

    const requestPromise = page.waitForRequest(request => request.url().includes('/api/search/random'));
    await page.locator('#search-random-btn').click();
    const request = await requestPromise;
    const randomUrl = new URL(request.url());

    expect(randomUrl.searchParams.get('include_source_needs_review')).toBe('1');
    expect(randomUrl.searchParams.getAll('source_assertion')).toContain(needsReviewAssertion.search_value);
  });

  test('removing the only needs-review chip drops the review opt-in flag', async ({ page }) => {
    await page.evaluate(() => {
      history.replaceState(
        null,
        '',
        '/?source_assertion=active-token&source_assertion=review-token&include_source_needs_review=1'
      );
      (window as any).gallery.renderSourceSearchSummary(
        {
          source_filters: {
            source_assertions: [
              {
                display_name: 'Active Source',
                search_value: 'active-token',
                include_source_needs_review: false,
              },
              {
                display_name: 'Review Source',
                search_value: 'review-token',
                include_source_needs_review: true,
              },
            ],
            source_tags: [],
          },
        },
        '',
        ['active-token', 'review-token'],
        [],
        true
      );
    });

    const activeHref = await page
      .locator('#source-search-summary a.selected-search-chip', { hasText: 'Active Source' })
      .getAttribute('href');
    const reviewHref = await page
      .locator('#source-search-summary a.selected-search-chip', { hasText: 'Review Source' })
      .getAttribute('href');

    const activeUrl = new URL(activeHref!, page.url());
    expect(activeUrl.searchParams.getAll('source_assertion')).toEqual(['review-token']);
    expect(activeUrl.searchParams.get('include_source_needs_review')).toBe('1');

    const reviewUrl = new URL(reviewHref!, page.url());
    expect(reviewUrl.searchParams.getAll('source_assertion')).toEqual(['active-token']);
    expect(reviewUrl.searchParams.get('include_source_needs_review')).toBeNull();
  });

  test('admin content left navigation switches visible sections', async ({ page }) => {
    await page.goto('/admin?tab=content#media-management');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#content-section-nav')).toBeVisible();

    await page.locator('#content-section-nav a[href="#local-library-scan"]').click();
    await expect(page.locator('#local-library-scan')).toBeVisible();
    await expect(page.locator('#media-management')).toBeHidden();

    await page.locator('#content-section-nav a[href="#albums-management-section"]').click();
    await expect(page.locator('#albums-management-section')).toBeVisible();
    await expect(page.locator('#local-library-scan')).toBeHidden();
  });
});

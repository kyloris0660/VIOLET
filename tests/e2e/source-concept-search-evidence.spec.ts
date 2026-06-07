import { test, expect, type ConsoleMessage, type Page } from '@playwright/test';
import { loginAsAdmin, apiCall } from './helpers/auth';

const isRealE2E = process.env.VIOLET_RUN_REAL_E2E === '1';
const markerTag = 'phase45_sc2_e2e_marker';
const ayakaJa = '\u795e\u91cc\u7dbe\u83ef';

type SourceConceptFixture = {
  tagAndConceptId: number;
  conceptOnlyId: number;
  tagOnlyId: number;
  conceptId: number;
};

function expectedQToken(label: string): string {
  return /^-|[\s:"*?\[\]\(\)]/.test(label) ? `"${label.replace(/"/g, '')}"` : label;
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

async function findSourceConceptFixture(page: Page): Promise<SourceConceptFixture | null> {
  const searchResp = await apiCall(page, `/api/search?q=${encodeURIComponent(markerTag)}&limit=20`);
  if (searchResp.status !== 200) return null;
  const items = searchResp.data?.items || [];
  let tagAndConceptId: number | null = null;
  let tagOnlyId: number | null = null;
  let conceptId: number | null = null;

  for (const item of items) {
    const layerResp = await apiCall(page, `/api/source-assertions/media/${item.id}`);
    const concepts = layerResp.data?.source_concepts || [];
    if (concepts.length && tagAndConceptId === null) {
      tagAndConceptId = item.id;
      conceptId = concepts[0].concept_id;
    } else if (!concepts.length && tagOnlyId === null) {
      tagOnlyId = item.id;
    }
  }

  const conceptSearchResp = await apiCall(page, `/api/search?q=${encodeURIComponent(ayakaJa)}&limit=20`);
  if (conceptSearchResp.status !== 200) return null;
  const conceptIds = (conceptSearchResp.data?.items || []).map((item: any) => item.id);
  const conceptOnlyId = conceptIds.find((id: number) => id !== tagAndConceptId) ?? null;

  if (tagAndConceptId === null || tagOnlyId === null || conceptOnlyId === null || conceptId === null) {
    return null;
  }

  return { tagAndConceptId, conceptOnlyId, tagOnlyId, conceptId };
}

test.describe('SC2 SourceConcept search expansion and evidence UI', () => {
  test.skip(!isRealE2E, 'Requires VIOLET_RUN_REAL_E2E=1 and seeded SC2 fixture');

  test.beforeEach(async ({ page }) => {
    await loginAsAdmin(page);
  });

  test('media detail shows SourceConcept evidence, q chip search, and disabled promotion', async ({ page }) => {
    const fixture = await findSourceConceptFixture(page);
    test.skip(!fixture, 'Run scripts/seed_phase45_sc2_e2e_fixture.py before this test');

    const errors = await collectConsoleErrors(page, async () => {
      await page.goto(`/media/${fixture!.tagAndConceptId}`);
      await page.waitForLoadState('networkidle');

      const sourceLayer = page.locator('#source-layer-container');
      await expect(sourceLayer).toBeVisible();
      await expect(sourceLayer.locator('.source-concept-chip').first()).toBeVisible();
      await expect(sourceLayer.locator('.source-concept-card')).toHaveCount(0);
      await expect(sourceLayer.locator('.source-concept-chip[data-display-name="Kamisato Ayaka"]')).toHaveCount(1);
      await expect(sourceLayer.locator('.source-concept-chip').first()).toContainText(/source-layer|未确认|active/i);
      await expect(sourceLayer.locator('.source-concept-details-panel')).not.toHaveAttribute('open', '');
      await expect(sourceLayer.locator('.source-concept-meta-grid').first()).toBeHidden();

      await sourceLayer.locator('.source-concept-details-panel summary').click();
      await expect(sourceLayer.locator('.source-concept-detail').first()).toBeVisible();
      await expect(sourceLayer.locator('.source-concept-evidence-row').first()).toBeVisible();
      await expect(sourceLayer.locator('.source-concept-promotion-preview').first()).toContainText(/disabled|禁用|Preview only|仅预览/i);
      await expect(sourceLayer).not.toContainText(/C:\\|Users\\|api_key|secret-token|private\.png/i);

      const chip = sourceLayer.locator('.source-concept-chip[data-search-value="Re:Zero"]').first();
      await expect(chip).toBeVisible();
      const searchValue = (await chip.getAttribute('data-search-value')) || (await chip.innerText()).trim();
      await chip.click();
      await page.waitForURL(/q=/);
      const url = new URL(page.url());
      expect(url.searchParams.get('q')).toBe(expectedQToken(searchValue));
      expect(url.searchParams.get('source_assertion')).toBeNull();
      expect(url.searchParams.get('source_tag')).toBeNull();

      const searchResp = await apiCall(page, `/api/search?${url.searchParams.toString()}`);
      expect(searchResp.status).toBe(200);
      expect(searchResp.data.items.map((item: any) => item.id)).toContain(fixture!.tagAndConceptId);
      expect(searchResp.data.source_concept_expansions.length).toBeGreaterThan(0);
      await expect(page.locator('#source-search-summary')).toBeVisible();
      await expect(page.locator('#source-search-summary .source-concept-expansion-row').first()).toBeVisible();
    });

    expect(errors).toEqual([]);
  });

  test('mixed normal tag plus SourceConcept search keeps AND semantics and explanation', async ({ page }) => {
    const fixture = await findSourceConceptFixture(page);
    test.skip(!fixture, 'Run scripts/seed_phase45_sc2_e2e_fixture.py before this test');

    const params = new URLSearchParams({ q: `${markerTag} ${ayakaJa}` });
    const apiResp = await apiCall(page, `/api/search?${params.toString()}`);
    expect(apiResp.status).toBe(200);
    const ids = apiResp.data.items.map((item: any) => item.id);
    expect(ids).toContain(fixture!.tagAndConceptId);
    expect(ids).not.toContain(fixture!.conceptOnlyId);
    expect(ids).not.toContain(fixture!.tagOnlyId);
    expect(apiResp.data.source_concept_expansions.length).toBeGreaterThan(0);

    const errors = await collectConsoleErrors(page, async () => {
      await page.goto(`/?${params.toString()}`);
      await page.waitForLoadState('networkidle');
      await expect(page.locator('#source-search-summary')).toBeVisible();
      await expect(page.locator('#source-search-summary .source-concept-expansion-row').first()).toBeVisible();
      await expect(page.locator('#gallery-grid .gallery-item')).toHaveCount(1);
    });

    expect(errors).toEqual([]);
  });

  test('needs-review SourceConcept expands on explicit alias search and stays labeled', async ({ page }) => {
    const defaultResp = await apiCall(page, '/api/search?q=review_only_character');
    expect(defaultResp.status).toBe(200);
    expect(defaultResp.data.total).toBeGreaterThanOrEqual(1);
    expect(defaultResp.data.source_concept_review_hints).toEqual([]);
    expect(defaultResp.data.source_concept_expansions[0].status).toBe('needs_review');
    expect(defaultResp.data.source_concept_expansions[0].source_layer_label).toBe('unconfirmed source-layer');
  });

  test('promotion preview API remains disabled and source-layer only', async ({ page }) => {
    const fixture = await findSourceConceptFixture(page);
    test.skip(!fixture, 'Run scripts/seed_phase45_sc2_e2e_fixture.py before this test');

    const preview = await apiCall(page, `/api/source-concepts/${fixture!.conceptId}/promotion-preview`);
    expect(preview.status).toBe(200);
    expect(preview.data.preview_only).toBe(true);
    expect(preview.data.disabled).toBe(true);
    expect(preview.data.truth_writes_allowed).toBe(false);
    expect(preview.data.forbidden_paths).toContain('media_tags');
    expect(JSON.stringify(preview.data)).not.toMatch(/C:\\|Users\\|api_key|secret-token|private\.png/i);
  });
});

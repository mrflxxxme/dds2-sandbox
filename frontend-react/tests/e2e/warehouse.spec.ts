import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad, expectNoAuthError } from './helpers';

test.describe('Warehouse', () => {
  test('should load warehouse list page', async ({ page }) => {
    await page.goto(projectUrl('/warehouse'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
    // Either warehouse list or empty state
    const content = page.locator('.glass-card, table, .empty-state');
    await expect(content.first()).toBeVisible({ timeout: 10_000 });
  });

  test('should load stock summary page', async ({ page }) => {
    await page.goto(projectUrl('/warehouse/stock'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
  });

  test('should load assembly requests page', async ({ page }) => {
    await page.goto(projectUrl('/warehouse/assembly'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
    const content = page.locator('table, .empty-state, .glass-card');
    await expect(content.first()).toBeVisible({ timeout: 10_000 });
  });

  test('should load FBO supplies page', async ({ page }) => {
    await page.goto(projectUrl('/warehouse/fbo-supplies'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
  });

  test('should load WB stocks page', async ({ page }) => {
    await page.goto(projectUrl('/warehouse/wb-stocks'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
  });

  test('should load warehouse analytics page', async ({ page }) => {
    await page.goto(projectUrl('/warehouse/analytics'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
  });

  test('should open transfers tab on warehouse detail page', async ({ page }) => {
    await page.goto(projectUrl('/warehouse'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
    const row = page.locator('tbody tr').first();
    const hasWarehouse = await row.isVisible({ timeout: 5_000 }).catch(() => false);
    test.skip(!hasWarehouse, 'Нет складов в проекте');
    await row.click();
    await page.waitForURL(/\/warehouse\/\d+/, { timeout: 10_000 });
    await waitForPageLoad(page);
    // Вкладка «Перемещения» (бейдж в имени допустим); «+ Перемещение» не матчится
    const transfersTab = page.getByRole('button', { name: /^Перемещения/ });
    await expect(transfersTab).toBeVisible({ timeout: 10_000 });
    await transfersTab.click();
    const content = page.locator('table, .empty-state, .glass-card');
    await expect(content.first()).toBeVisible({ timeout: 10_000 });
  });
});

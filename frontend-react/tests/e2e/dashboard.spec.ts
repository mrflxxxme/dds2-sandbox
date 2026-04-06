import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad, expectPageTitle } from './helpers';

test.describe('Dashboard', () => {
  test('should load dashboard page', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    await expectPageTitle(page, 'Дашборд');
  });

  test('should render period selector buttons', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    // Period labels: Месяц, Пред., 7д, 30д, 90д, Всё
    await expect(page.locator('button:has-text("Месяц")')).toBeVisible();
    await expect(page.locator('button:has-text("7д")')).toBeVisible();
  });

  test('should render KPI cards or loading state', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    const kpiOrContent = page.locator('.stat-card, .glass-card');
    await expect(kpiOrContent.first()).toBeVisible({ timeout: 15_000 });
  });

  test('should switch period without errors', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    const btn7d = page.locator('button:has-text("7д")');
    await btn7d.click();
    // Page should still be loaded after period switch
    await expect(page.locator('.page-title')).toContainText('Дашборд');
    expect(page.url()).not.toContain('/login');
  });
});

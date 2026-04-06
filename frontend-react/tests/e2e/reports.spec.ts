import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad, expectPageTitle } from './helpers';

test.describe('Reports', () => {
  test('should load reports page', async ({ page }) => {
    await page.goto(projectUrl('/reports'));
    await waitForPageLoad(page);
    await expectPageTitle(page, 'Отчёты');
  });

  test('should render all report tabs', async ({ page }) => {
    await page.goto(projectUrl('/reports'));
    await waitForPageLoad(page);
    for (const label of ['ДДС за месяц', 'БДР (WB)', 'Баланс по дням', 'FX Контроль', 'Таможня', 'Себестоимость']) {
      await expect(page.locator(`button:has-text("${label}")`)).toBeVisible();
    }
  });

  test('should show DDS tab active by default', async ({ page }) => {
    await page.goto(projectUrl('/reports'));
    await waitForPageLoad(page);
    const ddsTab = page.locator('button:has-text("ДДС за месяц")');
    await expect(ddsTab).toHaveClass(/btn-primary/);
  });

  test('should switch to BDR tab', async ({ page }) => {
    await page.goto(projectUrl('/reports'));
    await waitForPageLoad(page);
    await page.click('button:has-text("БДР (WB)")');
    const bdrTab = page.locator('button:has-text("БДР (WB)")');
    await expect(bdrTab).toHaveClass(/btn-primary/);
    const ddsTab = page.locator('button:has-text("ДДС за месяц")');
    await expect(ddsTab).toHaveClass(/btn-secondary/);
  });

  test('should switch to Balance tab', async ({ page }) => {
    await page.goto(projectUrl('/reports'));
    await waitForPageLoad(page);
    await page.click('button:has-text("Баланс по дням")');
    await expect(page.locator('button:has-text("Баланс по дням")')).toHaveClass(/btn-primary/);
  });
});

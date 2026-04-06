import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad, expectPageTitle } from './helpers';

test.describe('Planning', () => {
  test('should load planning page', async ({ page }) => {
    await page.goto(projectUrl('/planning'));
    await waitForPageLoad(page);
    await expectPageTitle(page, 'Планирование');
  });

  test('should render all planning tabs', async ({ page }) => {
    await page.goto(projectUrl('/planning'));
    await waitForPageLoad(page);
    for (const label of ['Заказы', 'Платежи', 'Поступления WB', 'WB Payouts', 'Кэшфлоу', 'Таможня']) {
      await expect(page.locator(`button:has-text("${label}")`)).toBeVisible();
    }
  });

  test('should show Orders tab active by default', async ({ page }) => {
    await page.goto(projectUrl('/planning'));
    await waitForPageLoad(page);
    await expect(page.locator('button:has-text("Заказы")')).toHaveClass(/btn-primary/);
  });

  test('should switch to Payments tab', async ({ page }) => {
    await page.goto(projectUrl('/planning'));
    await waitForPageLoad(page);
    await page.click('button:has-text("Платежи")');
    await expect(page.locator('button:has-text("Платежи")')).toHaveClass(/btn-primary/);
    await expect(page.locator('button:has-text("Заказы")')).toHaveClass(/btn-secondary/);
  });

  test('should switch to Cashflow tab', async ({ page }) => {
    await page.goto(projectUrl('/planning'));
    await waitForPageLoad(page);
    await page.click('button:has-text("Кэшфлоу")');
    await expect(page.locator('button:has-text("Кэшфлоу")')).toHaveClass(/btn-primary/);
  });
});

import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad, expectPageTitle } from './helpers';

test.describe('Settings', () => {
  test('should load settings page', async ({ page }) => {
    await page.goto(projectUrl('/settings'));
    await waitForPageLoad(page);
    await expectPageTitle(page, 'Настройки проекта');
  });

  test('should render all settings tabs', async ({ page }) => {
    await page.goto(projectUrl('/settings'));
    await waitForPageLoad(page);
    for (const label of ['API Интеграции', 'Номенклатура', 'Lead Times', 'Налоговые ставки', 'Тарифы WB', 'Telegram-бот']) {
      await expect(page.locator(`button:has-text("${label}")`)).toBeVisible();
    }
  });

  test('should show Integrations tab active by default', async ({ page }) => {
    await page.goto(projectUrl('/settings'));
    await waitForPageLoad(page);
    await expect(page.locator('button:has-text("API Интеграции")')).toHaveClass(/btn-primary/);
  });

  test('should switch to Nomenclature tab', async ({ page }) => {
    await page.goto(projectUrl('/settings'));
    await waitForPageLoad(page);
    await page.click('button:has-text("Номенклатура")');
    await expect(page.locator('button:has-text("Номенклатура")')).toHaveClass(/btn-primary/);
    await expect(page.locator('button:has-text("API Интеграции")')).toHaveClass(/btn-secondary/);
  });

  test('should switch to Tax Rates tab', async ({ page }) => {
    await page.goto(projectUrl('/settings'));
    await waitForPageLoad(page);
    await page.click('button:has-text("Налоговые ставки")');
    await expect(page.locator('button:has-text("Налоговые ставки")')).toHaveClass(/btn-primary/);
  });
});

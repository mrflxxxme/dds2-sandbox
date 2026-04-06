import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad, expectNoAuthError } from './helpers';

test.describe('Import', () => {
  test('should load import page', async ({ page }) => {
    await page.goto(projectUrl('/import'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
  });

  test('should display import type selector', async ({ page }) => {
    await page.goto(projectUrl('/import'));
    await waitForPageLoad(page);
    // Import types are rendered as buttons or select options
    await expect(page.locator('text=ВТБ Рубли Основной').or(page.locator('select, [class*="import-type"]')).first()).toBeVisible({ timeout: 10_000 });
  });

  test('should have file upload area', async ({ page }) => {
    await page.goto(projectUrl('/import'));
    await waitForPageLoad(page);
    const fileInput = page.locator('input[type="file"]');
    // File input may be hidden but must exist in DOM
    await expect(fileInput).toHaveCount(1);
  });

  test('should have upload button', async ({ page }) => {
    await page.goto(projectUrl('/import'));
    await waitForPageLoad(page);
    const uploadBtn = page.locator('button:has-text("Загрузить"), button:has-text("Импортировать")');
    await expect(uploadBtn.first()).toBeVisible({ timeout: 10_000 });
  });
});

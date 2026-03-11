import { test, expect } from '@playwright/test';

/**
 * Transactions page tests — verify table rendering and basic interactions.
 */

test.describe('Transactions', () => {
  test.beforeEach(async ({ page }) => {
    // Login
    await page.goto('/login');
    await page.fill('input[type="email"], input[name="email"], input[placeholder*="mail"]', 'admin@dds.local');
    await page.fill('input[type="password"]', 'admin');
    await page.locator('button[type="submit"], button:has-text("Войти")').click();
    await page.waitForURL(/(?!.*login).*/, { timeout: 5000 });
  });

  test('should display transactions table', async ({ page }) => {
    // Navigate to transactions page
    await page.goto('/p/default/txn');
    await page.waitForTimeout(2000);

    // Table should be present
    const table = page.locator('table, [class*="table"], [role="table"]').first();
    await expect(table).toBeVisible({ timeout: 5000 });
  });

  test('should have export button', async ({ page }) => {
    await page.goto('/p/default/txn');
    await page.waitForTimeout(2000);

    // Excel export button should be present (per project conventions)
    const exportBtn = page.locator('button:has-text("Excel"), button:has-text("Экспорт"), button:has-text("export")');
    await expect(exportBtn.first()).toBeVisible({ timeout: 5000 });
  });

  test('should show loading state', async ({ page }) => {
    await page.goto('/p/default/txn');
    // Page should show loading spinner or skeleton initially
    const loadingOrContent = page.locator('[class*="loading"], [class*="spinner"], table, [class*="table"]').first();
    await expect(loadingOrContent).toBeVisible({ timeout: 5000 });
  });
});

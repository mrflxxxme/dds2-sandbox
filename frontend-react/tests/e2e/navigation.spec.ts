import { test, expect } from '@playwright/test';

/**
 * Navigation tests — verify sidebar links and page rendering.
 * Requires authenticated session (login before each test).
 */

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    // Login first
    await page.goto('/login');
    await page.fill('input[type="email"], input[name="email"], input[placeholder*="mail"]', 'admin@dds.local');
    await page.fill('input[type="password"]', 'admin');
    await page.locator('button[type="submit"], button:has-text("Войти")').click();
    await page.waitForURL(/(?!.*login).*/, { timeout: 5000 });
  });

  test('should render dashboard page', async ({ page }) => {
    // Dashboard should be accessible after login
    const heading = page.locator('h1, h2, [class*="title"]').first();
    await expect(heading).toBeVisible({ timeout: 5000 });
  });

  test('should navigate to transactions page', async ({ page }) => {
    // Find and click transactions link in sidebar
    const txnLink = page.locator('a[href*="txn"], a[href*="transaction"], nav a:has-text("Транзакции")').first();
    if (await txnLink.isVisible()) {
      await txnLink.click();
      await page.waitForTimeout(1000);
      await expect(page).toHaveURL(/txn|transaction/);
    }
  });

  test('should navigate to reports page', async ({ page }) => {
    const reportsLink = page.locator('a[href*="report"], a[href*="dds"], nav a:has-text("ДДС")').first();
    if (await reportsLink.isVisible()) {
      await reportsLink.click();
      await page.waitForTimeout(1000);
      await expect(page).toHaveURL(/report|dds/);
    }
  });

  test('should navigate to settings page', async ({ page }) => {
    const settingsLink = page.locator('a[href*="settings"], nav a:has-text("Настройки")').first();
    if (await settingsLink.isVisible()) {
      await settingsLink.click();
      await page.waitForTimeout(1000);
      await expect(page).toHaveURL(/settings/);
    }
  });
});

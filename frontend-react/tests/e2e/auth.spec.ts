import { test, expect } from '@playwright/test';

test.describe('Auth Flow', () => {
  test('should show login page', async ({ page }) => {
    await page.goto('/login');
    // Login page should have email and password fields
    await expect(page.locator('input[type="email"], input[name="email"], input[placeholder*="mail"]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
  });

  test('should reject invalid credentials', async ({ page }) => {
    await page.goto('/login');
    // Fill in wrong credentials
    await page.fill('input[type="email"], input[name="email"], input[placeholder*="mail"]', 'wrong@example.com');
    await page.fill('input[type="password"]', 'wrongpassword');
    // Submit
    await page.locator('button[type="submit"], button:has-text("Войти")').click();
    // Should show error or stay on login page
    await page.waitForTimeout(1000);
    await expect(page).toHaveURL(/login/);
  });

  test('should login with valid credentials', async ({ page }) => {
    await page.goto('/login');
    // Use default admin credentials
    await page.fill('input[type="email"], input[name="email"], input[placeholder*="mail"]', 'admin@dds.local');
    await page.fill('input[type="password"]', 'admin');
    await page.locator('button[type="submit"], button:has-text("Войти")').click();
    // Should redirect away from login
    await page.waitForURL(/(?!.*login).*/, { timeout: 5000 });
    await expect(page).not.toHaveURL(/login/);
  });
});

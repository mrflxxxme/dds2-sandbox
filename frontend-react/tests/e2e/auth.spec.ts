import { test, expect } from '@playwright/test';

/**
 * Auth page tests.
 * Note: actual login flow is tested by global-setup.ts (with retry for rate limit).
 * These tests verify login page structure without making auth API calls,
 * because nginx rate-limits /api/v1/auth/* at 5r/m and chromium tests
 * consume the budget via GET /api/v1/auth/me on every page load.
 */

test.describe('Auth Page', () => {
  test('should show login page with correct fields', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('.auth-logo')).toHaveText('DDS');
    await expect(page.locator('.auth-title')).toContainText('Добро пожаловать');
    await expect(page.locator('.auth-subtitle')).toContainText('Войдите в свой аккаунт');
    await expect(page.locator('input[type="text"]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toContainText('Войти');
  });

  test('should show register link', async ({ page }) => {
    await page.goto('/login');
    const registerLink = page.locator('a[href="/register"]');
    await expect(registerLink).toBeVisible();
    await expect(registerLink).toContainText('Зарегистрироваться');
  });

  test('should have correct form labels', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('.form-label').first()).toContainText('Логин или email');
    await expect(page.locator('.form-label').nth(1)).toContainText('Пароль');
  });

  test('should have password field hidden', async ({ page }) => {
    await page.goto('/login');
    const pwdInput = page.locator('input[type="password"]');
    await expect(pwdInput).toHaveAttribute('placeholder', 'Введите пароль');
  });
});

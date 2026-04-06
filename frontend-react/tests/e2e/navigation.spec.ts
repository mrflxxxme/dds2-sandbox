import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad } from './helpers';

test.describe('Navigation', () => {
  test('should render sidebar with logo and project name', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    await expect(page.locator('.sidebar-logo')).toHaveText('DDS');
    await expect(page.locator('.project-selector')).toBeVisible();
  });

  test('should render sidebar nav groups', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    const sections = page.locator('.sidebar-section');
    await expect(sections.filter({ hasText: 'Финансы' })).toBeVisible();
    await expect(sections.filter({ hasText: 'Склад' })).toBeVisible();
    await expect(sections.filter({ hasText: 'Настройки' })).toBeVisible();
  });

  test('should navigate to transactions page', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    await page.click('a.sidebar-link:has-text("Операции")');
    await page.waitForURL(/\/txn/);
    await waitForPageLoad(page);
    await expect(page.locator('.page-title')).toContainText('Операции');
  });

  test('should navigate to reports page', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    await page.click('a.sidebar-link:has-text("Отчёты")');
    await page.waitForURL(/\/reports/);
    await waitForPageLoad(page);
    await expect(page.locator('.page-title')).toContainText('Отчёты');
  });

  test('should navigate to settings page', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    await page.click('a.sidebar-link:has-text("Настройка проекта")');
    await page.waitForURL(/\/settings/);
    await waitForPageLoad(page);
    await expect(page.locator('.page-title')).toContainText('Настройки проекта');
  });

  test('should show user info in sidebar', async ({ page }) => {
    await page.goto(projectUrl());
    await waitForPageLoad(page);
    await expect(page.locator('.sidebar-user')).toBeVisible();
    await expect(page.locator('.sidebar-avatar')).toBeVisible();
  });
});

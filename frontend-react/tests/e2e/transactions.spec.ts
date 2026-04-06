import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad, expectPageTitle } from './helpers';

test.describe('Transactions', () => {
  test('should load transactions page', async ({ page }) => {
    await page.goto(projectUrl('/txn'));
    await waitForPageLoad(page);
    await expectPageTitle(page, 'Операции');
  });

  test('should display table or empty state', async ({ page }) => {
    await page.goto(projectUrl('/txn'));
    await waitForPageLoad(page);
    const table = page.locator('table');
    const emptyState = page.locator('.empty-state');
    await expect(table.or(emptyState).first()).toBeVisible({ timeout: 10_000 });
  });

  test('should have export button when data exists or empty state', async ({ page }) => {
    await page.goto(projectUrl('/txn'));
    await waitForPageLoad(page);
    // Export button only visible when data.length > 0; empty state otherwise
    const exportBtn = page.locator('button:has-text("Excel")');
    const emptyState = page.locator('.empty-state');
    await expect(exportBtn.or(emptyState).first()).toBeVisible({ timeout: 10_000 });
  });

  test('should have date filter inputs', async ({ page }) => {
    await page.goto(projectUrl('/txn'));
    await waitForPageLoad(page);
    const dateInputs = page.locator('input[type="date"]');
    await expect(dateInputs.first()).toBeVisible({ timeout: 10_000 });
  });
});

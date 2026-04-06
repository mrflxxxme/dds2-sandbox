import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad, expectNoAuthError } from './helpers';

test.describe('Team', () => {
  test('should load team management page', async ({ page }) => {
    await page.goto(projectUrl('/team'));
    await waitForPageLoad(page);
    await expectNoAuthError(page);
  });

  test('should display team members', async ({ page }) => {
    await page.goto(projectUrl('/team'));
    await waitForPageLoad(page);
    const content = page.locator('table, .glass-card');
    await expect(content.first()).toBeVisible({ timeout: 10_000 });
  });

  test('should show role labels in table', async ({ page }) => {
    await page.goto(projectUrl('/team'));
    await waitForPageLoad(page);
    // RoleBadge is an inline-styled <span> inside a table cell
    const roleBadge = page.locator('td:has-text("Владелец")').or(page.locator('td:has-text("Админ")'));
    await expect(roleBadge.first()).toBeVisible({ timeout: 10_000 });
  });
});

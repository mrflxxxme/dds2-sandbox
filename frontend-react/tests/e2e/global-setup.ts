import { chromium, FullConfig } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const AUTH_DIR = path.join(__dirname, '.auth');

export default async function globalSetup(config: FullConfig) {
  const baseURL = (config as any).use?.baseURL || process.env.BASE_URL || 'http://localhost';

  // Ensure .auth directory exists
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({ baseURL });

  // Login with retry (handles nginx 503 rate limit from previous runs)
  let loggedIn = false;
  for (let attempt = 0; attempt < 5; attempt++) {
    if (attempt > 0) {
      console.log(`[global-setup] Login attempt ${attempt + 1}, waiting 15s for rate limit...`);
      await page.waitForTimeout(15_000);
    }
    await page.goto('/login');
    await page.fill('input[type="text"]', process.env.E2E_USERNAME || 'admin');
    await page.fill('input[type="password"]', process.env.E2E_PASSWORD || 'admin');
    await page.click('button[type="submit"]');

    const didRedirect = await page.waitForURL('**/projects', { timeout: 8_000 }).then(() => true).catch(() => false);
    if (didRedirect) {
      loggedIn = true;
      break;
    }
  }
  if (!loggedIn) throw new Error('Global setup: login failed after 5 attempts');

  // Pick first project (or create one if none exist)
  const emptyState = page.locator('.empty-state');
  if (await emptyState.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await page.click('button:has-text("Создать первый проект")');
    await page.fill('input.form-input', 'E2E Test Project');
    await page.click('button:has-text("Создать")');
    await page.locator('.glass-card').first().waitFor({ state: 'visible', timeout: 10_000 });
  }

  // Click first project card
  const projectCard = page.locator('.glass-card').first();
  await projectCard.click();
  await page.waitForURL(/\/p\/[^/]+/, { timeout: 10_000 });

  // Extract slug from URL
  const url = page.url();
  const match = url.match(/\/p\/([^/]+)/);
  const slug = match ? match[1] : 'default';

  // Save context for tests
  fs.writeFileSync(
    path.join(AUTH_DIR, 'test-context.json'),
    JSON.stringify({ slug, baseURL }),
  );

  // Save storage state (localStorage + cookies)
  await page.context().storageState({ path: path.join(AUTH_DIR, 'user.json') });

  await browser.close();
}

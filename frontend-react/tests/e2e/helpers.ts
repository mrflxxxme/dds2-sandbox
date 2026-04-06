import { Page, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const CONTEXT_PATH = path.join(__dirname, '.auth', 'test-context.json');

let _ctx: { slug: string; baseURL: string } | null = null;

function getContext() {
  if (!_ctx) {
    _ctx = JSON.parse(fs.readFileSync(CONTEXT_PATH, 'utf-8'));
  }
  return _ctx!;
}

export function getSlug(): string {
  return getContext().slug;
}

export function projectUrl(subpath: string = ''): string {
  const slug = getSlug();
  return `/p/${slug}${subpath}`;
}

/** Wait for page content to load (animate-in wrapper or glass-card) */
export async function waitForPageLoad(page: Page, timeout = 15_000) {
  await page.locator('.animate-in, .glass-card, main').first().waitFor({
    state: 'visible',
    timeout,
  });
}

/** Assert page title contains expected text */
export async function expectPageTitle(page: Page, text: string) {
  await expect(page.locator('.page-title').first()).toContainText(text, { timeout: 10_000 });
}

/** Assert page did not redirect to login or show access error */
export async function expectNoAuthError(page: Page) {
  expect(page.url()).not.toContain('/login');
  await expect(page.locator('text=Нет доступа')).not.toBeVisible({ timeout: 2_000 });
}

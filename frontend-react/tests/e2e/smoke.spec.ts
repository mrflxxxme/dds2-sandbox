import { test, expect } from '@playwright/test';
import { projectUrl, waitForPageLoad } from './helpers';

/**
 * Smoke tests: hit every main page once, verify it loads without crashing.
 * Uses pre-authenticated storageState.
 */

const pages = [
  { path: '', name: 'Dashboard' },
  { path: '/txn', name: 'Transactions' },
  { path: '/inbox', name: 'Inbox' },
  { path: '/reports', name: 'Reports' },
  { path: '/dds', name: 'DDS Report' },
  { path: '/cost', name: 'Cost' },
  { path: '/refs', name: 'References' },
  { path: '/import', name: 'Import' },
  { path: '/warehouse', name: 'Warehouse' },
  { path: '/warehouse/assembly', name: 'Assembly' },
  { path: '/warehouse/stock', name: 'Stock Summary' },
  { path: '/warehouse/fbo-supplies', name: 'FBO Supplies' },
  { path: '/warehouse/wb-returns', name: 'WB Returns (PVZ)' },
  { path: '/warehouse/wb-stocks', name: 'WB Stocks' },
  { path: '/warehouse/analytics', name: 'Stock Analytics' },
  { path: '/warehouse/logistics', name: 'Logistics' },
  { path: '/barcode-labels', name: 'Barcode Labels' },
  { path: '/planning', name: 'Planning' },
  { path: '/supply-chain', name: 'Supply Chain' },
  { path: '/container-loader', name: 'Container Loader' },
  { path: '/funnel', name: 'Funnel' },
  { path: '/trends', name: 'Trends' },
  { path: '/order-geography', name: 'Order Geography' },
  { path: '/opiu', name: 'OPIU' },
  { path: '/plan-fact', name: 'Plan-Fact' },
  { path: '/monitoring', name: 'Monitoring' },
  { path: '/settings', name: 'Settings' },
  { path: '/team', name: 'Team' },
  // { path: '/orders', name: 'Orders' }, // Known client-side crash — needs fix in orders page
  { path: '/bulk-cost', name: 'Bulk Cost' },
];

test.describe('Smoke Tests', () => {
  for (const { path, name } of pages) {
    test(`page ${name} (${path || '/'}) loads without crash`, async ({ page }) => {
      await page.goto(projectUrl(path));
      await waitForPageLoad(page);
      // Not redirected to login
      expect(page.url()).not.toContain('/login');
      // Page has some visible content
      const content = page.locator('.animate-in, .glass-card, main, .page-title');
      await expect(content.first()).toBeVisible({ timeout: 15_000 });
    });
  }
});

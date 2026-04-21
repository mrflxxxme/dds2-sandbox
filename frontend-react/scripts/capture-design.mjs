import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AUTH_PATH = path.join(__dirname, '..', 'tests', 'e2e', '.auth', 'user.json');
const CTX_PATH = path.join(__dirname, '..', 'tests', 'e2e', '.auth', 'test-context.json');
const OUT_DIR = process.env.OUT_DIR || path.join(process.env.HOME, 'Desktop', `dds-design-${new Date().toISOString().slice(0, 10)}`);
const BASE_URL = process.env.BASE_URL || 'http://localhost';

const { slug } = JSON.parse(fs.readFileSync(CTX_PATH, 'utf-8'));

const mainPages = [
  ['', '01-dashboard'],
  ['/txn', '02-transactions'],
  ['/inbox', '03-inbox'],
  ['/reports', '04-reports'],
  ['/dds', '05-dds-report'],
  ['/cost', '06-cost'],
  ['/bulk-cost', '07-bulk-cost'],
  ['/refs', '08-references'],
  ['/import', '09-import'],
  ['/warehouse', '10-warehouse'],
  ['/warehouse/assembly', '11-warehouse-assembly'],
  ['/warehouse/stock', '12-warehouse-stock'],
  ['/warehouse/fbo-supplies', '13-warehouse-fbo-supplies'],
  ['/warehouse/wb-stocks', '14-warehouse-wb-stocks'],
  ['/warehouse/analytics', '15-warehouse-analytics'],
  ['/warehouse/logistics', '16-warehouse-logistics'],
  ['/planning', '17-planning'],
  ['/supply-chain', '18-supply-chain'],
  ['/container-loader', '19-container-loader'],
  ['/funnel', '20-funnel'],
  ['/trends', '21-trends'],
  ['/order-geography', '22-order-geography'],
  ['/opiu', '23-opiu'],
  ['/plan-fact', '24-plan-fact'],
  ['/monitoring', '25-monitoring'],
  ['/settings', '26-settings'],
  ['/team', '27-team'],
  ['/ai-chat', '28-ai-chat'],
];

const tmaPages = [
  ['/tma/' + slug, 'tma-01-home'],
  ['/tma/' + slug + '/capital', 'tma-02-capital'],
  ['/tma/' + slug + '/chat', 'tma-03-chat'],
  ['/tma/' + slug + '/funnel', 'tma-04-funnel'],
  ['/tma/' + slug + '/pnl', 'tma-05-pnl'],
  ['/tma/' + slug + '/pulse', 'tma-06-pulse'],
  ['/tma/' + slug + '/warehouse', 'tma-07-warehouse'],
];

fs.mkdirSync(OUT_DIR, { recursive: true });
fs.mkdirSync(path.join(OUT_DIR, 'main'), { recursive: true });
fs.mkdirSync(path.join(OUT_DIR, 'tma'), { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  storageState: AUTH_PATH,
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
});

const results = { ok: [], fail: [] };

async function capture(subpath, name, subdir, fullPage = true, viewport = null) {
  const url = `${BASE_URL}/p/${slug}${subpath}`;
  const isTma = subpath.startsWith('/tma/');
  const finalUrl = isTma ? `${BASE_URL}${subpath}` : url;
  const page = await context.newPage();
  if (viewport) await page.setViewportSize(viewport);
  try {
    await page.goto(finalUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    try {
      await page.locator('.animate-in, .glass-card, main').first().waitFor({ state: 'visible', timeout: 10_000 });
    } catch {}
    await page.waitForTimeout(1500);
    const file = path.join(OUT_DIR, subdir, `${name}.png`);
    await page.screenshot({ path: file, fullPage });
    results.ok.push(name);
    console.log(`OK  ${name}`);
  } catch (e) {
    results.fail.push({ name, err: e.message });
    console.log(`ERR ${name}: ${e.message}`);
  } finally {
    await page.close();
  }
}

console.log(`Output: ${OUT_DIR}`);
console.log(`Main pages: ${mainPages.length}, TMA: ${tmaPages.length}`);

for (const [p, n] of mainPages) {
  await capture(p, n, 'main');
}

for (const [p, n] of tmaPages) {
  await capture(p, n, 'tma', true, { width: 430, height: 900 });
}

await browser.close();

fs.writeFileSync(path.join(OUT_DIR, 'summary.json'), JSON.stringify(results, null, 2));
console.log(`\nDone. OK=${results.ok.length} FAIL=${results.fail.length}`);
console.log(`Saved to: ${OUT_DIR}`);

import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { ADMIN, DRIVER, STUDENT, login } from './helpers';

/** WCAG 2.1 AA automated checks. The app's users are PwD students, so this is a release gate. */
async function audit(page: import('@playwright/test').Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .exclude('.leaflet-container') // third-party map canvas; its info is duplicated as text
    .analyze();
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  expect(serious.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
}

test('login and sign-up pages', async ({ page }) => {
  await page.goto('/login');
  await audit(page);
  await page.goto('/register');
  await audit(page);
});

for (const [name, who, path] of [
  ['rider booking', STUDENT, '/rider'],
  ['rider verification', STUDENT, '/rider/verification'],
  ['driver console', DRIVER, '/driver'],
  ['admin overview', ADMIN, '/admin'],
  ['admin fleet', ADMIN, '/admin/fleet'],
] as const) {
  test(name, async ({ page }) => {
    await login(page, who);
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    await audit(page);
  });
}

// Regression: Angular's critical-CSS inlining once left the app unstyled under our strict CSP.
test('global styles load and nothing violates the Content-Security-Policy', async ({ page }) => {
  const violations: string[] = [];
  page.on('console', (msg) => {
    if (/Content.Security.Policy/i.test(msg.text())) violations.push(msg.text());
  });
  await login(page, STUDENT);
  await page.waitForLoadState('networkidle');
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(bg).not.toBe('rgba(0, 0, 0, 0)');
  await expect(page.locator('.leaflet-tile-loaded').first()).toBeVisible();
  expect(violations).toEqual([]);
});

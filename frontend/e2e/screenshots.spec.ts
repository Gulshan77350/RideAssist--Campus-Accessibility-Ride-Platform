import { expect, test } from '@playwright/test';

import { ADMIN, DRIVER, STUDENT, login, resetDemoState } from './helpers';

// README screenshots. Opt-in:  SCREENSHOTS=1 npx playwright test screenshots
test.skip(!process.env['SCREENSHOTS'], 'set SCREENSHOTS=1 to regenerate docs/screenshots');
const OUT = '../docs/screenshots';

test('capture key screens', async ({ browser }) => {
  await resetDemoState();
  const viewport = { width: 1366, height: 820 };
  const driver = await (await browser.newContext({ viewport })).newPage();
  const rider = await (await browser.newContext({ viewport })).newPage();

  await rider.goto('/login');
  await rider.screenshot({ path: `${OUT}/login.png` });

  await login(driver, DRIVER);
  await driver.getByRole('button', { name: 'Go online' }).click();

  await login(rider, STUDENT);
  await expect(rider.getByText(/1 free shuttle nearby/)).toBeVisible({ timeout: 20_000 });
  const map = rider.locator('.leaflet-container');
  const box = (await map.boundingBox())!;
  await map.click({ position: { x: box.width / 2 - 120, y: box.height / 2 - 60 } });
  await map.click({ position: { x: box.width / 2 + 140, y: box.height / 2 + 90 } });
  await rider.getByLabel('Pickup landmark').fill('Girls Hostel 1, side gate');
  await rider.getByLabel('Drop landmark').fill('Central Library');
  await rider.waitForTimeout(1500); // tiles
  await rider.screenshot({ path: `${OUT}/rider-booking.png` });
  await rider.getByRole('button', { name: 'Request ride' }).click();

  await expect(driver.getByRole('alertdialog')).toBeVisible({ timeout: 20_000 });
  await driver.waitForTimeout(1000);
  await driver.screenshot({ path: `${OUT}/driver-offer.png` });
  await driver.getByRole('button', { name: 'Accept' }).click();

  await expect(rider.getByRole('heading', { name: 'Driver on the way' })).toBeVisible();
  await rider.waitForTimeout(6000); // let the simulated driver move along the route
  await rider.screenshot({ path: `${OUT}/rider-tracking.png` });

  const admin = await (await browser.newContext({ viewport })).newPage();
  await login(admin, ADMIN);
  await admin.waitForTimeout(1000);
  await admin.screenshot({ path: `${OUT}/admin-overview.png` });
  await admin.getByRole('link', { name: 'Live rides' }).click();
  await admin.waitForTimeout(2500);
  await admin.screenshot({ path: `${OUT}/admin-live-rides.png` });

  await resetDemoState();
});

test('capture mobile rider screen', async ({ browser }) => {
  const page = await (await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })).newPage();
  await login(page, STUDENT);
  await expect(page.getByRole('heading', { name: 'Where to?' })).toBeVisible();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/mobile-rider.png`, fullPage: true });
});

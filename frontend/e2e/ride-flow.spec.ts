import { expect, test } from '@playwright/test';

import { DRIVER, STUDENT, login, resetDemoState } from './helpers';

test.beforeEach(resetDemoState);

test('rider books, driver accepts, live tracking, completion and rating', async ({ browser }) => {
  // Two independent browser sessions talking through the real backend.
  const driverCtx = await browser.newContext({ permissions: [] });
  const riderCtx = await browser.newContext();
  const driver = await driverCtx.newPage();
  const rider = await riderCtx.newPage();

  await login(driver, DRIVER);
  await expect(driver.getByRole('heading', { name: 'You are offline' })).toBeVisible();
  await driver.getByRole('button', { name: 'Go online' }).click();
  await expect(driver.getByRole('heading', { name: 'You are online' })).toBeVisible();
  await expect(driver.getByText('Waiting for ride requests…')).toBeVisible();

  await login(rider, STUDENT);
  await expect(rider.getByRole('heading', { name: 'Where to?' })).toBeVisible();
  await expect(rider.getByText(/1 free shuttle nearby/)).toBeVisible({ timeout: 20_000 });

  const map = rider.locator('.leaflet-container');
  const box = (await map.boundingBox())!;
  await map.click({ position: { x: box.width / 2 - 60, y: box.height / 2 - 40 } }); // pickup
  await map.click({ position: { x: box.width / 2 + 80, y: box.height / 2 + 60 } }); // drop
  await rider.getByLabel('Pickup landmark').fill('Girls Hostel side gate');
  await rider.getByLabel('Drop landmark').fill('Library');
  await expect(rider.getByLabel('I need a wheelchair-accessible vehicle')).toBeChecked(); // from saved prefs
  await rider.getByRole('button', { name: 'Request ride' }).click();
  await expect(rider.getByRole('heading', { name: 'Finding driver' })).toBeVisible();

  // Driver receives the timed offer with the rider's accessibility note.
  const offer = driver.getByRole('alertdialog', { name: 'New ride request' });
  await expect(offer).toBeVisible({ timeout: 20_000 });
  await expect(offer.getByText(/ramp needed/i)).toBeVisible();
  await offer.getByRole('button', { name: 'Accept' }).click();
  await expect(driver.getByRole('heading', { name: 'Head to pickup' })).toBeVisible();

  // Rider sees the assignment pushed over the WebSocket, then the driver pin moving.
  await expect(rider.getByRole('heading', { name: 'Driver on the way' })).toBeVisible();
  await expect(rider.getByText('Ramesh Yadav', { exact: true })).toBeVisible();
  await expect(rider.locator('.map-pin.driver')).toBeVisible();
  const before = await rider.locator('.map-pin.driver').boundingBox();
  await expect
    .poll(async () => JSON.stringify(await rider.locator('.map-pin.driver').boundingBox()), { timeout: 20_000 })
    .not.toBe(JSON.stringify(before));

  await driver.getByRole('button', { name: /Rider on board/ }).click();
  await expect(driver.getByRole('heading', { name: 'Drive to drop-off' })).toBeVisible();
  await expect(rider.getByRole('heading', { name: 'On board' })).toBeVisible();

  await driver.waitForTimeout(4000); // let a few simulated GPS points land on the trail
  await driver.getByRole('button', { name: 'Complete ride' }).click();
  await expect(rider.getByRole('heading', { name: /You've arrived/ })).toBeVisible();

  await rider.getByText('5 stars').click({ force: true });
  await rider.getByRole('button', { name: 'Submit rating' }).click();
  await expect(rider.getByText('Thanks for the feedback!')).toBeVisible();
  await expect(rider.getByRole('heading', { name: 'Where to?' })).toBeVisible();

  await rider.getByRole('link', { name: 'My rides' }).click();
  await expect(rider.getByText('Completed').first()).toBeVisible();

  await driver.getByRole('button', { name: 'Skip' }).click();
  await driver.getByRole('button', { name: 'Go offline' }).click();
  await driverCtx.close();
  await riderCtx.close();
});

test('rider can cancel while searching', async ({ page }) => {
  await login(page, STUDENT);
  const map = page.locator('.leaflet-container');
  const box = (await map.boundingBox())!;
  await map.click({ position: { x: box.width / 2, y: box.height / 2 } });
  await map.click({ position: { x: box.width / 2 + 50, y: box.height / 2 + 50 } });
  await page.getByRole('button', { name: 'Request ride' }).click();
  await expect(page.getByRole('heading', { name: 'Finding driver' })).toBeVisible();
  page.once('dialog', (d) => d.accept());
  await page.getByRole('button', { name: 'Cancel ride' }).click();
  await expect(page.getByRole('heading', { name: 'Where to?' })).toBeVisible();
});

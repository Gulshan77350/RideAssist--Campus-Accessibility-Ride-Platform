import { expect, test } from '@playwright/test';

import { ADMIN, latestOtp, login } from './helpers';

// A tiny valid PNG (1x1) so the server's magic-byte check accepts it.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);

test('new student: sign up → OTP → upload UDID → admin approves → can book', async ({ browser }) => {
  const suffix = Date.now().toString().slice(-7);
  const email = `e2e${suffix}@iiita.ac.in`;
  const student = await (await browser.newContext()).newPage();

  await student.goto('/register');
  await student.getByLabel('College email').fill(email);
  await student.getByLabel('College email').blur();
  await expect(student.getByText('✓ IIIT Allahabad')).toBeVisible();
  await student.getByLabel('Enrollment / roll number').fill(`E2E${suffix}`);
  await student.getByLabel('Full name (as in college records)').fill('Test Student');
  await student.getByLabel('Password').fill('Passw0rd!23');
  await student.getByRole('button', { name: 'Continue' }).click();

  await expect(student.getByRole('heading', { name: 'Check your college inbox' })).toBeVisible();
  await student.getByLabel('6-digit code').fill(await latestOtp(email));
  await student.getByRole('button', { name: 'Verify' }).click();

  // Not on the PwD roster, so a document is needed.
  await expect(student.getByRole('heading', { name: 'PwD verification' })).toBeVisible();
  await expect(student.getByText('Document needed')).toBeVisible();
  await student.getByLabel('File').setInputFiles({ name: 'udid.png', mimeType: 'image/png', buffer: PNG });
  await student.getByRole('button', { name: 'Submit for review' }).click();
  await expect(student.getByText('Under review')).toBeVisible();

  // The booking gate is closed meanwhile.
  await student.getByRole('link', { name: 'Book a ride' }).click();
  await expect(student.getByText("You can't book rides yet.")).toBeVisible();

  // Disability-cell officer reviews it.
  const admin = await (await browser.newContext()).newPage();
  await login(admin, ADMIN);
  await admin.getByRole('link', { name: 'Verifications' }).click();
  const card = admin.locator('li.card', { hasText: email });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: /UDID card/ }).click();
  await expect(admin.getByRole('dialog', { name: 'Document preview' }).getByRole('img')).toBeVisible();
  await admin.getByRole('button', { name: 'Close' }).click();
  await card.getByRole('button', { name: 'Approve' }).click();
  await expect(admin.getByText('Test Student verified')).toBeVisible();

  await student.getByRole('link', { name: 'Verification' }).click();
  await expect(student.getByText('Verified', { exact: true })).toBeVisible();
  await student.getByRole('link', { name: 'Book a ride' }).click();
  await expect(student.getByText("You can't book rides yet.")).toHaveCount(0);
});

test('admin console pages load with live data', async ({ page }) => {
  await login(page, ADMIN);
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
  await expect(page.getByText('Rides today')).toBeVisible();
  await page.getByRole('link', { name: 'Fleet' }).click();
  await expect(page.getByRole('cell', { name: 'UP70EV0001', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Live rides' }).click();
  await expect(page.getByRole('heading', { name: 'Live rides' })).toBeVisible();
  await page.getByRole('link', { name: 'Anomalies' }).click();
  await expect(page.getByRole('heading', { name: 'Anomalies' })).toBeVisible();
});

test('role guards keep riders out of the admin console', async ({ page }) => {
  await login(page, { email: 'iit2023004@iiita.ac.in', password: 'Student@123' });
  await page.goto('/admin/fleet');
  await expect(page).toHaveURL(/\/rider$/);
});

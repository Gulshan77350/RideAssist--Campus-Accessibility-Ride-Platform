import { APIRequestContext, Page, expect, request } from '@playwright/test';

export const STUDENT = { email: 'iit2023004@iiita.ac.in', password: 'Student@123' };
export const DRIVER = { email: 'ramesh.driver@iiita.ac.in', password: 'Driver@12345' };
export const ADMIN = { email: 'admin@iiita.ac.in', password: 'Admin@12345' };
const MAILPIT = process.env['MAILPIT_URL'] ?? 'http://localhost:8025';

export async function login(page: Page, who: { email: string; password: string }): Promise<void> {
  await page.goto('/login');
  await page.getByLabel('Email').fill(who.email);
  await page.getByLabel('Password').fill(who.password);
  await page.getByRole('button', { name: 'Log in' }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function apiAs(who: { email: string; password: string }): Promise<APIRequestContext> {
  const anon = await request.newContext({ baseURL: process.env['BASE_URL'] ?? 'http://localhost:8080' });
  const resp = await anon.post('/api/v1/auth/token', { form: { username: who.email, password: who.password } });
  const { access_token } = await resp.json();
  await anon.dispose();
  return request.newContext({
    baseURL: process.env['BASE_URL'] ?? 'http://localhost:8080',
    extraHTTPHeaders: { Authorization: `Bearer ${access_token}` },
  });
}

/** Leaves the shared demo accounts in a clean state so tests are re-runnable. */
export async function resetDemoState(): Promise<void> {
  const rider = await apiAs(STUDENT);
  const active = await rider.get('/api/v1/rides/active');
  if (active.status() === 200) {
    const ride = await active.json();
    if (ride.status === 'ongoing') {
      const driver = await apiAs(DRIVER);
      await driver.post(`/api/v1/rides/${ride.id}/complete`);
      await driver.dispose();
    } else {
      await rider.post(`/api/v1/rides/${ride.id}/cancel`, { data: {} });
    }
  }
  await rider.dispose();
  for (const d of [DRIVER, { email: 'suresh.driver@iiita.ac.in', password: 'Driver@12345' }]) {
    const api = await apiAs(d);
    await api.post('/api/v1/drivers/me/offline');
    await api.dispose();
  }
}

export async function latestOtp(email: string): Promise<string> {
  const ctx = await request.newContext();
  try {
    for (let i = 0; i < 30; i++) {
      const found = await (await ctx.get(`${MAILPIT}/api/v1/search`, { params: { query: `to:${email}` } })).json();
      if (found.messages?.length) {
        const msg = await (await ctx.get(`${MAILPIT}/api/v1/message/${found.messages[0].ID}`)).json();
        return /\b(\d{6})\b/.exec(msg.Text)![1];
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error(`no OTP email for ${email}`);
  } finally {
    await ctx.dispose();
  }
}

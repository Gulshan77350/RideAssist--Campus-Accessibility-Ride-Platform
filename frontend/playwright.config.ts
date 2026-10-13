import { defineConfig, devices } from '@playwright/test';

/** E2E tests run against the full docker-compose stack (gateway on :8080). */
export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1, // tests share seeded accounts (one demo rider, one accessible-vehicle driver)
  reporter: [['list']],
  use: {
    baseURL: process.env['BASE_URL'] ?? 'http://localhost:8080',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], channel: process.env['PW_CHANNEL'] ?? 'chromium' } }],
});

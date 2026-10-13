# RideAssist web app

Angular 20 app with rider, driver and admin consoles. See the [root README](../README.md) for the
full project.

```bash
npm install
npm start              # http://localhost:4200, proxies /api (incl. WebSockets) to the gateway on :8080
npm run build          # production build → dist/frontend/browser (the gateway image does this for you)
npm run e2e            # Playwright against http://localhost:8080 (BASE_URL to override)
SCREENSHOTS=1 npx playwright test screenshots   # regenerate ../docs/screenshots
```

The backend stack must be running (`docker compose up -d` from the repo root) for `npm start` and
the E2E tests.

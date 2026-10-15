import process from 'node:process'

import { defineConfig, devices } from '@playwright/test'

import base from './playwright.config'

const port = Number(process.env.PYRIT_E2E_ANALYTICS_PORT ?? '18496')
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('PYRIT_E2E_ANALYTICS_PORT must be a valid TCP port.')
}
const url = `http://127.0.0.1:${port}`

export default defineConfig({
  ...base,
  testMatch: 'analytics.spec.ts',
  testIgnore: [],
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  use: {
    ...base.use,
    baseURL: url,
    storageState: {
      cookies: [],
      origins: [{ origin: url, localStorage: [{ name: 'pyrit-tour-completed', value: 'true' }] }],
    },
  },
  projects: [{
    name: 'analytics',
    use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1050 } },
  }],
  webServer: {
    command: `cd .. && uv run --no-sync python -m frontend.e2e.fixtures.analytics_backend --port ${port}`,
    env: { PYTHONIOENCODING: 'utf-8' },
    url: `${url}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})

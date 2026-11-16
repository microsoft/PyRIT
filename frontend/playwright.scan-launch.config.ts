import path from 'node:path'
import process from 'node:process'

import { defineConfig, devices } from '@playwright/test'

import type { UserPreferences } from './src/types'

import base from './playwright.config'

const frontendPort = Number(process.env.E2E_FRONTEND_PORT ?? '18723')
const backendPort = Number(process.env.PYRIT_E2E_BACKEND_PORT ?? '18724')
for (const port of [frontendPort, backendPort]) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('Scan-launch frontend and backend ports must be valid TCP ports.')
  }
}
const frontendUrl = `http://127.0.0.1:${frontendPort}`
const backendUrl = `http://127.0.0.1:${backendPort}`

// Missing-role cases need a fresh registry, not the shared seeded shard's named defaults.
export default defineConfig({
  ...base,
  testMatch: 'scan-launch.seeded.spec.ts',
  testIgnore: [],
  fullyParallel: false,
  workers: 1,
  use: {
    ...base.use,
    baseURL: frontendUrl,
    storageState: {
      cookies: [],
      origins: [{
        origin: frontendUrl,
        localStorage: [
          { name: 'pyrit-tour-completed', value: 'true' },
          {
            name: 'pyrit.userPreferences.v1.local',
            value: JSON.stringify({
              targets: { adversarial: null },
              hiddenTargetRegistryNames: [],
              labels: {},
              theme: 'system',
              chatMarkdown: false,
              operation: null,
            } satisfies UserPreferences),
          },
        ],
      }],
    },
  },
  projects: [{ name: 'scan-launch', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'cd .. && uv run --no-sync python -m pyrit.backend.pyrit_backend '
        + `--auth-mode local --host 127.0.0.1 --port ${backendPort} --log-level warning `
        + `--config-file ${path.join('tests', 'end_to_end', 'browser_config.yaml')}`,
      env: { PYRIT_DEV_MODE: 'true' },
      url: `${backendUrl}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx vite preview --host 127.0.0.1 --port ${frontendPort} --strictPort`,
      env: { PYRIT_BACKEND_URL: backendUrl },
      url: frontendUrl,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})

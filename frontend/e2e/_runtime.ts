import { expect, type Page } from '@playwright/test'

import type { RuntimeReadiness, RuntimeStatus } from '../src/types'

export async function waitForAppReady(page: Page): Promise<void> {
  await expect(page.getByRole('region', { name: 'Default Labels', exact: true })).toBeVisible()
  await expect(page.getByText(/^PyRIT runtime:/)).toHaveCount(0)
  await expect(page.getByText(
    'New attacks and scans are unavailable until default labels are loaded.', { exact: false },
  )).toHaveCount(0)
}

export const READY_RUNTIME: RuntimeReadiness = {
  ready: true,
  state: 'ready',
  generation: 'e2e-runtime',
}

export const READY_RUNTIME_STATUS: RuntimeStatus = {
  state: 'ready',
  generation: READY_RUNTIME.generation,
  version: 'e2e-config',
  enabled: true,
  applying: false,
  outcome: 'success',
  message: 'PyRIT is ready.',
}

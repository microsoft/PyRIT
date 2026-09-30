import { expect, test, type Locator, type Page } from '@playwright/test'

import type { RegisteredScenario } from '../src/types'

import { READY_RUNTIME } from './_runtime'
import { makeTarget } from './_targets'

const SCENARIO: RegisteredScenario = {
  scenario_name: 'numeric-controls',
  scenario_type: 'NumericControlsScenario',
  scenario_version: 1,
  description: 'Mock scenario for numeric input interactions.',
  description_markdown: 'Mock scenario for numeric input interactions.',
  all_techniques: ['prompt_sending'],
  aggregate_techniques: [],
  aggregate_technique_expansions: {},
  default_technique: 'prompt_sending',
  default_techniques: ['prompt_sending'],
  technique_summaries: [{ name: 'prompt_sending', description: null, tags: [] }],
  default_datasets: [],
  baseline_policy: 'disabled',
  include_baseline_by_default: false,
  supported_parameters: [
    { name: 'iterations', type_name: 'int', required: false, default: '2', choices: null, is_list: false },
    { name: 'temperature', type_name: 'float', required: false, default: '0.5', choices: null, is_list: false },
  ],
  default_run_size: {
    estimated_attack_count: null,
    components: [],
    datasets: [],
    note: 'Mock estimate only.',
  },
}

async function mockNumericControlApis(page: Page): Promise<void> {
  // No backend, model calls, or connections to another worktree's HMR server.
  await page.routeWebSocket('**/*', (socket) => socket.close())
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const responses: Record<string, unknown> = {
      '/api/auth/config': { clientId: '', tenantId: '', allowedGroupIds: '' },
      '/api/auth/access': { isAdmin: true },
      '/api/health': { status: 'healthy' },
      '/api/runtime': READY_RUNTIME,
      '/api/version': { version: '1.2.0', display: 'Mock PyRIT', default_labels: { operator: 'test' } },
      '/api/targets': {
        items: [makeTarget({ target_registry_name: 'mock-target' })],
        pagination: { limit: 200, has_more: false },
      },
      '/api/targets/types': {
        items: ['AzureMLChatTarget', 'RoundRobinTarget'].map((targetType) => ({
          target_type: targetType,
          parameters: [],
          supported_auth_modes: ['api_key'],
        })),
      },
      '/api/labels': { source: 'attacks', labels: {} },
      [`/api/scenarios/catalog/${SCENARIO.scenario_name}`]: SCENARIO,
      [`/api/scenarios/catalog/${SCENARIO.scenario_name}/estimate`]: SCENARIO.default_run_size,
    }
    if (!(path in responses)) {
      await route.abort()
      throw new Error(`Unexpected API request: ${route.request().method()} ${path}`)
    }
    await route.fulfill({ json: responses[path] })
  })
}

function spinButton(input: Locator, direction: 'Increment' | 'Decrement'): Locator {
  return input.locator('..').getByRole('button', { name: `${direction} value` })
}

async function clickNativeSpinner(input: Locator, direction: 'up' | 'down'): Promise<void> {
  await input.scrollIntoViewIfNeeded()
  const box = await input.boundingBox()
  if (!box) throw new Error('Expected a visible numeric input.')
  const paddingRight = await input.evaluate((element) => parseFloat(getComputedStyle(element).paddingRight))
  // Chromium's native spinner is a UA shadow control, not an accessible button.
  await input.click({
    delay: 200,
    position: { x: box.width - paddingRight - 8, y: box.height / 2 + (direction === 'up' ? -4 : 4) },
  })
}

async function expectNativeSteps(input: Locator, initial: number, step = 1): Promise<void> {
  await input.fill(String(initial))
  await clickNativeSpinner(input, 'up')
  await expect(input).toHaveValue(String(initial + step))
  await clickNativeSpinner(input, 'down')
  await expect(input).toHaveValue(String(initial))
  await input.press('ArrowUp')
  await expect(input).toHaveValue(String(initial + step))
  await input.press('ArrowDown')
  await expect(input).toHaveValue(String(initial))
}

test.beforeEach(async ({ page }) => {
  await mockNumericControlApis(page)
})

for (const clickDuration of [0, 200, 600]) {
  test(`scenario steppers apply one step for a ${clickDuration}ms click and arrow key`, async ({ page }) => {
    await page.goto(`/scanner/${SCENARIO.scenario_name}`)

    for (const [label, initial] of [['Max retries', 0], ['Max concurrency', 10]] as const) {
      const input = page.getByRole('spinbutton', { name: label, exact: true })
      await expect(input).toHaveValue(String(initial))
      await spinButton(input, 'Increment').click({ delay: clickDuration })
      await expect(input).toHaveValue(String(initial + 1))
      await spinButton(input, 'Decrement').click({ delay: clickDuration })
      await expect(input).toHaveValue(String(initial))
      await input.press('ArrowUp')
      await expect(input).toHaveValue(String(initial + 1))
      await input.press('ArrowDown')
      await expect(input).toHaveValue(String(initial))
      await expect(input).toBeFocused()
    }
  })
}

test('scenario steppers commit typed values before stepping and clamp at existing bounds', async ({ page }) => {
  await page.goto(`/scanner/${SCENARIO.scenario_name}`)

  for (const [label, min, max] of [['Max retries', 0, 20], ['Max concurrency', 1, 100]] as const) {
    const input = page.getByRole('spinbutton', { name: label, exact: true })
    await input.fill('7')
    await spinButton(input, 'Increment').click({ delay: 200 })
    await expect(input).toHaveValue('8')
    await input.fill('5')
    await spinButton(input, 'Decrement').click({ delay: 200 })
    await expect(input).toHaveValue('4')
    await input.fill(String(max - 1))
    await input.press('Tab')
    await spinButton(input, 'Increment').click()
    await expect(input).toHaveValue(String(max))
    await expect(spinButton(input, 'Increment')).toBeDisabled()
    await input.press('ArrowUp')
    await expect(input).toHaveValue(String(max))
    await input.fill(String(min + 1))
    await input.press('Tab')
    await spinButton(input, 'Decrement').click()
    await expect(input).toHaveValue(String(min))
    await expect(spinButton(input, 'Decrement')).toBeDisabled()
    await input.press('ArrowDown')
    await expect(input).toHaveValue(String(min))
  }
  await page.reload()
  await expect(page.getByRole('spinbutton', { name: 'Max retries', exact: true })).toHaveValue('0')
  await expect(page.getByRole('spinbutton', { name: 'Max concurrency', exact: true })).toHaveValue('10')
})

test('native scenario dataset and shared dynamic parameter controls step once', async ({ page }) => {
  await page.goto(`/scanner/${SCENARIO.scenario_name}`)
  const datasetSize = page.getByRole('spinbutton', { name: 'Max dataset size', exact: true })
  await expectNativeSteps(datasetSize, 2)
  await expectNativeSteps(page.getByRole('spinbutton', { name: 'iterations', exact: true }), 2)
  // The existing dynamic float input has native step=1 with a fractional base.
  await expectNativeSteps(page.getByRole('spinbutton', { name: 'temperature', exact: true }), 0.5)
  await datasetSize.fill('1')
  await clickNativeSpinner(datasetSize, 'down')
  await expect(datasetSize).toHaveValue('1')
  await datasetSize.press('ArrowDown')
  await expect(datasetSize).toHaveValue('1')
  await datasetSize.fill('')
  await datasetSize.press('Tab')
  await expect(datasetSize).toHaveValue('')
})

test('native Azure ML numeric controls retain their configured steps and reset defaults', async ({ page }) => {
  await page.goto('/registry/targets')
  await page.getByRole('button', { name: /new target/i }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('combobox', { name: 'Target Type' }).click()
  await page.getByRole('option', { name: /Implementation: AzureMLChatTarget/ }).click()

  for (const [label, initial] of [['Max New Tokens', 400], ['Temperature', 1], ['Top P', 1], ['Repetition Penalty', 1]] as const) {
    await expectNativeSteps(dialog.getByRole('spinbutton', { name: label, exact: true }), initial)
  }
  await dialog.getByRole('spinbutton', { name: 'Temperature', exact: true }).fill('0.7')
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.getByRole('button', { name: /new target/i }).click()
  await dialog.getByRole('combobox', { name: 'Target Type' }).click()
  await page.getByRole('option', { name: /Implementation: AzureMLChatTarget/ }).click()
  await expect(dialog.getByRole('spinbutton', { name: 'Temperature', exact: true })).toHaveValue('1.0')
})

test('native round-robin weights step once and preserve bounds and integer validation', async ({ page }) => {
  await page.goto('/registry/targets')
  await page.getByRole('button', { name: /new target/i }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('combobox', { name: 'Target Type' }).click()
  await page.getByRole('option', { name: /Implementation: RoundRobinTarget/ }).click()
  await dialog.getByRole('combobox', { name: 'Add Target' }).selectOption('mock-target')
  const weight = dialog.getByRole('spinbutton', { name: 'Weight for mock-target' })
  await expectNativeSteps(weight, 2)
  await weight.fill('1000')
  await clickNativeSpinner(weight, 'up')
  await expect(weight).toHaveValue('1000')
  await weight.fill('1')
  await clickNativeSpinner(weight, 'down')
  await expect(weight).toHaveValue('1')
  await weight.fill('2.5')
  await expect(dialog.getByRole('alert')).toContainText('Weight must be a whole number')
})

test('shared stepper preserves decimal steps, boundaries, disabled state, and reset', async ({ page }) => {
  await page.goto('/e2e/fixtures/numeric-controls.html')
  const input = page.getByRole('spinbutton', { name: 'Fractional step', exact: true })
  await expect(input).toHaveValue('0.5')
  await spinButton(input, 'Increment').click({ delay: 200 })
  await expect(input).toHaveValue('0.6')
  await spinButton(input, 'Decrement').click({ delay: 200 })
  await expect(input).toHaveValue('0.5')
  await input.press('ArrowUp')
  await expect(input).toHaveValue('0.6')
  await input.press('ArrowDown')
  await expect(input).toHaveValue('0.5')
  await input.fill('0.9')
  await spinButton(input, 'Increment').click()
  await expect(input).toHaveValue('1')
  await expect(spinButton(input, 'Increment')).toBeDisabled()
  await input.fill('0.1')
  await spinButton(input, 'Decrement').click()
  await expect(input).toHaveValue('0')
  await expect(spinButton(input, 'Decrement')).toBeDisabled()
  await page.getByRole('button', { name: 'Reset', exact: true }).click()
  await expect(input).toHaveValue('0.5')
  await spinButton(input, 'Increment').click()
  await expect(input).toHaveValue('0.6')
  const disabled = page.getByRole('spinbutton', { name: 'Disabled stepper', exact: true })
  await expect(disabled).toBeDisabled()
  await expect(spinButton(disabled, 'Increment')).toBeDisabled()
  await expect(spinButton(disabled, 'Decrement')).toBeDisabled()
})

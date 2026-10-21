import { expect, test, type Locator, type Page } from './_fixtures'
import { mockVersion } from './_compatibility'

import type { RegisteredScenario, ScenarioDatasetRequest, ScenarioDatasetSettings } from '../src/types'

import { READY_RUNTIME } from './_runtime'
import { makeTarget } from './_targets'

const DATASET_DEFAULTS: ScenarioDatasetSettings = {
  sources: [{ name: 'harmbench', max_size: 5, fetch: 'if_missing', configured_provider: false }],
  max_per_dataset: 5, max_total: 5, fetch: 'if_missing', filters: {},
}
const DATASET_OPTIONS = {
  mode: 'any' as const, allowed_names: [], per_dataset_limit: true,
  total_limit: true, supports_overrides: true, source_settings: true, filters: true,
}
const DATASETS = {
  items: [
    { name: 'harmbench', selection_key: 'dataset:named:harmbench', loaded: false, provider_available: true, origins: [], logical_examples: null },
    { name: 'custom_objectives', selection_key: 'dataset:named:custom_objectives', loaded: true, provider_available: true,
      origins: ['user'], logical_examples: 10, modalities: ['text', 'image_path'], harm_categories: [], has_unlabeled_harm_categories: false },
  ],
}

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
  default_datasets: ['harmbench'],
  dataset_defaults: DATASET_DEFAULTS,
  dataset_options: DATASET_OPTIONS,
  baseline_policy: 'disabled',
  include_baseline_by_default: false,
  supported_parameters: [
    { name: 'iterations', type_name: 'int', required: false, default: '2', choices: null, is_list: false },
    { name: 'temperature', type_name: 'float', required: false, default: '0.5', choices: null, is_list: false },
  ],
  supported_parameter_names: [
    'objective_target', 'scenario_techniques', 'dataset_config', 'dataset_overrides', 'memory_labels',
    'max_concurrency', 'max_retries', 'include_baseline', 'iterations', 'temperature',
  ],
  default_run_size: {
    dataset_size: { kind: 'indeterminate', reason: 'configuration_unavailable', detail: 'Population configuration is not available.' },
    dataset_limit: { state: 'value', value: 5 },
    dataset_defaults: DATASET_DEFAULTS, effective_datasets: DATASET_DEFAULTS, dataset_options: DATASET_OPTIONS,
    estimated_attack_count: null,
    components: [],
    datasets: [],
    note: 'Mock estimate only.',
  },
}

async function mockNumericControlApis(page: Page): Promise<void> {
  let datasetItems = DATASETS.items.map((item) => ({ ...item }))
  // No backend, model calls, or connections to another worktree's HMR server.
  await page.routeWebSocket('**/*', (socket) => socket.close())
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const responses: Record<string, unknown> = {
      '/api/auth/config': { clientId: '', tenantId: '', allowedGroupIds: '' },
      '/api/auth/access': { isAdmin: true },
      '/api/health': { status: 'healthy' },
      '/api/runtime': READY_RUNTIME,
      '/api/version': mockVersion({ display: 'Mock PyRIT', default_labels: { operator: 'test' } }),
      '/api/targets': {
        items: [makeTarget({ target_registry_name: 'mock-target' })],
        pagination: { limit: 200, has_more: false },
      },
      '/api/targets/types': {
        items: [
          {
            target_type: 'AzureMLChatTarget',
            parameters: [
              { name: 'max_new_tokens', type_name: 'int', required: false, default: '400' },
              { name: 'temperature', type_name: 'float', required: false, default: '1.0' },
              { name: 'top_p', type_name: 'float', required: false, default: '1.0' },
              { name: 'repetition_penalty', type_name: 'float', required: false, default: '1.0' },
            ],
            supported_auth_modes: ['api_key'],
          },
          {
            target_type: 'RoundRobinTarget',
            parameters: [],
            supported_auth_modes: ['api_key'],
          },
        ],
      },
      '/api/labels': { source: 'attacks', labels: {} },
      '/api/datasets': { items: datasetItems },
      [`/api/scenarios/catalog/${SCENARIO.scenario_name}`]: SCENARIO,
      [`/api/scenarios/catalog/${SCENARIO.scenario_name}/estimate`]: SCENARIO.default_run_size,
    }
    if (path.endsWith('/datasets/load')) {
      const edits: ScenarioDatasetRequest = route.request().postDataJSON()?.datasets ?? {}
      const names = (edits.sources ?? DATASET_DEFAULTS.sources).map((source) => source.name)
      datasetItems = datasetItems.map((item) => names.includes(item.name)
        ? { ...item, loaded: true, logical_examples: 200, modalities: ['text'], harm_categories: ['malware'] }
        : item)
      await route.fulfill({ json: { settings: DATASET_DEFAULTS, items: datasetItems.filter((item) => names.includes(item.name)) } })
      return
    }
    if (path.endsWith('/estimate')) {
      const edits: ScenarioDatasetRequest = route.request().postDataJSON()?.datasets ?? {}
      const limit = (value: ScenarioDatasetRequest['max_total'], fallback: number | 'all'): number | 'all' =>
        typeof value === 'number' || value === 'all' ? value : fallback
      const fetch = edits.fetch === 'default' ? DATASET_DEFAULTS.fetch : edits.fetch ?? DATASET_DEFAULTS.fetch
      const effective: ScenarioDatasetSettings = {
        ...DATASET_DEFAULTS,
        max_per_dataset: limit(edits.max_per_dataset, DATASET_DEFAULTS.max_per_dataset),
        max_total: limit(edits.max_total, DATASET_DEFAULTS.max_total),
        fetch,
        sources: (edits.sources ?? DATASET_DEFAULTS.sources).map((source) => ({
          name: source.name,
          max_size: limit(source.max_size, DATASET_DEFAULTS.max_per_dataset),
          fetch: source.fetch === 'default' ? fetch : source.fetch ?? fetch,
          configured_provider: false,
        })),
      }
      await route.fulfill({ json: { ...SCENARIO.default_run_size, effective_datasets: effective } })
      return
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
  // Native spinners auto-repeat when held, so a single-step check must not hold the mouse down.
  await input.click({
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

test('native shared dynamic parameter controls step once', async ({ page }) => {
  await page.goto(`/scanner/${SCENARIO.scenario_name}`)
  await expectNativeSteps(page.getByRole('spinbutton', { name: 'iterations', exact: true }), 2)
  // The existing dynamic float input has native step=1 with a fractional base.
  await expectNativeSteps(page.getByRole('spinbutton', { name: 'temperature', exact: true }), 0.5)
})

test('dataset sources and explicit loading are separate from Parameters', async ({ page }, testInfo) => {
  await page.goto(`/scanner/${SCENARIO.scenario_name}`)
  const section = page.getByRole('region', { name: 'Datasets', exact: true })
  await expect(section.getByRole('table', { name: 'Selected datasets' })).toBeVisible()
  await expect(section.getByRole('columnheader', { name: 'Size' })).toBeVisible()
  await expect(section.getByRole('button', { name: 'Edit harmbench objective limit' })).toHaveText('Up to 5')
  await expect(section.getByText('Not stored in memory', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Parameters' }).getByRole('table')).toHaveCount(0)
  await expect(section.getByRole('button', { name: 'Edit Maximum objectives' })).toHaveText('Up to 5')
  await expect(section.getByText('Details and advanced settings')).toHaveCount(0)
  await section.screenshot({ path: testInfo.outputPath('datasets-default.png') })
  await section.getByRole('button', { name: 'Load datasets' }).click()
  await expect(section.getByRole('button', { name: 'Edit harmbench objective limit' })).toHaveText('Up to 5 of 200')
  await expect(section.getByRole('button', { name: 'Load datasets' })).toHaveCount(0)
  await expect(section.getByRole('radio', { name: 'Load missing', exact: true })).toBeChecked()
  await expect(section.getByRole('button', { name: 'Filter harm category malware' })).toBeVisible()
  await expect(section.getByRole('button', { name: 'Filter data type Text' })).toBeVisible()
  await section.screenshot({ path: testInfo.outputPath('datasets-loaded.png') })
  await section.getByRole('button', { name: 'Edit Maximum objectives' }).click()
  const total = page.getByRole('textbox', { name: 'Maximum objectives', exact: true })
  await total.fill('12')
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await section.screenshot({ path: testInfo.outputPath('datasets-custom.png') })
  await section.getByRole('button', { name: 'Edit Maximum objectives' }).click()
  await total.fill('0')
  await total.press('Escape')
  await section.getByRole('button', { name: 'Reset datasets' }).click()
  await expect(section.getByRole('alert')).toContainText('Maximum objectives must be a positive integer')
  await expect(page.getByRole('button', { name: 'Launch scan', exact: true })).toBeDisabled()
  await section.screenshot({ path: testInfo.outputPath('datasets-invalid.png') })
  await section.getByRole('button', { name: 'Edit Maximum objectives' }).click()
  await expect(total).toHaveValue('0')
  await page.getByRole('button', { name: 'Reset to 5', exact: true }).click()
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(section.getByRole('alert')).not.toBeVisible()
})

test('native Azure ML advanced controls retain steps and reset to default guidance', async ({ page }) => {
  await page.goto('/registry/targets')
  await page.getByRole('button', { name: /new target/i }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('combobox', { name: 'Target Type' }).click()
  await page.getByRole('option', { name: /Implementation: AzureMLChatTarget/ }).click()
  await dialog.getByText('Advanced settings').click()

  for (const [label, initial] of [['Max New Tokens', 400], ['Temperature', 1], ['Top P', 1], ['Repetition Penalty', 1]] as const) {
    const input = dialog.getByRole('spinbutton', { name: label, exact: true })
    await expect(input).toHaveValue('')
    await expect(input).toHaveAttribute('placeholder', `Defaults to ${initial === 1 ? '1.0' : initial}`)
    await expectNativeSteps(input, initial)
  }
  await dialog.getByRole('spinbutton', { name: 'Temperature', exact: true }).fill('0.7')
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.getByRole('button', { name: /new target/i }).click()
  await dialog.getByRole('combobox', { name: 'Target Type' }).click()
  await page.getByRole('option', { name: /Implementation: AzureMLChatTarget/ }).click()
  await dialog.getByText('Advanced settings').click()
  const resetTemperature = dialog.getByRole('spinbutton', { name: 'Temperature', exact: true })
  await expect(resetTemperature).toHaveValue('')
  await expect(resetTemperature).toHaveAttribute('placeholder', 'Defaults to 1.0')
})

test('dataset rows add immediately, edit in place, share filters, and reset without loading', async ({ page }) => {
  await page.goto(`/scanner/${SCENARIO.scenario_name}`)
  const section = page.getByRole('region', { name: 'Datasets', exact: true })
  await section.getByRole('combobox', { name: 'Add dataset' }).click()
  await page.getByRole('option', { name: 'custom_objectives (user)', exact: true }).click()
  await expect(section.getByText('custom_objectives (user)', { exact: true })).toBeVisible()
  await section.getByRole('button', { name: 'Remove harmbench' }).click()
  await expect(section.getByRole('button', { name: 'Load datasets' })).toHaveCount(0)
  await section.getByRole('button', { name: 'Edit custom_objectives objective limit' }).click()
  await page.getByRole('textbox', { name: 'custom_objectives objective limit' }).fill('3')
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(section.getByRole('button', { name: 'Edit custom_objectives objective limit' })).toHaveText('Up to 3 of 10')
  await section.getByRole('button', { name: 'Filter data type Image' }).click()
  await expect(section.getByRole('button', { name: 'Filter data type Text' })).toHaveAttribute('aria-pressed', 'true')
  await expect(section.getByRole('link', { name: 'Browse datasets' })).toHaveAttribute('href', '/datasets')
  await expect(section.getByText('Filters:', { exact: true })).toHaveCount(0)
  await section.getByRole('button', { name: 'Reset datasets' }).click()
  await expect(section.getByText('harmbench (unknown)', { exact: true })).toBeVisible()
  await expect(section.getByRole('button', { name: 'Load datasets' })).toBeVisible()
  await expect(section.getByRole('button', { name: 'Remove type filter text' })).toHaveCount(0)
})

for (const [label, fetch] of [['Stored only', 'never'], ['Load missing', 'if_missing']] as const) {
  test(`dataset manual loading preserves the ${fetch} launch policy`, async ({ page }) => {
    await page.goto(`/scanner/${SCENARIO.scenario_name}`)
    const section = page.getByRole('region', { name: 'Datasets', exact: true })
    const policy = section.getByRole('radio', { name: label, exact: true })
    await expect(section.getByRole('radio', { name: 'Load missing', exact: true })).toBeChecked()
    if (fetch === 'if_missing') {
      await section.getByRole('radio', { name: 'Stored only', exact: true }).check()
    }
    await policy.check()
    const load = page.waitForRequest((request) => new URL(request.url()).pathname.endsWith('/datasets/load'))
    await section.getByRole('button', { name: 'Load datasets' }).click()
    const request = await load
    expect(request.postDataJSON().datasets.fetch).toBe(fetch)
    expect(request.postDataJSON().datasets.sources[0].fetch).toBe(fetch)
    await expect(section.getByRole('button', { name: 'Load datasets' })).toHaveCount(0)
    await expect(policy).toBeChecked()
    await expect(section.getByRole('radio')).toHaveCount(3)
  })
}

test('dataset refresh requires preview confirmation and warns before replacing user data', async ({ page }) => {
  const launches: ScenarioDatasetRequest[] = []
  await page.route('**/api/scenarios/runs', async (route) => {
    launches.push(route.request().postDataJSON().datasets)
    await route.fulfill({
      status: 400,
      json: { detail: 'Mock launch stopped after checking the refresh request.' },
    })
  })
  await page.goto(`/scanner/${SCENARIO.scenario_name}`)
  await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption('mock-target')
  const section = page.getByRole('region', { name: 'Datasets', exact: true })
  await section.getByRole('combobox', { name: 'Add dataset' }).click()
  await page.getByRole('option', { name: 'custom_objectives (user)', exact: true }).click()
  await section.getByRole('button', { name: 'Remove harmbench' }).click()
  await section.getByRole('radio', { name: 'Refresh', exact: true }).check()
  await expect(section.getByRole('radio', { name: 'Refresh', exact: true })).toBeChecked()
  await page.getByRole('button', { name: 'Launch scan', exact: true }).click()
  const preview = page.getByRole('dialog', { name: 'Run preview' })
  await expect(preview.getByRole('alert')).toContainText('full stored datasets: custom_objectives')
  await expect(preview.getByRole('alert')).toContainText('User or unknown-origin data will be replaced: custom_objectives')
  await expect(preview.getByRole('alert')).toContainText('prevent older runs from resuming')
  expect(launches).toHaveLength(0)
  await preview.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(preview).not.toBeVisible()
  expect(launches).toHaveLength(0)
  await page.getByRole('button', { name: 'Launch scan', exact: true }).click()
  await preview.getByRole('button', { name: 'Launch scan', exact: true }).click()
  await expect(page.getByText('Mock launch stopped after checking the refresh request.', { exact: true })).toBeVisible()
  expect(launches).toEqual([{
    fetch: 'always', sources: [{ name: 'custom_objectives', fetch: 'always' }],
  }])
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

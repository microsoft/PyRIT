import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { compatibilityHeaders } from './_compatibility'
import { waitForAppReady } from './_runtime'

test.use({ extraHTTPHeaders: compatibilityHeaders() })

async function capture(page: Page, name: string): Promise<void> {
  const directory = process.env.PYRIT_EVIDENCE_DIR
  if (!directory) throw new Error('Set PYRIT_EVIDENCE_DIR to preserve rendered explicit-target evidence.')
  await mkdir(directory, { recursive: true })
  await page.screenshot({ path: path.join(directory, `${name}.png`), fullPage: true, animations: 'disabled' })
}

for (const width of [1280, 390]) {
  test(`creates explicit offline roles without losing the scan draft at ${width}px @seeded`, async ({ page, request }) => {
    test.setTimeout(180_000)
    page.setDefaultTimeout(15_000)
    await page.setViewportSize({ width, height: 900 })
    const catalog = await request.get('/api/scenarios/catalog')
    expect(catalog.ok(), await catalog.text()).toBe(true)
    const typeCatalog = await (await request.get('/api/targets/types')).json()
    const openai = typeCatalog.items.find((entry: { target_type: string }) => entry.target_type === 'OpenAIChatTarget')
    expect(openai.parameters.find((parameter: { name: string }) => parameter.name === 'model_name').required).toBe(true)
    const targets = await (await request.get('/api/targets')).json()
    expect(targets.items.every((target: { identifier: { class_name: string } }) =>
      target.identifier.class_name !== 'OpenAIChatTarget')).toBe(true)
    const before = await (await request.get('/api/scenarios/runs')).json()
    await page.goto('/scanner/launch/technique.execute')
    await waitForAppReady(page)
    const objective = page.getByRole('textbox', { name: 'objective', exact: true })
    await objective.fill(`Offline explicit role acceptance ${width}`)
    await page.getByRole('radio', { name: 'flip', exact: true }).check()
    await capture(page, `launch-default-${width}`)
    await expect(page.getByTestId('launch-scenario-btn')).toBeDisabled()

    await page.getByRole('button', { name: 'Create objective target', exact: true }).click()
    const targetDialog = page.getByRole('dialog', { name: 'Create New Target' })
    await expect(targetDialog).toBeVisible()
    await capture(page, `target-default-${width}`)
    await targetDialog.getByRole('combobox', { name: 'Target Type' }).click()
    await page.getByRole('option', { name: /Implementation: OpenAIChatTarget\./ }).click()
    await targetDialog.getByRole('textbox', { name: 'Model / Deployment Name', exact: true }).fill('explicit-example-model')
    await targetDialog.getByRole('textbox', { name: 'Endpoint URL', exact: true }).fill('https://offline.invalid')
    await capture(page, `target-editing-${width}`)
    await targetDialog.getByRole('radio', { name: 'Identity-based (Microsoft Entra ID)' }).check()
    await expect(targetDialog.getByText(/Identity-based auth only works with Azure OpenAI/)).toBeVisible()
    await capture(page, `target-unsupported-${width}`)
    await targetDialog.getByRole('radio', { name: 'API Key', exact: true }).check()
    await targetDialog.getByRole('textbox', { name: 'Model / Deployment Name', exact: true }).fill('')
    await targetDialog.getByRole('textbox', { name: 'Endpoint URL', exact: true }).fill('')
    await targetDialog.getByTestId('create-target-form').dispatchEvent('submit')
    await expect(targetDialog.getByText('Please provide a model name')).toBeVisible()
    await capture(page, `target-error-${width}`)
    await targetDialog.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(objective).toHaveValue(`Offline explicit role acceptance ${width}`)

    await page.getByRole('button', { name: 'Create objective target', exact: true }).click()
    await targetDialog.getByRole('combobox', { name: 'Target Type' }).click()
    await page.getByRole('option', { name: /Implementation: BrowserTarget\./ }).click()
    await expect(targetDialog.getByRole('button', { name: 'Create Target', exact: true })).toBeEnabled()
    const createdTargetResponse = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/targets', { timeout: 15_000 })
    await targetDialog.getByRole('button', { name: 'Create Target', exact: true }).click()
    const targetResponse = await createdTargetResponse
    expect(targetResponse.ok(), await targetResponse.text()).toBe(true)
    const target = await targetResponse.json()
    await expect(page.getByTestId('scenario-target-select')).toHaveValue(target.target_registry_name)
    await expect(page.getByLabel('Scan configuration issues')).toContainText('scoring_target')
    await capture(page, `launch-error-${width}`)
    expect((await (await request.get('/api/scenarios/runs')).json()).items).toEqual(before.items)

    await page.getByRole('button', { name: 'Create objective scorer', exact: true }).click()
    const scorerDialog = page.getByRole('dialog', { name: 'Create objective scorer' })
    await capture(page, `scorer-default-${width}`)
    await scorerDialog.getByRole('textbox', { name: 'Scorer name' }).fill(`offline_explicit_${width}`)
    await scorerDialog.getByRole('combobox', { name: 'Scorer type' }).selectOption('GarakExploitationScorer')
    await expect(scorerDialog.getByText(/These required values are not supported by this form/)).toBeVisible()
    await capture(page, `scorer-unsupported-${width}`)
    await scorerDialog.getByRole('combobox', { name: 'Scorer type' }).selectOption('SubStringScorer')
    await scorerDialog.getByRole('textbox', { name: 'substring', exact: true }).fill('Offline browser')
    await capture(page, `scorer-editing-${width}`)
    await scorerDialog.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(objective).toHaveValue(`Offline explicit role acceptance ${width}`)
    await expect(page.getByRole('radio', { name: 'flip', exact: true })).toBeChecked()
    await page.getByRole('button', { name: 'Create objective scorer', exact: true }).click()
    await scorerDialog.getByRole('textbox', { name: 'Scorer name' }).fill(`offline_explicit_${width}`)
    await scorerDialog.getByRole('combobox', { name: 'Scorer type' }).selectOption('SubStringScorer')
    await scorerDialog.getByRole('button', { name: 'Create scorer', exact: true }).click()
    await expect(scorerDialog.getByText(/substring.*required/i)).toBeVisible()
    await capture(page, `scorer-error-${width}`)
    await scorerDialog.getByRole('textbox', { name: 'substring', exact: true }).fill('Offline browser')
    await scorerDialog.getByRole('button', { name: 'Create scorer', exact: true }).click()
    await expect(scorerDialog).toBeHidden()
    await expect(page.getByRole('combobox', { name: 'Objective scorer', exact: true }))
      .toHaveValue(`registered:offline_explicit_${width}`)
    await expect(page.getByRole('combobox', { name: 'Adversarial Target', exact: true })).toHaveCount(0)
    await expect(page.getByRole('combobox', { name: 'Scoring Target', exact: true })).toHaveCount(0)
    await expect(page.getByTestId('launch-scenario-btn')).toBeEnabled()
    await capture(page, `launch-editing-${width}`)
    await page.getByTestId('launch-scenario-btn').click()
    await capture(page, `launch-confirmation-${width}`)
    const started = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/scenarios/runs')
    await page.getByTestId('confirm-launch-scenario-btn').click()
    const response = await started
    expect(response.ok(), await response.text()).toBe(true)
    const run = await response.json()
    await expect.poll(async () => (await (await request.get(
      `/api/scenarios/runs/${run.scenario_result_id}`,
    )).json()).status, { timeout: 30_000 }).toBe('COMPLETED')

    const imageScenario = (await catalog.json()).items.find(
      (entry: { scenario_name: string }) => entry.scenario_name.includes('fig'),
    )
    expect(imageScenario).toBeTruthy()
    await page.goto(`/scanner/launch/${imageScenario.scenario_name}`)
    await waitForAppReady(page)
    await page.getByTestId('scenario-target-select').selectOption(target.target_registry_name)
    await expect(page.getByLabel('Scan configuration issues')).toContainText('image')
    await expect(page.getByTestId('launch-scenario-btn')).toBeDisabled()
    await capture(page, `launch-unsupported-${width}`)
  })

  test(`preflights every suite child and preserves role setup at ${width}px @seeded`, async ({ page, request }) => {
    test.setTimeout(180_000)
    page.setDefaultTimeout(15_000)
    await page.setViewportSize({ width, height: 900 })
    const suiteName = `explicit_suite_${width}`
    const suiteResponse = await request.get(`/api/scenario-suites/${suiteName}`)
    expect(suiteResponse.ok(), await suiteResponse.text()).toBe(true)
    await page.goto('/scanner/launch')
    await waitForAppReady(page)
    await page.getByTestId(`launch-suite-${suiteName}`).click()
    const suiteDialog = page.getByRole('dialog', { name: `Launch ${suiteName}`, exact: true })
    await expect(suiteDialog).toBeVisible()
    await capture(page, `suite-default-${width}`)
    await expect(page.getByTestId('confirm-launch-suite')).toBeDisabled()
    await expect(page.getByLabel('Suite configuration issues')).toContainText('Entry 2')
    const createObjectiveTarget = suiteDialog.getByRole('button', { name: 'Create objective target', exact: true })
    await expect.poll(async () => (await createObjectiveTarget.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(32)
    await page.getByLabel('Suite configuration issues').scrollIntoViewIfNeeded()
    await capture(page, `suite-error-${width}`)
    await createObjectiveTarget.click()
    const targetDialog = page.getByRole('dialog', { name: 'Create New Target' })
    await targetDialog.getByRole('button', { name: 'Cancel', exact: true }).click()
    await expect(page.getByTestId('launch-suite-entries').getByRole('listitem')).toHaveCount(2)
    await suiteDialog.getByRole('button', { name: 'Create objective target', exact: true }).click()
    await targetDialog.getByRole('combobox', { name: 'Target Type' }).click()
    await page.getByRole('option', { name: /Implementation: BrowserTarget\./ }).click()
    const targetCreated = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/targets')
    await expect(targetDialog.getByRole('button', { name: 'Create Target', exact: true })).toBeEnabled()
    await targetDialog.getByRole('button', { name: 'Create Target', exact: true }).click()
    const targetResponse = await targetCreated
    expect(targetResponse.ok(), await targetResponse.text()).toBe(true)
    const target = await targetResponse.json()
    await expect(suiteDialog.getByRole('combobox', { name: 'Target', exact: true })).toHaveValue(target.target_registry_name)
    await suiteDialog.getByRole('button', { name: 'Create objective scorer', exact: true }).click()
    const scorerDialog = page.getByRole('dialog', { name: 'Create objective scorer' })
    await scorerDialog.getByRole('textbox', { name: 'Scorer name' }).fill(`suite_scorer_${width}`)
    await scorerDialog.getByRole('combobox', { name: 'Scorer type' }).selectOption('SubStringScorer')
    await scorerDialog.getByRole('textbox', { name: 'substring', exact: true }).fill('Offline browser')
    await scorerDialog.getByRole('button', { name: 'Create scorer', exact: true }).click()
    await expect(scorerDialog).toBeHidden()
    await expect(page.getByTestId('launch-suite-entries').getByRole('listitem')).toHaveCount(2)
    await expect(page.getByTestId('confirm-launch-suite')).toBeEnabled()
    await capture(page, `suite-editing-${width}`)
    const started = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(`/${suiteName}/launch`))
    await page.getByTestId('confirm-launch-suite').click()
    const launchResponse = await started
    expect(launchResponse.ok(), await launchResponse.text()).toBe(true)
    const execution = await launchResponse.json()
    await expect.poll(async () => (await (await request.get(
      `/api/scenario-suites/executions/${execution.suite_execution_id}`,
    )).json()).status, { timeout: 30_000 }).toBe('COMPLETED')

    const mixedName = `explicit_mixed_${width}`
    const mixedResponse = await request.get(`/api/scenario-suites/${mixedName}`)
    expect(mixedResponse.ok(), await mixedResponse.text()).toBe(true)
    const before = await (await request.get('/api/scenarios/runs')).json()
    await page.goto('/scanner/launch')
    await waitForAppReady(page)
    await page.getByTestId(`launch-suite-${mixedName}`).click()
    const mixedDialog = page.getByRole('dialog', { name: `Launch ${mixedName}`, exact: true })
    await mixedDialog.getByRole('combobox', { name: 'Target', exact: true }).selectOption(target.target_registry_name)
    await mixedDialog.getByRole('combobox', { name: 'Objective scorer', exact: true })
      .selectOption(`registered:suite_scorer_${width}`)
    await expect(page.getByLabel('Suite configuration issues')).toContainText('Entry 2')
    await expect(page.getByLabel('Suite configuration issues')).toContainText('image')
    await expect(page.getByTestId('confirm-launch-suite')).toBeDisabled()
    await page.getByLabel('Suite configuration issues').scrollIntoViewIfNeeded()
    await capture(page, `suite-unsupported-${width}`)
    expect((await (await request.get('/api/scenarios/runs')).json()).items).toEqual(before.items)
  })
}

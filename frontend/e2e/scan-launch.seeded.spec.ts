import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

import type { RunScenarioRequest, ScenarioRunSummary, TargetListResponse } from '../src/types'
import { compatibilityHeaders } from './_compatibility'
import { waitForAppReady } from './_runtime'

test.use({
  // eslint-disable-next-line no-empty-pattern
  extraHTTPHeaders: async ({}, runTest) => runTest(compatibilityHeaders()),
})

async function targetSnapshot(request: APIRequestContext): Promise<string[]> {
  const identities: string[] = []
  let cursor: string | null | undefined
  do {
    const response = await request.get('/api/targets', {
      params: { limit: 200, ...(cursor ? { cursor } : {}) },
    })
    expect(response.ok(), await response.text()).toBe(true)
    const targets: TargetListResponse = await response.json()
    identities.push(...targets.items.map(target =>
      JSON.stringify([target.target_registry_name, target.identifier.hash])))
    if (!targets.pagination.has_more) break
    expect(targets.pagination.next_cursor).toBeTruthy()
    cursor = targets.pagination.next_cursor
  } while (cursor)
  return identities.sort()
}

async function capture(page: Page, name: string): Promise<void> {
  const directory = process.env.PYRIT_EVIDENCE_DIR || test.info().outputPath('scan-launch')
  await mkdir(directory, { recursive: true })
  await page.screenshot({ path: path.join(directory, `${name}.png`), fullPage: true, animations: 'disabled' })
}

for (const width of [1280, 390]) {
  test(`browses and launches with the original scan controls at ${width}px @seeded`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 })
    const initialTargets = await targetSnapshot(request)
    const catalog = await request.get('/api/scenarios/catalog')
    expect(catalog.ok(), await catalog.text()).toBe(true)
    await page.goto('/scanner/launch/technique.execute')
    await waitForAppReady(page)
    await expect(page.getByTestId('scenario-error')).toHaveCount(0)
    await expect(page.getByText('Environment variable OPENAI_CHAT_MODEL is required')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Create .*target|Create objective scorer/i })).toHaveCount(0)
    await expect(page.getByRole('combobox', { name: 'Scoring Target', exact: true })).toHaveCount(0)
    await expect(page.getByRole('combobox', { name: 'Objective scorer', exact: true }))
      .toHaveValue('')
    await expect(page.getByRole('option', { name: 'Use scenario default' })).toHaveCount(1)
    const objective = page.getByRole('textbox', { name: 'objective', exact: true })
    await objective.fill(`Offline scan acceptance ${width}`)
    await page.getByRole('radio', { name: 'flip', exact: true }).check()
    expect(await targetSnapshot(request)).toEqual(initialTargets)
    await capture(page, `scan-default-${width}`)

    const target = page.getByTestId('scenario-target-select')
    if (await target.inputValue() === '') {
      await page.getByTestId('launch-scenario-btn').click()
      await expect(page.getByRole('alert').filter({ hasText: 'Select a target.' })).toBeVisible()
    }
    await target.selectOption('browser_offline')
    await capture(page, `scan-configured-${width}`)
    await page.getByTestId('launch-scenario-btn').click()
    const preview = page.getByRole('dialog')
    await expect(preview.getByText('Use scenario default')).toBeVisible()
    await expect(preview.getByText('Scoring Target', { exact: true })).toHaveCount(0)
    await capture(page, `scan-preview-${width}`)
    const started = page.waitForResponse(response =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/scenarios/runs')
    await page.getByTestId('confirm-launch-scenario-btn').click()
    const response = await started
    expect(response.ok(), await response.text()).toBe(true)
    const run: ScenarioRunSummary = await response.json()
    await expect.poll(async () => (await (await request.get(
      `/api/scenarios/runs/${run.scenario_result_id}`,
    )).json()).status).toBe('COMPLETED')
    const savedResponse = await request.get(`/api/scenarios/runs/${run.scenario_result_id}/launch-configuration`)
    expect(savedResponse.ok(), await savedResponse.text()).toBe(true)
    const saved: RunScenarioRequest = await savedResponse.json()
    expect(saved.target_name).toBe('browser_offline')
    expect(saved.objective_scorer).toBeFalsy()
    expect(saved.scoring_target_name).toBeFalsy()
    expect(saved.adversarial_target_name).toBeFalsy()
    expect(saved.techniques).toEqual(['flip'])
    expect(await targetSnapshot(request)).toEqual(initialTargets)
  })

  test(`reports a missing generation target only when launching at ${width}px @seeded`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 })
    const initialTargets = await targetSnapshot(request)
    const before = await (await request.get('/api/scenarios/runs')).json()
    await page.goto('/scanner/launch/technique.execute')
    await waitForAppReady(page)
    await page.getByTestId('scenario-target-select').selectOption('browser_offline')
    await page.getByRole('textbox', { name: 'objective', exact: true }).fill('Missing generation role')
    await page.getByRole('radio', { name: 'red_teaming', exact: true }).check()
    await expect(page.getByRole('alert')).toHaveCount(0)
    await page.getByTestId('launch-scenario-btn').click()
    await expect(page.getByRole('dialog').getByText('Use server default')).toBeVisible()
    await page.getByTestId('confirm-launch-scenario-btn').click()
    await expect(page.getByRole('alert')).toContainText('adversarial_chat')
    await expect(page.getByTestId('launch-scenario-btn')).toBeEnabled()
    await capture(page, `scan-error-${width}`)
    expect((await (await request.get('/api/scenarios/runs')).json()).items).toEqual(before.items)
    expect(await targetSnapshot(request)).toEqual(initialTargets)
  })

  test(`launches suites with configured defaults and reports invalid children at ${width}px @seeded`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 900 })
    const initialTargets = await targetSnapshot(request)
    const suiteName = `explicit_suite_${width}`
    await page.goto('/scanner/launch')
    await waitForAppReady(page)
    await page.getByTestId(`launch-suite-${suiteName}`).click()
    const dialog = page.getByRole('dialog', { name: `Launch ${suiteName}`, exact: true })
    await expect(dialog.getByRole('combobox')).toHaveCount(2)
    await expect(dialog.getByRole('button', { name: /Create/i })).toHaveCount(0)
    await expect(dialog.getByRole('combobox', { name: 'Adversarial Target', exact: true })).toHaveValue('')
    await expect(dialog.getByTestId('launch-suite-entries').getByRole('listitem')).toHaveCount(2)
    await dialog.getByRole('combobox', { name: 'Target', exact: true }).selectOption('browser_offline')
    await capture(page, `suite-configured-${width}`)
    const started = page.waitForResponse(response =>
      response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(`/${suiteName}/launch`))
    await page.getByTestId('confirm-launch-suite').click()
    const launchResponse = await started
    expect(launchResponse.ok(), await launchResponse.text()).toBe(true)
    const execution = await launchResponse.json()
    await expect.poll(async () => (await (await request.get(
      `/api/scenario-suites/executions/${execution.suite_execution_id}`,
    )).json()).status).toBe('COMPLETED')

    const mixedName = `explicit_mixed_${width}`
    const before = await (await request.get('/api/scenarios/runs')).json()
    await page.goto('/scanner/launch')
    await waitForAppReady(page)
    await page.getByTestId(`launch-suite-${mixedName}`).click()
    const mixedDialog = page.getByRole('dialog', { name: `Launch ${mixedName}`, exact: true })
    await mixedDialog.getByRole('combobox', { name: 'Target', exact: true }).selectOption('browser_offline')
    await page.getByTestId('confirm-launch-suite').click()
    await expect(mixedDialog.getByRole('alert')).toContainText('image')
    await expect(page.getByTestId('confirm-launch-suite')).toBeEnabled()
    await capture(page, `suite-error-${width}`)
    expect((await (await request.get('/api/scenarios/runs')).json()).items).toEqual(before.items)
    expect(await targetSnapshot(request)).toEqual(initialTargets)
  })
}

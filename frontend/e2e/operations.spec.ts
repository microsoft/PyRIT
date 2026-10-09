import { test, expect } from '@playwright/test'

import type { Finding, Operation } from '../src/types'

test('creates a unique operation and records findings within it @seeded', async ({ page, request }, testInfo) => {
  test.setTimeout(90_000)
  const name = `Red team / α% ${Date.now()}`
  const title = 'Human assessment without an attack'
  await page.goto('/findings')
  await expect(page).toHaveURL(/\/operations$/)
  await expect(page.getByRole('heading', { name: 'Operations', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Operations', exact: true })).toHaveAttribute('aria-current', 'page')

  await page.getByRole('region', { name: 'Operations', exact: true }).getByRole('button', { name: 'New operation' }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByRole('textbox', { name: 'Name' }).fill(` ${name} `)
  const createdResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/operations') && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Create operation' }).click()
  const created = await createdResponse
  expect(created.status()).toBe(201)
  const operation: Operation = await created.json()
  expect(operation.name).toBe(name)
  await expect(page).toHaveURL(new RegExp(`/operations/${operation.id}$`))
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Operations', exact: true })).toHaveAttribute('aria-current', 'page')

  await page.getByRole('link', { name: 'Operations' }).click()
  await page.getByRole('region', { name: 'Operations', exact: true }).getByRole('button', { name: 'New operation' }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByRole('textbox', { name: 'Name' }).fill(`  ${name.toUpperCase()} `)
  await dialog.getByRole('button', { name: 'Create operation' }).click()
  await expect(dialog.getByText(/already exists/i)).toBeVisible()
  await dialog.getByRole('link', { name: `Open ${name}` }).click()
  await expect(page).toHaveURL(new RegExp(`/operations/${operation.id}$`))

  await page.getByRole('button', { name: 'New finding' }).click()
  dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('combobox', { name: 'Operation' })).toHaveCount(0)
  await dialog.getByRole('textbox', { name: 'Title' }).fill(title)
  await dialog.getByRole('combobox', { name: 'Severity' }).selectOption('informational')
  const savedResponse = page.waitForResponse(response =>
    response.url().endsWith(`/api/operations/${operation.id}/findings`) && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Save finding' }).click()
  const saved = await savedResponse
  expect(saved.status()).toBe(201)
  const finding: Finding = await saved.json()
  expect(finding.operation_id).toBe(operation.id)
  await expect(page.getByRole('heading', { level: 2, name: title })).toBeVisible()
  await expect(page.getByRole('button', { name: 'New finding' })).toBeFocused()
  await page.reload()
  await expect(page.getByRole('heading', { level: 2, name: title })).toBeVisible()

  const versionResponse = await request.get('/api/version')
  expect(versionResponse.ok()).toBeTruthy()
  const version: { compatibility_id: string } = await versionResponse.json()
  const headers = { 'PyRIT-Compatibility-ID': version.compatibility_id }
  for (let index = 0; index < 21; index++) {
    const response = await request.post(`/api/operations/${operation.id}/findings`, {
      headers, data: { title: `Assessment ${index}`, description: '', severity: index === 20 ? 'critical' : 'low' },
    })

    expect(response.status()).toBe(201)
  }
  await page.reload()
  const region = page.getByRole('region', { name: 'Operation', exact: true })
  await expect(region.getByRole('listitem')).toHaveCount(20)
  await expect(region.getByRole('heading', { level: 2 }).first()).toHaveText('Assessment 20')
  await page.getByRole('button', { name: 'Next', exact: true }).click()
  await expect(region.getByRole('listitem')).toHaveCount(2)
  await expect(page.getByRole('heading', { level: 2, name: title })).toBeVisible()

  await page.goto('/operations')
  await expect(page.getByRole('link', { name })).toBeVisible()
  await page.goto('/operations/00000000-0000-4000-8000-000000000000')
  await expect(page.getByRole('heading', { name: 'Operation not found' })).toBeVisible()

  await page.goto(`/operations/${operation.id}`)
  await page.screenshot({ path: testInfo.outputPath('operation-desktop.png'), animations: 'disabled' })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.getByRole('button', { name: 'New finding' }).click()
  await expect(dialog.getByRole('textbox', { name: 'Title' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('operation-mobile-dialog.png'), animations: 'disabled' })
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'New finding' })).toBeFocused()
})

test('selects saved operations inline and revisits exact-name attack history @seeded', async ({ page, request }) => {
  test.setTimeout(90_000)
  const name = `Engagement / α% & ${Date.now()}`
  await page.goto('/')
  const bar = page.getByRole('region', { name: 'Default Labels' })
  await bar.getByRole('combobox', { name: 'Operation', exact: true }).click()
  await page.getByRole('option', { name: 'New operation…', exact: true }).click()
  let dialog = page.getByRole('dialog')
  await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill(name)
  const creation = page.waitForResponse(response =>
    response.url().endsWith('/api/operations') && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Create operation' }).click()
  const operation: Operation = await (await creation).json()
  await expect(dialog).toHaveCount(0)
  await expect(bar.getByRole('combobox', { name: 'Operation' })).toHaveValue(name)
  await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toBeFocused()
  await expect(page).toHaveURL(/\/$/)
  await page.reload()
  await expect(bar.getByRole('combobox', { name: 'Operation' })).toHaveValue(name)

  await bar.getByRole('button', { name: 'Remove operation label' }).click()
  await bar.getByRole('combobox', { name: 'Operation', exact: true }).click()
  await page.getByTestId('edit-label-operation').fill('not saved anywhere')
  await expect(page.getByRole('option', { name: 'not saved anywhere', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toBeVisible()
  await bar.getByRole('combobox', { name: 'Operation', exact: true }).click()
  await page.getByRole('option', { name: 'New operation…', exact: true }).click()
  dialog = page.getByRole('dialog')
  await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill(` ${name.toUpperCase()} `)
  await dialog.getByRole('button', { name: 'Create operation' }).click()
  await dialog.getByRole('button', { name: 'Use existing', exact: true }).click()
  await expect(bar.getByRole('combobox', { name: 'Operation' })).toHaveValue(name)
  await expect(page).toHaveURL(/\/$/)

  const version = await (await request.get('/api/version')).json()
  const headers = { 'PyRIT-Compatibility-ID': version.compatibility_id }
  const targetResponse = await request.post('/api/targets', { headers, data: { type: 'TextTarget', params: {} } })
  expect(targetResponse.ok()).toBeTruthy()
  const target = await targetResponse.json()
  for (let index = 0; index < 7; index++) {
    const response = await request.post('/api/attacks', {
      headers, data: { target_registry_name: target.target_registry_name, operation: name },
    })
    expect(response.ok()).toBeTruthy()
    const attack = await response.json()
    const updated = await request.patch(`/api/attacks/${attack.attack_result_id}`, {
      headers, data: { objective: `Offline saved history ${index}` },
    })
    expect(updated.ok()).toBeTruthy()
  }
  const other = await request.post('/api/attacks', {
    headers, data: { target_registry_name: target.target_registry_name, operation: name.toUpperCase() },
  })
  expect(other.ok()).toBeTruthy()
  const finding = await request.post(`/api/operations/${operation.id}/findings`, {
    headers, data: { title: 'Independent human finding', description: '', severity: 'low' },
  })
  expect(finding.status()).toBe(201)
  await page.goto(`/operations/${operation.id}`)
  await expect(page.getByRole('heading', { name: 'Independent human finding', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Recent attacks', exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Recent scanner runs', exact: true })).toHaveCount(0)
  const historyLink = page.getByRole('link', { name: 'View execution history', exact: true })
  const href = await historyLink.getAttribute('href')
  expect(new URL(href!, 'http://localhost').searchParams.get('operation')).toBe(name)
  await historyLink.click()
  await expect(page).toHaveURL(/\/history\/attacks\?/)
  await expect(page.getByRole('row', { name: 'Open ManualAttack attack', exact: true })).toHaveCount(7)
  await expect(page.getByRole('combobox', { name: 'All operations' })).toHaveValue(name)
  await page.getByRole('tab', { name: 'Scanner', exact: true }).click()
  await expect(page).toHaveURL(/\/history\/scanner\?/)
  expect(new URL(page.url()).searchParams.get('operation')).toBe(name)
  const returnedAttacks = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/attacks' && response.request().method() === 'GET')
  await page.getByRole('tab', { name: 'Attacks', exact: true }).click()
  await expect(page).toHaveURL(/\/history\/attacks\?/)
  expect((await returnedAttacks).ok()).toBeTruthy()
  await expect(page.getByRole('progressbar', { name: 'Loading attacks...' })).toHaveCount(0, { timeout: 30_000 })
  await expect(page.getByRole('row', { name: 'Open ManualAttack attack', exact: true })).toHaveCount(7)
  await page.goto(`/operations/${operation.id}`)
  await page.reload()
  await expect(historyLink).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Independent human finding', exact: true })).toBeVisible()
})

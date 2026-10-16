import { test, expect } from '@playwright/test'

import type { Finding, Operation } from '../src/types'

for (const width of [1280, 390]) {
  test(`wraps long finding text in detail and evidence picker at ${width}px @seeded`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 })
    const version = await (await request.get('/api/version')).json()
    const headers = { 'PyRIT-Compatibility-ID': version.compatibility_id }
    const operationResponse = await request.post('/api/operations', {
      headers, data: { name: `Wrapping ${width} ${Date.now()}` },
    })
    expect(operationResponse.status()).toBe(201)
    const operation: Operation = await operationResponse.json()
    const title = `Long finding ${'unbroken'.repeat(35)}`
    const severity = `Custom severity ${'classification'.repeat(16)}`
    const harmType = `Custom harm ${'unbroken'.repeat(25)}`
    const findingResponse = await request.post(`/api/operations/${operation.id}/findings`, {
      headers, data: { title, severity: 'other', severity_other: severity, harm_type: 'Other', harm_type_other: harmType },
    })
    expect(findingResponse.status()).toBe(201)

    await page.goto(`/operations/${operation.id}`)
    const heading = page.getByRole('heading', { name: title, exact: true })
    await expect(heading).toBeVisible()
    const finding = page.getByRole('listitem').filter({ has: heading })
    const badge = finding.getByText(severity, { exact: true })
    await expect(badge).toBeVisible()
    expect(await finding.evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy()
    expect(await badge.evaluate(element =>
      element.scrollHeight <= element.clientHeight && element.scrollWidth <= element.clientWidth,
    )).toBeTruthy()
    const badgeBox = await badge.boundingBox()
    const timeBox = await finding.locator('time').boundingBox()
    if (!badgeBox || !timeBox) throw new Error('Expected visible finding metadata.')
    expect(badgeBox.x + badgeBox.width <= timeBox.x || badgeBox.y + badgeBox.height <= timeBox.y).toBeTruthy()
    await expect(finding.getByText(`Harm-type: ${harmType}`, { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()

    const targetResponse = await request.post('/api/targets', { headers, data: { type: 'TextTarget', params: {} } })
    expect(targetResponse.ok()).toBeTruthy()
    const target = await targetResponse.json()
    const attackResponse = await request.post('/api/attacks', {
      headers, data: {
        target_registry_name: target.target_registry_name,
        operation_id: operation.id,
        prepended_conversation: [{ role: 'user', pieces: [{ original_value: 'Saved offline evidence.' }] }],
      },
    })
    expect(attackResponse.ok()).toBeTruthy()
    const attack = await attackResponse.json()
    await page.goto(`/attacks/${attack.attack_result_id}/conversations/${attack.conversation_id}`)
    await page.getByTestId('objective-outcome-button').click()
    await page.getByRole('button', { name: 'Add as finding' }).click()
    const dialog = page.getByRole('dialog')
    const radio = dialog.getByRole('radio', { name: new RegExp('Long finding') })
    await expect(radio).toBeVisible()
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy()
    const pickerBadge = dialog.getByText(severity, { exact: true })
    expect(await pickerBadge.evaluate(element =>
      element.scrollHeight <= element.clientHeight && element.scrollWidth <= element.clientWidth,
    )).toBeTruthy()
    const label = dialog.locator('label').filter({ hasText: title })
    expect(await label.evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy()
    const pickerBadgeBox = await pickerBadge.boundingBox()
    const harmBox = await dialog.getByText(`Harm-type: ${harmType}`, { exact: true }).boundingBox()
    if (!pickerBadgeBox || !harmBox) throw new Error('Expected visible picker classifications.')
    expect(pickerBadgeBox.y + pickerBadgeBox.height).toBeLessThanOrEqual(harmBox.y)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
    await radio.focus()
    await page.keyboard.press('Space')
    await expect(radio).toBeChecked()
    await expect(dialog.getByRole('button', { name: 'Attach attack' })).toBeEnabled()
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
  })
}

test('creates a unique operation and records findings within it @seeded', async ({ page, request }, testInfo) => {
  test.setTimeout(90_000)
  const name = `Red team / α% ${Date.now()}`
  const title = `Human assessment ${'with a long title '.repeat(10)}`
  const description = 'Long supporting details '.repeat(18)
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
  await dialog.getByRole('textbox', { name: 'Description' }).fill(description)
  const savedResponse = page.waitForResponse(response =>
    response.url().endsWith(`/api/operations/${operation.id}/findings`) && response.request().method() === 'POST')
  await dialog.getByRole('button', { name: 'Save finding' }).click()
  const saved = await savedResponse
  expect(saved.status()).toBe(201)
  const finding: Finding = await saved.json()
  expect(finding.operation_id).toBe(operation.id)
  await expect(page.getByRole('heading', { level: 2, name: title })).toBeVisible()
  await expect(page.getByRole('button', { name: 'New finding' })).toBeFocused()
  await page.setViewportSize({ width: 390, height: 300 })
  const confirmation = page.locator('[aria-live="polite"]').filter({ hasText: 'Finding saved:' })
  const confirmationBox = await confirmation.boundingBox()
  const findingBox = await page.getByRole('heading', { level: 2, name: title }).boundingBox()
  expect(confirmationBox).not.toBeNull()
  expect(findingBox).not.toBeNull()
  expect(await confirmation.evaluate(element => element.scrollHeight <= element.clientHeight)).toBeTruthy()
  expect(confirmationBox!.y + confirmationBox!.height).toBeLessThanOrEqual(findingBox!.y)
  await page.setViewportSize({ width: 1280, height: 720 })
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

test('selects saved operations inline and revisits ID-scoped attack history @seeded', async ({ page, request }) => {
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

  await bar.getByRole('button', { name: 'Clear operation', exact: true }).click()
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
  let sourceAttack: { attack_result_id: string; conversation_id: string } | null = null
  for (let index = 0; index < 7; index++) {
    const response = await request.post('/api/attacks', {
      headers, data: {
        target_registry_name: target.target_registry_name,
        operation_id: operation.id,
        ...(index === 0 ? {
          prepended_conversation: [{ role: 'user', pieces: [{ original_value: 'Saved offline evidence.' }] }],
        } : {}),
      },
    })
    expect(response.ok()).toBeTruthy()
    const attack = await response.json()
    if (index === 0) sourceAttack = attack
    const updated = await request.patch(`/api/attacks/${attack.attack_result_id}`, {
      headers, data: { objective: `Offline saved history ${index}` },
    })
    expect(updated.ok()).toBeTruthy()
  }
  const otherOperationResponse = await request.post('/api/operations', {
    headers, data: { name: `Other ${name}` },
  })
  expect(otherOperationResponse.status()).toBe(201)
  const otherOperation: Operation = await otherOperationResponse.json()
  for (const operationId of [otherOperation.id, null]) {
    const other = await request.post('/api/attacks', {
      headers, data: { target_registry_name: target.target_registry_name, operation_id: operationId },
    })
    expect(other.ok()).toBeTruthy()
  }
  const finding = await request.post(`/api/operations/${operation.id}/findings`, {
    headers, data: { title: 'Independent human finding', description: '', severity: 'low' },
  })
  expect(finding.status()).toBe(201)
  if (!sourceAttack) throw new Error('Expected a saved attack for the evidence test.')
  await page.route(/\/api\/operations\/[^/]+\/findings\/[^/]+\/evidence$/, async route => {
    await new Promise(resolve => setTimeout(resolve, 300))
    await route.fulfill({
      status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Attach failed' }),
    })
  })
  await page.goto(`/attacks/${sourceAttack.attack_result_id}/conversations/${sourceAttack.conversation_id}`)
  await page.getByTestId('objective-outcome-button').click()
  await page.getByRole('button', { name: 'Add as finding' }).click()
  await page.getByRole('radio', { name: /Independent human finding/ }).click()
  await page.getByRole('button', { name: 'Attach attack' }).click()
  const busyAttach = page.getByRole('button', { name: 'Attaching...' })
  await expect(busyAttach).toBeFocused()
  const attachError = page.getByText('Could not attach: Attach failed')
  await expect(attachError).toBeVisible()
  await expect(page.locator('[aria-live="polite"]').filter({ hasText: 'Could not attach: Attach failed' })).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Attach attack' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.unroute(/\/api\/operations\/[^/]+\/findings\/[^/]+\/evidence$/)
  await page.goto(`/operations/${operation.id}`)
  await expect(page.getByRole('heading', { name: 'Independent human finding', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Recent attacks', exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Recent scanner runs', exact: true })).toHaveCount(0)
  const historyLink = page.getByRole('link', { name: 'All execution history', exact: true })
  const href = await historyLink.getAttribute('href')
  expect(new URL(href!, 'http://localhost').searchParams.getAll('operation_id')).toEqual([operation.id])
  await historyLink.click()
  await expect(page).toHaveURL(/\/history\/attacks\?/)
  await expect(page.getByRole('row', { name: 'Open ManualAttack attack', exact: true })).toHaveCount(7)
  await expect(page.getByRole('combobox', { name: 'All operations' })).toHaveValue(name)
  await page.getByRole('tab', { name: 'Scanner', exact: true }).click()
  await expect(page).toHaveURL(/\/history\/scanner\?/)
  expect(new URL(page.url()).searchParams.getAll('operation_id')).toEqual([operation.id])
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

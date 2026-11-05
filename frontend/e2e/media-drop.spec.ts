import path from 'node:path'
import { randomUUID } from 'node:crypto'

import { test, expect, type CDPSession, type Locator } from '@playwright/test'

import { compatibilityHeaders } from './_compatibility'

async function nativeFileDrag(client: CDPSession, target: Locator, drop: boolean = true): Promise<void> {
  await target.scrollIntoViewIfNeeded()
  const bounds = await target.boundingBox()
  if (!bounds) throw new Error('The file drop surface is not visible.')
  const data = {
    items: [],
    files: [path.resolve('e2e', 'fixtures', 'portrait-600x1200.png')],
    dragOperationsMask: 1,
  }
  for (const type of drop ? ['dragEnter', 'dragOver', 'drop'] as const : ['dragEnter', 'dragOver'] as const) {
    await client.send('Input.dispatchDragEvent', {
      type, data, x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2,
    })
  }
}

test('preserves the draft across native file drops outside the chat and on a portal dialog @seeded', async ({ page, request }) => {
  const client = await page.context().newCDPSession(page)
  const response = await request.post('/api/attacks/save-conversation', {
    headers: compatibilityHeaders(),
    data: {
      save_id: randomUUID(), destination: 'new_attack', objective: 'File drop safety',
      messages: [
        { role: 'user', pieces: [{ data_type: 'text', original_value: 'A local sample prompt.' }] },
        { role: 'simulated_assistant', pieces: [{ data_type: 'text', original_value: 'A local sample reply.' }] },
      ],
    },
  })
  expect(response.ok()).toBe(true)
  const saved = await response.json()
  const route = `/attack/chat/${saved.attack.attack_result_id}`
  await page.goto(route)
  const draft = page.getByTestId('chat-input')
  await draft.fill('Keep this unsent draft.')
  await nativeFileDrag(client, page.getByRole('button', { name: 'Registry', exact: true }))
  await expect(page).toHaveURL(new RegExp(`${route}$`))
  await expect(draft).toHaveValue('Keep this unsent draft.')
  await expect(page.getByRole('button', { name: 'Remove portrait-600x1200.png' })).toHaveCount(0)

  await page.getByRole('button', { name: /Objective achieved outcome:/i }).click()
  await page.getByRole('button', { name: 'Add as finding', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Attach to finding', exact: true })
  await expect(dialog).toBeVisible()
  await nativeFileDrag(client, dialog)
  await expect(page).toHaveURL(new RegExp(`${route}$`))
  await expect(page.getByText('Drop files to attach', { exact: true })).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  await draft.click()
  await expect(draft).toHaveValue('Keep this unsent draft.')
  await expect(page.getByRole('button', { name: 'Remove portrait-600x1200.png' })).toHaveCount(0)

  await nativeFileDrag(client, draft, false)
  await expect(page.getByText('Drop files to attach', { exact: true })).toHaveCount(1)
  await nativeFileDrag(client, draft)
  await expect(page.getByRole('button', { name: 'Remove portrait-600x1200.png' })).toHaveCount(1)
  await expect(draft).toHaveValue('Keep this unsent draft.')
  await expect(page.getByText('Drop files to attach', { exact: true })).toHaveCount(0)
  await client.detach()
})

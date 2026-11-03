import { expect, test, type Page, type Request, type Response, type TestInfo } from '@playwright/test'

import type { AttackConversationsResponse, AttackMetadata, BackendMessage, BackendMessagePiece, ConversationMessagesResponse, ConversationSummary } from '@/types'
import { attackConversationRoutePath, scenarioRunRoutePath } from '../src/utils/routeParams'
import { compatibilityHeaders } from './_compatibility'

// Seeded by e2e/fixtures/scenario_hierarchy_backend.py.
const scenarioResultId = '00000000-0000-0000-0000-000000003602'
const run = scenarioRunRoutePath(scenarioResultId)
const SAVED_ATTACK_ID = '00000000-0000-0000-0000-00036020000e'
const SAVED_MAIN_ID = '00000000-0000-0000-0000-00036030000e'
const SAVED_ROUTE = attackConversationRoutePath(SAVED_ATTACK_ID, SAVED_MAIN_ID, scenarioResultId)
const FIRST_PRUNED_ID = '00000000-0000-0000-0000-00010b000001'
const OFF_PAGE_ID = '00000000-0000-0000-0000-00010b00006e'
const SAVED_REQUEST = 'Synthetic request for Step 5. No external model was called.'
const SAVED_RESPONSE = 'Stored example response for Step 5. This is offline sample data.'

type SavedReadAction = 'reset' | 'status' | 'fail_messages' | 'fail_references'
  | 'hold_messages' | 'release_messages' | 'hold_references' | 'release_references'

async function controlSavedRead(
  page: Page, action: SavedReadAction, status: 400 | 503 = 503,
): Promise<{ waiting: boolean; reference_waiting: boolean }> {
  const response = await page.request.post('/api/e2e/saved-evidence', {
    data: { action, status }, headers: compatibilityHeaders(),
  })
  expect(response.status(), await response.text()).toBe(200)
  return response.json()
}

interface SavedMetadataRead {
  readonly url: URL
  readonly status: number
  readonly body: Promise<AttackMetadata>
}

interface SavedReads {
  readonly requests: URL[]
  readonly metadata: SavedMetadataRead[]
}

function captureSavedReads(page: Page): SavedReads {
  const reads: SavedReads = { requests: [], metadata: [] }
  page.on('request', (request: Request): void => {
    const url = new URL(request.url())
    if (request.method() === 'GET' && /\/api\/attacks\/[^/]+(?:\/(?:messages|conversations|metadata))?$/.test(url.pathname)) {
      reads.requests.push(url)
    }
  })
  page.on('response', (response: Response): void => {
    const url = new URL(response.url())
    if (response.request().method() === 'GET' && /\/api\/attacks\/[^/]+\/metadata$/.test(url.pathname)) {
      reads.metadata.push({ url, status: response.status(), body: response.json() })
    }
  })
  return reads
}

async function expectBoundedReads(reads: SavedReads): Promise<void> {
  expect(reads.requests.length).toBeGreaterThan(0)
  for (const url of reads.requests) {
    if (url.pathname.endsWith('/metadata')) continue
    expect(url.pathname, 'Saved Chat must not fetch legacy full attack details.').toMatch(/\/(?:messages|conversations)$/)
    expect(url.searchParams.get('limit')).toBe(url.pathname.endsWith('/messages') ? '100' : '50')
  }
  expect(reads.metadata.length).toBeGreaterThan(0)
  for (const read of reads.metadata) {
    expect(read.status).toBe(200)
    const metadata = await read.body
    expect(metadata).not.toHaveProperty('related_conversations')
    expect(metadata).not.toHaveProperty('related_conversation_ids')
    expect(metadata).not.toHaveProperty('last_response')
    expect(metadata.selected_conversation_id).toBe(read.url.searchParams.get('conversation_id') ?? metadata.conversation_id)
    expect(metadata.selected_conversation_valid).toBe(true)
    if (metadata.attack_result_id === SAVED_ATTACK_ID) expect(metadata.related_conversation_count).toBe(120)
  }
}

function waitForSavedRead(
  page: Page, resource: 'messages' | 'conversations', continuation: boolean, conversationId: string = SAVED_MAIN_ID,
): Promise<Response> {
  return page.waitForResponse((response: Response): boolean => {
    const url = new URL(response.url())
    return url.pathname === `/api/attacks/${SAVED_ATTACK_ID}/${resource}`
      && url.searchParams.has('cursor') === continuation
      && (resource === 'conversations' || url.searchParams.get('conversation_id') === conversationId)
  })
}

function messagePieces(response: ConversationMessagesResponse): BackendMessagePiece[] {
  return response.messages.flatMap((message: BackendMessage): BackendMessagePiece[] => message.message_pieces)
}

async function openSaved(page: Page): Promise<ConversationMessagesResponse> {
  const initial = waitForSavedRead(page, 'messages', false)
  await page.goto(SAVED_ROUTE)
  const response = await initial
  expect(response.status()).toBe(200)
  await expect(page.getByText(SAVED_RESPONSE, { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Saved transcript paging' })).toContainText('100 message pieces loaded.')
  const body: ConversationMessagesResponse = await response.json()
  expect(messagePieces(body)).toHaveLength(100)
  expect(body.pagination).toMatchObject({ limit: 100, has_more: true })
  expect(body.partial_message_sequences).toEqual([1])
  expect(body.target_response_status).toBeNull()
  return body
}

test.beforeEach(async ({ page }: { page: Page }): Promise<void> => {
  await controlSavedRead(page, 'reset')
})

async function openHierarchy(page: Page) {
  await page.goto(run)
  const section = page.getByRole('region', { name: 'Atomic attack groups', exact: true })
  await section.getByRole('button', { name: 'Expand attacks in Sequential' }).click()
  await expect(section.getByText('Sequential parent', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Result hierarchy', exact: true })).toHaveCount(0)
  return section
}

test('browses compound results in the existing attack-group table with stable totals', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const section = await openHierarchy(page)
  const totals = section.getByText('2 groups, 524 executions', { exact: true })
  const before = await totals.textContent()

  const firstPage = page.waitForResponse((response) => /\/hierarchy\/nodes\/[^/?]+\?limit=200$/.test(response.url()))
  await section.getByRole('button', { name: 'Expand children of sequential: Sequential parent' }).click()
  expect((await firstPage).status()).toBe(200)

  const table = section.getByRole('table', { name: 'Sequential attack results' })
  const row = (text: string) => table.getByRole('row').filter({ hasText: text }).first()
  await expect(table.getByText('Step 1', { exact: true })).toBeVisible()
  await expect(row('#2')).toContainText('Unresolved reference')
  await expect(row('#4')).toContainText('Unresolved reference')
  await expect(row('Step 5').getByRole('cell').nth(2)).toHaveText('Success')
  await expect(section.getByText('Showing 200 of 520 saved child positions')).toBeVisible()

  for (const shown of [400, 520]) {
    const next = page.waitForResponse((response) => response.url().includes('children_cursor='))
    await section.getByRole('button', { name: 'Load more children' }).click()
    expect((await next).status()).toBe(200)
    await expect(section.getByText(`Showing ${shown} of 520 saved child positions`)).toBeVisible()
  }
  await expect(section.getByRole('button', { name: 'Load more children' })).toHaveCount(0)
  await expect(table.getByText('Step 520', { exact: true })).toBeVisible()

  await table.getByRole('button', { name: 'Expand children of #1 sequential: Step 1' }).click()
  await expect(table.getByText('Nested step 2', { exact: true })).toBeVisible()
  await table.getByRole('button', { name: 'Collapse children of sequential: Sequential parent' }).click()
  await expect(table.getByText('Nested step 2', { exact: true })).toHaveCount(0)
  await table.getByRole('button', { name: 'Expand children of sequential: Sequential parent' }).click()
  await expect(table.getByText('Nested step 2', { exact: true })).toBeVisible()
  await expect(section.getByText('Showing 520 of 520 saved child positions')).toBeVisible()

  await section.getByRole('button', { name: 'Collapse attacks in Sequential' }).click()
  await section.getByRole('button', { name: 'Expand attacks in Sequential' }).click()
  await expect(section.getByText('Showing 520 of 520 saved child positions')).toBeVisible()
  await section.getByRole('button', { name: 'Collapse atomic attack groups' }).click()
  await section.getByRole('button', { name: 'Expand atomic attack groups' }).click()
  await expect(section.getByText('Nested step 2', { exact: true })).toBeVisible()
  expect(await totals.textContent()).toBe(before)

  await section.getByRole('button', { name: 'Open details for #5 sequential: Step 5' }).click()
  const dialog = page.getByRole('dialog', { name: 'sequential' })
  await expect(dialog.getByText('Step 5', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(section.getByRole('button', { name: 'Open details for #5 sequential: Step 5' })).toBeFocused()

  await page.getByRole('heading', { name: 'Atomic attack groups', exact: true }).evaluate((heading) =>
    heading.scrollIntoView({ block: 'start' }),
  )
  await page.screenshot({ path: testInfo.outputPath('scenario-hierarchy-desktop.png'), animations: 'disabled' })
  expect(errors).toEqual([])
})

test('opens an actual saved child chat and retains the existing scenario breadcrumb after a reload', async ({ page }, testInfo) => {
  const reads = captureSavedReads(page)
  const historyPath = '/history/scanner?status=COMPLETED'
  await page.goto(historyPath)
  await expect(page.getByRole('combobox', { name: 'Run status', exact: true })).toHaveValue('COMPLETED')
  await page.getByRole('table', { name: 'Scanner history' }).getByRole('link', { name: /Open .+ scenario run/ }).click()
  await expect(page).toHaveURL(run)
  const section = page.getByRole('region', { name: 'Atomic attack groups', exact: true })
  await section.getByRole('button', { name: 'Expand attacks in Sequential' }).click()
  await section.getByRole('button', { name: 'Expand children of sequential: Sequential parent' }).click()
  const row = section.getByRole('row', { name: 'View conversation for #5 sequential: Step 5' })
  const chatLink = row.getByRole('link', { name: 'Step 5', exact: true })
  await expect(chatLink).toHaveAttribute('href', new RegExp(`\\?scenarioResultId=${scenarioResultId}$`))
  await chatLink.click()

  await expect(page).toHaveURL(new RegExp(`/attacks/[^/]+/conversations/[^/?]+\\?scenarioResultId=${scenarioResultId}$`))
  await expect(page.getByText('Synthetic request for Step 5. No external model was called.', { exact: true })).toBeVisible()
  await expect(page.getByText('Stored example response for Step 5. This is offline sample data.', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Saved transcript paging' })).toContainText('100 message pieces loaded.')
  await expect(page.getByText('Incomplete turn', { exact: true })).toBeVisible()
  const breadcrumb = page.getByRole('link', { name: `Return to scenario run ${scenarioResultId}` })
  await expect(breadcrumb).toHaveAttribute('href', run)

  for (const count of [100, 121]) {
    const controls = page.getByTestId('conversation-panel').getByRole('region', { name: 'Saved conversation references' })
    await controls.getByRole('button', { name: 'Load more conversations', exact: true }).click()
    await expect(controls).toContainText(`${count} of 121 saved conversations loaded.`)
  }
  await page.getByTestId('conversation-panel').getByRole('button', {
    name: `Select conversation ${FIRST_PRUNED_ID}`, exact: true,
  }).click()
  await expect(page).toHaveURL(attackConversationRoutePath(SAVED_ATTACK_ID, FIRST_PRUNED_ID, scenarioResultId))
  await page.reload()
  await expect(page.getByText(`Saved pruned user for ${FIRST_PRUNED_ID}. Offline evidence.`, { exact: true })).toBeVisible()
  await expect(breadcrumb).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('scenario-saved-chat-breadcrumb.png'), animations: 'disabled' })

  await breadcrumb.click()
  await expect(page).toHaveURL(run)
  const groups = page.getByRole('region', { name: 'Atomic attack groups', exact: true })
  await expect(groups.getByText('2 groups, 524 executions', { exact: true })).toBeVisible()
  await groups.getByRole('button', { name: 'Expand attacks in Single turn' }).click()
  const single = groups.getByRole('row', { name: 'View conversation for single: Single-turn attempt' })
  await single.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Stored example response for Single-turn attempt. This is offline sample data.', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: `Return to scenario run ${scenarioResultId}` }).click()
  await expect(page).toHaveURL(run)
  await page.getByRole('link', { name: 'Back to scanner history', exact: true }).click()
  await expect(page).toHaveURL(historyPath)
  await expect(page.getByRole('combobox', { name: 'Run status', exact: true })).toHaveValue('COMPLETED')
  await expectBoundedReads(reads)
})

test('merges bounded piece pages and preserves the complete transcript when adding and removing a human score', async (
  { page }: { page: Page }, testInfo: TestInfo,
): Promise<void> => {
  const reads = captureSavedReads(page)
  const first = await openSaved(page)
  const firstPieces = messagePieces(first)
  expect(first.messages.map((message: BackendMessage): number => message.turn_number)).toEqual([-1, 0, 1])
  expect(firstPieces[firstPieces.length - 1].id).toBe('ffffffff-ffff-ffff-ffff-ffffffffffff')
  await expect(page.getByRole('button', { name: 'Continue manually', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Export conversation', exact: true })).toBeDisabled()

  const request = page.getByTestId('message-bubble-1')
  await request.getByRole('button', { name: 'Copy or Branch' }).click()
  await expect(page.getByRole('menuitem', { name: 'Branch attack', exact: true })).toHaveAttribute('aria-disabled', 'true')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toHaveCount(0)
  await expect(request.getByRole('button', { name: 'Copy or Branch' })).toBeFocused()
  const anchor = page.getByText('Saved response piece 10.', { exact: true })
  await anchor.evaluate((element: Element): void => { element.scrollIntoView({ block: 'start' }) })
  await expect(anchor).toBeInViewport()
  const anchorElement = await anchor.elementHandle()
  const anchorPiece = firstPieces.find((piece: BackendMessagePiece): boolean => piece.converted_value === 'Saved response piece 10.')
  expect(anchorPiece?.id).toBeDefined()

  const secondRead = waitForSavedRead(page, 'messages', true)
  const originalImageRead = page.waitForResponse((response: Response): boolean =>
    response.url().includes('saved-evidence-original.png'))
  await page.getByRole('button', { name: 'Load more message pieces', exact: true }).click()
  const second: ConversationMessagesResponse = await (await secondRead).json()
  const secondPieces = messagePieces(second)
  expect(secondPieces).toHaveLength(100)
  expect(secondPieces[0].id).toBe(firstPieces[firstPieces.length - 1].id)
  expect(second.partial_message_sequences).toContain(1)
  expect(second.target_response_status).toBeNull()
  await expect(page.getByRole('region', { name: 'Saved transcript paging' })).toContainText('199 message pieces loaded.')
  expect(await anchorElement?.evaluate((element: Element): boolean => element.isConnected)).toBe(true)
  await expect(anchor).toHaveCount(1)
  const originalImageResponse = await originalImageRead
  expect(originalImageResponse.status(), originalImageResponse.status() === 200 ? undefined : await originalImageResponse.text()).toBe(200)
  await expect(anchor).toBeInViewport()
  await expect(page.getByText('Incomplete turn', { exact: true })).toHaveCount(0)

  const turn = page.getByTestId('message-bubble-2')
  await expect(turn.getByText(SAVED_RESPONSE, { exact: true })).toHaveCount(1)
  await expect(turn.getByText('Earlier microsecond piece.', { exact: true })).toHaveCount(1)
  await expect(turn.getByText('Later microsecond piece.', { exact: true })).toHaveCount(1)
  const orderedText = (await turn.getByTestId(/^message-piece-2-/).allTextContents()).join('\n')
  expect(orderedText.indexOf('Earlier microsecond piece.')).toBeLessThan(orderedText.indexOf('Later microsecond piece.'))
  const media = secondPieces.find((piece: BackendMessagePiece): boolean => piece.converted_value_data_type === 'image_path')
  if (!media?.original_filename || !media.converted_filename) throw new Error('The saved image is missing its media filenames.')
  expect(media?.original_value_url).toContain('saved-evidence-original.png')
  expect(media?.converted_value_url).toContain('saved-evidence-converted.png')
  expect(media?.converter_identifiers?.[0]?.class_name).toBe('OfflineImageConverter')
  expect(media?.scores).toHaveLength(1)
  expect(media?.scores[0].scorer_class_identifier?.class_name).toBe('OfflineEvidenceScorer')
  for (const filename of [media.original_filename, media.converted_filename]) {
    const image = turn.getByRole('img', { name: filename, exact: true })
    await expect(image).toBeVisible()
    await expect.poll(async (): Promise<number> => image.evaluate((element: Element): number => {
      if (!(element instanceof HTMLImageElement)) throw new Error('Saved media did not render an image.')
      return element.naturalWidth
    })).toBe(8)
  }
  const score = turn.getByRole('button', { name: /^Score 0\.75 from / })
  await expect(score).toHaveCount(1)
  await score.click()
  await expect(page.getByText('Locally stored score for the converted saved image.', { exact: true })).toBeVisible()
  await expect(page.getByText('Supporting score', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('scenario-saved-multimodal-desktop.png'), animations: 'disabled' })
  await page.keyboard.press('Escape')

  const lastRead = waitForSavedRead(page, 'messages', true)
  await page.getByRole('button', { name: 'Load more message pieces', exact: true }).click()
  const last: ConversationMessagesResponse = await (await lastRead).json()
  expect(messagePieces(last)).toHaveLength(24)
  expect(last.pagination).toMatchObject({ limit: 100, has_more: false, next_cursor: null })
  expect(last.target_response_status).toBeNull()
  expect(new Set([...firstPieces, ...secondPieces, ...messagePieces(last)].map((piece: BackendMessagePiece): string => piece.id)).size)
    .toBe(222)
  await expect(page.getByRole('region', { name: 'Saved transcript paging' })).toContainText('End of saved transcript. 222 message pieces loaded.')
  await expect(page.getByTestId(/^message-bubble-/)).toHaveCount(123)
  await expect(page.getByRole('button', { name: 'Load more message pieces', exact: true })).toHaveCount(0)
  await page.getByRole('region', { name: 'Default Labels', exact: true }).getByRole('button', { name: 'Clear', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Continue manually', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Export conversation', exact: true })).toBeEnabled()

  const tail = page.getByTestId('message-list').getByText('Saved continuation turn 121. Offline evidence.', { exact: true })
  const tailElement = await tail.elementHandle()
  const waitForScoreRefresh = (): Promise<Response> => page.waitForResponse(async (response: Response): Promise<boolean> => {
    const url = new URL(response.url())
    if (response.request().method() !== 'GET' || url.pathname !== `/api/attacks/${SAVED_ATTACK_ID}/messages`) return false
    const body: ConversationMessagesResponse = await response.json()
    return body.pagination?.has_more === false
  })
  await page.getByRole('button', { name: /objective achieved outcome/i }).click()
  await page.getByRole('radio', { name: 'Failure', exact: true }).check()
  await page.getByRole('textbox', { name: 'Rationale', exact: true }).fill('Offline human review of all 222 saved pieces.')
  const scoreCreated = page.waitForResponse((response: Response): boolean =>
    response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/scores/manual',
  )
  const addedScoresRefreshed = waitForScoreRefresh()
  await page.getByRole('button', { name: 'Add', exact: true }).click()
  const createdResponse = await scoreCreated
  expect(createdResponse.status(), await createdResponse.text()).toBe(201)
  expect((await addedScoresRefreshed).status()).toBe(200)
  await expect(page.getByText('Attack Result Details', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Saved transcript paging' }))
    .toContainText('End of saved transcript. 222 message pieces loaded.')
  await expect(page.getByTestId(/^message-bubble-/)).toHaveCount(123)
  expect(await tailElement?.evaluate((element: Element): boolean => element.isConnected)).toBe(true)
  await expect(tail).toHaveCount(1)
  await expect(score).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Continue manually', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Export conversation', exact: true })).toBeEnabled()

  await page.getByRole('button', { name: /objective achieved outcome/i }).click()
  await expect(page.getByRole('textbox', { name: 'Rationale', exact: true }))
    .toHaveValue('Offline human review of all 222 saved pieces.')
  await expect(page.getByRole('radio', { name: 'Failure', exact: true })).toBeChecked()
  const scoreRemoved = page.waitForResponse((response: Response): boolean =>
    response.request().method() === 'DELETE' && new URL(response.url()).pathname === `/api/attacks/${SAVED_ATTACK_ID}/human-score`,
  )
  const removedScoresRefreshed = waitForScoreRefresh()
  await page.getByRole('button', { name: 'Remove', exact: true }).click()
  const removedResponse = await scoreRemoved
  expect(removedResponse.status(), await removedResponse.text()).toBe(200)
  expect((await removedScoresRefreshed).status()).toBe(200)
  await expect(page.getByRole('button', { name: 'Remove', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('region', { name: 'Saved transcript paging' }))
    .toContainText('End of saved transcript. 222 message pieces loaded.')
  await expect(page.getByTestId(/^message-bubble-/)).toHaveCount(123)
  expect(await tailElement?.evaluate((element: Element): boolean => element.isConnected)).toBe(true)
  await expect(tail).toHaveCount(1)
  await expect(score).toHaveCount(1)
  await expect(page.getByRole('button', { name: 'Continue manually', exact: true })).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Export conversation', exact: true })).toBeEnabled()
  await expectBoundedReads(reads)
})

test('retains loaded evidence through continuation errors and a failed bounded restart', async (
  { page }: { page: Page }, testInfo: TestInfo,
): Promise<void> => {
  const reads = captureSavedReads(page)
  await openSaved(page)
  const controls = page.getByRole('region', { name: 'Saved transcript paging' })
  await controlSavedRead(page, 'fail_messages')
  const failed = waitForSavedRead(page, 'messages', true)
  await page.getByRole('button', { name: 'Load more message pieces', exact: true }).click()
  expect((await failed).status()).toBe(503)
  await expect(controls).toContainText('100 message pieces loaded.')
  await expect(controls).toContainText('Loaded evidence is still shown.')
  await expect(page.getByText(SAVED_RESPONSE, { exact: true })).toBeVisible()
  const retry = waitForSavedRead(page, 'messages', true)
  await page.getByRole('button', { name: 'Retry more message pieces', exact: true }).click()
  expect((await retry).status()).toBe(200)
  await expect(controls).toContainText('199 message pieces loaded.')

  await controlSavedRead(page, 'fail_messages', 400)
  const stale = waitForSavedRead(page, 'messages', true)
  await page.getByRole('button', { name: 'Load more message pieces', exact: true }).click()
  expect((await stale).status()).toBe(400)
  await expect(controls).toContainText('199 message pieces loaded.')
  await controlSavedRead(page, 'fail_messages')
  const failedRestart = waitForSavedRead(page, 'messages', false)
  await page.getByRole('button', { name: 'Restart transcript loading', exact: true }).click()
  expect((await failedRestart).status()).toBe(503)
  await expect(page.getByText(SAVED_RESPONSE, { exact: true })).toBeVisible()
  await expect(controls).toContainText('199 message pieces loaded.')
  await expect(page.getByRole('button', { name: 'Restart transcript loading', exact: true })).toBeEnabled()
  await page.screenshot({ path: testInfo.outputPath('scenario-saved-transcript-restart-error.png'), animations: 'disabled' })
  const restart = waitForSavedRead(page, 'messages', false)
  await page.getByRole('button', { name: 'Restart transcript loading', exact: true }).click()
  expect((await restart).status()).toBe(200)
  await expect(controls).toContainText('100 message pieces loaded.')
  await expect(controls).not.toContainText('Loaded evidence is still shown.')
  await expectBoundedReads(reads)
})

test('pages references independently and retains rows through retry and restart failures', async (
  { page }: { page: Page }, testInfo: TestInfo,
): Promise<void> => {
  const reads = captureSavedReads(page)
  const firstRead = waitForSavedRead(page, 'conversations', false)
  const transcript = await openSaved(page)
  const first: AttackConversationsResponse = await (await firstRead).json()
  expect(first.conversations).toHaveLength(50)
  expect(first.total_count).toBe(121)
  expect(first.pagination).toMatchObject({ limit: 50, has_more: true })
  expect(first.pagination?.next_cursor).not.toBe(transcript.pagination?.next_cursor)
  const panel = page.getByTestId('conversation-panel')
  const controls = panel.getByRole('region', { name: 'Saved conversation references' })
  await expect(controls).toContainText('50 of 121 saved conversations loaded.')

  await controlSavedRead(page, 'fail_references')
  const failure = waitForSavedRead(page, 'conversations', true)
  await controls.getByRole('button', { name: 'Load more conversations', exact: true }).click()
  expect((await failure).status()).toBe(503)
  await expect(panel.getByRole('button', { name: /^Select conversation / })).toHaveCount(50)
  await expect(controls).toContainText('Loaded references are still available.')
  await controlSavedRead(page, 'hold_references')
  const retry = waitForSavedRead(page, 'conversations', true)
  await controls.getByRole('button', { name: 'Retry more conversations', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect.poll(async (): Promise<boolean> => (await controlSavedRead(page, 'status')).reference_waiting).toBe(true)
  await expect(controls.getByRole('status')).toContainText(/loading/i)
  await expect(controls.getByRole('status')).toBeFocused()
  await expect(panel.getByRole('button', { name: /^Select conversation / })).toHaveCount(50)
  await controlSavedRead(page, 'release_references')
  const second: AttackConversationsResponse = await (await retry).json()
  expect(second.conversations).toHaveLength(50)
  await expect(controls).toContainText('100 of 121 saved conversations loaded.')

  await controlSavedRead(page, 'fail_references', 400)
  const stale = waitForSavedRead(page, 'conversations', true)
  await controls.getByRole('button', { name: 'Load more conversations', exact: true }).click()
  expect((await stale).status()).toBe(400)
  await controlSavedRead(page, 'fail_references')
  const failedRestart = waitForSavedRead(page, 'conversations', false)
  await controls.getByRole('button', { name: 'Restart conversation references', exact: true }).click()
  expect((await failedRestart).status()).toBe(503)
  await expect(panel.getByRole('button', { name: /^Select conversation / })).toHaveCount(100)
  await expect(controls.getByRole('button', { name: 'Restart conversation references', exact: true })).toBeEnabled()
  await page.screenshot({ path: testInfo.outputPath('scenario-saved-reference-restart-error.png'), animations: 'disabled' })
  const restart = waitForSavedRead(page, 'conversations', false)
  await controls.getByRole('button', { name: 'Restart conversation references', exact: true }).click()
  expect((await restart).status()).toBe(200)
  for (const count of [100, 121]) {
    const next = waitForSavedRead(page, 'conversations', true)
    await controls.getByRole('button', { name: 'Load more conversations', exact: true }).click()
    const response: AttackConversationsResponse = await (await next).json()
    expect(response.conversations.length).toBeLessThanOrEqual(50)
    await expect(controls).toContainText(`${count} of 121 saved conversations loaded.`)
  }
  await expect(controls).toContainText('End of saved conversation references.')
  await expect(controls.getByRole('button', { name: 'Load more conversations', exact: true })).toHaveCount(0)
  expect(reads.requests.filter((url: URL): boolean => url.pathname.endsWith('/messages'))).toHaveLength(1)
  await expectBoundedReads(reads)
})

test('owns pending continuations across rapid selection, attack navigation and unmount', async (
  { page }: { page: Page },
): Promise<void> => {
  const reads = captureSavedReads(page)
  await openSaved(page)
  const panel = page.getByTestId('conversation-panel')
  for (const count of [100, 121]) {
    await panel.getByRole('button', { name: 'Load more conversations', exact: true }).click()
    await expect(panel.getByRole('region', { name: 'Saved conversation references' }))
      .toContainText(`${count} of 121 saved conversations loaded.`)
  }
  const transcript = page.getByTestId('message-list')
  expect(await transcript.evaluate((element: HTMLElement | SVGElement): string => getComputedStyle(element).overflowY)).toBe('auto')
  await transcript.evaluate((element: HTMLElement | SVGElement): void => { element.scrollTop = element.scrollHeight })
  expect(await transcript.evaluate((element: HTMLElement | SVGElement): number => element.scrollTop)).toBeGreaterThan(0)
  await controlSavedRead(page, 'hold_messages')
  const held = waitForSavedRead(page, 'messages', true)
  await page.getByRole('button', { name: 'Load more message pieces', exact: true }).click()
  await expect.poll(async (): Promise<boolean> => (await controlSavedRead(page, 'status')).waiting).toBe(true)
  await expect(page.getByRole('region', { name: 'Saved transcript paging' }).getByRole('status'))
    .toContainText(/loading.*message/i)
  const loading = page.getByRole('button', { name: 'Loading more message pieces...', exact: true })
  await expect(loading).toBeDisabled()
  await loading.press('Enter')
  expect(await transcript.evaluate((element: HTMLElement | SVGElement): number => element.scrollTop)).toBeGreaterThan(0)
  expect(reads.requests.filter((url: URL): boolean => url.pathname.endsWith('/messages') && url.searchParams.has('cursor'))).toHaveLength(1)
  const selected = waitForSavedRead(page, 'messages', false, OFF_PAGE_ID)
  await panel.getByRole('button', { name: `Select conversation ${OFF_PAGE_ID}`, exact: true }).focus()
  await page.keyboard.press('Enter')
  expect((await selected).status()).toBe(200)
  await expect(page).toHaveURL(attackConversationRoutePath(SAVED_ATTACK_ID, OFF_PAGE_ID, scenarioResultId))
  await controlSavedRead(page, 'release_messages')
  expect((await held).status()).toBe(200)
  await expect(page.getByText(`Saved pruned user for ${OFF_PAGE_ID}. Offline evidence.`, { exact: true })).toBeVisible()
  await expect(page.getByText(`Saved pruned user for ${OFF_PAGE_ID}. Offline evidence.`, { exact: true }))
    .toBeInViewport({ ratio: 0.5 })
  expect(await transcript.evaluate((element: HTMLElement | SVGElement): number => element.scrollTop)).toBe(0)
  await expect(page.getByText(SAVED_REQUEST, { exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Saved transcript paging' })).toContainText('End of saved transcript. 2 message pieces loaded.')

  const returnRead = waitForSavedRead(page, 'messages', false)
  await panel.getByRole('button', { name: `Select conversation ${SAVED_MAIN_ID}`, exact: true }).click()
  expect((await returnRead).status()).toBe(200)
  await controlSavedRead(page, 'hold_messages')
  const unmounted = waitForSavedRead(page, 'messages', true)
  await page.getByRole('button', { name: 'Load more message pieces', exact: true }).click()
  await expect.poll(async (): Promise<boolean> => (await controlSavedRead(page, 'status')).waiting).toBe(true)
  await page.getByRole('link', { name: `Return to scenario run ${scenarioResultId}` }).click()
  const groups = page.getByRole('region', { name: 'Atomic attack groups', exact: true })
  await groups.getByRole('button', { name: 'Expand attacks in Single turn' }).click()
  await groups.getByRole('row', { name: 'View conversation for single: Single-turn attempt' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Stored example response for Single-turn attempt. This is offline sample data.', { exact: true })).toBeVisible()
  await controlSavedRead(page, 'release_messages')
  expect((await unmounted).status()).toBe(200)
  await expect(page.getByText(SAVED_RESPONSE, { exact: true })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Saved transcript paging' })).toContainText('End of saved transcript. 2 message pieces loaded.')
  await expectBoundedReads(reads)
})

test.describe('on a narrow touch screen', () => {
  test.use({ hasTouch: true })

  test('keeps grouped hierarchy rows usable on touch and from the keyboard', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const section = await openHierarchy(page)

    const expand = section.getByRole('button', { name: 'Expand children of sequential: Sequential parent' })
    await expand.focus()
    await page.keyboard.press('Enter')
    await expect(section.getByRole('button', { name: 'Collapse children of sequential: Sequential parent' })).toHaveAttribute(
      'aria-expanded', 'true',
    )
    await expect(section.getByText('Showing 200 of 520 saved child positions')).toBeVisible()

    const loadMore = section.getByRole('button', { name: 'Load more children' })
    const size = await loadMore.boundingBox()
    expect(size?.height).toBeGreaterThanOrEqual(44)
    const overflow = await section.evaluate((element) => element.scrollWidth > element.clientWidth)
    expect(overflow).toBe(false)

    await section.getByText('Sequential', { exact: true }).evaluate((label) => label.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: testInfo.outputPath('scenario-hierarchy-mobile.png'), animations: 'disabled' })
  })

  test('pages the selector by keyboard and reloads a conversation outside its first page', async (
    { page }: { page: Page }, testInfo: TestInfo,
  ): Promise<void> => {
    await page.setViewportSize({ width: 390, height: 844 })
    const reads = captureSavedReads(page)
    const first = await openSaved(page)
    const systemContext = page.getByText('Saved system context for Step 5. This is offline evidence.', { exact: true })
    await expect(systemContext).toBeInViewport({ ratio: 0.5 })
    await page.screenshot({ path: testInfo.outputPath('scenario-saved-main-mobile.png'), animations: 'disabled' })
    const moreMessages = page.getByRole('button', { name: 'Load more message pieces', exact: true })
    const size = await moreMessages.boundingBox()
    expect(size?.height).toBeGreaterThanOrEqual(44)
    expect(await page.getByTestId('chat-area').evaluate((element: HTMLElement | SVGElement): boolean =>
      element.scrollWidth > element.clientWidth)).toBe(false)
    await moreMessages.focus()
    await expect(moreMessages).toBeFocused()
    await expect(moreMessages).toBeInViewport()
    const anchorValue = 'Saved response piece 96.'
    const anchor = page.getByText(anchorValue, { exact: true })
    await anchor.evaluate((element: Element): void => { element.scrollIntoView({ block: 'start' }) })
    await expect(moreMessages).toBeFocused()
    const anchorElement = await anchor.elementHandle()
    const anchorPiece = messagePieces(first).find((piece: BackendMessagePiece): boolean => piece.converted_value === anchorValue)
    if (!anchorPiece) throw new Error('The visible reading anchor has no saved piece identity.')
    await expect(anchor).toBeInViewport({ ratio: 0.5 })
    const anchorTop = await anchor.evaluate((element: Element): number => element.getBoundingClientRect().top)
    const continuation = waitForSavedRead(page, 'messages', true)
    await page.keyboard.press('Enter')
    const second: ConversationMessagesResponse = await (await continuation).json()
    expect(messagePieces(second)).toHaveLength(100)
    expect(messagePieces(second)[0]).toEqual(messagePieces(first)[99])
    const retainedPieces = new Map([...messagePieces(first), ...messagePieces(second)]
      .map((piece: BackendMessagePiece): [string, BackendMessagePiece] => [piece.id, piece]))
    expect(retainedPieces.size).toBe(199)
    expect(retainedPieces.get(anchorPiece.id)).toEqual(anchorPiece)
    await expect(page.getByRole('region', { name: 'Saved transcript paging' })).toContainText('199 message pieces loaded.')
    expect(await anchorElement?.evaluate((element: Element): boolean => element.isConnected)).toBe(true)
    await expect(anchor).toHaveCount(1)
    await expect(anchor).toBeInViewport({ ratio: 0.5 })
    expect(await anchor.evaluate((element: Element): number => element.getBoundingClientRect().top)).toBeCloseTo(anchorTop, 0)
    const toggle = page.getByRole('button', { name: 'Toggle conversations panel', exact: true })
    const firstReferenceRead = waitForSavedRead(page, 'conversations', false)
    await toggle.focus()
    await page.keyboard.press('Enter')
    const firstReferences: AttackConversationsResponse = await (await firstReferenceRead).json()
    const firstReferenceIds = new Set(firstReferences.conversations.map((reference: ConversationSummary): string => reference.conversation_id))
    const loadedReferences = [...firstReferences.conversations]
    const panel = page.getByTestId('conversation-panel')
    const controls = panel.getByRole('region', { name: 'Saved conversation references' })
    await expect(controls).toContainText('50 of 121 saved conversations loaded.')
    for (const count of [100, 121]) {
      const next = waitForSavedRead(page, 'conversations', true)
      const more = controls.getByRole('button', { name: 'Load more conversations', exact: true })
      const bounds = await more.boundingBox()
      expect(bounds?.height).toBeGreaterThanOrEqual(44)
      await more.focus()
      await expect(more).toBeFocused()
      await page.keyboard.press('Space')
      const response: AttackConversationsResponse = await (await next).json()
      expect(response.conversations.length).toBeLessThanOrEqual(50)
      loadedReferences.push(...response.conversations)
      await expect(controls).toContainText(`${count} of 121 saved conversations loaded.`)
      await expect(page.getByRole('dialog', { name: 'Attack Conversations', exact: true })).toBeVisible()
      await expect(controls.getByRole('status')).toBeFocused()
    }
    const row = panel.getByRole('button', { name: `Select conversation ${OFF_PAGE_ID}`, exact: true })
    expect((await row.boundingBox())?.height).toBeGreaterThanOrEqual(44)
    await row.focus()
    await page.keyboard.press('Space')
    await expect(page).toHaveURL(attackConversationRoutePath(SAVED_ATTACK_ID, OFF_PAGE_ID, scenarioResultId))
    await expect(toggle).toBeFocused()
    await expect(page.getByText(`Saved pruned user for ${OFF_PAGE_ID}. Offline evidence.`, { exact: true })).toBeVisible()
    expect(await page.getByTestId('chat-area').evaluate((element: HTMLElement | SVGElement): number => element.scrollTop)).toBe(0)
    await expect(page.getByText(`Saved pruned user for ${OFF_PAGE_ID}. Offline evidence.`, { exact: true }))
      .toBeInViewport({ ratio: 0.5 })
    await page.screenshot({ path: testInfo.outputPath('scenario-saved-pruned-mobile.png'), animations: 'disabled' })
    await page.reload()
    await expect(page.getByText(`Saved pruned user for ${OFF_PAGE_ID}. Offline evidence.`, { exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: `Return to scenario run ${scenarioResultId}` })).toBeVisible()
    const outsidePageId = !firstReferenceIds.has(OFF_PAGE_ID) ? OFF_PAGE_ID
      : loadedReferences.find((reference: ConversationSummary): boolean => !firstReferenceIds.has(reference.conversation_id))?.conversation_id
    if (!outsidePageId) throw new Error('The saved fixture has no conversation outside its initial reference page.')
    await page.goto(attackConversationRoutePath(SAVED_ATTACK_ID, outsidePageId, scenarioResultId))
    await page.reload()
    await expect(page.getByText(`Saved pruned user for ${outsidePageId}. Offline evidence.`, { exact: true })).toBeVisible()
    await toggle.focus()
    await page.keyboard.press('Enter')
    await expect(controls).toContainText('50 of 121 saved conversations loaded.')
    await expect(panel.getByText(`Selected conversation ${outsidePageId} is outside the loaded references. Its transcript remains selected.`))
      .toBeVisible()
    expect(await panel.evaluate((element: HTMLElement | SVGElement): boolean => element.scrollWidth > element.clientWidth)).toBe(false)
    await page.screenshot({ path: testInfo.outputPath('scenario-saved-off-page-mobile.png'), animations: 'disabled' })
    await panel.getByRole('button', { name: 'Close panel', exact: true }).click()

    await page.goto(attackConversationRoutePath(SAVED_ATTACK_ID, OFF_PAGE_ID))
    await expect(page.getByText(`Saved pruned user for ${OFF_PAGE_ID}. Offline evidence.`, { exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Saved transcript paging' })).toContainText('End of saved transcript. 2 message pieces loaded.')
    await expect(page.getByRole('link', { name: `Return to scenario run ${scenarioResultId}` })).toHaveCount(0)
    await expectBoundedReads(reads)
  })
})

import { test, expect } from './_fixtures'
import { makeAddMessageResponse } from './_attacks'
import { READY_RUNTIME } from './_runtime'
import { makeTarget } from './_targets'
import type { AttackSummary, BackendMessage, RegisteredComponentReference, MessageSendRequest, ScoringFailure } from '@/types'

test('selects a chat scorer, displays its result, and retains scoring errors after the notice expires', async ({ page }) => {
  const target = makeTarget({ target_registry_name: 'objective' })
  const reference: RegisteredComponentReference = { registry_name: 'judge', identifier_hash: 'judge-hash' }
  let messages: BackendMessage[] = []
  let submissions = 0
  let scoringFailure: ScoringFailure | null = null
  let attack: AttackSummary = makeAddMessageResponse('scoring-attack', 'scoring-conversation', []).attack
  attack = { ...attack, objective: 'Return a useful response', target: {
    target_type: target.identifier.class_name, identifier_hash: target.identifier.hash,
    target_registry_name: target.target_registry_name,
  } }
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/runtime') { await route.fulfill({ json: READY_RUNTIME }); return }
    if (path === '/api/targets') {
      await route.fulfill({ json: { items: [target], pagination: { has_more: false } } }); return
    }
    if (path === '/api/targets/objective') { await route.fulfill({ json: target }); return }
    if (path === '/api/auth/access') { await route.fulfill({ json: { isAdmin: false } }); return }
    if (path === '/api/labels') { await route.fulfill({ json: { source: 'attacks', labels: {} } }); return }
    if (path === '/api/scorers') {
      await route.fulfill({ json: { items: [{
        scorer_registry_name: 'judge', identifier: { class_name: 'SubStringScorer', hash: 'judge-hash', scorer_type: 'true_false' },
      }], pagination: { has_more: false } } }); return
    }
    if (path === '/api/attacks/scoring-attack') {
      if (route.request().method() === 'PATCH') {
        const update = route.request().postDataJSON()
        attack = { ...attack, chat_scorer: update.chat_scorer }
      }
      await route.fulfill({ json: attack }); return
    }
    if (path === '/api/attacks/scoring-attack/messages') {
      await route.fulfill({ json: { conversation_id: 'scoring-conversation', messages,
        target_response_status: null, scoring_failure: scoringFailure } }); return
    }
    if (path === '/api/attacks/scoring-attack/conversations') {
      await route.fulfill({ json: { attack_result_id: attack.attack_result_id, main_conversation_id: attack.conversation_id,
        conversations: [{ conversation_id: attack.conversation_id, message_count: messages.length }] } }); return
    }
    if (path === '/api/attacks/scoring-attack/message-sends') {
      const request: MessageSendRequest = route.request().postDataJSON()
      submissions++
      const pieceId = `response-${submissions}`
      messages = [...messages, ...(['user', 'assistant'] as const).map((role, index): BackendMessage => ({
        role, turn_number: messages.length + index, created_at: '2026-01-01T00:00:00Z',
        message_pieces: [{
          id: role === 'user' ? `request-${submissions}` : pieceId,
          original_value: role === 'user' ? request.pieces[0].original_value : `Delivered response ${submissions}`,
          converted_value: role === 'user' ? request.pieces[0].original_value : `Delivered response ${submissions}`,
          original_value_data_type: 'text', converted_value_data_type: 'text', response_error: 'none', scores: [],
        }],
      }))]
      const failed = submissions === 2
      attack = { ...attack, message_count: messages.length, outcome: failed ? 'undetermined' : 'success',
        last_response: messages[messages.length - 1].message_pieces[0],
        automated_score: failed ? null : {
          id: 'score', message_piece_id: pieceId, score_type: 'true_false', score_value: 'True',
          score_rationale: 'Objective satisfied', scorer_type: 'SubStringScorer', timestamp: '2026-01-01T00:00:00Z',
        },
      }
      scoringFailure = failed ? { scorer: reference, objective: attack.objective, response_id: pieceId,
        conversation_id: attack.conversation_id, error: 'The judge was unavailable.' } : null
      await route.fulfill({ status: 202, json: {
        send_id: request.submission_id, attack_result_id: attack.attack_result_id,
        conversation_id: attack.conversation_id, request_turn_number: messages.length - 2,
        state: 'completed', error: null, failure_stage: null,
        scoring_error: failed ? 'The judge was unavailable.' : null,
      } }); return
    }
    await route.fallback()
  })
  await page.goto('/attacks/scoring-attack/conversations/scoring-conversation')
  await page.getByRole('button', { name: /objective achieved outcome/i }).click()
  await page.getByRole('button', { name: /^automated scorer:/i }).click()
  await page.getByRole('combobox', { name: 'Chat scorer' }).selectOption('registered:judge')
  await expect.poll(() => attack.chat_scorer).toEqual(reference)
  expect(submissions).toBe(0)
  await page.keyboard.press('Escape')
  await page.getByPlaceholder('Type prompt here').fill('First response')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(page.getByText('Delivered response 1', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Objective achieved outcome: success' })).toBeVisible()
  await page.clock.install()
  await page.getByPlaceholder('Type prompt here').fill('Second response')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  const notice = page.getByText('A scoring error occurred. Open the score above for details.')
  await expect(notice).toBeVisible()
  await page.clock.runFor(10_001)
  await expect(notice).toHaveCount(0)
  await expect(page.getByText('Delivered response 2', { exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: /objective achieved outcome/i }).click()
  await page.getByRole('button', { name: /scoring error.*view details/i }).click()
  await expect(page.getByText('Scorer judge: The judge was unavailable.')).toBeVisible()
  expect(submissions).toBe(2)
})

import React from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { operationsApi } from '@/services/api'
import type { BackendScore, FindingEvidenceItem } from '@/types'
import FindingEvidenceList from './FindingEvidenceList'

jest.mock('@/services/api', () => ({
  operationsApi: { listFindingEvidence: jest.fn(), detachFindingEvidence: jest.fn() },
}))
const ID = '123e4567-e89b-12d3-a456-426614174000'
const EVIDENCE: FindingEvidenceItem = {
  item: { id: ID, finding_id: 'finding', attack_result_id: 'attack/1', attached_at: '2026-10-08T12:00:00Z' },
  availability: 'available', scenario_result_id: ID,
  source_operation_id: 'operation', source_operation_name: 'This operation',
  objective: 'Get the recipe', outcome: 'success', score: null, conversation_id: 'source/1',
}
const SCORE: BackendScore = {
  id: 'score', message_piece_id: 'piece', scorer_type: 'SelfAskTrueFalseScorer', score_type: 'true_false',
  score_value: 'true', score_rationale: 'The response complied.', timestamp: '2026-10-08T12:00:00Z',
}
const EMPTY = { items: [], has_more: false, next_offset: null }

function renderList(onDetached = jest.fn()): void {
  render(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <FindingEvidenceList operationId="operation" findingId="finding" count={1} onDetached={onDetached} />
  </MemoryRouter></FluentProvider>)
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({ ...EMPTY, items: [EVIDENCE] })
  jest.mocked(operationsApi.detachFindingEvidence).mockResolvedValue(undefined)
})

it('loads only on expansion and opens the conversation holding the score', async () => {
  const user = userEvent.setup()
  renderList()
  expect(operationsApi.listFindingEvidence).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  expect(await screen.findByText('Get the recipe')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open attack' })).toHaveAttribute('href',
    `/attacks/attack%2F1/conversations/source%2F1?scenarioResultId=${ID}&findingEvidenceId=${ID}`)
})

it("shows each attack's objective, outcome, and current score", async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({ ...EMPTY, items: [
    { ...EVIDENCE, score: SCORE },
    {
      ...EVIDENCE, item: { ...EVIDENCE.item, id: 'unscored-link', attack_result_id: 'attack/2' },
      objective: 'Unscored objective', outcome: 'undetermined', conversation_id: 'main/2',
    },
  ] })
  renderList()
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  const scored = await screen.findByRole('row', { name: /Get the recipe/ })
  const unscored = screen.getByRole('row', { name: /Unscored objective/ })
  expect(within(scored).getByText('success')).toBeInTheDocument()
  expect(within(scored).getByText('SelfAskTrueFalseScorer')).toBeInTheDocument()
  expect(within(scored).getByText('The response complied.')).toBeInTheDocument()
  expect(within(unscored).getByText('undetermined')).toBeInTheDocument()
  expect(within(unscored).getByText('Not scored')).toBeInTheDocument()
})

it.each(['success', 'failure', 'undetermined', 'error'] as const)(
  "badges a %s attack with the same outcome as chat, whatever its score's value",
  async (outcome) => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({
      ...EMPTY, items: [{ ...EVIDENCE, outcome, score: SCORE }],
    })
    renderList()
    await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
    const row = await screen.findByRole('row', { name: /Get the recipe/ })
    expect(within(row).getByText(outcome)).toBeInTheDocument()
  },
)

it.each([
  ['the same operation', 'operation', 'This operation', null],
  ['another operation', 'other', 'Other engagement', 'From operation: Other engagement'],
  ['a legacy operation name', null, 'Test', 'From operation: Test'],
  ['no operation', null, null, 'Not in an operation'],
])('shows where evidence from %s came from', async (
  _source: string, sourceId: string | null, sourceName: string | null, shown: string | null,
) => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({
    ...EMPTY, items: [{ ...EVIDENCE, source_operation_id: sourceId, source_operation_name: sourceName }],
  })
  renderList()
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  await screen.findByText('Get the recipe')
  if (shown) expect(screen.getByText(shown)).toBeInTheDocument()
  else expect(screen.queryByText(/From operation|Not in an operation/)).not.toBeInTheDocument()
})

it('retains unavailable identity, confirms association-only removal, and refreshes count', async () => {
  const user = userEvent.setup()
  const onDetached = jest.fn()
  // An attack that no longer exists has no objective or outcome, so the row names it by ID.
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({ ...EMPTY, items: [{
    ...EVIDENCE, availability: 'unavailable', source_operation_id: null, source_operation_name: null,
    objective: null, outcome: null, conversation_id: null,
  }] })
  renderList(onDetached)
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  expect(await screen.findByText('Evidence unavailable')).toBeInTheDocument()
  expect(screen.getByText('attack/1')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Open attack' })).not.toBeInTheDocument()
  expect(screen.queryByText(/Not scored|Not in an operation/)).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Remove link: attack/1' }))
  expect(screen.getByRole('dialog')).toHaveTextContent('attack/1')
  expect(screen.getByRole('dialog')).toHaveTextContent('The attack is not deleted')
  expect(operationsApi.detachFindingEvidence).not.toHaveBeenCalled()
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue(EMPTY)
  await user.click(screen.getByRole('button', { name: 'Remove evidence link' }))
  await waitFor(() => { expect(onDetached).toHaveBeenCalledTimes(1) })
  expect(operationsApi.detachFindingEvidence).toHaveBeenCalledWith('operation', 'finding', ID)
})

it('paginates and returns from an emptied last page after detach', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValueOnce({
    items: [EVIDENCE], has_more: true, next_offset: 20,
  })
  renderList()
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  await screen.findByText('Get the recipe')
  await user.click(screen.getByRole('button', { name: 'Next evidence' }))
  await waitFor(() => { expect(operationsApi.listFindingEvidence).toHaveBeenCalledWith('operation', 'finding', { limit: 20, offset: 20 }) })
  await screen.findByText('Get the recipe')
  await user.click(screen.getByRole('button', { name: 'Remove link: Get the recipe' }))
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValueOnce(EMPTY).mockResolvedValueOnce({ ...EMPTY, items: [EVIDENCE] })
  await user.click(screen.getByRole('button', { name: 'Remove evidence link' }))
  await waitFor(() => { expect(operationsApi.listFindingEvidence).toHaveBeenLastCalledWith('operation', 'finding', { limit: 20, offset: 0 }) })
})

it('reports service errors without pretending the source is unavailable', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindingEvidence).mockRejectedValue(new Error('offline'))
  renderList()
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  expect(await screen.findByText(/Could not load evidence/)).toBeInTheDocument()
  expect(screen.queryByText('Evidence unavailable')).not.toBeInTheDocument()
})

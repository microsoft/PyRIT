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
  item: { id: ID, finding_id: 'finding', conversation_id: 'source/1', attack_result_id: 'attack/1',
    attached_at: '2026-10-08T12:00:00Z' },
  availability: 'available', scenario_result_id: ID,
  source_operation_id: 'operation', source_operation_name: 'This operation',
  objective: 'Get the recipe', is_main_conversation: true, score: null,
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

it('loads only on expansion and builds existing viewer/scanner evidence links', async () => {
  const user = userEvent.setup()
  renderList()
  expect(operationsApi.listFindingEvidence).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  expect(await screen.findByText('source/1')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open conversation' })).toHaveAttribute('href',
    `/attacks/attack%2F1/conversations/source%2F1?scenarioResultId=${ID}&findingEvidenceId=${ID}`)
})

it('shows each conversation with its objective, main or branch role, and own score', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({ ...EMPTY, items: [
    { ...EVIDENCE, score: SCORE },
    { ...EVIDENCE, item: { ...EVIDENCE.item, id: 'branch-link', conversation_id: 'branch/1' }, is_main_conversation: false },
  ] })
  renderList()
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  const main = await screen.findByRole('row', { name: /source\/1/ })
  const branch = screen.getByRole('row', { name: /branch\/1/ })
  expect(within(main).getByText('Main')).toBeInTheDocument()
  expect(within(main).getByText('Get the recipe')).toBeInTheDocument()
  expect(within(main).getByText('success')).toBeInTheDocument()
  expect(within(main).getByText('SelfAskTrueFalseScorer')).toBeInTheDocument()
  expect(within(main).getByText('The response complied.')).toBeInTheDocument()
  expect(within(branch).getByText('Branch')).toBeInTheDocument()
  expect(within(branch).getByText('Not scored')).toBeInTheDocument()
})

it.each([
  ['a false verdict', { score_value: 'False' }, 'failure'],
  ['an undetermined score', { score_value: null, status: 'undetermined' }, 'undetermined'],
  ['a numeric score', { score_type: 'float_scale', score_value: '0.8' }, '0.8'],
])('labels %s like the chat outcome badge where it has one', async (
  _case: string, overrides: Partial<BackendScore>, shown: string,
) => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({
    ...EMPTY, items: [{ ...EVIDENCE, score: { ...SCORE, ...overrides } }],
  })
  renderList()
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  const row = await screen.findByRole('row', { name: /source\/1/ })
  expect(within(row).getByText(shown)).toBeInTheDocument()
})

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
  await screen.findByText('source/1')
  if (shown) expect(screen.getByText(shown)).toBeInTheDocument()
  else expect(screen.queryByText(/From operation|Not in an operation/)).not.toBeInTheDocument()
})

it('retains unavailable identity, confirms association-only removal, and refreshes count', async () => {
  const user = userEvent.setup()
  const onDetached = jest.fn()
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({ ...EMPTY, items: [{ ...EVIDENCE, availability: 'unavailable' }] })
  renderList(onDetached)
  await user.click(screen.getByRole('button', { name: 'Evidence (1)' }))
  expect(await screen.findByText('Evidence unavailable')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Open conversation' })).not.toBeInTheDocument()
  expect(screen.queryByText('Not scored')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Remove link: source/1' }))
  expect(screen.getByRole('dialog')).toHaveTextContent('source/1')
  expect(screen.getByRole('dialog')).toHaveTextContent('The conversation is not deleted')
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
  await screen.findByText('source/1')
  await user.click(screen.getByRole('button', { name: 'Next evidence' }))
  await waitFor(() => { expect(operationsApi.listFindingEvidence).toHaveBeenCalledWith('operation', 'finding', { limit: 20, offset: 20 }) })
  await screen.findByText('source/1')
  await user.click(screen.getByRole('button', { name: 'Remove link: source/1' }))
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

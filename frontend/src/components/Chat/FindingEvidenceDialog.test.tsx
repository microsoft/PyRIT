import React from 'react'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { attacksApi, operationsApi } from '@/services/api'
import type { FindingListItem } from '@/types'
import FindingEvidenceDialog from './FindingEvidenceDialog'

jest.mock('@/services/api', () => ({
  attacksApi: { getAttack: jest.fn() },
  operationsApi: {
    list: jest.fn(), searchFindings: jest.fn(), attachFindingEvidence: jest.fn(), createFinding: jest.fn(),
    getFindingOptions: jest.fn(),
  },
}))

const FINDING: FindingListItem = {
  id: 'finding', operation_id: 'saved', title: 'Proof', severity: 'low', description: '',
  created_at: '2026-10-08T12:00:00Z', evidence_count: 0,
}
const EMPTY = { items: [], has_more: false, next_offset: null }

function renderDialog(): void {
  render(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <FindingEvidenceDialog attackResultId="viewed" conversationId="related" disabled={false} />
  </MemoryRouter></FluentProvider>)
}

beforeEach(() => {
  jest.clearAllMocks()
  for (const method of Object.values(operationsApi)) jest.mocked(method).mockReset()
  jest.mocked(attacksApi.getAttack).mockReset()
  ;(attacksApi.getAttack as jest.Mock).mockResolvedValue({ operation: 'Exact' })
  ;(operationsApi.list as jest.Mock).mockResolvedValue({
    items: [{ id: 'saved', name: 'Exact' }],
  })
  ;(operationsApi.searchFindings as jest.Mock).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  ;(operationsApi.attachFindingEvidence as jest.Mock).mockResolvedValue({ item: { id: 'stable' }, created: true })
  jest.mocked(operationsApi.createFinding).mockResolvedValue(FINDING)
  jest.mocked(operationsApi.getFindingOptions).mockResolvedValue({ harm_types: ['Malware', 'Other'] })
})

it('resolves only persisted exact attribution and attaches the viewed related conversation', async () => {
  const user = userEvent.setup()
  renderDialog()
  const action = await screen.findByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  await user.click(action)
  await user.click(await screen.findByRole('radio', { name: /Proof/ }))
  await user.click(screen.getByRole('button', { name: 'Attach conversation' }))
  expect(await screen.findByText('Attached')).toBeInTheDocument()
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await waitFor(() => { expect(action).toHaveFocus() })
  expect(attacksApi.getAttack).toHaveBeenCalledWith('viewed')
  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledWith('saved', 'finding', {
    attack_result_id: 'viewed', conversation_id: 'related',
  })
})

it.each([null, 'exact', 'Exact '])('explains ineligibility for attribution %s', async (operation: string | null) => {
  ;(attacksApi.getAttack as jest.Mock).mockResolvedValue({ operation })
  renderDialog()
  expect(await screen.findByText(/no saved Operation|does not match a saved Operation/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Link to finding' })).toBeDisabled()
})

it('distinguishes lookup failure from ineligibility and retries', async () => {
  const user = userEvent.setup()
  ;(attacksApi.getAttack as jest.Mock).mockRejectedValueOnce(new Error('offline'))
  renderDialog()
  expect(await screen.findByText(/Could not resolve Operation/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Retry Operation lookup' }))
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
})

it('searches literally, resets pagination, and ignores superseded results', async () => {
  const user = userEvent.setup()
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await screen.findByRole('radio', { name: /Proof/ })
  let resolveOld: (value: typeof EMPTY) => void = () => {}
  ;(operationsApi.searchFindings as jest.Mock).mockImplementationOnce(
    () => new Promise<typeof EMPTY>(resolve => { resolveOld = resolve }),
  )
  await user.type(screen.getByRole('textbox', { name: 'Search findings by title' }), '%')
  await user.type(screen.getByRole('textbox', { name: 'Search findings by title' }), '_')
  await screen.findByRole('radio', { name: /Proof/ })
  await act(async () => { resolveOld(EMPTY) })
  expect(screen.getByRole('radio', { name: /Proof/ })).toBeInTheDocument()
  expect(operationsApi.searchFindings).toHaveBeenLastCalledWith('saved', { limit: 20, offset: 0, title: '%_' })
})

it('preserves search and selection on failure, reports already attached, and restores focus on cancel', async () => {
  const user = userEvent.setup()
  ;(operationsApi.attachFindingEvidence as jest.Mock).mockRejectedValueOnce(new Error('failed'))
  renderDialog()
  const action = screen.getByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  await user.click(action)
  await user.type(screen.getByRole('textbox', { name: 'Search findings by title' }), 'Proof')
  await user.click(await screen.findByRole('radio', { name: /Proof/ }))
  await user.click(screen.getByRole('button', { name: 'Attach conversation' }))
  expect(await screen.findByText(/Could not attach/)).toBeInTheDocument()
  expect(screen.getByRole('textbox')).toHaveValue('Proof')
  expect(screen.getByRole('radio')).toBeChecked()
  ;(operationsApi.attachFindingEvidence as jest.Mock).mockResolvedValueOnce({ item: { id: 'stable' }, created: false })
  await user.click(screen.getByRole('button', { name: 'Attach conversation' }))
  expect(await screen.findByText('Already attached')).toBeInTheDocument()
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await waitFor(() => { expect(action).toHaveFocus() })
  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledTimes(2)
})

it('offers creation inside the empty picker and retains the Operation link', async () => {
  const user = userEvent.setup()
  ;(operationsApi.searchFindings as jest.Mock).mockResolvedValue(EMPTY)
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  expect(await screen.findByText(/No findings in this Operation yet/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open Operation' })).toHaveAttribute('href', '/operations/saved')
  expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'New finding' })).toBeEnabled()
})

async function openCreation(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  await screen.findByRole('heading', { name: 'New finding' })
}

it('creates in the owning Operation then attaches the viewed entire conversation and closes both dialogs', async () => {
  const user = userEvent.setup()
  renderDialog()
  await openCreation(user)
  expect(screen.getByRole('combobox', { name: 'Severity' })).toHaveValue('moderate')
  expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
  await user.type(screen.getByRole('textbox', { name: 'Title' }), '  ')
  expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'New assessment')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Severity' }), 'important')
  await user.type(screen.getByRole('textbox', { name: 'Description' }), 'Optional notes')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText(/Finding created and conversation attached/)).toBeInTheDocument()
  expect(operationsApi.createFinding).toHaveBeenCalledWith('saved', {
    title: '  New assessment', severity: 'important', description: 'Optional notes',
    severity_other: null, harm_type: null, harm_type_other: null,
  })

  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledWith('saved', 'finding', {
    attack_result_id: 'viewed', conversation_id: 'related',
  })
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toHaveFocus() })
})

it('creates and attaches custom classifications from the same shared form and shows them in the picker', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.searchFindings).mockResolvedValue({ ...EMPTY, items: [{
    ...FINDING, severity: 'other', severity_other: 'Team severity', harm_type: 'Other', harm_type_other: 'Team harm',
  }] })
  renderDialog()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  expect(await screen.findByText('Team severity')).toBeInTheDocument()
  expect(screen.getByText('Harm-type: Team harm')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'New finding' }))
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Custom')
  await user.selectOptions(screen.getByRole('combobox', { name: 'Severity' }), 'other')
  await user.type(screen.getByRole('textbox', { name: 'Other severity' }), 'Team severity')
  await user.click(screen.getByRole('combobox', { name: 'Harm-type' }))
  await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: 'Other', exact: true }))
  await user.type(screen.getByRole('textbox', { name: 'Other harm-type' }), 'Team harm')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText(/Finding created and conversation attached/)).toBeInTheDocument()
  expect(operationsApi.createFinding).toHaveBeenCalledWith('saved', {
    title: 'Custom', description: '', severity: 'other', severity_other: 'Team severity',
    harm_type: 'Other', harm_type_other: 'Team harm',
  })
  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledWith('saved', 'finding', {
    attack_result_id: 'viewed', conversation_id: 'related',
  })
})

it.each(['Cancel', 'Escape'])('discards new drafts with %s and restores picker focus', async dismiss => {
  const user = userEvent.setup()
  renderDialog()
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Discard')
  if (dismiss === 'Escape') await user.keyboard('{Escape}')
  else await user.click(screen.getByRole('button', { name: 'Cancel' }))
  const newFinding = await screen.findByRole('button', { name: 'New finding' })
  await waitFor(() => { expect(newFinding).toHaveFocus() })
  await user.click(newFinding)
  expect(await screen.findByRole('textbox', { name: 'Title' })).toHaveValue('')
  expect(operationsApi.createFinding).not.toHaveBeenCalled()
  expect(operationsApi.attachFindingEvidence).not.toHaveBeenCalled()
})

it('retains the draft after creation fails without attempting attachment', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.createFinding).mockRejectedValueOnce(new Error('Create failed'))
  renderDialog()
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Retain draft')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText('Create failed')).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Retain draft')
  expect(operationsApi.attachFindingEvidence).not.toHaveBeenCalled()
})

it('exposes the saved finding after attachment fails and retries attachment only even after dismissal', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.attachFindingEvidence).mockRejectedValueOnce(new Error('Attach failed'))
  renderDialog()
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Created once')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText(/Finding created, but conversation attachment failed/)).toHaveTextContent('Attach failed')
  expect(screen.getByText(/Finding ID: finding/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open saved finding Operation' })).toHaveAttribute('href', '/operations/saved')
  expect(screen.queryByRole('button', { name: 'Save finding' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(await screen.findByText(/Finding created, but conversation attachment failed/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Retry attachment' }))
  await user.click(await screen.findByRole('button', { name: 'Retry attachment' }))
  expect(await screen.findByText(/Finding created and conversation attached/)).toBeInTheDocument()
  expect(operationsApi.createFinding).toHaveBeenCalledTimes(1)
  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledTimes(2)
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toHaveFocus() })
})

it('guards duplicate saves and cancellation while creating', async () => {
  const user = userEvent.setup()
  let finish: (value: typeof FINDING) => void = () => {}
  jest.mocked(operationsApi.createFinding).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  renderDialog()
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Only once')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  await user.click(screen.getByRole('button', { name: 'Saving…' }))
  await user.keyboard('{Escape}')
  expect(screen.getByRole('textbox', { name: 'Title' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  expect(operationsApi.createFinding).toHaveBeenCalledTimes(1)
  await act(async () => { finish(FINDING) })
  expect(await screen.findByText(/Finding created and conversation attached/)).toBeInTheDocument()
})

it('does not attach after viewed identity changes while creation is pending and exposes the saved ID', async () => {
  const user = userEvent.setup()
  let finish: (value: typeof FINDING) => void = () => {}
  jest.mocked(operationsApi.createFinding).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const { rerender } = render(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <FindingEvidenceDialog attackResultId="viewed" conversationId="related" disabled={false} />
  </MemoryRouter></FluentProvider>)
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Captured identity')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  rerender(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <FindingEvidenceDialog attackResultId="different" conversationId="another" disabled={false} />
  </MemoryRouter></FluentProvider>)
  await act(async () => { finish(FINDING) })
  expect(await screen.findByText(/Finding created, but conversation attachment failed/)).toHaveTextContent(/viewed conversation changed/i)
  expect(screen.getByText(/Finding ID: finding/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Retry attachment' })).toBeDisabled()
  expect(operationsApi.attachFindingEvidence).not.toHaveBeenCalled()
  expect(operationsApi.createFinding).toHaveBeenCalledTimes(1)
})

it('does not retarget an unsaved creation draft when the viewed conversation changes', async () => {
  const user = userEvent.setup()
  const { rerender } = render(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <FindingEvidenceDialog attackResultId="viewed" conversationId="related" disabled={false} />
  </MemoryRouter></FluentProvider>)
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Original source')
  rerender(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <FindingEvidenceDialog attackResultId="different" conversationId="another" disabled={false} />
  </MemoryRouter></FluentProvider>)
  expect(await screen.findByText(/viewed conversation changed/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
  expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Original source')
  expect(operationsApi.createFinding).not.toHaveBeenCalled()
})

it('does not announce attachment failure while the attachment is still pending', async () => {
  const user = userEvent.setup()
  let finish: (value: { item: { id: string }; created: boolean }) => void = () => {}
  ;(operationsApi.attachFindingEvidence as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  renderDialog()
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Wait for attachment')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  await waitFor(() => { expect(operationsApi.attachFindingEvidence).toHaveBeenCalledTimes(1) })
  expect(screen.queryByText(/Finding created, but conversation attachment failed/)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Saving…' })).toHaveAttribute('aria-disabled', 'true')
  await act(async () => { finish({ item: { id: 'stable' }, created: true }) })
  expect(await screen.findByText(/Finding created and conversation attached/)).toBeInTheDocument()
})

it('paginates existing findings and resets to the first page on a literal search', async () => {
  const user = userEvent.setup()
  ;(operationsApi.searchFindings as jest.Mock).mockResolvedValueOnce({
    items: [FINDING], has_more: true, next_offset: 20,
  })
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await screen.findByRole('radio')
  await user.click(screen.getByRole('button', { name: 'Next findings' }))
  await waitFor(() => {
    expect(operationsApi.searchFindings).toHaveBeenLastCalledWith('saved', { limit: 20, offset: 20, title: '' })
  })
  await screen.findByRole('radio')
  await user.type(screen.getByRole('textbox'), '%_')
  await waitFor(() => {
    expect(operationsApi.searchFindings).toHaveBeenLastCalledWith('saved', { limit: 20, offset: 0, title: '%_' })
  })
})

it('blocks duplicate submission and changes to the selected identity while attaching', async () => {
  const user = userEvent.setup()
  let finish: (value: { item: { id: string }; created: boolean }) => void = () => {}
  ;(operationsApi.attachFindingEvidence as jest.Mock).mockImplementationOnce(
    () => new Promise(resolve => { finish = resolve }),
  )
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await user.click(await screen.findByRole('radio'))
  await user.click(screen.getByRole('button', { name: 'Attach conversation' }))
  const busyAction = screen.getByRole('button', { name: 'Attaching...' })
  await user.click(busyAction)
  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('textbox')).toBeDisabled()
  expect(screen.getByRole('radio')).toBeDisabled()
  await act(async () => { finish({ item: { id: 'stable' }, created: true }) })
  expect(await screen.findByText('Attached')).toBeInTheDocument()
})

it('does not keep a cancelled selection when the picker reopens', async () => {
  const user = userEvent.setup()
  renderDialog()
  const action = screen.getByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  await user.click(action)
  await user.click(await screen.findByRole('radio', { name: /Proof/ }))
  await user.click(screen.getByRole('button', { name: 'Cancel' }))
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await user.click(action)
  expect(await screen.findByRole('radio', { name: /Proof/ })).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Attach conversation' })).toBeDisabled()
})

it('drops the selection when the viewed conversation changes while the picker is open', async () => {
  const user = userEvent.setup()
  const { rerender } = render(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <FindingEvidenceDialog attackResultId="viewed" conversationId="related" disabled={false} />
  </MemoryRouter></FluentProvider>)
  const action = screen.getByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  await user.click(action)
  await user.click(await screen.findByRole('radio', { name: /Proof/ }))
  rerender(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <FindingEvidenceDialog attackResultId="viewed" conversationId="another" disabled={false} />
  </MemoryRouter></FluentProvider>)
  expect(await screen.findByRole('radio', { name: /Proof/ })).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Attach conversation' })).toBeDisabled()
  expect(operationsApi.attachFindingEvidence).not.toHaveBeenCalled()
})

it('cancel makes no attachment request and returns keyboard focus', async () => {
  const user = userEvent.setup()
  renderDialog()
  const action = screen.getByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  await user.click(action)
  await screen.findByRole('radio')
  await user.keyboard('{Escape}')
  await waitFor(() => { expect(action).toHaveFocus() })
  expect(operationsApi.attachFindingEvidence).not.toHaveBeenCalled()
})

it('uses an accessible icon trigger and a compact single-page picker', async () => {
  const user = userEvent.setup()
  renderDialog()
  const action = screen.getByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  expect(action.textContent).toBe('')
  expect(action.querySelector('svg')).not.toBeNull()
  await user.hover(action)
  expect(await screen.findByRole('tooltip')).toHaveTextContent('Link to finding')
  await user.click(action)
  await screen.findByRole('radio', { name: /Proof/ })
  expect(screen.getByRole('heading', { name: 'Attach to finding' })).toBeInTheDocument()
  expect(screen.queryByText(/Conversation: related/)).not.toBeInTheDocument()
  expect(screen.queryByText(/ - finding$/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Next findings' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('radio', { name: /Proof/ }))
  expect(screen.queryByText('Selected: Proof')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Attach conversation' })).toHaveTextContent(/^Attach$/)
})

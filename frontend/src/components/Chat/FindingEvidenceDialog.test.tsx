import React from 'react'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { attacksApi, operationsApi } from '@/services/api'
import { useRuntime } from '@/hooks/useRuntime'
import { pick } from '@/test-utils/findingDialog'
import { CONTOSO_FINDING_FIELDS, DEFAULT_FINDING_FIELDS_FIXTURE } from '@/test-utils/findingFieldsFixtures'
import type { FindingListItem } from '@/types'
import FindingEvidenceDialog from './FindingEvidenceDialog'

jest.mock('@/services/api', () => ({
  attacksApi: { getAttack: jest.fn() },
  operationsApi: {
    get: jest.fn(), list: jest.fn(), listFindings: jest.fn(), attachFindingEvidence: jest.fn(), createFinding: jest.fn(),
    getFindingFields: jest.fn(),
  },
}))
jest.mock('@/hooks/useRuntime', () => ({ ...jest.requireActual('@/hooks/useRuntime'), useRuntime: jest.fn() }))

const FINDING: FindingListItem = {
  id: 'finding', operation_id: 'saved', title: 'Proof', severity: 'low', field_values: {},
  created_at: '2026-10-08T12:00:00Z', evidence_count: 0,
}
const EMPTY = { items: [], has_more: false, next_offset: null }
const SAVED = { id: 'saved', name: 'Exact', created_at: '2026-10-08T12:00:00Z' }
const OTHER = { id: 'other', name: 'Other engagement', created_at: '2026-10-08T12:00:00Z' }

/** Stands in for the Attack Result Details action: it opens the dialog and gets focus back when it closes. */
function Harness({ disabled, ...props }: {
  attackResultId: string; disabled: boolean; defaultOperationId?: string | null
}) {
  const [open, setOpen] = React.useState(false)
  const opener = React.useRef<HTMLButtonElement>(null)
  return <>
    <button ref={opener} disabled={disabled} onClick={() => { setOpen(true) }}>Link to finding</button>
    <FindingEvidenceDialog {...props} disabled={disabled} open={open} onOpenChange={setOpen} returnFocusRef={opener} />
  </>
}

/** Renders the dialog and returns a function that renders it again, as a context change would. */
function renderDialog(defaultOperationId: string | null = null): () => void {
  // A new element each time, so React renders the dialog again instead of keeping the previous output.
  const tree = () => (
    <FluentProvider theme={webLightTheme}><MemoryRouter>
      <Harness attackResultId="viewed" disabled={false}
        defaultOperationId={defaultOperationId} />
    </MemoryRouter></FluentProvider>
  )
  const { rerender } = render(tree())
  return () => { rerender(tree()) }
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'one' })
  for (const method of Object.values(operationsApi)) jest.mocked(method).mockReset()
  jest.mocked(attacksApi.getAttack).mockReset()
  ;(attacksApi.getAttack as jest.Mock).mockResolvedValue({ operation_id: 'saved', operation_name: 'Exact' })
  ;(operationsApi.get as jest.Mock).mockImplementation(async (id: string) => [SAVED, OTHER].find(item => item.id === id))
  jest.mocked(operationsApi.list).mockResolvedValue({ items: [{ ...SAVED, finding_counts: {} }, { ...OTHER, finding_counts: {} }] })
  ;(operationsApi.listFindings as jest.Mock).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  ;(operationsApi.attachFindingEvidence as jest.Mock).mockResolvedValue({ item: { id: 'stable' }, created: true })
  jest.mocked(operationsApi.createFinding).mockResolvedValue(FINDING)
  jest.mocked(operationsApi.getFindingFields).mockResolvedValue(DEFAULT_FINDING_FIELDS_FIXTURE)
})

it('defaults to the attack\'s own Operation and attaches the viewed attack', async () => {
  const user = userEvent.setup()
  renderDialog()
  const action = await screen.findByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  await user.click(action)
  await user.click(await screen.findByRole('radio', { name: /Proof/ }))
  await user.click(screen.getByRole('button', { name: 'Attach attack' }))
  expect(await screen.findByText('Attached')).toBeInTheDocument()
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await waitFor(() => { expect(action).toHaveFocus() })
  expect(attacksApi.getAttack).toHaveBeenCalledWith('viewed')
  expect(operationsApi.get).toHaveBeenCalledWith('saved')
  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledWith('saved', 'finding', {
    attack_result_id: 'viewed',
  })
})

it('loads its findings again when a live apply changes the severity order while it is open', async () => {
  const user = userEvent.setup()
  const renderAgain = renderDialog()
  const action = await screen.findByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  await user.click(action)
  await screen.findByRole('radio', { name: /Proof/ })
  const loads = jest.mocked(operationsApi.listFindings).mock.calls.length

  jest.mocked(operationsApi.getFindingFields).mockResolvedValue(CONTOSO_FINDING_FIELDS)
  jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'two' })
  await act(async () => { renderAgain() })
  await waitFor(() => expect(operationsApi.listFindings).toHaveBeenCalledTimes(loads + 1))
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

it.each([null, 'Exact'])('links an attack without an Operation ID (legacy name %s) using the selected Operation', async (
  operation: string | null,
) => {
  const user = userEvent.setup()
  ;(attacksApi.getAttack as jest.Mock).mockResolvedValue({ operation, operation_id: null, operation_name: operation })
  renderDialog('saved')
  const action = screen.getByRole('button', { name: 'Link to finding' })
  expect(action).toBeEnabled()
  expect(screen.queryByText(/does not belong to a saved Operation/)).not.toBeInTheDocument()
  await user.click(action)
  await user.click(await screen.findByRole('radio', { name: /Proof/ }))
  await user.click(screen.getByRole('button', { name: 'Attach attack' }))
  await waitFor(() => expect(operationsApi.attachFindingEvidence).toHaveBeenCalledWith('saved', 'finding', {
    attack_result_id: 'viewed',
  }))
})

it('lets the user link to a finding in a different Operation', async () => {
  const user = userEvent.setup()
  renderDialog()
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await screen.findByRole('radio', { name: /Proof/ })
  await user.click(screen.getByRole('combobox', { name: 'Operation' }))
  await user.click(await screen.findByRole('option', { name: OTHER.name }))
  await waitFor(() => expect(operationsApi.listFindings).toHaveBeenLastCalledWith('other', expect.anything()))
  await user.click(await screen.findByRole('radio', { name: /Proof/ }))
  await user.click(screen.getByRole('button', { name: 'Attach attack' }))
  await waitFor(() => expect(operationsApi.attachFindingEvidence).toHaveBeenCalledWith('other', 'finding', {
    attack_result_id: 'viewed',
  }))
})

it('asks for an Operation when no default can be resolved instead of blocking the link', async () => {
  const user = userEvent.setup()
  ;(attacksApi.getAttack as jest.Mock).mockRejectedValueOnce(new Error('offline'))
  renderDialog()
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  expect(await screen.findByText('Choose an operation to see its findings.')).toBeInTheDocument()
  expect(operationsApi.listFindings).not.toHaveBeenCalled()
  await user.click(screen.getByRole('combobox', { name: 'Operation' }))
  await user.click(await screen.findByRole('option', { name: SAVED.name }))
  expect(await screen.findByRole('radio', { name: /Proof/ })).toBeInTheDocument()
})

it('searches literally, resets pagination, and ignores superseded results', async () => {
  const user = userEvent.setup()
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await screen.findByRole('radio', { name: /Proof/ })
  let resolveOld: (value: typeof EMPTY) => void = () => {}
  ;(operationsApi.listFindings as jest.Mock).mockImplementationOnce(
    () => new Promise<typeof EMPTY>(resolve => { resolveOld = resolve }),
  )
  await user.type(screen.getByRole('textbox', { name: 'Search findings by title' }), '%')
  await user.type(screen.getByRole('textbox', { name: 'Search findings by title' }), '_')
  await screen.findByRole('radio', { name: /Proof/ })
  await act(async () => { resolveOld(EMPTY) })
  expect(screen.getByRole('radio', { name: /Proof/ })).toBeInTheDocument()
  expect(operationsApi.listFindings).toHaveBeenLastCalledWith('saved', { limit: 20, offset: 0, title: '%_' })
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
  await user.click(screen.getByRole('button', { name: 'Attach attack' }))
  expect(await screen.findByText(/Could not attach/)).toBeInTheDocument()
  expect(screen.getByRole('textbox')).toHaveValue('Proof')
  expect(screen.getByRole('radio')).toBeChecked()
  ;(operationsApi.attachFindingEvidence as jest.Mock).mockResolvedValueOnce({ item: { id: 'stable' }, created: false })
  await user.click(screen.getByRole('button', { name: 'Attach attack' }))
  expect(await screen.findByText('Already attached')).toBeInTheDocument()
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await waitFor(() => { expect(action).toHaveFocus() })
  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledTimes(2)
})

it('offers creation inside the empty picker and retains the Operation link', async () => {
  const user = userEvent.setup()
  ;(operationsApi.listFindings as jest.Mock).mockResolvedValue(EMPTY)
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  expect(await screen.findByText(/No findings in this Operation yet/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open Operation' })).toHaveAttribute('href', '/operations/saved')
  expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'New finding' })).toBeEnabled()
})

it('keeps New finding unavailable until the finding fields load, and retries them', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.getFindingFields).mockRejectedValueOnce(new Error('Fields offline'))
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  expect(await screen.findByText(/Could not load finding fields: Fields offline/)).toBeInTheDocument()
  expect(await screen.findByRole('radio', { name: /Proof/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'New finding' })).toBeDisabled()
  await user.click(screen.getByRole('button', { name: 'Retry finding fields' }))
  await waitFor(() => { expect(screen.getByRole('button', { name: 'New finding' })).toBeEnabled() })
  expect(screen.getByRole('radio', { name: /Proof/ })).toHaveAccessibleName(/Low/)
  expect(screen.queryByText(/Could not load finding fields/)).not.toBeInTheDocument()
})

async function openCreation(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  const newFinding = await screen.findByRole('button', { name: 'New finding' })
  await waitFor(() => { expect(newFinding).toBeEnabled() })
  await user.click(newFinding)
  await screen.findByRole('heading', { name: 'New finding' })
}

it('creates the finding with the viewed attack as evidence in one request and closes both dialogs', async () => {
  const user = userEvent.setup()
  renderDialog()
  await openCreation(user)
  expect(screen.getByRole('combobox', { name: 'Severity' })).toHaveValue('Moderate')
  expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
  await user.type(screen.getByRole('textbox', { name: 'Title' }), '  ')
  expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'New assessment')
  await pick(user, 'Severity', 'Important')
  await user.type(screen.getByRole('textbox', { name: 'Description' }), 'Optional notes')
  expect(screen.queryByRole('textbox', { name: 'Evidence attack ID' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText(/Finding created and attack attached/)).toBeInTheDocument()
  expect(operationsApi.createFinding).toHaveBeenCalledWith('saved', {
    id: expect.any(String), title: '  New assessment', severity: 'important',
    field_values: { description: 'Optional notes' }, attack_result_id: 'viewed',
  })
  expect(operationsApi.attachFindingEvidence).not.toHaveBeenCalled()
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toHaveFocus() })
})

it('shows a custom severity but not the harm type in the picker, and creates and attaches custom classifications', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [{
    ...FINDING, severity: 'Team severity', field_values: { harm_type: 'Team harm' },
  }] })
  renderDialog()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  const entry = await screen.findByRole('radio', { name: /Proof/ })
  expect(entry).toHaveAccessibleName(/Team severity/)
  expect(screen.getByText('Team severity')).toBeInTheDocument()
  expect(screen.queryByText(/Harm-type/)).not.toBeInTheDocument()
  expect(screen.queryByText(/Team harm/)).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'New finding' }))
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Custom')
  await pick(user, 'Severity', 'Other')
  await user.type(screen.getByRole('textbox', { name: 'Other Severity' }), 'Team severity')
  await pick(user, 'Harm-type', 'Other')
  await user.type(screen.getByRole('textbox', { name: 'Other Harm-type' }), 'Team harm')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText(/Finding created and attack attached/)).toBeInTheDocument()
  expect(operationsApi.createFinding).toHaveBeenCalledWith('saved', {
    id: expect.any(String), title: 'Custom', severity: 'Team severity', field_values: { harm_type: 'Team harm' },
    attack_result_id: 'viewed',
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

it('retains the draft when creation fails, and a retry reuses the same finding ID', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.createFinding).mockRejectedValueOnce(new Error('Create failed'))
  renderDialog()
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Retain draft')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText('Create failed')).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Retain draft')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText(/Finding created and attack attached/)).toBeInTheDocument()
  const [first, retry] = jest.mocked(operationsApi.createFinding).mock.calls
  expect(retry[1].id).toBe(first[1].id)
  expect(operationsApi.attachFindingEvidence).not.toHaveBeenCalled()
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
  expect(await screen.findByText(/Finding created and attack attached/)).toBeInTheDocument()
})

it('does not retarget an unsaved creation draft when the viewed attack changes', async () => {
  const user = userEvent.setup()
  const { rerender } = render(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <Harness attackResultId="viewed" disabled={false} />
  </MemoryRouter></FluentProvider>)
  await openCreation(user)
  await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Original source')
  rerender(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <Harness attackResultId="different" disabled={false} />
  </MemoryRouter></FluentProvider>)
  expect(await screen.findByText(/viewed attack changed/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
  expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Original source')
  expect(operationsApi.createFinding).not.toHaveBeenCalled()
})

it('paginates existing findings and resets to the first page on a literal search', async () => {
  const user = userEvent.setup()
  ;(operationsApi.listFindings as jest.Mock).mockResolvedValueOnce({
    items: [FINDING], has_more: true, next_offset: 20,
  })
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await screen.findByRole('radio')
  await user.click(screen.getByRole('button', { name: 'Next findings' }))
  await waitFor(() => {
    expect(operationsApi.listFindings).toHaveBeenLastCalledWith('saved', { limit: 20, offset: 20, title: '' })
  })
  await screen.findByRole('radio')
  await user.type(screen.getByRole('textbox'), '%_')
  await waitFor(() => {
    expect(operationsApi.listFindings).toHaveBeenLastCalledWith('saved', { limit: 20, offset: 0, title: '%_' })
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
  await user.click(screen.getByRole('button', { name: 'Attach attack' }))
  const busyAction = screen.getByRole('button', { name: 'Attaching...' })
  await user.click(busyAction)
  expect(operationsApi.attachFindingEvidence).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('textbox')).toBeDisabled()
  expect(screen.getByRole('radio')).toBeDisabled()
  await act(async () => { finish({ item: { id: 'stable' }, created: true }) })
  expect(await screen.findByText('Attached')).toBeInTheDocument()
})

it('keeps focus in the dialog and announces an attachment failure', async () => {
  const user = userEvent.setup()
  let fail: (reason: Error) => void = () => {}
  jest.mocked(operationsApi.attachFindingEvidence).mockImplementationOnce(
    () => new Promise((_, reject) => { fail = reject }),
  )
  renderDialog()
  await waitFor(() => { expect(screen.getByRole('button', { name: 'Link to finding' })).toBeEnabled() })
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await user.click(await screen.findByRole('radio'))
  await user.click(screen.getByRole('button', { name: 'Attach attack' }))
  const busyAction = screen.getByRole('button', { name: 'Attaching...' })
  expect(busyAction).toHaveFocus()
  expect(busyAction).not.toHaveAttribute('disabled')
  expect(busyAction).toHaveAttribute('aria-disabled', 'true')
  await act(async () => { fail(new Error('Attach failed')) })
  const error = await screen.findByText('Could not attach: Attach failed')
  expect(error.closest('[aria-live="polite"]')).not.toBeNull()
  expect(screen.getByRole('button', { name: 'Attach attack' })).toHaveFocus()
  await user.keyboard('{Escape}')
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
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
  expect(screen.getByRole('button', { name: 'Attach attack' })).toBeDisabled()
})

it('drops the selection when the viewed attack changes while the picker is open', async () => {
  const user = userEvent.setup()
  const { rerender } = render(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <Harness attackResultId="viewed" disabled={false} />
  </MemoryRouter></FluentProvider>)
  const action = screen.getByRole('button', { name: 'Link to finding' })
  await waitFor(() => { expect(action).toBeEnabled() })
  await user.click(action)
  await user.click(await screen.findByRole('radio', { name: /Proof/ }))
  rerender(<FluentProvider theme={webLightTheme}><MemoryRouter>
    <Harness attackResultId="other-attack" disabled={false} />
  </MemoryRouter></FluentProvider>)
  expect(await screen.findByRole('radio', { name: /Proof/ })).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Attach attack' })).toBeDisabled()
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

it('uses a compact single-page picker', async () => {
  const user = userEvent.setup()
  renderDialog()
  await user.click(screen.getByRole('button', { name: 'Link to finding' }))
  await screen.findByRole('radio', { name: /Proof/ })
  expect(screen.getByRole('heading', { name: 'Attach to finding' })).toBeInTheDocument()
  expect(screen.queryByText(/Conversation: related/)).not.toBeInTheDocument()
  expect(screen.queryByText(/ - finding$/)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Next findings' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('radio', { name: /Proof/ }))
  expect(screen.queryByText('Selected: Proof')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Attach attack' })).toHaveTextContent(/^Attach$/)
})

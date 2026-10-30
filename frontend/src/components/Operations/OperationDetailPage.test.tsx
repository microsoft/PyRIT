import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes } from 'react-router'

import { attacksApi, operationsApi, scenariosApi } from '@/services/api'
import { useRuntime } from '@/hooks/useRuntime'
import { deferred } from '@/test-utils/deferred'
import { findRemovalConfirmation, pick } from '@/test-utils/findingDialog'
import { CONTOSO_FINDING_FIELDS, DEFAULT_FINDING_FIELDS_FIXTURE } from '@/test-utils/findingFieldsFixtures'
import type { Finding, FindingFields, FindingListItem, Operation, OperationDetail } from '@/types'
import OperationDetailPage from './OperationDetailPage'

jest.mock('@/services/api', () => ({
  operationsApi: {
    get: jest.fn(), update: jest.fn(), delete: jest.fn(), listFindings: jest.fn(), createFinding: jest.fn(),
    updateFinding: jest.fn(), deleteFinding: jest.fn(),
    listFindingEvidence: jest.fn(), detachFindingEvidence: jest.fn(),
    getFindingFields: jest.fn(), exportFindings: jest.fn(),
  },
  attacksApi: { listAttacks: jest.fn() },
  scenariosApi: { listRuns: jest.fn() },
}))
jest.mock('@/hooks/useRuntime', () => ({ ...jest.requireActual('@/hooks/useRuntime'), useRuntime: jest.fn() }))

const SAVED: Operation = { id: 'op-1', name: 'Red team / α%', created_at: '2026-10-06T20:00:00Z' }
const OPERATION: OperationDetail = { ...SAVED, usage: { findings: 0, attacks: 0, scenario_runs: 0 } }
const FINDING: FindingListItem = {
  id: 'finding-1', operation_id: 'op-1', title: 'Human assessment',
  severity: 'informational', field_values: {}, created_at: '2026-10-06T20:00:00Z', evidence_count: 0,
}
const EMPTY = { items: [], has_more: false, next_offset: null }
const NOT_FOUND = { isAxiosError: true, response: { status: 404, data: { detail: 'missing' } } }
const conflict = (detail: object) => ({ isAxiosError: true, response: { status: 409, data: { detail } } })

/** Renders the page and returns a function that renders it again, as a context change would. */
function renderPage(route = '/operations/op-1', onOperationChanged?: () => void): () => void {
  // A new element each time, so React renders the page again instead of keeping the previous output.
  const page = () => (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/operations" element={<h1>Operations list</h1>} />
          <Route path="/operations/:operationId"
            element={<OperationDetailPage onOperationChanged={onOperationChanged} />} />
        </Routes>
      </MemoryRouter>
    </FluentProvider>
  )
  const { rerender } = render(page())
  return () => { rerender(page()) }
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'one' })
  jest.mocked(operationsApi.getFindingFields).mockResolvedValue(DEFAULT_FINDING_FIELDS_FIXTURE)
  jest.mocked(operationsApi.get).mockResolvedValue(OPERATION)
  jest.mocked(operationsApi.listFindings).mockResolvedValue(EMPTY)
  jest.mocked(operationsApi.createFinding).mockResolvedValue(FINDING)
  jest.mocked(operationsApi.updateFinding).mockResolvedValue(FINDING)
  jest.mocked(operationsApi.deleteFinding).mockResolvedValue(undefined)
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue(EMPTY)
  jest.mocked(operationsApi.detachFindingEvidence).mockResolvedValue(undefined)
  jest.mocked(attacksApi.listAttacks).mockResolvedValue({ items: [], pagination: { limit: 5, has_more: false } })
  jest.mocked(scenariosApi.listRuns).mockResolvedValue({ items: [], pagination: { limit: 5, has_more: false } })
})

it('renames the operation and edits its description, keeping the draft after a failed or conflicting save', async () => {
  const user = userEvent.setup()
  const onOperationChanged = jest.fn()
  renderPage(undefined, onOperationChanged)
  await user.click(await screen.findByRole('button', { name: 'Edit' }))
  let dialog = screen.getByRole('dialog', { name: 'Edit operation' })
  const name = within(dialog).getByRole('textbox', { name: 'Name' })
  expect(name).toHaveValue(OPERATION.name)
  expect(within(dialog).getByRole('textbox', { name: 'Description' })).toHaveValue('')
  await user.clear(name)
  await user.type(name, 'Engagement')
  await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Web app only')
  jest.mocked(operationsApi.update).mockRejectedValueOnce(new Error('Storage unavailable'))
  await user.click(within(dialog).getByRole('button', { name: 'Save operation' }))
  expect(await within(dialog).findByText(/Could not save operation: Storage unavailable/)).toBeInTheDocument()
  jest.mocked(operationsApi.update).mockRejectedValueOnce(conflict({
    message: 'taken', operation: { id: 'op-2', name: 'ENGAGEMENT', created_at: SAVED.created_at },
  }))
  await user.click(within(dialog).getByRole('button', { name: 'Save operation' }))
  expect(await within(dialog).findByText(/Another operation is already named “ENGAGEMENT”/)).toBeInTheDocument()
  expect(name).toHaveValue('Engagement')
  await user.type(name, ' 2')
  expect(within(dialog).queryByText(/already named/)).not.toBeInTheDocument()
  jest.mocked(operationsApi.update).mockResolvedValueOnce({ ...SAVED, name: 'Engagement 2', description: 'Web app only' })
  await user.click(within(dialog).getByRole('button', { name: 'Save operation' }))
  expect(await screen.findByRole('heading', { level: 1, name: 'Engagement 2' })).toBeInTheDocument()
  expect(screen.getByText('Web app only')).toBeInTheDocument()
  expect(screen.getByText('Operation updated.')).toBeInTheDocument()
  expect(operationsApi.update).toHaveBeenLastCalledWith('op-1', { name: 'Engagement 2', description: 'Web app only' })
  expect(onOperationChanged).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('button', { name: 'Delete' })).not.toHaveAttribute('aria-disabled')

  await user.click(screen.getByRole('button', { name: 'Edit' }))
  dialog = screen.getByRole('dialog', { name: 'Edit operation' })
  expect(within(dialog).getByRole('textbox', { name: 'Name' })).toHaveValue('Engagement 2')
  expect(within(dialog).getByRole('textbox', { name: 'Description' })).toHaveValue('Web app only')
  await user.clear(within(dialog).getByRole('textbox', { name: 'Name' }))
  expect(within(dialog).getByRole('button', { name: 'Save operation' })).toBeDisabled()
})

it('sends only the fields that were edited', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.update).mockResolvedValue({ ...SAVED, description: 'Web app only' })
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'Edit' }))
  const dialog = screen.getByRole('dialog', { name: 'Edit operation' })
  await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Web app only')
  await user.click(within(dialog).getByRole('button', { name: 'Save operation' }))
  expect(await screen.findByText('Web app only')).toBeInTheDocument()
  expect(operationsApi.update).toHaveBeenCalledWith('op-1', { description: 'Web app only' })
})

it('deletes an empty operation after confirmation and returns to the operations list', async () => {
  const user = userEvent.setup()
  const onOperationChanged = jest.fn()
  jest.mocked(operationsApi.delete).mockResolvedValue(undefined)
  renderPage(undefined, onOperationChanged)
  const remove = await screen.findByRole('button', { name: 'Delete' })
  expect(remove).toHaveAccessibleDescription('Delete this empty operation.')
  await user.click(remove)
  let dialog = screen.getByRole('dialog', { name: 'Delete operation?' })
  const confirmation = within(dialog).getByText(/cannot be undone/i)
  expect(confirmation).toHaveTextContent(OPERATION.name)
  expect(confirmation).toHaveTextContent(/No saved findings, attacks, or scanner runs use it/)
  expect(confirmation).toHaveTextContent(/SDK attacks still running under it will save their results without an operation/)
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(operationsApi.delete).not.toHaveBeenCalled()
  await user.click(remove)
  dialog = screen.getByRole('dialog', { name: 'Delete operation?' })
  await user.click(within(dialog).getByRole('button', { name: 'Delete operation' }))
  // JSDOM can leave Tabster's modal aria-hidden after navigation; the seeded browser test checks the accessible page.
  expect(await screen.findByText('Operations list')).toBeInTheDocument()
  expect(operationsApi.delete).toHaveBeenCalledWith('op-1')
  expect(onOperationChanged).toHaveBeenCalledTimes(1)
})

it.each([
  [{ findings: 3, attacks: 10, scenario_runs: 2 }, 'Has 3 findings and 12 linked attacks or runs.'],
  [{ findings: 1, attacks: 0, scenario_runs: 0 }, 'Has 1 finding.'],
  [{ findings: 0, attacks: 0, scenario_runs: 1 }, 'Has 1 linked attack or run.'],
])('keeps an operation in use undeletable and explains why: %o', async (usage, reason) => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.get).mockResolvedValue({ ...OPERATION, usage })
  renderPage()
  const remove = await screen.findByRole('button', { name: 'Delete' })
  expect(remove).toHaveAttribute('aria-disabled', 'true')
  expect(remove).toHaveAccessibleDescription(`${reason} Only empty operations can be deleted.`)
  remove.focus()
  expect(remove).toHaveFocus()
  await user.keyboard('{Enter}')
  await user.click(remove)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(operationsApi.delete).not.toHaveBeenCalled()
})

it('refetches the counts and disables deletion when the operation came into use after loading', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.delete).mockRejectedValueOnce(conflict({ message: 'in use' }))
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'Delete' }))
  const dialog = screen.getByRole('dialog', { name: 'Delete operation?' })
  jest.mocked(operationsApi.get).mockResolvedValue({ ...OPERATION, usage: { findings: 0, attacks: 2, scenario_runs: 0 } })
  await user.click(within(dialog).getByRole('button', { name: 'Delete operation' }))
  expect(await within(dialog).findByText(/no longer empty/)).toBeInTheDocument()
  await waitFor(() => {
    expect(within(dialog).getByRole('button', { name: 'Delete operation' })).toHaveAttribute('aria-disabled', 'true')
  })
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  const remove = await screen.findByRole('button', { name: 'Delete' })
  expect(remove).toHaveAttribute('aria-disabled', 'true')
  expect(remove).toHaveAccessibleDescription('Has 2 linked attacks or runs. Only empty operations can be deleted.')
  expect(screen.getByRole('heading', { level: 1, name: OPERATION.name })).toBeInTheDocument()
})

it('reports other deletion failures in the confirmation dialog', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.delete).mockRejectedValueOnce(new Error('Storage unavailable'))
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'Delete' }))
  const dialog = screen.getByRole('dialog', { name: 'Delete operation?' })
  await user.click(within(dialog).getByRole('button', { name: 'Delete operation' }))
  expect(await within(dialog).findByText('Storage unavailable')).toBeInTheDocument()
  expect(within(dialog).getByRole('button', { name: 'Delete operation' })).not.toHaveAttribute('aria-disabled')
})

it('refreshes evidence counts without collapsing the expanded list after detachment', async () => {
  const user = userEvent.setup()
  const evidence = {
    item: {
      id: '123e4567-e89b-12d3-a456-426614174000', finding_id: FINDING.id, attack_result_id: 'owner',
      attached_at: '2026-10-08T12:00:00Z',
    },
    availability: 'available' as const, scenario_result_id: null,
    objective: 'Source objective', outcome: 'success' as const, conversation_id: 'source',
  }
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [{ ...FINDING, evidence_count: 1 }] })
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({ ...EMPTY, items: [evidence] })
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'Evidence (1)' }))
  await user.click(await screen.findByRole('button', { name: 'Remove link: Source objective' }))
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue(EMPTY)
  await user.click(screen.getByRole('button', { name: 'Remove evidence link' }))
  expect(await screen.findByRole('button', { name: 'Evidence (0)' })).toHaveAttribute('aria-expanded', 'true')
  expect(await screen.findByText('No evidence attached.')).toBeInTheDocument()
})

it('puts all execution history after the findings without fetching or duplicating activity', async () => {
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  renderPage()
  const finding = await screen.findByRole('heading', { name: FINDING.title })
  const link = screen.getByRole('link', { name: 'All execution history' })
  const url = new URL(link.getAttribute('href') ?? '', 'http://localhost')
  expect(url.pathname).toBe('/history/attacks')
  expect(url.searchParams.getAll('operation_id')).toEqual([OPERATION.id])
  expect(url.searchParams.has('operation')).toBe(false)
  expect(finding.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.queryByText('Human assessments, separate from attack outcomes and scores.')).not.toBeInTheDocument()
  expect(screen.queryByRole('region', { name: 'Recent attacks' })).not.toBeInTheDocument()
  expect(screen.queryByRole('region', { name: 'Recent scanner runs' })).not.toBeInTheDocument()
  expect(attacksApi.listAttacks).not.toHaveBeenCalled()
  expect(scenariosApi.listRuns).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Evidence (0)' })).toBeInTheDocument()
})

it('shows the operation and records a finding within it', async () => {
  const user = userEvent.setup()
  renderPage()
  expect(await screen.findByRole('heading', { level: 1, name: OPERATION.name })).toBeInTheDocument()
  await screen.findByText(/no findings/i)
  await user.click(screen.getByRole('button', { name: 'New finding' }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).queryByRole('combobox', { name: 'Operation' })).not.toBeInTheDocument()
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), FINDING.title)
  await pick(user, 'Severity', 'Informational')
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ items: [FINDING], has_more: false, next_offset: null })
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByRole('heading', { level: 2, name: FINDING.title })).toBeInTheDocument()
  expect(screen.getByText('Informational')).toBeInTheDocument()
  expect(operationsApi.createFinding).toHaveBeenCalledWith('op-1', {
    id: expect.any(String), title: FINDING.title, severity: 'informational', field_values: {},
  })
})

it('links an optional evidence attack when the finding is created, but not when editing', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ items: [FINDING], has_more: false, next_offset: null })
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), 'With evidence')
  await user.type(within(dialog).getByRole('textbox', { name: 'Evidence attack ID' }), 'attack-1')
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  await waitFor(() => expect(operationsApi.createFinding).toHaveBeenCalledWith('op-1', expect.objectContaining({
    title: 'With evidence', attack_result_id: 'attack-1',
  })))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  await user.click(await screen.findByRole('button', { name: `Edit finding: ${FINDING.title}` }))
  expect(within(screen.getByRole('dialog')).queryByRole('textbox', { name: 'Evidence attack ID' }))
    .not.toBeInTheDocument()
})

it('reports a missing operation with a way back', async () => {
  jest.mocked(operationsApi.get).mockRejectedValue(NOT_FOUND)
  jest.mocked(operationsApi.listFindings).mockRejectedValue(NOT_FOUND)
  renderPage()
  expect(await screen.findByText(/operation not found/i)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /operations/i })).toHaveAttribute('href', '/operations')
  expect(screen.queryByRole('button', { name: 'New finding' })).not.toBeInTheDocument()
})

it('retains unsaved values after a failed save', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.createFinding).mockRejectedValue(new Error('Storage unavailable'))
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), 'Keep this')
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  expect(await within(dialog).findByText(/storage unavailable/i)).toBeInTheDocument()
  expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue('Keep this')
})

it.each([
  ['Cancel', async (user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) => {
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  }],
  ['Escape', async (user: ReturnType<typeof userEvent.setup>) => { await user.keyboard('{Escape}') }],
])('discards the draft when the dialog is dismissed with %s', async (_, dismiss) => {
  const user = userEvent.setup()
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  let dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), 'Draft title')
  await pick(user, 'Severity', 'Critical')
  await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Draft notes')
  await dismiss(user, dialog)
  await waitFor(() => { expect(screen.queryByRole('dialog', { hidden: true })).not.toBeInTheDocument() })
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  dialog = screen.getByRole('dialog')
  expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue('')
  expect(within(dialog).getByRole('combobox', { name: 'Severity' })).toHaveValue('Moderate')
  expect(within(dialog).getByRole('textbox', { name: 'Description' })).toHaveValue('')
})

it('reports a confirmed save separately from a failed list refresh', async () => {
  const user = userEvent.setup()
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  await user.type(within(screen.getByRole('dialog')).getByRole('textbox', { name: 'Title' }), 'Assessment')
  jest.mocked(operationsApi.listFindings).mockRejectedValue(new Error('List unavailable'))
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByText(/finding saved/i)).toBeInTheDocument()
  expect(await screen.findByText(/list unavailable/i)).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

it('disables saving a blank title and prevents duplicate submissions', async () => {
  const user = userEvent.setup()
  let finish!: (finding: Finding) => void
  jest.mocked(operationsApi.createFinding).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).getByRole('button', { name: 'Save finding' })).toBeDisabled()
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), 'Assessment')
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  expect(within(dialog).getByRole('button', { name: 'Saving…' })).toHaveAttribute('aria-disabled', 'true')
  await user.click(within(dialog).getByRole('button', { name: 'Saving…' }))
  await act(async () => { finish(FINDING) })
  expect(operationsApi.createFinding).toHaveBeenCalledTimes(1)
})

it('prefills edit fields and saves only editable values', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  const edited = { ...FINDING, title: 'Edited assessment', severity: 'critical', field_values: { description: 'Notes' } }
  jest.mocked(operationsApi.updateFinding).mockResolvedValue(edited)
  renderPage('/operations/op-1?offset=20')
  await user.click(await screen.findByRole('button', { name: `Edit finding: ${FINDING.title}` }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue(FINDING.title)
  expect(within(dialog).getByRole('combobox', { name: 'Severity' })).toHaveValue('Informational')
  await user.clear(within(dialog).getByRole('textbox', { name: 'Title' }))
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), edited.title)
  await pick(user, 'Severity', 'Critical')
  await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Notes')
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [edited] })
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await waitFor(() => { expect(screen.getByRole('heading', { name: edited.title })).toBeInTheDocument() })
  expect(operationsApi.updateFinding).toHaveBeenCalledWith('op-1', FINDING.id, {
    title: edited.title, severity: 'critical', field_values: { description: 'Notes' }, remove_retired_keys: [],
  })

  expect(operationsApi.createFinding).not.toHaveBeenCalled()
  expect(await screen.findByText('Page 1')).toBeInTheDocument()
})

it('displays and preserves both custom classifications when editing a finding', async () => {
  const user = userEvent.setup()
  const classified: FindingListItem = {
    ...FINDING, severity: 'Team severity', field_values: { harm_type: 'Team harm' },
  }
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [classified] })
  jest.mocked(operationsApi.updateFinding).mockResolvedValue(classified)
  renderPage()
  expect(await screen.findByText('Team severity')).toBeInTheDocument()
  expect(screen.getByText('Harm-type: Team harm')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: `Edit finding: ${FINDING.title}` }))
  expect(screen.getByRole('textbox', { name: 'Other Severity' })).toHaveValue('Team severity')
  expect(screen.getByRole('textbox', { name: 'Other Harm-type' })).toHaveValue('Team harm')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(operationsApi.updateFinding).toHaveBeenCalledWith('op-1', FINDING.id, {
    title: FINDING.title, severity: 'Team severity', field_values: { harm_type: 'Team harm' }, remove_retired_keys: [],
  })
})

it.each(['Cancel', 'Escape'])('discards edits with %s and restores the edit button focus', async dismiss => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  renderPage()
  const edit = await screen.findByRole('button', { name: `Edit finding: ${FINDING.title}` })
  await user.click(edit)
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), ' unsaved')
  if (dismiss === 'Escape') await user.keyboard('{Escape}')
  else await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(edit).toHaveFocus())
  expect(operationsApi.updateFinding).not.toHaveBeenCalled()
  await user.click(edit)
  expect(within(screen.getByRole('dialog')).getByRole('textbox', { name: 'Title' })).toHaveValue(FINDING.title)
})

it('retains an edited draft on update failure and shows why the backend rejected it', async () => {
  const user = userEvent.setup()
  const detail = 'Severity \'Critical\' matches the option \'Critical\' (key \'critical\'). Choose that option instead.'
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  jest.mocked(operationsApi.updateFinding).mockRejectedValue({
    isAxiosError: true, response: { status: 422, data: { detail } },
  })
  renderPage()
  await user.click(await screen.findByRole('button', { name: `Edit finding: ${FINDING.title}` }))
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), ' changed')
  await pick(user, 'Severity', 'Other')
  await user.type(within(dialog).getByRole('textbox', { name: 'Other Severity' }), 'Critical')
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  expect(await within(dialog).findByText(detail)).toBeInTheDocument()
  expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue(`${FINDING.title} changed`)
  expect(within(dialog).getByRole('textbox', { name: 'Other Severity' })).toHaveValue('Critical')
  expect(operationsApi.updateFinding).toHaveBeenCalledWith('op-1', FINDING.id, {
    title: `${FINDING.title} changed`, severity: 'Critical', field_values: {}, remove_retired_keys: [],
  })
})

it('shows configured rows and retired values on a finding card', async () => {
  const user = userEvent.setup()
  const steps = '1. Ask the Contoso assistant to repeat its instructions.\n2. Ask again in French.'
  const contosoFinding: FindingListItem = {
    ...FINDING, title: 'Contoso assistant repeats its system prompt', severity: 'sev1',
    field_values: {
      severity_rationale: 'customer_impact', category: 'model_security', subcategory: 'system_prompt_leak',
      technique: 'Contoso persona swap', steps, owner: 'Contoso red team',
      description: 'Recorded before Contoso configured its own fields.', contoso_ticket: 'CONTOSO-42',
    },
  }
  jest.mocked(operationsApi.getFindingFields).mockResolvedValue(CONTOSO_FINDING_FIELDS)
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [contosoFinding] })
  renderPage()

  const card = (await screen.findByRole('heading', { level: 2, name: contosoFinding.title })).closest('li') as HTMLElement
  expect(within(card).getByText('Sev 1')).toBeInTheDocument()
  const rows = ['Severity rationale: Customer impact', 'Category: Model security', 'Subcategory: System prompt leak',
    'Technique: Contoso persona swap', 'Owner: Contoso red team'].map(row => within(card).getByText(row))
  for (const [index, row] of rows.slice(1).entries()) {
    expect(rows[index].compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  }
  expect(within(card).getByText('Steps to reproduce')).toBeInTheDocument()
  expect(within(card).getByText(/Ask again in French/)).toHaveTextContent(steps, { normalizeWhitespace: false })
  expect(within(card).queryByText(/^Steps to reproduce:/)).not.toBeInTheDocument()

  const retired = within(card).getByRole('group', { name: 'Retired fields' })
  expect(within(retired).getByText('description')).toBeInTheDocument()
  expect(within(retired).getByText('Recorded before Contoso configured its own fields.')).toBeInTheDocument()
  expect(within(retired).getByText('contoso_ticket')).toBeInTheDocument()
  expect(within(retired).getByText('CONTOSO-42')).toBeInTheDocument()
  expect(within(retired).queryByRole('button')).not.toBeInTheDocument()
  expect(rows[4].compareDocumentPosition(retired) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

  await user.click(within(card).getByRole('button', { name: `Edit finding: ${contosoFinding.title}` }))
  expect(within(screen.getByRole('dialog')).getByRole('combobox', { name: 'Technique' })).toHaveValue('Other')
})

it('resends the kept retired values and lists the removed ones, and sends none for a new finding', async () => {
  const user = userEvent.setup()
  const legacy: FindingListItem = {
    ...FINDING, field_values: { harm_type: 'Malware', contoso_ticket: 'CONTOSO-42', retired_owner: 'Contoso' },
  }
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [legacy] })
  renderPage()
  await user.click(await screen.findByRole('button', { name: `Edit finding: ${FINDING.title}` }))
  await user.click(screen.getByRole('button', { name: 'Remove retired value contoso_ticket' }))
  await user.click(within(await findRemovalConfirmation()).getByRole('button', { name: 'Remove' }))
  await user.click(await screen.findByRole('button', { name: 'Save finding' }))
  await waitFor(() => expect(operationsApi.updateFinding).toHaveBeenCalledWith('op-1', FINDING.id, {
    title: FINDING.title, severity: 'informational', field_values: { harm_type: 'Malware', retired_owner: 'Contoso' },
    remove_retired_keys: ['contoso_ticket'],
  }))

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  await user.type(within(screen.getByRole('dialog')).getByRole('textbox', { name: 'Title' }), 'Fresh')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  await waitFor(() => expect(operationsApi.createFinding).toHaveBeenCalledWith('op-1', {
    id: expect.any(String), title: 'Fresh', severity: 'moderate', field_values: {},
  }))
})

it('disables New finding until the fields load and offers Retry on failure', async () => {
  const user = userEvent.setup()
  const fields = deferred<FindingFields>()
  jest.mocked(operationsApi.getFindingFields).mockReturnValueOnce(fields.promise)
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  renderPage()

  const newFinding = await screen.findByRole('button', { name: 'New finding' })
  expect(newFinding).toBeDisabled()
  expect(screen.getByText('Loading findings…')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { level: 2, name: FINDING.title })).not.toBeInTheDocument()

  await act(async () => { fields.reject(new Error('Fields offline')) })
  expect(await screen.findByText(/Could not load finding fields: Fields offline/)).toBeInTheDocument()
  expect(newFinding).toBeDisabled()
  expect(screen.queryByRole('heading', { level: 2, name: FINDING.title })).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Retry finding fields' }))
  expect(await screen.findByRole('heading', { level: 2, name: FINDING.title })).toBeInTheDocument()
  expect(screen.queryByText(/Could not load finding fields/)).not.toBeInTheDocument()
  expect(newFinding).toBeEnabled()
  expect(operationsApi.getFindingFields).toHaveBeenCalledTimes(2)
})

it('requires delete confirmation and refreshes the first page after deleting its last finding', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  renderPage('/operations/op-1?offset=20')
  const remove = await screen.findByRole('button', { name: `Delete finding: ${FINDING.title}` })
  await user.click(remove)
  let dialog = screen.getByRole('dialog')
  expect(within(dialog).getByText(/cannot be undone/i)).toHaveTextContent(FINDING.title)
  expect(operationsApi.deleteFinding).not.toHaveBeenCalled()
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(remove).toHaveFocus())
  await user.click(remove)
  dialog = screen.getByRole('dialog')
  jest.mocked(operationsApi.listFindings).mockResolvedValue(EMPTY)
  await user.click(within(dialog).getByRole('button', { name: 'Delete finding' }))
  expect(await screen.findByText(`Finding deleted: ${FINDING.title}`)).toBeInTheDocument()
  await waitFor(() => {
    expect(screen.getByText(/no findings/i)).toBeInTheDocument()
    expect(screen.getByText('Page 1')).toBeInTheDocument()
  })
  expect(operationsApi.deleteFinding).toHaveBeenCalledWith('op-1', FINDING.id)
  await waitFor(() => expect(screen.getByRole('button', { name: 'New finding' })).toHaveFocus())
})

it('refreshes operation usage after findings change so an emptied operation can be deleted', async () => {
  const user = userEvent.setup()
  const usage = { findings: 1, attacks: 0, scenario_runs: 0 }
  jest.mocked(operationsApi.get).mockResolvedValue({ ...OPERATION, usage })
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  renderPage()
  expect(await screen.findByRole('button', { name: 'Delete' })).toHaveAttribute('aria-disabled', 'true')
  await user.click(screen.getByRole('button', { name: `Delete finding: ${FINDING.title}` }))
  jest.mocked(operationsApi.get).mockResolvedValue(OPERATION)
  jest.mocked(operationsApi.listFindings).mockResolvedValue(EMPTY)
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete finding' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Delete' })).not.toHaveAttribute('aria-disabled'))
  expect(operationsApi.get).toHaveBeenCalledTimes(2)

  jest.mocked(operationsApi.get).mockRejectedValue(new Error('Busy'))
  await user.click(screen.getByRole('button', { name: 'New finding' }))
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), FINDING.title)
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  await waitFor(() => expect(operationsApi.get).toHaveBeenCalledTimes(3))
  expect(await screen.findByRole('heading', { level: 1, name: OPERATION.name })).toBeInTheDocument()
  expect(screen.queryByText(/Could not load operation/)).not.toBeInTheDocument()
})

it('keeps deletion errors visible and prevents duplicate confirmations', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  let rejectDelete: (error: Error) => void = () => {}
  jest.mocked(operationsApi.deleteFinding).mockImplementationOnce(() => new Promise((_, reject) => {
    rejectDelete = reject
  }))
  renderPage()
  await user.click(await screen.findByRole('button', { name: `Delete finding: ${FINDING.title}` }))
  const dialog = screen.getByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: 'Delete finding' }))
  const pending = within(dialog).getByRole('button', { name: 'Deleting…' })
  expect(pending).toHaveAttribute('aria-disabled', 'true')
  await user.click(pending)
  await user.keyboard('{Escape}')
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  await act(async () => { rejectDelete(new Error('Delete failed')) })
  expect(await within(dialog).findByText('Delete failed')).toBeInTheDocument()
  expect(operationsApi.deleteFinding).toHaveBeenCalledTimes(1)
})

it('opens findings export from the header without changing findings or the dashboard page', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  renderPage('/operations/op-1?offset=20')
  await screen.findByRole('heading', { level: 2, name: FINDING.title })
  const trigger = screen.getByRole('button', { name: 'Export findings' })
  await user.click(trigger)
  const dialog = screen.getByRole('dialog', { name: 'Export findings' })
  expect(await within(dialog).findByRole('checkbox', { name: FINDING.title })).toBeChecked()
  expect(operationsApi.listFindings).toHaveBeenCalledWith('op-1', { limit: 100, offset: 0 })

  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  await waitFor(() => expect(trigger).toHaveFocus())
  expect(screen.getByText('Page 2')).toBeInTheDocument()
  expect(jest.mocked(operationsApi.listFindings).mock.calls.filter(([, params]) => params.limit === 20))
    .toEqual([['op-1', { limit: 20, offset: 20 }]])
  expect(operationsApi.exportFindings).not.toHaveBeenCalled()
  expect(operationsApi.createFinding).not.toHaveBeenCalled()
  expect(operationsApi.updateFinding).not.toHaveBeenCalled()
  expect(operationsApi.deleteFinding).not.toHaveBeenCalled()
})

it('shows decorative icons on the header actions but not on the finding actions', async () => {
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  renderPage()
  const heading = await screen.findByRole('heading', { level: 1, name: OPERATION.name })
  await screen.findByRole('heading', { level: 2, name: FINDING.title })
  const header = within(heading.closest('header') as HTMLElement)
  const withoutIcon = ['Edit', 'Delete', 'Export findings', 'New finding']
    .filter(name => header.getByRole('button', { name }).querySelector('svg[aria-hidden="true"]') === null)
  expect(withoutIcon).toEqual([])
  expect(screen.getByRole('button', { name: `Edit finding: ${FINDING.title}` }).querySelector('svg')).toBeNull()
  expect(screen.getByRole('button', { name: `Delete finding: ${FINDING.title}` }).querySelector('svg')).toBeNull()
})

it('pages through findings', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValueOnce({ items: [FINDING], has_more: true, next_offset: 20 })
  renderPage()
  await screen.findByRole('heading', { level: 2, name: FINDING.title })
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await screen.findByText(/no findings/i)
  expect(jest.mocked(operationsApi.listFindings).mock.calls.at(-1)).toEqual(['op-1', { limit: 20, offset: 20 }])
  expect(screen.getByText('Page 2')).toBeInTheDocument()
})

it('loads the shown page again only when a live apply changes the severity order', async () => {
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  const pageCalls = (): unknown[] =>
    jest.mocked(operationsApi.listFindings).mock.calls.filter(([, params]) => params.limit === 20)
  const renderAgain = renderPage('/operations/op-1?offset=20')
  await screen.findByRole('heading', { level: 2, name: FINDING.title })
  expect(pageCalls()).toHaveLength(1)

  jest.mocked(operationsApi.getFindingFields).mockResolvedValue({ ...DEFAULT_FINDING_FIELDS_FIXTURE })
  jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'two' })
  await act(async () => { renderAgain() })
  await waitFor(() => expect(operationsApi.getFindingFields).toHaveBeenCalledTimes(2))
  await act(async () => {})
  expect(pageCalls()).toHaveLength(1)

  jest.mocked(operationsApi.getFindingFields).mockResolvedValue(CONTOSO_FINDING_FIELDS)
  jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'three' })
  await act(async () => { renderAgain() })
  await waitFor(() => expect(pageCalls()).toHaveLength(2))
  expect(pageCalls().at(-1)).toEqual(['op-1', { limit: 20, offset: 20 }])
})

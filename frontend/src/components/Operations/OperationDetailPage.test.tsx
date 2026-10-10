import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes } from 'react-router'

import { attacksApi, operationsApi, scenariosApi } from '@/services/api'
import type { Finding, FindingListItem, Operation } from '@/types'
import OperationDetailPage from './OperationDetailPage'

jest.mock('@/services/api', () => ({
  operationsApi: {
    get: jest.fn(), listFindings: jest.fn(), createFinding: jest.fn(),
    updateFinding: jest.fn(), deleteFinding: jest.fn(),
    listFindingEvidence: jest.fn(), detachFindingEvidence: jest.fn(),
    getFindingOptions: jest.fn(),
  },
  attacksApi: { listAttacks: jest.fn() },
  scenariosApi: { listRuns: jest.fn() },
}))

const OPERATION: Operation = { id: 'op-1', name: 'Red team / α%', created_at: '2026-10-06T20:00:00Z' }
const FINDING: FindingListItem = {
  id: 'finding-1', operation_id: 'op-1', title: 'Human assessment',
  description: '', severity: 'informational', created_at: '2026-10-06T20:00:00Z', evidence_count: 0,
}
const EMPTY = { items: [], has_more: false, next_offset: null }
const NOT_FOUND = { isAxiosError: true, response: { status: 404, data: { detail: 'missing' } } }

function renderPage(route = '/operations/op-1'): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/operations/:operationId" element={<OperationDetailPage />} />
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(operationsApi.getFindingOptions).mockResolvedValue({ harm_types: ['Malware', 'Other'] })
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

it('refreshes evidence counts without collapsing the expanded list after detachment', async () => {
  const user = userEvent.setup()
  const evidence = {
    item: {
      id: '123e4567-e89b-12d3-a456-426614174000', finding_id: FINDING.id, attack_result_id: 'owner',
      conversation_id: 'source', attached_at: '2026-10-08T12:00:00Z',
    },
    availability: 'available' as const, scenario_result_id: null,
  }
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [{ ...FINDING, evidence_count: 1 }] })
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue({ ...EMPTY, items: [evidence] })
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'Evidence (1)' }))
  await user.click(await screen.findByRole('button', { name: 'Remove link: source' }))
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  jest.mocked(operationsApi.listFindingEvidence).mockResolvedValue(EMPTY)
  await user.click(screen.getByRole('button', { name: 'Remove evidence link' }))
  expect(await screen.findByRole('button', { name: 'Evidence (0)' })).toHaveAttribute('aria-expanded', 'true')
  expect(await screen.findByText('No evidence attached.')).toBeInTheDocument()
})

it('links to exact-name execution history without fetching or duplicating activity', async () => {
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  renderPage()
  expect(await screen.findByRole('heading', { name: FINDING.title })).toBeInTheDocument()
  const link = screen.getByRole('link', { name: 'View execution history' })
  const url = new URL(link.getAttribute('href') ?? '', 'http://localhost')
  expect(url.pathname).toBe('/history/attacks')
  expect(url.searchParams.get('operation')).toBe(OPERATION.name)
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
  await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Severity' }), 'informational')
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ items: [FINDING], has_more: false, next_offset: null })
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  expect(await screen.findByRole('heading', { level: 2, name: FINDING.title })).toBeInTheDocument()
  expect(screen.getByText('Informational')).toBeInTheDocument()
  expect(operationsApi.createFinding).toHaveBeenCalledWith('op-1', {
    title: FINDING.title, severity: 'informational', description: '',
    severity_other: null, harm_type: null, harm_type_other: null,
  })
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
  await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Severity' }), 'critical')
  await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Draft notes')
  await dismiss(user, dialog)
  await waitFor(() => { expect(screen.queryByRole('dialog', { hidden: true })).not.toBeInTheDocument() })
  await user.click(await screen.findByRole('button', { name: 'New finding' }))
  dialog = screen.getByRole('dialog')
  expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue('')
  expect(within(dialog).getByRole('combobox', { name: 'Severity' })).toHaveValue('moderate')
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
  const edited = { ...FINDING, title: 'Edited assessment', severity: 'critical' as const, description: 'Notes' }
  jest.mocked(operationsApi.updateFinding).mockResolvedValue(edited)
  renderPage('/operations/op-1?offset=20')
  await user.click(await screen.findByRole('button', { name: `Edit finding: ${FINDING.title}` }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue(FINDING.title)
  expect(within(dialog).getByRole('combobox', { name: 'Severity' })).toHaveValue(FINDING.severity)
  await user.clear(within(dialog).getByRole('textbox', { name: 'Title' }))
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), edited.title)
  await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Severity' }), edited.severity)
  await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), edited.description)
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [edited] })
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
  await waitFor(() => { expect(screen.getByRole('heading', { name: edited.title })).toBeInTheDocument() })
  expect(operationsApi.updateFinding).toHaveBeenCalledWith('op-1', FINDING.id, {
    title: edited.title, severity: edited.severity, description: edited.description,
    severity_other: null, harm_type: null, harm_type_other: null,
  })

  expect(operationsApi.createFinding).not.toHaveBeenCalled()
  expect(await screen.findByText('Page 1')).toBeInTheDocument()
})

it('displays and preserves both custom classifications when editing a finding', async () => {
  const user = userEvent.setup()
  const classified: FindingListItem = {
    ...FINDING, severity: 'other', severity_other: 'Team severity', harm_type: 'Other', harm_type_other: 'Team harm',
  }
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [classified] })
  jest.mocked(operationsApi.updateFinding).mockResolvedValue(classified)
  renderPage()
  expect(await screen.findByText('Team severity')).toBeInTheDocument()
  expect(screen.getByText('Harm-type: Team harm')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: `Edit finding: ${FINDING.title}` }))
  expect(screen.getByRole('textbox', { name: 'Other severity' })).toHaveValue('Team severity')
  expect(screen.getByRole('textbox', { name: 'Other harm-type' })).toHaveValue('Team harm')
  await user.click(screen.getByRole('button', { name: 'Save finding' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(operationsApi.updateFinding).toHaveBeenCalledWith('op-1', FINDING.id, {
    title: FINDING.title, description: '', severity: 'other', severity_other: 'Team severity',
    harm_type: 'Other', harm_type_other: 'Team harm',
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

it('retains an edited draft on update failure', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.listFindings).mockResolvedValue({ ...EMPTY, items: [FINDING] })
  jest.mocked(operationsApi.updateFinding).mockRejectedValue(new Error('Update failed'))
  renderPage()
  await user.click(await screen.findByRole('button', { name: `Edit finding: ${FINDING.title}` }))
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Title' }), ' changed')
  await user.click(within(dialog).getByRole('button', { name: 'Save finding' }))
  expect(await within(dialog).findByText('Update failed')).toBeInTheDocument()
  expect(within(dialog).getByRole('textbox', { name: 'Title' })).toHaveValue(`${FINDING.title} changed`)
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

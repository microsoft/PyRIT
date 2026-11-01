import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import { DEFAULT_FINDING_FIELDS_FIXTURE } from '@/test-utils/findingFieldsFixtures'
import type { FindingListItem, FindingListResponse, Operation } from '@/types'
import { downloadFile } from '@/utils/downloadFile'
import FindingExportDialog from './FindingExportDialog'

jest.mock('@/services/api', () => ({ operationsApi: { listFindings: jest.fn(), exportFindings: jest.fn() } }))
jest.mock('@/utils/downloadFile', () => ({ downloadFile: jest.fn() }))

const OPERATION: Operation = {
  id: '6b1f0c3a-2d4e-4f5a-8b6c-7d8e9f0a1b2c', name: 'Synthetic evaluation', created_at: '2026-10-21T12:00:00Z',
}
const OTHER_OPERATION: Operation = {
  id: '1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f', name: 'Another evaluation', created_at: '2026-10-21T12:00:00Z',
}

function syntheticFinding(index: number, overrides: Partial<FindingListItem> = {}): FindingListItem {
  return {
    id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`, operation_id: OPERATION.id,
    title: `Synthetic finding ${index}`, severity: 'moderate', field_values: {},
    created_at: '2026-10-21T12:00:00Z', evidence_count: 0, ...overrides,
  }
}

function onePage(items: FindingListItem[]): FindingListResponse {
  return { items, has_more: false, next_offset: null }
}

const FIRST_PAGE: FindingListResponse = {
  items: Array.from({ length: 100 }, (_, index) => syntheticFinding(index + 1)), has_more: true, next_offset: 100,
}
const LAST_PAGE_FINDING = syntheticFinding(101, { title: 'Final-page finding', severity: 'Team-specific severity' })
const LAST_PAGE = onePage([LAST_PAGE_FINDING])
const ALPHA = syntheticFinding(1, { title: 'Alpha', severity: 'critical' })
const BETA = syntheticFinding(2, { title: 'Beta', severity: 'low' })
const GAMMA = syntheticFinding(3, { title: 'Gamma', severity: 'informational' })
const BLOB = new Blob(['synthetic document'])
const FILENAME_TIME = '\\d{4}-\\d{2}-\\d{2}-\\d{4}'
const FINDING_NOT_FOUND = { isAxiosError: true, response: { status: 404, data: { detail: 'Finding not found' } } }
const OPERATION_NOT_FOUND = { isAxiosError: true, response: { status: 404, data: { detail: 'Operation not found' } } }
const REVIEW_AGAIN = 'Review the list and download again.'

function renderDialog(operation: Operation = OPERATION): ReturnType<typeof render> {
  return render(
    <FluentProvider theme={webLightTheme}>
      <FindingExportDialog key={operation.id} operation={operation} fields={DEFAULT_FINDING_FIELDS_FIXTURE} />
    </FluentProvider>,
  )
}

async function openDialog(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  // Tabster restores the background's accessibility after a deferred update once a dialog closes.
  await user.click(await screen.findByRole('button', { name: 'Export findings' }))
  return screen.getByRole('dialog', { name: 'Export findings' })
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(operationsApi.listFindings).mockResolvedValue(onePage([ALPHA, BETA, GAMMA]))
  jest.mocked(operationsApi.exportFindings).mockResolvedValue(BLOB)
})

describe('FindingExportDialog', () => {
  it('loads every page before enabling download and sends exactly the selected final-page finding', async () => {
    const user = userEvent.setup()
    const lastPage = deferred<FindingListResponse>()
    jest.mocked(operationsApi.listFindings).mockImplementation(
      async (_, { offset }) => (offset === 0 ? FIRST_PAGE : lastPage.promise),
    )
    renderDialog()
    const dialog = await openDialog(user)
    await waitFor(() => expect(operationsApi.listFindings).toHaveBeenCalledTimes(2))
    expect(operationsApi.listFindings).toHaveBeenNthCalledWith(1, OPERATION.id, { limit: 100, offset: 0 })
    expect(operationsApi.listFindings).toHaveBeenNthCalledWith(2, OPERATION.id, { limit: 100, offset: 100 })
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Download Word' })).toBeDisabled()

    await act(async () => { lastPage.resolve(LAST_PAGE) })
    const selectAll = within(dialog).getByRole('checkbox', { name: 'Select all' })
    const rows = within(dialog).getAllByRole('checkbox').filter(checkbox => checkbox !== selectAll)
    expect(rows).toHaveLength(101)
    for (const row of rows) expect(row).toBeChecked()
    expect(selectAll).toBeChecked()
    expect(within(dialog).getByText('Team-specific severity')).toBeInTheDocument()

    await user.click(selectAll)
    expect(within(dialog).queryAllByRole('checkbox', { checked: true })).toHaveLength(0)
    expect(within(dialog).getByRole('button', { name: 'Download Word' })).toBeDisabled()

    const lastRow = within(dialog).getByRole('checkbox', { name: LAST_PAGE_FINDING.title })
    await user.click(lastRow)
    expect(selectAll).toBePartiallyChecked()
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Format' }), 'Markdown (.md)')
    expect(lastRow).toBeChecked()
    await user.click(within(dialog).getByRole('button', { name: 'Download Markdown' }))

    await waitFor(() => expect(downloadFile).toHaveBeenCalledTimes(1))
    expect(operationsApi.exportFindings).toHaveBeenCalledWith(
      OPERATION.id,
      {
        finding_ids: [LAST_PAGE_FINDING.id],
        format: 'markdown',
        dashboard_origin: 'http://localhost',
      },
      expect.any(AbortSignal),
    )
    expect(downloadFile).toHaveBeenCalledWith(
      BLOB, expect.stringMatching(new RegExp(`^copyrit-findings-synthetic-evaluation-${FILENAME_TIME}\\.md$`)),
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Export findings' })).toHaveFocus())
  })

  it('lists and exports once a finding that a later page repeats', async () => {
    const user = userEvent.setup()
    // A finding created while pages load shifts the next page, so it can start with the previous page's last row.
    jest.mocked(operationsApi.listFindings)
      .mockResolvedValueOnce({ items: [ALPHA, BETA], has_more: true, next_offset: 2 })
      .mockResolvedValueOnce(onePage([BETA, GAMMA]))
    renderDialog()
    const dialog = await openDialog(user)
    await within(dialog).findByRole('checkbox', { name: GAMMA.title })
    expect(within(dialog).getAllByRole('checkbox', { name: BETA.title })).toHaveLength(1)
    expect(within(dialog).getAllByRole('checkbox')).toHaveLength(4)
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    await waitFor(() => expect(operationsApi.exportFindings).toHaveBeenCalledTimes(1))
    expect(jest.mocked(operationsApi.exportFindings).mock.calls[0][1].finding_ids)
      .toEqual([ALPHA.id, BETA.id, GAMMA.id])
  })

  it('moves between all, none and subset selections', async () => {
    const user = userEvent.setup()
    renderDialog()
    const dialog = await openDialog(user)
    const selectAll = await within(dialog).findByRole('checkbox', { name: 'Select all' })
    const [alpha, beta, gamma] = [ALPHA, BETA, GAMMA].map(finding => (
      within(dialog).getByRole('checkbox', { name: finding.title })))
    const download = within(dialog).getByRole('button', { name: 'Download Word' })
    expect(selectAll).toBeChecked()
    expect(selectAll).not.toBePartiallyChecked()

    await user.click(beta)
    expect(selectAll).toBePartiallyChecked()
    expect(download).toBeEnabled()
    await user.click(selectAll)
    for (const row of [alpha, beta, gamma]) expect(row).toBeChecked()
    expect(selectAll).toBeChecked()

    await user.click(selectAll)
    for (const row of [alpha, beta, gamma]) expect(row).not.toBeChecked()
    expect(selectAll).not.toBeChecked()
    expect(selectAll).not.toBePartiallyChecked()
    expect(download).toBeDisabled()

    await user.click(alpha)
    expect(selectAll).toBePartiallyChecked()
    await user.click(beta)
    await user.click(gamma)
    expect(selectAll).toBeChecked()
    expect(selectAll).not.toBePartiallyChecked()
  })

  it('defaults to Word and keeps the selection when the format changes', async () => {
    const user = userEvent.setup()
    renderDialog()
    const dialog = await openDialog(user)
    const beta = await within(dialog).findByRole('checkbox', { name: BETA.title })
    const format = within(dialog).getByRole('combobox', { name: 'Format' })
    expect(format).toHaveDisplayValue('Word (.docx)')
    expect(within(format).getAllByRole('option').map(option => option.textContent))
      .toEqual(['Word (.docx)', 'Markdown (.md)'])

    await user.click(beta)
    await user.selectOptions(format, 'Markdown (.md)')
    expect(within(dialog).getByRole('button', { name: 'Download Markdown' })).toBeEnabled()
    await user.selectOptions(format, 'Word (.docx)')
    expect(beta).not.toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: ALPHA.title })).toBeChecked()
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    await waitFor(() => expect(downloadFile).toHaveBeenCalledTimes(1))
    expect(operationsApi.exportFindings).toHaveBeenCalledWith(OPERATION.id, {
      finding_ids: [ALPHA.id, GAMMA.id], format: 'docx', dashboard_origin: 'http://localhost',
    }, expect.any(AbortSignal))
    expect(downloadFile).toHaveBeenCalledWith(
      BLOB, expect.stringMatching(new RegExp(`^copyrit-findings-synthetic-evaluation-${FILENAME_TIME}\\.docx$`)),
    )
  })

  it('shows an empty Operation without an enabled download', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.listFindings).mockResolvedValue(onePage([]))
    renderDialog()
    const dialog = await openDialog(user)
    expect(await within(dialog).findByText('This operation has no findings to export.')).toBeInTheDocument()
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Download Word' })).toBeDisabled()
  })

  it('offers no partial selection when a page fails, and retries the whole list', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.listFindings)
      .mockResolvedValueOnce(FIRST_PAGE)
      .mockRejectedValueOnce(new Error('Page unavailable'))
      .mockResolvedValueOnce(FIRST_PAGE)
      .mockResolvedValueOnce(LAST_PAGE)
    renderDialog()
    const dialog = await openDialog(user)
    expect(await within(dialog).findByText(/Could not load findings: Page unavailable/)).toBeInTheDocument()
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Download Word' })).toBeDisabled()

    await user.click(within(dialog).getByRole('button', { name: 'Retry' }))
    expect(await within(dialog).findByRole('checkbox', { name: LAST_PAGE_FINDING.title })).toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: 'Select all' })).toBeChecked()
    expect(within(dialog).queryByText(/Could not load findings/)).not.toBeInTheDocument()
    expect(jest.mocked(operationsApi.listFindings).mock.calls.map(([, params]) => params.offset))
      .toEqual([0, 100, 0, 100])
  })

  it('keeps the selection after an export error so the download can be retried', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.exportFindings).mockRejectedValueOnce({
      isAxiosError: true, response: { status: 500, data: { detail: 'Renderer unavailable' } },
    })
    renderDialog()
    const dialog = await openDialog(user)
    await user.click(await within(dialog).findByRole('checkbox', { name: BETA.title }))
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    expect(await within(dialog).findByText('Could not export findings: Renderer unavailable')).toBeInTheDocument()
    expect(downloadFile).not.toHaveBeenCalled()
    expect(within(dialog).getByRole('checkbox', { name: BETA.title })).not.toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: ALPHA.title })).toBeChecked()

    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))
    await waitFor(() => expect(downloadFile).toHaveBeenCalledTimes(1))
    for (const [, request] of jest.mocked(operationsApi.exportFindings).mock.calls) {
      expect(request.finding_ids).toEqual([ALPHA.id, GAMMA.id])
    }
  })

  it('shows each finding\'s creation time without changing its checkbox name', async () => {
    const user = userEvent.setup()
    const earlier = syntheticFinding(4, { title: 'Repeated title', created_at: '2026-10-20T08:15:00Z' })
    const later = syntheticFinding(5, { title: 'Repeated title', created_at: '2026-10-21T17:45:00Z' })
    jest.mocked(operationsApi.listFindings).mockResolvedValue(onePage([later, earlier]))
    renderDialog()
    const dialog = await openDialog(user)

    const rows = await within(dialog).findAllByRole('checkbox', { name: 'Repeated title' })
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveAccessibleDescription(new Date(later.created_at).toLocaleString())
    expect(rows[1]).toHaveAccessibleDescription(new Date(earlier.created_at).toLocaleString())
    expect(within(dialog).getByText(new Date(earlier.created_at).toLocaleString())).toBeInTheDocument()
  })

  it('reloads the list, keeps the rest of the selection and leaves new findings unchecked when a finding was deleted', async () => {
    const user = userEvent.setup()
    const delta = syntheticFinding(4, { title: 'Delta' })
    jest.mocked(operationsApi.listFindings)
      .mockResolvedValueOnce(onePage([ALPHA, BETA, GAMMA]))
      .mockResolvedValueOnce(onePage([BETA, GAMMA, delta]))
    jest.mocked(operationsApi.exportFindings).mockRejectedValueOnce(FINDING_NOT_FOUND)
    renderDialog()
    const dialog = await openDialog(user)
    await user.click(await within(dialog).findByRole('checkbox', { name: BETA.title }))
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    expect(await within(dialog).findByText(
      `"${ALPHA.title}" no longer exists and was removed from your selection. ${REVIEW_AGAIN}`,
    )).toBeInTheDocument()
    expect(operationsApi.listFindings).toHaveBeenCalledTimes(2)
    expect(within(dialog).queryByRole('checkbox', { name: ALPHA.title })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('checkbox', { name: BETA.title })).not.toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: GAMMA.title })).toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: delta.title })).not.toBeChecked()
    expect(within(dialog).queryByText(/Could not export findings/)).not.toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))
    await waitFor(() => expect(downloadFile).toHaveBeenCalledTimes(1))
    expect(jest.mocked(operationsApi.exportFindings).mock.calls[1][1].finding_ids).toEqual([GAMMA.id])
  })

  it('names every deleted finding in one warning', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.listFindings)
      .mockResolvedValueOnce(onePage([ALPHA, BETA, GAMMA]))
      .mockResolvedValueOnce(onePage([BETA]))
    jest.mocked(operationsApi.exportFindings).mockRejectedValueOnce(FINDING_NOT_FOUND)
    renderDialog()
    const dialog = await openDialog(user)
    await within(dialog).findByRole('checkbox', { name: 'Select all' })
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    expect(await within(dialog).findByText(
      `"${ALPHA.title}", "${GAMMA.title}" no longer exist and were removed from your selection. ${REVIEW_AGAIN}`,
    )).toBeInTheDocument()
    expect(within(dialog).getByRole('checkbox', { name: BETA.title })).toBeChecked()
  })

  it('keeps focus on Download while the list reloads after a deleted finding', async () => {
    const user = userEvent.setup()
    const reload = deferred<FindingListResponse>()
    jest.mocked(operationsApi.listFindings)
      .mockResolvedValueOnce(onePage([ALPHA, BETA, GAMMA]))
      .mockReturnValueOnce(reload.promise)
    jest.mocked(operationsApi.exportFindings).mockRejectedValueOnce(FINDING_NOT_FOUND)
    renderDialog()
    const dialog = await openDialog(user)
    await within(dialog).findByRole('checkbox', { name: 'Select all' })
    within(dialog).getByRole('button', { name: 'Download Word' }).focus()
    await user.keyboard('{Enter}')

    await within(dialog).findByText('Loading findings…')
    const download = within(dialog).getByRole('button', { name: 'Download Word' })
    // A natively disabled button drops focus out of the dialog; an aria-disabled one keeps it.
    expect(download).not.toBeDisabled()
    expect(download).toHaveAttribute('aria-disabled', 'true')
    expect(download).toHaveFocus()

    await act(async () => { reload.resolve(onePage([BETA, GAMMA])) })
    expect(download).toBeEnabled()
    expect(download).not.toHaveAttribute('aria-disabled')
    expect(download).toHaveFocus()
  })

  it('keeps Download focusable but unavailable when every selected finding was deleted', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.listFindings)
      .mockResolvedValueOnce(onePage([ALPHA, BETA, GAMMA]))
      .mockResolvedValueOnce(onePage([ALPHA, GAMMA]))
    jest.mocked(operationsApi.exportFindings).mockRejectedValueOnce(FINDING_NOT_FOUND)
    renderDialog()
    const dialog = await openDialog(user)
    await user.click(await within(dialog).findByRole('checkbox', { name: 'Select all' }))
    await user.click(within(dialog).getByRole('checkbox', { name: BETA.title }))
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    expect(await within(dialog).findByText(
      `"${BETA.title}" no longer exists and was removed from your selection. ${REVIEW_AGAIN}`,
    )).toBeInTheDocument()
    const download = within(dialog).getByRole('button', { name: 'Download Word' })
    expect(download).not.toBeDisabled()
    expect(download).toHaveAttribute('aria-disabled', 'true')
    expect(download).toHaveFocus()
    await user.click(download)
    expect(operationsApi.exportFindings).toHaveBeenCalledTimes(1)

    await user.click(within(dialog).getByRole('checkbox', { name: ALPHA.title }))
    expect(download).toBeEnabled()
  })

  it('shows the export error when the reload still finds every selected finding', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.exportFindings).mockRejectedValueOnce(FINDING_NOT_FOUND)
    renderDialog()
    const dialog = await openDialog(user)
    await within(dialog).findByRole('checkbox', { name: 'Select all' })
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    expect(await within(dialog).findByText('Could not export findings: Finding not found')).toBeInTheDocument()
    expect(operationsApi.listFindings).toHaveBeenCalledTimes(2)
    for (const finding of [ALPHA, BETA, GAMMA]) {
      expect(within(dialog).getByRole('checkbox', { name: finding.title })).toBeChecked()
    }
    expect(within(dialog).queryByText(new RegExp(REVIEW_AGAIN))).not.toBeInTheDocument()
  })

  it('shows the load error when the Operation is gone after a deleted-finding reload', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.listFindings)
      .mockResolvedValueOnce(onePage([ALPHA, BETA, GAMMA]))
      .mockRejectedValueOnce(OPERATION_NOT_FOUND)
    jest.mocked(operationsApi.exportFindings).mockRejectedValueOnce(OPERATION_NOT_FOUND)
    renderDialog()
    const dialog = await openDialog(user)
    await within(dialog).findByRole('checkbox', { name: 'Select all' })
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    expect(await within(dialog).findByText(/Could not load findings: Operation not found/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Download Word' })).toBeDisabled()
  })

  it('keeps the dialog and selection when the browser download cannot start', async () => {
    const user = userEvent.setup()
    jest.mocked(downloadFile).mockImplementationOnce(() => { throw new Error('Download blocked') })
    renderDialog()
    const dialog = await openDialog(user)
    await user.click(await within(dialog).findByRole('checkbox', { name: BETA.title }))
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    expect(await within(dialog).findByText('Could not export findings: Download blocked')).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Export findings' })).toBeInTheDocument()
    expect(within(dialog).getByRole('checkbox', { name: BETA.title })).not.toBeChecked()
    expect(within(dialog).getByRole('button', { name: 'Download Word' })).toBeEnabled()
  })

  it('prevents duplicate export requests while the document is prepared', async () => {
    const user = userEvent.setup()
    const exported = deferred<Blob>()
    jest.mocked(operationsApi.exportFindings).mockReturnValueOnce(exported.promise)
    renderDialog()
    const dialog = await openDialog(user)
    await within(dialog).findByRole('checkbox', { name: 'Select all' })
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    const pending = within(dialog).getByRole('button', { name: 'Preparing download…' })
    expect(pending).toHaveAttribute('aria-disabled', 'true')
    expect(within(dialog).getByRole('checkbox', { name: 'Select all' })).toBeDisabled()
    expect(within(dialog).getByRole('combobox', { name: 'Format' })).toBeDisabled()
    await user.click(pending)
    expect(operationsApi.exportFindings).toHaveBeenCalledTimes(1)

    await act(async () => { exported.resolve(BLOB) })
    expect(downloadFile).toHaveBeenCalledTimes(1)
  })

  it.each(['Cancel', 'Escape'])('restores focus to the trigger after closing with %s', async dismiss => {
    const user = userEvent.setup()
    renderDialog()
    const dialog = await openDialog(user)
    await within(dialog).findByRole('checkbox', { name: 'Select all' })
    if (dismiss === 'Escape') await user.keyboard('{Escape}')
    else await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Export findings' })).toHaveFocus())
    expect(operationsApi.exportFindings).not.toHaveBeenCalled()
  })

  it('warns that exports can contain unredacted sensitive content', async () => {
    const user = userEvent.setup()
    renderDialog()
    const dialog = await openDialog(user)
    expect(within(dialog).getByText(
      'Findings, attack objectives and score rationales can contain sensitive content. '
      + 'Nothing is redacted automatically.',
    )).toBeInTheDocument()
  })

  it('ignores a list that finishes after the dialog was closed and reopened', async () => {
    const user = userEvent.setup()
    const stale = deferred<FindingListResponse>()
    const fresh = syntheticFinding(7, { title: 'Fresh finding' })
    jest.mocked(operationsApi.listFindings)
      .mockReturnValueOnce(stale.promise)
      .mockResolvedValueOnce(onePage([fresh]))
    renderDialog()
    let dialog = await openDialog(user)
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    dialog = await openDialog(user)
    expect(await within(dialog).findByRole('checkbox', { name: fresh.title })).toBeChecked()

    await act(async () => { stale.resolve(onePage([syntheticFinding(8, { title: 'Stale finding' })])) })
    expect(within(dialog).queryByRole('checkbox', { name: 'Stale finding' })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('checkbox', { name: fresh.title })).toBeChecked()
  })

  it('does not download a file that finishes after the dialog was closed, and reopens with everything selected', async () => {
    const user = userEvent.setup()
    const exported = deferred<Blob>()
    jest.mocked(operationsApi.exportFindings).mockReturnValueOnce(exported.promise)
    renderDialog()
    let dialog = await openDialog(user)
    await user.click(await within(dialog).findByRole('checkbox', { name: BETA.title }))
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'Format' }), 'Markdown (.md)')
    await user.click(within(dialog).getByRole('button', { name: 'Download Markdown' }))
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(jest.mocked(operationsApi.exportFindings).mock.calls[0][2]?.aborted).toBe(true)

    await act(async () => { exported.resolve(BLOB) })
    expect(downloadFile).not.toHaveBeenCalled()

    dialog = await openDialog(user)
    expect(await within(dialog).findByRole('checkbox', { name: BETA.title })).toBeChecked()
    expect(within(dialog).getByRole('checkbox', { name: 'Select all' })).toBeChecked()
    expect(within(dialog).getByRole('combobox', { name: 'Format' })).toHaveDisplayValue('Word (.docx)')
    expect(within(dialog).getByRole('button', { name: 'Download Word' })).toBeEnabled()
    expect(within(dialog).queryByText(/Could not export findings/)).not.toBeInTheDocument()
  })

  it('does not download the previous Operation\'s file after the Operation changes', async () => {
    const user = userEvent.setup()
    const exported = deferred<Blob>()
    jest.mocked(operationsApi.exportFindings).mockReturnValueOnce(exported.promise)
    const view = renderDialog(OPERATION)
    const dialog = await openDialog(user)
    await within(dialog).findByRole('checkbox', { name: 'Select all' })
    await user.click(within(dialog).getByRole('button', { name: 'Download Word' }))

    view.rerender(
      <FluentProvider theme={webLightTheme}>
        <FindingExportDialog key={OTHER_OPERATION.id} operation={OTHER_OPERATION}
          fields={DEFAULT_FINDING_FIELDS_FIXTURE} />
      </FluentProvider>,
    )
    expect(jest.mocked(operationsApi.exportFindings).mock.calls[0][2]?.aborted).toBe(true)
    await act(async () => { exported.resolve(BLOB) })
    expect(downloadFile).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('ignores a list from the previous Operation that finishes after the Operation changes', async () => {
    const user = userEvent.setup()
    const staleList = deferred<FindingListResponse>()
    const other = syntheticFinding(9, { operation_id: OTHER_OPERATION.id, title: 'Other operation finding' })
    jest.mocked(operationsApi.listFindings)
      .mockReturnValueOnce(staleList.promise)
      .mockResolvedValueOnce(onePage([other]))
    const view = renderDialog(OPERATION)
    await openDialog(user)

    view.rerender(
      <FluentProvider theme={webLightTheme}>
        <FindingExportDialog key={OTHER_OPERATION.id} operation={OTHER_OPERATION}
          fields={DEFAULT_FINDING_FIELDS_FIXTURE} />
      </FluentProvider>,
    )
    const dialog = await openDialog(user)
    expect(await within(dialog).findByRole('checkbox', { name: other.title })).toBeChecked()
    expect(operationsApi.listFindings).toHaveBeenLastCalledWith(OTHER_OPERATION.id, { limit: 100, offset: 0 })

    await act(async () => { staleList.resolve(onePage([ALPHA])) })
    expect(within(dialog).queryByRole('checkbox', { name: ALPHA.title })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('checkbox', { name: other.title })).toBeChecked()
  })

  it('warns when selected findings have retired values', async () => {
    const user = userEvent.setup()
    const legacyAlpha = { ...ALPHA, field_values: { harm_type: 'Malware', contoso_ticket: 'CONTOSO-42' } }
    const legacyBeta = { ...BETA, field_values: { description: 'Kept from the earlier form.', retired_owner: 'Contoso' } }
    const singular = 'Retired fields aren\'t included in the export. 1 selected finding has retired values.'
    const plural = 'Retired fields aren\'t included in the export. 2 selected findings have retired values.'
    jest.mocked(operationsApi.listFindings).mockResolvedValue(onePage([legacyAlpha, legacyBeta, GAMMA]))
    renderDialog()
    const dialog = await openDialog(user)
    const selectAll = await within(dialog).findByRole('checkbox', { name: 'Select all' })
    await user.click(selectAll)
    expect(within(dialog).queryByText(/Retired fields/)).not.toBeInTheDocument()

    await user.click(within(dialog).getByRole('checkbox', { name: legacyAlpha.title }))
    expect(within(dialog).getByText(singular)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('checkbox', { name: GAMMA.title }))
    expect(within(dialog).getByText(singular)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('checkbox', { name: legacyBeta.title }))
    expect(within(dialog).getByText(plural)).toBeInTheDocument()
    expect(within(dialog).queryByText(singular)).not.toBeInTheDocument()

    await user.click(within(dialog).getByRole('checkbox', { name: legacyAlpha.title }))
    await user.click(within(dialog).getByRole('checkbox', { name: legacyBeta.title }))
    expect(within(dialog).queryByText(/Retired fields/)).not.toBeInTheDocument()
    expect(within(dialog).getByRole('checkbox', { name: GAMMA.title })).toBeChecked()
  })
})

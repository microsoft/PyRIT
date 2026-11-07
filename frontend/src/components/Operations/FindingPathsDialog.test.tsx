import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { FindingListItem } from '@/types'
import FindingPathsDialog from './FindingPathsDialog'

jest.mock('@/services/api', () => ({ operationsApi: { replaceFindingPaths: jest.fn() } }))

const BASE: FindingListItem = {
  id: 'parent', operation_id: 'op-1', title: 'Data theft', description: '', severity: 'critical',
  created_at: '2026-11-06T12:00:00Z', evidence_count: 0, parent_finding_id: null, demonstrated: true,
  paths: [], child_count: 2,
}
const RECON: FindingListItem = { ...BASE, id: 'recon', title: 'Recon', parent_finding_id: 'parent', child_count: 0 }
const CREDS: FindingListItem = { ...RECON, id: 'creds', title: 'Obtain creds', demonstrated: false }
const PARENT: FindingListItem = {
  ...BASE, paths: [{ id: 'path-1', name: 'Only way', description: 'Kept as written', steps: ['recon'], complete: true }],
}

function renderDialog(props: Partial<React.ComponentProps<typeof FindingPathsDialog>> = {}): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <FindingPathsDialog operationId="op-1" finding={PARENT} subFindings={[RECON, CREDS]} onSaved={jest.fn()}
        onClose={jest.fn()} {...props} />
    </FluentProvider>,
  )
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe('FindingPathsDialog', () => {
  it('derives completeness from each step and keeps the path description', async () => {
    const user = userEvent.setup()
    const onSaved = jest.fn()
    jest.mocked(operationsApi.replaceFindingPaths).mockResolvedValue(PARENT)
    renderDialog({ onSaved })
    const path = screen.getByRole('group', { name: /Only way/ })
    expect(within(path).getByText('Complete')).toBeInTheDocument()

    await user.selectOptions(within(path).getByRole('combobox', { name: 'Add step' }), CREDS.title)
    expect(within(path).getByText('Partial')).toBeInTheDocument()
    expect(within(path).getByText('(not demonstrated)', { exact: false })).toBeInTheDocument()
    expect(within(path).getByRole('combobox', { name: 'Add step' })).toBeDisabled()
    expect(within(path).getByText('Every sub-finding is already a step of this path.')).toBeInTheDocument()
    expect(within(path).getByRole('button', { name: `Move up: ${RECON.title}` })).toBeDisabled()
    expect(within(path).getByRole('button', { name: `Move down: ${CREDS.title}` })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Save paths' }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(PARENT))
    expect(operationsApi.replaceFindingPaths).toHaveBeenCalledWith('op-1', PARENT.id, {
      paths: [{ id: 'path-1', name: 'Only way', description: 'Kept as written', steps: [RECON.id, CREDS.id] }],
    })
  })

  it('removes every path, and does not save a path without steps', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.replaceFindingPaths).mockResolvedValue({ ...PARENT, paths: [] })
    renderDialog()
    const path = screen.getByRole('group', { name: /Only way/ })
    await user.click(within(path).getByRole('button', { name: `Remove step: ${RECON.title}` }))
    expect(within(path).getByText('Add at least one step.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save paths' })).toBeDisabled()

    await user.click(within(path).getByRole('button', { name: 'Remove path: Only way' }))
    expect(screen.getByText('This finding has no paths.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save paths' }))
    await waitFor(() => expect(operationsApi.replaceFindingPaths).toHaveBeenCalledWith('op-1', PARENT.id, { paths: [] }))
  })

  it('keeps the drafts and explains a rejected save, and blocks a second save while one is pending', async () => {
    const user = userEvent.setup()
    const pending = deferred<FindingListItem>()
    jest.mocked(operationsApi.replaceFindingPaths)
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { detail: 'Steps must be direct sub-findings.' } } })
      .mockReturnValueOnce(pending.promise)
    const onSaved = jest.fn()
    renderDialog({ onSaved })
    await user.type(screen.getByRole('textbox', { name: 'Name' }), ' renamed')
    await user.click(screen.getByRole('button', { name: 'Save paths' }))

    expect(await screen.findByText('Could not save paths: Steps must be direct sub-findings.')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Only way renamed')
    expect(onSaved).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Save paths' }))
    const saving = screen.getByRole('button', { name: 'Saving…' })
    expect(saving).toHaveAttribute('aria-disabled', 'true')
    await user.click(saving)
    expect(operationsApi.replaceFindingPaths).toHaveBeenCalledTimes(2)
    await act(async () => { pending.resolve(PARENT) })
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('closes on Cancel without saving', async () => {
    const user = userEvent.setup()
    const onClose = jest.fn()
    renderDialog({ onClose })
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(operationsApi.replaceFindingPaths).not.toHaveBeenCalled()
  })
})

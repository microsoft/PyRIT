import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

import { attacksApi, operationsApi } from '@/services/api'

import AttachToFindingsButton from './AttachToFindingsButton'

jest.mock('@/services/api', () => ({
  attacksApi: { getAttack: jest.fn() },
  operationsApi: {
    get: jest.fn(), list: jest.fn(), listFindings: jest.fn(), attachFindingEvidence: jest.fn(), createFinding: jest.fn(),
    getFindingOptions: jest.fn(),
  },
}))

const OPERATION = { id: 'scan-operation', name: 'Scan operation', created_at: '2026-10-08T12:00:00Z' }
const FINDING = {
  id: 'finding', operation_id: OPERATION.id, title: 'Proof', severity: 'low', description: '',
  created_at: '2026-10-08T12:00:00Z', evidence_count: 0,
}

function TestWrapper({ children }: { readonly children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}><MemoryRouter>{children}</MemoryRouter></FluentProvider>
}

describe('AttachToFindingsButton', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(attacksApi.getAttack).mockResolvedValue({ operation_id: null } as never)
    jest.mocked(operationsApi.get).mockResolvedValue(OPERATION as never)
    jest.mocked(operationsApi.list).mockResolvedValue({ items: [{ ...OPERATION, finding_counts: {} }] } as never)
    jest.mocked(operationsApi.listFindings).mockResolvedValue({ items: [FINDING], has_more: false, next_offset: null } as never)
    jest.mocked(operationsApi.attachFindingEvidence).mockResolvedValue({ item: { id: 'evidence' }, created: true } as never)
  })

  it('should attach the attack to a finding in the default operation and report it', async () => {
    const user = userEvent.setup()
    const onAttached = jest.fn()
    render(<TestWrapper>
      <AttachToFindingsButton attackResultId="attack-1" defaultOperationId={OPERATION.id} onAttached={onAttached} />
    </TestWrapper>)

    const button = screen.getByRole('button', { name: 'Attach to findings' })
    await user.click(button)
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(operationsApi.get).toHaveBeenCalledWith(OPERATION.id)
    await user.click(await screen.findByRole('radio', { name: /Proof/ }))
    await user.click(screen.getByRole('button', { name: 'Attach attack' }))

    await waitFor(() => { expect(onAttached).toHaveBeenCalledTimes(1) })
    expect(operationsApi.attachFindingEvidence).toHaveBeenCalledWith(OPERATION.id, FINDING.id, { attack_result_id: 'attack-1' })
    await waitFor(() => { expect(button).toHaveFocus() })
  })

  it('should not report an attachment when the dialog is cancelled', async () => {
    const user = userEvent.setup()
    const onAttached = jest.fn()
    render(<TestWrapper><AttachToFindingsButton attackResultId="attack-1" onAttached={onAttached} /></TestWrapper>)

    await user.click(screen.getByRole('button', { name: 'Attach to findings' }))
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))

    await waitFor(() => { expect(screen.queryByRole('dialog')).not.toBeInTheDocument() })
    expect(onAttached).not.toHaveBeenCalled()
  })

  it('should explain why a running attack cannot be attached yet', () => {
    render(<TestWrapper><AttachToFindingsButton attackResultId="attack-1" disabled /></TestWrapper>)
    const button = screen.getByRole('button', { name: 'Attach to findings' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', 'Available after the attack finishes')
  })
})

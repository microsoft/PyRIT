import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'

import ScanStatusBadge from './ScanStatusBadge'

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('ScanStatusBadge', () => {
  it.each([
    ['CREATED', 'Created'],
    ['QUEUED', 'Queued'],
    ['IN_PROGRESS', 'Executing'],
    ['COMPLETED', 'Completed'],
    ['FAILED', 'Failed'],
    ['CANCELLED', 'Cancelled'],
    ['QUEUING', 'Queuing'],
    ['PENDING', 'Pending'],
    ['SUBMITTING', 'Submitting'],
    ['SUBMISSION_FAILED', 'Submission failed'],
  ] as const)('should label %s as %s', (status, label) => {
    render(<TestWrapper><ScanStatusBadge status={status} testId="status" /></TestWrapper>)
    expect(screen.getByTestId('status')).toHaveTextContent(label)
  })

  it('should let a caller reword the label', () => {
    render(<TestWrapper><ScanStatusBadge status="IN_PROGRESS" label="In progress" testId="status" /></TestWrapper>)
    expect(screen.getByTestId('status')).toHaveTextContent('In progress')
  })

  it('should mark terminal states with an icon only when asked', () => {
    const view = render(<TestWrapper><ScanStatusBadge status="COMPLETED" testId="status" /></TestWrapper>)
    expect(screen.getByTestId('status').querySelector('svg')).toBeNull()
    view.rerender(<TestWrapper><ScanStatusBadge status="COMPLETED" withIcon testId="status" /></TestWrapper>)
    expect(screen.getByTestId('status').querySelector('svg')).not.toBeNull()
    view.rerender(<TestWrapper><ScanStatusBadge status="IN_PROGRESS" withIcon testId="status" /></TestWrapper>)
    expect(screen.getByTestId('status').querySelector('svg')).toBeNull()
  })
})

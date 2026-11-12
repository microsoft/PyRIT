import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'

import type { AttackOutcome } from '@/types'

import OutcomeBadge, { OutcomeIcon } from './OutcomeBadge'

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('OutcomeBadge', () => {
  it.each<AttackOutcome>(['success', 'failure', 'error', 'undetermined'])(
    'keeps the %s label and lets its icon inherit the badge foreground',
    (outcome: AttackOutcome) => {
      render(<TestWrapper><OutcomeBadge outcome={outcome} /></TestWrapper>)
      const badge = screen.getByText(outcome, { exact: true })
      expect(badge).toBeVisible()
      expect(badge.querySelector('svg')).not.toHaveAttribute('style')
    },
  )

  it.each([undefined, null])('treats a missing outcome as undetermined', (outcome: null | undefined) => {
    render(<TestWrapper><OutcomeBadge outcome={outcome} /></TestWrapper>)
    expect(screen.getByText('undetermined')).toBeVisible()
  })

  it('preserves custom labels, size, appearance, class names, and test IDs', () => {
    render(
      <TestWrapper>
        <OutcomeBadge outcome="failure" label="No" appearance="tint" size="small" className="custom" testId="verdict" />
      </TestWrapper>,
    )
    expect(screen.getByTestId('verdict')).toHaveTextContent('No')
    expect(screen.getByTestId('verdict')).toHaveClass('custom')
  })

  it.each<[AttackOutcome, 'outcome' | 'success', string]>([
    ['success', 'success', 'True'],
    ['failure', 'success', 'False'],
    ['error', 'success', 'Error'],
    ['success', 'outcome', 'Success'],
    ['undetermined', 'outcome', 'Undetermined'],
  ])('labels %s in %s style as %s', (outcome: AttackOutcome, labelStyle: 'outcome' | 'success', label: string) => {
    render(<TestWrapper><OutcomeBadge outcome={outcome} labelStyle={labelStyle} testId="verdict" /></TestWrapper>)
    expect(screen.getByTestId('verdict')).toHaveTextContent(label)
  })
})

describe('OutcomeIcon', () => {
  it.each<AttackOutcome>(['success', 'failure', 'error', 'undetermined'])(
    'provides a non-color label for the %s icon',
    (outcome: AttackOutcome) => {
      render(<TestWrapper><OutcomeIcon outcome={outcome} /></TestWrapper>)
      expect(screen.getByRole('img', { name: outcome })).toBeVisible()
    },
  )
})

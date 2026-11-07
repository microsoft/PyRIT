import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { AttackDisplayOutcome, AttackOutcome } from '@/types'

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

  it.each<[AttackDisplayOutcome, 'outcome' | 'success', string]>([
    ['success', 'success', 'True'],
    ['failure', 'success', 'False'],
    ['error', 'success', 'Error'],
    ['success', 'outcome', 'Success'],
    ['undetermined', 'outcome', 'Undetermined'],
    ['in_progress', 'success', 'In progress'],
    ['preparing', 'outcome', 'Preparing'],
  ])('labels %s in %s style as %s', (outcome: AttackDisplayOutcome, labelStyle: 'outcome' | 'success', label: string) => {
    render(<TestWrapper><OutcomeBadge outcome={outcome} labelStyle={labelStyle} testId="verdict" /></TestWrapper>)
    expect(screen.getByTestId('verdict')).toHaveTextContent(label)
  })

  it('stays a plain badge without a score', () => {
    render(<TestWrapper><OutcomeBadge outcome="success" /></TestWrapper>)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('opens the score behind the outcome from the badge', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <OutcomeBadge outcome="success" labelStyle="success" scoreButtonLabel="View score for First objective" score={{
        scorer: 'objective', scoreType: 'true_false', value: 'true', rationale: 'The objective was met.', isObjective: true,
      }} />
    </TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'True: View score for First objective' }))
    expect(await screen.findByText('Score details')).toBeInTheDocument()
    expect(screen.getByText('The objective was met.')).toBeInTheDocument()
  })

  it('explains a missing score in the popover', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <OutcomeBadge outcome="error" score={null} scoreFallback="The attack ended before a score was saved." />
    </TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'error: View score' }))
    expect(await screen.findByText('The attack ended before a score was saved.')).toBeInTheDocument()
  })

  it.each<[AttackDisplayOutcome | null | undefined, string]>([
    ['success', 'Success'],
    ['failure', 'Failure'],
    ['error', 'Error'],
    ['undetermined', 'Undetermined'],
    ['in_progress', 'In progress'],
    ['preparing', 'Preparing'],
    [null, 'Undetermined'],
    [undefined, 'Undetermined'],
  ])('keeps the displayed %s outcome in the score action name', (
    outcome: AttackDisplayOutcome | null | undefined, label: string,
  ) => {
    render(<TestWrapper>
      <OutcomeBadge outcome={outcome} labelStyle="outcome" scoreFallback="No score saved." />
    </TestWrapper>)
    expect(screen.getByRole('button', { name: `${label}: View score`, exact: true })).toHaveTextContent(label)
  })

  it('keeps a custom displayed outcome in the score action name', () => {
    render(<TestWrapper>
      <OutcomeBadge outcome="failure" label="Not achieved" scoreFallback="No score saved." />
    </TestWrapper>)
    expect(screen.getByRole('button', { name: 'Not achieved: View score', exact: true })).toHaveTextContent('Not achieved')
  })
})

describe('OutcomeIcon', () => {
  it('names a pending outcome in words', () => {
    render(<TestWrapper><OutcomeIcon outcome="in_progress" /></TestWrapper>)
    expect(screen.getByRole('img', { name: 'in progress' })).toBeVisible()
  })

  it.each<AttackOutcome>(['success', 'failure', 'error', 'undetermined'])(
    'provides a non-color label for the %s icon',
    (outcome: AttackOutcome) => {
      render(<TestWrapper><OutcomeIcon outcome={outcome} /></TestWrapper>)
      expect(screen.getByRole('img', { name: outcome })).toBeVisible()
    },
  )
})

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { ScorerEvaluationMetrics } from '@/types'

import ScorerEvaluationDetails from './ScorerEvaluationDetails'

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('ScorerEvaluationDetails', () => {
  it('should disclose complete binary metrics and extra keys by keyboard', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ScorerEvaluationDetails name="configured judge" scoreType="true_false"
      metrics={{ accuracy: 0.93, accuracy_standard_error: 0.02, recall: 0, precision: 0.9, f1_score: 0.8,
        num_responses: 100, num_human_raters: 0, custom_value: 'retained', average_score_time_seconds: 0 }} /></TestWrapper>)
    expect(screen.queryByRole('group', { name: 'Num responses' })).not.toBeInTheDocument()
    const trigger = screen.getByRole('button', { name: 'Evaluation metrics for configured judge' })
    trigger.focus()
    await user.keyboard('{Enter}')
    const dialog = screen.getByRole('dialog', { name: 'Scorer evaluation metrics' })
    expect(within(dialog).getByRole('group', { name: 'Accuracy' })).toHaveTextContent('93.00%')
    expect(within(dialog).getByRole('group', { name: 'Recall' })).toHaveTextContent('0.00%')
    expect(within(dialog).getByRole('group', { name: 'Accuracy std error' })).toHaveTextContent('0.0200')
    expect(within(dialog).getByRole('group', { name: 'Num responses' })).toHaveTextContent('100')
    expect(within(dialog).getByRole('group', { name: 'Num human raters' })).toHaveTextContent('0')
    expect(within(dialog).getByRole('group', { name: 'Custom value' })).toHaveTextContent('retained')
    expect(within(dialog).getByRole('group', { name: 'Average score time' })).toHaveTextContent('Unavailable')
    expect(dialog).toHaveTextContent(
      'Quality of the recorded scorer configuration, not an individual attack score or the scan success rate.',
    )
    expect(within(dialog).getByRole('link', { name: 'About scorer metrics' })).toHaveAttribute(
      'href', 'https://microsoft.github.io/PyRIT/latest/code/scoring/scorer-metrics/',
    )
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('should show zero float-scale errors, agreement, extra sample counts and missing supplied values', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ScorerEvaluationDetails name="scale" scoreType="float_scale" metrics={{
      mean_absolute_error: 0, mae_standard_error: 0.01, krippendorff_alpha_combined: 0.8,
      num_responses: 25, custom_standard_error: null,
    }} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Evaluation metrics for scale' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('group', { name: 'Mean absolute error' })).toHaveTextContent('0.0000')
    expect(within(dialog).getByRole('group', { name: 'Agreement (Krippendorff alpha)' })).toHaveTextContent('0.8000')
    expect(within(dialog).getByRole('group', { name: 'Num responses' })).toHaveTextContent('25')
    expect(within(dialog).getByRole('group', { name: 'Custom standard error' })).toHaveTextContent('Unavailable')
    expect(within(dialog).queryByRole('group', { name: 'Accuracy' })).not.toBeInTheDocument()
  })

  it.each<ScorerEvaluationMetrics | null | undefined>([null, undefined, {}, { accuracy: null }])(
    'should explain unavailable official evaluations for %p', async (metrics: ScorerEvaluationMetrics | null | undefined) => {
      const user = userEvent.setup()
      render(<TestWrapper><ScorerEvaluationDetails name="unevaluated" metrics={metrics} /></TestWrapper>)
      await user.click(screen.getByRole('button', { name: 'Evaluation metrics for unevaluated' }))
      expect(screen.getByRole('dialog')).toHaveTextContent('No official evaluation metrics are available')
      expect(screen.getByRole('link', { name: 'About scorer metrics' })).toBeVisible()
      expect(screen.queryByRole('group', { name: 'Accuracy' })).not.toBeInTheDocument()
    },
  )
})

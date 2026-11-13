import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'

import type { ScenarioObjectiveScorerMetrics } from '@/types'

import ScorerMetricsDetails from './ScorerMetricsDetails'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const PERCENT_METRIC_LABELS = ['Accuracy', 'Precision', 'Recall']

function renderScorer(metrics: ScenarioObjectiveScorerMetrics | null): void {
  render(<TestWrapper><ScorerMetricsDetails metrics={metrics && { ...metrics }} fallback="Unavailable" /></TestWrapper>)
}

function expectMetric(label: string, value: string): void {
  expect(within(screen.getByRole('group', { name: label })).getByText(value)).toBeInTheDocument()
}

describe('ScorerMetricsDetails', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should show accuracy, precision, and recall as percentages and keep F1 on its 0-to-1 scale', () => {
    renderScorer({
      accuracy: 0.94, f1_score: 0.91, precision: 0.95, recall: 0.88,
      accuracy_standard_error: 0.01, average_score_time_seconds: 0.25,
    })
    expectMetric('Accuracy', '94.00%')
    expectMetric('F1 score', '0.9100')
    expectMetric('Precision', '95.00%')
    expectMetric('Recall', '88.00%')
    expectMetric('Accuracy std error', '0.0100')
    expectMetric('Average score time', '0.25s')
  })

  it.each([
    { value: 0, percent: '0.00%', score: '0.0000' },
    { value: 1, percent: '100.00%', score: '1.0000' },
    { value: 0.95678, percent: '95.68%', score: '0.9568' },
  ])('should show $value as $percent, and an F1 of $value as $score', (
    { value, percent, score }: { value: number, percent: string, score: string },
  ) => {
    renderScorer({ accuracy: value, f1_score: value, precision: value, recall: value })
    for (const label of PERCENT_METRIC_LABELS) expectMetric(label, percent)
    expectMetric('F1 score', score)
  })

  it('should keep the unavailable fallback for missing metrics', () => {
    renderScorer({ accuracy: 0.94, f1_score: null })
    expectMetric('Accuracy', '94.00%')
    expectMetric('F1 score', 'Unavailable')
    expectMetric('Precision', 'Unavailable')
    expectMetric('Recall', 'Unavailable')
  })
})

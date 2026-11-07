import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'

import type { ScenarioObjectiveScorer, ScenarioObjectiveScorerMetrics } from '@/types'

import ObjectiveScorerDetails from './ObjectiveScorerDetails'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const RATIO_METRIC_LABELS = ['Accuracy', 'F1 score', 'Precision', 'Recall']

function renderScorer(metrics: ScenarioObjectiveScorerMetrics | null): void {
  const scorer: ScenarioObjectiveScorer = {
    component_name: 'SelfAskTrueFalseScorer', parameters: {}, children: {}, metrics,
  }
  render(<TestWrapper><ObjectiveScorerDetails scorer={scorer} /></TestWrapper>)
}

function expectMetric(label: string, value: string): void {
  expect(within(screen.getByRole('group', { name: label })).getByText(value)).toBeInTheDocument()
}

describe('ObjectiveScorerDetails', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should show every ratio metric as a percentage and keep other metrics in their units', () => {
    renderScorer({
      accuracy: 0.94, f1_score: 0.91, precision: 0.95, recall: 0.88,
      accuracy_standard_error: 0.01, average_score_time_seconds: 0.25,
    })
    expectMetric('Accuracy', '94.00%')
    expectMetric('F1 score', '91.00%')
    expectMetric('Precision', '95.00%')
    expectMetric('Recall', '88.00%')
    expectMetric('Accuracy std error', '0.0100')
    expectMetric('Average score time', '0.25s')
  })

  it.each([
    { value: 0, display: '0.00%' },
    { value: 1, display: '100.00%' },
    { value: 0.95678, display: '95.68%' },
  ])('should show ratio metric $value as $display', ({ value, display }: { value: number, display: string }) => {
    renderScorer({ accuracy: value, f1_score: value, precision: value, recall: value })
    for (const label of RATIO_METRIC_LABELS) expectMetric(label, display)
  })

  it('should keep the unavailable fallback for missing ratio metrics', () => {
    renderScorer({ accuracy: 0.94, f1_score: null })
    expectMetric('Accuracy', '94.00%')
    expectMetric('F1 score', 'Unavailable')
    expectMetric('Precision', 'Unavailable')
    expectMetric('Recall', 'Unavailable')
  })

  it('should explain when no official evaluation exists', () => {
    renderScorer(null)
    expect(screen.getByText('Official evaluation has not been run for this scorer configuration.')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Accuracy' })).not.toBeInTheDocument()
  })
})

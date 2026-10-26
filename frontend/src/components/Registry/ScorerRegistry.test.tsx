import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { UserPreferencesProvider } from '@/hooks/useUserPreferences'
import { readUserPreferences } from '@/utils/userPreferences'
import type { ScorerInstance } from '@/types'

import ScorerRegistry from './ScorerRegistry'

const scorers: ScorerInstance[] = [
  { scorer_registry_name: 'judge', identifier: { class_name: 'SubStringScorer', class_module: 'pyrit.score', hash: 'judge-hash', scorer_type: 'true_false' } },
  { scorer_registry_name: 'scale', identifier: { class_name: 'ScaleScorer', class_module: 'pyrit.score', hash: 'scale-hash', scorer_type: 'float_scale' },
    chat_scoring_disabled_reason: 'Chat requires a true/false response scorer.' },
  { scorer_registry_name: 'criteria', identifier: { class_name: 'MessageToolCallScorer', hash: 'criteria-hash', scorer_type: 'true_false' },
    chat_scoring_disabled_reason: 'Requires tool criteria.' },
]

describe('ScorerRegistry', () => {
  beforeEach(() => { window.localStorage.clear() })

  it('browses existing scorers and saves a personal default without a create action', async () => {
    const user = userEvent.setup()
    const onRefresh = jest.fn()
    const rendered = render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="alice">
      <ScorerRegistry scorers={scorers} loading={false} error={null} onRefresh={onRefresh} />
    </UserPreferencesProvider></FluentProvider>)
    const binary = screen.getByRole('table', { name: 'True/false scorers' })
    const scale = screen.getByRole('table', { name: 'Float-scale scorers' })
    expect(within(binary).getByText('judge')).toBeVisible()
    expect(within(binary).queryByText('scale')).not.toBeInTheDocument()
    expect(within(scale).getByText('scale')).toBeVisible()
    expect(screen.queryByRole('button', { name: /add|new|create/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /scale/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /criteria/ })).not.toBeInTheDocument()
    expect(screen.getByText('ScaleScorer')).toBeVisible()
    expect(screen.queryByText(/personal to this account and browser/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)
    const selector = screen.getByRole('combobox', { name: 'Default objective scorer for chats' })
    for (const option of within(selector).getAllByRole('option')) expect(option).toBeEnabled()
    expect(within(selector).getByRole('option', { name: 'judge - SubStringScorer' })).toBeInTheDocument()
    await user.selectOptions(selector, 'registered:judge')
    expect(within(selector).getByRole('option', { selected: true })).toHaveTextContent('judge - SubStringScorer')
    expect(readUserPreferences('alice').chatScorer).toEqual({ registry_name: 'judge', identifier_hash: 'judge-hash' })
    expect(readUserPreferences('bob').chatScorer).toBeUndefined()
    rendered.rerender(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="alice">
      <ScorerRegistry scorers={[{ ...scorers[0], identifier: { ...scorers[0].identifier, hash: 'changed' } }]}
        loading={false} error={null} onRefresh={onRefresh} />
    </UserPreferencesProvider></FluentProvider>)
    expect(screen.getByRole('alert')).toHaveTextContent('configuration changed')
    expect(selector).toHaveValue('unavailable')
    expect(screen.queryByRole('option', { name: 'Unavailable: judge' })).not.toBeInTheDocument()
    await user.selectOptions(selector, '')
    expect(readUserPreferences('alice').chatScorer).toBeNull()
  })

  it('shows nested types, evaluation-only details, primary metrics, and average time', async () => {
    const user = userEvent.setup()
    const evaluated: ScorerInstance = {
      scorer_registry_name: 'wrapped-judge',
      identifier: { class_name: 'Threshold', class_module: 'pyrit.score', hash: 'runtime-hash',
        scorer_type: 'true_false', endpoint: 'https://private-deployment.invalid' },
      display_type: 'FloatScaleThresholdScorer(AzureContentFilterScorer)',
      scorable_types: ['content', 'content_entry', 'message'],
      evaluation_identifier: {
        class_name: 'Threshold', class_module: 'pyrit.score', hash: 'projected-hash', eval_hash: 'evaluation-hash',
        threshold: 0.5, children: { sub_scorers: [{
          class_name: 'AzureContentFilterScorer', class_module: 'pyrit.score', hash: 'child-hash', harm_category: 'hate',
        }] },
      },
      metrics: { precision: 0.95678, recall: 0.0, accuracy: 0.94, f1_score: 0.91, num_responses: 100, average_score_time_seconds: 0.25 },
    }
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="alice">
      <ScorerRegistry scorers={[evaluated]} loading={false} error={null} onRefresh={jest.fn()} />
    </UserPreferencesProvider></FluentProvider>)
    expect(screen.getByRole('separator', { name: 'Scorer registry' })).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader').map((cell: HTMLElement) => cell.textContent)).toEqual([
      'Name', 'Type', 'Scorable', 'Precision', 'Recall', 'Accuracy', 'F1', 'Avg time',
    ])
    expect(screen.getByText('FloatScaleThresholdScorer(AzureContentFilterScorer)')).toBeVisible()
    expect(screen.getByRole('option', { name: 'wrapped-judge - FloatScaleThresholdScorer(AzureContentFilterScorer)' })).toBeInTheDocument()
    expect(screen.getByText('Message')).toBeVisible()
    expect(screen.queryByText('Message * true_false')).not.toBeInTheDocument()
    const cells = within(screen.getAllByRole('row')[1]).getAllByRole('cell')
    expect(cells.slice(2).map((cell: HTMLElement) => cell.textContent)).toEqual([
      'Message', '95.7%', '0.0%', '94.0%', '91.0%', '0.25s',
    ])
    await user.hover(within(cells[3]).getByText('95.7%'))
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Recorded value: 0.95678')
    expect(screen.queryByText('More metrics')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Num responses' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Evaluation details for wrapped-judge' }))
    const details = screen.getByRole('region', { name: 'Evaluation details for wrapped-judge' })
    expect(JSON.parse(details.textContent ?? '')).toEqual(evaluated.evaluation_identifier)
    expect(details.textContent).toMatch(/\n\s+"class_name":/)
    expect(screen.queryByText('https://private-deployment.invalid')).not.toBeInTheDocument()
    expect(screen.queryByText('runtime-hash')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Evaluation details for wrapped-judge' }))
    expect(screen.queryByRole('region', { name: 'Evaluation details for wrapped-judge' })).not.toBeInTheDocument()
  })

  it('uses harm metrics, distinguishes tool evidence, and reports unavailable values as n/a', () => {
    const entries: ScorerInstance[] = [{
      scorer_registry_name: 'harm', identifier: { class_name: 'HarmScorer', class_module: 'pyrit.score', hash: 'harm', scorer_type: 'float_scale' },
      scorable_types: ['message'], metrics: { mean_absolute_error: 0.12, krippendorff_alpha_combined: 0.8, average_score_time_seconds: 0 },
    }, {
      scorer_registry_name: 'tools', identifier: { class_name: 'OtelToolCallScorer', class_module: 'pyrit.score', hash: 'tools', scorer_type: 'true_false' },
      scorable_types: ['message', 'trace'], condition_types: ['ToolsCalled'],
    }, {
      scorer_registry_name: 'custom', identifier: { class_name: 'CustomScorer', class_module: 'tests', hash: 'custom', scorer_type: 'unknown' },
    }]
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="alice">
      <ScorerRegistry scorers={entries} loading={false} error={null} onRefresh={jest.fn()} />
    </UserPreferencesProvider></FluentProvider>)
    const scale = screen.getByRole('table', { name: 'Float-scale scorers' })
    expect(within(scale).getAllByRole('columnheader').map((cell: HTMLElement) => cell.textContent)).toEqual([
      'Name', 'Type', 'Scorable', 'MAE', 'Agreement', 'Avg time',
    ])
    const scaleCells = within(within(scale).getAllByRole('row')[1]).getAllByRole('cell')
    expect(scaleCells.slice(2).map((cell: HTMLElement) => cell.textContent)).toEqual(['Message', '0.1200', '0.8000', 'n/a'])
    const binary = screen.getByRole('table', { name: 'True/false scorers' })
    expect(within(binary).getByText('Message / Trace (Tool calls)')).toBeVisible()
    const toolCells = within(within(binary).getAllByRole('row')[1]).getAllByRole('cell')
    expect(toolCells.slice(3).map((cell: HTMLElement) => cell.textContent)).toEqual(['n/a', 'n/a', 'n/a', 'n/a', 'n/a'])
    expect(within(screen.getByRole('table', { name: 'Other scorers' })).getByText('custom')).toBeVisible()
  })

  it('sorts full-precision metrics locally, keeps missing values last, and supports keyboard toggles', async () => {
    const user = userEvent.setup()
    const entries: ScorerInstance[] = [
      { ...scorers[0], scorer_registry_name: 'c_slow',
        metrics: { precision: 0.7, recall: 0.8, accuracy: 0.6, f1_score: 0.85, average_score_time_seconds: 0.3 } },
      { ...scorers[0], scorer_registry_name: 'a_missing', metrics: { average_score_time_seconds: 0 } },
      { ...scorers[0], scorer_registry_name: 'd_tied',
        metrics: { precision: 0.90002, recall: 0.5, accuracy: 0.9, f1_score: 0.5, average_score_time_seconds: 0.2 } },
      { ...scorers[0], scorer_registry_name: 'b_fast',
        metrics: { precision: 0.90001, recall: 0.2, accuracy: 0.9, f1_score: 0.3, average_score_time_seconds: 0.1 } },
      { ...scorers[1], scorer_registry_name: 'a_high_mae', metrics: { mean_absolute_error: 0.2, krippendorff_alpha_combined: 0.3 } },
      { ...scorers[1], scorer_registry_name: 'z_low_mae', metrics: { mean_absolute_error: 0.05, krippendorff_alpha_combined: 0.9 } },
    ]
    const originalOrder = entries.map((entry: ScorerInstance) => entry.scorer_registry_name)
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="alice">
      <ScorerRegistry scorers={entries} loading={false} error={null} onRefresh={jest.fn()} />
    </UserPreferencesProvider></FluentProvider>)
    const binary = screen.getByRole('table', { name: 'True/false scorers' })
    const scale = screen.getByRole('table', { name: 'Float-scale scorers' })
    const names = (table: HTMLElement): string[] => within(table)
      .getAllByRole('button', { name: /^Evaluation details for / })
      .map((button: HTMLElement) => button.textContent ?? '')
    expect(names(binary)).toEqual(['a_missing', 'b_fast', 'c_slow', 'd_tied'])
    const precision = within(binary).getByRole('button', { name: 'Precision', exact: true })
    await user.click(precision)
    expect(names(binary)).toEqual(['d_tied', 'b_fast', 'c_slow', 'a_missing'])
    expect(within(binary).getByRole('columnheader', { name: 'Precision', exact: true })).toHaveAttribute('aria-sort', 'descending')
    precision.focus()
    await user.keyboard('{Enter}')
    expect(names(binary)).toEqual(['c_slow', 'b_fast', 'd_tied', 'a_missing'])
    expect(within(binary).getByRole('columnheader', { name: 'Precision', exact: true })).toHaveAttribute('aria-sort', 'ascending')
    await user.click(within(binary).getByRole('button', { name: 'Recall', exact: true }))
    expect(names(binary)).toEqual(['c_slow', 'd_tied', 'b_fast', 'a_missing'])
    await user.click(within(binary).getByRole('button', { name: 'Accuracy', exact: true }))
    expect(names(binary)).toEqual(['b_fast', 'd_tied', 'c_slow', 'a_missing'])
    await user.click(within(binary).getByRole('button', { name: 'F1', exact: true }))
    expect(names(binary)).toEqual(['c_slow', 'd_tied', 'b_fast', 'a_missing'])
    await user.click(within(binary).getByRole('button', { name: 'Avg time', exact: true }))
    expect(names(binary)).toEqual(['b_fast', 'd_tied', 'c_slow', 'a_missing'])
    await user.click(within(binary).getByRole('button', { name: 'Avg time', exact: true }))
    expect(names(binary)).toEqual(['c_slow', 'd_tied', 'b_fast', 'a_missing'])
    await user.click(within(binary).getByRole('button', { name: 'Name', exact: true }))
    expect(names(binary)).toEqual(['a_missing', 'b_fast', 'c_slow', 'd_tied'])
    await user.click(within(binary).getByRole('button', { name: 'Name', exact: true }))
    expect(names(binary)).toEqual(['d_tied', 'c_slow', 'b_fast', 'a_missing'])
    await user.click(within(scale).getByRole('button', { name: 'MAE', exact: true }))
    expect(names(scale)).toEqual(['z_low_mae', 'a_high_mae'])
    await user.click(within(scale).getByRole('button', { name: 'Agreement', exact: true }))
    expect(names(scale)).toEqual(['z_low_mae', 'a_high_mae'])
    await user.click(within(scale).getByRole('button', { name: 'Agreement', exact: true }))
    expect(names(scale)).toEqual(['a_high_mae', 'z_low_mae'])
    expect(names(binary)).toEqual(['d_tied', 'c_slow', 'b_fast', 'a_missing'])
    expect(entries.map((entry: ScorerInstance) => entry.scorer_registry_name)).toEqual(originalOrder)
  })
})

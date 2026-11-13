import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

import { attacksApi } from '@/services/api'
import type { ScenarioObjectiveScorer, ScenarioProgressResult } from '@/types'

import AttackAttemptDetails from './AttackAttemptDetails'

jest.mock('@/services/api', () => ({ attacksApi: { getAttackFindings: jest.fn() } }))

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}><MemoryRouter>{children}</MemoryRouter></FluentProvider>
}

const attempt: ScenarioProgressResult = {
  attack_result_id: 'saved-attack', conversation_id: 'saved-conversation', atomic_group_id: 'group',
  atomic_attack_name: 'PromptSendingAttack', seed_group_id: 'seed', outcome: 'success',
  execution_time_ms: 0, total_retries: 0, timestamp: '2026-01-01T00:00:00Z', retries: [],
  score: { scorer_name: 'recorded judge', score_type: 'true_false', status: 'complete',
    score_value: 'true', score_rationale: 'The objective was met.' },
}
const recordedScorer: ScenarioObjectiveScorer = {
  component_name: 'old registry alias', parameters: { scorer_type: 'true_false', eval_hash: 'recorded-configuration' },
  children: {}, metrics: { accuracy: 0, recall: 0 },
}

describe('AttackAttemptDetails', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(attacksApi.getAttackFindings).mockResolvedValue({ attack_result_id: attempt.attack_result_id, items: [] })
  })

  it('should retain readable recorded metrics without registry lookup and keep individual attack score details', async () => {
    const user = userEvent.setup()
    const rendered = render(<TestWrapper><AttackAttemptDetails attempt={attempt} objective="Recorded objective"
      objectiveScorer={recordedScorer} conversationPath="/saved" /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Evaluation metrics for old registry alias' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Quality of the recorded scorer configuration')
    expect(within(dialog).getByRole('group', { name: 'Accuracy' })).toHaveTextContent('0.00%')
    expect(within(dialog).getByRole('group', { name: 'Recall' })).toHaveTextContent('0.00%')
    await user.keyboard('{Escape}')
    rendered.rerender(<TestWrapper><AttackAttemptDetails attempt={attempt} objective="Recorded objective"
      objectiveScorer={{ ...recordedScorer, component_name: 'renamed or unregistered judge' }} conversationPath="/saved" /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Evaluation metrics for renamed or unregistered judge' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('0.00%')
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'True: View attack score' }))
    expect(screen.getByText('Score details')).toBeVisible()
    expect(screen.getByText('Final score')).toBeVisible()
    expect(screen.getByText('recorded judge')).toBeVisible()
  })

  it('should distinguish failed findings reads from empty links and recover with a keyboard retry', async () => {
    const user = userEvent.setup()
    jest.mocked(attacksApi.getAttackFindings).mockRejectedValueOnce({
      isAxiosError: true, response: { status: 503, data: { detail: 'Findings unavailable' } },
    }).mockResolvedValueOnce({
      attack_result_id: attempt.attack_result_id, items: [{
        id: 'finding', operation_id: 'operation', operation_name: 'Recorded operation', evidence_id: 'evidence',
        title: 'Recovered attached finding', description: 'Stored evidence', severity: 'low', created_at: attempt.timestamp,
      }],
    })
    render(<TestWrapper><AttackAttemptDetails attempt={attempt} conversationPath="/saved" /></TestWrapper>)
    expect(await screen.findByText(/Could not load attached findings: Findings unavailable/)).toBeVisible()
    expect(screen.queryByRole('list', { name: 'Attached findings' })).not.toBeInTheDocument()
    const retry = screen.getByRole('button', { name: 'Retry findings' })
    retry.focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('list', { name: 'Attached findings' })).toHaveTextContent('Recovered attached finding')
    expect(attacksApi.getAttackFindings).toHaveBeenCalledTimes(2)
    expect(screen.queryByText(/Could not load attached findings/)).not.toBeInTheDocument()
  })
})

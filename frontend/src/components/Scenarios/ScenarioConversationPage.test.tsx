import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { useConversationMessages } from '@/hooks/useConversationMessages'
import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { scenariosApi } from '@/services/api'
import type { ScenarioRunSummary } from '@/types'
import { INITIAL_SCENARIO_RUN_PROGRESS_STATE } from '@/utils/scenarioRunProgress'

import ScenarioConversationPage from './ScenarioConversationPage'

jest.mock('@/hooks/useScenarioRunProgress', () => ({ useScenarioRunProgress: jest.fn(), SCENARIO_RUN_POLL_INTERVAL_MS: 2500 }))
jest.mock('@/hooks/useConversationMessages', () => ({ useConversationMessages: jest.fn() }))
jest.mock('@/services/api', () => ({ scenariosApi: { cancelRun: jest.fn(), getRunConversation: jest.fn() } }))

const RUN_ID = '123e4567-e89b-12d3-a456-426614174000'
const applyRunSummary = jest.fn()
const loadConversation = jest.fn()
const active = {
  attack_result_id: 'attempt', conversation_id: 'conversation', atomic_attack_name: 'PromptSendingAttack',
  objective: 'Review the response', started_at: '2026-01-01T00:00:00Z', seed_identifiers: [],
}
const CANCELLED: ScenarioRunSummary = {
  scenario_result_id: RUN_ID, scenario_name: 'review.scan', scenario_version: 1, status: 'CANCELLED',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', techniques_used: [],
  total_attacks: 1, completed_attacks: 0, objective_achieved_rate: 0, failed_attacks: [],
  attack_retries: [], total_retries: 0, labels: {},
}

function LocationTrace() {
  const location = useLocation()
  return <output aria-label="Location">{location.pathname}{location.search}</output>
}

function Wrapper() {
  return <FluentProvider theme={webLightTheme}><MemoryRouter initialEntries={[`/scanner/history/${RUN_ID}/conversations/conversation`]}>
    <LocationTrace /><Routes>
      <Route path="/scanner/history/:scenarioResultId/conversations/:conversationId" element={<ScenarioConversationPage />} />
      <Route path="*" element={<h1>Completed conversation</h1>} />
    </Routes>
  </MemoryRouter></FluentProvider>
}

beforeEach(() => {
  jest.clearAllMocks()
  ;(useScenarioRunProgress as jest.Mock).mockReturnValue({
    state: { ...INITIAL_SCENARIO_RUN_PROGRESS_STATE, loadStatus: 'ready',
      run: { scenario_result_id: RUN_ID, scenario_name: 'review.scan', status: 'IN_PROGRESS' },
      activeConversations: [active] },
    retry: jest.fn(), applyRunSummary,
  })
  ;(useConversationMessages as jest.Mock).mockReturnValue({
    messages: [{ role: 'user', content: 'A saved prompt', timestamp: '' }],
    loadedConversationId: 'conversation', conversationLoadIssue: null, loadConversation,
  })
})

it('shows persisted scan messages read-only and uses the scoped conversation API', async () => {
  jest.mocked(scenariosApi.getRunConversation).mockResolvedValue({ conversation_id: 'conversation', messages: [], target_response_status: null })
  render(<Wrapper />)
  expect(screen.getByText('A saved prompt')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cancel scan' })).toBeInTheDocument()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  const options = (useConversationMessages as jest.Mock).mock.calls[0][0]
  await options.readConversation('attempt', 'conversation')
  expect(scenariosApi.getRunConversation).toHaveBeenCalledWith(RUN_ID, 'conversation')
  expect(options.pollIntervalMs).toBe(2500)
})

it('never offers scan cancellation for an unrelated or unverified conversation', () => {
  ;(useScenarioRunProgress as jest.Mock).mockReturnValue({
    state: { ...INITIAL_SCENARIO_RUN_PROGRESS_STATE, loadStatus: 'ready',
      run: { scenario_result_id: RUN_ID, status: 'IN_PROGRESS' } },
    retry: jest.fn(), applyRunSummary,
  })
  render(<Wrapper />)
  expect(screen.queryByRole('button', { name: 'Cancel scan' })).not.toBeInTheDocument()
  expect(screen.getByText('This conversation is not part of this scan.')).toBeInTheDocument()
})

it('cancels the owning scan only after explicit confirmation', async () => {
  const user = userEvent.setup()
  jest.mocked(scenariosApi.cancelRun).mockResolvedValue(CANCELLED)
  render(<Wrapper />)
  await user.click(screen.getByRole('button', { name: 'Cancel scan' }))
  expect(scenariosApi.cancelRun).not.toHaveBeenCalled()
  await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel scan' }))
  await waitFor(() => expect(scenariosApi.cancelRun).toHaveBeenCalledWith(RUN_ID))
  expect(applyRunSummary).toHaveBeenCalled()
})

it('returns completed conversations to the existing per-conversation scoring view', async () => {
  ;(useScenarioRunProgress as jest.Mock).mockReturnValue({
    state: { ...INITIAL_SCENARIO_RUN_PROGRESS_STATE, loadStatus: 'ready',
      run: { scenario_result_id: RUN_ID, status: 'COMPLETED' },
      results: [{ ...active, outcome: 'success' }] },
    retry: jest.fn(), applyRunSummary,
  })
  render(<Wrapper />)
  await waitFor(() => expect(screen.getByLabelText('Location')).toHaveTextContent(
    `/attack/chat/attempt/conversations/conversation?scenarioResultId=${RUN_ID}`,
  ))
  expect(screen.queryByRole('button', { name: 'Cancel scan' })).not.toBeInTheDocument()
})

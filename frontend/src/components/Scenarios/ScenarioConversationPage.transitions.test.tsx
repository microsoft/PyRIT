import { act } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { scenariosApi } from '@/services/api'
import type { ConversationMessagesResponse, ScenarioRunProgress, ScenarioRunSummary } from '@/types'

import ScenarioConversationPage from './ScenarioConversationPage'

jest.mock('@/services/api', () => ({
  scenariosApi: { getRunProgress: jest.fn(), getRunConversation: jest.fn(), cancelRun: jest.fn() },
  attacksApi: { getMessages: jest.fn() },
}))

const RUN_ID = '123e4567-e89b-12d3-a456-426614174000'
const LIVE_PATH = `/scanner/history/${RUN_ID}/conversations/conversation`
const active = {
  attack_result_id: 'attempt', conversation_id: 'conversation', atomic_attack_name: 'PromptSendingAttack',
  objective: 'Review the response', started_at: '2026-01-01T00:00:00Z', seed_group_id: 'seed', seed_identifiers: [],
}
const transcript: ConversationMessagesResponse = {
  conversation_id: 'conversation', target_response_status: null,
  messages: [{
    turn_number: 0, role: 'user', created_at: '2026-01-01T00:00:00Z',
    message_pieces: [{
      id: 'piece', original_value_data_type: 'text', converted_value_data_type: 'text',
      converted_value: 'A saved prompt', response_error: 'none', scores: [],
    }],
  }],
}

function progress(status: 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED'): ScenarioRunProgress {
  const completed = status === 'COMPLETED'
  return {
    run: {
      scenario_result_id: RUN_ID, scenario_name: 'review.scan', scenario_version: 1, status,
      created_at: '2026-01-01T00:00:00Z',
    },
    summary: {
      overall: { completed: completed ? 1 : 0, planned: 1, succeeded: completed ? 1 : 0,
        success_percentage: null, errors: 0, retries: 0 },
      techniques: [], seed_groups: [], atomic_groups: [],
    },
    plan: { version: 1, atomic_groups: [], seed_groups: [{
      id: 'seed', objective_sha256: 'hash', objective: active.objective, prompts: [],
    }] },
    results: completed ? [{
      ...active, atomic_group_id: 'group', seed_group_id: 'seed', outcome: 'success',
      execution_time_ms: 1000, timestamp: '2026-01-01T00:00:01Z', total_retries: 0, retries: [],
    }] : [],
    active_conversations: status === 'IN_PROGRESS' ? [active] : [],
    recorded_conversations: status === 'CANCELLED' ? [active] : [],
    next_cursor: completed ? 'finished' : null, has_more: false, plan_complete: true,
  }
}

function LocationTrace() {
  const location = useLocation()
  return <output aria-label="Location">{location.pathname}{location.search}</output>
}

function Wrapper() {
  return <FluentProvider theme={webLightTheme}><MemoryRouter initialEntries={[LIVE_PATH]}>
    <LocationTrace /><Routes>
      <Route path="/scanner/history/:scenarioResultId/conversations/:conversationId" element={<ScenarioConversationPage />} />
      <Route path="*" element={<h1>Completed conversation</h1>} />
    </Routes>
  </MemoryRouter></FluentProvider>
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.useFakeTimers()
  jest.mocked(scenariosApi.getRunProgress).mockResolvedValue(progress('IN_PROGRESS'))
  jest.mocked(scenariosApi.getRunConversation).mockResolvedValue(transcript)
})
afterEach(() => { jest.useRealTimers() })

it('retains the transcript and stopped state after Cancel and reload without adding an outcome', async () => {
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
  jest.mocked(scenariosApi.cancelRun).mockImplementation(async () => {
    jest.mocked(scenariosApi.getRunProgress).mockResolvedValue(progress('CANCELLED'))
    return {
      ...progress('CANCELLED').run, updated_at: '2026-01-01T00:00:01Z', techniques_used: [],
      total_attacks: 1, completed_attacks: 0, objective_achieved_rate: 0,
      failed_attacks: [], attack_retries: [], total_retries: 0, labels: {},
    } satisfies ScenarioRunSummary
  })
  const view = render(<Wrapper />)
  expect(await screen.findByText('A saved prompt')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Cancel scan' }))
  await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel scan' }))
  expect(await screen.findByText('Stopped before finishing')).toBeInTheDocument()
  expect(screen.getByText(active.objective)).toBeInTheDocument()
  expect(screen.getByText('A saved prompt')).toBeInTheDocument()
  expect(screen.queryByText('This conversation is not part of this scan.')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel scan' })).not.toBeInTheDocument()
  view.unmount()
  render(<Wrapper />)
  expect(await screen.findByText('Stopped before finishing')).toBeInTheDocument()
  expect(await screen.findByText('A saved prompt')).toBeInTheDocument()
  expect(screen.getByLabelText('Location')).toHaveTextContent(LIVE_PATH)
  expect(scenariosApi.getRunConversation).toHaveBeenLastCalledWith(RUN_ID, 'conversation')
})

it('loads an earlier conversation by scoped identity before progress publishes its attack ID', async () => {
  const initialProgress = progress('IN_PROGRESS')
  initialProgress.active_conversations = []
  initialProgress.results = []
  initialProgress.recorded_conversations = []
  jest.mocked(scenariosApi.getRunProgress).mockResolvedValue(initialProgress)
  render(<Wrapper />)
  expect(await screen.findByText('A saved prompt')).toBeInTheDocument()
  expect(scenariosApi.getRunConversation).toHaveBeenCalledWith(RUN_ID, 'conversation')
  expect(screen.queryByText('This conversation is not part of this scan.')).not.toBeInTheDocument()
})

it('moves a live conversation to the canonical completed chat after the progress poll', async () => {
  render(<Wrapper />)
  await screen.findByText('A saved prompt')
  jest.mocked(scenariosApi.getRunProgress).mockResolvedValue(progress('COMPLETED'))
  await act(async () => { await jest.advanceTimersByTimeAsync(2500) })
  expect(await screen.findByRole('heading', { name: 'Completed conversation' })).toBeInTheDocument()
  expect(screen.getByLabelText('Location')).toHaveTextContent(
    `/attack/chat/attempt/conversations/conversation?scenarioResultId=${RUN_ID}`,
  )
})

it('does not expose cancellation when the scoped transcript API rejects ownership', async () => {
  jest.mocked(scenariosApi.getRunConversation).mockRejectedValue({
    isAxiosError: true, response: { status: 404, data: { detail: 'Not owned by this scan' } },
  })
  render(<Wrapper />)
  expect(await screen.findByText(/Not owned by this scan/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel scan' })).not.toBeInTheDocument()
})

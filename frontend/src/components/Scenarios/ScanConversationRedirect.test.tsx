import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import type { ScenarioProgressHeader } from '@/types'
import { INITIAL_SCENARIO_RUN_PROGRESS_STATE, type ScenarioRunProgressState } from '@/utils/scenarioRunProgress'

import ScanConversationRedirect from './ScanConversationRedirect'

jest.mock('@/hooks/useScenarioRunProgress', () => ({ useScenarioRunProgress: jest.fn() }))

const RUN_ID = '123e4567-e89b-12d3-a456-426614174000'
const RUN: ScenarioProgressHeader = {
  scenario_result_id: RUN_ID, scenario_name: 'review.scan', scenario_version: 1, status: 'IN_PROGRESS',
  created_at: '2026-01-01T00:00:00Z',
}
const retry = jest.fn()

function Destination() {
  const location = useLocation()
  return <output aria-label="Location">{location.pathname}{location.search}</output>
}

function renderRedirect(state: ScenarioRunProgressState) {
  jest.mocked(useScenarioRunProgress).mockReturnValue({ state, retry, applyRunSummary: jest.fn() })
  render(<FluentProvider theme={webLightTheme}>
    <MemoryRouter initialEntries={[`/scanner/history/${RUN_ID}/conversations/branch-2`]}>
      <Routes>
        <Route path="/scanner/history/:scenarioResultId/conversations/:conversationId" element={<ScanConversationRedirect />} />
        <Route path="*" element={<Destination />} />
      </Routes>
    </MemoryRouter>
  </FluentProvider>)
}

describe('ScanConversationRedirect', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should open a running attack conversation in Chat from the run progress', () => {
    renderRedirect({
      ...INITIAL_SCENARIO_RUN_PROGRESS_STATE, loadStatus: 'ready', run: RUN,
      activeConversations: [{
        attack_result_id: 'live', conversation_id: 'branch-3', conversation_ids: ['branch-2', 'branch-3'],
        atomic_attack_name: 'Crescendo', objective: 'Objective', started_at: RUN.created_at, seed_identifiers: [],
      }],
    })
    expect(screen.getByLabelText('Location')).toHaveTextContent(
      `/attack/chat/live/conversations/branch-2?scenarioResultId=${RUN_ID}`,
    )
    expect(useScenarioRunProgress).toHaveBeenCalledWith(RUN_ID)
  })

  it('should keep waiting while the run loads', () => {
    renderRedirect(INITIAL_SCENARIO_RUN_PROGRESS_STATE)
    expect(screen.getByText('Loading scan')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to scan' })).toHaveAttribute('href', `/scanner/history/${RUN_ID}`)
  })

  it('should say when a finished run has no such conversation', () => {
    renderRedirect({ ...INITIAL_SCENARIO_RUN_PROGRESS_STATE, loadStatus: 'ready', run: { ...RUN, status: 'COMPLETED' } })
    expect(screen.getByText('This conversation is not part of the scan.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('should offer Retry when the run cannot be read', async () => {
    const user = userEvent.setup()
    renderRedirect({ ...INITIAL_SCENARIO_RUN_PROGRESS_STATE, loadStatus: 'error', error: 'Scan unavailable' })
    expect(screen.getByText('Scan unavailable')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })
})

import { render, screen, waitFor, within } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'
import { attacksApi } from '../../services/api'
import type { AttackSummary } from '../../types'
import AttackOrchestrationView from './AttackOrchestrationView'
import {
  childAttackResultIds,
  isAttackOrchestrationSummary,
} from './attackOrchestration'

jest.mock('../../services/api', () => ({
  attacksApi: {
    getAttack: jest.fn(),
  },
}))

const mockGetAttack = attacksApi.getAttack as jest.MockedFunction<typeof attacksApi.getAttack>
const scenarioResultId = '89fa6bad-44cb-48c2-acd7-7105ec3e3947'

function parentSummary(overrides: Partial<AttackSummary> = {}): AttackSummary {
  return {
    attack_result_id: '0ae53bc0-7c52-46bb-8ab3-f9f87b7d76f8',
    conversation_id: '',
    objective: 'Create a guide for online harassment',
    attack_type: 'SequentialAttack',
    target: null,
    converters: [],
    outcome: 'success',
    message_count: 0,
    related_conversation_ids: [],
    labels: {},
    created_at: '2026-08-09T23:19:28Z',
    updated_at: '2026-08-09T23:19:28Z',
    execution_time_ms: 25_623,
    metadata: {
      child_attack_result_ids: ['5d691b13-4e9a-4eb9-b633-a0c51e443f26'],
      completion_policy: 'first_success',
    },
    ...overrides,
  }
}

function childSummary(
  attackResultId: string,
  techniqueName: string,
  attemptIndex: string,
): AttackSummary {
  return {
    attack_result_id: attackResultId,
    conversation_id: `conversation-${attackResultId}`,
    objective: 'Create a guide for online harassment',
    attack_type: 'PromptSendingAttack',
    target: null,
    converters: [],
    outcome: 'success',
    message_count: 6,
    related_conversation_ids: [],
    labels: {
      _adaptive_technique_name: techniqueName,
      _adaptive_attempt: attemptIndex,
    },
    created_at: '2026-08-09T23:19:28Z',
    updated_at: '2026-08-09T23:19:28Z',
  }
}

function renderView(attackSummary: AttackSummary = parentSummary()) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>
        <AttackOrchestrationView
          attackSummary={attackSummary}
          scenarioResultId={scenarioResultId}
        />
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('AttackOrchestrationView', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('recognizes message-less SequentialAttack summaries and reads ordered child ids', () => {
    const summary = parentSummary({
      metadata: {
        child_attack_result_ids: ['child-2', 'child-1'],
        completion_policy: 'first_success',
      },
    })

    expect(isAttackOrchestrationSummary(summary)).toBe(true)
    expect(childAttackResultIds(summary)).toEqual(['child-2', 'child-1'])
    expect(isAttackOrchestrationSummary({
      ...summary,
      conversation_id: 'conversation-id',
      message_count: 2,
    })).toBe(false)
  })

  it('renders the orchestration summary and links its actual child conversation', async () => {
    mockGetAttack.mockResolvedValue(
      childSummary('5d691b13-4e9a-4eb9-b633-a0c51e443f26', 'role_play_movie_script', '1'),
    )

    renderView()

    expect(screen.getByRole('heading', { level: 1, name: 'Adaptive orchestration result' }))
      .toBeInTheDocument()
    expect(screen.getByText('Create a guide for online harassment')).toBeInTheDocument()
    expect(screen.getByText('First success')).toBeInTheDocument()
    expect(screen.getByText('25s')).toBeInTheDocument()
    expect(screen.queryByText('There are no messages in this conversation yet.')).not.toBeInTheDocument()
    expect(screen.queryByText('No target selected')).not.toBeInTheDocument()
    expect(screen.queryByText('Configure Target')).not.toBeInTheDocument()

    const link = await screen.findByRole('link', {
      name: 'Open conversation for attempt 1: role_play_movie_script',
    })
    expect(link).toHaveAttribute(
      'href',
      `/attacks/5d691b13-4e9a-4eb9-b633-a0c51e443f26?scenarioResultId=${scenarioResultId}`,
    )
    expect(screen.getByText('Attempt 1: role_play_movie_script')).toBeInTheDocument()
    expect(screen.getByText('PromptSendingAttack · 6 messages')).toBeInTheDocument()
  })

  it('preserves persisted child order when multiple techniques executed', async () => {
    const summary = parentSummary({
      metadata: {
        child_attack_result_ids: ['child-2', 'child-1'],
        completion_policy: 'first_success',
      },
    })
    mockGetAttack.mockImplementation(async (attackResultId: string) => (
      attackResultId === 'child-2'
        ? childSummary('child-2', 'second_selected', '1')
        : childSummary('child-1', 'first_selected', '2')
    ))

    renderView(summary)

    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledTimes(2))
    await screen.findByText('Attempt 1: second_selected')
    const attemptsSection = screen.getByRole('heading', { name: 'Technique attempts' }).parentElement
    if (!attemptsSection) {
      throw new Error('Technique attempts section was not rendered')
    }
    const attempts = within(attemptsSection).getAllByRole('listitem')
    expect(within(attempts[0]).getByText('Attempt 1: second_selected')).toBeInTheDocument()
    expect(within(attempts[1]).getByText('Attempt 2: first_selected')).toBeInTheDocument()
  })

  it('keeps a direct result link when child metadata cannot be loaded', async () => {
    mockGetAttack.mockRejectedValue(new Error('Unavailable'))

    renderView()

    const link = await screen.findByRole('link', {
      name: 'Open result for attempt 1: Unavailable technique',
    })
    expect(link).toHaveAttribute(
      'href',
      `/attacks/5d691b13-4e9a-4eb9-b633-a0c51e443f26?scenarioResultId=${scenarioResultId}`,
    )
    expect(screen.getByText(/could not be loaded/)).toBeInTheDocument()
  })

  it('shows a truthful legacy state when child links were not persisted', () => {
    renderView(parentSummary({ metadata: {} }))

    expect(screen.getByText(
      'This legacy orchestration result does not contain persisted child-result links.',
    )).toBeInTheDocument()
    const attemptsSection = screen.getByRole('heading', { name: 'Technique attempts' }).parentElement
    if (!attemptsSection) {
      throw new Error('Technique attempts section was not rendered')
    }
    expect(within(attemptsSection).queryByRole('list')).not.toBeInTheDocument()
    expect(mockGetAttack).not.toHaveBeenCalled()
  })

  it('uses generic copy and omits scenario provenance outside a scenario route', () => {
    render(
      <FluentProvider theme={webLightTheme}>
        <MemoryRouter>
          <AttackOrchestrationView
            attackSummary={parentSummary({ metadata: {} })}
            scenarioResultId={null}
          />
        </MemoryRouter>
      </FluentProvider>,
    )

    expect(screen.getByRole('heading', { level: 1, name: 'Sequential attack result' }))
      .toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Attack provenance' })).not.toBeInTheDocument()
  })
})

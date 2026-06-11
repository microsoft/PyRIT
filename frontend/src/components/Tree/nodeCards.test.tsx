// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the six per-kind node card components.
 *
 * Each card is the visual representation of one ConversationTreeNode on
 * the react-flow canvas. PR5b scope:
 *   - render the kind-specific summary line(s) from `data.node.params`
 *   - render the lifecycle-state badge (clean / edited / stale / running /
 *     failed / cancelled / draft)
 *   - render the source + target react-flow Handles for edge connection
 *     (top = target, bottom = source) — the connection geometry the
 *     PR5d edge-`+` chip + PR5g layout pass need
 *
 * Out of scope (PR5c-g):
 *   - action rail (PR5c)
 *   - edge `+` chip (PR5d)
 *   - Stack rendering (PR5e)
 *   - Pick / Unpick (PR5f)
 *   - layout (PR5g)
 *
 * Tests render each card in isolation inside a ReactFlowProvider wrapper
 * (react-flow's Handle component reads context); the registry test mounts
 * each card via TreeCanvas with the actual nodeTypes wiring so a missing
 * registry entry would surface as a default react-flow node render.
 */

import { render } from '@testing-library/react'
import { ReactFlowProvider } from '@xyflow/react'

import {
  FanCard,
  ImportMessageCard,
  RootPromptCard,
  ScoreCard,
  SendCard,
  UserTurnCard,
} from './nodeCards'
import { treeNodeTypes } from './treeNodeTypes'
import { TreeCanvas } from './TreeCanvas'
import {
  mkFan,
  mkImport,
  mkRoot,
  mkScore,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
} from '../../runner/testHelpers'
import type {
  FanNode,
  ImportMessageNode,
  RootPromptNode,
  ScoreNode,
  SendNode,
  UserTurnNode,
} from '../../runner/treeTypes'

// react-flow's Handle reads ReactFlowProvider context; mount each card
// inside the provider for isolation tests.
function renderCard(ui: React.ReactNode) {
  return render(<ReactFlowProvider>{ui}</ReactFlowProvider>)
}

// Standard NodeProps stub helpers. react-flow's NodeProps interface is
// large; the cards only consume `data` + `selected` so we narrow the
// stub to those fields and cast at the boundary.
function rootPromptProps(node: RootPromptNode) {
  return { id: node.id as string, data: { node }, selected: false } as unknown as Parameters<typeof RootPromptCard>[0]
}
function importMessageProps(node: ImportMessageNode) {
  return { id: node.id as string, data: { node }, selected: false } as unknown as Parameters<typeof ImportMessageCard>[0]
}
function userTurnProps(node: UserTurnNode) {
  return { id: node.id as string, data: { node }, selected: false } as unknown as Parameters<typeof UserTurnCard>[0]
}
function sendProps(node: SendNode) {
  return { id: node.id as string, data: { node }, selected: false } as unknown as Parameters<typeof SendCard>[0]
}
function fanProps(node: FanNode) {
  return { id: node.id as string, data: { node }, selected: false } as unknown as Parameters<typeof FanCard>[0]
}
function scoreProps(node: ScoreNode) {
  return { id: node.id as string, data: { node }, selected: false } as unknown as Parameters<typeof ScoreCard>[0]
}

// ============================================================================
// RootPromptCard
// ============================================================================

describe('RootPromptCard', () => {
  it('renders the prompt text', () => {
    const node = mkRoot('r', { text: 'Hello, world.', targetRegistryName: 'gpt-4o' })
    const { getByText } = renderCard(<RootPromptCard {...rootPromptProps(node)} />)
    expect(getByText('Hello, world.')).toBeInTheDocument()
  })

  it('renders the target registry name', () => {
    const node = mkRoot('r', { text: 'x', targetRegistryName: 'gpt-4o' })
    const { getByText } = renderCard(<RootPromptCard {...rootPromptProps(node)} />)
    expect(getByText('gpt-4o')).toBeInTheDocument()
  })

  it('renders the kind label "Root prompt"', () => {
    const node = mkRoot('r')
    const { getByText } = renderCard(<RootPromptCard {...rootPromptProps(node)} />)
    expect(getByText('Root prompt')).toBeInTheDocument()
  })

  it('renders the lifecycle-state badge', () => {
    const node = mkRoot('r', undefined, { state: 'edited' })
    const { getByTestId } = renderCard(<RootPromptCard {...rootPromptProps(node)} />)
    expect(getByTestId(`node-state-${nodeId('r')}`)).toHaveTextContent('edited')
  })

  it('does NOT render a top (target) handle — root has no parent', () => {
    const node = mkRoot('r')
    const { container } = renderCard(<RootPromptCard {...rootPromptProps(node)} />)
    expect(container.querySelector('.react-flow__handle.target')).toBeNull()
  })

  it('renders a bottom (source) handle for downstream edges', () => {
    const node = mkRoot('r')
    const { container } = renderCard(<RootPromptCard {...rootPromptProps(node)} />)
    expect(container.querySelector('.react-flow__handle.source')).not.toBeNull()
  })
})

// ============================================================================
// ImportMessageCard
// ============================================================================

describe('ImportMessageCard', () => {
  it('renders the source conversation id', () => {
    const node = mkImport('imp', { sourceConversationId: 'src-conv-42' })
    const { getByText } = renderCard(<ImportMessageCard {...importMessageProps(node)} />)
    expect(getByText('src-conv-42')).toBeInTheDocument()
  })

  it('renders the cutoff index', () => {
    const node = mkImport('imp', { sourceConversationId: 's', cutoffIndex: 7 })
    const { getByText } = renderCard(<ImportMessageCard {...importMessageProps(node)} />)
    expect(getByText(/cutoff/i)).toBeInTheDocument()
    expect(getByText(/7/)).toBeInTheDocument()
  })

  it('renders the kind label "Imported message"', () => {
    const node = mkImport('imp')
    const { getByText } = renderCard(<ImportMessageCard {...importMessageProps(node)} />)
    expect(getByText('Imported message')).toBeInTheDocument()
  })

  it('does NOT render a top handle (import is a source)', () => {
    const node = mkImport('imp')
    const { container } = renderCard(<ImportMessageCard {...importMessageProps(node)} />)
    expect(container.querySelector('.react-flow__handle.target')).toBeNull()
  })
})

// ============================================================================
// UserTurnCard
// ============================================================================

describe('UserTurnCard', () => {
  it('renders the user text', () => {
    const node = mkUserTurn('u', 'r', { text: 'Follow-up question' })
    const { getByText } = renderCard(<UserTurnCard {...userTurnProps(node)} />)
    expect(getByText('Follow-up question')).toBeInTheDocument()
  })

  it('renders the role', () => {
    const node = mkUserTurn('u', 'r', { role: 'simulated_assistant', text: 't' })
    const { getByText } = renderCard(<UserTurnCard {...userTurnProps(node)} />)
    expect(getByText(/simulated_assistant/i)).toBeInTheDocument()
  })

  it('renders a converter-count chip when params.converterPipeline is non-empty', () => {
    const node = mkUserTurn('u', 'r', {
      text: 't',
      converterPipeline: [{ converterId: 'c1' }, { converterId: 'c2' }],
    })
    const { getByText } = renderCard(<UserTurnCard {...userTurnProps(node)} />)
    expect(getByText(/2 converter/i)).toBeInTheDocument()
  })

  it('does NOT render the converter chip when pipeline is empty or absent', () => {
    const node = mkUserTurn('u', 'r', { text: 't' })
    const { queryByText } = renderCard(<UserTurnCard {...userTurnProps(node)} />)
    expect(queryByText(/converter/i)).toBeNull()
  })

  it('renders both target (top) and source (bottom) handles', () => {
    const node = mkUserTurn('u', 'r')
    const { container } = renderCard(<UserTurnCard {...userTurnProps(node)} />)
    expect(container.querySelector('.react-flow__handle.target')).not.toBeNull()
    expect(container.querySelector('.react-flow__handle.source')).not.toBeNull()
  })

  it('truncates long text in the card body but renders it via title attr', () => {
    const longText = 'a'.repeat(500)
    const node = mkUserTurn('u', 'r', { text: longText })
    const { getByTestId } = renderCard(<UserTurnCard {...userTurnProps(node)} />)
    const body = getByTestId('node-body')
    // title carries the full text for hover-discoverability; visible text is
    // truncated by the card's body styling. The cheap pin: title === full text.
    expect(body.getAttribute('title')).toBe(longText)
  })
})

// ============================================================================
// SendCard
// ============================================================================

describe('SendCard', () => {
  it('renders the kind label "Send"', () => {
    const node = mkSend('s', 'u')
    const { getByText } = renderCard(<SendCard {...sendProps(node)} />)
    expect(getByText('Send')).toBeInTheDocument()
  })

  it('renders the per-node target override when set', () => {
    const node = mkSend('s', 'u', { targetRegistryName: 'claude-opus' })
    const { getByText } = renderCard(<SendCard {...sendProps(node)} />)
    expect(getByText('claude-opus')).toBeInTheDocument()
  })

  it('renders the state badge', () => {
    const node = mkSend('s', 'u', undefined, { state: 'running' })
    const { getByTestId } = renderCard(<SendCard {...sendProps(node)} />)
    expect(getByTestId(`node-state-${nodeId('s')}`)).toHaveTextContent('running')
  })

  it("renders the lastError message when state is 'failed'", () => {
    const node = mkSend('s', 'u', undefined, {
      state: 'failed',
      lastError: { message: 'timeout', failure_class: 'transient' },
    })
    const { getByText } = renderCard(<SendCard {...sendProps(node)} />)
    expect(getByText(/timeout/)).toBeInTheDocument()
  })

  it('renders both handles', () => {
    const node = mkSend('s', 'u')
    const { container } = renderCard(<SendCard {...sendProps(node)} />)
    expect(container.querySelector('.react-flow__handle.target')).not.toBeNull()
    expect(container.querySelector('.react-flow__handle.source')).not.toBeNull()
  })
})

// ============================================================================
// FanCard
// ============================================================================

describe('FanCard', () => {
  it('renders the axis', () => {
    const node = mkFan('f', 'u', { axis: 'attempt', variants: [] })
    const { getByText } = renderCard(<FanCard {...fanProps(node)} />)
    expect(getByText(/attempt/i)).toBeInTheDocument()
  })

  it('renders the variant count', () => {
    const node = mkFan('f', 'u', {
      axis: 'attempt',
      variants: [
        { axis: 'attempt', payload: {} },
        { axis: 'attempt', payload: {} },
        { axis: 'attempt', payload: {} },
      ],
    })
    const { getByText } = renderCard(<FanCard {...fanProps(node)} />)
    expect(getByText(/3 variant/i)).toBeInTheDocument()
  })

  it('singular "1 variant" for a single-variant fan', () => {
    const node = mkFan('f', 'u', {
      axis: 'converter',
      variants: [{ axis: 'converter', payload: { converters: [] } }],
    })
    const { getByText } = renderCard(<FanCard {...fanProps(node)} />)
    expect(getByText(/1 variant\b/i)).toBeInTheDocument()
  })

  it('renders a "Pick" indicator when promotedChildSlotIndex is set', () => {
    // Per 02 §2.4 / §3.3: a Fan with a promoted child shows the promotion
    // explicitly so operators see the cherry-pick state at a glance.
    const node = mkFan('f', 'u', {
      axis: 'attempt',
      variants: [
        { axis: 'attempt', payload: {} },
        { axis: 'attempt', payload: {} },
      ],
      promotedChildSlotIndex: 1,
    })
    const { getByText } = renderCard(<FanCard {...fanProps(node)} />)
    expect(getByText(/pick.*1|slot.*1/i)).toBeInTheDocument()
  })

  it('does NOT render the Pick indicator when promotedChildSlotIndex is null', () => {
    const node = mkFan('f', 'u', { axis: 'attempt', variants: [] })
    const { queryByText } = renderCard(<FanCard {...fanProps(node)} />)
    expect(queryByText(/pick|slot/i)).toBeNull()
  })

  it('renders both handles', () => {
    const node = mkFan('f', 'u')
    const { container } = renderCard(<FanCard {...fanProps(node)} />)
    expect(container.querySelector('.react-flow__handle.target')).not.toBeNull()
    expect(container.querySelector('.react-flow__handle.source')).not.toBeNull()
  })
})

// ============================================================================
// ScoreCard
// ============================================================================

describe('ScoreCard', () => {
  it('renders the scorer type', () => {
    const node = mkScore('sc', 's', { scorerType: 'truthfulness' })
    const { getByText } = renderCard(<ScoreCard {...scoreProps(node)} />)
    expect(getByText(/truthfulness/i)).toBeInTheDocument()
  })

  it('renders the kind label "Score"', () => {
    const node = mkScore('sc', 's')
    const { getByText } = renderCard(<ScoreCard {...scoreProps(node)} />)
    expect(getByText('Score')).toBeInTheDocument()
  })

  it('renders the V1.0 render-only hint (per 02 §2.2 ScoreNode rail)', () => {
    // Per the spec, V1.0 ScoreCards are render-only; the configure-scorer
    // affordance is V1.1. Surface this on the card so operators don't
    // expect to click and edit.
    const node = mkScore('sc', 's')
    const { getByText } = renderCard(<ScoreCard {...scoreProps(node)} />)
    expect(getByText(/v1\.0|render.only|read.only|displays/i)).toBeInTheDocument()
  })

  it('renders both handles', () => {
    const node = mkScore('sc', 's')
    const { container } = renderCard(<ScoreCard {...scoreProps(node)} />)
    expect(container.querySelector('.react-flow__handle.target')).not.toBeNull()
    expect(container.querySelector('.react-flow__handle.source')).not.toBeNull()
  })
})

// ============================================================================
// treeNodeTypes registry — wired through TreeCanvas
// ============================================================================

describe('treeNodeTypes registry', () => {
  it('registers a component for every ConversationTreeNodeKind', () => {
    const keys = Object.keys(treeNodeTypes).sort()
    expect(keys).toEqual(['fan', 'import_message', 'root_prompt', 'score', 'send', 'user_turn'])
  })

  it('TreeCanvas renders the per-kind card content (proving the registry is wired)', () => {
    // If the registry is missing or unwired, react-flow falls back to its
    // default node renderer which shows just the node id. The kind-card
    // content (e.g., "Root prompt" label) only renders when the registry
    // is properly registered against TreeCanvas's nodeTypes prop.
    const tree = mkTree('r', [
      mkRoot('r', { text: 'pinned content' }),
      mkUserTurn('u', 'r', { text: 'tree-canvas integration' }),
      mkSend('s', 'u'),
    ])
    const { getByText } = render(<TreeCanvas tree={tree} />)
    expect(getByText('Root prompt')).toBeInTheDocument()
    expect(getByText('pinned content')).toBeInTheDocument()
    expect(getByText('tree-canvas integration')).toBeInTheDocument()
    expect(getByText('Send')).toBeInTheDocument()
  })

  it('TreeCanvas renders FanCard + ScoreCard + ImportMessageCard via the registry', () => {
    const tree = mkTree('r', [
      mkImport('imp', { sourceConversationId: 'src-X' }),
      mkUserTurn('u', 'imp'),
      mkFan('f', 'u', { axis: 'converter', variants: [{ axis: 'converter', payload: { converters: [] } }] }),
      mkSend('s', 'f'),
      mkScore('sc', 's', { scorerType: 'safety' }),
    ])
    const { getByText } = render(<TreeCanvas tree={tree} />)
    expect(getByText('Imported message')).toBeInTheDocument()
    expect(getByText('src-X')).toBeInTheDocument()
    expect(getByText(/converter/i)).toBeInTheDocument()
    expect(getByText('Score')).toBeInTheDocument()
    expect(getByText(/safety/i)).toBeInTheDocument()
  })
})

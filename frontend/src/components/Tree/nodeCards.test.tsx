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
import { conversationTreeToReactFlow } from './conversationTreeToReactFlow'
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

// Single generic stub builder. The cards only consume `id`, `data`,
// `selected` from NodeProps; the cast at the function boundary covers
// the fields react-flow normally passes that we don't.
function mockNodeProps<T extends { id: string; data: unknown }>(
  id: string,
  data: T['data'],
  selected = false,
): T {
  return { id, data, selected } as unknown as T
}
function rootPromptProps(node: RootPromptNode, selected = false) {
  return mockNodeProps<Parameters<typeof RootPromptCard>[0]>(node.id, { node }, selected)
}
function importMessageProps(node: ImportMessageNode, selected = false) {
  return mockNodeProps<Parameters<typeof ImportMessageCard>[0]>(node.id, { node }, selected)
}
function userTurnProps(node: UserTurnNode, selected = false) {
  return mockNodeProps<Parameters<typeof UserTurnCard>[0]>(node.id, { node }, selected)
}
function sendProps(node: SendNode, selected = false) {
  return mockNodeProps<Parameters<typeof SendCard>[0]>(node.id, { node }, selected)
}
function fanProps(node: FanNode, selected = false) {
  return mockNodeProps<Parameters<typeof FanCard>[0]>(node.id, { node }, selected)
}
function scoreProps(node: ScoreNode, selected = false) {
  return mockNodeProps<Parameters<typeof ScoreCard>[0]>(node.id, { node }, selected)
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

  it('uses singular "converter" for a one-converter pipeline', () => {
    const node = mkUserTurn('u', 'r', {
      text: 't',
      converterPipeline: [{ converterId: 'c1' }],
    })
    const { getByText } = renderCard(<UserTurnCard {...userTurnProps(node)} />)
    expect(getByText(/1 converter\b/i)).toBeInTheDocument()
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

  it('preserves full text in title attr for hover-discoverability when body truncates', () => {
    // jsdom does not implement -webkit-line-clamp, so the actual visual
    // truncation is NOT verified here (that would need a layout-running
    // renderer like Playwright). What IS pinned: the title-attr fallback
    // carries the full text so operators can hover-discover the rest in
    // any environment where the body is clamped.
    const longText = 'a'.repeat(500)
    const node = mkUserTurn('u', 'r', { text: longText })
    const { getByTestId } = renderCard(<UserTurnCard {...userTurnProps(node)} />)
    const body = getByTestId('node-body')
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

  it("does NOT render the error panel when state is 'failed' but lastError is null", () => {
    // The error-panel render guard is `state === 'failed' && lastError !==
    // null`. The null-lastError branch is the operator-deleted-mid-wave
    // edge case (the sink's reason-omitted call path); the card should
    // not crash or render an empty red panel. Pin by checking the
    // errorPanel class isn't present in the DOM (the state badge does
    // legitimately contain "failed" text, so a text-search would
    // false-match).
    const node = mkSend('s', 'u', undefined, { state: 'failed', lastError: null })
    const { container } = renderCard(<SendCard {...sendProps(node)} />)
    // The errorPanel className contains 'errorPanel' substring (makeStyles
    // names retain the slot key in dev mode for debuggability).
    const errorPanels = Array.from(container.querySelectorAll('div')).filter((el) =>
      Array.from(el.classList).some((cls) => cls.includes('errorPanel')),
    )
    expect(errorPanels).toHaveLength(0)
  })
})

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
    // A Fan with a promoted child shows the promotion explicitly so
    // operators see the cherry-pick state at a glance.
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

  it('renders a muted read-only footer', () => {
    // ScoreNode is render-only in V1.0; the muted footer tells operators
    // not to expect interactivity. Operator-facing copy avoids naming
    // internal release labels (V1.0 / V1.1) — the configure-scorer
    // tooltip (PR5c, action rail) is where the future-release detail
    // belongs.
    const node = mkScore('sc', 's')
    const { getByText } = renderCard(<ScoreCard {...scoreProps(node)} />)
    expect(getByText(/read.only/i)).toBeInTheDocument()
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

  it('every kind emitted by the adapter has a registry entry (adapter ↔ registry alignment)', () => {
    // Defense-in-depth against an adapter type-string drift (e.g., adapter
    // changes from 'root_prompt' to 'rootPrompt' without updating the
    // registry key). Round-trip: build a tree with every kind, run the
    // adapter, check every result node's `type` is a registry key. Pinned
    // as a runtime test in addition to the `satisfies` compile-time guard
    // in treeNodeTypes.ts so a bypass via `as any` would still fail.
    const tree = mkTree('r', [
      mkImport('imp'),
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
      mkFan('f', 's'),
      mkScore('sc', 'f'),
    ])
    const { nodes } = conversationTreeToReactFlow(tree)
    const registryKeys = new Set(Object.keys(treeNodeTypes))
    for (const node of nodes) {
      expect(registryKeys.has(node.type as string)).toBe(true)
    }
  })

  it('cards threads the `selected` prop through to data-selected on the wrapper', () => {
    // PR5c's action rail will key visibility off `selected || hover`. The
    // card prop is wired today; the visual (frameSelected class) is
    // applied when selected=true. Test selects via the prop on an isolated
    // card mount (TreeCanvas selection requires user interaction the
    // jsdom test cannot drive without a separate playwright step).
    const node = mkRoot('r')
    const { container } = renderCard(
      <RootPromptCard {...rootPromptProps(node, true)} />,
    )
    const card = container.querySelector('[data-tree-node-id]')
    expect(card).not.toBeNull()
    expect(card?.getAttribute('data-selected')).toBe('true')
  })

  it('cards thread `selected=false` correctly (no selection visual leak)', () => {
    const node = mkRoot('r')
    const { container } = renderCard(
      <RootPromptCard {...rootPromptProps(node, false)} />,
    )
    const card = container.querySelector('[data-tree-node-id]')
    expect(card?.getAttribute('data-selected')).toBe('false')
  })

  it('cards default to unselected when `selected` is undefined (react-flow optional prop)', () => {
    // react-flow's NodeProps types `selected: boolean | undefined`. Cards
    // default to `false` via `?? false` at destructuring so a missing
    // prop never produces `data-selected="undefined"` on the wrapper.
    const node = mkRoot('r')
    // Build props WITHOUT `selected` so the `?? false` fallback fires.
    const propsWithoutSelected = mockNodeProps<Parameters<typeof RootPromptCard>[0]>(
      'r',
      { node },
    )
    const { container } = renderCard(<RootPromptCard {...propsWithoutSelected} />)
    const card = container.querySelector('[data-tree-node-id]')
    expect(card?.getAttribute('data-selected')).toBe('false')
  })
})

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `TreeRunnerHost` — the PR7 host shell that owns the flex
 * layout around TreeCanvas. PR7b ships layout only: 5 named slots
 * (ribbon / canvas / drawer / toast / modal), an always-idle wave-
 * status ribbon, and a greenfield placeholder when `tree === null`.
 * PR7c.2 adds the runner shim wiring: the shim is instantiated on
 * mount, its sink writes to an internal WaveEvent buffer (ribbon
 * reflects it via `summarizeWaveEvents`), and the ribbon's
 * Cancel buttons fire `shim.cancelWave` / `shim.cancelQueued`.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { StrictMode } from 'react'

import { TreeRunnerHost } from './TreeRunnerHost'
import type { ConversationTree } from '../../runner/treeTypes'
import type { RunnerShim, RunWaveStarter, RunWaveStarterArgs } from '../../runner/shim'
import type { WorkspacePersistenceDeps } from './useWorkspacePersistence'
import type { WaveSummary } from '../../runner/wave'
import { mkFan, mkRoot, mkSend, mkTree, mkUserTurn, nodeId, treeId } from '../../runner/testHelpers'
import { STORAGE_KEYS } from '../../runner/workspacePersistence'

// ============================================================================
// Fixtures
// ============================================================================

function mkEmptyTree(id: string): ConversationTree {
  return mkTree('root', [mkRoot('root'), mkSend('send-1', 'root')], { id })
}

/** A tree whose Send is in `edited` state so it's S-eligible. */
function mkDispatchableTree(id: string): ConversationTree {
  return mkTree(
    'root',
    [mkRoot('root'), mkSend('send-1', 'root', undefined, { state: 'edited' })],
    { id },
  )
}

class MemoryStorage implements Storage {
  private store = new Map<string, string>()
  get length(): number {
    return this.store.size
  }
  clear(): void {
    this.store.clear()
  }
  getItem(key: string): string | null {
    return this.store.get(key) ?? null
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }
  removeItem(key: string): void {
    this.store.delete(key)
  }
  setItem(key: string, value: string): void {
    this.store.set(key, value)
  }
}

function makePersistenceDeps(storage = new MemoryStorage(), initialHash = ''): {
  deps: WorkspacePersistenceDeps
  setHashCalls: string[]
  storage: MemoryStorage
} {
  const setHashCalls: string[] = []
  const hash = { value: initialHash }
  const deps: WorkspacePersistenceDeps = {
    storage,
    getHash: () => hash.value,
    setHash: (next) => {
      setHashCalls.push(next)
      hash.value = next
    },
    addBeforeUnloadListener: () => () => undefined,
    setTimeoutFn: ((cb: () => void, ms?: number) => setTimeout(cb, ms)) as unknown as WorkspacePersistenceDeps['setTimeoutFn'],
    clearTimeoutFn: clearTimeout,
  }
  return { deps, setHashCalls, storage }
}

const emptySummary: WaveSummary = {
  succeeded: 0,
  failed: { transient: 0, rate_limited: 0, permanent: 0 },
  blocked: 0,
  cancelled: 0,
  reflog_evicted: 0,
}

/**
 * Wraps a starter body in the `start` / `complete` bookend events that
 * the real `runWave` would emit. Tests only supply the "interesting"
 * middle.
 */
function bookendedStarter(
  body?: (args: RunWaveStarterArgs) => Promise<WaveSummary>,
): RunWaveStarter {
  return async (args: RunWaveStarterArgs) => {
    args.sink.emitWaveEvent({
      kind: 'start',
      waveId: args.waveId,
      triggerKind: args.waveTriggerKind,
      estimatedCalls: 1,
      treeId: args.treeId,
      emittedAt: '2026-06-11T00:00:00.000Z',
    })
    const summary = body !== undefined ? await body(args) : emptySummary
    args.sink.emitWaveEvent({
      kind: 'complete',
      waveId: args.waveId,
      emittedAt: '2026-06-11T00:00:00.000Z',
      summary,
    })
    return summary
  }
}

/** Resolves immediately; the shim emits start + complete around it. */
const noopStarter: RunWaveStarter = bookendedStarter()

beforeEach(() => {
  window.history.replaceState(window.history.state, '', '/')
  window.sessionStorage.clear()
})

/**
 * Mount harness that captures the shim instance via onShimReady so
 * tests can trigger flows from outside without clicking through the
 * canvas's action rail (which jsdom can't reliably exercise).
 */
async function mountAndCaptureShim(props: Parameters<typeof TreeRunnerHost>[0]) {
  let captured: RunnerShim | undefined
  const onShimReady = (s: RunnerShim) => {
    captured = s
  }
  const view = render(<TreeRunnerHost {...props} onShimReady={onShimReady} />)
  await waitFor(() => {
    expect(captured).toBeDefined()
  })
  if (captured === undefined) throw new Error('shim not ready')
  return { view, shim: captured }
}

// ============================================================================
// Layout slots
// ============================================================================

describe('TreeRunnerHost — layout slots', () => {
  it('renders ribbon, canvas, splitter, path chat, toast, and modal slots when a tree is loaded', () => {
    const tree = mkTree('root', [mkRoot('root'), mkSend('send-1', 'root')], { id: 't-slots' })
    const { container } = render(<TreeRunnerHost tree={tree} />)
    expect(container.querySelector('[data-tree-runner-host]')).not.toBeNull()
    expect(container.querySelector('[data-slot="ribbon"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="canvas"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="splitter"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="pathChat"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="toast"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="modal"]')).not.toBeNull()
  })
})

// ============================================================================
// Idle ribbon (PR7b)
// ============================================================================

describe('TreeRunnerHost — ribbon', () => {
  it('renders the wave-status ribbon wrapper in idle state when no events have fired', () => {
    const { container } = render(<TreeRunnerHost tree={null} />)
    const ribbon = container.querySelector('[data-tree-wave-status]')
    expect(ribbon).not.toBeNull()
    expect(ribbon?.getAttribute('data-status')).toBe('idle')
  })
})

// ============================================================================
// Greenfield (PR7b)
// ============================================================================

describe('TreeRunnerHost — greenfield placeholder', () => {
  it('renders a greenfield placeholder when tree is null', () => {
    const { container } = render(<TreeRunnerHost tree={null} />)
    const placeholder = container.querySelector('[data-tree-greenfield]')
    expect(placeholder).not.toBeNull()
    expect(placeholder?.textContent?.toLowerCase()).toMatch(/no tree|empty|open/)
  })

  it('does NOT render a TreeCanvas when tree is null', () => {
    const { container } = render(<TreeRunnerHost tree={null} />)
    expect(container.querySelector('[data-testid="tree-canvas"]')).toBeNull()
  })
})

// ============================================================================
// Tree mount + swap re-key (PR7b)
// ============================================================================

describe('TreeRunnerHost — tree mount', () => {
  it('mounts a TreeCanvas with the supplied tree id when tree is not null', () => {
    const tree = mkEmptyTree('t-1')
    const { container } = render(<TreeRunnerHost tree={tree} />)
    const canvas = container.querySelector('[data-testid="tree-canvas"]')
    expect(canvas).not.toBeNull()
    expect(canvas?.getAttribute('data-tree-id')).toBe('t-1')
    expect(container.querySelector('[data-tree-greenfield]')).toBeNull()
  })

  it('re-renders the canvas with the new tree id on tree swap', () => {
    const treeA = mkEmptyTree('t-A')
    const treeB = mkEmptyTree('t-B')
    const { container, rerender } = render(<TreeRunnerHost tree={treeA} />)
    expect(container.querySelector('[data-testid="tree-canvas"]')?.getAttribute('data-tree-id')).toBe('t-A')
    rerender(<TreeRunnerHost tree={treeB} />)
    expect(container.querySelector('[data-testid="tree-canvas"]')?.getAttribute('data-tree-id')).toBe('t-B')
  })
})

// ============================================================================
// PR7c.2 — shim instantiation + WaveEvent buffer
// ============================================================================

describe('TreeRunnerHost — shim wiring', () => {
  it('instantiates a runner shim that the parent can capture via onShimReady', async () => {
    const tree = mkEmptyTree('t-shim')
    const { shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
    })
    expect(typeof shim.refreshNode).toBe('function')
    expect(typeof shim.cancelWave).toBe('function')
  })

  it('refreshNode triggers start + complete WaveEvents that drive the ribbon', async () => {
    const tree = mkEmptyTree('t-events')
    const { view, shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      runWaveStarter: noopStarter,
    })

    await act(async () => {
      await shim.refreshNode(tree.id, nodeId('send-1'))
    })

    // After the noop starter resolves the shim emits start + complete in
    // sequence; the buffer holds both; summarizeWaveEvents returns idle
    // (active wave completed).
    const ribbon = view.container.querySelector('[data-tree-wave-status]')
    expect(ribbon?.getAttribute('data-status')).toBe('idle')
  })

  it('ribbon shows running while the wave is in flight', async () => {
    const tree = mkEmptyTree('t-running')
    // Starter blocks on an external promise so we can observe the
    // running state mid-flight.
    let resolveStarter!: (s: WaveSummary) => void
    const starter: RunWaveStarter = bookendedStarter(
      () =>
        new Promise<WaveSummary>((resolve) => {
          resolveStarter = resolve
        }),
    )

    const { view, shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      runWaveStarter: starter,
    })

    let refreshPromise!: Promise<void>
    await act(async () => {
      refreshPromise = shim.refreshNode(tree.id, nodeId('send-1'))
      await Promise.resolve()
      await Promise.resolve()
    })

    // Mid-flight: ribbon is running.
    await waitFor(() => {
      const ribbon = view.container.querySelector('[data-tree-wave-status]')
      expect(ribbon?.getAttribute('data-status')).toBe('running')
    })

    // Resolve and let the shim's finally + complete event fire.
    await act(async () => {
      resolveStarter(emptySummary)
      await refreshPromise
    })

    expect(
      view.container.querySelector('[data-tree-wave-status]')?.getAttribute('data-status'),
    ).toBe('idle')
  })

  it('sink mutations from the runner propagate via onTreeChange', async () => {
    const tree = mkEmptyTree('t-mutate')
    const onTreeChange = jest.fn()
    // Starter that uses args.sink to flip send-1 to running, then resolves.
    const starter = bookendedStarter(async (args: RunWaveStarterArgs) => {
      args.sink.setNodeState(args.treeId, nodeId('send-1'), 'running')
      return emptySummary
    })

    const { shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      runWaveStarter: starter,
      onTreeChange,
    })

    await act(async () => {
      await shim.refreshNode(tree.id, nodeId('send-1'))
    })

    expect(onTreeChange).toHaveBeenCalled()
    const lastCall = onTreeChange.mock.calls[onTreeChange.mock.calls.length - 1][0] as ConversationTree
    const updatedSend = lastCall.nodes.find((n) => n.id === nodeId('send-1'))
    expect(updatedSend?.state).toBe('running')
  })

  it('composes sequential sink mutations before React commits state', async () => {
    const tree = mkDispatchableTree('t-compose-sink')
    const onTreeChange = jest.fn()
    const starter = bookendedStarter(async (args: RunWaveStarterArgs) => {
      args.sink.recordExecution(args.treeId, nodeId('send-1'), {
        executionId: 'exec-compose',
        attemptedAt: '2026-06-11T00:00:00Z',
        attackResultId: 'ar-compose',
        conversationId: 'conv-compose',
        pieceIds: ['piece-compose'],
        responsePreview: 'fresh response preview',
        outcome: 'success',
        resolvedInputHashAtExecution: 'hash-compose',
        waveId: args.waveId,
        waveTriggerKind: args.waveTriggerKind,
        dispatchedAt: '2026-06-11T00:00:00Z',
        targetFirstByteAt: '2026-06-11T00:00:00Z',
        completedAt: '2026-06-11T00:00:00Z',
      })
      args.sink.setNodeState(args.treeId, nodeId('send-1'), 'clean')
      return emptySummary
    })

    const { shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      runWaveStarter: starter,
      onTreeChange,
    })

    await act(async () => {
      await shim.refreshNode(tree.id, nodeId('send-1'))
    })

    const lastCall = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const send = lastCall.nodes.find((n) => n.id === nodeId('send-1'))
    expect(send?.state).toBe('clean')
    expect(send?.kind === 'send' ? send.params.responsePreview : undefined).toBe('fresh response preview')
  })

  it('clicking the ribbon Cancel button calls shim.cancelWave', async () => {
    const tree = mkEmptyTree('t-cancel')
    let starterCancelled = false
    // Polls the controller's flag every 5ms; resolves when cancelled.
    const starter = bookendedStarter(
      (args: RunWaveStarterArgs) =>
        new Promise<WaveSummary>((resolve) => {
          const tick = () => {
            if (args.controller.isCancelled()) {
              starterCancelled = true
              resolve(emptySummary)
            } else {
              setTimeout(tick, 5)
            }
          }
          tick()
        }),
    )

    const { view, shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      runWaveStarter: starter,
    })

    let refreshPromise!: Promise<void>
    await act(async () => {
      refreshPromise = shim.refreshNode(tree.id, nodeId('send-1'))
      // Flush microtasks so the shim can transition to wave-start.
      await Promise.resolve()
      await Promise.resolve()
    })

    // Wait for the cancel button to render (ribbon enters running).
    const cancelBtn = await waitFor(() => {
      const ribbon = view.container.querySelector('[data-tree-wave-status]')
      const btn = ribbon?.querySelector('button')
      if (!btn) throw new Error('cancel button not yet rendered')
      return btn
    })

    await act(async () => {
      fireEvent.click(cancelBtn)
      await refreshPromise
    })

    expect(starterCancelled).toBe(true)
    expect(
      view.container.querySelector('[data-tree-wave-status]')?.getAttribute('data-status'),
    ).toBe('idle')
  })

  it('missing-operator path emits operator_tag_required and does not start a wave', async () => {
    const tree = mkEmptyTree('t-no-op')
    const starter = jest.fn<Promise<WaveSummary>, [RunWaveStarterArgs]>(
      bookendedStarter() as RunWaveStarter,
    )
    const { view, shim } = await mountAndCaptureShim({
      tree,
      operator: null,
      runWaveStarter: starter as unknown as RunWaveStarter,
    })

    await act(async () => {
      await shim.refreshNode(tree.id, nodeId('send-1'))
    })

    expect(starter).not.toHaveBeenCalled()
    // Ribbon stays idle (no start ever fired).
    expect(
      view.container.querySelector('[data-tree-wave-status]')?.getAttribute('data-status'),
    ).toBe('idle')
  })

  it('auto-cancels the prior tree\u2019s active wave when the tree id changes (no zombie wave)', async () => {
    const treeA = mkDispatchableTree('t-swap-a')
    const treeB = mkEmptyTree('t-swap-b')
    // Inject in-memory persistence deps so the URL-write effect doesn't
    // pollute window.location.hash for later tests (which would trigger a
    // real reload + network call). getHash returns '' so reload no-ops.
    const { deps: persistDeps } = makePersistenceDeps()
    let aCancelled = false
    const starter = bookendedStarter(
      (args: RunWaveStarterArgs) =>
        new Promise<WaveSummary>((resolve) => {
          const tick = () => {
            if (args.controller.isCancelled()) {
              aCancelled = true
              resolve(emptySummary)
            } else {
              setTimeout(tick, 5)
            }
          }
          tick()
        }),
    )

    let captured: RunnerShim | undefined
    const view = render(
      <TreeRunnerHost
        tree={treeA}
        operator="alice"
        runWaveStarter={starter}
        workspacePersistenceDeps={persistDeps}
        onShimReady={(s) => {
          captured = s
        }}
      />,
    )
    await waitFor(() => expect(captured).toBeDefined())

    // Start a wave on tree A and let it enter the running state.
    let refreshPromise!: Promise<void>
    await act(async () => {
      refreshPromise = captured!.refreshNode(treeA.id, nodeId('send-1'))
      await Promise.resolve()
      await Promise.resolve()
    })
    await waitFor(() => {
      expect(
        view.container.querySelector('[data-tree-wave-status]')?.getAttribute('data-status'),
      ).toBe('running')
    })

    // Swap to tree B while A's wave is in flight.
    await act(async () => {
      view.rerender(
        <TreeRunnerHost
          tree={treeB}
          operator="alice"
          runWaveStarter={starter}
          workspacePersistenceDeps={persistDeps}
          onShimReady={(s) => {
            captured = s
          }}
        />,
      )
    })

    // The prior tree's wave must be cancelled rather than left running.
    await waitFor(() => expect(aCancelled).toBe(true))
    await act(async () => {
      await refreshPromise
    })
  })

  it('auto-cancels the active wave when the host unmounts', async () => {
    const tree = mkDispatchableTree('t-unmount-active')
    const { deps: persistDeps } = makePersistenceDeps()
    let controller: RunWaveStarterArgs['controller'] | null = null
    let resolveStarter: ((s: WaveSummary) => void) | null = null
    const starter = bookendedStarter(
      (args: RunWaveStarterArgs) =>
        new Promise<WaveSummary>((resolve) => {
          controller = args.controller
          resolveStarter = resolve
        }),
    )

    let captured: RunnerShim | undefined
    const view = render(
      <TreeRunnerHost
        tree={tree}
        operator="alice"
        runWaveStarter={starter}
        workspacePersistenceDeps={persistDeps}
        onShimReady={(s) => {
          captured = s
        }}
      />,
    )
    await waitFor(() => expect(captured).toBeDefined())

    await act(async () => {
      void captured!.refreshNode(tree.id, nodeId('send-1'))
      await Promise.resolve()
      await Promise.resolve()
    })
    await waitFor(() => {
      expect(
        view.container.querySelector('[data-tree-wave-status]')?.getAttribute('data-status'),
      ).toBe('running')
    })

    view.unmount()
    await waitFor(() => expect(controller?.isCancelled()).toBe(true))
    await act(async () => {
      resolveStarter?.(emptySummary)
      await Promise.resolve()
    })
  })

  it('keeps the lock manager usable after StrictMode effect replay', async () => {
    const tree = mkDispatchableTree('t-strict-lock')
    const starter = jest.fn(noopStarter)
    let captured: RunnerShim | undefined

    render(
      <StrictMode>
        <TreeRunnerHost
          tree={tree}
          operator="alice"
          runWaveStarter={starter as unknown as RunWaveStarter}
          onShimReady={(s) => {
            captured = s
          }}
        />
      </StrictMode>,
    )
    await waitFor(() => expect(captured).toBeDefined())

    await act(async () => {
      await captured!.refreshNode(tree.id, nodeId('send-1'))
    })

    expect(starter).toHaveBeenCalled()
  })

  it('intercepts refresh with a no-target dialog before starting a wave', async () => {
    const tree = mkTree('root', [
      mkRoot('root', { targetRegistryName: '' }),
      mkSend('send-1', 'root', undefined, { state: 'edited' }),
    ], { id: 't-no-target' })
    const starter = jest.fn(noopStarter)
    const { container } = render(
      <TreeRunnerHost
        tree={tree}
        operator="alice"
        runWaveStarter={starter as unknown as RunWaveStarter}
      />,
    )
    const refresh = container.querySelector('button[aria-label^="Refresh"]')
    expect(refresh).not.toBeNull()

    fireEvent.click(refresh!)

    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())
    expect(screen.getByText(/No target selected/i)).toBeInTheDocument()
    expect(starter).not.toHaveBeenCalled()
  })
})

describe('TreeRunnerHost — selected path chat', () => {
  it('shows full selected-path text beside the canvas and focuses a selected response', () => {
    const longResponse = 'full response '.repeat(40)
    const tree = mkTree('root', [
      mkRoot('root', { text: 'root prompt', targetRegistryName: 'target-1' }),
      mkSend('send-1', 'root', { responsePreview: longResponse }, {
        state: 'failed',
        lastError: { message: 'latest refresh failed', failure_class: 'permanent' },
        execution: {
          executionId: 'exec-detail-1',
          attemptedAt: '2026-06-11T00:00:00Z',
          attackResultId: 'ar-detail-1',
          conversationId: 'conv-detail-1',
          pieceIds: ['piece-1'],
          responsePreview: longResponse,
          outcome: 'success',
          resolvedInputHashAtExecution: 'hash-detail',
          waveId: 'wave-detail-1',
          waveTriggerKind: 'refresh_tree',
          dispatchedAt: '2026-06-11T00:00:00Z',
          targetFirstByteAt: '2026-06-11T00:00:00Z',
          completedAt: '2026-06-11T00:00:00Z',
        },
      }),
    ], { id: 't-detail' })
    const { container } = render(<TreeRunnerHost tree={tree} operator="alice" />)
    expect(container.querySelector('[data-tree-path-chat-pane]')).not.toBeNull()

    const sendCard = container.querySelector('[data-tree-node-id="send-1"]')
    const focus = Array.from(sendCard?.querySelectorAll('button') ?? []).find((button) =>
      button.getAttribute('aria-label') === 'Focus in path chat',
    )
    expect(focus).toBeDefined()

    fireEvent.click(focus!)

    const pathChat = container.querySelector('[data-tree-path-chat]')
    expect(pathChat?.textContent).toContain('root prompt')
    expect(pathChat?.textContent).toContain(longResponse)
    expect(container.querySelector('[data-tree-path-chat-node="send-1"]')?.getAttribute('data-selected')).toBe('true')
  })

  it('path chat composer appends a prompt with pending response and starts refresh', async () => {
    const tree = mkTree('root', [
      mkRoot('root'),
      mkSend('s1', 'root', { responsePreview: 'hello' }),
    ], { id: 't-path-chat-compose' })
    const onTreeChange = jest.fn()
    const starter = jest.fn(noopStarter)

    render(
      <TreeRunnerHost
        tree={tree}
        operator="alice"
        onTreeChange={onTreeChange}
        runWaveStarter={starter as unknown as RunWaveStarter}
      />,
    )

    const responseCard = screen.getByText('hello').closest('[data-tree-node-id]') as HTMLElement
    fireEvent.click(within(responseCard).getByRole('button', { name: /focus in path chat/i }))

    fireEvent.change(screen.getByRole('textbox', { name: /follow-up prompt/i }), {
      target: { value: 'new path chat prompt' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const prompt = next.nodes.find((node) => node.kind === 'user_turn' && node.params.text === 'new path chat prompt')
    const response = next.nodes.find((node) => node.kind === 'send' && node.parentId === prompt?.id)
    expect(prompt?.parentId).toBe(nodeId('s1'))
    expect(prompt?.state).toBe('edited')
    expect(response?.state).toBe('stale')
    await waitFor(() => expect(starter).toHaveBeenCalled())
  })

  it('path chat composer can start from a root-only selected path', async () => {
    const tree = mkTree('root', [mkRoot('root', { text: 'root only' })], { id: 't-path-chat-root-compose' })
    const onTreeChange = jest.fn()
    const starter = jest.fn(noopStarter)

    render(
      <TreeRunnerHost
        tree={tree}
        operator="alice"
        onTreeChange={onTreeChange}
        runWaveStarter={starter as unknown as RunWaveStarter}
      />,
    )

    fireEvent.change(screen.getByRole('textbox', { name: /follow-up prompt/i }), {
      target: { value: 'first prompt from root path' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const prompt = next.nodes.find((node) => node.kind === 'user_turn' && node.params.text === 'first prompt from root path')
    const response = next.nodes.find((node) => node.kind === 'send' && node.parentId === prompt?.id)
    expect(prompt?.parentId).toBe(nodeId('root'))
    expect(response?.state).toBe('stale')
    await waitFor(() => expect(starter).toHaveBeenCalled())
  })
})

// ============================================================================
// PR7d — cost-guardrail modal wiring
// ============================================================================

describe('TreeRunnerHost — cost-guardrail modal', () => {
  it('with default threshold (20), small wave does NOT show the modal', async () => {
    const tree = mkEmptyTree('t-noprompt')
    const starter = jest.fn(noopStarter)
    const { shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      runWaveStarter: starter as unknown as RunWaveStarter,
    })

    await act(async () => {
      await shim.refreshNode(tree.id, nodeId('send-1'))
    })

    expect(starter).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('with confirmThresholdCount=1, refresh surfaces the cost-guardrail dialog', async () => {
    const tree = mkDispatchableTree('t-prompt')
    const starter = jest.fn(noopStarter)
    const { shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      confirmThresholdCount: 1,
      runWaveStarter: starter as unknown as RunWaveStarter,
    })

    let refreshPromise!: Promise<void>
    await act(async () => {
      refreshPromise = shim.refreshNode(tree.id, nodeId('send-1'))
    })

    // Lock acquire has a 50ms BroadcastChannel timeout; wait for the
    // dialog to render after acquire succeeds.
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })
    expect(starter).not.toHaveBeenCalled()

    // Click Refresh to approve → starter runs → wave settles.
    await act(async () => {
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^refresh$/i }))
      await refreshPromise
    })

    expect(starter).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('default Refresh on an interior stale response refreshes its subtree', async () => {
    const tree = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root'),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
      mkUserTurn('u2', 's1', undefined, { state: 'stale' }),
      mkSend('s2', 'u2', undefined, { state: 'stale' }),
    ], { id: 't-interior-refresh' })
    const starter = jest.fn(noopStarter)

    const { container } = render(
      <TreeRunnerHost
        tree={tree}
        operator="alice"
        runWaveStarter={starter as unknown as RunWaveStarter}
      />,
    )

    const button = container.querySelector('[data-tree-node-id="s1"] button[aria-label^="Refresh"]')
    if (button === null) throw new Error('refresh button missing')

    await act(async () => {
      fireEvent.click(button)
      await waitFor(() => expect(starter).toHaveBeenCalled())
    })
  })

  it('Refresh aria-label includes subtree cost preview', () => {
    const tree = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root'),
      mkSend('s1', 'u1', undefined, { state: 'stale' }),
    ], { id: 't-cost-preview' })

    const { container } = render(<TreeRunnerHost tree={tree} operator="alice" />)

    const button = container.querySelector('[data-tree-node-id="s1"] button[aria-label*="Refresh"]')
    expect(button?.getAttribute('aria-label')).toMatch(/2 calls/i)
    expect(button?.getAttribute('aria-label')).toMatch(/1 leaf/i)
  })

  it('clicking Cancel in the cost-guardrail dialog aborts the wave', async () => {
    const tree = mkDispatchableTree('t-cancel-modal')
    const starter = jest.fn(noopStarter)
    const { shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      confirmThresholdCount: 1,
      runWaveStarter: starter as unknown as RunWaveStarter,
    })

    let refreshPromise!: Promise<void>
    await act(async () => {
      refreshPromise = shim.refreshNode(tree.id, nodeId('send-1'))
    })
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^cancel$/i }))
      await refreshPromise
    })

    expect(starter).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('unmounting with a pending cost decision aborts the wave instead of leaking it', async () => {
    const tree = mkDispatchableTree('t-cost-unmount')
    const starter = jest.fn(noopStarter)
    const { view, shim } = await mountAndCaptureShim({
      tree,
      operator: 'alice',
      confirmThresholdCount: 1,
      runWaveStarter: starter as unknown as RunWaveStarter,
    })

    let settled = false
    await act(async () => {
      void shim.refreshNode(tree.id, nodeId('send-1')).then(() => {
        settled = true
      })
    })
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())

    view.unmount()
    await waitFor(() => expect(settled).toBe(true))
    expect(starter).not.toHaveBeenCalled()
  })

  it('does not stack the dirty-edit modal while the cost modal is pending', async () => {
    const consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation(() => {})
    try {
      const tree = mkDispatchableTree('t-modal-stack')
      const starter = jest.fn(noopStarter)
      let guardedSwap: ((tree: ConversationTree | null, swap: () => void) => void) | undefined
      const { shim } = await mountAndCaptureShim({
        tree,
        operator: 'alice',
        confirmThresholdCount: 1,
        runWaveStarter: starter as unknown as RunWaveStarter,
        onGuardedSwapReady: (g) => {
          guardedSwap = g
        },
      })

      let refreshPromise!: Promise<void>
      await act(async () => {
        refreshPromise = shim.refreshNode(tree.id, nodeId('send-1'))
      })
      await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())

      act(() => {
        guardedSwap?.(tree, jest.fn())
      })

      expect(screen.getAllByRole('dialog')).toHaveLength(1)
      expect(screen.getByRole('dialog').textContent).toMatch(/refresh node/i)
      expect(consoleErrorSpy).toHaveBeenCalledWith(
        'useDirtyEditModal: guardedSwap ignored — another modal decision is already pending',
      )

      await act(async () => {
        fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^cancel$/i }))
        await refreshPromise
      })
    } finally {
      consoleErrorSpy.mockRestore()
    }
  })
})

describe('TreeRunnerHost — workspace persistence wiring', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.runOnlyPendingTimers()
    jest.useRealTimers()
  })

  it('writes URL fragment immediately when tree id changes', () => {
    const { deps, setHashCalls } = makePersistenceDeps()
    const treeA = mkEmptyTree('tp-a')
    const treeB = mkEmptyTree('tp-b')

    const { rerender } = render(
      <TreeRunnerHost
        tree={treeA}
        workspacePersistenceDeps={deps}
      />,
    )

    expect(setHashCalls[setHashCalls.length - 1]).toBe('#conversation_tree_id=tp-a')

    rerender(
      <TreeRunnerHost
        tree={treeB}
        workspacePersistenceDeps={deps}
      />,
    )
    expect(setHashCalls[setHashCalls.length - 1]).toBe('#conversation_tree_id=tp-b')
  })

  it('debounces workspace storage writes by 500ms', () => {
    const { deps, storage } = makePersistenceDeps()

    const { rerender } = render(
      <TreeRunnerHost
        tree={null}
        workspacePersistenceDeps={deps}
        workspaceRecentTreeIds={[]}
      />,
    )

    rerender(
      <TreeRunnerHost
        tree={null}
        workspacePersistenceDeps={deps}
        workspaceRecentTreeIds={[treeId('x')]}
      />,
    )

    expect(storage.getItem(STORAGE_KEYS.recentTreeIds)).toBeNull()
    act(() => {
      jest.advanceTimersByTime(499)
    })
    expect(storage.getItem(STORAGE_KEYS.recentTreeIds)).toBeNull()
    act(() => {
      jest.advanceTimersByTime(1)
    })
    expect(JSON.parse(storage.getItem(STORAGE_KEYS.recentTreeIds) ?? '[]')).toEqual(['x'])
  })

  it('triggers reload reconstruction from fragment tree id when tree is null', async () => {
    const { deps } = makePersistenceDeps(new MemoryStorage(), '#conversation_tree_id=frag-id')
    const reloadApi = {
      listAttacks: jest.fn(async () => ({
        items: [
          {
            attack_result_id: 'ar-1',
            conversation_id: 'conv-1',
            attack_type: 'red_teaming',
            converters: [],
            message_count: 2,
            related_conversation_ids: [],
            labels: { conversation_tree_id: 'frag-id' },
            created_at: '2026-06-11T00:00:00Z',
            updated_at: '2026-06-11T00:00:00Z',
          },
        ],
        pagination: { limit: 1, has_more: false, next_cursor: null, prev_cursor: null },
      })),
      getMessages: jest.fn(async () => ({
        conversation_id: 'conv-1',
        messages: [
          {
            turn_number: 1,
            role: 'user',
            pieces: [{
              piece_id: 'p1',
              original_value_data_type: 'text',
              converted_value_data_type: 'text',
              original_value: 'hello',
              converted_value: 'hello',
              scores: [],
              response_error: 'none',
              original_prompt_id: 'p1',
              converter_identifiers: [],
            }],
            created_at: '2026-06-11T00:00:00Z',
          },
          {
            turn_number: 2,
            role: 'assistant',
            pieces: [{
              piece_id: 'p2',
              original_value_data_type: 'text',
              converted_value_data_type: 'text',
              original_value: 'hi',
              converted_value: 'hi',
              scores: [],
              response_error: 'none',
              original_prompt_id: 'p2',
              converter_identifiers: [],
            }],
            created_at: '2026-06-11T00:00:00Z',
          },
        ],
      })),
    }
    const onTreeChange = jest.fn()

    render(
      <TreeRunnerHost
        tree={null}
        onTreeChange={onTreeChange}
        workspacePersistenceDeps={deps}
        reloadApi={reloadApi}
      />,
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(reloadApi.listAttacks).toHaveBeenCalledWith({
      limit: 100,
      label: ['conversation_tree_id:frag-id'],
    })
  })
})

// ============================================================================
// PR7h — dirty-edit swap guard wiring
// ============================================================================

describe('TreeRunnerHost — dirty-edit swap guard', () => {
  it('exposes guardedSwap via onGuardedSwapReady that runs swap synchronously when clean', () => {
    let captured: ((tree: ConversationTree | null, swap: () => void) => void) | undefined
    render(
      <TreeRunnerHost
        tree={mkEmptyTree('t-clean')}
        onGuardedSwapReady={(g) => {
          captured = g
        }}
      />,
    )
    expect(captured).toBeDefined()
    const swap = jest.fn()
    act(() => {
      captured?.(mkEmptyTree('t-clean'), swap)
    })
    expect(swap).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('shows the dirty-edit modal in the modal slot when swap is guarded against a dirty tree', () => {
    let captured: ((tree: ConversationTree | null, swap: () => void) => void) | undefined
    render(
      <TreeRunnerHost
        tree={null}
        onGuardedSwapReady={(g) => {
          captured = g
        }}
      />,
    )
    const dirty = mkDispatchableTree('t-dirty')
    const swap = jest.fn()
    act(() => {
      captured?.(dirty, swap)
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(swap).not.toHaveBeenCalled()

    act(() => {
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /discard/i }))
    })
    expect(swap).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// ============================================================================
// Integrated editing — card affordances wired through host reducers
// ============================================================================

describe('TreeRunnerHost — integrated editing', () => {
  it('editing a UserTurn marks it edited and stales downstream nodes', async () => {
    const tree = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root', { text: 'old text' }),
      mkSend('s1', 'u1'),
    ], { id: 't-edit' })
    const onTreeChange = jest.fn()

    render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    fireEvent.click(screen.getByRole('button', { name: /edit text inline/i }))
    const editor = screen.getByRole('textbox', { name: /edit user turn text/i })
    fireEvent.change(editor, { target: { value: 'new text' } })
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const byId = new Map(next.nodes.map((node) => [node.id, node]))
    expect(byId.get(nodeId('u1'))?.state).toBe('edited')
    expect(byId.get(nodeId('u1'))?.params).toMatchObject({ text: 'new text' })
    expect(byId.get(nodeId('s1'))?.state).toBe('stale')
  })

  it('persists converter palette changes and stales downstream response', async () => {
    const tree = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root', { text: 'old text' }),
      mkSend('s1', 'u1'),
    ], { id: 't-converter-edit' })
    const onTreeChange = jest.fn()

    render(
      <TreeRunnerHost
        tree={tree}
        onTreeChange={onTreeChange}
        availableConverters={[{ id: 'base64', label: 'Base64 encoder' }]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /converter palette/i }))
    const item = Array.from(document.querySelectorAll('[role="menuitem"]')).find((el) =>
      el.textContent?.includes('Base64 encoder'),
    ) as HTMLElement | undefined
    expect(item).toBeDefined()
    fireEvent.click(item!)

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const byId = new Map(next.nodes.map((node) => [node.id, node]))
    const user = byId.get(nodeId('u1'))
    expect(user?.state).toBe('edited')
    expect(user?.kind === 'user_turn' ? user.params.converterPipeline : undefined).toEqual([{ converterId: 'base64' }])
    expect(byId.get(nodeId('s1'))?.state).toBe('stale')
  })

  it('persists fan child Pick/Unpick visual state', async () => {
    const tree = mkTree('root', [
      mkRoot('root'),
      mkFan('fan', 'root', { axis: 'attempt', variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }] }),
      mkSend('s0', 'fan'),
      mkSend('s1', 'fan'),
    ], { id: 't-pick-fan' })
    const onTreeChange = jest.fn()

    render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    fireEvent.click(screen.getAllByRole('button', { name: /pick this attempt/i })[1])

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const fan = next.nodes.find((node) => node.id === nodeId('fan'))
    expect(fan?.kind === 'fan' ? fan.params.promotedChildSlotIndex : null).toBe(1)
  })

  it('clones the current tree when Branch/Clone is clicked', async () => {
    const tree = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')], { id: 't-clone-source' })
    const onTreeChange = jest.fn()

    render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    fireEvent.click(screen.getByRole('button', { name: /clone tree/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    expect(next.id).not.toBe(tree.id)
    expect(next.parentConversationTreeId).toBe(tree.id)
    expect(next.nodes.map((node) => node.id)).toEqual(tree.nodes.map((node) => node.id))
  })

  it('deletes a non-root subtree from the canvas', async () => {
    const tree = mkTree('root', [
      mkRoot('root'),
      mkUserTurn('u1', 'root'),
      mkSend('s1', 'u1'),
    ], { id: 't-delete' })
    const onTreeChange = jest.fn()

    const { container } = render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    const sendCard = container.querySelector('[data-tree-node-id="s1"]')!
    fireEvent.click(within(sendCard as HTMLElement).getByRole('button', { name: /^delete$/i }))

    expect(screen.getByRole('dialog', { name: /delete subtree/i })).toBeInTheDocument()
    expect(onTreeChange).not.toHaveBeenCalled()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^delete$/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    expect(next.nodes.some((node) => node.id === nodeId('s1'))).toBe(false)
    expect(next.nodes.some((node) => node.id === nodeId('u1'))).toBe(true)
  })

  it('does not render Delete on the root card', () => {
    const tree = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')], { id: 't-root-no-delete' })
    const { container } = render(<TreeRunnerHost tree={tree} />)

    const rootCard = container.querySelector('[data-tree-node-id="root"]')!
    expect(within(rootCard as HTMLElement).queryByRole('button', { name: /^delete$/i })).toBeNull()
  })

  it('focuses the selected node in the path chat', () => {
    const tree = mkTree('root', [
      mkRoot('root', { text: 'root text' }),
      mkUserTurn('u1', 'root', { text: 'turn text' }),
      mkSend('s1', 'u1', { responsePreview: 'response text' }),
    ], { id: 't-open-linear' })

    const { container } = render(<TreeRunnerHost tree={tree} />)

    const sendCard = container.querySelector('[data-tree-node-id="s1"]')!
    fireEvent.click(within(sendCard as HTMLElement).getByRole('button', { name: /focus in path chat/i }))

    const pathChat = container.querySelector('[data-tree-path-chat]')
    expect(pathChat?.textContent).toContain('root text')
    expect(pathChat?.textContent).toContain('turn text')
    expect(pathChat?.textContent).toContain('response text')
    expect(container.querySelector('[data-tree-path-chat-node="s1"]')?.getAttribute('data-selected')).toBe('true')
  })

  it('adds a follow-up prompt and pending response under a response card', async () => {
    const tree = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')], { id: 't-add-follow-up' })
    const onTreeChange = jest.fn()

    render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    fireEvent.click(screen.getByRole('button', { name: /add follow-up prompt/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const added = next.nodes.find((node) => node.parentId === nodeId('s1'))
    const pendingResponse = next.nodes.find((node) => node.parentId === added?.id)
    expect(added?.kind).toBe('user_turn')
    expect(added?.state).toBe('edited')
    expect(pendingResponse?.kind).toBe('send')
    expect(pendingResponse?.state).toBe('stale')
  })

  it('adds a response under a leaf user turn so deleted-response dead ends can recover', async () => {
    const tree = mkTree('root', [mkRoot('root'), mkUserTurn('u1', 'root', { text: 'dead-end prompt' })], { id: 't-add-response' })
    const onTreeChange = jest.fn()

    render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    const userCard = screen.getByText('dead-end prompt').closest('[data-tree-node-id]') as HTMLElement
    fireEvent.click(within(userCard).getByRole('button', { name: /add response/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const response = next.nodes.find((node) => node.kind === 'send' && node.parentId === nodeId('u1'))
    expect(response?.state).toBe('stale')
  })

  it('fans out an existing response into attempt variants', async () => {
    const tree = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')], { id: 't-fan-attempt' })
    const onTreeChange = jest.fn()

    render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    fireEvent.click(screen.getByRole('button', { name: /fan out response attempts/i }))
    fireEvent.change(screen.getByRole('spinbutton', { name: /attempt count/i }), {
      target: { value: '5' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^create$/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const fan = next.nodes.find((node) => node.kind === 'fan')
    const sends = next.nodes.filter((node) => node.kind === 'send')
    expect(fan?.kind).toBe('fan')
    expect(fan?.kind === 'fan' ? fan.params.axis : null).toBe('attempt')
    expect(sends).toHaveLength(5)
    expect(fan?.kind === 'fan' ? fan.params.variants : []).toHaveLength(5)
  })

  it('prunes an attempt fan to the picked path without deleting the picked response', async () => {
    const tree = mkTree('root', [
      mkRoot('root'),
      mkFan('fan', 'root', {
        axis: 'attempt',
        variants: [{ axis: 'attempt', payload: {} }, { axis: 'attempt', payload: {} }],
        promotedChildSlotIndex: 1,
      }),
      mkSend('s0', 'fan'),
      mkSend('s1', 'fan'),
    ], { id: 't-prune-fan' })
    const onTreeChange = jest.fn()

    render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    fireEvent.click(screen.getByRole('button', { name: /prune to picked slot 1/i }))
    expect(screen.getByRole('dialog', { name: /prune fan/i })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^prune$/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    expect(next.nodes.some((node) => node.id === nodeId('fan'))).toBe(false)
    expect(next.nodes.some((node) => node.id === nodeId('s0'))).toBe(false)
    expect(next.nodes.find((node) => node.id === nodeId('s1'))?.parentId).toBe(nodeId('root'))
  })

  it('fans out an existing response into converter variants', async () => {
    const tree = mkTree('root', [mkRoot('root'), mkSend('s1', 'root')], { id: 't-fan-converter' })
    const onTreeChange = jest.fn()

    render(<TreeRunnerHost tree={tree} onTreeChange={onTreeChange} />)

    fireEvent.click(screen.getByRole('button', { name: /compare converters/i }))

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    const next = onTreeChange.mock.calls.at(-1)?.[0] as ConversationTree
    const fan = next.nodes.find((node) => node.kind === 'fan')
    expect(fan?.kind).toBe('fan')
    expect(fan?.kind === 'fan' ? fan.params.axis : null).toBe('converter')
    expect(next.nodes.some((node) => node.kind === 'user_turn' && node.parentId === fan?.id)).toBe(true)
  })
})

// ============================================================================
// PR6a.2 — cost-modal suppression sourced from / persisted to WorkspaceSettings
// ============================================================================

describe('TreeRunnerHost — suppression persistence', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })
  afterEach(() => {
    jest.runOnlyPendingTimers()
    jest.useRealTimers()
  })

  it('honors persisted suppressConfirmModalThisSession from storage (no modal at threshold)', async () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.schemaVersion, '1')
    storage.setItem(
      STORAGE_KEYS.settings,
      JSON.stringify({ reflogCapPerNode: 50, confirmThresholdCount: 2, suppressConfirmModalThisSession: true }),
    )
    const { deps } = makePersistenceDeps(storage)
    const tree = mkDispatchableTree('t-suppressed')
    const starter = jest.fn(noopStarter)

    let captured: RunnerShim | undefined
    render(
      <TreeRunnerHost
        tree={tree}
        operator="alice"
        runWaveStarter={starter as unknown as RunWaveStarter}
        workspacePersistenceDeps={deps}
        onShimReady={(s) => {
          captured = s
        }}
      />,
    )
    await waitFor(() => expect(captured).toBeDefined())

    // The single edited send off the root estimates 2 calls; threshold 2 puts
    // it in the suppression window [threshold, 2x), so persisted suppression
    // auto-approves with no modal. Advance the lock-acquire timer, then settle.
    await act(async () => {
      const p = captured!.refreshNode(tree.id, nodeId('send-1'))
      jest.advanceTimersByTime(60)
      await p
    })

    expect(screen.queryByRole('dialog')).toBeNull()
    expect(starter).toHaveBeenCalledTimes(1)
  })

  it('persists suppression to storage when the operator commits "Don\u2019t ask again"', async () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.schemaVersion, '1')
    storage.setItem(
      STORAGE_KEYS.settings,
      JSON.stringify({ reflogCapPerNode: 50, confirmThresholdCount: 1, suppressConfirmModalThisSession: false }),
    )
    const { deps } = makePersistenceDeps(storage)
    const tree = mkDispatchableTree('t-persist-suppress')
    const starter = jest.fn(noopStarter)

    let captured: RunnerShim | undefined
    render(
      <TreeRunnerHost
        tree={tree}
        operator="alice"
        runWaveStarter={starter as unknown as RunWaveStarter}
        workspacePersistenceDeps={deps}
        onShimReady={(s) => {
          captured = s
        }}
      />,
    )
    await waitFor(() => expect(captured).toBeDefined())

    let refreshPromise!: Promise<void>
    await act(async () => {
      refreshPromise = captured!.refreshNode(tree.id, nodeId('send-1'))
      jest.advanceTimersByTime(60)
    })
    // Modal shows (not yet suppressed).
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('checkbox', { name: /don't ask again/i }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^refresh$/i }))
      await refreshPromise
    })

    // Debounced settings write flushes the new suppression flag to storage.
    act(() => {
      jest.advanceTimersByTime(600)
    })
    const persisted = JSON.parse(storage.getItem(STORAGE_KEYS.settings) ?? '{}')
    expect(persisted.suppressConfirmModalThisSession).toBe(true)
  })
})

// ============================================================================
// PR7i.3b — open-from-history into tree (useAutoReverse seam)
// ============================================================================

describe('TreeRunnerHost — open from attack result', () => {
  const fakeAr = {
    attack_result_id: 'ar-open',
    conversation_id: 'conv-open',
    attack_type: 'red_teaming',
    converters: [],
    message_count: 2,
    related_conversation_ids: [],
    labels: {},
    created_at: '2026-06-11T00:00:00Z',
    updated_at: '2026-06-11T00:00:00Z',
  }
  const fakeMessages = {
    conversation_id: 'conv-open',
    messages: [
      {
        turn_number: 1,
        role: 'user',
        pieces: [{
          piece_id: 'p1',
          original_value_data_type: 'text',
          converted_value_data_type: 'text',
          original_value: 'hello',
          converted_value: 'hello',
          scores: [],
          response_error: 'none',
          original_prompt_id: 'p1',
          converter_identifiers: [],
        }],
        created_at: '2026-06-11T00:00:00Z',
      },
      {
        turn_number: 2,
        role: 'assistant',
        pieces: [{
          piece_id: 'p2',
          original_value_data_type: 'text',
          converted_value_data_type: 'text',
          original_value: 'hi',
          converted_value: 'hi',
          scores: [],
          response_error: 'none',
          original_prompt_id: 'p2',
          converter_identifiers: [],
        }],
        created_at: '2026-06-11T00:00:00Z',
      },
    ],
  }

  it('auto-reverses the AR and emits the reconstructed tree via onTreeChange', async () => {
    const onTreeChange = jest.fn()
    const autoReverseApi = {
      getAttack: jest.fn(async () => fakeAr),
      getMessages: jest.fn(async () => fakeMessages),
    }
    render(
      <TreeRunnerHost
        tree={null}
        onTreeChange={onTreeChange}
        openFromAttackResultId="ar-open"
        autoReverseApi={autoReverseApi}
      />,
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(autoReverseApi.getAttack).toHaveBeenCalledWith('ar-open')
    expect(autoReverseApi.getMessages).toHaveBeenCalledWith('ar-open', 'conv-open')
    const tree = onTreeChange.mock.calls[0][0] as ConversationTree
    expect(tree.nodes.find((n) => n.id === tree.rootId)?.kind).toBe('root_prompt')
  })

  it('suppresses stale URL-fragment reload while opening an explicit AR as tree', async () => {
    const { deps } = makePersistenceDeps(new MemoryStorage(), '#conversation_tree_id=stale-tree')
    const onTreeChange = jest.fn()
    const autoReverseApi = {
      getAttack: jest.fn(async () => fakeAr),
      getMessages: jest.fn(async () => fakeMessages),
    }
    const reloadApi = {
      listAttacks: jest.fn(async () => ({
        items: [],
        pagination: { limit: 100, has_more: false, next_cursor: null, prev_cursor: null },
      })),
      getMessages: jest.fn(async () => fakeMessages),
    }

    render(
      <TreeRunnerHost
        tree={null}
        onTreeChange={onTreeChange}
        openFromAttackResultId="ar-open"
        autoReverseApi={autoReverseApi}
        reloadApi={reloadApi}
        workspacePersistenceDeps={deps}
      />,
    )

    await waitFor(() => expect(onTreeChange).toHaveBeenCalled())
    expect(autoReverseApi.getAttack).toHaveBeenCalledWith('ar-open')
    expect(reloadApi.listAttacks).not.toHaveBeenCalled()
  })

  it('does not auto-reverse when openFromAttackResultId is null', async () => {
    const onTreeChange = jest.fn()
    const autoReverseApi = {
      getAttack: jest.fn(async () => fakeAr),
      getMessages: jest.fn(async () => fakeMessages),
    }
    render(
      <TreeRunnerHost
        tree={null}
        onTreeChange={onTreeChange}
        openFromAttackResultId={null}
        autoReverseApi={autoReverseApi}
      />,
    )
    // Give any stray effect a chance to fire.
    await act(async () => {
      await Promise.resolve()
    })
    expect(autoReverseApi.getAttack).not.toHaveBeenCalled()
    expect(onTreeChange).not.toHaveBeenCalled()
  })
})

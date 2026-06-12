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

import { TreeRunnerHost } from './TreeRunnerHost'
import type { ConversationTree } from '../../runner/treeTypes'
import type { RunnerShim, RunWaveStarter, RunWaveStarterArgs } from '../../runner/shim'
import type { WorkspacePersistenceDeps } from './useWorkspacePersistence'
import type { WaveSummary } from '../../runner/wave'
import { mkRoot, mkSend, mkTree, nodeId, treeId } from '../../runner/testHelpers'
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
// Layout — 5 named slots (PR7b)
// ============================================================================

describe('TreeRunnerHost — layout slots', () => {
  it('renders all 5 named slots (ribbon, canvas, drawer, toast, modal)', () => {
    const { container } = render(<TreeRunnerHost tree={null} />)
    expect(container.querySelector('[data-tree-runner-host]')).not.toBeNull()
    expect(container.querySelector('[data-slot="ribbon"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="canvas"]')).not.toBeNull()
    expect(container.querySelector('[data-slot="drawer"]')).not.toBeNull()
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
      limit: 200,
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

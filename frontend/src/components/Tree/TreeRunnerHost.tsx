// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * TreeRunnerHost — PR7c.2 wires the runner shim into the layout shell
 * from PR7b. PR7d adds the cost-guardrail modal — every wave whose
 * estimated call count meets the threshold (default 20 per spec §13.1)
 * surfaces the modal in the host's modal slot, gating wave start on
 * the operator's confirm/cancel.
 *
 * The host now owns:
 *
 *   - the runner shim (instantiated once via useState lazy initializer)
 *   - the cross-tab lock manager (instantiated once via useState lazy init)
 *   - a WaveEvent buffer (useState) that the sink appends to
 *   - the cost-guardrail hook + modal element (PR7d)
 *   - a stable `RunnerStateSink` implementation that closes over
 *     refs so its identity survives re-renders
 *   - default `actionCallbacks.onRefresh` wired to `shim.refreshNode`
 *   - ribbon `onCancelWave` / `onCancelQueued` wired to the shim
 *
 * The host stays controlled: the `tree` prop is parent-owned; the
 * sink's tree mutations propagate via `onTreeChange` so the parent
 * can re-render with the next ConversationTree.
 *
 * Five named slots from PR7b: ribbon / canvas / drawer / toast / modal.
 * Drawer + toast slots stay empty in PR7d; PR7e–h fill them.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import { TreeCanvas } from './TreeCanvas'
import { WaveStatusRibbon } from './WaveStatusRibbon'
import { summarizeWaveEvents } from './waveStatus'
import { appendWaveEvent } from './waveStatus'
import { useCostGuardrailModal } from './useCostGuardrailModal'
import { useDirtyEditModal } from './useDirtyEditModal'
import { useWorkspacePersistence, type WorkspacePersistenceDeps } from './useWorkspacePersistence'
import { useReloadReconstruction, type ReloadReconstructionApi } from './useReloadReconstruction'
import { useTreeRunnerHostStyles } from './TreeRunnerHost.styles'
import type { ActionCallbacks } from './actionRail'
import type { AvailableConvertersValue } from './availableConvertersContext'
import {
  applyClearExecution,
  applyRecordExecution,
  applySetNodeState,
  applySetReflogPinned,
} from '../../runner/treeStateReducer'
import {
  createBroadcastChannelLockManager,
} from '../../runner/crossTabLock'
import { createRunnerShim, type RunWaveStarter, type RunnerShim } from '../../runner/shim'
import type {
  ConversationTree,
  ConversationTreeId,
  ConversationTreeNodeId,
  CostGuardrail,
  RunnerStateSink,
  WaveEvent,
  WorkspaceSettings,
} from '../../runner/treeTypes'
import type { WaveSummary } from '../../runner/wave'
import { DEFAULT_WORKSPACE_SETTINGS } from '../../runner/workspacePersistence'
import { attacksApi } from '../../services/api'

// ============================================================================
// Public surface
// ============================================================================

export interface TreeRunnerHostProps {
  /** Foregrounded tree; `null` renders the greenfield placeholder. */
  tree: ConversationTree | null
  /** Operator tag; gates the shim's wave-start step (spec 03 §2.1 step 1). */
  operator?: string | null
  /**
   * Production wave dispatcher. App.tsx supplies a closure over `runWave` +
   * the real attacksApi. When omitted (tests + standalone mounts), defaults
   * to a no-op that returns an empty summary; the shim still emits start +
   * complete bookending events.
   */
  runWaveStarter?: RunWaveStarter
  /**
   * Sink mutations (setNodeState / recordExecution / clearExecution /
   * setReflogPinned) flow through here. Caller owns `tree` state and
   * re-passes the updated tree on the next render.
   */
  onTreeChange?: (tree: ConversationTree) => void
  /** Per-node reflog cap; default 50 per spec §13.1 WorkspaceSettings. */
  reflogCap?: number
  /**
   * Cost-guardrail threshold — waves at or above this estimated call count
   * surface the confirm modal (spec §8.1). Default 20 per spec §13.1.
   */
  confirmThresholdCount?: number
  /**
   * Fired once after the shim is constructed. Tests use this to capture
   * the shim and trigger flows directly. Production callers usually
   * ignore it.
   */
  onShimReady?: (shim: RunnerShim) => void
  /** Pass-through to TreeCanvas. */
  actionCallbacks?: ActionCallbacks
  /** Pass-through to TreeCanvas. */
  availableConverters?: AvailableConvertersValue
  /** Recent tree id stack persisted to sessionStorage (PR7f). */
  workspaceRecentTreeIds?: ConversationTreeId[]
  /** Workspace settings persisted to sessionStorage (PR7f). */
  workspaceSettings?: WorkspaceSettings
  /** Test-only override for persistence browser deps (PR7f.2 tests). */
  workspacePersistenceDeps?: WorkspacePersistenceDeps
  /** Test-only override for reload reconstruction API (PR7g tests). */
  reloadApi?: ReloadReconstructionApi
  /**
   * Fired once with the dirty-edit `guardedSwap(tree, swap)` (PR7h). The
   * host wires App's openTree / newTree / closeTree through it so an
   * in-app tree swap with unrefreshed edits prompts the confirm modal.
   * `branchToNewTree` is exempt per spec §13.1.
   */
  onGuardedSwapReady?: (guardedSwap: (tree: ConversationTree | null, swap: () => void) => void) => void
}

// ============================================================================
// Defaults
// ============================================================================

const EMPTY_SUMMARY: WaveSummary = {
  succeeded: 0,
  failed: { transient: 0, rate_limited: 0, permanent: 0 },
  blocked: 0,
  cancelled: 0,
  reflog_evicted: 0,
}

const DEFAULT_RUN_WAVE_STARTER: RunWaveStarter = async () => EMPTY_SUMMARY
const DEFAULT_CONFIRM_THRESHOLD = 20

// ============================================================================
// Component
// ============================================================================

export function TreeRunnerHost({
  tree,
  operator,
  runWaveStarter,
  onTreeChange,
  reflogCap,
  confirmThresholdCount,
  onShimReady,
  actionCallbacks,
  availableConverters,
  workspaceRecentTreeIds,
  workspaceSettings,
  workspacePersistenceDeps,
  reloadApi,
  onGuardedSwapReady,
}: TreeRunnerHostProps) {
  const styles = useTreeRunnerHostStyles()
  const [waveEvents, setWaveEvents] = useState<WaveEvent[]>([])

  // Cost-guardrail hook owns the modal's pending-decision state + the
  // session suppression flag. PR7f rewires suppression to sessionStorage.
  const { guardrail, modalElement } = useCostGuardrailModal({
    confirmThresholdCount: confirmThresholdCount ?? DEFAULT_CONFIRM_THRESHOLD,
  })

  // PR7h: in-app tree-swap guard (spec §13.1a). The host exposes
  // guardedSwap to App via onGuardedSwapReady; its modal renders in the
  // modal slot alongside the cost-guardrail modal.
  const { guardedSwap, modalElement: dirtyEditModalElement } = useDirtyEditModal()
  const onGuardedSwapReadyRef = useRef(onGuardedSwapReady)
  useEffect(() => {
    onGuardedSwapReadyRef.current = onGuardedSwapReady
  }, [onGuardedSwapReady])
  useEffect(() => {
    onGuardedSwapReadyRef.current?.(guardedSwap)
  }, [guardedSwap])

  // PR7f.2: schema-versioned sessionStorage + URL fragment sync +
  // beforeunload dirty-edit guard.
  const { boot } = useWorkspacePersistence({
    tree,
    recentTreeIds: workspaceRecentTreeIds ?? [],
    settings: workspaceSettings ?? DEFAULT_WORKSPACE_SETTINGS,
    deps: workspacePersistenceDeps,
  })

  useReloadReconstruction({
    fragmentTreeId: boot.treeIdFromFragment,
    currentTree: tree,
    onTreeChange,
    reloadApi: reloadApi ?? attacksApi,
  })

  // Refs hold the latest prop values so the sink + shim deps (constructed
  // once) read live values via .current rather than stale closure captures.
  // Initialized via useRef and updated post-commit via useEffect to comply
  // with the react-hooks/refs rule (no writes during render).
  const treeRef = useRef(tree)
  const operatorRef = useRef<string | null>(operator ?? null)
  const onTreeChangeRef = useRef(onTreeChange)
  const reflogCapRef = useRef(reflogCap)
  const costGuardrailRef = useRef<CostGuardrail>(guardrail)
  const runWaveStarterRef = useRef<RunWaveStarter>(runWaveStarter ?? DEFAULT_RUN_WAVE_STARTER)
  useEffect(() => {
    treeRef.current = tree
  }, [tree])
  useEffect(() => {
    operatorRef.current = operator ?? null
  }, [operator])
  useEffect(() => {
    onTreeChangeRef.current = onTreeChange
  }, [onTreeChange])
  useEffect(() => {
    reflogCapRef.current = reflogCap
  }, [reflogCap])
  useEffect(() => {
    costGuardrailRef.current = guardrail
  }, [guardrail])
  useEffect(() => {
    runWaveStarterRef.current = runWaveStarter ?? DEFAULT_RUN_WAVE_STARTER
  }, [runWaveStarter])

  // Lock manager + sink + shim constructed once via useState lazy
  // initializers (stable, no useRef-during-render writes).
  const [lockManager] = useState(() => createBroadcastChannelLockManager())
  useEffect(() => () => lockManager.close(), [lockManager])

  const [sink] = useState<RunnerStateSink>(() => ({
    setNodeState: (treeIdArg, nodeIdArg, state, opts) => {
      const current = treeRef.current
      if (current === null || current.id !== treeIdArg) return
      const next = applySetNodeState(current, nodeIdArg, state, opts ?? {})
      if (next !== current) onTreeChangeRef.current?.(next)
    },
    recordExecution: (treeIdArg, nodeIdArg, record) => {
      const current = treeRef.current
      if (current === null || current.id !== treeIdArg) return
      const next = applyRecordExecution(current, nodeIdArg, record, {
        reflogCap: reflogCapRef.current,
      })
      if (next !== current) onTreeChangeRef.current?.(next)
    },
    clearExecution: (treeIdArg, nodeIdArg) => {
      const current = treeRef.current
      if (current === null || current.id !== treeIdArg) return
      const next = applyClearExecution(current, nodeIdArg)
      if (next !== current) onTreeChangeRef.current?.(next)
    },
    setReflogPinned: (treeIdArg, nodeIdArg, executionId, pinned) => {
      const current = treeRef.current
      if (current === null || current.id !== treeIdArg) return
      const next = applySetReflogPinned(current, nodeIdArg, executionId, pinned)
      if (next !== current) onTreeChangeRef.current?.(next)
    },
    emitWaveEvent: (event) => {
      setWaveEvents((prev) => appendWaveEvent(prev, event))
    },
  }))

  // The shim's closures here read refs LAZILY (when shim methods are called
  // by user interactions, not during render). The react-hooks/refs rule is
  // conservative — it flags `ref.current` mentioned in any expression
  // evaluated during render, even inside a closure body that doesn't run
  // until later. Disable for this construction site only.
  /* eslint-disable react-hooks/refs */
  const [shim] = useState<RunnerShim>(() =>
    createRunnerShim({
      operatorProvider: () => operatorRef.current,
      treeProvider: (id) => {
        const t = treeRef.current
        return t !== null && t.id === id ? t : undefined
      },
      sink,
      lockManager,
      costGuardrail: {
        approve: (count, kind) => costGuardrailRef.current.approve(count, kind),
      },
      runWaveStarter: (args) => runWaveStarterRef.current(args),
      uuid: () => crypto.randomUUID(),
    }),
  )
  /* eslint-enable react-hooks/refs */

  // Fire onShimReady once on mount so tests can grab the shim.
  useEffect(() => {
    onShimReady?.(shim)
    // onShimReady is meant to fire once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Compose action callbacks: default onRefresh routes to shim.refreshNode
  // for whichever tree is current. Host-supplied callbacks (if any) win.
  const composedActionCallbacks = useMemo<ActionCallbacks | undefined>(() => {
    if (tree === null) return actionCallbacks
    const treeIdForCallbacks: ConversationTreeId = tree.id
    const defaults: ActionCallbacks = {
      onRefresh: (nodeIdArg: ConversationTreeNodeId) => {
        void shim.refreshNode(treeIdForCallbacks, nodeIdArg)
      },
    }
    if (actionCallbacks === undefined) return defaults
    return { ...defaults, ...actionCallbacks }
  }, [actionCallbacks, tree, shim])

  const ribbonState = useMemo(() => summarizeWaveEvents(waveEvents), [waveEvents])

  const onCancelWave = useMemo(() => {
    if (tree === null) return undefined
    const treeIdForCancel = tree.id
    return () => {
      void shim.cancelWave(treeIdForCancel)
    }
  }, [tree, shim])

  const onCancelQueued = useMemo(() => {
    if (tree === null) return undefined
    const treeIdForCancel = tree.id
    return () => {
      void shim.cancelQueued(treeIdForCancel)
    }
  }, [tree, shim])

  return (
    <div data-tree-runner-host className={styles.root}>
      <div data-slot="ribbon" className={styles.ribbon}>
        <WaveStatusRibbon
          state={ribbonState}
          onCancelWave={onCancelWave}
          onCancelQueued={onCancelQueued}
        />
      </div>
      <div data-slot="canvas" className={styles.canvas}>
        {tree !== null ? (
          // Re-key on tree.id so react-flow's internal zoom/pan state
          // resets across tree swaps. TreeCanvas's own collapse state
          // is already re-keyed internally.
          <TreeCanvas
            key={tree.id}
            tree={tree}
            actionCallbacks={composedActionCallbacks}
            availableConverters={availableConverters}
          />
        ) : (
          <div data-tree-greenfield className={styles.greenfield}>
            <p>No tree loaded. Open one from history or start a new attack.</p>
          </div>
        )}
      </div>
      <div data-slot="drawer" className={styles.drawer} />
      <div data-slot="toast" className={styles.toast} />
      <div data-slot="modal" className={styles.modal}>
        {modalElement}
        {dirtyEditModalElement}
      </div>
    </div>
  )
}


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
import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from '@fluentui/react-components'

import { TreeCanvas } from './TreeCanvas'
import { WaveStatusRibbon } from './WaveStatusRibbon'
import { summarizeWaveEvents } from './waveStatus'
import { appendWaveEvent } from './waveStatus'
import { useCostGuardrailModal } from './useCostGuardrailModal'
import { useDirtyEditModal } from './useDirtyEditModal'
import { useWorkspacePersistence, type WorkspacePersistenceDeps } from './useWorkspacePersistence'
import {
  useReloadReconstruction,
  type ReloadReconstructionApi,
  type ReconstructionDegradedInfo,
} from './useReloadReconstruction'
import { useAutoReverse, type UseAutoReverseApi } from './useAutoReverse'
import { useTreeRunnerHostStyles } from './TreeRunnerHost.styles'
import type { ActionCallbacks, AppendChildKind, EdgeInsertKind } from './actionRail'
import type { AvailableConvertersValue } from './availableConvertersContext'
import {
  applyClearExecution,
  applyAppendChild,
  applyCloneTree,
  applyEditRootPromptParams,
  applyEditUserTurnText,
  applyDeleteSubtree,
  applyInsertBetween,
  applyWrapWithFan,
  applyRecordExecution,
  applySetFanPromotedChild,
  applySetNodeState,
  applySetReflogPinned,
  applySetUserTurnConverterPipeline,
} from '../../runner/treeStateReducer'
import {
  createBroadcastChannelLockManager,
} from '../../runner/crossTabLock'
import { estimateRefreshCost } from '../../runner/estimateRefreshCost'
import { buildSForSubtree } from '../../runner/readiness'
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
import { loadWorkspaceFromStorage, wipeIfSchemaMismatch } from '../../runner/workspacePersistence'
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
   * Fired when reload reconstructs a tree that had fan topology as a
   * linear chain (slice-1 limitation). The host forwards it so App can
   * surface an operator banner. Removed when PR7g slice 2 lands
   * fan-aware reload.
   */
  onReconstructionDegraded?: (info: ReconstructionDegradedInfo) => void
  /**
   * Fired once with the dirty-edit `guardedSwap(tree, swap)` (PR7h). The
   * host wires App's openTree / newTree / closeTree through it so an
   * in-app tree swap with unrefreshed edits prompts the confirm modal.
   * `branchToNewTree` is exempt per spec §13.1.
   */
  onGuardedSwapReady?: (guardedSwap: (tree: ConversationTree | null, swap: () => void) => void) => void
  /**
   * One-shot "Open as tree" signal (spec §5.12 / §13.1
   * openTreeFromAttackResult). When set, the host linearly auto-reverses
   * that AR into a ConversationTree and emits it via onTreeChange. V1.0
   * ships linear+converter reconstruction (fanout detection is V1.1).
   */
  openFromAttackResultId?: string | null
  /** Test-only override for the auto-reverse API (PR7i.3b tests). */
  autoReverseApi?: UseAutoReverseApi
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
  onReconstructionDegraded,
  onGuardedSwapReady,
  openFromAttackResultId,
  autoReverseApi,
}: TreeRunnerHostProps) {
  const styles = useTreeRunnerHostStyles()
  const [waveEvents, setWaveEvents] = useState<WaveEvent[]>([])
  const [linearNodeId, setLinearNodeId] = useState<ConversationTreeNodeId | null>(null)
  const [pendingDeleteNodeId, setPendingDeleteNodeId] = useState<ConversationTreeNodeId | null>(null)

  // Host-owned live WorkspaceSettings (spec §13.1): seeds the cost-modal
  // suppression + the reflog cap, and is persisted to sessionStorage by
  // useWorkspacePersistence's debounced write. Seeded from the injected
  // settings prop (tests) or a direct storage load. The schema wipe is run
  // first and is idempotent with useWorkspacePersistence's own boot wipe.
  const settingsStorage = workspacePersistenceDeps?.storage ?? window.sessionStorage
  const [settings, setSettings] = useState<WorkspaceSettings>(() => {
    if (workspaceSettings !== undefined) return workspaceSettings
    wipeIfSchemaMismatch(settingsStorage)
    return loadWorkspaceFromStorage(settingsStorage).settings
  })

  // Cost-guardrail hook: suppression is sourced from (and committed back to)
  // WorkspaceSettings so it survives reload via sessionStorage (PR6a.2).
  const { guardrail, modalElement, isPending: isCostModalPending } = useCostGuardrailModal({
    confirmThresholdCount: confirmThresholdCount ?? settings.confirmThresholdCount,
    suppressed: settings.suppressConfirmModalThisSession,
    onChangeSuppressed: (next) =>
      setSettings((s) => ({ ...s, suppressConfirmModalThisSession: next })),
  })

  // PR7h: in-app tree-swap guard (spec §13.1a). The host exposes
  // guardedSwap to App via onGuardedSwapReady; its modal renders in the
  // modal slot alongside the cost-guardrail modal.
  const { guardedSwap, modalElement: dirtyEditModalElement, isPending: isDirtyModalPending } =
    useDirtyEditModal({ blocked: isCostModalPending })
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
    settings,
    deps: workspacePersistenceDeps,
  })

  useReloadReconstruction({
    fragmentTreeId: openFromAttackResultId ? null : boot.treeIdFromFragment,
    currentTree: tree,
    onTreeChange,
    onReconstructionDegraded,
    reloadApi: reloadApi ?? attacksApi,
  })

  // PR7i.3b: "Open as tree" (spec §5.12). Linearly auto-reverse the AR; the
  // result is pushed via onTreeChange in an effect below (after the
  // onTreeChangeRef mirror is declared), applied once per reconstructed AR id.
  const autoReversed = useAutoReverse(openFromAttackResultId ?? null, {
    attacksApi: autoReverseApi ?? attacksApi,
  })

  // Refs hold the latest prop values so the sink + shim deps (constructed
  // once) read live values via .current rather than stale closure captures.
  // Initialized via useRef and updated post-commit via useEffect to comply
  // with the react-hooks/refs rule (no writes during render).
  const treeRef = useRef(tree)
  const operatorRef = useRef<string | null>(operator ?? null)
  const onTreeChangeRef = useRef(onTreeChange)
  const lastOpenedTreeIdRef = useRef<string | null>(null)
  // Effective per-node reflog cap: the explicit `reflogCap` override wins
  // (tests), else the host-owned WorkspaceSettings.reflogCapPerNode (§13.1).
  const effectiveReflogCap = reflogCap ?? settings.reflogCapPerNode
  const reflogCapRef = useRef(effectiveReflogCap)
  const costGuardrailRef = useRef<CostGuardrail>(guardrail)
  const dirtyModalPendingRef = useRef(isDirtyModalPending)
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
  // Push the auto-reversed "Open as tree" result via onTreeChange, once per
  // reconstructed AR id (the inline onTreeChange re-fires each render, so we
  // gate on the tree id to avoid re-pushing the same reconstruction).
  useEffect(() => {
    const opened = autoReversed.tree
    if (opened !== null && opened.id !== lastOpenedTreeIdRef.current) {
      lastOpenedTreeIdRef.current = opened.id
      onTreeChangeRef.current?.(opened)
    }
  }, [autoReversed.tree])
  useEffect(() => {
    reflogCapRef.current = effectiveReflogCap
  }, [effectiveReflogCap])
  useEffect(() => {
    costGuardrailRef.current = guardrail
  }, [guardrail])
  useEffect(() => {
    dirtyModalPendingRef.current = isDirtyModalPending
  }, [isDirtyModalPending])
  useEffect(() => {
    runWaveStarterRef.current = runWaveStarter ?? DEFAULT_RUN_WAVE_STARTER
  }, [runWaveStarter])

  // Lock manager + sink + shim constructed once via useState lazy
  // initializers (stable, no useRef-during-render writes).
  const [lockManager] = useState(() => createBroadcastChannelLockManager())
  const lockCleanupTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const [sink] = useState<RunnerStateSink>(() => ({
    setNodeState: (treeIdArg, nodeIdArg, state, opts) => {
      const current = treeRef.current
      if (current === null || current.id !== treeIdArg) return
      const next = applySetNodeState(current, nodeIdArg, state, opts ?? {})
      if (next !== current) {
        treeRef.current = next
        onTreeChangeRef.current?.(next)
      }
    },
    recordExecution: (treeIdArg, nodeIdArg, record) => {
      const current = treeRef.current
      if (current === null || current.id !== treeIdArg) return
      const next = applyRecordExecution(current, nodeIdArg, record, {
        reflogCap: reflogCapRef.current,
      })
      if (next !== current) {
        treeRef.current = next
        onTreeChangeRef.current?.(next)
      }
    },
    clearExecution: (treeIdArg, nodeIdArg) => {
      const current = treeRef.current
      if (current === null || current.id !== treeIdArg) return
      const next = applyClearExecution(current, nodeIdArg)
      if (next !== current) {
        treeRef.current = next
        onTreeChangeRef.current?.(next)
      }
    },
    setReflogPinned: (treeIdArg, nodeIdArg, executionId, pinned) => {
      const current = treeRef.current
      if (current === null || current.id !== treeIdArg) return
      const next = applySetReflogPinned(current, nodeIdArg, executionId, pinned)
      if (next !== current) {
        treeRef.current = next
        onTreeChangeRef.current?.(next)
      }
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
        approve: (count, kind) => {
          if (dirtyModalPendingRef.current) {
            console.error(
              'TreeRunnerHost: cost guardrail rejected — dirty-edit modal is already pending',
            )
            return Promise.resolve(false)
          }
          return costGuardrailRef.current.approve(count, kind)
        },
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

  // Auto-cancel the prior tree's in-flight wave when the foregrounded tree
  // swaps. Without this, a wave running on tree A keeps running after a
  // swap to tree B — its sink writes silently drop (id mismatch) and the
  // ribbon Cancel button can no longer reach it (it's bound to B's id).
  // cancelWave is a clean no-op when the prior tree has no active wave.
  const prevTreeIdRef = useRef<ConversationTreeId | null>(tree?.id ?? null)
  useEffect(() => {
    const prev = prevTreeIdRef.current
    const curr = tree?.id ?? null
    if (prev !== null && prev !== curr) {
      void shim.cancelWave(prev)
    }
    prevTreeIdRef.current = curr
  }, [tree?.id, shim])
  useEffect(
    () => {
      if (lockCleanupTimerRef.current !== null) {
        clearTimeout(lockCleanupTimerRef.current)
        lockCleanupTimerRef.current = null
      }
      return () => {
        lockCleanupTimerRef.current = setTimeout(() => {
          lockCleanupTimerRef.current = null
          const activeTreeId = treeRef.current?.id ?? prevTreeIdRef.current
          if (activeTreeId === null) {
            lockManager.close()
            return
          }
          void shim.cancelWave(activeTreeId).finally(() => lockManager.close())
        }, 0)
      }
    },
    [lockManager, shim],
  )

  // Compose action callbacks: default onRefresh routes to shim.refreshNode
  // for whichever tree is current. Host-supplied callbacks (if any) win.
  const composedActionCallbacks = useMemo<ActionCallbacks | undefined>(() => {
    if (tree === null) return actionCallbacks
    const treeIdForCallbacks: ConversationTreeId = tree.id
    const defaults: ActionCallbacks = {
      onRefresh: (nodeIdArg: ConversationTreeNodeId) => {
        void shim.refreshSubtree(treeIdForCallbacks, nodeIdArg)
      },
      getRefreshCost: (nodeIdArg) => {
        const current = treeRef.current
        if (current === null) return { calls: 0, leaves: 0 }
        return estimateRefreshCost(current, buildSForSubtree(current, nodeIdArg))
      },
      onEditUserTurnText: (nodeIdArg, newText) => {
        const current = treeRef.current
        if (current === null) return
        const next = applyEditUserTurnText(current, nodeIdArg, newText)
        if (next !== current) {
          treeRef.current = next
          onTreeChangeRef.current?.(next)
        }
      },
      onEditRootPromptParams: (nodeIdArg, patch) => {
        const current = treeRef.current
        if (current === null) return
        const next = applyEditRootPromptParams(current, nodeIdArg, patch)
        if (next !== current) {
          treeRef.current = next
          onTreeChangeRef.current?.(next)
        }
      },
      onAppendChild: (parentIdArg, kind) => {
        const current = treeRef.current
        if (current === null) return
        const next = applyAppendChild(current, parentIdArg, kind, () => crypto.randomUUID())
        if (next !== current) {
          treeRef.current = next
          onTreeChangeRef.current?.(next)
        }
      },
      onCreateFanFromNode: (nodeIdArg, axis) => {
        const current = treeRef.current
        const node = current?.nodes.find((candidate) => candidate.id === nodeIdArg)
        if (current === null || node === undefined || node.parentId === null) return
        const next = applyWrapWithFan(current, node.parentId, nodeIdArg, axis, () => crypto.randomUUID())
        if (next !== current) {
          treeRef.current = next
          onTreeChangeRef.current?.(next)
        }
      },
      onEdgeInsert: (parentIdArg, childIdArg, kind) => {
        const current = treeRef.current
        if (current === null) return
        const next = applyEdgeInsert(current, parentIdArg, childIdArg, kind, () => crypto.randomUUID())
        if (next !== current) {
          treeRef.current = next
          onTreeChangeRef.current?.(next)
        }
      },
      onSetUserTurnConverterPipeline: (nodeIdArg, pipeline) => {
        const current = treeRef.current
        if (current === null) return
        const next = applySetUserTurnConverterPipeline(current, nodeIdArg, pipeline)
        if (next !== current) {
          treeRef.current = next
          onTreeChangeRef.current?.(next)
        }
      },
      onPickFanChild: (fanNodeId, slotIndex) => {
        const current = treeRef.current
        if (current === null) return
        const next = applySetFanPromotedChild(current, fanNodeId, slotIndex)
        if (next !== current) {
          treeRef.current = next
          onTreeChangeRef.current?.(next)
        }
      },
      onBranch: () => {
        const current = treeRef.current
        if (current === null) return
        const next = applyCloneTree(current, () => crypto.randomUUID())
        treeRef.current = next
        setLinearNodeId(null)
        onTreeChangeRef.current?.(next)
      },
      onDelete: (nodeIdArg) => {
        const current = treeRef.current
        if (current === null) return
        if (nodeIdArg === current.rootId) return
        setPendingDeleteNodeId(nodeIdArg)
      },
      onOpenLinear: (nodeIdArg) => {
        setLinearNodeId(nodeIdArg)
      },
    }
    if (actionCallbacks === undefined) return defaults
    return { ...defaults, ...actionCallbacks }
  }, [actionCallbacks, tree, shim])

  const confirmDelete = () => {
    const current = treeRef.current
    const nodeIdToDelete = pendingDeleteNodeId
    if (current === null || nodeIdToDelete === null) {
      setPendingDeleteNodeId(null)
      return
    }
    const next = applyDeleteSubtree(current, nodeIdToDelete)
    if (next !== current) {
      treeRef.current = next
      if (linearNodeId === nodeIdToDelete) setLinearNodeId(null)
      onTreeChangeRef.current?.(next)
    }
    setPendingDeleteNodeId(null)
  }

  const deleteModalElement = pendingDeleteNodeId !== null ? (
    <Dialog
      open
      onOpenChange={(_event, data) => {
        if (!data.open) setPendingDeleteNodeId(null)
      }}
    >
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Delete subtree?</DialogTitle>
          <DialogContent>
            <p>This removes the selected node and all of its descendants from this tree.</p>
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={() => setPendingDeleteNodeId(null)}>
              Cancel
            </Button>
            <Button appearance="primary" onClick={confirmDelete}>
              Delete
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  ) : null

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
      <div data-slot="drawer" className={linearNodeId !== null ? styles.drawerOpen : styles.drawer}>
        {tree !== null && linearNodeId !== null && (
          <LinearPathDrawer
            tree={tree}
            nodeId={linearNodeId}
            onClose={() => setLinearNodeId(null)}
            styles={styles}
          />
        )}
      </div>
      <div data-slot="toast" className={styles.toast} />
      <div data-slot="modal" className={styles.modal}>
        {modalElement ?? dirtyEditModalElement ?? deleteModalElement}
      </div>
    </div>
  )
}

function LinearPathDrawer({
  tree,
  nodeId,
  onClose,
  styles,
}: {
  tree: ConversationTree
  nodeId: ConversationTreeNodeId
  onClose: () => void
  styles: ReturnType<typeof useTreeRunnerHostStyles>
}) {
  const byId = new Map(tree.nodes.map((node) => [node.id, node]))
  const path = pathToNode(tree, nodeId)
  return (
    <aside data-tree-linear-drawer>
      <div className={styles.drawerHeader}>
        <strong>Path</strong>
        <Button size="small" appearance="subtle" onClick={onClose}>Close</Button>
      </div>
      {path.map((id) => {
        const node = byId.get(id)
        if (node === undefined) return null
        return (
          <section key={id} data-tree-linear-node className={styles.drawerNode}>
            <div className={styles.drawerNodeKind}>{node.kind.replace('_', ' ')}</div>
            <div className={styles.drawerNodeState}>{node.state}</div>
            <pre className={styles.drawerNodeText}>
              {nodeText(node)}
            </pre>
          </section>
        )
      })}
    </aside>
  )
}

function pathToNode(tree: ConversationTree, nodeId: ConversationTreeNodeId): ConversationTreeNodeId[] {
  const byId = new Map(tree.nodes.map((node) => [node.id, node]))
  const out: ConversationTreeNodeId[] = []
  let cursor = byId.get(nodeId)
  while (cursor !== undefined) {
    out.push(cursor.id)
    cursor = cursor.parentId === null ? undefined : byId.get(cursor.parentId)
  }
  return out.reverse()
}

function nodeText(node: ConversationTree['nodes'][number]): string {
  switch (node.kind) {
    case 'root_prompt':
      return node.params.text
    case 'user_turn':
      return node.params.text
    case 'send':
      return node.params.responsePreview ?? ''
    case 'fan':
      return `axis: ${node.params.axis}\nvariants: ${node.params.variants.length}`
    case 'score':
      return node.params.scorerType
    case 'import_message':
      return node.params.sourceConversationId
  }
}

function applyEdgeInsert(
  tree: ConversationTree,
  parentId: ConversationTreeNodeId,
  childId: ConversationTreeNodeId,
  kind: EdgeInsertKind,
  uuid: () => string,
): ConversationTree {
  if (kind === 'fan_attempt') return applyWrapWithFan(tree, parentId, childId, 'attempt', uuid)
  if (kind === 'fan_converter' || kind === 'append_converter') {
    return applyWrapWithFan(tree, parentId, childId, 'converter', uuid)
  }
  return applyInsertBetween(tree, parentId, childId, kind as AppendChildKind, uuid)
}


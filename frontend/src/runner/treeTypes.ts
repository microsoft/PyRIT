// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tree-UI (CoPyRIT V1.0) domain types and runner interfaces.
 *
 * The single source of truth is the design doc set at
 *   doc/gui/design/01_tree_primitives.md  (data model, lifecycle, propagation)
 *   doc/gui/design/02_tree_ui_affordances.md  (operator UX)
 *   doc/gui/design/03_runner.md  (dispatch loop, wave bookkeeping)
 *
 * Each type below carries a single-line citation to the section it derives
 * from; multi-section derivations cite the primary source. The compile-time
 * contract is enforced by `frontend/src/runner/treeTypes.contract.test.ts`.
 */

// PrependedMessageRequest / ComponentIdentifier live in the API-types module
// (`frontend/src/types/index.ts`) since they mirror backend wire shapes; the
// runner module bridges them where dispatch needs them. No direct imports
// from this module are required for the type layer.

// ============================================================================
// Identifier types — branded strings for type-level disambiguation
// ============================================================================
//
// At run time these are plain strings; the `__brand` field is a phantom type
// that never exists at runtime. Branding catches "passed node id where tree
// id was expected" at compile time without any runtime cost.

declare const conversationTreeIdBrand: unique symbol
export type ConversationTreeId = string & { readonly [conversationTreeIdBrand]: 'ConversationTreeId' }

declare const conversationTreeNodeIdBrand: unique symbol
export type ConversationTreeNodeId = string & {
  readonly [conversationTreeNodeIdBrand]: 'ConversationTreeNodeId'
}

// ============================================================================
// Lifecycle (01 §6.1)
// ============================================================================

/** Per 01 §6.1: the seven values the runner reads to decide eligibility. */
export type NodeState = 'draft' | 'clean' | 'edited' | 'stale' | 'running' | 'failed' | 'cancelled'

/**
 * Per 01 §6 lastError.failure_class + 03 §3.3a _format_api_error.
 * - 'transient'    : 5xx, network, timeout. Retry eligible.
 * - 'rate_limited' : HTTP 429 or provider-specific overloaded shapes.
 *                    Retry-eligible but UI gates until window clears.
 * - 'permanent'    : 4xx other than 429 (validation, operator-lock mismatch,
 *                    target-not-found). Retry-ineligible without operator action.
 * - 'blocked'      : runner-synthesized when this leaf was dropped from
 *                    `ready` by the 03 §5.3 in-flight cascade.
 */
export type NodeFailureClass = 'transient' | 'rate_limited' | 'permanent' | 'blocked'

/**
 * Per 03 §3.3a: structured error reason returned by `_format_api_error` and
 * passed into `RunnerStateSink.setNodeState(opts.reason)`. The sink writes it
 * directly into the node's `lastError`.
 */
export interface ApiErrorReason {
  message: string
  failure_class: NodeFailureClass
}

// ============================================================================
// Shared types (01 §4.6)
// ============================================================================

/** Per 01 §4.6. */
export type PromptDataType = 'text' | 'image_path' | 'audio_path' | 'video_path' | 'binary_path'

/**
 * Per 01 §4.6. Either a stored converter id (preferred — matches
 * `converter_id` on the backend) OR an inline ephemeral converter spec.
 */
export interface ConverterRef {
  converterId?: string
  inline?: {
    type: string
    params: Record<string, unknown>
  }
}

/** Per 01 §4.6. */
export interface PieceSpec {
  dataType: PromptDataType
  value: string
  mimeType?: string
  /** Matches `MessagePieceRequest.original_prompt_id` on the wire. */
  originalPromptId?: string
}

/** Per 03 §6.2. The wire-level enum. Closed in V1.0. */
export type WaveTriggerKind =
  | 'refresh_node'
  | 'refresh_subtree'
  | 'refresh_tree'
  | 'retry_failed'
  | 'synced_peer_add'
  | 'cross_tree_rebase'

/**
 * Per 01 §4.6 (rev 18 / Finding C.1). The runner writes the timing triple
 * inline with state transitions — `dispatchedAt` at `running`,
 * `targetFirstByteAt` when the first response chunk arrives (or on
 * `add_message`'s response for non-streaming targets), `completedAt` at the
 * terminal `clean` / `failed` / `cancelled` transition. All three are nullable
 * to cover failures that never reached the target.
 *
 * Immutable once written; per 01 §6.5 ExecutionRecords may be shared across
 * cloned trees (the per-tree `ReflogEntry` wraps them).
 */
export interface ExecutionRecord {
  /** UUID v4 minted by the runner; replaces the old timestamp-based id. */
  executionId: string
  /** ISO-8601 UTC. The historical "attemptedAt" field; mirrors a Python timestamp. */
  attemptedAt: string
  /** Which AttackResult this execution belongs to. Null only on failed pre-create_attack dispatches. */
  attackResultId: string | null
  /** Which conversation in that AttackResult. */
  conversationId: string | null
  /** MessagePiece ids produced by this execution. */
  pieceIds: string[]
  /** Best-effort text preview from assistant pieces produced by this execution. */
  responsePreview?: string
  outcome: 'success' | 'failure' | 'error' | 'cancelled' | 'pending'
  errorMessage?: string
  /** For replay / debugging — the hash that was current when this execution started. */
  resolvedInputHashAtExecution: string
  /** Per 01 §14: the wave that produced this execution. Null only for synthetic auto-reverse records. */
  waveId: string | null
  /** Per 03 §6.2: which kind of operator action fired this wave. */
  waveTriggerKind: WaveTriggerKind | null
  /** Per 01 §4.6 timing triple. */
  dispatchedAt: string | null
  targetFirstByteAt: string | null
  completedAt: string | null
}

/**
 * Per 01 §4.6 / §6.6: per-tree wrapper around an `ExecutionRecord`. The
 * `execution` object is immutable and may be shared across cloned trees;
 * the `pinned` flag is per-tree (pinning entry E in tree A does not pin
 * the same shared execution in tree B's reflog).
 */
export interface ReflogEntry {
  execution: ExecutionRecord
  pinned: boolean
}

// ============================================================================
// Node taxonomy (01 §4)
// ============================================================================

export type ConversationTreeNodeKind =
  | 'root_prompt'
  | 'import_message'
  | 'user_turn'
  | 'converter'
  | 'send'
  | 'fan'
  | 'score'

/**
 * Per 01 §4.0. Shared fields on every node kind.
 *
 * `version: number` (rev 18 / Finding C.5): monotonic counter bumped on every
 * `editParams` / `regenerateFanChildren` / `makeCurrent` mutation. V1.0 reads
 * it only for telemetry / debug logs; V2 uses it as the last-write-wins key
 * for collaborative-tree concurrency. Carrying it in V1.0 costs nothing at
 * the data-model layer and makes V2 a non-migration.
 */
export interface ConversationTreeNodeBase {
  id: ConversationTreeNodeId
  kind: ConversationTreeNodeKind
  parentId: ConversationTreeNodeId | null // null = root
  /** SHA-256 of the resolved input bundle (01 §5.3). Lazy-recomputed on read. */
  resolvedInputHash: string
  state: NodeState
  execution: ExecutionRecord | null
  executionHistory: ReflogEntry[]
  /**
   * Operator-readable error reason populated when the node transitions to
   * `failed` / `cancelled` (or `stale` via the 03 §5.3 in-flight cascade).
   * Cleared by `recordExecution` (success path) or `setNodeState` with
   * `opts.reason: null`.
   */
  lastError: ApiErrorReason | null
  labels: Record<string, string>
  createdAt: string
  updatedAt: string
  version: number
}

// --- Source class (no input) -------------------------------------------------

/** Per 01 §4.1. */
export interface RootPromptNode extends ConversationTreeNodeBase {
  kind: 'root_prompt'
  params: {
    text: string
    attachments: PieceSpec[]
    systemPrompt?: string
    /** Default target for downstream Send nodes (per 01 §4.1). */
    targetRegistryName: string
  }
}

/** Per 01 §4.1. */
export interface ImportMessageNode extends ConversationTreeNodeBase {
  kind: 'import_message'
  params: {
    sourceConversationId: string
    /** Matches the backend's `cutoff_index` on `CreateAttackRequest`. */
    cutoffIndex: number
  }
}

// --- Transform class (1 in, 1 out, pure) ------------------------------------

/**
 * Per 01 §4.2. Single kind; `role` discriminates. `'assistant'` is
 * deliberately excluded — real assistant turns come only from a Send.
 */
export interface UserTurnNode extends ConversationTreeNodeBase {
  kind: 'user_turn'
  params: {
    role: 'user' | 'simulated_assistant' | 'system'
    text: string
    attachments: PieceSpec[]
    /** Sequential converter pipeline; matches `AddMessageRequest.converter_ids`. */
    converterPipeline?: ConverterRef[]
  }
}

export interface ConverterNode extends ConversationTreeNodeBase {
  kind: 'converter'
  params: {
    /** Ordered converter pipeline applied before the downstream response dispatch. */
    pipeline: ConverterRef[]
    /** Optional operator label for unconfigured / comparison nodes. */
    label?: string
    /** Preview is inspection-only; refresh still sends converter_ids to the backend. */
    preview?: {
      value: string
      dataType: PromptDataType
    }
  }
}

// --- Side-effecting class ----------------------------------------------------

/** Per 01 §4.3. The only node kind that mutates external state (one POST per refresh). */
export interface SendNode extends ConversationTreeNodeBase {
  kind: 'send'
  params: {
    /** May override the target inherited from the upstream RootPromptNode. */
    targetRegistryName?: string
    /** Optional send-time converters; merged after the upstream UserTurn's pipeline. */
    converterPipeline?: ConverterRef[]
    /** Best-effort assistant/target response text reconstructed from backend history. */
    responsePreview?: string
  }
}

// --- Structural class (FanNode + axes/variants) -----------------------------

/**
 * Per 01 §4.4. The full design surface includes V1.1+ axes; V1.0 ships only
 * `attempt` and `converter`. V1.1+ adds the rest without changing the type.
 */
export type FanAxis = 'attempt' | 'converter' | 'prompt' | 'target' | 'system_prompt' | 'temperature'

/**
 * Per 01 §4.4 FanVariant discriminated union. Each variant's `payload` is
 * keyed by `axis`. The `attempt` payload is `{}` (slotIndex differentiates).
 */
export type FanVariant =
  | { axis: 'attempt'; payload: Record<string, never> }
  | { axis: 'prompt'; payload: { text: string; attachments?: PieceSpec[] } }
  | { axis: 'converter'; payload: { converters: ConverterRef[] } }
  | { axis: 'target'; payload: { targetRegistryName: string } }
  | { axis: 'system_prompt'; payload: { systemPrompt: string } }
  | { axis: 'temperature'; payload: { temperature: number } }

/** Per 01 §4.4. */
export interface FanNode extends ConversationTreeNodeBase {
  kind: 'fan'
  params: {
    axis: FanAxis
    variants: FanVariant[]
    /** Default 'each'; V1.0 does not implement Cartesian sweep (use nested fans). */
    mode?: 'each'
    /**
     * Per 01 §4.4 / §6.6 + 02 §3.3 Pick/Unpick: the slotIndex of one child to
     * mark as "promoted" (cherry-pick analogue). Null = all children synced
     * (default). Runner ignores this field; it's purely a UI/editing concern.
     */
    promotedChildSlotIndex: number | null
    /**
     * Per 01 §5.1 invariant 2: deleted slot indices are tombstones (siblings
     * do not renumber). The next allocated slot is
     * `max(variants[].slotIndex ∪ deletedSlotIndices) + 1`.
     */
    deletedSlotIndices: number[]
  }
}

// --- Observational class -----------------------------------------------------

/** Per 01 §4.5. V1.0 is render-only; runner does not dispatch ScoreNodes. */
export interface ScoreNode extends ConversationTreeNodeBase {
  kind: 'score'
  params: {
    scorerType: string
    scorerParams?: Record<string, unknown>
  }
}

/** The discriminated union over the six V1.0 node kinds. */
export type ConversationTreeNode =
  | RootPromptNode
  | ImportMessageNode
  | UserTurnNode
  | ConverterNode
  | SendNode
  | FanNode
  | ScoreNode

/**
 * Discriminated union of every node's `params` shape. The undo system (01 §6.9)
 * needs to snapshot the params of any node kind, and TypeScript narrows from
 * `node.kind` to `node.params` via `ConversationTreeNode` directly; this
 * helper alias gives consumers a name for "any kind's params" when storing
 * snapshots outside a kind-discriminated context.
 */
export type NodeParams = ConversationTreeNode['params']

// ============================================================================
// Edge model (01 §5)
// ============================================================================

/**
 * Per 01 §5. Edges are derived from `parentId` + slot assignment. `slotIndex`
 * is the fan-discriminator; MUST be incorporated into the child's
 * `resolvedInputHash` so siblings of an `attempt`-axis fan have distinct
 * hashes even when their parent's resolved input is identical (01 §5.1 #4).
 */
export interface ConversationTreeEdge {
  id: string
  parentId: ConversationTreeNodeId
  childId: ConversationTreeNodeId
  /** For non-fan parents, slotIndex is 0. For FanNode parents, identifies the variant. */
  slotIndex: number
}

// ============================================================================
// Undo (01 §6.9) — per-tree inverse-op stack with state-snapshot widening
// ============================================================================

/**
 * Per 01 §6.9 (rev 16, Findings 6+7): each variant snapshots the *affected-
 * node-set state* (not just params/execution) so the inverse fully reverses
 * the op's downstream cascade. Without this widening, undo was structurally
 * lossy — Ctrl-Z visually "did something" but left descendants in `stale`.
 */
export type UndoOp =
  | {
      kind: 'add'
      nodeId: ConversationTreeNodeId
      autoInsertedChildIds: ConversationTreeNodeId[]
    }
  | {
      kind: 'delete'
      subtreeSnapshot: ConversationTreeNode[]
      edgesSnapshot: ConversationTreeEdge[]
      /** The original parent the subtree was attached under, for re-grafting. */
      parentId: ConversationTreeNodeId
    }
  | {
      kind: 'editParams'
      nodeId: ConversationTreeNodeId
      oldParams: NodeParams
      /** The node's state before the §6.3 rule 1 cascade fired. */
      priorState: NodeState
      /** Every descendant the §6.3 rule re-staled, with its pre-cascade state. */
      priorDescendantStates: Map<ConversationTreeNodeId, NodeState>
    }
  | {
      kind: 'regenerateFanChildren'
      fanNodeId: ConversationTreeNodeId
      oldChildren: ConversationTreeNode[]
      oldChildEdges: ConversationTreeEdge[]
    }
  | {
      kind: 'makeCurrent'
      nodeId: ConversationTreeNodeId
      /** Per 01 §6.7 step 0: `null` is a valid prior (failed-node makeCurrent path). */
      priorExecution: ExecutionRecord | null
      /** The promoted entry; move it back to the reflog on undo. */
      promotedExecution: ExecutionRecord
      priorDescendantStates: Map<ConversationTreeNodeId, NodeState>
      priorDescendantExecutions: Map<ConversationTreeNodeId, ExecutionRecord | null>
    }

// ============================================================================
// ConversationTree (01 §13.3) — the top-level container
// ============================================================================

export interface ConversationTree {
  id: ConversationTreeId
  nodes: ConversationTreeNode[]
  edges: ConversationTreeEdge[]
  rootId: ConversationTreeNodeId
  displayName: string
  createdAt: string
  /**
   * Set at clone time by `branchToNewTree` (01 §6.5) to the source tree's id.
   * Null for trees created via `newTree()` or restored from History without a
   * parent context.
   */
  parentConversationTreeId: ConversationTreeId | null
  /**
   * Set at Open-as-tree time by `openTreeFromAttackResult` (01 §13.1) when the
   * source AR is pre-V1.0 (no `conversation_tree_id` label). Carries the
   * source AR's `conversation_id` so the §9.4.1 reload-reconstruction fallback
   * path can locate the legacy AR. Null for fresh or already-tree-tagged trees.
   */
  parentSourceConversationId: string | null
  /**
   * Per 01 §6.9: in-memory inverse-op stack for Ctrl-Z structural undo. Cap
   * N=20, FIFO eviction. Cleared on tree-swap; carried into the clone by
   * `branchToNewTree`. Not persisted to sessionStorage (V1.0 reload loses it).
   */
  undoStack: UndoOp[]
}

// ============================================================================
// Workspace (01 §13.1) — V1.0 minimal shape
// ============================================================================

export interface WorkspaceSettings {
  /** Default 50, hard max 200 (per 01 §6.6). */
  reflogCapPerNode: number
  /** Default 20 (per 02 §8.1 cost-guardrail modal). */
  confirmThresholdCount: number
  /** Operator-toggled "Don't ask again" (default false). */
  suppressConfirmModalThisSession: boolean
}

/**
 * Per 01 §13.1: V1.0 minimal Workspace. V1.1 promotes `currentTree` to
 * `conversationTrees: ConversationTree[]` + adds the tab strip; the V1.0
 * shape is a strict subset.
 */
export interface Workspace {
  /** The foregrounded tree; null = greenfield. */
  currentTree: ConversationTree | null
  /** Last ~10 tree ids visited (persisted to sessionStorage). */
  recentTreeIds: ConversationTreeId[]
  settings: WorkspaceSettings
}

// ============================================================================
// Wave bookkeeping (03 §6)
// ============================================================================

/**
 * Per 03 §6.3. Discriminated union over `kind`. Every variant carries an
 * `emittedAt: string` (ISO-8601 UTC) populated by the sink at emit time
 * (per 01 §4.6 / rev 18 / Finding C.1).
 *
 * `complete.summary.failed` is bucketed by failure class so the wave-complete
 * toast and ribbon can drive separate counts/colors without per-node scans.
 * `blocked` is computed from leaves left `stale` with
 * `lastError.failure_class === 'blocked'` (the §5.3 in-flight cascade victims).
 */
export type WaveEvent =
  | {
      kind: 'start'
      waveId: string
      triggerKind: WaveTriggerKind
      estimatedCalls: number
      treeId: ConversationTreeId
      emittedAt: string
    }
  | {
      kind: 'node_complete'
      waveId: string
      nodeId: ConversationTreeNodeId
      outcome: 'success' | 'failure'
      emittedAt: string
    }
  | {
      kind: 'complete'
      waveId: string
      emittedAt: string
      summary: {
        succeeded: number
        failed: {
          transient: number
          rate_limited: number
          permanent: number
        }
        blocked: number
        cancelled: number
        reflog_evicted: number
      }
    }
  | {
      kind: 'busy'
      treeId: ConversationTreeId
      holderTabId: string
      emittedAt: string
    }
  | {
      kind: 'queued'
      waveId: string
      treeId: ConversationTreeId
      queueDepth: number
      emittedAt: string
    }
  | {
      kind: 'reflog_eviction'
      treeId: ConversationTreeId
      nodeId: ConversationTreeNodeId
      evictedExecutionId: string
      /** First ~80 chars of the evicted execution's first piece — for the ribbon marker. */
      preview: string
      emittedAt: string
    }
  | {
      /**
       * Per 03 §2.1 entry-point shim step 1: the tag-hygiene gate fired and
       * the wave never started. The UI shows the operator-tag-required modal.
       */
      kind: 'operator_tag_required'
      treeId: ConversationTreeId
      emittedAt: string
    }

// ============================================================================
// Runner interfaces (03 §2.1, §2.2, §2.3, §10.4)
// ============================================================================

/**
 * Per 03 §2.1. The public API the UI invokes; every entry point is
 * implemented by the §2.1 5-step shim (tag gate → lock acquire → cost modal
 * → queue check → wave start).
 *
 * Each method's `Promise<void>` resolves when the wave is fully settled.
 * Per-node state updates flow through `RunnerStateSink` during the wave;
 * callers `await` only when they need to know the wave is over.
 */
export interface Runner {
  refreshNode(treeId: ConversationTreeId, nodeId: ConversationTreeNodeId): Promise<void>
  refreshSubtree(treeId: ConversationTreeId, rootNodeId: ConversationTreeNodeId): Promise<void>
  refreshTree(treeId: ConversationTreeId): Promise<void>
  /** V1.0: UI-level cancel (flips a per-wave flag; in-flight HTTP completes). */
  cancelWave(treeId: ConversationTreeId): Promise<void>
  /** Drop every queued wave for this tree; does NOT affect the active wave. */
  cancelQueued(treeId: ConversationTreeId): Promise<void>
  /**
   * Per 02 §5.14 / 03 §5.3: scoped retry of wave-W's failed + blocked leaves.
   * `nodeIds` captured by the toast at wave-complete time so retry scope is
   * stable even if the operator edits the tree between completion and click.
   */
  retryFailedNodes(
    treeId: ConversationTreeId,
    nodeIds: ConversationTreeNodeId[],
  ): Promise<void>
}

/**
 * Per 03 §2.2. The runner's sole mutation surface for React state. Keeping
 * this a single interface lets the runner be unit-tested with a mock sink
 * and prevents importing React hooks inside the dispatch loop.
 *
 * `opts.reason` accepts three shapes (per the §2.2 reason semantics):
 * - `string` → normalized to `{ message, failure_class: 'transient' }`
 * - `ApiErrorReason` → written directly to `node.lastError`
 * - `null` → clears `node.lastError` (used by retry-failed demotion)
 * Omitted leaves the existing `lastError` unchanged.
 *
 * Missing-node tolerance (per §2.2): all mutating methods silently no-op
 * when the target node does not exist (e.g., operator deleted mid-wave).
 */
export interface RunnerStateSink {
  setNodeState(
    treeId: ConversationTreeId,
    nodeId: ConversationTreeNodeId,
    state: NodeState,
    opts?: { reason?: string | ApiErrorReason | null },
  ): void
  /**
   * Attach a fresh ExecutionRecord; the prior execution (if any) is wrapped
   * in a `ReflogEntry` with `pinned=false` and pushed onto `executionHistory`,
   * evicting the oldest unpinned entry if at cap (01 §6.6).
   */
  recordExecution(
    treeId: ConversationTreeId,
    nodeId: ConversationTreeNodeId,
    record: ExecutionRecord,
  ): void
  /** Null out a node's `execution` field (01 §6.4.1). Does NOT touch reflog. */
  clearExecution(treeId: ConversationTreeId, nodeId: ConversationTreeNodeId): void
  /** Set / clear the `pinned` flag on a `ReflogEntry`; per-tree per-execution. */
  setReflogPinned(
    treeId: ConversationTreeId,
    nodeId: ConversationTreeNodeId,
    executionId: string,
    pinned: boolean,
  ): void
  emitWaveEvent(event: WaveEvent): void
}

/**
 * Per 03 §2.3. The runner consults this before dispatch. Returns true if the
 * wave is approved (count under threshold or operator clicked through the
 * modal). False short-circuits the wave with state unchanged.
 */
export interface CostGuardrail {
  approve(estimatedCalls: number, waveTriggerKind: WaveTriggerKind): Promise<boolean>
}

/**
 * Per 03 §10.4 / 01 §9.4.3: BroadcastChannel-keyed advisory lock on
 * `conversation_tree_id` so two browser tabs viewing the same tree cannot
 * concurrently rebase it (the dominant fork-bomb risk).
 *
 * `acquire` returns a discriminated union: `{ acquired: true }` when the
 * lock is ours, `{ acquired: false; holderTabId }` when another tab holds
 * it. The acquired-true variant carries no `holderTabId` field at all —
 * the lock is ours, there's nothing meaningful to populate. On busy,
 * `holderTabId` is the responding tab's id so the UI can render
 * *"another tab (id: …) is refreshing"* in the busy modal.
 *
 * `release` is unconditional; the §2.1 shim's outer try/finally guarantees
 * it runs on every exit path.
 */
export type LockAcquireResult =
  | { acquired: true }
  | { acquired: false; holderTabId: string }

export interface CrossTabLockManager {
  acquire(treeId: ConversationTreeId): Promise<LockAcquireResult>
  release(treeId: ConversationTreeId): void
}

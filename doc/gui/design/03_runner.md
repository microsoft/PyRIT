# Tree-Based UI — Runner Spec (V1.0 stub)

> Status: **DRAFT stub (revision 18)** — companion to [01_tree_primitives.md](01_tree_primitives.md) and [02_tree_ui_affordances.md](02_tree_ui_affordances.md). This doc is intentionally outline-level. Each section names what the runner does and references the primitives section that decides the *why*; sections marked **TODO:spec** need a focused expansion pass before the runner is implemented. The reviewer's strong recommendation was "write the runner spec before any code" — this stub lets implementers start fanning out (interfaces, state-update plumbing, the dispatch queue) in parallel with the spec-expansion work.
> Rolling revision history lives at [01 §0](01_tree_primitives.md#0-rolling-revision-history); refer there for cross-doc change summaries. The freshest substantive gate items between current state and implementer onboarding are [Q.S.1–Q.S.3](#12-open-questions) below.

### Version-scope legend

Shared with [01](01_tree_primitives.md#version-scope-legend) and [02](02_tree_ui_affordances.md#version-scope-legend). V1.0 surface only is fleshed out below; V1.1 deltas (per-Workspace budgeting, Synced-Peers Stack dispatch, multi-tab fair-share) are flagged inline.

## 1. Goals & Non-Goals

### Goals

1. **Translate a ConversationTree into backend calls deterministically.** Same tree shape + same node states → same call sequence (modulo concurrency ordering). No hidden runner heuristics that aren't in the data model.
2. **Honor the V1 contract that nothing fires unless the operator asks.** Edits mark nodes edited/stale (§6.3 of [01](01_tree_primitives.md#63-propagation-rules)); the runner is silent until `refreshNode`, `refreshSubtree`, or `refreshTree` is called.
3. **AR-per-leaf with no backend changes.** Per the materialization rule in [01 §7.1](01_tree_primitives.md#71-conversationtree-operation--backend-call), each leaf `SendNode` dispatch is a **`create_attack` + N `add_message` sequence**: first `POST /api/attacks` ([`create_attack`](../../../pyrit/backend/routes/attacks.py#L184)) to create the AR with the resolved clean-prefix history as `prepended_conversation`, then one `POST /api/attacks/{new_id}/messages` ([`add_message`](../../../pyrit/backend/routes/attacks.py#L432)) per stale Send on the path (in topo order, finishing at the leaf). `create_attack` is context setup; `add_message` with `send=True` is the call that produces the assistant response. The N add_messages re-fire stale interior Sends and the leaf within the same AR — see §3.2 / §3.3 for the partition rule and the deadlock-avoidance reasoning. Existing backend semantics; the runner does not change them.
4. **Bounded concurrency.** `maxParallel=4` (V1.0: per-session; V1.1: per-Workspace with fair-share). The runner is the single chokepoint that enforces this — no other layer should fire backend calls. **Each leaf's full dispatch sequence (`create_attack` + N `add_message`s) counts as one budget slot** held atomically for the duration; all calls in the sequence execute sequentially within the same slot.
5. **Partial-commit on failure.** In-flight calls complete; not-yet-dispatched nodes transition to `cancelled` (§6.4 of [01](01_tree_primitives.md#64-failure--partial-commit-semantics)).
6. **Wave bookkeeping.** Every refresh stamps a fresh `waveId` and a `waveTriggerKind` from the §14.4 enum on each affected `ExecutionRecord` and on each leaf AR's `labels.wave_id` / `labels.wave_trigger_kind` (see §6).

### Non-Goals

- **Server-side runner / queue.** V1's runner is a client-side TypeScript module under `frontend/src/runner/` (proposed path). The backend is a stateless target of HTTP calls. The §6.4 partial-commit semantics live in the client because there's no server-side cancellation surface (see §9 and [01 §12.8](01_tree_primitives.md#128-cancellation-deferred---accepted-follow-up-v1x)).
- **Retries with backoff.** The runner does not retry failed calls. The backend's `AttackService` already has [`max_attempts_on_failure`](../../../pyrit/attacks/) at the *per-attack* layer; the runner adds no second retry layer (would compound exponentially in fan-outs). Failed nodes surface to the operator who decides whether to re-trigger.
- **Streaming partial responses.** The runner awaits each backend POST to completion. SSE / WebSocket streaming is a V2 polish item.
- **Cross-tab synchronization.** Two browser tabs with two tree views run independent runners; per [01 §9.4.3](01_tree_primitives.md#943-concurrent-tab-advisory-lock-v10), V1.0 ships a `BroadcastChannel`-based **advisory lock** keyed on `conversation_tree_id` that prevents two tabs from concurrently rebasing the same tree (the dominant fork-bomb risk). The lock is advisory — it bounds the common case without requiring server-side coordination. Full coordination (live state sync, undo/redo across tabs) is V2.
- **Distributed dispatch.** No worker pool, no Web Workers — the runner is one async loop in the main thread. The bottleneck is network I/O, not CPU. **TODO:spec** — benchmark whether the JSON-serialization cost for a 200-message `prepended_conversation` justifies pushing the serialize step to a Worker. Likely "no" for V1.0; revisit if a 60-leaf refresh visibly janks the UI.

## 2. Surface Area

### 2.1 Entry points (the public API)

```ts
// frontend/src/runner/runner.ts (proposed)

export interface Runner {
  /** Refresh exactly one node. Idempotent during a single in-flight call.
   *
   * V1.0 behavior by node kind ([01 §6.3](01_tree_primitives.md#63-propagation-rules) rule 2):
   * - root_prompt / import_message: no dispatch (re-hydrate seed bundle locally).
   * - user_turn / score: no dispatch (recompute resolvedInputHash; clean if upstream clean).
   * - send (leaf): one dispatch sequence via §3.3.
   * - send (interior): aliased to refreshSubtree(id) restricted to descendant leaves —
   *   per [01 §6.3 rule 2 'send (interior)'](01_tree_primitives.md#63-propagation-rules), the
   *   runner cannot fast-path a single interior Send because downstream leaf ARs still
   *   reference the interior's OLD assistant pieces in their prepended_conversation.
   * - fan: aliased to refreshSubtree(id) — fan children are typically user_turn nodes,
   *   and "refreshing" a user_turn is a no-op state recompute. Aliasing to subtree-refresh
   *   walks every Send descendant under the fan, which is what the ↻ action rail's
   *   "Refresh all children" tooltip means to the operator.
   */
  refreshNode(treeId: ConversationTreeId, nodeId: ConversationTreeNodeId): Promise<void>

  /** Refresh the node and all transitively-stale descendants. The §6.3 propagation
   * rules already marked the right set as stale; the runner walks them in topo order. */
  refreshSubtree(treeId: ConversationTreeId, rootNodeId: ConversationTreeNodeId): Promise<void>

  /** Convenience: refreshSubtree(treeId, tree.rootId). */
  refreshTree(treeId: ConversationTreeId): Promise<void>

  /** Cancel the active in-flight wave for this tree (V1.0; UI-level only — flips a per-wave
   * flag that the dispatch loop checks at each `ready.popNext()` boundary per §9). In-flight
   * HTTP calls complete; not-yet-dispatched leaves transition to `cancelled`. Returns when
   * the wave fully settles. Does NOT touch queued waves — use `cancelQueued` for those.
   * V1.x adds backend-token cancellation that aborts in-flight calls. */
  cancelWave(treeId: ConversationTreeId): Promise<void>

  /** Drop every queued (not-yet-active) wave for this tree (V1.0; per [§10.3](#103-backpressure-per-tree-wave-queue)).
   * Does NOT affect the active wave — use `cancelWave` for that. Resolves immediately;
   * dropped waves emit a `WaveEvent { kind: 'complete', summary.cancelled: <all leaves> }`
   * so the UI reconciles their queued banner state. */
  cancelQueued(treeId: ConversationTreeId): Promise<void>

  /** Retry a specific set of leaves (V1.0; called by the [02 §5.14](02_tree_ui_affordances.md#514-partial-failure-mid-refresh) `[Retry failed]`
   * toast button). `nodeIds` is captured by the UI at wave-complete time — the union of
   * the wave's failed leaves (any `failure_class` except `permanent`) plus its `blocked`
   * leaves. The runner builds `S` for this wave as: those nodeIds themselves PLUS any
   * `failed`/`cancelled` Send ancestors on each nodeId's root-to-leaf path (so the
   * [§3.1 step 2b retry-failed demotion](#31-topological-walk) can flip them back to
   * `stale` and the path becomes dispatchable). `waveTriggerKind = 'retry_failed'`.
   *
   * Distinct from `refreshSubtree(rootId)` because the retry is scoped to wave-W's
   * victims, not the whole tree — an operator who edited an unrelated node between
   * the original wave and the retry click does NOT have that edit swept up by retry.
   * The toast captures `nodeIds` at completion time so this scope is stable even if
   * the operator edits the tree before clicking. */
  retryFailedNodes(treeId: ConversationTreeId, nodeIds: ConversationTreeNodeId[]): Promise<void>
}
```

All three refresh methods return a `Promise<void>` that resolves when the wave is *settled* (every dispatched call has terminated — succeeded, failed, or cancelled). Per-node state updates flow through the React state container during the wave; callers `await` only when they need to know the wave is over (e.g., for telemetry or test assertions).

#### Entry-point shim ordering (V1.0)

Each `refresh*` method is implemented by an **entry-point shim** that runs five steps in a fixed order *before* the dispatch loop in [§3.1](#31-topological-walk) executes. Steps 2-5 are wrapped in `try { ... } finally { lockManager.release(treeId) }` so the cross-tab lock is released on every exit path — success, failure, cancel, OR early-return from the tag-hygiene gate or wave-queue check.

```ts
async function refreshSubtree(treeId, rootNodeId, triggerKind) {  // mirror for refreshNode / refreshTree
  // 1. Tag-hygiene gate (runs BEFORE lock acquire so a tag-missing operator does
  //    not lock out other tabs while seeing the modal). Per [§3.1 step 0 reframe](#31-topological-walk).
  const operator = currentOperator()
  if (!operator) {
    sink.emitWaveEvent({ kind: 'operator_tag_required', treeId })
    return  // wave never starts; no lock acquired, no cost modal, no node state mutated
  }

  // 2. Cross-tab advisory lock (§10.4). Acquire BEFORE the cost modal so a second
  //    tab can't sneak in while the operator reads the cost confirmation. The
  //    try/finally below guarantees release on every exit path.
  const lock = await lockManager.acquire(treeId)
  if (lock === 'busy') {
    sink.emitWaveEvent({ kind: 'busy', treeId, holderTabId: ... })
    return  // no lock acquired, nothing to release
  }

  try {
    // 3. Cost guardrail (§2.3). Operator may cancel here; the lock release in finally
    //    runs and the other tab can proceed.
    const estimatedCalls = estimate(rootNodeId)
    const approved = await costGuardrail.approve(estimatedCalls, triggerKind)
    if (!approved) return

    // 4. Per-tree wave-queue check (§10.3). If another wave is active on this tree,
    //    enqueue this one and return; the lock release in finally fires (the active
    //    wave holds its own lock acquired earlier). When the active wave settles,
    //    the queue drain logic re-acquires the lock for each queued wave via this
    //    same shim.
    if (currentWaveByTree.has(treeId)) {
      const req = { waveId: uuid(), rootNodeId, triggerKind, enqueuedAt: now() }
      queueByTree.get(treeId)?.push(req) ?? queueByTree.set(treeId, [req])
      sink.emitWaveEvent({ kind: 'queued', waveId: req.waveId, treeId, queueDepth: queueByTree.get(treeId)!.length })
      return
    }

    // 5. Wave start (§3.1). The dispatch loop runs to settlement; its emitWaveEvent
    //    `complete` event fires before this function returns.
    currentWaveByTree.set(treeId, { rootNodeId, triggerKind })
    try {
      await _runDispatchLoop(treeId, rootNodeId, triggerKind)  // §3.1
    } finally {
      currentWaveByTree.delete(treeId)
    }
    // Drain queue if non-empty (each queued wave re-enters via the same shim above).
    while ((queueByTree.get(treeId) ?? []).length > 0) {
      const next = queueByTree.get(treeId)!.shift()!
      await refreshSubtree(treeId, next.rootNodeId, next.triggerKind)  // re-enters the shim
    }
  } finally {
    lockManager.release(treeId)  // unconditional; every exit path releases
  }
}
```

**Why this ordering.** The five steps run in this order specifically:

1. **Tag-hygiene gate FIRST.** Operator with no tag set sees the modal before any other UI surface or lock acquire. Reviewer rev-15 spotted that placing this at §3.1's step 0 (the previous spec) caused the cost modal to fire first AND leaked the cross-tab lock on early-return. Moving it to step 1 of the shim fixes both at once.
2. **Lock acquire SECOND.** Cost modal can take seconds for the operator to read; a second tab racing in during that window would otherwise blow `maxParallel` cumulative across tabs.
3. **Cost modal THIRD.** Operator confirms what they're about to spend; cancel returns through finally and releases the lock.
4. **Queue check FOURTH.** Only after cost approval do we decide whether to enqueue (lock is released in finally; the active wave holds its own lock from its earlier shim invocation). Queue semantics (FIFO, no-coalescing, stale-set recomputed at wave-start, banner copy) are spec'd in [§10.3](#103-backpressure-per-tree-wave-queue); this shim is the canonical implementation of that contract.
5. **Wave start FIFTH.** The §3.1 dispatch loop runs; its `complete` event is the natural wave-settle marker that the lock-release finally also covers.

### 2.2 State-update plumbing

The runner does not own React state. It receives a `RunnerStateSink` at construction:

```ts
export interface RunnerStateSink {
  /** Move a node into a new lifecycle state (clean/edited/stale/running/failed/cancelled).
   * The optional `opts.reason` populates the node's `lastError` field for failed/cancelled
   * transitions (per [01 §6.4.1](01_tree_primitives.md#641-why-nodeexecution--null-on-failure-not-preserved)); on transitions away from failed
   * (e.g., back to running on retry), the sink clears `lastError`. */
  setNodeState(
    treeId: ConversationTreeId,
    nodeId: ConversationTreeNodeId,
    state: NodeState,
    opts?: { reason?: string | ApiErrorReason | null },
  ): void

  /** Attach a fresh ExecutionRecord to a node (also moves prior execution into reflog
   * per [01 §6.6](01_tree_primitives.md#66-executionhistory-gc-the-reflog) — wrapping
   * the prior execution in a `ReflogEntry` with `pinned=false`). */
  recordExecution(treeId: ConversationTreeId, nodeId: ConversationTreeNodeId, record: ExecutionRecord): void

  /** Null out a node's `execution` field. Called on `failed` and `cancelled` transitions
   * per [01 §6.4.1](01_tree_primitives.md#641-why-nodeexecution--null-on-failure-not-preserved). Does NOT touch `executionHistory`
   * (the reflog only ever receives executions that completed via `recordExecution`). */
  clearExecution(treeId: ConversationTreeId, nodeId: ConversationTreeNodeId): void

  /** Set or clear the `pinned` flag on a `ReflogEntry` (per [01 §6.6](01_tree_primitives.md#66-executionhistory-gc-the-reflog)).
   * Per-tree per-execution; called by the UI when the operator clicks Pin/Unpin in the reflog
   * drawer. No-ops if the entry is not in the tree's reflog (e.g., was just evicted). */
  setReflogPinned(
    treeId: ConversationTreeId,
    nodeId: ConversationTreeNodeId,
    executionId: string,
    pinned: boolean,
  ): void

  /** Emit a wave event (start / per-node-complete / wave-complete) so the UI can
   * render the [02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances) progress bar and the [02 §8.1](02_tree_ui_affordances.md#81-the-v1-chain-preview-banner--confirm-modal--toast--drawer-panel) toast. */
  emitWaveEvent(event: WaveEvent): void
}
```

The sink is the **only** way the runner mutates React state. This boundary keeps the runner unit-testable with a mock sink (see §11) and prevents the temptation to import React hooks inside the dispatch loop.

**Sink reason semantics (V1.0).** `opts.reason` accepts three shapes:

- `string` — plain text. Sink normalizes to `{ message: <string>, failure_class: 'transient' }` (defensive default; pre-rev-15 callsites that just passed a string land in `transient`).
- `ApiErrorReason` — the structured `{ message; failure_class }` from [§3.3a `_format_api_error`](#33a-helpers-referenced-by-the-dispatch-step). Sink writes the object directly to `node.lastError`.
- `null` — clear `node.lastError` entirely (set to `null`). Used by the [§3.1 step 2b retry-failed demotion](#31-topological-walk) when flipping `failed`/`cancelled` nodes back to `stale` for a retry wave. Distinct from "omitted" (no `reason` key in `opts`): omitted leaves the existing `lastError` unchanged. The same null-clears-vs-omitted-leaves-unchanged convention applies on `clean` transitions (recordExecution-driven; the sink clears `lastError` implicitly on success).

**Missing-node tolerance.** All sink mutating methods (`setNodeState`, `recordExecution`, `clearExecution`, `setReflogPinned`) silently no-op when the target node does not exist in the current tree state (e.g., operator deleted the node mid-wave). The runner discovers the deletion at no extra cost — the next `sink.setNodeState` for the deleted node is a no-op, the next `ready.popNext()` ignores deleted nodes, the wave settles without the deleted-node contributions. The sink emits a single telemetry event `node_dispatched_post_delete` per occurrence (sampled), so operators-of-the-runner can detect if the pattern is common in practice. Wave-complete summary counts the deletion-victim as `cancelled` (not as `failed.*` — the operator made the choice; not `clean` — the dispatch didn't complete).

### 2.3 Cost-guardrail hook

Before dispatch, the runner consults the count-based guardrail per [02 §8.1](02_tree_ui_affordances.md#81-the-v1-chain-preview-banner--confirm-modal--toast--drawer-panel) (`confirmThresholdCount`, default 20):

```ts
export interface CostGuardrail {
  /** Returns true if the wave is approved (operator clicked through the modal, or
   * the count was under threshold). False short-circuits the wave with state unchanged. */
  approve(estimatedCalls: number, waveTriggerKind: WaveTriggerKind): Promise<boolean>
}
```

The estimate (V1.0): **`Σ leaves (count of stale Sends on each leaf's root-to-leaf path)`** — each leaf's dispatch fires one `create_attack` plus N sequential `add_message` calls (per §3.3), and per-leaf paths are dispatched independently. Practical examples:
- Single-leaf, 10-deep stale chain: 10 calls.
- 60-leaf attempt-fan with a clean prefix: 60 calls (each leaf is its own fresh suffix; no shared interior Sends because attempt-fan children diverge at the leaf-Send itself).
- 60-leaf attempt-fan with a 10-deep shared stale prefix: 60 leaves × 10 stale-Sends-per-path = 600 calls. Each leaf re-fires the shared prefix independently. The [02 §8.1](02_tree_ui_affordances.md#81-the-v1-chain-preview-banner--confirm-modal--toast--drawer-panel) cost-guardrail modal (default `confirmThresholdCount = 20`) intercepts and asks the operator to confirm before any backend call.
- 3-leaf prompt-fan with a 5-deep shared stale prefix: 3 leaves × 5 stale-Sends-per-path = 15 calls. (V1.1 — V1.0 ships only `attempt` and `converter` axes per [01 §4.4](01_tree_primitives.md#44-structural-nodes--the-single-fan-out-primitive).)

The estimator counts what the runner will actually fire — each leaf's dispatch is independent in V1.0. No cost-based variant in V1.0 — see [02 §8.1](02_tree_ui_affordances.md#81-the-v1-chain-preview-banner--confirm-modal--toast--drawer-panel) roadmap note. **Intra-wave memoization** for shared stale interior Sends (which would collapse the 60-leaf/10-deep-shared-prefix case from 600 to 70 calls by regenerating the shared prefix once per wave) was designed in revision 14 and cut in revision 15 per reviewer Finding 2 — see [§12 Q.6](#12-open-questions) for the V1.1 follow-up.

## 3. The Dispatch Loop

### 3.1 Topological walk

```
Inputs:  treeId, set S of in-need-of-dispatch nodes
         For refreshNode/refreshSubtree/refreshTree: S = {n : n.state ∈ {'edited','stale','failed','cancelled'} AND n is within scope (subtree root or whole tree)}
         For retryFailedNodes(nodeIds): S = {nodeIds} ∪ {failed/cancelled Send ancestors on each nodeId's path}
                                          — scoped to the specific leaves the [Retry failed] toast captured
Outputs: per-node execution updates via RunnerStateSink

1. waveId ← uuid()
2. waveTriggerKind ← inferred from caller (§6.2 below)
2a. cancelled ← false                // per-wave cancel flag; flipped by sink's cancelWave (§9)
   // Tag-hygiene gate (formerly step 0) now runs at the [entry-point shim per §2.1](#entry-point-shim-ordering-v10),
   // before the cross-tab lock acquire and cost guardrail. By the time the dispatch loop
   // runs, `currentOperator()` is non-null/non-empty by construction — no need to re-check
   // here, and the previous step-0 lock leak (rev-15 Finding 4) is closed.
2b. // Retry-failed pre-readiness demotion (per §5.3 step 4).
    // Without this, S-member failed/cancelled nodes would still be in state
    // failed/cancelled when step 3's readiness allowlist runs, and the leaves below
    // them would be excluded from `ready` — silently no-op'ing the retry wave.
    // Demotion to `stale` puts them in the ancestor allowlist; their leaves enter
    // `ready` and dispatch normally; the interior failed Sends are regenerated as
    // part of each descendant leaf's fresh suffix per §3.2.
    if waveTriggerKind == 'retry_failed':
        for n in S where n.state in {'failed', 'cancelled'}:
            sink.setNodeState(treeId, n.id, 'stale', opts={'reason': null})
            sink.clearExecution(treeId, n.id)  // belt-and-suspenders; already null per [01 §6.4.1](01_tree_primitives.md#641-why-nodeexecution--null-on-failure-not-preserved)
3. ready ← { n ∈ S : n is a leaf Send AND every Send ancestor of n has node.state ∈ {edited, stale, running} or is clean }
   // Interior Sends never appear in `ready` — they are dispatched as part of their
   // descendant leaf's dispatch sequence per §3.2. The readiness rule for leaves
   // checks that the leaf's path is dispatchable: ancestors are either pending in this
   // wave (edited/stale, will be regenerated as part of the leaf's dispatch),
   // currently dispatching (running, the leaf will be added to `ready` after the
   // ancestor's completion), or previously clean (their stored pieces feed
   // prepended_conversation). `failed` and `cancelled` ancestors EXCLUDE the leaf from
   // `ready` until a separate [Retry failed] wave (§6.2 `waveTriggerKind='retry_failed'`)
   // re-admits them; this is the in-flight-cascade contract from §5.3.
4. inflight ← ∅
5. while ready ≠ ∅ or inflight ≠ ∅:
   while |inflight| < maxParallel and ready ≠ ∅:
     n ← ready.popNext()              // fair-share pick when V1.1; FIFO V1.0
     sink.setNodeState(n, 'running')
     promise ← dispatch(n, waveId, waveTriggerKind)
     inflight.add(promise)
   completed ← await Promise.race(inflight)
   inflight.delete(completed.promise)
   handleCompletion(completed)        // state transition + cascade ready set
6. // Wave-end transform reconcile (per reviewer rev-15 Finding 9 / [§3.3a](#33a-helpers-referenced-by-the-dispatch-step) `reconcileAllTransforms`).
   // The per-dispatch `reconcileTransformStates(treeId, path)` calls in §3.3 only touch
   // transforms ON the just-completed leaf's root-to-leaf path. ScoreNodes (and any
   // UserTurn/Fan) operators attach as SIBLINGS of a Send — the operator-typical
   // placement for "score this leaf's response" — are never on a dispatched leaf's path
   // and would stay `stale` indefinitely. The wave-end pass walks every node in the tree
   // once and applies the same per-node reconcile rule. O(tree-size); negligible at
   // typical 60-node trees, bounded by the 1000-node soft cap.
   reconcileAllTransforms(treeId)
   sink.emitWaveEvent({ kind: 'complete', waveId, summary })
```

**`S = {edited, stale, failed, cancelled}` — failed/cancelled stay in S, but the readiness rule excludes them from the ancestor allowlist.** S still admits failed/cancelled leaves so a separate retry wave (`waveTriggerKind='retry_failed'` per §6.2, triggered by the [02 §5.14](02_tree_ui_affordances.md#514-partial-failure-mid-refresh) toast button) can dispatch them — the leaf itself reads `state ∈ S` and is eligible. **What changed in revision 15 (per reviewer Finding 4):** the ancestor-side allowlist no longer admits `failed`/`cancelled`. An earlier framing accepted any S-member ancestor as "will be regenerated as part of the leaf's dispatch," producing retry amplification where every sibling leaf sharing a transiently-failed ancestor X would independently retry X via `add_message` in its own `fresh_suffix`. Under V1.0's no-backpressure model (Finding 6a) this amplifies a single 5xx into `min(maxParallel, sibling_count)` retries. The new rule blocks descendants of in-wave failures; the operator's [Retry failed] click starts a fresh wave with `S = {failed,cancelled,...}` whose leaves ARE now `failed` (themselves in S) with no in-wave failed-ancestor blocker, so they dispatch normally. See §5.3 for the cascade contract.

**`ready.popNext()` in V1.0** is FIFO over insertion order (which happens to be topological order). **V1.1** changes this to fair-share across multiple `ConversationTree`s — see §10.2.

**`handleCompletion`** flips the node to `clean` (on success) or `failed`, and re-evaluates `ready` for any newly-eligible descendant. A descendant becomes eligible when *all* of its parents are in `clean` state. A descendant whose parent failed stays `stale` (per [01 §6.4](01_tree_primitives.md#64-failure--partial-commit-semantics)) and never becomes ready in this wave.

### 3.2 What gets dispatched

The dispatch step varies by node kind (see [01 §4](01_tree_primitives.md#4-node-taxonomy) "side-effect class" spine):

| Side-effect class | Node kinds | Dispatch action |
|---|---|---|
| **Source** | `RootPromptNode`, `ImportMessageNode` | No backend call. State transitions to `clean` immediately; cascade. |
| **Transform** | `UserTurnNode` | No backend call. Pure local computation (resolved input bundle update). Cascade. |
| **Side-effecting** | `SendNode` (leaf or interior) | **Only leaves are picked from the `ready` queue.** A leaf's dispatch fires **one `create_attack` + N `add_message` calls** in sequence (held within one concurrency slot, §10.1) where N = the count of stale `SendNode`s on the leaf's root-to-leaf path (including the leaf itself). Each `add_message` regenerates one Send's assistant pieces; interior Sends on the path transition `running → clean` as their add_message returns. See §3.3 for the partition rule and §4.1 for the resolver. |
| **Structural** | `FanNode` | No backend call. Materializes children if needed; cascade per-child. |
| **Observational** | `ScoreNode` | **V1.0: render-only**, reads upstream `MessagePiece.scores` already attached to ancestor pieces. The runner does not enqueue ScoreNodes and never issues scorer requests. The `✏ Configure scorer` affordance is a disabled stub per [02 §2.2](02_tree_ui_affordances.md#22-per-node-action-rail). State is reconciled by the wave-end [`reconcileAllTransforms`](#33a-helpers-referenced-by-the-dispatch-step) pass at [§3.1 step 6](#31-topological-walk) — ScoreNodes attached as siblings of a Send (the operator-typical placement) are reconciled correctly, not only when they happen to sit on a dispatched leaf's path. **V1.1+:** one POST to a future `/api/scores` route per [01 §4.5](01_tree_primitives.md#45-observational-nodes-no-side-effect-on-the-conversation). **TODO:spec** — wire to the existing scorer service in V1.1. |

**Interior `SendNode`s never appear in the `ready` queue.** Per the §3.1 readiness rule, a node becomes ready when *every* parent is `clean`. Interior Sends with stale upstream are themselves stale; their leaf descendants then can't become ready (their interior-Send parent isn't `clean`). To avoid the deadlock that would otherwise result, **V1.0 treats every interior Send as part of its descendant leaf's dispatch sequence**, never an independent dispatch. The ready-set computation skips interior Sends entirely — only leaves are picked. When a leaf's dispatch runs, it claims every stale Send on its path (transitioning them `stale → running` together at dispatch start), then transitions each `running → clean` as the corresponding `add_message` returns. The §3.3 dispatch loop spells out the partition.

**Why not regenerate interior Sends as their own ARs.** Reviewer rev 10 suggested making interior Sends into "mini-leaves" with full `create_attack + add_message` pairs of their own — producing N ARs per chain refresh. Rejected because (a) it breaks AR-per-leaf (`labels.conversation_tree_id` filtering returns N×leaves rows, not leaves), (b) the History view becomes confusing (N rows per leaf with no operator-visible distinction between leaf and interior), and (c) the single-AR-with-N-add_messages model in §3.3 below uses the same total target calls without the AR-row explosion.

**Leaves with shared interior Sends — each leaf dispatches independently in V1.0.** Two leaves L1, L2 that share a stale interior Send X each regenerate X in their own dispatch sequence: L1 fires `create_attack + N add_message`s with X in its fresh suffix; L2 fires `create_attack + M add_message`s with X *also* in its fresh suffix. The target is called once per leaf for X, not once per wave. For a 60-leaf attempt-fan with a 10-deep shared stale prefix this costs 600 target calls (60 leaves × 10 stale Sends per path) rather than the 70 calls that intra-wave memoization would achieve.

**Cost ceiling.** The [02 §8.1](02_tree_ui_affordances.md#81-the-v1-chain-preview-banner--confirm-modal--toast--drawer-panel) cost-guardrail modal fires at 20 calls (default `confirmThresholdCount`), so a 600-call refresh is intercepted before any backend call goes out. The operator sees *"Refresh 600 leaves? Estimated 600 target calls. [Refresh] [Cancel]"* and decides. If they need surgical scope, [01 §6.5](01_tree_primitives.md#65-branch-from-node---the-immutable-history-primitive) `branchToNewTree` from a midpoint scopes the refresh to one path.

**Why this is V1.0-acceptable.** V1.0 ships only the `attempt` and `converter` fan axes ([01 §4.4](01_tree_primitives.md#44-structural-nodes--the-single-fan-out-primitive)). Walk both: attempt-fan children diverge at the leaf-Send (no shared interior Sends to dedupe), and converter-fan children diverge at the converter `UserTurn` (each child's downstream Sends produce different outputs because the input was converted differently). The chain-then-fan tree shape with edits high up the chain — the only shape that benefits — is a real workflow (Crescendo with depth-extension) but not the dominant V1.0 use case. V1.1 may add intra-wave memoization once telemetry quantifies the workflow's prevalence (see [§12 Q.6](#12-open-questions)).

**Tree-side X state after the wave.** Each leaf's dispatch regenerates X independently. The wave's `recordExecution` for X is determined by last-writer-wins on the leaf completion order; since interior-Send `ExecutionRecord`s collapse into the leaf AR they share, the operator sees the final X execution from whichever leaf completed last. Practically harmless because every leaf's `ExecutionRecord` carries the same `waveId` and reads the same prepended chain; the only operator-visible difference is the `conversation_id` of the leaf AR that owns the displayed X record.

**Orphan-Send case (Send with no descendants — not just no leaf descendants).** A SendNode with no children at all (operator added a Send, deleted its child UserTurn, never added a replacement) is itself a leaf per the §2 vocabulary definition. It enters `ready` and dispatches normally as a single-Send sequence (one `create_attack` + one `add_message`). No special-case behavior — the dispatch loop treats it the same as any other leaf. Operators who didn't intend to fire the orphan can delete it before the wave starts; the [02 §5.16 delete-a-branch](02_tree_ui_affordances.md#516-delete-a-branch) affordance applies.

### 3.3 Dispatch step (leaf SendNode) — partition + create_attack + sequential add_message calls

Per the §3.2 model, a leaf's dispatch is **one `create_attack` followed by N `add_message` calls in sequence**, where the N add_messages correspond to the stale Sends on the leaf's path (including the leaf itself). The partition rule:

- **Clean prefix:** Sends on the path that are `clean` (their current params match their existing execution's `resolvedInputHashAtExecution`). Their input UserTurns + their assistant-response pieces go into `prepended_conversation`. No add_message needed — these turns are pre-loaded into the AR's conversation as historical context.
- **Fresh suffix:** the first stale Send on the path and everything after (down to and including the leaf). Each `(input_user_turn, send_node)` pair becomes one sequential `add_message(send=True)` call. Each call fires the target and produces fresh assistant pieces, which become that Send's new `ExecutionRecord.pieceIds`.

The whole sequence is one AR (cleanly filterable in History by `conversation_tree_id`) and one concurrency slot.

```python
async def dispatch(leaf_send_node, waveId, waveTriggerKind):
    # Hold one concurrency slot for the whole sequence (§10.1):
    async with dispatchSemaphore:
        path = root_to_node_path(leaf_send_node)
        # Partition: returns (prepended_messages, fresh_suffix_pairs).
        # - prepended_messages: list[PrependedMessageRequest], one per turn in clean prefix.
        # - fresh_suffix: list[(UserTurnNode, fan_variant_or_None, SendNode)] in topo order.
        #   Each entry includes the fan-variant (axis, slot) the resolver captured if a Fan
        #   ancestor sits between the UserTurn and this Send; None otherwise.
        prepended, fresh_suffix = resolve_path_partition(path)  # §4.1
        if len(prepended) > 200:  # Backend cap is on prepended_conversation only (max_length=200).
            sink.setNodeState(treeId, leaf_send_node.id, 'failed',
                              opts={'reason': 'clean prefix exceeds 200 turns; branch from a midpoint to continue'})
            # Reconcile transform ancestors so any UserTurn/Fan/Score that were `stale`
            # waiting on this leaf settle correctly. With the leaf now `failed`, the
            # reconciler's "all descendants clean" check is false for them — they stay stale —
            # but the walker itself is idempotent and safe to invoke here.
            reconcileTransformStates(treeId, path)
            return

        # Mark all stale Sends in fresh_suffix as `running` together (interior + leaf).
        # Each leaf's dispatch regenerates its own copy of any shared interior Sends —
        # V1.0 has no intra-wave memoization (per §3.2; deferred to V1.1 per §12 Q.6).
        for _, _, send_node in fresh_suffix:
            sink.setNodeState(treeId, send_node.id, 'running')

        # The post-cap body is wrapped in try/finally so reconcileTransformStates runs
        # on every dispatch outcome — success, create_attack failure, or mid-chain
        # add_message failure. Without the finally, a mid-chain failure that left some
        # Sends `clean` would leave their UserTurn ancestors lingering in `stale` because
        # the post-loop reconcile call was never reached (the failure path `return`s early).
        # See [§3.3a `reconcileTransformStates`](#33a-helpers-referenced-by-the-dispatch-step) —
        # the walker is idempotent and bounded by path length; the per-dispatch invocation
        # is cheap regardless of outcome.
        try:
            # Call #1 — create_attack: setup only, no target call.
            # Returns attack_result_id AND conversation_id; we need conversation_id for add_message.
            try:
                create_resp = await attacksApi.createAttack(CreateAttackRequest(
                    target_registry_name=path.target,
                    prepended_conversation=prepended,
                    labels=_build_labels(path, treeId, waveId, waveTriggerKind),
                ))
            except ApiError as e:
                reason = _format_api_error(e, 'create_attack')   # §3.3a — discriminates 4xx vs. 5xx for retry UX
                for _, _, send_node in fresh_suffix:
                    sink.setNodeState(treeId, send_node.id, 'failed', reason=reason)
                    sink.clearExecution(treeId, send_node.id)
                return

            # Calls #2..N+1 — one add_message per (UserTurn, fan_variant, Send) in fresh_suffix.
            # Each call fires the target; assistant pieces become that Send's new execution.
            # `prior_max_turn_number` tracks the highest turn_number already in the AR so the
            # next call's response can be diffed to find new pieces (see §3.3a
            # `_extract_new_assistant_pieces`). Backend turn_number is 1-indexed; len(prepended)
            # is the count of messages create_attack just persisted, so that's the starting max.
            prior_max_turn_number = len(prepended)
            for idx, (input_ut, fan_variant, send_node) in enumerate(fresh_suffix):
                try:
                    add_resp = await attacksApi.addMessage(create_resp.attack_result_id,
                        AddMessageRequest(
                            role='user',
                            pieces=_pieces_for_user_turn(input_ut, fan_variant),
                            send=True,
                            target_registry_name=path.target,
                            target_conversation_id=create_resp.conversation_id,
                            converter_ids=_resolved_converter_ids(input_ut, fan_variant),
                            labels=_build_labels(path, treeId, waveId, waveTriggerKind),
                        ))
                except ApiError as e:
                    # Partial-commit: this Send (and any after it in the chain) fail.
                    # Per [01 §6.4.1], failed Sends have their execution nulled so the
                    # resolver correctly identifies them as needing fresh dispatch on retry.
                    reason = _format_api_error(e, 'add_message')
                    sink.setNodeState(treeId, send_node.id, 'failed', reason=reason)
                    sink.clearExecution(treeId, send_node.id)
                    # Sends after this in fresh_suffix were marked `running` at dispatch start;
                    # flip back to stale and clear their executions too.
                    for _, _, later_send in fresh_suffix[idx + 1:]:
                        sink.setNodeState(treeId, later_send.id, 'stale')
                        sink.clearExecution(treeId, later_send.id)
                    return
                # Record the Send's new ExecutionRecord. AR id is the leaf's AR (shared across
                # all Sends on the chain); pieceIds are the fresh assistant pieces from add_resp
                # (extracted via turn-number diff per §3.3a `_extract_new_assistant_pieces`).
                new_pieces, prior_max_turn_number = _extract_new_assistant_pieces(
                    add_resp, prior_max_turn_number,
                )
                record = build_execution_record(
                    attack_result_id=create_resp.attack_result_id,
                    conversation_id=create_resp.conversation_id,
                    assistant_pieces=new_pieces,
                    waveId=waveId,
                    waveTriggerKind=waveTriggerKind,
                )
                sink.recordExecution(treeId, send_node.id, record)
                sink.setNodeState(treeId, send_node.id, 'clean')
        finally:
            # Reconcile non-Send transform states regardless of outcome (§3.3a). Correctly
            # handles full success (all UserTurn ancestors flip clean), partial success on
            # mid-chain failure (UserTurn ancestors of the succeeded prefix flip clean;
            # ancestors of the failed/stale suffix stay stale), and create_attack failure
            # (no Sends became clean; no ancestors flip).
            reconcileTransformStates(treeId, path)
```

**Why hold the semaphore for the whole sequence.** The N+1 calls all target the same AR (via `target_conversation_id = create_resp.conversation_id`) and reference state created by earlier calls in the sequence. Releasing the slot between calls would let other leaves race for it while this leaf is waiting on a mid-chain `add_message`, and the runner's per-tree serialization would no longer reflect actual in-flight calls. Holding the slot keeps the budget honest: `maxParallel=4` concurrent leaves = at most 4 active operator-meaningful chains, regardless of chain depth.

**Partial-commit on mid-chain failure.** If `add_message` #3 of a 5-message sequence fails, the AR exists with the first 2 user turns + assistant responses successfully sent. The failed Send transitions to `failed`; Sends 4 and 5 transition back to `stale` (they were `running` before; the chain stopped before reaching them). The leaf shows `failed` because its add_message was never reached. The runner's `handleCompletion` then runs the §5.3 in-flight cascade: any sibling leaves in `ready` whose path includes the failed Send are dropped to `blocked` so they don't independently retry the same failure. The operator's retry from the toast re-dispatches the whole leaf, which:

- Creates a brand-new AR (does not reuse the partial AR; see §7.5 below for the "no retry fast-path in V1.0" decision).
- Re-fires all stale Sends on the path. The previously-succeeded Sends in the prior partial dispatch are no longer reachable through this dispatch (their `ExecutionRecord`s point to the previous AR, which still exists in History as a partial row).

**Field reference (verified against backend, [pyrit/backend/models/attacks.py](../../../pyrit/backend/models/attacks.py)):**

- `CreateAttackRequest.prepended_conversation: list[PrependedMessageRequest] | None` — max 200 messages.
- `PrependedMessageRequest = { role: ChatMessageRole, pieces: list[MessagePieceRequest] }` — one message per turn; multimodal turns have multiple pieces in one PrependedMessageRequest.
- `AddMessageRequest = { role, pieces, send, target_registry_name, target_conversation_id, converter_ids, labels }` — `target_conversation_id` is **required always**; `target_registry_name` is required when `send=True`.
- `CreateAttackResponse = { attack_result_id, conversation_id, created_at }` — the runner needs both ids; `conversation_id` flows into the second-and-later `add_message` calls.

**Idempotency.** The runner does not deduplicate. If the operator double-clicks Refresh, **two waves fire, two leaf AR sequences land** (cost ≈ 2× tokens). The §3.3b debounce catches the common case; the cost-guardrail modal (§2.3) catches the above-threshold case.

### 3.3a Helpers referenced by the dispatch step

The §3.3 pseudocode uses several helpers that need explicit specs (the implementer cannot guess them from the call sites alone).

**`_extract_new_assistant_pieces(add_resp, prior_max_turn_number)`** — `AddMessageResponse.messages` is a `ConversationMessagesResponse` (verified against [pyrit/backend/models/attacks.py L153-L157](../../../pyrit/backend/models/attacks.py#L153)) whose `.messages: list[Message]` carries the **entire conversation**, not just the new pieces. Each `Message` has `.turn_number` (1-indexed), `.role`, `.pieces: list[MessagePiece]`. The runner identifies just-added assistant pieces by turn-number diff: before each `add_message` call, hold `prior_max_turn_number` (initialized to `len(prepended_conversation)` after `create_attack` returns, since `turn_number` is 1-indexed); after the call returns, walk `add_resp.messages.messages` and collect pieces from any Message whose `turn_number > prior_max_turn_number` and `role == 'assistant'`. Update `prior_max_turn_number` for the next iteration.

```python
def _extract_new_assistant_pieces(add_resp, prior_max_turn_number):
    new_pieces = []
    new_max = prior_max_turn_number
    for msg in add_resp.messages.messages:   # AddMessageResponse.messages: ConversationMessagesResponse
        if msg.turn_number > prior_max_turn_number and msg.role == 'assistant':
            new_pieces.extend(msg.pieces)
            new_max = max(new_max, msg.turn_number)
    return new_pieces, new_max
```

If V1.1 adds a backend `?since_turn=N` filter, this helper collapses to one extend call; the V1.0 walk is O(messages-in-AR) per add_message, which is bounded by the 200-message cap.

**`_format_api_error(error, call_name)`** — classifies an API error into one of three failure classes for retry UX: `'transient'` (5xx + network/timeout; retry-eligible), `'rate_limited'` (HTTP 429 + provider-specific overloaded errors; retry-eligible but gated until the operator manually re-triggers), `'permanent'` (4xx other than 429: validation, operator-lock mismatch, target-not-found; retry-ineligible without operator action). The wave-complete toast ([02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances)) reads `error.failure_class` to decide the [Retry failed] button gating and per-class summary count.

```python
def _format_api_error(error, call_name):
    if error.status_code is None:                # network error / timeout
        return ApiErrorReason(
            message=f"{call_name} failed (network): {error.message} — likely transient, retry",
            failure_class='transient',
        )
    if error.status_code == 429 or _is_provider_rate_limit_shape(error):
        # Provider-specific shapes: Anthropic overloaded_error, OpenAI rate_limit_exceeded,
        # Azure-specific. See [Q.G.1](#12-open-questions) for the small detection registry.
        return ApiErrorReason(
            message=f"{call_name} rate-limited ({error.status_code}): {error.message} — wait for the target's rate-limit window, then retry",
            failure_class='rate_limited',
        )
    if 500 <= error.status_code < 600:
        return ApiErrorReason(
            message=f"{call_name} failed ({error.status_code}): {error.message} — transient, retry",
            failure_class='transient',
        )
    if error.status_code == 400 and 'operator' in (error.message or '').lower():
        return ApiErrorReason(
            message=f"{call_name} blocked by operator lock — branch from this node to take ownership",
            failure_class='permanent',
        )
    return ApiErrorReason(
        message=f"{call_name} failed ({error.status_code}): {error.message}",
        failure_class='permanent',
    )
```

The leaf's stored `lastError` carries both fields. Wave-summary aggregation counts each leaf's terminal `failure_class` into the toast's three-class breakdown (`failed` / `rate_limited` / `permanent`). The [Retry failed] button is enabled when at least one leaf has `failure_class ∈ {'transient', 'rate_limited'}` AND no rate-limited-only state — i.e., button is disabled when *every* failed leaf is `rate_limited` (the operator must wait); enabled when *any* failed leaf is `transient` (button retries only the transient subset; rate-limited leaves stay failed in the toast and a follow-up manual Refresh tree retries them once the operator believes the window has cleared). Tooltip text follows the gating: rate-limited-only → *"All N failed leaves were rate-limited. Wait for the target's rate-limit window to clear, then click Refresh tree to retry."*; mixed → *"Retrying N transient failures; M rate-limited leaves are excluded and remain failed in the wave summary."* V1.x adds `Retry-After` header parsing and a countdown timer (see [§12 Q.7](#12-open-questions)).

**`_root_prompt_as_user_turn(root_node)`** — promotes a `RootPromptNode` into the shape `_make_user_turn_message` expects. The `text` becomes the user-turn text; the `attachments` become the user-turn attachments. `systemPrompt` does NOT become part of this user turn — it routes separately (see below).

**`_systemPrompt_as_prepended_message(root_node)`** — `CreateAttackRequest` has no `systemPrompt` field (verified against [pyrit/backend/models/attacks.py L221-L243](../../../pyrit/backend/models/attacks.py#L221)). The backend pattern for system prompts is `PrependedMessageRequest` with `role='system'` as the first prepended message. When `root_node.params.systemPrompt` is non-empty, the resolver prepends a synthetic system message to the `prepended` list:

```python
def _systemPrompt_as_prepended_message(root_node):
    if not root_node.params.systemPrompt:
        return None
    return PrependedMessageRequest(
        role='system',
        pieces=[MessagePieceRequest(
            role='system',
            original_value=root_node.params.systemPrompt,
            converted_value=root_node.params.systemPrompt,
            original_value_data_type='text',
            converted_value_data_type='text',
        )],
    )
```

The system message is always at sequence 0 (first in `prepended_conversation`). Counts against the 200-message cap. If absent, the AR has no system message — same as today's chat tab default.

**`reconcileTransformStates(treeId, path)`** — non-Send nodes (UserTurn, Fan, Score) are marked `stale`/`edited` by the [01 §6.3 propagation rules](01_tree_primitives.md#63-propagation-rules) but the runner's dispatch loop only transitions Send-state. After each successful Send completion, the runner walks back up the path and flips any `stale` UserTurn / Fan / Score whose ancestors are now all `clean` back to `clean`. Without this, the canvas shows lingering yellow borders on transform nodes after a fully-successful refresh.

```python
def reconcileTransformStates(treeId, path):
    """Walk ancestors of just-completed Sends; flip transforms to clean when ancestors are clean."""
    for node in path:
        if isinstance(node, (UserTurnNode, FanNode, ScoreNode)):
            if node.state == 'stale' and all(p.state == 'clean' for p in node.parents):
                sink.setNodeState(treeId, node.id, 'clean')
```

Called after each `sink.recordExecution + setNodeState(clean)` on a Send in the §3.3 dispatch loop. Idempotent: a node already `clean` is unchanged.

**`reconcileAllTransforms(treeId)`** — the wave-end sibling helper. Same per-node rule as `reconcileTransformStates`, but iterates **every** node in the tree (not just the path). Called once at §3.1 step 6 prologue, after the dispatch loop settles and before `emitWaveEvent({ kind: 'complete' })`. Catches transforms (especially ScoreNodes) attached as siblings of Sends rather than on a dispatched leaf's path — the operator-typical ScoreNode placement that the path-scoped `reconcileTransformStates` cannot reach.

```python
def reconcileAllTransforms(treeId):
    """Walk every transform node in the tree once; flip stale→clean where ancestors are clean."""
    tree = workspace.currentTree
    for node in tree.nodes:
        if isinstance(node, (UserTurnNode, FanNode, ScoreNode)):
            if node.state == 'stale' and all(p.state == 'clean' for p in node.parents):
                sink.setNodeState(treeId, node.id, 'clean')
```

Idempotent and cheap (O(tree-size) once per wave); the per-dispatch calls remain in place so canvas state catches up incrementally as leaves settle, and the wave-end pass ensures sibling transforms reconcile too.

**`_pieces_for_user_turn(user_turn, fan_variant)` and `_resolved_converter_ids(user_turn, fan_variant)`** — straightforward: the former builds the `MessagePieceRequest` list (attachments + text) for the user turn, applying any `converter` fan-axis variant payload that overrides the in-path UserTurn's params; the latter resolves the converter pipeline (the UserTurn's `converterPipeline` plus any fan-variant converter list) into the `converter_ids` list the backend's converter machinery expects.

**`_build_labels(path, treeId, waveId, waveTriggerKind) → Record<string, string>`** — builds the labels dict that gets sent on every `CreateAttackRequest` and `AddMessageRequest` in the leaf's dispatch sequence. All keys are present in every wave's calls per the [§4.3 piece-label divergence invariant](#43-label-writes-the-round-trip-fidelity-contract). Conditional fields are omitted (not `null` or empty-string) when not applicable so the backend's `_resolve_labels` ([attack_service.py:L716](../../../pyrit/backend/services/attack_service.py#L716)) doesn't fall back to existing-piece labels for a key that should remain unset.

```python
def _build_labels(path, treeId, waveId, waveTriggerKind) -> dict[str, str]:
    """Returns the labels dict for every CreateAttackRequest and AddMessageRequest
    in a leaf's dispatch sequence (§4.3 invariant: identical across all calls)."""
    tree = path.tree                                 # the ConversationTree the leaf lives in
    operator = currentOperator()
    assert operator is not None and operator != '', (
        "tag-hygiene gate bypassed: _build_labels reached with no operator. "
        "The §2.1 entry-point shim step 1 must abort the wave with WaveEvent "
        "'operator_tag_required' before dispatch reaches here. See 'Missing operator "
        "tag handling' below for the contract."
    )
    labels = {
        'operator': operator,
        'operation': tree.operation or '',           # operator-selected at tree creation; '' if not set
        'conversation_tree_id': str(treeId),
        'wave_id': waveId,
        'wave_trigger_kind': waveTriggerKind,
        'tree_path': json.dumps(path.tree_path_segments),  # always present; '[]' for fan-less leaves
    }
    # parent_conversation_tree_id: only on cloned trees (set by branchToNewTree, [01 §6.5]).
    # OMITTED for fresh trees (newTree, openTree from History without a parent). The
    # auto-reverse path reads this key and treats absence as "no parent" — safer than
    # writing the empty string, which History "Open clones of" would surface as a row
    # claiming the tree is its own parent.
    if tree.parentConversationTreeId is not None:
        labels['parent_conversation_tree_id'] = str(tree.parentConversationTreeId)
    return labels
```

**Missing operator tag handling (tag-hygiene gate).** `operator` is a tag the operator picks for their work — not an auth claim. The tag is what powers History filtering ("show me all my work"), per-operator `_validate_operator_match` isolation on the backend (operator-Y can't `add_message` against operator-X's tagged ARs), and the §15 audit log's work-attribution column. Under normal operation, the [§2.1 entry-point shim step 1](#entry-point-shim-ordering-v10) prevents any wave from dispatching when `currentOperator()` returns null/empty — `_build_labels` is never invoked in the missing-tag state, so no `operator: ''` AR is ever created. The UI surfaces a per-action modal (the runner's `WaveEvent { kind: 'operator_tag_required' }` triggers it, see [02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances)) so the operator sets a tag and re-triggers; the wave-start gate fires once at the canvas-level click moment, not per-leaf.

**Hard assertion at dispatch time — no defense-in-depth fallback.** `_build_labels` includes `assert operator is not None and operator != ''` at its entry. If the shim's tag-hygiene gate is somehow bypassed (test fixture that mocks the gate, future runner refactor that misses the gate, mid-wave tag-cleared race), the assertion fires and the dispatch panics rather than silently writing `operator: ''` ARs. Reviewer rev-16 caught that an earlier defense-in-depth path that wrote `operator: ''` was **broken under the previously-spec'd [§9.4.5 backend tightening](01_tree_primitives.md#945-hard-backend-dependency-relocate-_validate_operator_match)** (since-reverted per Q.S.2 rev 18 — see that section's body): the tightened `_validate_operator_match` would have raised an operator-mismatch error against requests with an empty operator label, so the supposed defense-in-depth ARs would always 400 at the first `add_message`. Even with Q.S.2 reverting the tightening (the no-labels early-return is preserved, so empty operator now passes through), the assert-and-panic path is still the right choice because (a) silently writing `operator: ''` ARs is operator-hostile regardless of backend response — the audit trail loses authorship; (b) the asymmetry of "which backend version is deployed" was itself a hazard. Rev-16 chose the assert-and-panic path: the gate IS the contract; defense-in-depth-by-empty-string was a non-functional rationalization. The earlier "empty-string is grep-able in History" argument also failed under the tightening since those records never get created past the first message.

**`tree_path` segments are computed once per dispatch.** `path.tree_path_segments` is `list[tuple[str, int]]` — the (axis, slotIndex) tuples for every `FanNode` ancestor on the leaf's root-to-leaf path, in topo order. Computed from the path itself (no separate state); JSON-encoded inside `_build_labels`. Empty array for leaves with no fan ancestors; encoded as `'[]'` (the parser per [§4.3 tree_path encoding](#tree_path-encoding-v10-json-to-keep-forward-compatible) accepts both `'[]'` and absence).

**Piece-fetch caching for `_load_piece_as_request(pid)`** in `_load_send_response_as_message` (§4.1). The backend exposes **no piece-by-id endpoint** ([routes/attacks.py](../../../pyrit/backend/routes/attacks.py) lists only conversation-level reads); the only read path for piece data is `GET /api/attacks/{attack_result_id}/messages` which returns every piece for one AR's conversation. The cache is populated **at wave-start** by a pre-fetch pass: the runner walks each leaf's clean-prefix Sends, collects the distinct source-AR ids referenced by those Sends' `execution.attackResultId` fields, and issues **one `GET /messages` per distinct AR** (not one per piece). Each response's pieces all land in `pieceCache` keyed by `piece.id`; `_load_piece_as_request(pid)` then resolves from the cache without per-piece HTTP. For a 60-leaf wave with 10-deep clean prefixes referencing 5 distinct source ARs, the pre-fetch issues 5 HTTPs, populates ~300 pieces, and avoids the ~600 per-piece round-trips the cache name initially suggested. Cache lifetime is one wave (cleared on wave-complete) to keep memory bounded; cross-wave reuse is not attempted because intervening Refresh activity may have invalidated piece content. *Backend note:* a future `GET /api/pieces/{id}` endpoint would let the cache become lazy (fetch-on-miss) instead of pre-fetch, but isn't needed for V1.0 — conversation-level reads are cheap and already paid for in the auto-reverse path (§9.3).

### 3.3b Debounce on `refreshTree` / `refreshSubtree`

V1.0 firm: the refresh button handler debounces user clicks at **250 ms** before dispatching. The debounce is in the UI button handler, not in the runner — the runner's API is intentionally fire-and-trust. Double-clicking the button within 250 ms collapses to one runner invocation.

**Single debounce module across UI surfaces.** The debounce module lives at `frontend/src/ui/refreshHandlers.ts` and exposes one hook `useDebouncedRefresh()` plus one global event emitter `refreshBus` (a singleton `EventTarget`). The wiring:

- **Ribbon button** (`<RefreshButton>` component): calls `useDebouncedRefresh().refreshTree(treeId)` on click. Hook-internal `setTimeout` enforces the 250 ms window.
- **Right-click "Refresh subtree"** (in the [react-flow context menu](https://reactflow.dev/api-reference/components/context-menu)): calls the same hook via the menu item's `onClick`.
- **`R` keyboard shortcut** (registered in `<TreeCanvas>`'s `onKeyDown`): dispatches `refreshBus.dispatchEvent(new CustomEvent('refresh_subtree_request', { detail: { treeId, nodeId } }))`; the hook listens to `refreshBus` and routes through the same debounce.
- **Cross-surface coalescing:** the hook stores `lastFireAtByTree: Map<ConversationTreeId, number>`; any call within 250 ms of the previous fire (regardless of surface) is dropped. The bus pattern is just to avoid prop-drilling the hook into every component.

The `frontend/src/runner/runner.ts` module does NOT depend on the debounce module — the runner is invoked by the hook, not the other way around. This keeps the runner test surface clean of UI concerns.

**Operator override:** shift-click or Cmd-click bypasses the debounce and fires a second wave immediately, for operators who actually want N waves back-to-back. The escape hatch keeps the debounce from blocking power users.

**Why this matters.** A 60-leaf refresh whose second wave fires from a double-click = 120 AR sequences = $$$ at typical model prices. The cost-guardrail modal (default `confirmThresholdCount = 20`) only intercepts the *first* click in a double-click; the second click already cleared the modal and fires unmodaled. Debouncing in the UI is the only reliable defense.

## 4. Per-leaf AR Materialization

### 4.1 The resolved root-to-leaf path → (prepended, final user turn)

For a leaf `SendNode` L, walk parents to the root and partition the path's Sends into a **clean prefix** (Sends whose current params still match their executions — their input UserTurns and stored assistant pieces can be loaded into `prepended_conversation` as historical context) and a **fresh suffix** (the first stale Send and everything after, down to the leaf — each (input UserTurn, Send) pair becomes one sequential `add_message` call per §3.3).

This partition is the central trick that makes Option A work: an N-deep stale chain becomes one AR with `prepended_conversation` covering everything above the first stale Send, plus N sequential `add_message` calls to regenerate the stale Sends in topo order. The leaf and all its interior-Send ancestors share one AR; History stays clean.

```python
def resolve_path_partition(path):
    """Returns (prepended, fresh_suffix).

    - prepended: list[PrependedMessageRequest], one entry per turn in the clean prefix.
      Multimodal turns (e.g. user text + image) become ONE PrependedMessageRequest with
      multiple pieces (max 50 per the backend model). The backend caps prepended length
      at 200 messages.
    - fresh_suffix: list[(UserTurnNode, fan_variant_or_None, SendNode)] in topo order,
      each entry becoming one add_message(send=True) call. The last element is always
      (leaf_input_user_turn, leaf_fan_variant_or_None, leaf).

    V1.0 has no intra-wave shared-piece cache (per §3.2 V1.0-decision; deferred to V1.1
    per §12 Q.6). Each leaf's dispatch independently regenerates every stale Send on
    its path — if multiple leaves share a stale interior Send, the target is called
    once per leaf for that Send.

    The path is `[Source, UserTurn, Send, UserTurn, Fan, Send, ...]` (per [01 §5.1 invariant 5](01_tree_primitives.md#51-invariants) — a Send's *first non-Fan, non-Score ancestor* on the path is always a UserTurn with `role='user'` or a RootPromptNode). FanNode and ScoreNode pass through transparently; the resolver holds `pending_user_turn` across Fan/Score boundaries so a Send inside a Fan(attempt) picks up the Fan's parent UserTurn (with fan-variant override applied at piece-construction time).
    """
    prepended = []
    fresh_suffix = []
    pending_user_turn = None         # UserTurn waiting to be paired with the next Send (held across Fan/Score)
    pending_fan_variant = None       # axis+slot for the most recent Fan ancestor; resets when we exit the Fan
    seen_first_stale = False

    for node in path:
        if isinstance(node, RootPromptNode):
            # Root prompt is the first user-role turn; treat its text as a UserTurn input
            # for the first Send. systemPrompt (if any) routes through PrependedMessageRequest
            # with role='system' as the FIRST prepended message — there is no systemPrompt
            # field on CreateAttackRequest (verified against backend models/attacks.py).
            # See §3.3a `_systemPrompt_as_prepended_message` for the helper spec.
            sys_msg = _systemPrompt_as_prepended_message(node)
            if sys_msg is not None:
                prepended.append(sys_msg)
            pending_user_turn = _root_prompt_as_user_turn(node)
            pending_fan_variant = None
        elif isinstance(node, UserTurnNode):
            # Hold this UserTurn until we see its downstream Send. Reset the fan-variant
            # cursor — a new UserTurn means we're past any fan whose variant applied to
            # a previous UserTurn.
            pending_user_turn = node
            pending_fan_variant = None
        elif isinstance(node, SendNode):
            assert pending_user_turn is not None, (
                "tree-shape invariant ([01 §5.1] #5): every Send has a UserTurn/Root "
                "ancestor on the path (Fan/Score may sit between them transparently)"
            )
            # Per §3.1, S = {edited, stale, failed, cancelled}. The state check covers all
            # four explicitly; the `execution is None` clause is the safety net for
            # failed/cancelled (per [01 §6.4.1] they have execution=null) and for the
            # rare case of a leaf with no prior execution at all (freshly-added Send
            # that's never been refreshed).
            is_stale = (node.state in {'edited', 'stale', 'failed', 'cancelled'}) or (node.execution is None)
            if not seen_first_stale and not is_stale:
                # Still in the clean prefix: load this turn's input + assistant response from storage.
                prepended.append(_make_user_turn_message(pending_user_turn, pending_fan_variant))
                prepended.append(_load_send_response_as_message(node))  # role='assistant', multimodal ok
            else:
                seen_first_stale = True
                # Fresh suffix: this pair will fire via add_message in §3.3. The variant
                # is carried alongside the UserTurn so add_message gets the right converter_ids
                # and piece content.
                fresh_suffix.append((pending_user_turn, pending_fan_variant, node))
            # The Send "consumes" the pending UserTurn — next iteration needs a fresh one
            # (typically supplied by the next UserTurn or RootPromptNode in the path).
            pending_user_turn = None
            pending_fan_variant = None
        elif isinstance(node, FanNode):
            # Structural pass-through. Capture which (axis, slot) we're descending into so
            # the resolver can apply the variant payload to the downstream Send's content.
            # The path's downstream node carries the chosen child's slot index in its
            # edge.slotIndex; the resolver reads it here. pending_user_turn is held across
            # the Fan (NOT cleared) so a Fan(attempt) directly above a Send works correctly:
            # the Send's input is the UserTurn ABOVE the Fan, varied by the fan's variant.
            pending_fan_variant = (node.params.axis, path.edge_slot_for(node))
        elif isinstance(node, ScoreNode):
            # Observational pass-through; no piece contribution. Holds pending_user_turn
            # and pending_fan_variant unchanged.
            pass

    # Sanity: the leaf must always be the last element of fresh_suffix; if a leaf
    # path ends with everything clean, the leaf itself must be in fresh_suffix because
    # the operator wouldn't have triggered a dispatch on a clean node.
    assert fresh_suffix and fresh_suffix[-1][2].id == path[-1].id, \
        "fresh_suffix invariant: ends at the leaf Send"

    return (prepended, fresh_suffix)


def _make_user_turn_message(user_turn_or_root) -> PrependedMessageRequest:
    """Build a PrependedMessageRequest from a UserTurnNode or RootPromptNode-as-user-turn.
    Multimodal pieces (text + attachments) are bundled into one message."""
    return PrependedMessageRequest(
        role=user_turn_or_root.role,    # 'user' | 'system' | 'simulated_assistant'
        pieces=[_piece_from_attachment(a) for a in user_turn_or_root.attachments]
               + [_piece_for_text(user_turn_or_root.text, user_turn_or_root.converter_pipeline)],
    )


def _load_send_response_as_message(send_node) -> PrependedMessageRequest:
    """Load the assistant pieces from a clean Send's prior execution into ONE message.

    Each piece carries forward its original_prompt_id so lineage chains stay intact
    across re-prepends. The §9.4.4 (b) DTO extension exposes this field on
    BackendMessagePiece; `_load_piece_as_request` reads it and writes it onto the
    new MessagePieceRequest. The backend's MessagePieceRequest accepts
    original_prompt_id as an optional field; absent → fresh lineage root.
    """
    assert send_node.execution is not None, "clean Send must have an execution"
    return PrependedMessageRequest(
        role='assistant',
        pieces=[_load_piece_as_request(pid) for pid in send_node.execution.pieceIds],
    )


def _load_piece_as_request(piece_id) -> MessagePieceRequest:
    """Fetch the BackendMessagePiece (cached per-wave, §3.3a) and copy its fields
    into a MessagePieceRequest, preserving original_prompt_id for lineage."""
    piece = pieceCache.get(piece_id)  # cached for the duration of the current wave
    return MessagePieceRequest(
        data_type=piece.original_value_data_type,
        original_value=piece.original_value or '',
        converted_value=piece.converted_value,
        mime_type=piece.original_value_mime_type,
        original_prompt_id=piece.original_prompt_id,  # PRESERVE lineage (§9.4.4 b dep)
        prompt_metadata=piece.prompt_metadata,
    )
```

**Why partition.** Sends whose params haven't changed since they last executed have valid stored pieces — re-firing them is wasteful and yields different responses (target nondeterminism). Sends whose params changed need to re-fire to get a response that matches the new input. The partition is the natural boundary between the two.

**Why interior Sends in the fresh suffix don't need their old `execution.pieceIds`.** They're about to be regenerated. Their old pieces become stale `ExecutionRecord` entries in `executionHistory` (per §6.6) — operators can checkout-detached to inspect, but the runner doesn't reference them in the new dispatch.

**Why interior Sends in the clean prefix DO need their old `execution.pieceIds`.** They're not being regenerated, so the target needs to see their prior assistant responses as historical context in `prepended_conversation`.

**Leaf-only path with all-clean upstream.** Say the operator just hit `↻` on a leaf (the leaf itself is `edited` because they tweaked its input UserTurn, but everything upstream is `clean`). The partition produces:
- `prepended` = [Root user turn, Send1 assistant, UserTurn2, Send2 assistant, …, leaf's-parent-UserTurn's-prior-version, leaf's-prior-Send-assistant-if-it-existed]
- `fresh_suffix` = [(leaf_input_user_turn_new_params, leaf)]

Wait — the partition rule above marks the leaf as stale iff `node.state in {'stale', 'edited'} or node.execution is None`. A leaf the operator just tweaked has the *node above it* (the UserTurn) edited; the leaf Send itself is `stale` (per §6.3 rule 1) because its ancestor changed. So the leaf is in fresh_suffix. ✓

**Fan axis variant resolution (V1.0 axes).** When `path` traverses a `FanNode`, the path itself selects which child UserTurn is visited; the variant payload is resolved at piece-construction time inside `_make_user_turn_message`:

- `axis='attempt'`: variant payload is empty `{}`; all attempts share identical `prepended` + identical `fresh_suffix` pieces (the AR id and creation timestamp differ).
- `axis='converter'`: the fan child's `converters: ConverterRef[]` is appended to the input UserTurn's `converter_pipeline` before piece construction. The `converted_value` differs per leaf. The runner also passes `converter_ids` on the corresponding `add_message` so the backend's converter machinery is engaged — without this, the converter axis does nothing at runtime. (V1.0 carries this in `AddMessageRequest.converter_ids` per the §3.3 dispatch code.)

V1.1 axes (`prompt`, `target`, `system_prompt`, `temperature`) plug into the same resolver — the variant payload overrides a specific field on the in-path node (per [01 §4.4 FanVariant types](01_tree_primitives.md#44-structural-nodes--the-single-fan-out-primitive)).

### 4.2 The 200-message cap

`CreateAttackRequest.prepended_conversation` is capped at 200 messages by the backend model ([attacks.py L221-L243](../../../pyrit/backend/models/attacks.py#L221)). The cap is on `PrependedMessageRequest` count (messages, not pieces — a multimodal turn with 3 pieces is one message). **The cap applies only to `prepended_conversation`**; the backend does not cap conversation length grown via subsequent `add_message` calls.

**The runner checks `len(prepended) > 200`** before dispatching. If over, the runner short-circuits before `create_attack` and the leaf transitions to `failed` with reason `"clean prefix exceeds 200 turns; branch from a midpoint to continue"`. The post-dispatch `add_message` sequence adds 2×N messages (one user + one assistant per Send in fresh_suffix) to the conversation but those don't count against this cap — they extend the AR's conversation past 200 messages cleanly. *Earlier revisions used `len(prepended) + len(fresh_suffix)` as a conservative estimate; this rejected valid dispatches whose `prepended` was under 200 but whose total post-`add_message` length exceeded it, even though the backend would have accepted them.*

Under AR-per-leaf the cap is **per-root-to-leaf-path's clean prefix** — a tree with 1000 leaves at 10 turns deep is fine; only a leaf whose *clean prefix alone* exceeds 200 turns trips the cap. Operationally this is unreachable until a tree has accumulated 200+ clean Sends on a single chain, which is several waves' worth of refresh on a Crescendo-style depth-extending attack.

**V1.0 recovery path:**

- **Soft warning at 180 turns of clean prefix** in the canvas-level ribbon ([02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances)): *"This conversation is approaching the 200-turn prepended ceiling. Use Branch from a midpoint to keep extending."*
- **Hard refusal at 200 clean-prefix turns**: leaf goes `failed`; tooltip points at `📋` (`branchToNewTree`, V1.0-shipped per [01 §6.5](01_tree_primitives.md#65-branch-from-node---the-immutable-history-primitive)) as the recovery primitive. Operator picks a midpoint node, clicks `📋`, edits the midpoint's text to summarize the truncated prefix, and continues from there.
- The recovery is operator-driven; the runner does not auto-truncate (would silently change the conversation context the target sees).

### 4.3 Label writes (the round-trip-fidelity contract)

Every dispatched AR carries:

| Label | Source | Version | Why |
|---|---|---|---|
| `operator` | Current user (per [01 §9.1](01_tree_primitives.md#91-operator-isolation-posture)) | V1.0 | Operator-isolation check; the V1.0 PR set carries the [§7.4 / §9.4.5](01_tree_primitives.md#945-hard-backend-dependency-relocate-_validate_operator_match) relocation so the server-side check survives `removed_in="0.16.0"` piece-label deprecation |
| `operation` | Operator-selected (existing chat flow) | V1.0 | History grouping |
| `conversation_tree_id` | `tree.id` | V1.0 | Groups all leaves from one tree (per [01 §2 Vocabulary](01_tree_primitives.md#2-vocabulary)) |
| `wave_id` | `waveId` (generated in §3.1) | V1.0 | Groups leaves from one operator action |
| `wave_trigger_kind` | One of [01 §14.1 enum](01_tree_primitives.md#141-the-data-model-addition) | V1.0 | `refresh_node` / `refresh_subtree` / `refresh_tree` / `retry_failed` (V1.0); `synced_peer_add` (V1.1); `cross_tree_rebase` (V2.1+) |
| `parent_conversation_tree_id` | Set by `branchToNewTree` on cloned trees (the source tree's id) | **V1.0** (per Patch #1) | History "where did I fork this from" navigation per [02 §7 A.1](02_tree_ui_affordances.md#7-decisions-and-open-questions); ships V1.0 because `branchToNewTree` ships V1.0 |
| `tree_path` | JSON-encoded array of `[axis, slotIndex]` pairs from root to leaf — see encoding below | **V1.0** (required) | Lets V1.1 fanout-detection reconstruct **nested fan structure** for V1.0+ trees without relying on `original_prompt_id` chain flattening (which loses nesting per [01 §9.3.1 caveat](01_tree_primitives.md#931-fan-grouping-algorithm-v11--original_prompt_id-chain-flattening--wave_id-disambiguator)). |

These labels are the entire round-trip-fidelity story for V1.0 — the auto-reverse logic ([01 §9.3](01_tree_primitives.md#93-migration-of-existing-linear-attacks---auto-reverse-to-a-tree)) and the [§9.4.1 reload-reconstruction path](01_tree_primitives.md#941-reload-reconstruction-v10) read them back to reconstruct the tree.

**Piece-label divergence invariant.** Within one leaf's dispatch sequence, every piece created by `create_attack` (the prepended messages) and every piece created by the N `add_message` calls carries the **same** label set: `operator`, `operation`, `conversation_tree_id`, `wave_id`, `wave_trigger_kind`, `parent_conversation_tree_id`, `tree_path`. The runner does not vary labels across the sequence's calls. This matters because the backend's [`_resolve_labels` at attack_service.py:L708](../../../pyrit/backend/services/attack_service.py#L708) prefers existing piece labels over request labels — if the runner accidentally diverged labels mid-sequence, later add_messages would silently inherit earlier pieces' labels. The invariant holds by construction (one `_build_labels(path, treeId, waveId, waveTriggerKind)` call passed identically to every request in the sequence), and is asserted by [§11.1 labels-divergence test](#111-unit-testable-in-isolation-no-backend) (client-side) AND [§11.2 labels round-trip test](#112-needs-the-backend-integration-tests) (catches backend `_resolve_labels` regressions — the silent-corruption class that the [§9.4.5](01_tree_primitives.md#945-hard-backend-dependency-relocate-_validate_operator_match) PR set anticipates).

#### `tree_path` encoding (V1.0, JSON to keep forward-compatible)

Earlier rev 10 used `<axis>/<slotIndex>` segments joined by `,`. Rejected per reviewer rev 10 (C6): if any future fan axis name contains `/` or `,`, decoding breaks silently. V1.0 ships **JSON array of `[axis, slotIndex]` tuples**:

```
labels.tree_path = '[["prompt",2],["attempt",3]]'   # nested: outer prompt fan, inner attempt fan
labels.tree_path = '[]'                              # leaf with no fan ancestors (empty array, not omitted)
labels.tree_path = '[["attempt",7]]'                 # single fan ancestor
```

**Parser contract:**

```ts
function parseTreePath(label: string | undefined): Array<[string, number]> {
  if (label === undefined || label === '') return []
  try {
    const parsed = JSON.parse(label)
    if (!Array.isArray(parsed)) throw new Error('not array')
    return parsed.map(([axis, slot]) => {
      if (typeof axis !== 'string' || typeof slot !== 'number') throw new Error('bad shape')
      return [axis, slot]
    })
  } catch (e) {
    console.warn(`malformed tree_path label "${label}":`, e)
    return []   // fail-soft: treat leaf as having no fan ancestors
  }
}
```

**Forward compatibility:** if a future runner version writes a new `tree_path` format (e.g., embedding fan node IDs), older clients see malformed JSON → empty path → fall back to lineage-flattening for those leaves. No hard crash.

**Why drop the V1.0 `fan_axis` label.** Earlier rev 10 carried a separate `fan_axis` label (the immediate fan ancestor's axis) as a History-tab filtering convenience. Reviewer rev 10 (C7) flagged it as redundant data inviting drift. V1.0 drops it; History-tab filtering by "this leaf's immediate fan axis" derives from the last element of `parseTreePath(tree_path)` — one string-split-equivalent per row, irrelevant cost.

## 5. State Machine

The states and transitions are specified in [01 §6.1-§6.2](01_tree_primitives.md#61-states); this section names the runner's contract with the state machine, not the state machine itself.

### 5.1 The runner only owns three transitions

| From | To | Trigger |
|---|---|---|
| `stale` ∨ `edited` | `running` | Dispatch start |
| `running` | `clean` | Dispatch success |
| `running` | `failed` | Dispatch error |

All other transitions (`clean` ↔ `edited` via operator edit, `clean` → `stale` via ancestor change, `running` → `cancelled` via wave abort) are owned by the React state container based on operator actions. The runner reads the state to decide eligibility; it does not write it except for its three transitions.

### 5.2 Cascade-on-success

When a `running → clean` transition fires:

1. Sink records the ExecutionRecord.
2. Sink moves the node to `clean`.
3. The dispatch loop re-evaluates: for each `stale` child of this node, if *all* its parents are now `clean`, add it to `ready`. (Most fan children become ready simultaneously when their fan-parent goes clean; the next iteration of the loop will pick up to `maxParallel - inflight.size` of them.)

### 5.3 Cascade-on-failure

When a `running → failed` transition fires:

1. Sink moves the node to `failed`. Its `node.execution` is nulled and `node.lastError` carries the reason ([01 §6.4.1](01_tree_primitives.md#641-why-nodeexecution--null-on-failure-not-preserved)).
2. **In-flight cascade.** The runner iterates `ready` and drops every leaf whose root-to-leaf path includes the just-failed Send. Dropped leaves transition to `stale` via `sink.setNodeState(treeId, leaf.id, 'stale', opts={'reason': { message: 'blocked by ancestor failure in wave <waveId>', failure_class: 'blocked' }})` — the structured reason populates the leaf's `lastError` with `failure_class='blocked'` so the wave-summary's `blocked` count ([§6 WaveEvent](#6-wave-bookkeeping)) can be computed by a single scan of terminal-state leaves' `lastError.failure_class` fields. The wave-summary counts them as **`blocked`** (not as `failed.*` — they never dispatched; the failure was the ancestor's). The dispatch loop's next iteration sees the reduced `ready` set and proceeds with the remaining leaves.
3. **Operator surface.** The [02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances) wave-status banner renders the four-value summary `(N ✓, N ⚠ failed, N ⦾ blocked, N ○ cancelled)` during the wave and on the wave-complete toast. Hovering a blocked node's `⦾` chip shows *"Blocked by ancestor `<node-name>` failure in this wave. [Retry failed] to attempt recovery."*
4. **Recovery is a separate operator gesture.** The [Retry failed] toast button (per [02 §5.14](02_tree_ui_affordances.md#514-partial-failure-mid-refresh)) calls [`runner.retryFailedNodes(treeId, nodeIds)`](#21-entry-points-the-public-api) with the wave-complete-captured `nodeIds` = the union of this wave's failed leaves (any `failure_class` except `permanent`) plus its `blocked` leaves. The runner builds `S` as `{nodeIds} ∪ {failed/cancelled Send ancestors on each nodeId's path}` — scoped to just wave-W's victims, not the whole tree. The new wave's [§3.1 step 2b pre-readiness demotion](#31-topological-walk) flips every `S`-member node currently in `failed`/`cancelled` back to `stale` *before* the readiness rule runs. After demotion, the ancestor allowlist admits them (their state is now `stale`, in the allowlist; no longer `failed`/`cancelled`, in the exclusion set), the leaves below them satisfy readiness, and dispatch proceeds. The interior failed Sends are regenerated as part of each descendant leaf's fresh suffix per [§3.2](#32-what-gets-dispatched). Repeated 5xx (`failure_class='transient'`) on the same Send cascades the same way: each Retry-failed wave is a fresh attempt with no exponential backoff in V1.0. **Rate-limit failures** (`failure_class='rate_limited'` per [§3.3a `_format_api_error`](#33a-helpers-referenced-by-the-dispatch-step)) are surfaced distinctly in the wave-complete toast and excluded from `nodeIds` (the [Retry failed] button is disabled when *all* failed leaves are rate-limited, OR retries only the non-rate-limited subset; rate-limited leaves stay failed in the wave summary until the operator manually clicks Refresh tree after the rate-limit window clears). V1.x adds `Retry-After` header parsing + countdown timer + auto-enable (see [§12 Q.7](#12-open-questions)).

**Why the toast captures `nodeIds` (not just `treeId`).** Reviewer rev-16 spotted that exposing only `refreshNode`/`refreshSubtree`/`refreshTree` meant `[Retry failed]` had no API to call — it would either fall back to `refreshTree(treeId)` (which sweeps unrelated edits the operator made between waves) or invent ad-hoc scope. The toast captures wave-W's failed+blocked leaf ids at wave-complete time and passes them to `retryFailedNodes`; the runner derives ancestors itself. This scope is stable even if the operator edits the tree between wave-W completion and the retry click — the retry only touches W's victims.

**Why a pre-readiness demotion and not a weakened readiness rule.** Reviewer rev-15 spotted that the previous §5.3 wording ("the new wave's readiness rule sees the failed-ancestor nodes IN ITS S so descendants can dispatch through them") was false against §3.1 as written — the rule inspects `node.state`, which is still `failed` regardless of which wave is computing. Two fixes were on the table: (a) demote at wave-start, gated on `waveTriggerKind='retry_failed'`; (b) weaken the readiness allowlist to "in S or clean" globally. Option (a) shipped because (b) would revert the anti-amplification fix that's the whole point of §5.3 — sibling leaves of a transiently-failed shared ancestor would each retry the ancestor independently, bringing back the `min(maxParallel, sibling_count)` retry-storm on rate-limited targets. The demotion is operator-invisible (the [Retry failed] click already implies "give up on the previous failure, try again"); the destructive `lastError` clobber is the price.

**Why cascade in-flight instead of letting sibling leaves retry the shared ancestor.** Under the V1.0 no-coordination, no-backpressure model, sibling leaves sharing a transiently-failed Send X would each independently include X in `fresh_suffix` and retry it. With `maxParallel=4` and a 60-leaf fan against a rate-limited target, the first 429 on X cascades to ~48 more 429s on the same X as siblings dispatch. The in-flight cascade collapses this to one X-failure plus N blocked leaves; the operator's [Retry failed] click then surfaces the recovery as an operator-explicit gesture (cost-modal-visible, telemetry-attributable) rather than runner-invisible amplification.

## 6. Wave Bookkeeping

### 6.1 `waveId` generation

Per [01 §14.4](01_tree_primitives.md#144-wave-id-generation---one-rule):

```ts
function startWave(triggerKind: WaveTriggerKind): { waveId: string; waveTriggerKind: WaveTriggerKind } {
  return { waveId: uuid(), waveTriggerKind: triggerKind }
}
```

One `waveId` per `refreshNode` / `refreshSubtree` / `refreshTree` call. The wave never escapes the single dispatch-loop invocation that created it.

### 6.2 `waveTriggerKind` enum

The wire-level enum is defined in [01 §14.1](01_tree_primitives.md#141-the-data-model-addition); this table maps every operator-facing UI action that fires a wave to which of the four V1.0 enum values it carries. The caller passes the trigger kind to the runner; the runner does not infer it (the §14.4 decision was to make the source explicit).

| UI action ([02 §2.2 / §2.3](02_tree_ui_affordances.md#22-per-node-action-rail)) | `waveTriggerKind` | Version |
|---|---|---|
| Node `↻` (per-node Refresh) | `refresh_node` | V1.0 |
| Node shift-`↻` / right-click "Refresh subtree" | `refresh_subtree` | V1.0 |
| Canvas-ribbon "Refresh tree" button | `refresh_tree` | V1.0 |
| Auto-trigger on first `addNode(send)` after authoring | `refresh_node` | V1.0 |
| Fan `+` (Add another variant) — runner refreshes the new variant alone | `refresh_node` | V1.0 |
| Fan-axis change (destructive op with confirm) | `refresh_subtree` | V1.0 |
| `branchToNewTree` → operator immediately edits & refreshes the cloned tree | `refresh_tree` | V1.0 |
| `↻×N` Re-run multiple (promotes Send to attempt-fan, runs all N children) | `refresh_subtree` | V1.0 |
| Auto-reverse opens a historical AR → no immediate wave (the AR is already executed) | (no wave generated) | V1.0 |
| Operator clicks Retry-failed in the wave-complete toast | `retry_failed` | V1.0 |
| Stack-`+` adds a synced peer set → runner refreshes all peers | `synced_peer_add` | **V1.1** (depends on Synced-Peers Stack) |
| Cross-tree refresh (refresh B's root against A's current root — conceptually a cross-tree rebase) | `cross_tree_rebase` | V2.1+ |

**Reflog drawer "Make current"** does NOT appear in this table because `makeCurrent` itself generates no wave — it's a pure pointer swap per [01 §6.7 step 6](01_tree_primitives.md#67-makecurrent---destructive-promotion-from-the-reflog). The operator's subsequent Refresh of the now-stale descendants is the wave-generating event, and it carries `refresh_subtree` (per [01 §14.4 note](01_tree_primitives.md#144-wave-id-generation---one-rule)).

**Earlier 11-value enum collapsed.** Revision 15 (per reviewer Finding 1) absorbed five V1.0-specific kinds (`initial_send`, `fan_expand`, `fan_axis_change`, `branch_rebase`, `rerun_multiple`) into the three core verbs above. The UI-action column still names every distinct trigger; the `waveTriggerKind` column tells the runner which entry-point semantics fired. See [01 §14.1](01_tree_primitives.md#141-the-data-model-addition) for the rationale.

The enum is **closed** in V1.0 (the listed kinds are the only legal values; introducing a new kind requires bumping the runner version). Operators see the kind in the §8.2 "Recent waves" drawer label.

The enum lives in the primitives doc per [01 §14.1](01_tree_primitives.md#141-the-data-model-addition); the UI-affordance *mapping* lives here. Two locations because the enum is a data-model fact (touches the schema) and the mapping is a UI/runner fact (touches affordances).

### 6.3 Wave events

```ts
export type WaveEvent =
  | { kind: 'start'; waveId: string; triggerKind: WaveTriggerKind; estimatedCalls: number; treeId: ConversationTreeId }
  | { kind: 'node_complete'; waveId: string; nodeId: ConversationTreeNodeId; outcome: 'success' | 'failure' }
  | {
      kind: 'complete'; waveId: string;
      summary: {
        succeeded: number;
        failed: { transient: number; rate_limited: number; permanent: number };  // bucketed by [01 §6 lastError.failure_class](01_tree_primitives.md#61-states)
        blocked: number;       // §5.3 in-flight cascade victims (state=stale, failure_class='blocked')
        cancelled: number;
        reflog_evicted: number;
      }
    }
  | { kind: 'busy'; treeId: ConversationTreeId; holderTabId: string }  // §10.4 cross-tab advisory lock
  | { kind: 'queued'; waveId: string; treeId: ConversationTreeId; queueDepth: number }  // §10.3 per-tree queue
  | { kind: 'reflog_eviction'; treeId: ConversationTreeId; nodeId: ConversationTreeNodeId; evictedExecutionId: string; preview: string }  // single eviction outside a wave (e.g. makeCurrent at cap, §6.7 of primitives)
  | { kind: 'operator_tag_required'; treeId: ConversationTreeId }  // §2.1 entry-point shim step 1 tag-hygiene gate fired; wave never started
```

**Every event variant carries `emittedAt: string` (ISO-8601 UTC) (rev 18, per rubber-duck Finding C.1).** The field is implicit in the union above to keep the variant declarations readable; the sink populates it at `emitWaveEvent` callsite via a wrapper. Combined with the per-`ExecutionRecord` `dispatchedAt`/`targetFirstByteAt`/`completedAt` triple ([01 §4.6](01_tree_primitives.md#46-shared-types)), this gives the [02 §8.2 Recent waves drawer](02_tree_ui_affordances.md#82-the-v1-drawer-a-recent-waves-tab) the data it needs to render per-wave timing (wave duration = `complete.emittedAt - start.emittedAt`; per-leaf latency = `record.completedAt - record.dispatchedAt`). The [§11.1 invariants](#111-unit-testable-in-isolation-no-backend) (e.g., `inflight.size <= maxParallel`) become validatable in production rather than only in unit tests because the timestamp data is on every event and every record. Operators triaging *"the wave took 5 minutes — what was the runner doing?"* read the drawer; SREs reading aggregated logs read the same fields.

**`complete.summary` shape (rev 16, per reviewer Findings 2 + 3).** Earlier revisions used a flat `failed: number`. The bucketed shape lets the [02 §2.3 ribbon](02_tree_ui_affordances.md#23-canvas-level-affordances) and [02 §5.14 toast](02_tree_ui_affordances.md#514-partial-failure-mid-refresh) drive separate counts/colors per failure class (`⚠ failed` for transient + permanent, `⏱ rate-limited`, `⦾ blocked`) without per-node scans. Wave aggregation iterates the wave's terminal-state leaves and buckets by `node.lastError?.failure_class`: leaves in `clean` increment `succeeded`; leaves in `failed` with class `transient`/`rate_limited`/`permanent` increment `failed.<class>`; leaves in `stale` with `failure_class='blocked'` increment `blocked`; leaves in `cancelled` increment `cancelled`. A `failed` leaf with `lastError===null` is treated as `transient` (defensive default; should not happen by construction but the aggregator is robust). The [Retry failed] button-gating logic ([§5.3 step 4](#53-cascade-on-failure)) reads `summary.failed.transient + summary.blocked > 0` for enablement.

**Legacy single-int helper.** Callsites that just want "how many leaves failed (any class)" can use `totalFailed(summary) = summary.failed.transient + summary.failed.rate_limited + summary.failed.permanent`; the [02 §8.2 "Recent waves" drawer](02_tree_ui_affordances.md#82-recent-waves-drawer-tab) uses this for the per-wave row's compact count. Test assertions and any analytics consumers built against the pre-rev-16 `failed: number` shape need to migrate to either `totalFailed(...)` or the bucketed fields.

The `complete.summary.reflog_evicted` count rolls up evictions that fired during the wave so the wave-complete toast ([02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances)) can show *"Past runs evicted: N"* in one line instead of stacking N transient markers. Standalone `reflog_eviction` events (outside a wave) still fire individually for the ribbon marker.

The UI subscribes to wave events to drive:
- The in-canvas progress bar ([02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances): `[ ●●●●●●○○○○ ] 6/60 (3 ✓, 0 ⚠, 1 ●)`).
- The wave-complete toast ([02 §8.1](02_tree_ui_affordances.md#81-the-v1-chain-preview-banner--confirm-modal--toast--drawer-panel)).
- The "Recent waves" drawer tab ([02 §8.2](02_tree_ui_affordances.md#82-the-v1-drawer-a-recent-waves-tab)).
- The cross-tab busy modal ([02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances): *"Another tab is refreshing this tree. [Refresh anyway] [Wait]"*).
- The reflog-eviction ribbon marker ([02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances): *"Past run evicted from node X. [Pin evicted run] [Increase cap]"*).

## 7. Failure & Partial-Commit Semantics

Per [01 §6.4](01_tree_primitives.md#64-failure--partial-commit-semantics), the runner's failure contract is:

1. **In-flight completes.** When the operator hits Cancel (V1.1) or an early failure triggers wave-abort, any dispatched-but-not-returned `create_attack`/`add_message` calls run to completion. The runner awaits all of `inflight`; it does not abandon the promises.
2. **Not-yet-dispatched → `cancelled`.** Nodes still in `ready` (or not yet ready due to a failed parent) transition to `cancelled` rather than staying `stale`. This distinguishes "operator stopped this wave" from "the next wave hasn't happened yet."
3. **No automatic re-dispatch.** The operator triggers retry explicitly. The wave-complete toast surfaces "[Retry failed]" which re-evaluates `failed` nodes against the current tree state. Retries on partial-success leaves (§3.3) skip `create_attack` and re-run only `add_message`.
4. **Single-leaf failure does NOT abort the wave.** A 60-leaf refresh where leaf 7 fails continues to process leaves 8-60. The wave summary reports `succeeded=59, failed=1, cancelled=0`. This matches the [02 §5.14](02_tree_ui_affordances.md#514-partial-failure-mid-refresh) scenario.
5. **Within-leaf mid-chain partial commit.** Per §3.3, the leaf dispatch is `create_attack` + N `add_message` calls. If add_message #k fails (for any k from 1 to N), the AR exists on the backend with the first k-1 user-assistant turn pairs successfully sent. The k-th Send transitions to `failed`; Sends k+1..N transition back to `stale`. **All Sends in fresh_suffix that did not complete (the failed Send and all later ones) have their `node.execution` nulled** per [01 §6.4.1](01_tree_primitives.md#641-why-nodeexecution--null-on-failure-not-preserved) — this is what makes the resolver's `is_stale` predicate (§4.1) correctly identify them as needing fresh dispatch on retry. The Sends that DID complete (k-1 of them) keep their fresh ExecutionRecords pointing to the partial AR. The leaf shows `failed`. **No fast-path retry in V1.0.** The operator's retry from the toast re-dispatches the whole leaf, creating a brand-new AR and re-firing all stale Sends on the path. The partial AR remains in History as a failed-mid-chain row (operators see it; not a regression vs. today's chat tab which has the same partial-attack semantics on target errors). *V1.1* may add a partial-retry fast-path that reuses the partial AR id and skips create_attack + the already-succeeded add_messages — deferred because (a) it adds a `partialAttackResultId: string | null` field to track the reusable AR id on the failed Send (the cleaner V1.1 alternative to bringing back a `'partial'` outcome), (b) the dispatch loop grows a retry-aware branch, and (c) telemetry will show whether retries are common enough to justify the optimization.

**Wave-abort triggers (V1.0):** the explicit Cancel chip in the wave-status banner ([02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances)). Per §9, V1.0 ships UI-level cancellation: cancel flips a per-wave flag the dispatch loop checks at each `ready.popNext()` boundary — already-dispatched leaves still complete (step 1 above); undispatched leaves transition to `cancelled` (step 2 above).

**Wave-abort triggers (V1.x):** V1.x adds backend-token cancellation that aborts in-flight HTTP calls too, eliminating step 1's "in-flight completes" caveat.

## 8. Backend Call Mapping

### 8.1 Per-leaf dispatch: `create_attack` + N `add_message`s

Per [01 §7.1](01_tree_primitives.md#71-conversationtree-operation--backend-call) and §3.3 of this doc, each leaf's full dispatch sequence is one AR with a `create_attack` setup call plus N `add_message` calls, where N = the count of stale Sends on the leaf's root-to-leaf path (including the leaf itself). All calls share the same `attack_result_id` returned by `create_attack`; the runner passes `target_conversation_id = create_resp.conversation_id` on every `add_message`.

| Operator intent | Backend call(s) | Notes |
|---|---|---|
| Refresh leaf SendNode (chain wholly clean upstream) | (1) POST [`/api/attacks`](../../../pyrit/backend/routes/attacks.py#L184) with `prepended_conversation` = all clean-prefix turns + assistant responses; (1) POST [`/api/attacks/{id}/messages`](../../../pyrit/backend/routes/attacks.py#L432) for the leaf's input UserTurn | Per §3.3. Two calls, one slot, one ExecutionRecord on the leaf. |
| Refresh leaf SendNode (chain stale from depth k) | (1) POST `/api/attacks` with `prepended_conversation` = clean prefix only (turns 1..k-1 plus their assistant responses); (N-k+1) POST `/api/attacks/{id}/messages` calls, one per stale Send from k to leaf | Per §3.3. N-k+2 calls total, one slot, one AR. Each interior Send in the fresh suffix gets its own ExecutionRecord that shares the leaf's AR id. |
| Refresh interior SendNode in isolation (operator clicks `↻` on an interior, not on a leaf) | Same as above where the operator-targeted Send is treated as the leaf for this dispatch sequence | The actual leaf below the targeted Send stays `stale` until separately refreshed. |
| Retry a partial-failed leaf (§7 rule 5) | Same as "chain stale from depth k" — brand-new AR, all stale Sends re-fired | No reuse of the partial AR id in V1.0; the fast-path optimization is V1.1 (gated on a future `partialAttackResultId` field). |
| Edit node params | (no backend call) | State-only; marks descendants stale per [01 §6.3](01_tree_primitives.md#63-propagation-rules). |
| Delete tree node | (no backend call) | State-only; backend ARs persist per [02 §5.16](02_tree_ui_affordances.md#516-delete-a-branch). |
| Branch from node | (no backend call) | **V1.0** (per Patch #1); cheap-refs operation per [01 §6.5](01_tree_primitives.md#65-branch-from-node---the-immutable-history-primitive). Lands by swapping the active tree (V1.0) or opening a new tab in the strip (V1.1). |

### 8.2 Why every leaf uses `create_attack` + N `add_message`s (not one or the other alone)

[`create_attack`](../../../pyrit/backend/services/attack_service.py#L277) is **context setup only** — it persists the `prepended_conversation` history into the new AR's conversation but does **not** invoke the target. Only [`add_message`](../../../pyrit/backend/services/attack_service.py#L570) with `send=True` fires the target call and produces an assistant response. This is existing backend semantics; the runner mirrors them.

**Why not `create_attack` alone (with all stale turns as prepended).** A "single create_attack per leaf, no add_message" runner would create the AR with prior history but never invoke the target — operators would click Refresh to discover zero assistant outputs. Add_message is what makes the model produce something.

**Why not `add_message` alone (extending an existing leaf's AR with a new turn).** This would be the natural fit for "operator added one more UserTurn+Send pair on the end of a clean leaf — just send the new turn against the existing AR." Rejected for V1.0:

1. **AR-per-leaf says every leaf is its own AR** ([01 §7.2](01_tree_primitives.md#72-conversationtree-to-execution-materialization-rule)). Extending an existing AR's conversation breaks the property that `labels.conversation_tree_id` filtering returns a clean leaf set: the previously-leaf Send would now be interior, but its AR still claims it as a leaf.
2. **`add_message` is operator-and-target locked.** [`_validate_operator_match`](../../../pyrit/backend/services/attack_service.py#L682) and [`_validate_target_match`](../../../pyrit/backend/services/attack_service.py#L647) check the existing AR's labels. Cross-operator or cross-target extensions immediately 400; the runner would have to fall back to create_attack anyway. Simpler to always create_attack.
3. **The cost is dominated by token usage, not HTTP overhead.** One `create_attack` with a 12-message `prepended_conversation` plus an `add_message` costs nearly the same as a single `add_message` to a pre-existing AR — both re-send the full context to the target (PyRIT targets are not server-stateful).

**Why the split between prepended and add_message.** `prepended_conversation` is the *cheap* way to inject clean-prefix history — one bulk insert into a new conversation, zero target calls, no operator-lock checks on individual turns. Using N add_messages to build up the clean prefix would be N round-trips, N target validations, N target calls re-firing turns the operator already had answers for. The combined approach gets the best of both: one cheap setup call for everything that doesn't need to re-fire, plus N add_messages for everything that does. The partition rule in §3.3 / §4.1 decides where the clean/fresh boundary sits.

V1.1 may revisit `add_message`-only extension for the "extend the main path of a clean leaf by one turn" hot-path optimization if telemetry shows it matters — operationally it requires either relaxing the AR-per-leaf invariant or introducing a per-Send `parentAttackResultId` field to track "this Send extends that AR." Neither is V1.0.

### 8.3 Future calls (V1.1+)

| Operation | Call | Version |
|---|---|---|
| Score a leaf | POST `/api/scores` (does not exist yet) | V1.1 — needs backend route + scorer service wiring |
| Persist a ConversationTree | POST `/api/conversation_trees` (does not exist) | V2 — per [01 §11](01_tree_primitives.md#11-future-work-conversationtree-persistence) |
| Resume a persisted tree | GET `/api/conversation_trees/{id}` | V2 |

## 9. Cancellation

**V1.0 ships UI-level cancellation; backend-token cancellation is V1.x.** The two have different cost/value profiles and only the first is needed for the operator's "stop this 600-call refresh before it bills me $30" workflow. The runner exposes **two distinct cancel operations** so the operator can act on either the active wave or the queued waves without confusing the two:

- **`cancelWave(treeId)`** — cancels the currently-dispatching wave; in-flight HTTP calls complete; not-yet-dispatched leaves flip to `cancelled`. Resolves when the wave is fully settled.
- **`cancelQueued(treeId)`** — drops every wave on `queueByTree[treeId]` ([§10.3](#103-backpressure-per-tree-wave-queue)) without touching the active wave. Each dropped wave emits a `WaveEvent { kind: 'complete', summary.cancelled: <all leaves> }` so the UI reconciles its queued banner.

The two operations are independent: clicking the active-wave Cancel does NOT drop the queue (the next queued wave still starts when the active one settles); clicking Cancel-queued does NOT abort the active wave. Operators wanting both call both — the UI's "Cancel everything" affordance (not in V1.0; flagged for V1.1 if operators request it) would call them in sequence.

**V1.0: UI-level cancel flag at `ready.popNext()` boundary.** The runner's per-wave loop (per §3.1 step 2b) initializes `cancelled = false` at wave start. `cancelWave(treeId)` flips the flag to `true` for the matching active wave. The dispatch loop checks the flag at each `ready.popNext()` iteration (after each leaf finishes, before the next leaf starts):

```python
while ready and not cancelled:
    n = ready.popNext()
    ...
# After loop: wave settled. Flip remaining nodes to 'cancelled'.
if cancelled:
    for n in S - completed_set:                   # everything in S that didn't finish
        sink.setNodeState(treeId, n.id, 'cancelled', opts={'reason': 'operator cancelled wave'})
        sink.clearExecution(treeId, n.id)
sink.emitWaveEvent({
    kind: 'complete', waveId,
    summary: {
        succeeded: count(leaves in S that completed with state='clean'),
        failed: {
            transient:    count(failed leaves where lastError.failure_class == 'transient'),
            rate_limited: count(failed leaves where lastError.failure_class == 'rate_limited'),
            permanent:    count(failed leaves where lastError.failure_class == 'permanent'),
        },
        blocked:        count(leaves in S left stale with lastError.failure_class == 'blocked'),
        cancelled:      count(S - completed_set) if cancelled else 0,
        reflog_evicted: count(reflog evictions that fired during this wave),
    }
})
```

**What V1.0 cancel does and does not stop:**
- ✓ Stops the runner from starting new leaf dispatches. The next `ready.popNext()` returns the cancel signal; the loop exits.
- ✓ Marks all undispatched leaves as `cancelled` so the operator sees them clearly in the wave-complete toast.
- ✗ Does NOT abort in-flight `create_attack` or `add_message` HTTP calls that are mid-flight when cancel fires. Those complete (success → recorded; failure → marked failed). Per §7 rule 1, in-flight completes is the V1.0 contract.
- ✗ Does NOT recall already-committed backend ARs. Successful leaves stay in History.

**Backend dependency (deferred to V1.x):** the `create_attack` route has no `CancellationToken` parameter today. Adding one is the [01 §12.8](01_tree_primitives.md#128-cancellation-deferred---accepted-follow-up-v1x) follow-up. Until then, the runner cannot stop a dispatched call from completing on the backend; it can only stop subsequent dispatches (above). For a 600-call refresh, the V1.0 UI-cancel saves the operator the *unstarted* calls (potentially hundreds), which is the dominant cost — the in-flight 4 are bounded.

**Operator surface ([02 §2.3](02_tree_ui_affordances.md#23-canvas-level-affordances)):** the wave-status banner during an in-flight wave shows `[ ●●●●●●○○○○ ] 6/60 (3 ✓, 0 ⚠, 1 ●) [Cancel]`. Clicking Cancel calls `runner.cancelWave(treeId)`. The button transitions to a disabled `[Cancelling…]` while in-flight leaves finish; the wave-complete toast then reads *"Wave cancelled: 6 ✓, 0 ⚠, 54 cancelled. [View wave]"*. The runner's `cancelWave` returns a `Promise<void>` that resolves when the wave is fully settled (including draining the in-flight leaves), so the UI can await it before re-enabling the Refresh button. When the per-tree queue is non-empty, the banner adds a separate `[Cancel queued]` chip that calls `runner.cancelQueued(treeId)` — drops the queue without touching the active wave (see [§10.3](#103-backpressure-per-tree-wave-queue)).

## 10. Concurrency Budget

### 10.1 V1.0 — per-session, slot held across the full leaf sequence

A single `Semaphore(4)` (or equivalent — Promise-counting pattern is fine) gates all dispatch in the session. With one tree per session in V1.0, this collapses to per-tree. **Each leaf's full dispatch sequence (§3.3) holds one slot for the duration** — the `create_attack` + N `add_message` calls all execute sequentially within the same slot.

```ts
const dispatchSemaphore = new Semaphore(4)

async function dispatch(leaf, waveId, waveTriggerKind) {
  await dispatchSemaphore.acquire()
  try {
    // ... §3.3 body
  } finally {
    dispatchSemaphore.release()
  }
}
```

### 10.2 V1.1 — per-Workspace with fair-share

Per [01 §12.2](01_tree_primitives.md#122-concurrency-budget-maxparallel4-per-session-v10--per-workspace-v11-with-fair-share-decided):

```ts
// Per-Workspace shared semaphore (single instance across all open trees)
const workspaceSemaphore = new Semaphore(4)

// Per-tree "in-flight wave count" for fair-share picking. Updated by the dispatch
// wrapper below — incremented on slot acquire, decremented on slot release.
const inflightByTree = new Map<ConversationTreeId, number>()

async function dispatchLeaf(treeId: ConversationTreeId, leaf: SendNode, waveId, waveTriggerKind) {
  await workspaceSemaphore.acquire()
  inflightByTree.set(treeId, (inflightByTree.get(treeId) ?? 0) + 1)
  try {
    // ... §3.3 body — full create_attack + N add_message sequence
  } finally {
    inflightByTree.set(treeId, (inflightByTree.get(treeId) ?? 1) - 1)
    workspaceSemaphore.release()
  }
}

function pickNextReady(readyByTree: Map<ConversationTreeId, ReadyQueue>): { treeId, node } | null {
  // Pick the tree with the fewest in-flight calls (fair-share)
  const candidates = [...readyByTree.entries()].filter(([_, q]) => !q.isEmpty())
  if (candidates.length === 0) return null
  candidates.sort(([a], [b]) => (inflightByTree.get(a) ?? 0) - (inflightByTree.get(b) ?? 0))
  const [treeId, queue] = candidates[0]
  return { treeId, node: queue.pop() }
}
```

**Why per-Workspace and not per-target:** [01 §12.2](01_tree_primitives.md#122-concurrency-budget-maxparallel4-per-session-v10--per-workspace-v11-with-fair-share-decided) notes that `RoundRobinTarget` already handles cross-endpoint load distribution below the runner. Per-target budgeting is V1.x if real operators ask.

### 10.3 Backpressure: per-tree wave queue

V1.0 ships a per-tree wave queue on top of the per-session semaphore (§10.1). The semaphore is `Semaphore(4)` for in-flight leaf dispatches; the queue is keyed on `conversationTreeId` and serializes waves on the same tree.

**The queue's lifecycle is implemented inside the [§2.1 entry-point shim step 4](#entry-point-shim-ordering-v10).** This section spec's the queue *contract* — FIFO order, no coalescing, stale-set recomputed at wave-start, banner copy. Implementers refer to §2.1 for the canonical `currentWaveByTree.set/delete` + `queueByTree.push/shift` + `queued`-event-emission code. The two module-level maps and the queue-element type are shared:

```ts
const dispatchSemaphore = new Semaphore(4)                            // §10.1 in-flight cap
const queueByTree = new Map<ConversationTreeId, WaveRequest[]>()      // FIFO queue per tree
const currentWaveByTree = new Map<ConversationTreeId, WaveRequest>()  // sentinel for "a wave is active on this tree"

interface WaveRequest {
  waveId: string
  triggerKind: WaveTriggerKind
  rootNodeId: ConversationTreeNodeId   // the subtree root (or tree.rootId for full-tree)
  enqueuedAt: number
  // The set of stale Sends is NOT stored here — it's recomputed when the wave actually
  // starts (the operator may edit the tree between enqueue and dispatch); see the
  // "stale-set is recomputed at wave-start" semantics below.
}
```

Rev-15 had a duplicate `refreshSubtree(treeId, rootNodeId, triggerKind)` pseudocode block here that referenced an undefined `_runWave` and never called `currentWaveByTree.set` — the queue was structurally unreachable (reviewer Finding 5). Rev 16 cuts the duplicate in favor of §2.1's shim spec, which wires the lifecycle correctly inside try/finally.

**Queue semantics:**

- **FIFO order** within a tree. Operator clicks Refresh-tree, then Refresh-subtree-X — both run; the second waits for the first to complete, then runs.
- **No automatic coalescing.** Two queued waves on the same tree run as two separate waves (two `waveId`s, two toasts, two AR-per-leaf groupings). The §3.3a debounce catches the 250ms double-click case; beyond that, operators get what they asked for. *Rationale:* coalescing wave A's stale-set into wave B is operator-invisible and would confuse "I clicked Refresh twice and got one toast." Explicit second-wave behavior maintains the mental model.
- **Stale-set is recomputed at wave-start, not at enqueue-time.** If the operator edits the tree between enqueue and dispatch, the wave dispatches against the current state. This is correct (operator's most recent intent wins) but means the wave-status banner's "estimated calls" preview should refresh when the wave moves from queued to active.
- **The wave-status banner shows queue state.** When `queueByTree.get(treeId)` is non-empty, the banner reads *"Wave in progress · 2 queued · [Cancel queued]"* — operators can clear pending waves without aborting the active one. The `[Cancel queued]` chip calls `runner.cancelQueued(treeId)` ([§9](#9-cancellation)) which drops every queued wave without touching the active one; each dropped wave emits its own `complete` event with `summary.cancelled` set to its leaf count.

**V1.1 cross-tree behavior** per §10.2: the per-tree queues remain per-tree; the V1.1 fair-share scheduler picks from multiple trees' queues at the semaphore level. Per-tree serialization is preserved (never two waves on the same tree).

### 10.4 Cross-tab advisory lock (V1.0)

The §10.1/§10.2 semaphores are per-tab. Two browser tabs viewing the same `conversation_tree_id` (e.g., for the §13.1 minimal-Workspace side-by-side workflow per [01 §9.4.3](01_tree_primitives.md#943-concurrent-tab-advisory-lock-v10)) can independently fire `maxParallel=4` POSTs each — blowing the cap to 8 in-flight against one target.

V1.0 ships a `BroadcastChannel('pyrit-runner')` **advisory lock keyed on `conversation_tree_id`**. Acquire-on-wave-start, release-on-wave-settle. Full spec including the operator-facing "Another tab is refreshing — Refresh anyway / Wait" modal is in [01 §9.4.3](01_tree_primitives.md#943-concurrent-tab-advisory-lock-v10).

The runner's contract:

- Every `refresh*` entry point's shim ([§2.1 entry-point shim ordering](#entry-point-shim-ordering-v10)) calls `lockManager.acquire(treeId)` as step 2, AFTER the tag-hygiene gate (step 1) and BEFORE the cost guardrail (step 3).
- If `acquire` returns `'busy'`, the runner surfaces a `WaveEvent { kind: 'busy', treeId, holderTabId }` and aborts the wave (no dispatches, no state changes; no `release` needed because no acquire succeeded).
- The UI listens for `busy` events and shows the modal.
- On wave settle — OR on any early-return from steps 3, 4, 5 (cost-modal cancel, wave queued behind another, dispatch-loop completion, dispatch-loop exception) — the shim's outer `try/finally` unconditionally calls `lockManager.release(treeId)`. The release is invariant against the early-return paths the rev-15 tag-hygiene gate (Finding 4) added to the runner; an implementer following the §2.1 shim spec cannot leak the lock.

```ts
export interface CrossTabLockManager {
  acquire(treeId: ConversationTreeId): Promise<'acquired' | 'busy'>
  release(treeId: ConversationTreeId): void
}
```

**The lock manager is mocked in unit tests** (it's a clean boundary), and the §11.1 test list adds a `runner.crossTab.test.ts` for the lock-acquire / busy-modal / lock-release lifecycle.

**TODO:spec** — the per-tree serialization contract is implicit above; make it an explicit invariant. Lean: at most one wave per tree in flight; concurrent refresh requests on the same tree queue or no-op (operator preference, **TBD**).

## 11. Testing Surface

### 11.1 Unit-testable in isolation (no backend)

- **Topological walk correctness.** Given a hand-built tree and a stale-set, assert the dispatch order respects parent-before-child.
- **Concurrency cap.** With a stub `dispatch` that sleeps, assert `inflight.size ≤ maxParallel` throughout the wave.
- **Fair-share scheduling (V1.1).** With two trees and `maxParallel=4`, assert each tree gets ~2 in-flight slots over time.
- **State machine.** With a mock `RunnerStateSink`, assert the §5.1 three transitions fire in the right order.
- **Partial-commit on failure.** With a `dispatch` that fails leaf #7 of 60, assert leaves 8-60 still dispatch and the wave summary is correct.
- **In-flight cascade on shared-ancestor failure (§5.3).** With a chain-then-fan tree (10-deep stale prefix, 60 leaves) and a `dispatch` that fails the deepest interior Send X, assert: (a) every leaf in `ready` whose path includes X is dropped to `stale` with `lastError` referencing the failed wave, (b) the wave-summary counts them as `blocked` (not `failed`), (c) no leaf retries X via `add_message` in its own fresh_suffix, (d) the runner does NOT fire `add_message` for any blocked leaf, (e) a follow-up `retry_failed` wave includes the failed X plus its blocked descendants in S and admits them to `ready`.
- **Labels-divergence invariant (§4.3).** With a mock `attacksApi` that captures every `createAttack` and `addMessage` request, dispatch a leaf with N stale Sends and assert: (a) all N+1 captured requests' `labels` dicts are deep-equal, (b) every required label key (`operator`, `operation`, `conversation_tree_id`, `wave_id`, `wave_trigger_kind`, `tree_path`) is present in every request, (c) `parent_conversation_tree_id` is present in every request iff `tree.parentConversationTreeId !== null` (consistent omission per [§3.3a `_build_labels`](#33a-helpers-referenced-by-the-dispatch-step)). Guards against client-side regressions where a future runner refactor accidentally varies labels across the sequence.
- **Wave event sequence.** Assert `start → N × node_complete → complete` ordering.
- **`prepended_conversation` resolution.** Given a tree + leaf, assert the resolved message list matches expected.
- **200-message cap short-circuit.** Assert the leaf transitions to `failed` with the correct reason before any HTTP call fires.

### 11.2 Needs the backend (integration tests)

- **End-to-end `create_attack` round-trip** with realistic `prepended_conversation`.
- **Label writes propagate** to the AR's `labels` and survive a `GET /api/attacks/{id}`.
- **Labels round-trip (§4.3) — backend `_resolve_labels` regression canary.** Fire a real wave at a dev-backend leaf with 3 stale Sends; `GET /api/attacks/{ar.id}` and assert the round-tripped AR's `labels` dict matches the labels the runner sent on `create_attack` (the first call). The runner sends identical labels on every call in the sequence per the §4.3 invariant, so the round-tripped AR's labels should equal any single sent call's labels. Fails loudly if a future 0.16.x / 0.17.x backend change drifts `_resolve_labels` preference semantics under multi-piece `prepended_conversation` — the exact silent-corruption regression class the [§9.4.5](01_tree_primitives.md#945-hard-backend-dependency-relocate-_validate_operator_match) PR set anticipates.
- **Operator-lock interaction.** A wave with a leaf whose path contains a cross-operator message piece returns 400 from `add_message` (V1.1) — V1.0 with always-`create_attack` doesn't hit this path; document the V1.1 expansion test.
- **Concurrent waves across two browser tabs** confirming no cross-tab interference (V1.0 contract: independent runners, no coordination).

### 11.3 Test scaffolding

Proposed structure under `frontend/src/runner/__tests__/`:
- `runner.dispatch.test.ts` — §11.1 unit tests
- `runner.failure.test.ts` — partial-commit + failure cascade
- `runner.concurrency.test.ts` — semaphore + fair-share
- `runner.crossTab.test.ts` — `BroadcastChannel` lock acquire / busy / release (§10.4)
- `runner.reflog.test.ts` — eviction events, cap configurability, `pinExecution`
- `runner.materialization.test.ts` — `prepended_conversation` resolution
- `runner.integration.test.ts` — §11.2 with msw-mocked backend or real dev-server

## 12. Open Questions

- **Q.1 — Debounce on `refreshTree`.** §3.3 lean is "yes, in the UI button handler." Confirm with operators after first usability test.
- **Q.2 — Per-tree serialization vs. parallel waves on one tree.** §10.3 — lean is serialize per tree, but for the "edit root, click Refresh, immediately edit again, click Refresh again" pattern an operator might expect both to run. **TBD with operators.**
- **Q.3 — `prepended_conversation` >200 messages recovery.** §4.2 — the "Clone tree from a midpoint" suggestion needs an actual primitive. Resolved: V1.0 `branchToNewTree` (per [01 §6.5](01_tree_primitives.md#65-branch-from-node---the-immutable-history-primitive)) provides this — clone from any midpoint node and continue from there. V1.0 also surfaces the soft warning at 180 turns and the hard refusal at 200 per §4.2.
- **Q.4 — Streaming partial responses for very long Sends.** Out of scope per §1 Non-Goals; revisit in V2 if operator complaints about "the UI looks frozen during a 30-second target call" outnumber other priorities.
- **Q.5 — Telemetry events.** Should the runner emit OpenTelemetry spans for each dispatch, each wave, and each failure? Lean: yes, behind a feature flag, to validate the §11.1 invariants in production. **TODO:spec** — coordinate with the existing telemetry surface (search `frontend/src/services/` for the current pattern). Per [Q.S.4](#qs1-qs9-fourth-pass-rubber-duck-gate-items-rev-18) the per-leaf and per-event timing fields ship V1.0; OpenTelemetry wraps them V1.x.
- **Q.6 — Intra-wave memoization for shared stale interior Sends.** Designed in revision 14, cut in revision 15 per reviewer Finding 2; re-litigated in rev 18 per [Q.S.1](#qs1-qs9-fourth-pass-rubber-duck-gate-items-rev-18) and **DECIDED V1.0: accept-and-disclose (cache stays cut, Crescendo cost-cliff documented in [01 §1.2](01_tree_primitives.md#12-v10-known-limitations-sharp-edges-in-what-v10-does-ship))**. The mechanism (per-wave `sharedPieceCache` keyed on `node_id`, populated by the first leaf's regeneration of a shared interior Send, consulted by subsequent leaves' resolvers to fold cached pieces into `prepended_conversation` instead of re-firing the target) would collapse the 60-leaf-with-10-deep-shared-stale-prefix case from 600 to 70 calls. **Cut because** V1.0's two fan axes (`attempt`, `converter`) don't produce shared interior Sends in the trivial case — attempt-fan children diverge at the leaf-Send and converter-fan children diverge at the converter UserTurn. The chain-then-fan + Crescendo-with-depth-extension workflow IS affected; rev 18 accepted the cost cliff for V1.0 in exchange for the dumb-but-correct runner property (no per-wave cache invalidation bugs in unhappy paths). **Revisit in V1.x** with telemetry from the [Q.S.4 Crescendo experiment](#qs1-qs9-fourth-pass-rubber-duck-gate-items-rev-18) — if operators reach all-clean within 2 [Retry failed] cycles the cache stays cut; if not, the rev-14 design is restored. The `prompt`/`system_prompt`/`target` axes (V1.1+) can produce shared interior Sends and may justify the cache independent of the Crescendo workflow.
- **Q.7 — V1.x rate-limit handling: `Retry-After` header parsing + countdown timer + auto-enable.** V1.0 ships L1 diagnostic-only handling per [§3.3a `_format_api_error`](#33a-helpers-referenced-by-the-dispatch-step) and reviewer Finding 6a: leaves that hit 429 (or provider-specific rate-limit shapes) get `failure_class='rate_limited'`, surface distinctly in the wave-complete toast (`⏱ rate-limited` count), and disable [Retry failed] when all failed leaves are rate-limited. **V1.x adds:** parse the `Retry-After` response header (or provider-specific equivalents like Anthropic's `x-ratelimit-reset` epoch); render a countdown timer on the [Retry failed] button; auto-enable when the countdown expires. The leaf-failure-class field shipping in V1.0 makes V1.x a non-breaking addition — the migration is a UI/timer + per-leaf `retry_after_ms: number | null` field, no structural changes to `S`, the dispatch loop, or the cascade contract. **V1.x++ (deferred further):** per-target token-bucket throttling in the dispatch loop (L3 of the design spectrum) that prevents the initial 60-failure wave by holding ready leaves until tokens replenish. Requires target-capability lookup, per-target queue, config UI; the right time is once `TargetCapabilitiesInfo.max_requests_per_minute` exposure is plumbed through the runner.
- **Q.G.1 — Provider-specific rate-limit detection registry.** `_format_api_error`'s rate-limit detection needs a small mapping table of (status_code, error_code, response-body-snippet) tuples per provider: HTTP 429 covers most, but Anthropic's `overloaded_error` (sometimes HTTP 529), OpenAI's `rate_limit_exceeded` error code, Azure's specific shape, and Google's quota-exceeded responses each need their own match. **Lean for V1.0:** small registry at `frontend/src/runner/rateLimitDetection.ts` consumed by `_is_provider_rate_limit_shape(error)`. Per-provider entries are easy to add and don't require backend changes. **Promote to backend (V1.x+)** if the V1.x token-bucket throttling story lands — the backend already knows which provider each target maps to, so server-side detection avoids client-side maintenance of the registry.
- **Q.H.1 — Label inheritance for prepended pieces hydrated from pre-V1.0 ARs.** Under [01 §13.1 `openTreeFromAttackResult`](01_tree_primitives.md#131-v10-minimal-workspace) (Nit H), the first Refresh on a minted tree fires `create_attack` with `prepended_conversation` populated from the source AR's pieces (which have no `conversation_tree_id` label). Backend [`_resolve_labels` at attack_service.py:L716](../../../pyrit/backend/services/attack_service.py#L716) prefers existing piece labels over request labels. Two choices for the prepended pieces' label state: **(a)** inherit the new tree's `conversation_tree_id` via a backend-side rewrite or a label-fill-on-write; **(b)** stay un-labelled, preserving backend append-only semantics. **Lean: (b)** — History filter by `conversation_tree_id` returns only the new tree's leaves; operators who want to trace the legacy provenance use History filter by `conversation_id`. Needs a sentence of agreement in the [§9.4.5](01_tree_primitives.md#945-hard-backend-dependency-relocate-_validate_operator_match) PR description so reviewers see the choice. Does NOT affect the runner's labels-divergence invariant ([§4.3](#43-label-writes-the-round-trip-fidelity-contract)) — that invariant is about labels the runner writes on its own create_attack/add_message calls within one leaf's dispatch, which all carry identical labels per call by construction.
- **Q.R.1 — Drained-wave cost-modal suppression (V1.x).** The [§2.1 entry-point shim](#entry-point-shim-ordering-v10)'s queue-drain loop re-enters via `await refreshSubtree(...)` for each queued wave — every drained wave re-runs the full shim including step 3 (cost modal). Operator-hostile when 5+ waves are queued: the operator approved the top-level wave, but the cost modal fires again for each drained one. **Lean for V1.x:** suppress the cost modal on drained waves (the operator's queue-time confirmation propagates to drained successors); the suppression should respect the count-threshold for SAFETY (if the drained wave is unexpectedly large — say, due to operator edits between enqueue and dispatch widening the stale-set — still fire the modal). Mechanism: pass a `fromDrain: boolean` flag through the shim and bypass the cost guardrail when `fromDrain && estimatedCalls <= 2 * approvedCountFromOriginatingWave`. Out of V1.0 because V1.0 ships single-tree single-wave-at-a-time as the common case (§1.2); queue depth >1 is rare without the V1.1 tab strip.

### Q.S.1–Q.S.9: Fourth-pass + rubber-duck gate items (rev 18)

Formalized from the rev-18 rubber-duck review. **Q.S.1 and Q.S.2 are DECIDED V1.0** (rev 18; see entries below). **Q.S.3 remains a V1.0 BLOCKER candidate** gated on the [Q.S.4 Crescendo experiment](#qs1-qs9-fourth-pass-rubber-duck-gate-items-rev-18) outcome. Q.S.5–Q.S.9 are PR-sized follow-ups that do not gate implementer onboarding.

- **Q.S.1 — Intra-wave memoization: DECIDED V1.0 — accept-and-disclose (rev 18).** The rev-15 Q.6 cut argued "V1.0's two fan axes don't produce shared interior Sends." Rubber-duck Finding B.1 demonstrated this is true only for trivial cases: chain-then-fan trees with edits high up the chain — Crescendo with depth-extension ([crescendo.py:L74](../../../pyrit/executor/attack/multi_turn/crescendo.py#L74)) — produce the 60-leaf/10-deep-shared-stale-prefix case (600 add_message calls instead of ~70). **Decision:** V1.0 does NOT ship the rev-14 `sharedPieceCache`; the cost cliff is documented in [01 §1.2 known limitations](01_tree_primitives.md#12-v10-known-limitations-sharp-edges-in-what-v10-does-ship) so operators discover it via documentation, not the cost modal mid-refresh. The [02 §8.1](02_tree_ui_affordances.md#81-the-v1-chain-preview-banner--confirm-modal--toast--drawer-panel) cost-guardrail modal intercepts at 20 calls and the [02 §2.2](02_tree_ui_affordances.md#22-per-node-action-rail) `↻` tooltip cost-preview surfaces the cost on hover, so operators are forewarned at click time. V1.x revisits via [Q.6](#12-open-questions) with telemetry from the [Q.S.4](#qs1-qs9-fourth-pass-rubber-duck-gate-items-rev-18) Crescendo experiment — if the experiment shows operators reach all-clean within 2 [Retry failed] cycles, the cache stays cut; if not, the rev-14 design is restored. Rationale for accept-and-disclose: V1.0's runner-correctness story is small and well-tested; layering a per-wave cache adds invalidation bugs in unhappy paths (mid-wave cancel, leaf-edit-during-wave) that the V1.0 design has otherwise eliminated by construction. Accept-the-cost preserves the dumb-but-correct property until telemetry justifies the complexity.

- **Q.S.2 — Operator-as-tag vs operator-as-claim: DECIDED V1.0 — operator-as-tag (honor-system), rev 18.** Per rubber-duck Finding B.2: [§9.1](01_tree_primitives.md#91-operator-isolation-posture) had framed `operator` as "a tag the operator picks for History grouping + per-operator AR isolation, **not an auth claim**" while [§9.4.5](01_tree_primitives.md#945-hard-backend-dependency-relocate-_validate_operator_match) demanded the backend TIGHTEN `_validate_operator_match` to "reject anonymous requests against operator-owned ARs." These implied different mental models. **Decision:** operator-as-tag wins. §9.4.5 scaled back to relocation-only (no anonymous-rejection); the no-labels early-return is preserved by design — anonymous callers pass through unchallenged because the tag is honor-system, not an auth claim. The "Branch from here is the escape hatch" framing in §9.1 stays consistent: any operator can branch any tree they can read, creating a fresh AR under their own tag with no auth gate. **The V1.0 posture defends against accidental mis-attribution and casual cross-operator extensions, not against motivated bypass.** V1.1 multi-operator collaboration ([01 §13.8](01_tree_primitives.md#138-multi-operator-collaboration-v2)) revisits whether the tag should be promoted to a claim — if yes, the escape-hatch primitive needs a confirmation step at that time. V1.0 ships honor-system.

- **Q.S.3 — Per-target rate-limit circuit breaker (V1.0 BLOCKER candidate).** Per rubber-duck Finding B.5: AR-per-leaf's "each leaf is independent" claim is true at the data layer but **false at the rate-limit layer** — a 60-leaf attempt-fan against a 60-RPM target dispatches 60 leaves, collects 60 separate 429s, the [Retry failed] button is disabled when all failures are rate-limited (operator's only recourse is *"wait, click Refresh tree, watch the same thing happen, repeat"*). The Q.7 deferral of `Retry-After` parsing to V1.x compounds this. **The decision is:** add a per-target circuit breaker to the dispatch loop — when N consecutive 429s land within W seconds against one `target_registry_name`, halt further dispatches to that target for the rate-limit window (or a backoff). Add to [§10](#10-concurrency--maxparallel) as §10.5. Out of V1.0 only if the [Q.S.4](#qs1-qs9-fourth-pass-rubber-duck-gate-items-rev-18) Crescendo experiment shows operators reach all-clean within 2 [Retry failed] cycles; in if they don't.

- **Q.S.4 — Crescendo de-risk experiment (test plan; gates Q.S.1 + Q.S.3).** Per rubber-duck Finding E. Build a 60-leaf Crescendo-shaped tree in a throwaway test rig pointing at a real `gpt-4o` endpoint with a 60-RPM rate limit (or a `RoundRobinTarget` configured to simulate one). Click Refresh tree. Measure: (a) how many 429s land, (b) what the wave-complete toast says (including the new `✋ needs-fix` bucket from rev 18), (c) what the operator's `[Retry failed]` experience looks like across 2+ cycles, (d) total wall-clock to all-clean. Three possible outcomes: (1) operator clicks Retry twice and it works — V1.0 is fine without Q.S.1 + Q.S.3; (2) operator clicks Retry 8 times across 10 minutes and it eventually works — V1.0 needs Q.S.3 before ship, Q.S.1 deferred; (3) operator never reaches all-clean — V1.0 needs both Q.S.1 + Q.S.3 before ship. One day of work; cleanly de-risks the largest cost-cliff in the spec. Should run before the runner PR opens, not after.

- **Q.S.5 — Transform-reconciliation unification: one React effect instead of two runner walkers (V1.1 candidate).** Per rubber-duck Finding B.4: [§3.1 step 6 `reconcileAllTransforms`](#31-topological-walk) (wave-end, tree-wide) and the per-dispatch `reconcileTransformStates` (path-scoped) are two places that must stay in sync — adding a new transform-state rule in V1.1 requires updating both. Reviewer's structural alternative: own transform-state reconciliation in *one* place — a React effect that subscribes to "Send state went to `clean`" events and re-runs the per-node rule on the tree. Removes both runner-side invocations; the runner stops owning anything but its three Send transitions ([§5.1](#51-the-runner-only-owns-three-transitions)). **Defer to V1.1** because (a) the V1.0 two-place approach is correct and rev-15 reviewer-blessed; (b) the React-effect migration moves the runner's state-ownership boundary, which is bigger than a docs-only patch; (c) ScoreNode V1.0 render-only scope already minimizes the cost of the duplication. Revisit when V1.1's `runScorer(node_id)` makes ScoreNode a dispatch-class node and the reconciliation surface grows.

- **Q.S.6 — Accessibility follow-up doc (V1.0 deliverable; half-day scope).** Per rubber-duck Finding C.2: the docs are silent on focus management when layout shifts move a focused node off-screen; screen-reader announcement strategy for a 60-leaf fan completion; keyboard discoverability of the `+` edge affordance which is hover-only ([02 §2.1](02_tree_ui_affordances.md#21-per-edge-insert-on-edge-)); tab order through the per-node action rail. **The deliverable** is a 04_accessibility.md doc enumerating the keyboard-nav state machine, the focus-restore-on-layout-shift policy, and the screen-reader announcement throttling rules. Tractable in a half-day; not architecturally interesting but blocking for WCAG 2.1 AA-mandated security-team deployments.

- **Q.S.7 — `pieceCache` cross-tab read-after-write semantics (V1.x; documentation).** Per rubber-duck Finding C.4: [§3.3a piece-fetch caching](#33a-helpers-referenced-by-the-dispatch-step) spells out the pre-fetch mechanism but doesn't address: if tab A holds the lock, mutates pieces (via `add_message`), releases; tab B acquires, pre-fetches the same pieces — is the GET guaranteed to see tab A's writes? For SQLite with default isolation (the PyRIT default per `pyrit/backend/services/attack_service.py` session config) this is fine (committed = visible). For hypothetical PostgreSQL with REPEATABLE READ it's less obvious. **The fix** is a paragraph in §3.3a naming the assumed database isolation level ("read-committed or stronger") and the V1.0 single-user deployment context that makes the assumption safe. V2 multi-operator path ([01 §13.8](01_tree_primitives.md#138-multi-operator-collaboration-v2)) needs to revisit. Documentation patch; ~50 words.

- **Q.S.8 — Collapse `RootPromptNode` + `ImportMessageNode` into one `SourceNode { source: 'root' | 'import' }` (V1.x refactor).** Per rubber-duck Finding D.1: the two kinds differ only in source payload; both occupy the same side-effect class in the runner ([§4.1 "Source" branch](#41-the-resolved-root-to-leaf-path--prepended-final-user-turn)); the runner treats them identically through every spine. The current 6-kind taxonomy is 4 kinds masquerading as 6; collapsing Root/Import saves one `kind` branch in `conversationTreeToReactFlow`, one file under `frontend/src/components/Tree/nodes/`, and one branch in every consumer that switches on `kind`. **Defer to V1.x** because the V1.0 two-kind split is documented and the V1.0 implementer cost of carrying both kinds is one extra file (small). Revisit when the editor surface for each kind diverges enough to make the union shape awkward, or when V1.1 adds a third source variant (e.g., "import from local JSON" per [01 §1.2 export/import gap](01_tree_primitives.md#12-v10-known-limitations-sharp-edges-in-what-v10-does-ship)) and the rename becomes the natural moment.

- **Q.S.9 — Pure-event-log alternative reconsideration for V1.x scoping (decision point at V2 boundary).** Per rubber-duck Finding D.4: the ConversationTree-vs-AttackResult split's rejection of "pure event log + projection" was too curt for a decision V2 server-side collaboration will reopen. The V1.0 design already implements *most* of an event log expensively reimplemented as four separate mechanisms: §6.9 undo with state-snapshot widening, §9.4.3 BroadcastChannel advisory lock, §9.4.1 labels-decoding reload reconstruction, §10.3 per-tree wave queue. A pure event log would unify them. **The decision is:** revisit this explicitly at the V2 server-side trees scoping milestone (not before — V1.0 / V1.1 are committed to the ConversationTree shape). The V2 PR should weigh (a) event-sourcing rewrite vs (b) extending the V1.x ConversationTree with a `version` field ([already added in V1.0 per rev-18 §3.1](01_tree_primitives.md#3-data-model)) + a server-side last-write-wins resolver. Decision point, not gate item.

## Appendix: Runner Module Structure (Proposed)

```
frontend/src/runner/
├── runner.ts                  # public Runner interface + dispatch loop (§3)
├── materialization.ts         # resolve_prepended_conversation (§4.1)
├── stateSink.ts               # RunnerStateSink interface + React-bound impl
├── waveBookkeeping.ts         # waveId + waveTriggerKind enum (§6)
├── concurrency.ts             # Semaphore + fair-share pick (§10)
├── costGuardrail.ts           # threshold check + modal trigger (§2.3)
└── __tests__/
    ├── runner.dispatch.test.ts
    ├── runner.failure.test.ts
    ├── runner.concurrency.test.ts
    ├── runner.materialization.test.ts
    └── runner.integration.test.ts
```

The split keeps the dispatch loop (§3) under ~150 LOC by delegating; everything else is testable in isolation per §11.1.

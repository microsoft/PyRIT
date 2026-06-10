# Tree-Based UI — Affordances, Layout, and Scenarios

> Status: **DRAFT for review** — companion to [01_tree_primitives.md](01_tree_primitives.md).
> Scope: UX affordances, layout algorithm, scenario walkthroughs.
> Out of scope: data model (covered in primitives doc), implementation code, visual style.
> One primitives-level addition is requested here (§6); the rest is pure UX.

### Version-scope legend

This doc and [01_tree_primitives.md](01_tree_primitives.md) share the same version markers. See [01_tree_primitives.md §0 legend](01_tree_primitives.md#version-scope-legend) for definitions.

The condensed V1.0 surface area (revision 9):
- **Nodes:** `RootPrompt`, `UserTurn`, `Send`, `ScoreNode`, `FanNode(axis ∈ {attempt, converter})`.
- **Stacks:** Fan-Children Stack (§3.1) only — Synced-Peers Stack and Stack-`+` gating are V1.1 (§3.2, §3.4a). V1.1 design treated as provisional pending V1.0 operator feedback.
- **Layout:** plain Buchheim-Walker via `d3-hierarchy.tree()` — main-path pinning is V1.1 (§4.3).
- **Branching:** `branchFromNode` always-new-tree variant **ships in V1.0** via the minimal-Workspace data model ([01 §13.1](01_tree_primitives.md#131-v10-minimal-workspace)); clicking `📋` swaps the active tree to the clone. The sibling-subtree variant (`🌿`, V1.1) renders as disabled stub in V1.0. The full tab strip is V1.1.
- **Auto-reverse:** linear chain + per-piece converter pipelines from history ships V1.0. Multi-conversation fanout-detection ([01 §9.3.1](01_tree_primitives.md#931-fan-grouping-algorithm-v11--original_prompt_id-chain-flattening--wave_id-disambiguator)) is V1.1.
- **Reload reconstruction:** restores `currentTree` from URL fragment via auto-reverse ([01 §9.4.1](01_tree_primitives.md#941-reload-reconstruction-v10)). The `beforeunload` guard ([01 §9.4.2](01_tree_primitives.md#942-the-beforeunload-guard-v10)) protects unsaved edits. `BroadcastChannel` advisory lock ([01 §9.4.3](01_tree_primitives.md#943-concurrent-tab-advisory-lock-v10)) prevents two-tab fork-bombs.
- **Pick / Unpick:** ships in V1.0 against fan-children (single `promotedChildSlotIndex` per FanNode) — without the synced-peers draft-placeholder dance from §3.3, which is V1.1.
- **Reflog cap:** `REFLOG_CAP_PER_NODE = 50` (configurable per-Workspace, see [01 §6.6](01_tree_primitives.md#66-executionhistory-gc-the-reflog)); eviction is operator-visible.

## 1. Design Principles

These four principles drive every decision below.

1. **Familiar first.** The existing four chat-message buttons ([MessageList.tsx#L308-L420](../../../frontend/src/components/Chat/MessageList.tsx#L308-L420)) — *copy to input, copy to new conversation, branch conversation, branch attack* — are already in operators' muscle memory. Tree-view affordances should map onto these or replace them with something obviously better, never confuse them with a new vocabulary.
2. **Edge-affordances over modal buttons.** Adding a node into the middle of a chain is something operators want to do often. A `+` button that *appears between two nodes when you hover the edge* (the pattern used by n8n, Zapier, Linear's workflows) is cheaper than a "select node, click Insert After, pick type" modal flow.
3. **Stacks are the unit of repetition.** A `FanNode` with N identical-looking children is visual noise. The Stack — a single rendered card that *contains* N synchronized subtrees — is how the UI represents a fan that hasn't been edited per-child yet. The user's "drag follow-up over the fanned-out messages" intuition is this same concept.
4. **One canonical action per intent.** "Run this prompt 10 times" is one user intent. The UI should not require the operator to *choose between* "add Fan, axis=attempt" and "click re-run 9 times". Re-run multiple **promotes** to a Fan automatically.

---

## 2. Affordance Inventory

### 2.1 Per-edge: insert-on-edge `+`

The single most important affordance. When the operator hovers an edge between two nodes (or the empty space below a leaf), a translucent `+` chip slides in mid-edge. Clicking it opens a popover:

```
                    Send  ✓
                      │
                      │ + ← hover affordance, click to open
                      │
                  ╔═══╧═════════════════════╗
                  ║ Insert after this Send  ║
                  ║                          ║
                  ║ ▸ Follow-up user message║  (UserTurn, role=user)
                  ║ ▸ Inject assistant text ║  (UserTurn, role=simulated_assistant)
                  ║ ▸ Score                  ║  (ScoreNode)
                  ║ ▸ Fan out: ...           ║  (submenu: attempt / prompt / converter / target)
                  ╚══════════════════════════╝
```

The same affordance, hovered between a `UserTurn` and a `Send`:

```
                  UserTurn: "How do I bake bread?"
                      │
                      │ + ← popover changes contextually
                      │
                  ╔═══╧═════════════════════╗
                  ║ Insert after this turn  ║
                  ║                          ║
                  ║ ▸ Send to target        ║  (rare — usually auto-inserted)
                  ║ ▸ Append converter       ║  (modifies the UserTurn's pipeline)
                  ║ ▸ Fan out: converter    ║  (wraps in a Fan)
                  ║ ▸ Fan out: prompt        ║
                  ╚══════════════════════════╝
```

**Why context matters in the popover:** the legal next-node types depend on the upstream node's kind. After a `Send` you almost always want a follow-up or a fan; after a `UserTurn` you usually want a converter or send. Hiding illegal options is cheaper than enabling-with-error.

### 2.2 Per-node action rail

A small action row floats below each node card on hover/focus. Icons only when collapsed; labels appear on hover-of-the-icon.

> **Version scope.** Every icon below ships in V1.0 unless explicitly marked **V1.1**. V1.1-marked icons render as disabled in V1.0 with a tooltip pointing to the V1.0 fallback (where one exists). Disabled-in-V1.0 affordances keep their slot reserved so V1.1 is a state flip, not an introduction (the rationale is "don't create a V1.0 trigger that V1.1 would then repurpose"; see [01 §6.5](01_tree_primitives.md#65-branch-from-node---the-immutable-history-primitive)).

**Common to every node:**

| Icon | Action | Version | Notes |
|---|---|---|---|
| `↻` | Refresh | V1.0 | Per §6.3 in primitives. Long-press / shift-click opens `Refresh subtree` |
| `📋` | Branch from here / Clone tree | **V1.0** | Per §6.5 in primitives. **V1.0 lands** by swapping the Workspace's `currentTree` to the clone; source is re-openable from History. **V1.1 lands** as a new tab in the tab strip. Label: **"Clone tree"** on root, **"Branch from here"** otherwise. |
| `🌿` | Branch as subtree (same canvas) | **V1.1** | Per §6.5 in primitives. Lands the cloned slice as a sibling subtree of the source node in the *same* ConversationTree, no tab switch. **V1.0:** rendered disabled with tooltip *"Available in V1.1"*. The slot is reserved here so V1.1 enablement does not introduce a new trigger that conflicts with `📋`. Branch-glyph chosen for visual distinctness from `📋` (clipboard-glyph) — the two icons sit adjacent on every node's action rail and operators must not mistake them. |
| `🗑` | Delete | V1.0 | Confirmation modal; preserves backend `AttackResult`s under same `conversation_tree_id` (§5.16 below) |
| `🔍` | Open in linear view | V1.0 | Switches the linear pane to focus on this node's path; the tree view stays loaded (§10 in primitives) |

**`RootPromptNode`-specific:**

| Icon | Action | Version |
|---|---|---|
| `✏` | Edit prompt + target + system prompt (inline editor) | V1.0 |
| `📎` | Add attachment | V1.0 |

**`UserTurnNode`-specific:**

| Icon | Action | Version |
|---|---|---|
| `✏` | Edit text inline | V1.0 |
| `🔀` | Wrap in `FanNode(axis='prompt')` with this turn as variant #0 — the user's "shuffle" intuition | **V1.1** (depends on `prompt` axis; see [01 §4.4](01_tree_primitives.md#44-structural-nodes--the-single-fan-out-primitive)). **V1.0:** rendered disabled. |
| `⚡` | Open converter palette (adds to `params.converterPipeline`) | V1.0 |
| `≡` | Change role (`user` ↔ `simulated_assistant` ↔ `system`) | V1.0 |

**`SendNode`-specific:**

| Icon | Action | Version |
|---|---|---|
| `↻` | Re-run (single — one more attempt, recorded in `executionHistory`) | V1.0 |
| `↻×N` | Re-run multiple — **promotes to `FanNode(axis='attempt', variants=[…])` automatically** (§3.1 below) | V1.0 |
| `🎯` | Change target (per-node override) | **V1.1** (depends on `target` axis; rendered disabled in V1.0) |
| `💬` | View raw response panel (right-hand drawer) | V1.0 |
| `★` | Pin as "main" path leg (visual emphasis; see §4.3 layout) | **V1.1** (main-path pinning deferred — see §4.3). **V1.0:** the icon is not rendered at all (no V1.0 trigger to reserve; the centerline layout pass simply doesn't exist yet, so there is nothing the operator's flip-of-a-flag would activate). |

**`FanNode`-specific:**

| Icon | Action | Version |
|---|---|---|
| `+` | Add another variant | V1.0 |
| `≡` | Change axis (only legal before any children have executed; otherwise destructive op with confirmation) | V1.0 (axis choices limited to `attempt` and `converter` in V1.0 per [01 §4.4](01_tree_primitives.md#44-structural-nodes--the-single-fan-out-primitive)) |
| `⊟` / `⊞` | Collapse to Stack / Expand to per-child cards (§3 below) | V1.0 (Fan-Children Stack only; Synced-Peers Stack is V1.1) |
| `↻` | Refresh all children (parallel, respects `maxParallel`) | V1.0 |

**`ScoreNode`-specific:**

| Icon | Action | Version |
|---|---|---|
| `✏` | Configure scorer + params | **V1.1** (depends on `runScorer(node_id)` per [01 §4.5](01_tree_primitives.md#45-observational-nodes-no-side-effect-on-the-conversation)). **V1.0:** rendered disabled with tooltip *"Scorer configuration is V1.1; V1.0 displays scores already attached to upstream pieces."* Slot reservation against UX regression. |
| `📊` | View score distribution (across all leaves in current subtree) | V1.0 |

### 2.3 Canvas-level affordances

- **Top-left ribbon:**
  - `+ New tree` (when canvas is empty)
  - `← Linear view` toggle (switches the right pane to the linear chat; tree stays in the left pane)
  - `conversation_tree_id` chip + `Open in History` link + Copy affordance (the §9.4.3 two-tab workflow pastes this into a second browser tab)
  - **`Switch tree`** button (V1.0; §13.1 minimal-Workspace surface). Opens a popover listing the Workspace's `recentTreeIds`; selecting one calls `openTree(id)` and the canvas swaps. *V1.1 replaces this with the tab strip.*
  - Operator label
  - **Wave status:** when nodes are edited/stale, shows `"1 edited, 60 stale · ~60 calls · [Refresh tree]"`. During an in-flight wave, shows progress + cancel: `[ ●●●●●●○○○○ ] 6/60 (3 ✓, 0 ⚠, 0 ⏱, 1 ⦾, 1 ●) [Cancel]` — the five-value tail is `succeeded / failed / rate-limited / blocked / running`. `⏱ rate-limited` counts leaves whose `failure_class='rate_limited'` per [03 §3.3a `_format_api_error`](03_runner.md#33a-helpers-referenced-by-the-dispatch-step) (HTTP 429 or provider-specific overloaded shapes). `⦾ blocked` counts leaves dropped from `ready` by the [03 §5.3](03_runner.md#53-cascade-on-failure) in-flight cascade (an ancestor failed earlier in this wave). Cancel calls `runner.cancelWave(treeId)` per [03 §9](03_runner.md#9-cancellation); button transitions to disabled `[Cancelling…]` while in-flight leaves drain; the toast then reads *"Wave cancelled: 6 ✓, 0 ⚠, 0 ⏱, 1 ⦾, 54 cancelled. [View wave]"*. When the per-tree queue ([03 §10.3](03_runner.md#103-backpressure-per-tree-wave-queue)) is non-empty, a separate `[Cancel queued]` chip appears on the same banner and calls `runner.cancelQueued(treeId)` — drops queued waves without touching the active one. After a wave completes the toast in §8.1 takes over.
  - **Deep-chain warning** (V1.0 §1 V1.0 exclusions): when the deepest path in the current tree reaches 180 turns, the ribbon shows *"This conversation is approaching the 200-turn ceiling. Use Branch from a midpoint to keep extending."* with a quick-action chip that scrolls to a midpoint UserTurn and arms its `📋` button.
- **Bottom-right minimap** (react-flow built-in) showing the full tree with a viewport rectangle.
- **Bottom-left zoom controls** + a `Fit to view` button (also a keyboard shortcut `F`).
- **Right-side action drawer** (slides in when a node is selected) — tabs:
  - `Current` — params editor + most recent execution.
  - `Past runs (Reflog)` — per-node reflog popover content (Q.7.B).
  - `Recent waves` — ConversationTree-scoped wave list (§8.2); always available regardless of which node is selected.
  - `Compare` — V2 (§8.5).
- **Wave completion toast** (bottom-right, transient): `"Wave complete: 57 ✓, 3 ⚠, 0 ⏱, 0 ⦾. [View wave]"` — see §8.1. The four-value tail is `succeeded / failed / rate-limited / blocked`. `⏱ rate-limited` surfaces leaves whose `failure_class='rate_limited'` per [03 §3.3a](03_runner.md#33a-helpers-referenced-by-the-dispatch-step) (HTTP 429 + provider-specific overloaded shapes); the [Retry failed] button is **disabled when every failed leaf is rate-limited** (operator must wait for the target's rate-limit window to clear, then click Refresh tree manually). When in-flight cascade ([03 §5.3](03_runner.md#53-cascade-on-failure)) drops sibling leaves of a failed ancestor, the toast surfaces them as `⦾ blocked` (distinct from `⚠ failed`). The [Retry failed] button starts a fresh wave that retries `failure_class='transient'` failures and their blocked descendants; rate-limited leaves are excluded and remain failed in the wave summary.
- **Reflog eviction summary** (V1.0; §6.6 of primitives): when the runner evicts unpinned reflog entries during a wave, the count is **aggregated into the wave-complete toast** rather than firing per-eviction markers (which would stack and push the toast off-screen). The toast reads: *"Wave complete: 57 ✓, 3 ⚠. Past runs evicted: 12. [View wave]"*. Single-eviction events outside a wave (e.g., `makeCurrent` displacing an entry while at cap, §6.7) fire a single transient marker for ~8 seconds: *"Past run evicted from node X. [Pin evicted run] [Increase cap]"*. *Operator-facing terminology uses "past run(s)"* per the friendly-first §7 Q.7.A convention; "reflog" appears only in code, data-model docs, and the right-click git-alias menu.
- **Multi-tab busy modal** (V1.0; §9.4.3 of primitives): when this tab attempts a Refresh but another tab holds the advisory lock for this `conversation_tree_id`, a modal appears: *"Another tab is refreshing this tree. [Refresh anyway] [Wait]"*.
- **Operator-tag-required modal** (V1.0; [03 §2.1 entry-point shim step 1](03_runner.md#entry-point-shim-ordering-v10) + [01 §9.1 isolation posture layer 2](01_tree_primitives.md#91-operator-isolation-posture)): when the operator clicks Refresh tree / Refresh subtree / Refresh node while `currentOperator()` returns null/empty (the operator never set a tag this session, or cleared it from the ribbon), the runner aborts pre-dispatch and emits a `WaveEvent { kind: 'operator_tag_required' }`. The UI surfaces a modal: *"Operator tag required. This refresh would create AttackResults with no operator tag, which makes them hard to find in History and breaks per-operator isolation. Set your operator tag in the top bar, then click Refresh again. [Set operator tag] [Cancel]"*. `[Set operator tag]` focuses the ribbon's operator-tag input; `[Cancel]` dismisses; either way, no backend call has fired, no cross-tab lock was acquired, AND the cost-preview modal is suppressed (it would normally fire as shim step 3, after the lock acquire at step 2; the tag gate at step 1 returns first). *Note: `operation` (§15 audit tag) is NOT gated — operators mid-experiment may genuinely refresh without an operation set; a top-banner reminder surfaces when `operation` is empty but the wave proceeds.*
- **Ctrl-Z structural undo** (V1.0; per [01 §6.9](01_tree_primitives.md#69-node-editor-undo-v10)). Ctrl-Z (or Cmd-Z on macOS) inside the canvas pops the last structural edit — add/delete/editParams/regenerateFanChildren/makeCurrent — from the per-tree undo stack (capped at 20 entries, FIFO eviction). **Native input undo unaffected:** when a node's textarea has focus, Ctrl-Z does typing-level undo (browser default); operators press Esc to blur the textarea before structural Ctrl-Z reaches the canvas handler. Tree-swap clears the stack; reload loses it (matches the [01 §9.4.1](01_tree_primitives.md#941-reload-reconstruction-v10) reload-loss contract for edits). No redo in V1.0 — Ctrl-Shift-Z lands V1.x if operators report needing it.

### 2.4 Per-stack affordances

When a Fan is in Stack rendering (§3), the stack itself has its own action rail at its bottom edge:

| Icon | Action | Version |
|---|---|---|
| `+` | Add a synchronized child to all members of the stack (the "fan-through" case — §5.6) | **V1.1** (depends on Synced-Peers Stack — §3.2). **V1.0:** rendered disabled with tooltip *"Available in V1.1"*. |
| `⊞` | Expand stack to show per-child cards | V1.0 |
| `🎯` | "Pick one" — promote one member (sets `FanNode.params.promotedChildSlotIndex`, dims the others) | V1.0 (without the V1.1 draft-placeholder dance from §3.3 — V1.0 just dims the non-promoted children) |
| `↻` | Refresh all children | V1.0 |

---

## 3. The Stack — Two Distinct Visual Aggregations

The doc previously described "the Stack" as one concept with two uses. The second-pass review of decision #3 showed they are **two distinct render rules** that often coexist in the same tree but follow different predicates and have different operator semantics. Naming them separately removes a real source of confusion.

| | **Fan-Children Stack** (§3.1) | **Synced-Peers Stack** (§3.2) |
|---|---|---|
| What it groups | Direct children of one `FanNode` whose subtrees look identical (typically `attempt`-axis) | N nodes added together via Stack-`+` (the §5.6 fan-through pattern), wherever they live in the tree |
| Trigger | Automatic on render when the predicate holds | Operator clicks the synced-peer Stack's `+` affordance |
| Underlying field | None — pure derivation from `parentId` + structural identity | `addedToStack: boolean` on each peer (see §6.1) |
| Edit semantics | None — fan-axis variants ARE the per-child differences, there is nothing to "sync" | Stack-edit propagates to all peers via parent-walk peer detection |
| Decomposes when | A child's subtree shape differs from peers | A peer's `params` differs from peers (divergence is implicit) |

Both can apply at different layers of the same canvas. The §5.6 scenario has *both* — the fan card aggregates 10 identical Send children (Fan-Children Stack), and below them sit 10 synced UserTurns added by Stack-`+` (Synced-Peers Stack).

### 3.1 Fan-Children Stack — visual aggregation only

When a `FanNode` has N children with **identical recursive subtree structure** (e.g., right after creation of an `attempt` fan, or after a "Refresh all"), the UI does not render N separate cards. It renders one card with a multiplicity badge:

```
   UserTurn: "How do I bake bread?"
       │
       ▼
   ┌─────────────────────────────────┐
   │ Fan: axis=attempt, n=10         │
   │                                  │
   │  ┌─────────────────────────┐    │
   │  │  Send  ×10              │    │   ← Fan-Children Stack: 10 Sends
   │  │  "9 ✓, 1 ⚠"             │    │     shown as one card with
   │  │  ▶ expand to see each   │    │     aggregate status
   │  └─────────────────────────┘    │
   └─────────────────────────────────┘
```

Compare to expanded rendering:

```
   ┌─────────────────────────────────┐
   │ Fan: axis=attempt, n=10         │
   │  ┌──────┐┌──────┐┌──────┐...    │
   │  │Send✓ ││Send✓ ││Send⚠ │      │   ← per-child cards: visual sprawl
   │  └──────┘└──────┘└──────┘       │
   └─────────────────────────────────┘
```

Stack rendering is the default; expand-on-demand. **Collapse to Stack** is auto-applied when N>3 and all children are structurally identical; otherwise expanded.

**There is no data-level synchronization here.** Fan-axis children of `prompt`/`converter`/`target`/`system_prompt`/`temperature` are deliberately *different* (the variant payload IS the difference), so they never collapse — only the `attempt` axis produces a collapsible Fan-Children Stack in practice. None of these children carry `addedToStack`; the aggregation is a pure render rule keyed on `parentId` + structural match.

### 3.2 Synced-Peers Stack — synchronized authoring surface

> **Version scope: V1.1 (design treated as provisional pending V1.0 operator feedback).** The synchronized authoring surface (the user's "drag a follow-up over the fanned-out messages" intuition) lands in V1.1. The Stack-`+` affordance on Fan cards renders disabled in V1.0 (see [§2.4](#24-per-stack-affordances)). **V1.0 fallback for fan-through:** operators expand the Stack (`⊞`) and add a follow-up under each child individually, or wait for V1.1. The `addedToStack` field on `ConversationTreeNodeBase` is **not present** in the V1.0 type (§6.1 deferred to V1.1; revision 9 dropped the V1.0 reservation).
>
> **Why provisional:** the parent-walk peer detection, the params-deep-equality re-stacking rule, the divergence-decomposes-stack behavior, and the Promoted-state draft-placeholder semantics from [§3.3](#33-stack-semantics---three-operations-two-visual-states) are clever but have not been pressure-tested by real operators. The V1.0 release is the first time operators will use Fan-Children Stack at scale and form opinions about whether the synced-peers metaphor matches their workflow at all. **Revision 9 commits to revisiting the entire §3.2 design after V1.0 ships** — if operators don't actually want the fan-through pattern, or want something different (e.g., copy-the-edit-to-all instead of bidirectional sync), the V1.1 design changes accordingly. The detailed spec below is the leading candidate, not a frozen commitment.

The user's "drag a follow-up over the fanned-out messages" intuition translates to: **a Stack accepts new children, and adding a child to a Stack adds it under each member, with the new descendants synced to each other.**

```
   ┌─────────────────────────────────┐
   │ Fan: axis=attempt, n=10         │
   │                                  │
   │  ┌─────────────────────────┐    │
   │  │  Send  ×10              │    │   ← Fan-Children Stack (§3.1)
   │  │  "9 ✓, 1 ⚠"             │    │
   │  └─────────────────────────┘    │
   │                                  │
   │              + ← stack `+` affordance: "add to all"
   │                                  │
   └─────────────────────────────────┘

   (click `+`, choose "Follow-up user message")
   ┌─────────────────────────────────┐
   │ Fan: axis=attempt, n=10         │
   │                                  │
   │  ┌─────────────────────────┐    │
   │  │  Send  ×10              │    │
   │  └─────────────────────────┘    │
   │              │                   │
   │              ▼                   │
   │  ┌─────────────────────────┐    │
   │  │  UserTurn  ×10 (synced) │    │   ← Synced-Peers Stack:
   │  │  "Now expand on point 3"│    │     all 10 share addedToStack=true,
   │  └─────────────────────────┘    │     edit propagates to all
   │              │                   │
   │              ▼                   │
   │  ┌─────────────────────────┐    │
   │  │  Send  ×10              │    │   ← also Synced-Peers Stack
   │  │  (draft, click refresh) │    │     (auto-inserted, also marked
   │  └─────────────────────────┘    │      addedToStack=true)
   └─────────────────────────────────┘
```

Under the hood the conversation tree has **10 actual `UserTurnNode`s** (and 10 auto-inserted `SendNode`s) under the 10 fan-children Sends. Each carries `addedToStack=true`. The grouping is **not** recorded in a shared UUID — it is **derived** at render time by walking each candidate's `parentId` chain to the nearest `FanNode` ancestor and grouping those that share the same ancestor + depth-below.

**Peer-detection rule (precise):** two nodes A and B are Synced-Peers Stack peers iff
1. `A.addedToStack === true` AND `B.addedToStack === true`,
2. The nearest `FanNode` ancestor of A equals the nearest `FanNode` ancestor of B (same node UUID), AND the number of edges from each up to that ancestor is equal,
3. `A.params` deeply equals `B.params` (divergence is implicit — no flag).

All three keyed on data the conversation tree already has (`parentId`, `kind`, `params`). No new UUIDs, no synthetic signatures.

### 3.3 Stack semantics - three operations, two visual states

> **Version scope.** The two-state table below is the **V1.1 model** with draft-placeholder semantics. **V1.0 simplification:** with no Synced-Peers Stack (§3.2 is V1.1), the Promoted state collapses to "dim the non-promoted children; the Stack-`+` is disabled." No draft placeholders, no Stack-edit divergence, no Unpick-activates-placeholders. **V1.0 Pick = set `promotedChildSlotIndex`; visual dim. V1.0 Unpick = clear it; visual re-equalize.** That's it. The full table below is preserved for V1.1 implementers; V1.0 readers can mentally drop everything about Stack-`+`, draft placeholders, and Stack-edit-propagation.

Stack operations apply to both Fan-Children and Synced-Peers stacks; they share UI affordances. Per Q.A.4: instead of detaching a picked member into its own card (which would shift the layout), **promotion is purely a visual state on the existing Stack**. The Stack card stays put; the promoted member gets full color + highlight border; the others dim to ~40% opacity. The `+` affordance stays anchored to the Stack and unambiguously means "add a child to this layer" (see §3.4 for the one-`+`-per-fan-layer gating rule).

This collapses the previous revision's three-state model (synced / promoted-detached / frozen) into **two states** with one transition:

| State | When | Visual | Stack `+` adds child to | Stack-edit targets |
|---|---|---|---|---|
| **Synced (default)** | No promotion set (`FanNode.params.promotedChildSlotIndex` is `null`) | All N peers rendered equally | All N peers (a new Synced-Peers Stack, `addedToStack=true` on each new node, all non-draft) | All N peers via parent-walk rule (§3.2) |
| **Promoted** | One peer set as promoted (`FanNode.params.promotedChildSlotIndex` is some slotIndex) | Promoted peer: full opacity + highlight border. Others: ~40% opacity, hover-readable, not editable, no new children added under them. | All N peers (`addedToStack=true` on each), BUT only the promoted peer's added node is non-draft; the other N-1 added nodes are `draft` placeholders that show as dimmed shadows in the expanded view. If the operator later Unpicks, the placeholders activate (transition to `edited`) so the Stack becomes a real Synced-Peers Stack across all N. | Promoted peer only |

**Three operations:**

1. **Stack-edit** - edit text or params on the Stack card. Under *Synced* this propagates to all peers (Synced-Peers Stack via parent-walk rule, §3.2). Under *Promoted* it targets only the promoted peer's path; the N-1 draft placeholders mirror the edit so that if the operator later Unpicks, the placeholders are ready to activate.
2. **Pick** - set `FanNode.params.promotedChildSlotIndex` to the clicked member's `slotIndex`. Instant visual transition to Promoted state; no layout shift; no tree restructuring; no execution change. Clicking a different member's "Pick" while already in Promoted state simply swaps the promotion; any draft placeholders inherited from the previous promotion remain dimmed under their new context. The cherry-pick analogue from the git mental model in §3.5.
3. **Unpick** - set `promotedChildSlotIndex` back to `null`. Returns to Synced. The N-1 placeholders activate (each is now a peer just like the originally-promoted one was). Useful when the operator decides "actually I want to keep exploring all 10 branches synchronously again".

**Why N-symmetric peers in Promoted state instead of singletons?** Per Q.3.3 (revision 7): a singleton add in Promoted state followed by Unpick would leave an asymmetric tree (1 peer under one fan-child, 0 under the others), which the §3.4 predicate sees as un-stackable and decomposes into expanded per-card rendering. Symmetric N-peer adds with N-1 placeholders preserves the option to return to synced exploration without operator surprise. The placeholders consume no token cost (they don't refresh until activated) and the runner only dispatches `Send`s for non-draft nodes.

**Promotion is per-FanNode.** If a tree has nested fans (Fan A with 10 children, child #4's subtree contains Fan B with 5 children), Fan A's promotion of child #4 does not affect Fan B. Fan B has its own independent `promotedChildSlotIndex`. The visual de-emphasis cascades (child #4's subtree renders at full opacity; #1-3, #5-10 and their entire subtrees render dimmed), but the *editing* model stays per-FanNode.

**Pursuing two promotions in parallel** is not a primitive - it is a tree-clone operation via `branchToNewTree(treeRoot)` (§6.5 of primitives). Two trees, two tabs, two different `promotedChildSlotIndex` values. Operators flip between tabs to compare.

### 3.4 Stack rendering predicates - both apply, independently

**Fan-Children Stack** (§3.1) renders iff:
1. Parent is a `FanNode`.
2. All children have structurally identical subtrees (recursive shape and kinds match; `params` and execution may differ).
3. Operator has not explicitly clicked "Expand" on this Fan.

**Synced-Peers Stack** (§3.2) renders iff:
1. Two or more nodes share the same nearest `FanNode` ancestor at the same depth below.
2. All of them have `addedToStack=true`.
3. All of their `params` are deeply equal (any divergence collapses the visual stack into per-card rendering for that layer; convergence later re-stacks).

The two predicates are independent. A given canvas may show a Fan-Children Stack at the fan layer and a Synced-Peers Stack two layers below it (as in the §5.6 worked example). Decomposition of one does not force decomposition of the other.

The Promoted state is **orthogonal** to both predicates: promotion does not break stack rendering. The stack with one promoted member is still rendered as a stack (the visual difference is opacity + border, not layout).

### 3.4a Stack-`+` gating - one synced layer per fan, chain extends downward

> **Version scope: V1.1.** This gating rule only applies once Synced-Peers Stacks exist; V1.0 has none, so the Stack-`+` affordance on Fan cards is uniformly disabled (see [§2.4](#24-per-stack-affordances)) and no gating logic is needed. The rule below describes V1.1 behavior.

Per Q.3.4 (revision 7): the Stack-`+` affordance is **gated** so that each fan layer can host at most one synced-peer set. The rule disambiguates the affordance and eliminates the "two batches merge into one stack" surprise from earlier revisions.

**Stack-`+` on a Fan card** (the affordance that begins a new synced chain) is shown iff no `addedToStack=true` node has this Fan as its nearest-Fan ancestor at depth-below=2. In plain words: a Fan offers Stack-`+` until the operator clicks it once. After that, the chain extends downward from the new Synced-Peers Stack, not from the Fan.

**Stack-`+` on a Synced-Peers Stack card** (the affordance that extends an existing synced chain) is **always shown**. The new peers it creates inherit the same nearest-Fan ancestor + a deeper depth-below, so they form their own layer and don't collide with anything above.

Visually:

```
Fan(attempt, n=5)
  ┌───────────────────────────────┐
  │ [Send ×5]                       │
  │     │                            │
  │     +  ← Stack-+ available       │  (first add at this depth)
  │     ↓                            │
  │ [UserTurn ×5 "Why?"]             │  ← addedToStack=true
  │     │                            │
  │    (no +)  ← Stack-+ DISABLED    │  (fan layer already has a synced layer)
  │                                  │
  └───────────────────────────────┘

      │  (the chain extends here, from the synced-peers stack)
      ▼
  ┌───────────────────────────────┐
  │ [UserTurn ×5 "Why?"]             │
  │       +  ← Stack-+ available     │  (extend the chain downward)
  │       ↓                          │
  │ [Send ×5 (draft)]                │
  └───────────────────────────────┘
```

**Edge cases:**
- Operator deletes the synced-peer layer entirely → Stack-`+` on the Fan re-enables (predicate true again).
- Operator diverges one peer (per-edit) so the synced layer visually decomposes → Stack-`+` on the Fan stays disabled. Divergence is a render state, not a data-model state; the peers still exist with `addedToStack=true`.
- Nested fans (Fan A at depth 0, Fan B at depth 4 inside one of A's branches) → Fan A's Stack-`+` is gated on A's depth-below=2; Fan B's is gated on B's depth-below=2. Independent gates.

**Implementation cost:** one tree-walk predicate check per fan render. Bounded by fan-children count. Cheap.

**What this means for the operator:** if they want "two different follow-ups in parallel under all 5 attempts," they either (a) edit one of the existing synced UserTurns into a fan itself (`Fan(axis='prompt', variants=[A, B])`), or (b) clone the whole tree and try the second follow-up in the clone. Both are more honest about what they're doing than two competing synced layers at the same fan depth.

### 3.5 Git mental model

The primitives doc has the full table in [01_tree_primitives.md §6.8](01_tree_primitives.md#68-git-mental-model-for-operator-vocabulary); this section is the affordances-doc summary an operator might read first.

The whole tree-view design lines up surprisingly well with git, and **operator vocabulary in the UI uses git verbs**:

- A tree node's `execution` is its current **commit** (the most recent `ExecutionRecord`). Its `executionHistory` is the **reflog**.
- Editing a node and then clicking the canvas-level "Refresh tree" button performs what git calls a **rebase** — downstream nodes that became stale rebuild on top of the new upstream.
- The "Pick" operation on a Stack is **cherry-pick**: choose one of N runs as the canonical commit on this ref.
- Branching from a node is `git branch new-branch <commit>` — a cheap copy of refs, no commits duplicated.
- `branchToNewTree(root)` is "Clone tree"; `branchToNewTree(anyOtherNode)` is "Branch from here". One function, two labels (§6.5 of primitives). The V1.1 `branchToSubtree(nodeId)` ships under a separate `🌿` affordance with sibling-subtree landing.
- Selecting a past run from a node's reflog enters **detached HEAD** rendering (dotted border, banner); re-running while detached creates a fresh tip and exits detached state.

**Two places the analogy is loose** (operators should know):

- A git branch has one tip; our conversation tree has many tips (one per leaf Send). So "tree = branch" is more like "tree = a workspace containing one or more git-like ref chains".
- Git rebase is destructive (old commits become unreachable from any ref). Our refresh is **non-destructive** — old `ExecutionRecord`s stay in each node's reflog (capped at `REFLOG_CAP_PER_NODE`, default 50, configurable per-Workspace; see [01 §6.6](01_tree_primitives.md#66-executionhistory-gc-the-reflog)), and the underlying backend `AttackResult`s remain queryable in the History tab filtered by `conversation_tree_id` regardless of tree-side state.

The data model keeps its existing names (`conversation_tree_id`, `ExecutionRecord`, `executionHistory`, `branchToNewTree` / V1.1 `branchToSubtree`). Primary UI button labels match the API verbs (`Refresh node` / `Refresh subtree` / `Refresh tree`). Git terminology surfaces for execution-history concepts only — `Reflog` / `Past runs` tab title, `Cherry-pick` Stack action, `Checkout this run` for inspecting past runs, `Make current` for promoting from the reflog, `Clone tree` / `Branch from here` for `branchToNewTree`.

---

## 4. Layout

### 4.1 Goals

In rough priority order:

1. **No overlap.** Hard constraint.
2. **Determinism.** Same tree → same coordinates. Operator muscle memory is real.
3. **Tightness.** Use horizontal space efficiently; wide trees should not be 4× wider than necessary.
4. **Stable under edit.** Adding/removing one node should shift the rest of the tree as little as possible — operator focus stays where it was. This is a layout-engine pick + an animation policy (§4.6).
5. **Main path is visually obvious.** When a leaf is pinned (§2.2 SendNode `★`), the root→leaf chain renders as a perfectly straight vertical spine. **V1.1** — main-path pinning is deferred from V1.0 (the `★` affordance is not rendered in V1.0; see §2.2 and §4.3 below).

### 4.2 Algorithm comparison

| Algorithm | Time | Tightness | Equal-subtree symmetry | Stability under edit | Notes |
|---|---|---|---|---|---|
| **Naïve DFS width-summing** (what §8.2 of primitives proposes) | O(n) | Loose (always equal to sum of widths) | Yes | OK | The 50-LOC option. Wastes horizontal space when subtrees are very different sizes |
| **Reingold–Tilford** | O(n²) | Tight (subtree contours interleave) | Yes | OK | The textbook "tidy tree". Quadratic in the worst case |
| **Buchheim–Walker** | O(n) | Same as Reingold–Tilford | Yes | OK | Reingold–Tilford done in linear time. The standard for "tidy trees" today. This is what `d3-hierarchy.tree()` actually implements |
| **Force-directed** (d3-force) | O(n²) per iter | Variable | No (re-runs converge differently) | Bad — every edit re-jostles the whole graph | Wrong shape for our tree; reject |
| **Sugiyama** (dagre) | O(n²) typical | Good | No (DAG-oriented) | OK | Designed for DAGs; overkill for our tree |
| **Manual / grid** | — | — | — | — | Operator-positioned; doesn't scale to fan-outs; reject |

### 4.3 Recommendation: Buchheim–Walker + pinned main path + adaptive collapse

> **Version scope.** **V1.0 ships plain `d3-hierarchy.tree()`** — layer 2 below (Buchheim–Walker over the whole tree). The Stack-collapse logic (layer 3) ships in V1.0 for Fan-Children Stack only. **Main-path pinning (layer 1) is V1.1**, when the `★` Pin affordance (§2.2 SendNode rail) is enabled. The three-layer design is preserved here for V1.1 implementers; V1.0 readers can mentally skip layer 1.

Three layers, applied in order:

1. **(V1.1) Identify the main path** (if any leaf is pinned). The main path is the unique root→pinned-leaf chain. Pin every main-path node's x-coordinate to a fixed centerline.
2. **(V1.0) Buchheim–Walker for the rest.** In V1.0, applied to the entire tree (no main path). In V1.1, applied to each off-main subtree with the main-path-side contour treated as a wall.
3. **(V1.0) Render-time stack collapse.** Nodes identified as Fan-Children Stack peers by the predicates in §3.1 are folded into a single Stack card. (Synced-Peers Stack collapse is V1.1 per §3.2.)

The V1.0 layout call simplifies to:

```ts
function layout(tree: ConversationTree): Map<ConversationTreeNodeId, Point> {
  // V1.0: plain Buchheim–Walker on the whole tree
  return buchheimWalker(tree.root, /* side */ 'center')
}
```

The full V1.1 algorithm:

```ts
function layout(tree: ConversationTree): Map<ConversationTreeNodeId, Point> {
  const positions = new Map<ConversationTreeNodeId, Point>()
  const mainPath = computeMainPath(tree)           // V1.1: root → pinned leaf, or empty

  // 1. (V1.1) Lay out main-path nodes on the centerline
  let y = 0
  for (const node of mainPath) {
    positions.set(node.id, { x: 0, y })
    y += VERTICAL_SPACING
  }

  // 2. For every branching point on the main path, lay out the off-main subtree
  for (const branchPoint of mainPath) {
    for (const child of branchPoint.children) {
      if (mainPath.includes(child)) continue
      const subtreeRoot = child
      const isLeftOfCenter = chooseSide(branchPoint)   // alternates / packs tightly
      const offset = buchheimWalker(subtreeRoot, isLeftOfCenter)
      for (const [nodeId, point] of offset) {
        positions.set(nodeId, point)
      }
    }
  }

  // 3. (V1.0) If no main path is pinned, fall back to plain B–W on the whole tree
  if (mainPath.length === 0) {
    return buchheimWalker(tree.root, /* side */ 'center')
  }

  return positions
}
```

**Why this beats the §8.2 naïve DFS:** the naïve approach reserves `Σwidth(children)` for every parent. Reingold–Tilford-style algorithms let small subtrees nestle into the gaps of large ones, often halving total width. For our use case where fan-outs frequently produce wide subtrees next to narrow chains, the tightness win is substantial.

**Library choice:**

- For the **layout primitive itself**, use `d3-hierarchy`'s `tree()` function — ~10 KB, well-tested, exactly the Reingold–Tilford-flavored "tidy tree" we need. We DO NOT pull in the rest of `d3` — `d3-hierarchy` is a standalone package.
- For the **main-path constraint and stack-collapse logic**, write our own ~80 LOC on top of `d3-hierarchy` output.

This is a small upgrade from the §8.2 recommendation (which was "custom DFS, deterministic, ~50 LOC, dagre as fallback"). The honest reason to upgrade: the user has now explicitly raised the question of how to avoid horizontal sprawl, and B–W is the textbook answer to exactly that. §8.2 of `01_tree_primitives.md` should be updated to reflect this.

### 4.4 Edge routing

Three options, with a clear winner:

| Style | When it's good | When it's bad |
|---|---|---|
| **Straight lines** | Few nodes, short distances | Crosses other nodes in dense trees |
| **Bezier curves** (react-flow default) | Looks nice; few crossings | Hard to follow at scale; ambiguous origin handle |
| **Orthogonal / "Manhattan"** | Mirrors org-chart conventions; obvious parent-child relationships; no crossings if layout is right | Stiff-looking; needs corner-routing logic |

**Recommendation: Orthogonal.** Tree layouts look like org charts; org charts use orthogonal routing for a reason — operators read them top-down and following a right-angle path is unambiguous. React-flow exposes `type: 'smoothstep'` which gives rounded orthogonal corners and is the standard choice for tree-like diagrams.

### 4.5 Animation policy on layout shifts

When a node is added/removed/moved, the rest of the tree may shift. We don't want a 200 ms "everything jumps" effect.

Policy:

- **Position changes < 4 px**: instant, no animation (avoids "twitch").
- **Position changes 4–100 px**: animate with a 200 ms `ease-out`.
- **Position changes > 100 px** (operator added a big subtree off-screen): pan the viewport to *follow* the affected subtree's centroid instead of animating the layout shift in place. Operator focus stays anchored.
- **Stack-collapse / expand transitions**: 250 ms, scale + opacity. The stack card "expands into" the per-child cards.

Use `framer-motion`'s `layout` animations if we want to take advantage of FLIP transitions; otherwise raw CSS transitions are fine and lighter (~0 bundle cost vs. ~50 KB).

### 4.6 Stack collapse policy at different zoom levels

Adaptive: as the operator zooms out, Stacks aggregate more aggressively.

| Zoom | Stack rendering |
|---|---|
| ≥ 100% | Stack shows: card + multiplicity + 3 most-recent execution summaries |
| 50–100% | Stack shows: card + multiplicity + aggregate status (e.g., "9 ✓, 1 ⚠") |
| < 50% | Stack shows: dot + multiplicity badge |
| < 25% | Whole subtrees beyond depth 2 collapse into a single "+N subtree" indicator |

Lazy expansion (operator click) overrides the zoom rule.

---

## 5. Scenario Walkthroughs

Eighteen scenarios. Each: **goal → action sequence → before/after sketch → verdict (✓ design handles / ⚠ gap / 🛠 needs work)**.

State suffix legend: `✓` clean, `↻` stale, `●` running, `⚠` failed, `◯` draft, `🔒` operator-locked.

### Scenario → version map

The full design surface is documented below. The V1.0 release covers the scenarios that touch only V1.0-shipped primitives.

| Scenario | Version | V1.0 fallback if V1.1 |
|---|---|---|
| 5.1 Greenfield: first send | V1.0 | — |
| 5.2 Continue the conversation | V1.0 | — |
| 5.3 Re-roll the last response | V1.0 | — |
| 5.4 "Try this prompt 10 times" (attempt fan) | V1.0 | — |
| 5.5 Pick one of 10 to continue | V1.0 | Per §3.3 V1.0 note: visual dim only, no draft-placeholder dance |
| 5.6 Fan-through (synced follow-up to all branches) | **V1.1** | Operator expands the Stack and types the follow-up under each child individually |
| 5.7 Try 3 different converters on the same prompt | V1.0 | — |
| 5.8 Sweep across 3 targets | **V1.1** | Operator manually clones the tree (via `📋` Clone tree, which now ships V1.0) per target, editing the target on each clone's root prompt |
| 5.9 Edit upstream: visual propagation | V1.0 | — |
| 5.10 Refresh subtree | V1.0 | — |
| 5.11 Branch from a node | **V1.0** | Ships via the always-new-tree variant of `branchFromNode` (Patch #1, revision 9). V1.0 lands by swapping the active tree; V1.1 lands as a new tab in the strip. |
| 5.12 Open a historical attack (auto-reverse) | V1.0 (linear+converter) | The V1.1 fanout-detection mapping is the only gap; V1.0 shows the linear chain with converter pipelines, no implicit FanNodes |
| 5.13 Operator-locked branch | V1.0 | — |
| 5.14 Partial failure mid-refresh | V1.0 | — |
| 5.15 Drill into linear view | V1.0 | — |
| 5.16 Delete a branch | V1.0 | — |
| 5.17 Edit an early node in a large tree | V1.0 | — |
| 5.18 Browse refresh waves across the whole workspace | **V1.0** (depends only on `wave_id` labels which ship V1.0; the V1.x History-tab "Group by wave" toggle is the implementation surface) | — |

### 5.1 Greenfield: first send

**Goal:** Operator wants to send a single prompt.

**Actions:**
1. Click `+ New tree` in the empty canvas.
2. RootPromptNode appears, focused. Operator types text + picks target.
3. Operator clicks `Send` button on the RootPromptNode card (or presses Enter).
4. A `SendNode` is auto-inserted as the RootPrompt's child; runner fires; node transitions `draft → running → clean`.

```
Before:                  After click:               After send:
(empty canvas)           [RootPrompt: "Hi"]◯        [RootPrompt: "Hi"]✓
                                                          │
                                                          ▼
                                                    [Send → "Hi there!"]✓
```

**Verdict:** ✓ Handled.

### 5.2 Continue the conversation

**Goal:** Operator wants to add a follow-up user message after seeing the response.

**Actions:**
1. Hover the edge below the `Send` node. `+` chip appears.
2. Click `+`. Popover shows "Follow-up user message" as the first option. Click it.
3. New `UserTurnNode` appears below `Send`, focused, empty.
4. Operator types text, presses Enter.
5. A new `SendNode` auto-inserts under the new `UserTurnNode`. Runner fires.

```
[RootPrompt: "Hi"]✓                  [RootPrompt: "Hi"]✓
       │                                     │
       ▼                                     ▼
[Send → "Hi there!"]✓        →        [Send → "Hi there!"]✓
       │                                     │
       + ← hover                             ▼
                                       [UserTurn: "How are you?"]◯
                                             │
                                             ▼
                                       [Send]●
```

**Verdict:** ✓ Handled. Edge-affordance + auto-Send insertion makes this 2 clicks.

### 5.3 Re-roll the last response

**Goal:** "I didn't like that answer, try again."

**Actions:** Click `↻` on the `SendNode`.

**UI shows:** Node briefly enters `●` state. Old `ExecutionRecord` moves into `executionHistory` (visible in the right-side drawer with a "Compare" toggle). New `ExecutionRecord` lands as `clean`. **Tree shape unchanged.**

**Verdict:** ✓ Handled.

### 5.4 "Try this prompt 10 times" (attempt fan from a fresh Send)

**Goal:** Sweep N attempts on the same prompt.

**Action A (operator knows up-front):**
1. After typing the prompt and before clicking Send, click `↻×N` on the RootPrompt's pending Send affordance. Picker appears: "How many attempts? [10]".
2. Click OK. A `FanNode(axis='attempt', n=10)` is created with 10 `SendNode` children, rendered as a Stack.

**Action B (operator decides after first response):**
1. After seeing the response, click `↻×N` on the existing `SendNode`. Picker: "Total attempts including this one? [10]".
2. The existing `SendNode` is **wrapped**: a new `FanNode(axis='attempt')` is inserted as the SendNode's parent, the existing SendNode becomes variant #0, 9 new draft SendNodes are added as variants #1–9.

```
Before (Action B):                After:
[Send → "X is ..."]✓              ┌─────────────────────────────┐
                                  │ Fan: axis=attempt, n=10     │
                                  │  ┌──────────────────────┐   │
                                  │  │ Send ×10             │   │
                                  │  │ (1 ✓, 9 ◯) ▶ refresh│   │
                                  │  └──────────────────────┘   │
                                  └─────────────────────────────┘
```

**Verdict:** ✓ Handled. The promote-existing-Send-to-fan mechanic preserves the operator's first execution as variant #0 rather than re-running.

### 5.5 Pick one of 10 to continue (the stacked-response operation)

**Goal:** Operator ran 10 attempts; wants to continue the conversation from response #4.

**Actions:**
1. Click `⊞` on the Stack card to expand. 10 per-child SendNode cards appear in a tight horizontal row.
2. Operator clicks each card to read responses (right-side drawer shows the assistant text).
3. Operator clicks `🎯 Pick one` on card #4. Confirmation: "Promote #4 and freeze the other 9?".
4. (Under the revised model, no field changes: `FanNode.params.promotedChildSlotIndex=4` is set. Cards #1-3, #5-10 dim to ~40% opacity; card #4 stays full opacity with a highlight border. No layout shift.)
5. Card #4 now has a normal `+` edge-affordance below it; operator inserts a follow-up.

```
After Pick:
[Fan: axis=attempt, n=10]
  │
  ├──── [Stack: 9 frozen attempts] 🔒  (cannot be edited; preserved for history)
  │
  └──── [Send #4 → "X is best understood as..."]✓
              │
              + ← operator continues from here
```

**Verdict:** ✓ Handled. This is the cleanest UX for the "stacked response with selectable propagation" the user described.

### 5.6 Fan-through: follow-up that applies to all branches

**Goal:** "I want to send these 10 attempts, then ask 'what assumptions are you making?' to ALL of them."

**Actions:**
1. Operator has a Stack with 10 attempts in **Synced state** (`promotedChildSlotIndex = null`).
2. Operator clicks `+` at the bottom of the Stack card (the per-stack `+` affordance from §2.4).
3. Popover: "Add follow-up to all 10 branches". Operator picks "Follow-up user message".
4. A `UserTurn ×10 (synced)` card appears inside the Stack's bounding box, with one shared text editor.
5. Operator types "What assumptions are you making?" once. Each of the 10 underlying `UserTurnNode`s is created with `addedToStack=true` and identical `params.text`; the parent-walk peer rule (§3.2) groups them, and edits to the Stack card propagate to all 10.
6. A `Send ×10` card auto-inserts below. Operator clicks the Stack's `↻` ("Refresh children") to run.

```
[Fan: axis=attempt, n=10] (Synced — no promotion)
  ┌────────────────────────────────────────┐
  │ [Send ×10]  "10 ✓"                    │
  │      │                                  │
  │      ▼                                  │
  │ [UserTurn ×10 (synced)]                │
  │ "What assumptions are you making?"     │
  │      │                                  │
  │      ▼                                  │
  │ [Send ×10]  "10 ✓"                    │
  └────────────────────────────────────────┘
```

If the operator later **Picks** one (say #3), the visual changes but the structure does not: #3's path stays at full opacity, all other peers dim. New `+` clicks then add only under #3.

If the operator wants to **diverge** branch #3 from the synced UserTurn text without picking ("on this one, ask something different"):

7. Operator clicks `⊞` to expand the inner Stack, then clicks the per-child `+` (grey-on-card, distinguishable from the Stack's blue `+` per §2.4) on branch #3's UserTurn for a one-off edit — OR uses the "Unstack" affordance to disband the sync entirely.
8. Branch #3 becomes individually editable. Its `params.text` now differs from the other 9, so the §3.2 peer rule no longer groups it with them; the Stack visually decomposes at this layer. Branches 1, 2, 4-10 still match each other's `params` and remain rendered as a smaller Synced-Peers Stack with 9 peers. If the operator later restores #3's text to match the others, the Stack re-forms at full size (implicit re-stacking via params convergence).

**Verdict:** ✓ Handled. The `+`-on-Stack vs. `+`-on-child distinction is the same color/style rule used in §2.4.

### 5.7 Try 3 different converters on the same prompt

**Goal:** Sweep ROT13 / Base64 / NoOp.

**Actions:**
1. After typing the prompt (or selecting an existing UserTurnNode), click the `🔀` (wrap-in-fan) affordance on the node's rail.
2. Picker: "Fan axis: [prompt / converter / target / system_prompt / attempt]". Pick "converter".
3. Modal: "Variants" with an Add chip. Operator adds ROT13, Base64, NoOp.
4. Tree shape changes: UserTurnNode is wrapped in a `FanNode(axis='converter')` with 3 child UserTurnNodes, each carrying one converter in its pipeline. SendNodes under each.

**Verdict:** ✓ Handled.

### 5.8 Sweep across 3 targets

**Goal:** Same prompt, three models.

**Actions:** Same as §5.7 with axis = `target`. Each child is a SendNode (no UserTurn variant needed; the prompt is identical).

```
[RootPrompt: "Explain photosynthesis"]✓
       │
       ▼
[Fan: axis=target, variants=[gpt-4o, claude-3.5, llama-3]]
       │
       ▼ (3 branches)
   [Send→gpt-4o]✓   [Send→claude-3.5]✓   [Send→llama-3]✓
   AR_1             AR_2                 AR_3
```

Per §7.2 of primitives, 3 ARs because target changes. Per §9.2 of primitives, this is no longer a special case under AR-per-leaf.

**Verdict:** ✓ Handled. The Fan card displays "spawns 3 AttackResults" hint.

### 5.9 Edit upstream: visual propagation

**Goal:** Operator changes the root prompt and wants to see what becomes stale.

**Actions:**
1. Operator clicks the root `RootPromptNode`'s `✏` button, edits text, blurs.
2. Root state: `clean → edited`.
3. **All descendants** transition `clean → stale`. Visually: their cards get a yellow border + a small `↻` overlay icon. Edge animation: a faint pulse travels down each edge for 400 ms to draw the eye.
4. The canvas-level ribbon shows "1 edited, 14 stale" with a `Refresh tree` button.

**Verdict:** ✓ Handled. The visual pulse is a "show, don't tell" cue that propagation happened.

### 5.10 Refresh subtree

**Goal:** Operator only wants to re-run one branch, not the whole tree. In git terms: rebase a subtree onto its updated upstream.

**Actions:**
1. Right-click on the branch's root node → context menu → "Refresh subtree" (or shift-click the node's `↻`).
2. Runner walks down with `maxParallel=4` (per-Workspace; §12.2 of primitives). Each affected node animates `stale/edited → running → clean/failed`.
3. Previous executions per node move into reflog (§6.6 of primitives), evicting oldest if over the configurable cap (default `REFLOG_CAP_PER_NODE = 50`); eviction surfaces a ribbon marker per §2.3.

**Verdict:** ✓ Handled.

### 5.11 Branch from a node - the "this prompt didn't work, let me try another angle" motion

**Goal:** Operator is mid-conversation. The most recent prompt didn't land well — they want to **edit that prompt and re-run** to see a different outcome, while **preserving the original run** so they can compare or come back.

**Actions (V1.0 — minimal Workspace swap variant):**
1. Operator clicks the `📋` icon on the UserTurn whose text they want to rewrite. Tooltip reads "Branch from here" (because the node is not the root).
2. **The canvas swaps to a new ConversationTree** (V1.0; V1.1 opens a new tab — see §13.1 vs §13.3 of primitives). The source tree's id is pushed onto `recentTreeIds` and a toast appears: *"Branched from <node>. Source tree saved to History (use Switch tree or History → Open as tree to return)."*
3. The new tree contains a deep copy of the root-to-this-node path **plus this node's descendants**. Siblings of any node on the path are not carried over. All cloned nodes initially share `ExecutionRecord` refs with the source — no token cost, no backend calls.
4. The cloned UserTurn is focused with its text editor open. Operator edits the text and presses Enter. The edited node goes `edited`; its descendants go `stale`. Runner kicks off a wave on the cloned subtree under the new tree's fresh `conversation_tree_id`. The original tree is **never touched** (its backend ARs are untouched; only this canvas swapped away from it).
5. Operator can return to the source via:
   - **Switch tree** button in the canvas-level ribbon (§2.3) — picks from `recentTreeIds`.
   - **History tab → Open as tree** (the §9.4.1 reload-reconstruction path; restores the source with all completed leaves).
   - **Second browser tab** for true side-by-side comparison (the §9.4.3 `BroadcastChannel` advisory lock keeps the two tabs from racing the runner).

**Actions (V1.1 — full tab strip):** identical except step 2 opens a new tab in the strip instead of swapping; the operator flips between source and clone via tabs without going through "Switch tree" or History.

```
Original tree:                          New tree (after edit + refresh):
R --- A                                  R' --- X' (edited)
   \- X --- B                                    \- B' (refreshed, new AR)
         \- C                                    \- C' (refreshed, new AR)
```

**The whole-tree case ("I want both attempt #4 AND attempt #7"):** click `📋` on the root node. Tooltip reads "Clone tree" instead of "Branch from here" because the source slice is the entire tree. Mechanically identical — it's `branchToNewTree(root)`. V1.0: clone swaps the canvas, operator flips via Switch tree / second browser tab; V1.1: both trees show in the tab strip, the operator sets a different `promotedChildSlotIndex` in each.

**Verdict:** ✓ Handled. One affordance (`📋`), one primitive (`branchFromNode`), two contextual labels. The user's "edit this prompt and propagate to see the outcome — but the old one stays immutable" motion is the design intent. V1.0 ships the data-model and primitive; V1.1 ships the tab-strip ergonomics.

### 5.12 Open a historical attack (auto-reverse)

**Goal:** Operator opens a 12-turn attack from the History tab.

**Actions:**
1. From History tab, click "Open as tree" on an AttackResult row. The frontend calls [01 §13.1 `openTreeFromAttackResult(attackResultId)`](01_tree_primitives.md#131-v10-minimal-workspace).
2. Per §9.3 of primitives, the runner walks the conversation's messages and synthesizes tree nodes:
   - 12 `UserTurn`+`Send` pairs in a linear chain (V1.0).
   - **(V1.1)** If multiple leaf ARs share a `conversation_tree_id` and converge at a common lineage root via `original_prompt_id` (per §9.3.1 of primitives — the O(1) hash-bucket group-by; `wave_id` disambiguates fan members vs. separate explorations), an implicit `FanNode(axis='prompt')` is inserted at the divergence point.
3. Tree renders. Synthesized nodes get a "reconstructed" badge (V1.0); reconstructed fans additionally get a "reconstructed from history" badge (V1.1).

**`conversation_tree_id` id-minting (V1.0).** `openTreeFromAttackResult` inspects the source AR's `labels.conversation_tree_id`:
- **V1.0+ AR** (label present): delegates to `openTree(treeId)`; URL fragment reflects the existing id; reload-reconstruction follows the standard §9.4.1 path.
- **Pre-V1.0 AR** (label absent): frontend mints a fresh `ConversationTreeId` via `crypto.randomUUID()` and stores `ConversationTree.parentSourceConversationId = ar.conversation_id` (also mirrored to sessionStorage at `pyrit.workspace.parentSourceConversationId.<minted>`). URL fragment immediately reflects the new tree id. **Until the first Refresh fires, no backend write has happened** — the minted id is operator-local. Reload of an unrefreshed minted tree uses the §9.4.1 pre-V1.0 fallback path: labels-query returns no rows, sessionStorage lookup returns the legacy `conversation_id`, hydration falls through to `GET /api/attacks?conversation_id=Y`. The first Refresh fires `create_attack + N add_message` with the minted id in `labels.conversation_tree_id`; the resulting per-leaf AR rows in History are the first persisted references to the new tree, and the legacy AR keeps its own `conversation_id` (no label rewrite — see [03 §12 Q.H.1](03_runner.md#12-open-questions) for the label-inheritance choice).

```
After auto-reverse of a 12-turn linear AR:
[ImportMessage: AR_xxx]✓
  │
  ▼
[UserTurn #1]✓ (reconstructed)
  │
  ▼
[Send #1]✓ → AR_xxx (this AR)
  │
  ▼
... 11 more pairs ...
```

The operator can now edit any node and refresh — re-execution spawns new ARs under a fresh `conversation_tree_id`.

**Verdict:** ✓ Handled. The "reconstructed" badges set expectations that the conversation tree structure is inferred, not authored.

### 5.13 Operator-locked branch

**Goal:** Operator opens a colleague's attack.

**Actions:**
1. Open in tree view (5.12).
2. Per §9.1 of primitives, every reconstructed node from someone else's AR renders with a 🔒 badge.
3. All mutating affordances (`✏`, `↻`, `+`, `🗑`, `🔀`) are disabled and grey, with tooltips: "Owned by alice — snapshot to continue".
4. Only `📋 Snapshot` and `🔍 Open in linear view` are enabled.

**Verdict:** ✓ Handled — but only the visual lock; per §9.1 the runner must also catch the backend 400 if the operator somehow bypasses the visual guard (e.g., via keyboard shortcut).

### 5.14 Partial failure mid-refresh

**Goal:** Operator clicks "Refresh tree", 3 of 15 leaves fail (rate limit / target down).

**Actions:**
1. Subtree refresh starts. Nodes go `●` in waves.
2. As completions come back: 12 transition to `✓`, 3 transition to `⚠ failed`. The [03 §5.3](03_runner.md#53-cascade-on-failure) in-flight cascade drops any sibling leaves sharing a failed ancestor from `ready` and marks them `⦾ blocked` (distinct from `⚠ failed` — a blocked leaf never dispatched).
3. The 12 are `clean`; the 3's descendants (if any) remain `stale` because they have no input.
4. Top-of-canvas toast: "Refresh complete: 12 succeeded, 3 failed, 0 rate-limited, 0 blocked, 0 cancelled. [Retry failed]". The [Retry failed] button captures wave-W's failed-leaf ids + blocked-leaf ids at this completion event and calls [`runner.retryFailedNodes(treeId, nodeIds)`](../../../doc/gui/design/03_runner.md#21-entry-points-the-public-api) on click — scoped to wave-W's victims, not the whole tree. Rate-limited leaves are excluded from `nodeIds` (operator must wait + click Refresh tree manually). When *all* failures are rate-limited, [Retry failed] is disabled with tooltip *"N leaves were rate-limited. Wait for the target's rate-limit window to clear, then click Refresh tree to retry."*
5. Failed nodes show a small `⚠` chip with hover-tooltip showing the error message.

**Verdict:** ✓ Handled per §6.4 of primitives.

### 5.15 Drill into linear view

**Goal:** Operator wants to read a full conversation in the familiar chat UI for one leaf.

**Actions:**
1. Click `🔍` on a leaf SendNode (or just click the node and use the keyboard shortcut `L`).
2. Right pane slides in showing the existing `MessageList` + `ChatInputArea` ([ChatWindow.tsx](../../../frontend/src/components/Chat/ChatWindow.tsx)) loaded with the leaf's `AttackResult` and conversation.
3. The tree view in the left pane stays interactive — the operator can switch to other leaves and the right pane follows.
4. Sending a message in the linear view's input box: under the hood, this is a new `UserTurnNode + SendNode` child appended to the leaf in the tree. The tree updates immediately.

**Verdict:** ✓ Handled. The "follow-up animation" between graph and linear views from §10.2 of primitives is the polish item.

### 5.16 Delete a branch

**Goal:** "I don't need this experimental branch anymore."

**Actions:**
1. Operator clicks `🗑` on the subtree's root.
2. Confirmation: "Delete 7 tree nodes? Their 4 AttackResults will remain in History (filter by conversation_tree_id to find them)."
3. Operator confirms. The subtree disappears from the canvas.
4. Backend state untouched (append-only).

**Verdict:** ✓ Handled. The confirmation language tells the operator exactly what is and isn't deleted.

### 5.17 Edit an early node in a large tree — see what the refresh produced

**Goal:** Operator has a 60-leaf tree (per Appendix A in primitives). They edit the root prompt and want to understand the resulting refresh wave digestibly. This is the §10 walkthrough in scenario form.

**Actions:**
1. Operator clicks the root `RootPromptNode`'s `✏` button, edits text, blurs. Root → `edited`; 60 descendants → `stale`. Yellow borders propagate. Canvas-top ribbon reads "1 edited, 60 stale".
2. Operator clicks the ribbon's "Refresh tree" button.
3. **Preview banner** has already shown: *"Refresh 60 leaves? Estimated 60 target calls. [Refresh] [Cancel]"*. Since 60 > the default `confirmThresholdCount = 20`, a **confirmation modal** intercepts the click before any backend call goes out (§8.1). Operator confirms.
4. Operator confirms. Runner stamps a fresh `waveId = abc123` and walks the tree with `maxParallel=4` (per-Workspace; §12.2 of primitives). Affected nodes pulse `stale → running → clean`. (Failed nodes pulse `running → failed`.)
5. **Wave completion toast** lands at the bottom-right: "*Wave complete: 57 ✓, 3 ⚠. [View wave]*".
6. Operator clicks "View wave". The right-side drawer opens to the "Recent waves" tab with `abc123` selected; the canvas dims everything except the nodes touched by this wave; the drawer shows:
   - Trigger: `RootPromptNode` (with "Jump to node" link)
   - 60 leaves affected: 57 succeeded, 3 failed, 0 cancelled
   - Per-leaf list with status + 80-char output preview
   - "Compare to previous wave" button (V2; greyed in V1)

**Verdict:** ✓ Handled in V1 by the toast + drawer panel. Tree-local diff view is V2.

### 5.18 Browse refresh waves across the whole workspace

**Goal:** Operator has three worktrees open and wants to see what's been happening across all of them in the last hour. This is the cross-tree wave story.

**Actions:**
1. Operator switches to the existing **History** tab (sidebar, alongside `'tree'`, `'chat'`, `'config'`).
2. The History tab's existing filter chips (operator, operation, attack type, outcome) gain a new chip: **"Group by wave"** (toggle).
3. Operator toggles it on. AR rows collapse into wave-group rows. Each wave-group row shows: `wave_id` short suffix · timestamp · trigger ConversationTree/node · "60 ARs (57 ✓, 3 ⚠)" · expand chevron.
4. Operator expands the most recent wave. The 60 ARs are listed underneath, each clickable for its individual conversation.
5. Operator clicks "Open in tree". The originating ConversationTree opens (or focuses, if already open) in the `'tree'` tab with the wave-filter pre-applied (matches scenario §5.17 step 6 from the History side).

**Verdict:** ✓ Handled in V1.x once the History tab gains the `wave_id` group toggle (~30 LOC). The History tab already accepts the `?label=wave_id:X` filter via its existing labels filter ([HistoryFilters.tsx](../../../frontend/src/components/History/historyFilters.ts) — exact reference resolved at implementation).

---

## 6. Affordances → Primitives Delta

Two small additions to `01_tree_primitives.md` are needed to make the Stack and the Promoted state work cleanly. Everything else in this doc is pure UX over the existing primitives.

### 6.1 `addedToStack` on `ConversationTreeNodeBase` (V1.1)

> **Version scope: V1.1 only.** Revision 8 reserved `addedToStack` on the V1.0 type "so V1.1 doesn't need a schema migration." **Revision 9 drops the V1.0 reservation** — the field has zero V1.0 readers or writers, so its presence on the V1.0 type is dead code and a "what is this?" tax on every V1.0 reader.
>
> **V1.0 → V1.1 migration: TypeScript-structural extension with explicit `false` default at the read site.** The V1.1 PR adds `addedToStack: boolean` to `ConversationTreeNodeBase`. The V1.1 reader code paths (Synced-Peers Stack detection in §3.2, Stack-`+` gating in §3.4a, the §6.1 peer-detection rule) read `node.addedToStack ?? false` rather than `node.addedToStack` directly — TypeScript treats absent fields as `undefined` at the type level (since the field is required after the V1.1 schema change, but V1.0-created nodes loaded from sessionStorage won't have it). The `?? false` is the entire migration cost: no schema-rewrite script, no version field, no migration timestamp. V1.0 nodes correctly read as "not operator-stacked" (which is true — V1.0 had no Stack-`+` to set them).
>
> The V1.0 PR set does NOT include this field; the V1.1 PR set adds it as a non-breaking type extension.

The V1.1 type:

```ts
export interface ConversationTreeNodeBase {
  // ... existing fields ...

  /**
   * True iff this node was created as part of a Stack-`+` operation that added
   * N>=2 synchronized peers at once (the §5.6 fan-through case). Default
   * false. Set at creation; never auto-flipped. Carried across `branchFromNode`
   * clones via deep-copy.
   *
   * Stack peer-detection is DERIVED (no stored grouping UUID). See §3.2:
   * two nodes are Synced-Peers Stack peers iff
   *   (a) both have addedToStack=true,
   *   (b) walking up their parent chains they reach the same nearest FanNode
   *       ancestor at the same depth below it,
   *   (c) their params are deeply equal (divergence is implicit, no flag).
   *
   * Stack-`+` on a Fan card is gated (§3.4a): once any synced-peer layer
   * exists under a Fan, the Fan's Stack-`+` disables and the chain extends
   * via the new Synced-Peers Stack's own Stack-`+`. This guarantees one
   * synced-peer set per fan layer.
   *
   * In Promoted state (per §3.3), Stack-`+` adds N symmetric peers (not a
   * singleton): the promoted peer's child is non-draft, the N-1 others are
   * draft placeholders. Unpick activates the placeholders so the Stack
   * becomes a real Synced-Peers Stack across all N.
   *
   * Fan-axis children NEVER get addedToStack=true. They are visually grouped
   * by the separate Fan-Children Stack render rule (§3.1).
   */
  addedToStack: boolean
}
```

**Why it must live in the conversation tree model and not just in render state:**

- It persists across edits and reloads (V2): the field records *how the node was created*, which is durable provenance.
- The runner reads it when servicing `refreshSubtree` to optionally bundle synced peers into one wave.
- `branchFromNode` deep-copies it; clones preserve which nodes were operator-stacked and which were fan-children.

**Why we dropped `syncGroupId`** (the revision 6 design): the only source of "synced peers" is operator-driven Stack-`+`; everything else is structural. A stored grouping UUID added a field operators never see, required cloning gymnastics, and obscured the fact that divergence is just "params differ" — derivable, not stored.

### 6.2 `promotedChildSlotIndex` on `FanNode.params`

```ts
export interface FanNode extends ConversationTreeNodeBase {
  kind: 'fan'
  params: {
    // ... existing fields (axis, variants, mode) ...

    /**
     * Optional: the slotIndex of one child to mark as "promoted" (the git
     * cherry-pick analogue, §3.5). UI renders the promoted child at full
     * opacity + highlight border; other children dim to ~40% opacity
     * ("frozen" — not deleted, not editable, no new synced children).
     * Set by the "Pick" affordance; cleared by "Unpick". Promotion is per-
     * FanNode and does not cascade through nested fans (each FanNode owns
     * its own promotion state). Null = all children synced (default).
     */
    promotedChildSlotIndex: number | null

    /**
     * Tombstone list — slotIndices that have been deleted. Per [01 §5.1
     * invariant 2](01_tree_primitives.md#51-invariants), deleted children's
     * indices do not get reused. Makes the invariant runtime-checkable.
     */
    deletedSlotIndices: number[]
  }
}
```

**Promotion is purely a UI/editing concern.** The runner ignores `promotedChildSlotIndex` and always refreshes every stale descendant. Operators who want "only refresh the promoted path" use a per-call option (`refreshSubtree(id, { promotedOnly: true })`), not this field.

### 6.3 Suggested update to §8.2 of primitives

Already applied in revision 4: §8.2 now recommends **Buchheim-Walker via `d3-hierarchy.tree()`** + main-path pinning + adaptive stack collapse. Bundle delta: +10 KB for `d3-hierarchy`. Code delta: ~80 LOC for main-path pinning, replacing the ~50 LOC of naïve DFS.

### 6.4 Suggested update to §6.5 of primitives (Branch from node)

Applied in revision 7: §6.5 of primitives defined a single primitive `branchFromNode(nodeId)`. **Revision 14 split it into two explicit functions** — `branchToNewTree(nodeId)` (V1.0/V1.1 always-new-tree variant) and `branchToSubtree(nodeId)` (V1.1 sibling-subtree variant) — forcing call sites to be explicit about landing mode. The split is per reviewer guidance: the two operations differ in return type, version-scope, and downstream invariants; a single-function-with-flag would hide silent call-site bugs. UI labels still disambiguate: "Clone tree" on root, "Branch from here" otherwise (both invoke `branchToNewTree`); the V1.1 `🌿` icon invokes `branchToSubtree`. V1.0 ships the V1.0 surface; V1.1 adds `branchToSubtree` non-breakingly.

### 6.5 Git mental model

The git-vocabulary table lives in [01_tree_primitives.md §6.8](01_tree_primitives.md#68-git-mental-model-for-operator-vocabulary). Primary UI button labels in this doc use the friendly verbs that match the API surface (`Refresh node` / `Refresh subtree` / `Refresh tree`). Git terminology surfaces only for execution-history concepts that have no equally-concise English equivalent: `Reflog` (`Past runs` tab), `Cherry-pick` (Stack picks), `Clone Tree`, `Checkout this run`, `Make current`. The data model keeps its existing names (`conversation_tree_id`, `ExecutionRecord`, `executionHistory`, `refreshSubtree`).

---

## 7. Decisions and Open Questions

### Version-scope summary (this round)

The revision-7 decisions below are unchanged; revision 8 layers V1.0/V1.1 scope on top per the [01 §1 V1.0 exclusions](01_tree_primitives.md#v10-explicit-exclusions-deferred-to-v11). The decisions are about *whether* and *how*; the version markers are about *when*. None of the V1.1 exclusions changes any decision below — V1.1 ships them as the decisions specify, just later than V1.0.

### Resolved (this round)

**A.1 — Snapshot `conversation_tree_id` policy → Fresh `conversation_tree_id` with `parent_conversation_tree_id` back-link.** When the operator clones a tree (snapshot-at-root) or snapshots a subtree, the new conversation tree nodes are tagged with a fresh `conversation_tree_id` and an additional `parent_conversation_tree_id` label pointing at the source. Consequences:

- History filter by `conversation_tree_id` shows only ARs born under that tree (cleanly separated views per workspace).
- History filter by `parent_conversation_tree_id = T` shows all clones derived from `T` (the "where did I fork this from" navigation).
- Two clones can be browsed side-by-side without contaminating either's history view.
- The git framing in §3.5 is faithful: each tree is its own branch with its own ref history; the parent pointer is the equivalent of `branch.<name>.merge` configuration.

This replaces revision 3's "same conversation_tree_id" idea (which would have made the History tab confusing as soon as the operator started cloning).

**A.2 — "Pick one" cost → Orphan from conversation tree only; no new labels.** Picking a Stack member does not introduce any backend-visible distinction between the picked and frozen members — they all stay queryable in History under the same `conversation_tree_id`. The operator's UI surfaces the choice (highlight + dim), and that's the entire story. **Pursuing multiple "picked" responses in parallel uses `branchToNewTree(treeRoot)` (§5.11), not a multi-promoted primitive.** Promotion stays single-valued per FanNode; branching is the answer when the operator wants "but I also want to see what attempt #7 leads to".

This honors the user's "just modifying the linking, not copying the commits" intuition: a cloned tree initially references all the same `ExecutionRecord`s as the original — the divergence happens at edit/re-run time, not at clone time.

**A.3 — Onboarding overlay → Not pursued.** Per the user: no. The `+` chip behavior is discoverable through hover and is consistent with whiteboard/canvas tools the target operator population already uses (Miro, FigJam, Linear's workflows). Skip the overlay.

**A.4 - Stack `+` vs. per-child `+` ambiguity → Promotion state + one-per-fan-layer gating disambiguates.** When the Stack is in **Synced** state, the Stack `+` (filled blue, at the Stack's bottom edge) is the only `+` visible and unambiguously means "add a synced peer set at this depth". When a member is **Promoted**, the Stack `+` stays put and now adds N symmetric peers but only the promoted one is non-draft (§3.3). Per-child `+` chips on expanded Stack rendering remain grey-on-card to distinguish from the blue Stack `+`. Per Q.3.4 (revision 7), the **fan's** Stack-`+` disables once a synced-peer layer exists under it (§3.4a) - the chain extends downward from the new Synced-Peers Stack's own `+`, not from the fan. This collapses the previous three-affordance model into one Stack `+` whose meaning is read from the visual context (which member is highlighted) and whose presence is gated to one per fan layer, eliminating the "two batches merge" surprise.

**A.5 — Mobile / narrow viewport → Out of scope for V1; long-term whiteboard vision noted in §9.** Per the user: do not worry about this now. The aspirational direction is a navigable canvas (Miro-style pan/zoom, free node positioning, multi-tree workspace). React-flow already supports the canvas mechanics; the whiteboard polish is a follow-up doc.

### Resolved this round

**A.6 — Worktree data model.** Adopted formally in [01_tree_primitives.md §13](01_tree_primitives.md#13-workspace-and-worktrees---the-data-model). Workspace = `{ conversationTrees: ConversationTree[]; activeConversationTreeId }`; tab strip in the 'tree' view; `branchFromNode` (§6.5) creates a new ConversationTree tab. Rejected: per-node `frozen` flag (branching is the answer), full conversation tree version log (V2+).

**Q.7.B — Reflog browsing → in-place ⟲ badge + drawer tab (both).** Per the user's revision-5 input: surface the reflog as a visible icon on the node *and* in the drawer. Spec:
- **On the node card:** a small `⟲ N` badge appears in the node's footer when `executionHistory.length > 0`. Clicking opens an in-place popover listing past runs (timestamp + truncated output preview). Clicking a past-run row in the popover enters detached state (see Q.7.C).
- **In the drawer:** the right-side drawer (§2.3) gains a "Past runs" tab next to "Current" and "Compare". Same content as the popover but with full output rendering, scoring details, and an explicit "Make current" affordance per row.
- The in-place badge keeps the reflog discoverable without forcing a drawer open. The drawer is for deeper inspection and the "Make current" destructive op.

**Q.7.C — Detached HEAD safety → (a) silently re-tip with a toast.** Per the user's `⟲` suggestion, the visual entry point is the same icon used for Q.7.B. Spec:
1. Operator clicks the `⟲ N` badge → popover lists past runs (newest first).
2. Operator clicks a past run → node enters **detached** rendering: dotted border, small "Detached" pill, a "Make current" button visible in the drawer's reflog tab.
3. While detached, the displayed `execution` is the past run (read-only inspection). The node's actual `execution` field is unchanged.
4. If operator clicks `↻` (Refresh) while detached:
   - Default: silently creates a new tip (new `ExecutionRecord` from the current resolved input), exits detached state, surfaces a toast "*Created new run #N. The detached past run is still in this node's reflog.*"
   - Operator's prior detached selection is preserved in the reflog (it never left).
   - This is git's `checkout -b new && commit` semantics, packaged as one click, with the safety net that nothing becomes unreachable.
5. To make the detached selection the current execution destructively, operator clicks "Make current" (the `git reset --hard` analogue). Confirmation modal: "*This will replace the current run. The previous run will move into the reflog.*"

The toast on auto-re-tip is the key affordance — it makes the safety semantics visible without modal interruption. Operators learn the model from the toast text after one or two encounters.

### Remaining open questions

**Q.7.A — "Rebase" / "Refresh" terminology — DECIDED V1.0: friendly-first.** Primary UI button labels read `Refresh node` / `Refresh subtree` / `Refresh tree`, matching the API surface (`refreshNode` / `refreshSubtree` / `refreshTree`). Git terminology survives for execution-history concepts with no equally-concise English equivalent: `Reflog` (`Past runs` tab title), `Cherry-pick` (Stack picks), `Detached HEAD` (past-run inspection state), `Make current` (promotion from reflog), `Clone tree` / `Branch from here` (branching). The *rebase concept* remains the mental model explained in [01 §6.8](01_tree_primitives.md#68-git-mental-model-for-operator-vocabulary) — what Refresh does to stale descendants — but is not a button label.

**V1.x follow-up (deferred):** the originally-brainstormed right-click "Rebase subtree" alias on the per-node context menu is deferred. Operators who want the git surface get it through the conceptual section, the reflog/cherry-pick tab titles, and tooltip text on the Refresh buttons that names the git equivalent. The choice is reversible: a single `terminology.ts` module mapping operation IDs to (primary label, alias label, tooltip text) tuples can A/B-test git-first labels post-launch if operator feedback warrants. Originally V1 PR scope per the brainstorm below; reduced to V1.x to keep V1.0's primary-label surface uniform.

**Brainstorm (preserved for historical context; verdict in bold):**

| Operation | **Friendly-first (DECIDED V1.0)** | "Git first" (rejected) | Mixed (rejected) |
|---|---|---|---|
| `refreshSubtree` (button label) | **`Refresh subtree`** | `Rebase subtree` | Default to context: "Refresh" on a fresh subtree, "Rebase" when descendants are stale |
| `refreshSubtree` (right-click alias) | (V1.x: optional `Rebase subtree` alias) | — | — |
| `executionHistory` browsing | **`Past runs (N)`** | `Reflog (N)` | `Past runs (Reflog)` — both terms in the tab title |
| Stack `Pick` (button) | **`Pick this run`** (V1.x: alias `Cherry-pick`) | `Cherry-pick this run` | `Pick (cherry-pick)` |
| Detached state | **`Viewing past run`** | `Detached HEAD` | `Viewing past run (detached)` |
| `branchToNewTree(root)` | **`Clone tree`** | `git checkout -b` / `git worktree add` | Always opens a new tree |
| `branchToNewTree(non-root)` | **`Branch from here`** | `git branch <commit>` | Always opens a new tree |

*Author lean: **friendly-first labels in the primary UI; git verbs surface in three places only** — (1) right-click aliases on the same action (V1.x), (2) the tab title for past runs ("Past runs (Reflog)" so the term is teachable), (3) tooltips on the friendly buttons that name the git equivalent for users who already know the model.* This gives discoverability without overwhelming operators who don't think in git. The choice is reversible: a single i18n table flip switches between modes, so we can A/B test post-launch.

**Followup PR scope** when the V1.x right-click aliases get picked up: a small `terminology.ts` module mapping operation IDs to (primary label, alias label, tooltip text) tuples. Every UI surface reads from it. Switching modes globally then becomes one line.

**Q.7.D — "Discard from history" affordance (V1.x roadmap).** Exploration-heavy workflows produce a lot of history rows: a 200-leaf tree where the operator finds 5 interesting and discards 195 leaves still leaves 195 ARs in History with no operator-facing way to mark them as exploration noise. The §15.1 audit posture requires we **keep** the backend rows (never hard-delete), but a soft "Discard from History default view" affordance would let operators clean up the History tab's default scrollback.

*Lean (V1.x):* add a `labels.discarded_from_history: "true"` AR label, settable from the tree-view's `🗑 Delete` confirmation modal ("Also hide N AttackResults from default History view? They remain queryable via Show discarded toggle."). The History tab's default filter excludes `discarded_from_history=true`; a "Show discarded" toggle lifts the filter. No backend changes; one extra label.

*Why V1.x and not V1.0:* not blocking V1.0 release (operators can ignore discarded rows for the first month), and the affordance design wants to be informed by real History-tab usage patterns after the tree-UI ships.

---

## 8. Reviewing Refresh Waves

When the operator refreshes a 60-leaf tree, they get 60 new ExecutionRecords across many leaves. Without grouping these become an unsorted soup of UUIDs. This section is the UX side of [01_tree_primitives.md §14 (Refresh Waves)](01_tree_primitives.md#14-refresh-waves---grouping-per-node-executions-into-a-user-intent-unit), which adds the `waveId` to the data model. With one shared `waveId` per refresh call, three layered views become tractable.

### 8.1 The V1 chain: preview banner → confirm modal → toast → drawer panel

Four lightweight UX surfaces, ordered by when the operator encounters them:

**Before the refresh — preview banner.** The propagation pulse from §5.9 already makes "X nodes will be affected" visible. The canvas-top ribbon adds an explicit numeric line and a "Refresh tree" button. The preview reads: *"1 edited, 60 stale · estimated 60 target calls · [Refresh tree]"*. The estimate is the count of `Send` nodes in the edited+stale set times the max attempts each could trigger — accurate enough for a sanity check.

**Before the refresh — confirmation modal (count-based threshold).** When the operator clicks `[Refresh tree]` and the estimated call count exceeds `confirmThresholdCount` (default **20**, configurable in workspace settings), a modal intercepts the click:

```
┌────────────────────────────────────────────┐
│ Refresh 60 leaves?                               │
│                                                  │
│ This will send 60 calls to gpt-4o                │
│ (threshold: 20 calls per refresh)                │
│                                                  │
│ [ ] Don't ask again this session                 │
│                                                  │
│           [Cancel]  [Refresh →]                  │
└──────────────────────────────────────────────┘
```

If the refresh spans multiple targets (cross-target `FanNode` per §9.2), the modal breaks down the count per target: *"40 calls to gpt-4o + 20 calls to claude-3.5-sonnet"*. The "Don't ask again this session" checkbox suppresses the modal until the operator reloads or until a 2× safety floor (the modal always fires for >`2 × confirmThresholdCount` even with the checkbox set).

Waves below the threshold skip the modal entirely — small refreshes stay one-click.

**During the refresh — in-canvas progress.** Per §5.14, affected nodes animate `stale → running → clean/failed`. The ribbon shows `[ ●●●●●●○○○○ ] 6/60 (3 ✓, 0 ⚠, 1 ●)` so the operator can see progress without watching every node.

**After the refresh — wave completion toast.** Bottom-right toast: *"Wave complete: 57 ✓, 3 ⚠, 0 cancelled. [View wave] [Dismiss]"*. The toast auto-dismisses after 8 seconds; the "View wave" link remains accessible via the Recent waves drawer tab (§8.2).

This four-step chain is the minimum-viable answer to "what just happened." It costs ~200 LOC: the ribbon counter, the confirmation modal, the toast component, and the wave-state tracking. No new views.

**Roadmap: cost-based threshold (V1.x).** V1 ships with a **count-based** threshold only. The same modal scaffold can later carry a per-target `estimatedCostPerCallUSD` field (operator-typed at target-create time) and a `confirmThresholdUSD` cap that triggers the modal independently of the call count. Surfaced as *"Estimated cost: ~$3.20 (cap: $1.00)"* in the modal body. Out of V1 scope to keep the first PR small; revisit when operators ask for it or after the first credit-card-blowing refresh reported in the wild.

### 8.1a Detached HEAD on a `failed` node (V1.0)

A `failed` node has `node.execution = null` per [01 §6.4.1](01_tree_primitives.md#641-why-nodeexecution--null-on-failure-not-preserved) but its `executionHistory` may still contain prior successful runs. The reflog badge (§8.2's `➺ N` per-node footer) still shows; clicking it lets the operator inspect those prior runs. The detached state on a failed node renders specially:

- **Dotted border** (same as detached on a clean node) plus **a red error chip** showing `node.lastError` (per [03 §2.2 sink](03_runner.md#22-state-update-plumbing)).
- **The "Make current" button is enabled** even though current `execution` is null — the `makeCurrent` step-0 guard in [01 §6.7](01_tree_primitives.md#67-makecurrent---destructive-promotion-from-the-reflog) handles the null source. Promoting transitions the node from `failed` to `clean` and clears `lastError`. **Operator surface:** the modal reads *"Promote this past run to current? The node will transition from failed to clean; the most recent failure detail (`{node.lastError}`) will be discarded. Descendants will become stale and need a rebase."*
- **No "silent re-tip" affordance** — the §8.1 / Q.7.C re-tip path requires a current execution to displace into the reflog. For a `failed` node, the equivalent is just `refreshNode(id)` (rebase the node), which fires a normal dispatch. The detached panel surfaces a `[Rebase node]` button next to `[Make current]` for the operator who wants "try again with current params" rather than "go back to this past attempt."
- **Reflog-empty failed node:** the badge does not appear (no past runs to detach to). The drawer's "Past runs" tab shows *"No past runs. Use Rebase to retry."*

### 8.2 The V1 drawer: a "Recent waves" tab

The right-side drawer (already present, hosting the per-node "Past runs" tab per Q.7.B) gains a sibling tab: **"Recent waves"** (ConversationTree-scoped). The tab is sorted newest-first and shows:

```
Recent waves (this ConversationTree)
────────────────────────────────────────────
⟲ abc123        2 min ago
  Trigger: RootPrompt (edit)
  60 leaves: 57 ✓ · 3 ⚠ · 0 cancelled
  [Highlight in canvas] [Open compare] (V2)

⟲ def456        1 hour ago
  Trigger: UserTurn #2 (subtree)
  15 leaves: 15 ✓
  [Highlight] [Open compare]

⟲ ghi789        2 hours ago
  Trigger: refreshTree
  30 leaves: 28 ✓ · 2 ⚠
  ...
```

**"Highlight in canvas"** dims all nodes *not* touched by the wave, keeping only affected nodes at full opacity. The operator can click any highlighted node to see its individual reflog entry from this wave. Clicking "Highlight" a second time (or pressing Esc) restores the normal view.

**"Open compare"** is V2 (see §8.5).

Implementation cost: ~80 LOC of UI on top of the existing drawer. The data is already there once `waveId` is stamped.

### 8.3 The V1.x cross-tree view: History tab gains "Group by wave"

The existing History tab in the sidebar ([AttackHistory.tsx](../../../frontend/src/components/History/AttackHistory.tsx)) already lists `AttackResult`s with filter chips for operator, operation, attack type, outcome, and converters. The `wave_id` label is just another label — the History tab's existing labels-filter machinery picks it up for free.

Two additions:

1. **A new filter chip "Wave"** alongside the existing ones. Picks up `labels.wave_id` values seen in the user's recent ARs (the backend's `/labels` endpoint already returns these). Selecting a wave filters the AR list down.
2. **A "Group by wave" toggle** in the filter bar. When on, AR rows collapse into wave-group rows showing `wave_id` short suffix, timestamp, trigger ConversationTree/node ID, aggregate outcome counts, and an expand chevron. Operators see "the last 5 waves across all my worktrees" rather than "the last 300 individual ARs."

Wave rows include an "Open in tree" button that opens (or focuses) the originating ConversationTree in the `'tree'` tab with the wave's highlight pre-applied (per §8.2).

This is **the cross-tree answer**: don't build a new view; teach the History tab one new grouping. Operators already know History.

### 8.4 What "digestible" actually means at scale

The user's question framed digestibility around "redo an early message in a large tree." The numbers that matter:

| Workspace size | Wave-affected leaves | UI treatment |
|---|---|---|
| 1 wave, 1-3 leaves | 1-3 | Inline highlight + toast. No drawer panel needed unless the operator opens it. |
| 1 wave, 4-30 leaves | 4-30 | Toast + Recent waves panel default-opens on completion |
| 1 wave, 31-200 leaves | 31-200 | Toast + Recent waves panel + offer "Highlight in canvas" automatically; recommend "Compare to previous wave" (V2) once available |
| 1 wave, >200 leaves | >200 | Soft cap from §9.4 of primitives already triggers an "explicit override" prompt; the wave UX inherits the cap |
| N waves across M conversation trees, recent | All sizes | History tab "Group by wave" surfaces them at workspace level |
| N waves across M conversation trees, historical | All sizes | History tab filter by `conversation_tree_id` + date range; wave grouping still applies |

The key UX principle: **the operator never sees raw ExecutionRecords as a flat list**. The minimum aggregation is the wave; the workspace aggregation is the History tab.

### 8.5 V2: tree-local diff view (per-wave compare)

For the heaviest "what actually changed" question — "the model said X before my edit; now it says Y; was the difference what I hoped for?" — V2 introduces a **compare mode** on the canvas.

Operator clicks "Compare to previous wave" in §8.2. The canvas re-renders each node card as a vertical split: previous wave's response on the left, current wave's response on the right. Stable nodes (unchanged across waves) collapse to a single read-only card. Failed nodes show the failure side-by-side with the prior success. Operators can click any card to expand to a full diff in the drawer.

Compare mode is non-destructive — it's a different view of the same data, toggleable. V2 because it requires diff rendering primitives and careful UX for multi-modal content (images, audio, video).

### 8.6 V2: workspace timeline (swimlanes per ConversationTree, waves as stripes)

When the operator wants a bird's-eye view of all activity across all worktrees, V2 introduces a **Workspace Timeline** view. Each ConversationTree is a horizontal swimlane; the time axis runs left-to-right; each wave renders as a colored stripe spanning the lane positions of its affected leaves. Color encodes wave outcome (green = all ✓, yellow = mixed, red = mostly ⚠).

The timeline doubles as a workspace-wide undo/redo affordance — clicking an old wave on a lane opens that ConversationTree with the wave selected. Server-side conversation tree persistence (§11 of primitives) is a prerequisite because workspace-spanning state has to survive a reload.

This is V2 territory specifically because the data model (waveId + conversation_tree_id + workspace) is V1, but the cross-lane visualization is the kind of thing where polish matters and we want to ship the simpler History-tab grouping first to learn what operators actually need.

---

## 9. Long-term vision: navigable whiteboard canvas

The user's revision-4 Q.A.5 named the aspirational direction: a navigable canvas like a whiteboard or other flow chart editor. The revision-5 worktree adoption ([01_tree_primitives.md §13](01_tree_primitives.md#13-workspace-and-worktrees---the-data-model)) **already promotes multi-tree workspaces from aspirational to V1**. The remaining items in this section are V1.x and beyond.

**What V1 already supports** (via react-flow's built-ins + revision-5 worktrees):
- Infinite canvas with pan (drag) + zoom (scroll/pinch).
- Minimap (§2.3) with viewport rectangle.
- Fit-to-view (`F` keyboard shortcut).
- Multi-select (lasso) and group operations.
- **Multi-tree workspaces** — each ConversationTree is its own tab in the 'tree' view (per [01 §13](01_tree_primitives.md#13-workspace-and-worktrees---the-data-model)). Clone Tree opens a new tab; closing a tab drops it from React state; History → "Open as tree" creates one. Each ConversationTree has its own viewport and selection state, persisted in the Workspace's React state for the session.

**What "feels like a whiteboard" adds beyond V1:**

- **Operator-positioned nodes.** Pure layout algorithms are great until the operator wants to manually reorganize. A "free-positioning" mode where Buchheim-Walker becomes a starting hint (operator can drag nodes to override) is the natural next step. *V1.x; complexity is in re-running layout when topology changes without trampling manual positions.*
- **Multi-ConversationTree canvas merge.** Today each ConversationTree is its own tab (separate canvas). A "show all conversation trees on one canvas" view (Miro-style) for cross-tree comparison would be useful for retrospectives. Display-only; no data-model change. *V1.x.*
- **Sticky notes and grouping rectangles.** "I want to annotate this subtree as 'jailbreak attempts' and that one as 'baseline'". Pure visual; no data-model change. *V2.*
- **Connector overlays (non-tree).** Visual arrows that operators draw to indicate "this came from that observation", outside the conversation tree. Annotation only; not execution-relevant. *V2.*
- **Multi-operator presence cursors.** Once V2 server-side conversation trees land (§11 of primitives), real-time collaborative editing with operator cursors becomes feasible. *V2.x.*
- **Snapshot to image/SVG.** Export the canvas as a static image for sharing in incident reports or post-mortems. *V1.x; trivial with react-flow's built-in viewport-to-image.*
- **Cross-ConversationTree rebase** ("apply this prompt change to all my experiments"). *V2.1+; requires preview UX to avoid surprising the operator with mass changes.*

None of these change the V1 conversation tree primitives. They are pure UI/UX layered on top of the existing `ConversationTreeNode` + `ConversationTreeEdge` + `conversation_tree_id` + `Workspace` model. The whiteboard direction is compatible with everything in this doc.

---

## 10. What This Doc Does Not Cover

- **Visual style** (colors, typography, spacing): a follow-up.
- **Onboarding / first-run experience**: a follow-up.
- **Telemetry events** to instrument operator behavior: a follow-up.
- **Keyboard-only operation specification beyond §8.4 of primitives**: a follow-up, blocked on the visual style decision (focus rings depend on the theme).

---

## Summary Table

Version column reflects the V1.0 cut decisions from this round (see [01 §1 V1.0 exclusions](01_tree_primitives.md#v10-explicit-exclusions-deferred-to-v11)). Rows marked V1.1 have a documented V1.0 fallback in §5's scenario→version map.

| User intent | UI primitive | Git verb | ConversationTree-level operation | Version |
|---|---|---|---|---|
| Send a prompt | RootPrompt + auto-Send | (initial commit) | `addNode(root_prompt); refreshNode(send)` | V1.0 |
| Continue conversation | Edge `+` → "Follow-up" | (new commit on branch) | `insertChild(user_turn); refresh` | V1.0 |
| Re-roll response | Node `↻` | (new commit; old in reflog) | `refreshNode(send)`; old execution → reflog | V1.0 |
| Try N times | Node `↻×N` | (fan-out branches) | `wrapInFan(axis='attempt')` | V1.0 |
| Pick one of N | Stack `🎯` | cherry-pick | Set `FanNode.params.promotedChildSlotIndex` | V1.0 (visual dim only; draft-placeholder dance is V1.1) |
| Unpick (back to synced) | Stack right-click → Unpick | (revert cherry-pick) | Clear `promotedChildSlotIndex` | V1.0 |
| Follow-up to all peers | Stack `+` (Synced state, once per fan layer per §3.4a) | (commit on each branch) | Add synced child to each peer | **V1.1** |
| Follow-up to picked only | Stack `+` (Promoted state) | (commit on selected branch) | Add N symmetric peers; promoted is non-draft, N-1 are draft placeholders (§3.3) | **V1.1** |
| Try N converters | Node `🔀` → axis=converter | (fan-out branches) | `wrapInFan(axis='converter')` | V1.0 |
| Try N targets | Node `🔀` → axis=target | (fan-out branches; new ARs) | `wrapInFan(axis='target')` | **V1.1** |
| Edit upstream | Node `✏` | (amend or new commit) | `editParams`; descendants → stale | V1.0 |
| Rebase subtree | Right-click → Refresh / shift-`↻` | rebase | `refreshSubtree(id)` | V1.0 |
| Branch from node | Node `📋` | `git branch <commit>` | `branchToNewTree(id)` (V1.0: swaps active tree; V1.1: new tab in strip) | V1.0 (always-new-tree swap variant; tab strip is V1.1) |
| Branch as subtree | Node `🌿` | `git branch <commit>` (in-canvas) | `branchToSubtree(id)` landing as sibling subtree in same canvas | **V1.1** (V1.0 disabled stub) |
| Clone whole tree | Root `📋` | `git checkout -b new` | `branchToNewTree(root.id)` (degenerate case; same function) | V1.0 (same swap semantics as branch from node) |
| View past run | Node card → reflog drawer → click run | `git checkout <commit>` (detached) | Display-only; node enters detached state | V1.0 |
| Make past run current | Past run → "Make current" | `git reset --hard <commit>` | Swap `execution` with reflog entry | V1.0 |
| Open historical | History → "Open as tree" | (browse a branch) | Auto-reverse (§9.3 of primitives) | V1.0 (linear+converter; fanout detection V1.1) |
| Read linear | Node `🔍` | (log of one branch) | Switch right pane to linear view | V1.0 |
| Delete branch | Node `🗑` | (delete branch ref) | Remove tree nodes; backend ARs preserved | V1.0 |
| Review a refresh | Toast → "View wave" / drawer "Recent waves" tab | (read `git log <wave>`) | Filter ExecutionRecords by `waveId` (§8.1, §8.2) | V1.0 |
| Cross-ConversationTree wave search | History tab → "Group by wave" toggle (V1.x) | (`git log --all`) | SQL group by `labels.wave_id` over all ARs (§8.3) | V1.x (depends on Workspace + History extension) |
| Compare current to previous wave | Drawer "Compare" tab (V2) | (`git diff <wave-1> <wave>`) | Per-node diff over last two `waveId`s (§8.5) | V2 |

# Tree UI V1.0 Shipability Action Plan

Status: implementation-guide checkpoint after live browser review (revision 2) — confirmed deliverables: Chat-to-tree opens the full active attack as a merged tree; adding a prompt auto-creates its pending response without exposing Send vocabulary; V1.0 hides future-only dead-end controls; fans can be pruned to a picked path without deleting backend history; Tree View has a resizable tree-left/path-chat-right split; converters are visible transform nodes; Playwright is the MVP acceptance harness.
Scope: finish the V1.0 operator experience without expanding into V1.1 architecture. This session's output is a descriptive implementation guide: each confirmed decision lands with current-state evidence, implementation notes, acceptance criteria, and alternatives considered.

## Exit Criteria

A reviewer can use only the browser, with `VITE_ENABLE_TREE_UI=true`, and complete the full Tree UI happy path plus recovery flows without reading code:

1. Open an existing attack as a tree from Chat or History; attacks with multiple conversations reconstruct as one merged tree, not as a single selected conversation.
2. Inspect the selected path as a chat-style transcript beside the tree canvas, with a resizable split that keeps tree context visible.
3. Add a follow-up prompt after a response and immediately see the linked pending response that Refresh will produce.
4. Create attempt fans and visible converter-transform branches with understandable controls.
5. Edit prompts/converters, see stale propagation, and refresh successfully or get a clear preflight reason why refresh is blocked.
6. Recover from mistakes via delete confirmation, fan prune-to-picked-path, branch clone, dirty-edit guard, and visible past-run state.
7. Use only visible controls that either work in V1.0 or explain a current preflight blocker; no normal MVP path exposes future-only disabled buttons.
8. Reload the page and get an honest restored tree or an explicit degraded-state explanation.
9. Navigate the canvas with keyboard, mouse, minimap, controls, and sane auto-layout behavior.
10. Pass the mocked Playwright MVP acceptance suite, with screenshots and layout assertions for the critical Tree UI states.

## P0: Must Fix Before V1.0 Sign-Off

### 1. Chat-To-Tree Full-Attack Reconstruction

Problem:
Tree View is currently reachable from History, but not from the loaded Chat attack. More importantly, current open-as-tree reconstruction is single-conversation shaped: live browser validation of a History row with 30 messages and 3 conversations produced a 12-node linear tree with 0 fan nodes and 0 stack summaries. That does not meet the operator goal of using Tree View to understand the whole attack graph from the place they are already working.

Deliverables:
- Add an `Open tree` action to the Chat ribbon when `attackResultId` is present. Place it next to the conversations-panel toggle so it reads as another view of the active attack, not as a per-message branch action.
- Route the Chat action through the same dirty-edit guarded tree swap used by History open-as-tree.
- Replace or extend the current single-AR `useAutoReverse(openFromAttackResultId)` path with an attack-level reconstruction mode:
  - fetch the `AttackSummary`,
  - fetch `getConversations(attackResultId)`,
  - fetch messages for every active conversation returned by that list,
  - build one `ConversationTree` by merging identical prefixes and branching at the first divergent turn,
  - preserve target registry name and operator/operation labels on the reconstructed root/tree metadata.
- Keep History `Open as tree`, but make it use the same full-attack reconstruction path. A History row's `Convs > 1` must not silently reconstruct only `AttackSummary.conversation_id`.
- If prefix merging cannot confidently infer a shared branch point, reconstruct the main conversation and show an explicit degraded-state banner naming how many related conversations were omitted.

Implementation notes:
- Chat entry wiring lives in `frontend/src/App.tsx` and `frontend/src/components/Chat/ChatWindow.tsx`. Add a prop such as `onOpenAttackAsTree?: (attackResultId: string) => void` to `ChatWindow` and have App reuse the existing `handleOpenAttackAsTree` dirty-swap path.
- Reconstruction lifecycle currently lives in `frontend/src/components/Tree/useAutoReverse.ts`, which fetches only `getAttack` and `getMessages(ar.conversation_id)`. Introduce a second hook or mode whose dependency surface also includes `getConversations`.
- Pure merge logic belongs beside `linearChainFromMessages` in `frontend/src/runner/autoReverse.ts`, not inside React components. Keep it unit-testable with arrays of conversation message lists.
- The merge key should be conservative: role + converted text/value + converter identifiers + attachment identity where available. When keys differ, branch. When required identity is missing, degrade explicitly rather than guessing.
- Preserve the URL fragment behavior: V1.0+ trees with `labels.conversation_tree_id` keep that id; pre-tree attacks get a fresh client tree id.

Acceptance:
- From Chat, a loaded attack with one conversation opens Tree View to the same content currently available from History open-as-tree.
- From Chat, a loaded attack with multiple conversations opens one tree containing all active conversations under the attack.
- From History, a row with `Convs > 1` uses the same full-attack reconstruction and does not silently drop related conversations.
- Shared prefixes appear once; divergent turns become branches/fans with stable slot ordering.
- The empty Tree View state points operators to both Chat-loaded attacks and History, not only History.
- Browser tests cover Chat open-as-tree for one-conversation and multi-conversation attacks; History open-as-tree for a multi-conversation attack; and degraded-state copy when merge inference is not possible.

Alternatives considered:
Keeping History-only open-as-tree was rejected because it forces operators out of the Chat context where they notice the need for a tree. Adding a Chat button that reuses the current single-conversation auto-reverse was rejected because it looks convenient while silently dropping related conversations. Replacing Chat's conversation panel with Tree View was rejected as too disruptive for V1.0 and contrary to the non-goal of replacing the linear chat experience.

### 2. Auto-Create Response Placeholder / No Send Vocabulary

Problem:
The live Tree UI exposes the internal runner shape too directly. A follow-up action on an assistant response currently creates only `User turn (edited) -> New prompt`, leaving a dangling prompt with no visible response target. At the same time, fully coalescing prompt and response into one rendered card would make the important `prompt -> converter -> response` workflow harder to represent honestly. Operators should see a prompt-response path, but they should not need to understand a separate `SendNode` concept.

Deliverables:
- Keep the internal `UserTurnNode -> SendNode` data/runner model for V1.0.
- Change `Add follow-up prompt` so it creates both nodes in one structural edit:
  - `Assistant response -> User prompt (edited) -> Assistant response placeholder (stale/dirty)`.
- Render the child `SendNode` as a response-state card, never as `Send`:
  - clean with response text: `Assistant response`,
  - stale/edited/draft without response text: `Pending response` or `Response pending refresh`,
  - failed with prior response text: show the previous response separately from the latest error.
- Keep the prompt-to-response edge as the insertion point for converter transforms. `Append converter` inserts a visible converter transform node between the prompt and response placeholder; converter-node behavior is specified in §10.
- Preserve the direct prompt-to-response path as the no-converter baseline unless the operator explicitly replaces it.
- Remove or avoid user-facing `Send` vocabulary in buttons, menus, labels, tests, and empty states. Implementation names can remain `SendNode` in TypeScript.

Implementation notes:
- Structural behavior belongs in `frontend/src/runner/treeStateReducer.ts`. Add a helper such as `applyAppendPromptWithResponse(parentResponseId, uuid)` or extend `applyAppendChild` for the follow-up path so both nodes are created atomically.
- `frontend/src/components/Tree/SendCard.tsx` should choose user-facing labels from response state. The current `kindLabel = node.state === 'draft' || node.state === 'edited' ? 'Send' : 'Assistant response'` should become response vocabulary for every state.
- `frontend/src/components/Tree/InsertEdge.tsx` should keep the prompt-to-response edge menu focused on legal V1.0 actions: append converter transform, compare converters, and response refresh path. Avoid future disabled entries in the normal MVP menu.
- Converter transform nodes must stay non-side-effecting with respect to the target: they prepare converter IDs / preview output; downstream response nodes remain the target-call refresh points.
- Dirty-edit detection should count the newly created prompt/response pair as one operator edit for modal copy, even if two internal nodes were added.

Acceptance:
- Clicking `Add follow-up prompt` on an assistant response creates an edited prompt and a visible pending response placeholder in one action.
- The pending response placeholder is the obvious refresh target and participates in cost preview/stale propagation.
- No visible card, tooltip, menu item, or empty-state copy says `Send`.
- The prompt-to-response edge offers `Append converter`; choosing a converter inserts a visible transform node and keeps the downstream response placeholder linked.
- Browser tests cover add-follow-up creating the paired pending response, converter insertion on the prompt-to-response edge, and absence of visible `Send` vocabulary in the normal Tree UI.

Alternatives considered:
Keeping the current explicit response/send behavior was rejected because it leaves a dangling prompt after the dominant follow-up action. Coalescing prompt and response into one rendered card was rejected after considering `prompt -> converter -> response`: the converter belongs between prompt and response, and hiding that relationship inside a combined card makes the edge affordance dishonest. Hiding converter state as only prompt-card chips was rejected after converter research because converters carry parameters, supported input/output data types, previews, possible LLM cost, and direct-vs-converted branch semantics that operators need to see.

### 3. Hide Future-Only Dead-End Controls

Problem:
Live browser review found disabled future affordances in normal operator paths: `Branch as subtree (coming in a future release)` appears on action rails, and `Fan out: prompt (coming later)` appears in the user-turn edge menu. These controls are intentionally unavailable, but their presence makes the MVP feel unfinished and forces operators to learn which visible controls to ignore.

Deliverables:
- Hide future-only controls from the normal V1.0 operator UI.
- Remove `Branch as subtree` from the action rail unless a dev/review flag explicitly enables future-slot preview.
- Remove disabled future fan axes from edge insert menus in normal V1.0. The menu should show only actions that can be completed now.
- Keep disabled states only for current, actionable preflight blockers, such as no target selected, operator tag missing, no converter selected, wave already running, or insufficient permissions.
- Replace any visible `coming later`, `future release`, or equivalent tooltip copy with either no control or a current-state explanation.
- If future-slot preview is useful for implementers, gate it behind a clearly named development flag and exclude it from browser tests that represent the operator MVP.

Implementation notes:
- `frontend/src/components/Tree/actionRail.tsx` currently renders a disabled `Branch as subtree` button unconditionally. Gate or remove it for normal V1.0.
- `frontend/src/components/Tree/InsertEdge.tsx` currently includes disabled V1.1 fan-axis rows. Filter those options out unless future-slot preview is enabled.
- Keep layout stable through CSS spacing and responsive constraints rather than reserving dead controls.
- Search UI code and tests for strings like `coming later`, `future release`, `Available in a future release`, and `V1.1` before sign-off; normal operator snapshots should not contain them.

Acceptance:
- In normal V1.0 mode, action rails expose only implemented actions or actions blocked by a current preflight condition.
- Edge insert menus expose only implemented V1.0 actions.
- No visible tooltip/menu/button copy says `coming later`, `future release`, or equivalent on the normal Tree UI path.
- Browser tests cover the response action rail and user-turn edge menu and assert future-only controls are absent.
- Any optional future-slot preview is gated behind a dev/review flag and is not enabled for MVP validation.

Alternatives considered:
Keeping disabled future slots visible was rejected because slot reservation is less important than operator confidence in a first MVP. Improving tooltip copy was rejected because the core problem is the visible dead end, not the exact explanation. Showing future controls only in a development/review mode remains acceptable because it keeps implementation inspection possible without leaking unfinished affordances to operators.

### 4. Prune Fan To Picked Path

Problem:
The current UI has two different fan-removal-adjacent behaviors, neither of which matches the operator goal. `Collapse to stack` is visual only; it hides repeated fan children but keeps the fan. `Delete` removes the selected node and all descendants, which is too destructive when the operator has compared variants and wants to keep the useful path. Operators need a way to finish a fan comparison by removing the fan wrapper and extra variants from the client tree while preserving one selected path/subtree.

Deliverables:
- Add a fan-level action named `Prune to picked path`.
- If `FanNode.params.promotedChildSlotIndex` is set, pruning keeps that slot.
- If no child is picked, clicking the action opens a small chooser listing variants by slot, state, and response/prompt preview.
- Show a confirmation modal before pruning:
  - identify the kept slot,
  - state how many other variants/subtrees will be removed from this tree,
  - state that backend `AttackResult` history is not deleted.
- Rewire the client tree so `parent -> fan -> pickedChild` becomes `parent -> pickedChild`.
- Preserve the picked child node, execution, execution history, descendants, and edge slot semantics where relevant.
- Remove the fan node and non-picked variant subtrees from client tree state. Do not delete backend attacks, messages, or history entries.

Implementation notes:
- Pure structural logic belongs in `frontend/src/runner/treeStateReducer.ts`, e.g. `applyPruneFanToPickedPath(tree, fanNodeId, slotIndex)`.
- The reducer should locate the incoming edge to the fan, the outgoing edge for the kept slot, and every descendant of non-kept children. It should remove the fan plus non-kept descendants, update the kept child `parentId` to the fan's former parent, and replace the incoming/outgoing edges with one parent-to-kept-child edge using the fan's former incoming `slotIndex`.
- If the kept child is itself a response placeholder or has descendants, preserve that entire subtree unchanged except for the kept child's new `parentId`.
- Add a host callback in `TreeRunnerHost` and fan-specific action in `FanCard`. The action should sit near Pick/Collapse controls, not in the generic Delete path.
- Undo should treat prune as one structural operation if the existing undo stack is wired for structural edits; if not, document undo as a follow-up rather than silently half-supporting it.

Acceptance:
- Operator can create a 3-attempt fan, pick one attempt, prune to that picked path, and see the fan card disappear while the picked response/subtree remains.
- If no attempt is picked, the prune action asks the operator which slot to keep before showing the confirmation.
- Pruning removes only client tree nodes for non-picked variants; backend History still contains prior `AttackResult`s.
- Cost preview and refresh behavior still work on the preserved path after pruning.
- Browser tests cover pick-then-prune, chooser-then-prune, cancel confirmation, and preservation of the picked subtree.

Alternatives considered:
Keeping only visual stack collapse was rejected because it does not clean up the tree after comparison. Deleting the whole fan subtree was rejected because it destroys useful work and makes fan experimentation feel unsafe. Promoting all fan children by removing only the wrapper was rejected for V1.0 because it can create multiple siblings in a place where the parent expected one path, making the canvas harder to reason about. Per-variant delete remains useful as a later complement, but it does not replace the dominant "keep the winner" workflow.

### 5. Resizable Tree / Path Chat Split View

Problem:
The current `Open in linear view` action opens an in-tree details drawer with a `Path` section. That is useful metadata inspection, but it creates a weaker second linear surface rather than giving operators the normal text-message reading experience while they reason about branches. Jumping back to the existing Chat tab would preserve a canonical chat surface, but it drops tree context. For MVP, Tree View should combine both: tree structure and a readable chat transcript for the selected path.

Deliverables:
- Keep the existing Chat tab unchanged for V1.0.
- Change Tree View into a two-pane workspace:
  - left pane: tree canvas,
  - right pane: chat-style transcript for the selected root-to-node path.
- Make the tree/chat split resizable with a visible drag handle and keyboard-accessible resize controls.
- Persist the split width for the current browser session or workspace settings, with sane min/max widths so neither pane becomes unusable.
- Selecting a node in the tree updates the path chat to that root-to-node path.
- Selecting a message/bubble in the path chat highlights and scrolls to the corresponding tree node.
- Pending response placeholders render as pending assistant bubbles in the path chat.
- Converter transform steps render as compact transform bubbles in the path chat and correspond to visible converter nodes in the tree.
- Keep structure-only actions on the tree canvas. Path-chat actions can include transcript-native conveniences, but they must call the same tree edit callbacks as the canvas.

Implementation notes:
- `TreeRunnerHost` should own the selected node/path state and pass it to both `TreeCanvas` and the new path-chat pane.
- Extract reusable message-bubble presentation from `frontend/src/components/Chat/MessageList.tsx` if practical; avoid importing Chat's attack/conversation orchestration into Tree View.
- Add a pure path projection helper near tree utilities: given `ConversationTree` + selected node id, return ordered transcript entries with node ids, roles, text, converter transform steps, execution/error state, and pending-response status.
- The right pane should not pretend to be the backend Chat tab. It is a selected-path transcript over the client tree, including unpersisted edits and pending response placeholders.
- Replace or demote `Open in linear view`; if a details drawer remains, label it as `Details` or `Inspect node`, not as a separate linear view.
- Use CSS grid/flex with explicit min widths and a drag handle. Avoid overlaying the path chat as a drawer on top of the tree, since the goal is simultaneous context.

Acceptance:
- Tree View opens with tree canvas on the left and selected-path chat transcript on the right.
- The split can be resized by pointer and keyboard, and both panes remain usable at supported desktop widths.
- Selecting tree nodes updates the transcript; selecting transcript bubbles highlights the corresponding tree node.
- Adding a follow-up prompt updates both panes and shows the pending assistant response in the path chat.
- Existing Chat tab behavior is unchanged.
- Browser tests cover default split rendering, resize behavior, tree-to-chat selection sync, chat-to-tree selection sync, pending response bubble rendering, and absence of the misleading `Open in linear view` label.

Alternatives considered:
Keeping the current details drawer was rejected because it makes the path transcript feel secondary and separate from the main workflow. Navigating to the existing Chat tab was rejected because it loses branch context at the moment the operator is reasoning about a tree. Replacing the existing Chat tab with Tree View was rejected as too disruptive for MVP. A fixed split was rejected because trees and transcripts vary widely in width; operators need to decide which pane gets space for the current task.

### 6. Converter Transform Nodes And Comparisons

Problem:
Converter research shows converters are not just labels or prompt-card chips. They are backend registry instances with type-specific parameters, supported input/output data types, preview behavior, optional LLM-backed cost, and persisted `converter_identifiers` on message pieces. Operators also need to compare direct and converted paths naturally, e.g. `prompt -> direct response` alongside `prompt -> converter(options) -> response`. Hiding converters inside a prompt card makes that branch structure and provenance too hard to see.

Deliverables:
- Add a visible `ConverterNode` (or equivalent named transform node) to the Tree UI model for V1.0.
- A converter node is a transform/configuration step, not a target-call node. Downstream response nodes remain the refresh/target-call points.
- `Append converter` on a prompt-to-response edge inserts `prompt -> converter -> response placeholder`.
- Preserve or offer an explicit direct baseline path: `prompt -> response placeholder` with no converter.
- `Compare converters` creates a converter comparison structure that can fan over converter pipelines/options while keeping the direct baseline visible when requested.
- Converter nodes show:
  - converter type and display name,
  - configured parameters,
  - pipeline order when multiple converters are chained,
  - supported input/output data types,
  - preview status/output when available,
  - LLM-based/cost warning when applicable.
- Tree refresh sends `converter_ids` to the backend. Preview output is inspection-only and must not become the sent message content unless the operator explicitly converts it into prompt text.
- Empty converter branches must be explicit states: `Choose converter`, `Configure converter`, or `No converter baseline`. They must not look like complete duplicate prompts.

Implementation notes:
- Reuse/extract the existing Chat converter panel behavior where practical: catalog lookup, parameter form, converter instance creation, preview, and `Use Converted Value` semantics. Do not duplicate converter-type introspection logic in Tree-only code.
- Store configured converter references as backend `converter_id`s whenever possible, matching `AddMessageRequest.converter_ids`. Inline converter refs from reload reconstruction can still render read-only or require re-registration before refresh if no backend id is available.
- The runner already resolves `UserTurnNode.params.converterPipeline` into `converter_ids`. Introducing visible converter nodes requires either:
  - a reducer/resolver pass that folds converter-node pipelines into the next downstream user turn before dispatch, or
  - a small runner extension where `resolvePathPartition` accumulates converter nodes between a prompt and response into the entry's converter pipeline.
- Converter nodes should validate data-type compatibility against the upstream piece(s) and the downstream target's supported modalities before refresh.
- LLM-backed converters and file/media-output converters require explicit preview/run actions; do not auto-preview them on every edit.
- Path-chat rendering should show converter nodes as compact transform bubbles between the user prompt and assistant response, with preview clearly labeled as preview.

Acceptance:
- Operator can create `prompt -> direct response` and `prompt -> converter -> response` sibling paths and see the difference without opening a drawer.
- Operator can configure converter type/params from the converter node and preview supported conversions.
- Refresh of a converted path sends converter IDs to the backend, not locally previewed converted text.
- Direct/no-converter baseline is visually distinct from unconfigured converter variants.
- Data-type incompatibility, missing required params, and LLM-backed preview cost are surfaced before refresh.
- Path-chat shows converter transform bubbles in the selected path.
- Browser tests cover append converter insertion, direct-vs-converted sibling paths, converter comparison variants, preview vs refresh behavior, and data-type/preflight warnings.

Alternatives considered:
Keeping converters as prompt/edge chips only was rejected because it hides meaningful branch structure and converter provenance. Treating converters as side-effecting response nodes was rejected because converters transform prompts and do not call the target. Keeping only `Fan(axis='converter')` without visible converter cards was rejected because it exposes fan mechanics while hiding transformation intent. Limiting MVP converter fan to pre-existing simple text-to-text converters was rejected as too narrow for Co-PyRIT's converter catalog, which includes modality-changing and LLM-backed converters.

### 7. Playwright MVP Acceptance Harness

Problem:
Live browser exploration found product mismatches that source review alone did not make visceral: History open-as-tree dropped related conversations, add-follow-up produced a dangling prompt, future-only disabled controls appeared in normal menus, and `Open in linear view` opened a secondary drawer rather than a chat-like surface. The MVP needs a repeatable browser harness that validates the operator experience, not only unit-level tree reducers.

Deliverables:
- Add or expand a Playwright suite such as `frontend/e2e/tree-mvp.spec.ts` for the Tree UI MVP acceptance path.
- Use mocked API routes for deterministic coverage of core workflows, including multi-conversation attacks, converter catalogs, converter previews, target metadata, missing-target responses, wave summaries, and reload reconstruction.
- Keep live backend/model smoke tests separate and optional. They can validate integration when credentials/targets exist, but they must not be the only MVP sign-off path.
- Capture screenshots for key states:
  - empty Tree View,
  - Chat open-as-tree entry,
  - merged multi-conversation tree,
  - pending response placeholder,
  - resizable tree/path-chat split,
  - converter transform branch with direct baseline,
  - attempt fan before and after prune-to-picked-path,
  - degraded reconstruction banner.
- Add visual/layout assertions in addition to text assertions:
  - no overlapping node cards or rails,
  - selected card action rail visible,
  - edge insert chips targetable,
  - split panes above minimum width,
  - text not clipped in critical buttons/chips,
  - no visible `Send`, `coming later`, or `future release` copy in normal V1.0 mode.

Implementation notes:
- Build on existing `frontend/e2e/tree.spec.ts` route-mocking style, but separate MVP acceptance flows from narrower regression tests if the file becomes too large.
- Prefer explicit fixtures for one-conversation, multi-conversation shared-prefix, multi-conversation degraded-merge, attempt fan, converter transform, and missing-target attacks.
- Use Playwright locators by role/test id for behavior assertions and screenshots for visual review. Avoid relying only on screenshots for pass/fail.
- Store screenshots as test artifacts rather than committing generated images unless the repo already uses checked-in baselines.
- Run the suite with `VITE_ENABLE_TREE_UI=true` and document the command in the guide or test README if it differs from existing e2e commands.

Acceptance:
- A reviewer can run one Playwright MVP suite and cover every P0 implementation surface in this guide.
- The suite fails if Chat open-as-tree is absent, multi-conversation reconstruction silently drops conversations, add-follow-up lacks a pending response, future-only controls appear, converter transforms are hidden, fan prune loses the picked path, or split panes become unusable.
- The suite emits screenshots/artifacts that make layout regressions reviewable without manual reproduction.
- Unit tests still cover reducer/resolver details; Playwright covers the operator-facing workflow.

Alternatives considered:
Ad hoc browser checks were rejected because they already found issues but are too easy to forget. Unit-only coverage was rejected because it misses layout, action rail, menu, and mental-model regressions. Requiring live backend/model tests for every sign-off was rejected as too slow and environment-dependent; live smoke remains useful but optional.

## Validation / Regression Gates

These items are already implemented or substantially implemented based on source and live-browser review. They remain V1.0 sign-off gates, but they are tracked as validation/regression coverage rather than fresh P0 implementation work.

### Gate A. Target Registry Recovery / Refresh Preflight

Current evidence:
Source review shows `AttackSummary.target.target_registry_name` is present in frontend DTOs, auto-reverse populates `RootPrompt.params.targetRegistryName`, and Tree refresh intercepts missing target before dispatch. Live browser review also showed recovered trees with a target value on the root card.

Validation:
- Historical Open-as-tree from an attack with a target reconstructs a refreshable root target.
- Historical Open-as-tree from an attack without recoverable target does not fail later with an empty-target 404; it tells the operator before dispatch.
- Missing-target UI uses an explicit root warning and modal/banner before any backend call.
- Unit + browser tests cover both recovered-target and missing-target cases.

Residual risk:
Historical data may still lack recoverable target registry names. That is acceptable only if the UI blocks refresh with a clear preflight explanation.

### Gate B. Attempt Fan Count Picker

Current evidence:
Live browser review showed `Fan out response attempts` opening an attempt-count dialog and creating a 3-attempt fan with stale response leaves. Source review shows validation for 2-50 attempts and e2e coverage for a 5-attempt fan.

Validation:
- Operator can create 2, 3, 5, and larger attempt fans within the allowed range.
- Created fan has correct variant count, child count, slot indices, and no duplicate slot ids.
- Cost preview reflects the created leaves.
- Browser tests cover creating a 5-attempt fan and rejecting invalid counts.

Residual risk:
Attempt fan cleanup is handled by the new `Prune to picked path` P0 item; this gate only validates creation.

### Gate C. Branch From Here True Subtree/Path Semantics

Current evidence:
Source review shows root `Clone tree` and non-root `Branch from here` are separate reducer paths. Non-root branching keeps the root-to-selected path plus selected descendants and excludes unrelated sibling branches.

Validation:
- Root action remains `Clone tree` and clones the whole tree.
- Branching from a middle node creates a new tree containing only the path and selected subtree.
- Sibling branches outside the selected subtree are absent.
- Included nodes preserve executions/history.
- Refresh labels include `parent_conversation_tree_id` for cloned/branched trees.
- Unit tests cover root clone vs non-root branch; browser tests cover branch from a non-root response.

Residual risk:
The UI label must stay honest after the split-view work. Non-root action should still read `Branch from here`; root action should still read `Clone tree`.

### Gate D. Long Response Inspection

Current evidence:
Tree cards already keep previews short and the current details drawer can inspect full node/path content. The newly accepted split workspace supersedes the drawer as the primary long-content surface: selected-path chat becomes the normal way to read full prompts/responses.

Validation:
- Canvas cards remain short and stable; long content does not expand layout unpredictably.
- Selected-path chat pane shows full prompt/response text without leaving Tree View.
- Latest error state is distinguishable from previous successful response preview.
- Execution id, AR id, conversation id, wave id, target, converter transforms, and copy affordances remain available either in path chat or a clearly labeled metadata/details surface.
- Browser tests cover long text in the path-chat pane and verify no card overlap/regression on the canvas.

Residual risk:
If the details drawer remains after split-view lands, it must be labeled as metadata/details, not as a separate linear chat experience.

## P1: Should Fix Before Wider Internal Rollout

### 8. Auto-Layout and Fit Behavior

Problem:
Long chains and expanded editors can push useful content offscreen. Fit View can make cards too small. Manual dragging helps, but layout needs a first-class reset/autolayout story.

Deliverables:
- Add explicit `Auto layout` / `Reset layout` control.
- Preserve manual positions during the current tree session.
- Reset manual positions on tree swap or operator command.
- Tune `fitViewOptions`, `minZoom`, and initial viewport so cards are readable.
- Consider `Fit current path` for long chains.
- Ensure layout accounts for expanded editor/detail states or gives enough vertical space.

Acceptance:
- Opening a long linear tree starts with readable cards, not microscopic cards.
- Operator can drag nodes, then restore deterministic layout.
- No node/card overlap after edit mode opens.
- Browser tests cover overlap checks at desktop and narrow viewport.

### 9. Action Rail Discoverability

Problem:
Hover/focus floating rail is visually cleaner but not always discoverable.

Deliverables:
- Keep the rail on hover/focus and selected card.
- Consider a persistent compact actions trigger per card.
- Ensure selected card always shows rail.
- Confirm touch/mobile behavior.
- Ensure rail never covers editor Save/Cancel controls or nearby cards.

Acceptance:
- Keyboard and mouse users can discover and operate actions without guessing.
- Rails do not overlap important content.
- Accessibility labels are unique enough to avoid duplicate-reader confusion.

### 10. Edge Handles and Insert Chips Polish

Problem:
Handles are now at the card perimeter, but edge/handle/chip alignment still needs visual review on larger and zoomed-out trees.

Deliverables:
- Align handles with edge paths visually at common zoom levels.
- Increase edge insert chip hit target if needed.
- Show insert chip on selected edge/path, not only hover, if discoverability is weak.
- Ensure chips do not flicker during node dragging.

Acceptance:
- Moving nodes does not make edges/chips visually flicker in a distracting way.
- Insert chip remains targetable at normal zoom.
- Edge handles do not appear inside card content.

### 11. Delete Confirmation Detail

Problem:
Delete now confirms, but the copy is generic.

Deliverables:
- Show deleted subtree count.
- Mention selected node kind.
- Optionally list first few affected node kinds.
- Consider special copy for deleting a fan branch vs a plain chain.

Acceptance:
- Operator understands the blast radius before deleting.
- Root delete remains unavailable.

### 12. Past Runs / Execution Detail

Problem:
PastRunsDrawer exists, but the integrated host/drawer experience has not had enough browser validation.

Deliverables:
- Wire selected-node detail/past-runs drawer if not already fully integrated.
- Show current execution and reflog entries.
- Pin/unpin works.
- Checkout remains disabled/deferred unless implemented honestly.
- UUID truncation with full title works.

Acceptance:
- Browser test covers a node with current execution and history.
- Pin/unpin persists in host state.

### 13. Wave Toast, Retry, Cancel Queued

Problem:
Wave status ribbon is usable; wave-complete toast/retry/queued flows need browser-level confidence.

Deliverables:
- Browser validate running, cancel, queued, cancel queued.
- Browser validate complete summary buckets.
- Browser validate retry failed behavior with mock failures.

Acceptance:
- Operators get immediate feedback after Refresh.
- Failed/rate-limited/permanent/blocked outcomes are understandable.

## P2: Nice-To-Have / V1.1 Candidates

### 14. Server-Side Target/Tree Metadata Improvements

- Persist `conversation_tree_node_id` only if/when V2 server-side tree persistence is accepted.
- Add richer target lineage metadata for historical reconstruction.
- Add server-side tree storage if client-only persistence becomes insufficient.

### 15. Synced-Peers / Stack Authoring

- Do not add synced-peer authoring in V1.0 unless operators explicitly ask.
- Keep fan-child stack visual aggregation only.

### 16. Advanced Graph Interactions

- Drag-and-drop from a node palette.
- Add node on edge drop.
- Keyboard shortcuts beyond basic editor save/cancel.
- Layout animation, if it helps comprehension and does not add flicker.

## Browser / E2E Coverage Plan

### Existing Coverage

- `frontend/e2e/tree.spec.ts` covers:
  - Tree View greenfield,
  - History Open-as-tree,
  - response preview reconstruction,
  - add follow-up prompt,
  - attempt fan creation,
  - converter fan creation.

### Add Next — Implementation Coverage

1. Playwright MVP acceptance suite that runs the P0 operator path with mocked APIs and emits screenshots/artifacts.
2. Chat open-as-tree for a one-conversation loaded attack.
3. Chat open-as-tree for a multi-conversation loaded attack, asserting all conversations appear in one tree.
4. History open-as-tree for a multi-conversation row, asserting related conversations are not silently dropped.
5. Degraded reconstruction banner when full-attack merge inference is not possible.
6. Add-follow-up creates an edited prompt plus visible pending response placeholder.
7. Prompt-to-response edge supports converter-node insertion and keeps the downstream response placeholder linked.
8. Normal Tree UI has no visible `Send` vocabulary.
9. Normal Tree UI hides future-only dead-end controls from action rails and edge menus.
10. Attempt fan prune-to-picked-path keeps the selected subtree and removes the fan wrapper/client-only variants.
11. Tree View renders a resizable tree-left/path-chat-right split with selection sync both directions.
12. Converter transform nodes: append converter, direct baseline, comparison variants, preview-vs-refresh behavior, and preflight warnings.

### Add Next — Regression Gate Coverage

1. Missing target preflight.
2. Attempt fan count picker, including invalid count rejection.
3. Branch from non-root excludes sibling branches.
4. Long-response inspection in the selected-path chat pane, with metadata details available without reintroducing a separate linear view.
5. Delete confirmation cancel/confirm.
6. Reload from `#conversation_tree_id` with fan labels.
7. Dark/light canvas chrome screenshot/assertions.
8. Narrow viewport layout/overlap checks.

## Recommended Next Work Order

1. Chat-to-tree full-attack reconstruction.
2. Auto-create response placeholder / no Send vocabulary.
3. Hide future-only dead-end controls.
4. Resizable tree-left/path-chat-right split view.
5. Prune fan to picked path.
6. Converter transform nodes and comparisons.
7. Playwright MVP acceptance harness.
8. Auto layout/reset layout.
9. Past runs / wave toast browser validation.
10. Delete confirmation details.

## Validation / Regression Work Order

1. Target registry recovery/preflight.
2. Attempt fan count picker.
3. Branch-from-here true subtree/path semantics.
4. Long-response inspection via selected-path chat.

## Non-Goals For This Checkpoint

- Server-side tree persistence.
- Synced-peer stack authoring.
- New fan axes beyond attempt/converter.
- Replacing the linear chat experience.
- Major redesign of the runner dispatch model.

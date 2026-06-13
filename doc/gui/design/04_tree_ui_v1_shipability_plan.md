# Tree UI V1.0 Shipability Action Plan

Status: actionable checkpoint plan after PR7 host-integration quality gate.
Scope: finish the V1.0 operator experience without expanding into V1.1 architecture.

## Exit Criteria

A reviewer can use only the browser, with `VITE_ENABLE_TREE_UI=true`, and complete the full Tree UI happy path plus recovery flows without reading code:

1. Open an existing attack as a tree from History.
2. Inspect prompt and assistant response content without losing context.
3. Add a follow-up prompt after a response.
4. Create attempt and converter fans with understandable controls.
5. Edit prompts/converters, see stale propagation, and refresh successfully or get a clear preflight reason why refresh is blocked.
6. Recover from mistakes via delete confirmation, branch/subtree clone, dirty-edit guard, and visible past-run state.
7. Reload the page and get an honest restored tree or an explicit degraded-state explanation.
8. Navigate the canvas with keyboard, mouse, minimap, controls, and sane auto-layout behavior.

## P0: Must Fix Before V1.0 Sign-Off

### 1. Target Registry Recovery / Refresh Preflight

Problem:
Historical Open-as-tree/reload reconstructs `RootPrompt.params.targetRegistryName` as empty because `AttackSummary.target` does not expose the backend registry key. Operators can see Refresh buttons, click them, and receive `Target instance '' not found` failures.

Deliverables:
- Backend/API: expose the attack's target registry name when available.
- Frontend type: add `target_registry_name` to the target/attack DTO mirror.
- Auto-reverse/reload: populate `RootPrompt.params.targetRegistryName` from that field.
- UI preflight: when target is blank, show an explicit `No target` warning chip on the root card.
- Refresh behavior: disable or intercept Refresh with a modal/banner explaining that a target must be set first.

Acceptance:
- Historical Open-as-tree from an attack with a target reconstructs a refreshable root target.
- Historical Open-as-tree from an attack without recoverable target does not fail later with an empty-target 404; it tells the operator before dispatch.
- Unit + browser tests cover both recovered-target and missing-target cases.

### 2. Attempt Fan Count Picker

Problem:
`Fan out response attempts` currently creates a fixed two-variant fan. The operator intent is usually "rerun this N times."

Deliverables:
- Clicking `Fan out response attempts` opens a small popover or dialog.
- Include a numeric input/stepper for attempt count.
- Provide sensible default, likely 3 or 5.
- Validate range, e.g. 2-50 for V1.0.
- Create N stale response leaves.
- Cost preview should reflect N leaves.

Acceptance:
- Operator can create 2, 3, 5, and larger attempt fans.
- Created fan has correct variant count, child count, slot indices, and no duplicate slot ids.
- Browser test covers creating a 5-attempt fan.

### 3. Branch From Here Must Not Clone Whole Tree

Problem:
Current `Branch from here`/`Clone tree` behavior clones the whole foreground tree. For non-root nodes this is misleading: "from here" should create a new tree rooted in the selected path/subtree, not duplicate unrelated branches.

Deliverables:
- Root action remains `Clone tree` and clones the whole tree.
- Non-root action becomes true `Branch from here`:
  - Include the root-to-selected-node path needed for context.
  - Include the selected node's descendant subtree.
  - Exclude unrelated sibling branches.
  - Preserve executions/history on included nodes.
  - Set `parentConversationTreeId` to the source tree id.
  - Consider `parentSourceConversationId` if source AR context is known.
- If the exact branch semantics are not implemented for V1.0, rename non-root action to avoid overpromising.

Acceptance:
- Branching from a middle node creates a new tree containing only the path and selected subtree.
- Sibling branches outside the selected subtree are absent.
- Refresh labels include `parent_conversation_tree_id` for cloned trees.
- Unit tests cover root clone vs non-root branch.
- Browser test covers branch from a non-root response.

### 4. Long Response Inspection

Problem:
Cards need truncation to keep the canvas usable, but operators need a comfortable way to read and compare full prompts/responses.

Deliverables:
- Keep card body preview short and stable.
- Add a response/detail drawer or upgrade the existing linear path drawer.
- Drawer should show:
  - full prompt/response text,
  - role/kind/state,
  - target/converter info,
  - execution id, AR id, conversation id, wave id when available,
  - latest error state separately from previous successful response preview.
- Add copy affordance where appropriate.

Acceptance:
- Long response cards do not expand canvas layout unpredictably.
- Operator can inspect full text without leaving Tree View.
- Failed refresh does not make it ambiguous whether the displayed response is previous/current.

### 5. Converter Fan UX Clarity

Problem:
`Fan out converters` and `Append converter` are conceptually close. The converter fan currently creates a branch with an empty converter choice, which can feel like a broken duplicate prompt.

Deliverables:
- Rename/clarify labels:
  - `Append converter to this turn` = mutate current user turn pipeline.
  - `Compare converters` or `Fan out by converter` = create sibling converter variants.
- When creating converter fan, auto-focus the new branch's converter picker or show `Choose converter` placeholder.
- Empty converter variants should not look complete.
- Add a clear way to add/remove converter variants.

Acceptance:
- Operator can create a converter fan and immediately assign converters without hunting.
- Empty converter branch has an explicit visual state.
- Browser test covers creating converter fan and assigning a converter.

## P1: Should Fix Before Wider Internal Rollout

### 6. Auto-Layout and Fit Behavior

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

### 7. Action Rail Discoverability

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

### 8. Edge Handles and Insert Chips Polish

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

### 9. Delete Confirmation Detail

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

### 10. Past Runs / Execution Detail

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

### 11. Wave Toast, Retry, Cancel Queued

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

### 12. Server-Side Target/Tree Metadata Improvements

- Persist `conversation_tree_node_id` only if/when V2 server-side tree persistence is accepted.
- Add richer target lineage metadata for historical reconstruction.
- Add server-side tree storage if client-only persistence becomes insufficient.

### 13. Synced-Peers / Stack Authoring

- Do not add synced-peer authoring in V1.0 unless operators explicitly ask.
- Keep fan-child stack visual aggregation only.

### 14. Advanced Graph Interactions

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

### Add Next

1. Missing target preflight.
2. Attempt fan count picker.
3. Converter fan assignment flow.
4. Branch from non-root excludes sibling branches.
5. Delete confirmation cancel/confirm.
6. Open-linear/detail drawer for long response.
7. Reload from `#conversation_tree_id` with fan labels.
8. Dark/light canvas chrome screenshot/assertions.
9. Narrow viewport layout/overlap checks.

## Recommended Next Work Order

1. Target registry recovery/preflight.
2. Attempt fan count picker.
3. Branch-from-here true subtree/path semantics.
4. Long-response detail drawer.
5. Converter fan UX clarity.
6. Auto layout/reset layout.
7. Past runs / wave toast browser validation.
8. Delete confirmation details.

## Non-Goals For This Checkpoint

- Server-side tree persistence.
- Synced-peer stack authoring.
- New fan axes beyond attempt/converter.
- Replacing the linear chat experience.
- Major redesign of the runner dispatch model.

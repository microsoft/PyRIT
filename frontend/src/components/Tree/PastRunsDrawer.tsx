// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Per-node "Past runs" drawer tab per spec §2.3. Pure presentational —
 * the host owns the selected-node state and supplies the node's
 * current `execution` + `executionHistory[]`.
 *
 * Per spec §6.6: `executionHistory` is the reflog. Entries are
 * push-down LIFO; pinned entries survive cap-eviction. The drawer
 * renders the current execution first (marked `data-current="true"`),
 * then the history in caller-supplied order (host owns ordering so
 * pinned-first / wave-grouped variants can land in V1.x without a
 * component change).
 */

import { Button } from '@fluentui/react-components'

import type {
  ConversationTreeNodeId,
  ExecutionRecord,
  ReflogEntry,
} from '../../runner/treeTypes'
import { usePastRunsDrawerStyles } from './PastRunsDrawer.styles'

export interface PastRunsDrawerProps {
  /** Which node this drawer is showing. Used for callback context only — the
   *  host already knows which node it's wiring; no display use. */
  nodeId: ConversationTreeNodeId
  execution: ExecutionRecord | null
  executionHistory: ReadonlyArray<ReflogEntry>
  /**
   * Toggle pinned state for a past-run entry. The runner sink's
   * `setReflogPinned` is the wire; host passes `(executionId,
   * !currentlyPinned)`.
   */
  onTogglePin?: (executionId: string, pinned: boolean) => void
  /**
   * Swap a past run back into the current execution slot. PR6e ships
   * the contract; the host's `makeCurrent` plumbing (or runner sink
   * extension) lands in V1.x per spec §6.7.
   */
  onCheckout?: (executionId: string) => void
}

export function PastRunsDrawer({
  nodeId: _nodeId,
  execution,
  executionHistory,
  onTogglePin,
  onCheckout,
}: PastRunsDrawerProps) {
  const styles = usePastRunsDrawerStyles()
  if (execution === null && executionHistory.length === 0) {
    return (
      <div data-tree-past-runs className={styles.drawer}>
        <p className={styles.empty}>No past runs.</p>
      </div>
    )
  }
  return (
    <div data-tree-past-runs className={styles.drawer}>
      {execution !== null && (
        <EntryRow
          execution={execution}
          isCurrent
          pinned={false}
          onTogglePin={onTogglePin}
          onCheckout={onCheckout}
        />
      )}
      {executionHistory.map((entry) => (
        <EntryRow
          key={entry.execution.executionId}
          execution={entry.execution}
          isCurrent={false}
          pinned={entry.pinned}
          onTogglePin={onTogglePin}
          onCheckout={onCheckout}
        />
      ))}
    </div>
  )
}

function EntryRow({
  execution,
  isCurrent,
  pinned,
  onTogglePin,
  onCheckout,
}: {
  execution: ExecutionRecord
  isCurrent: boolean
  pinned: boolean
  onTogglePin?: (executionId: string, pinned: boolean) => void
  onCheckout?: (executionId: string) => void
}) {
  const styles = usePastRunsDrawerStyles()
  const outcomeGlyph = outcomeGlyphFor(execution.outcome)
  const waveSuffix = (execution.waveId ?? '').slice(0, 6)
  return (
    <div
      data-tree-reflog-entry
      data-execution-id={execution.executionId}
      data-current={String(isCurrent)}
      data-pinned={String(pinned)}
      className={styles.entry}
    >
      <span className={styles.outcome} title={execution.outcome}>
        {outcomeGlyph}
      </span>
      <span
        data-tree-execution-id-display
        className={styles.id}
        title={execution.executionId}
      >
        {truncateId(execution.executionId)}
      </span>
      <span className={styles.timestamp}>{execution.attemptedAt}</span>
      {waveSuffix !== '' && <span className={styles.wave}>wave: {waveSuffix}</span>}
      {isCurrent && <span className={styles.currentTag}>current</span>}
      <div className={styles.actions}>
        {!isCurrent && onTogglePin !== undefined && (
          <Button
            size="small"
            appearance="subtle"
            onClick={() => onTogglePin(execution.executionId, !pinned)}
          >
            {pinned ? 'Unpin' : 'Pin'}
          </Button>
        )}
        {!isCurrent && onCheckout !== undefined && (
          <Button
            size="small"
            appearance="subtle"
            onClick={() => onCheckout(execution.executionId)}
          >
            Checkout
          </Button>
        )}
      </div>
    </div>
  )
}

function truncateId(id: string): string {
  if (id.length <= 12) return id
  return `${id.slice(0, 8)}\u2026`
}

function outcomeGlyphFor(outcome: ExecutionRecord['outcome']): string {
  switch (outcome) {
    case 'success':
      return '✓'
    case 'failure':
    case 'error':
      return '⚠'
    case 'cancelled':
      return '⦾'
    case 'pending':
      return '●'
  }
}

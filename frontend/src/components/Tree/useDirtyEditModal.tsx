// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * React-state-backed dirty-edit swap guard per spec 01 §13.1a.
 *
 * `guardedSwap(tree, swap)` runs `swap()` immediately when the tree has
 * no unrefreshed edits; otherwise it defers `swap()` behind a confirm
 * modal. "Discard and continue" runs the deferred swap; "Cancel"
 * abandons it. Mirrors the `useCostGuardrailModal` shape: a stable
 * callback + a `modalElement` the host renders in its modal slot.
 *
 * The three `Workspace`-mutating operations (openTree / newTree /
 * closeTree) funnel through `guardedSwap`; `branchToNewTree` is exempt
 * (the clone deep-copies the source's edited state, so nothing is lost
 * in-session) per spec §13.1.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { DirtyEditModal } from './DirtyEditModal'
import { countUnrefreshedEdits, hasUnrefreshedEdits } from '../../runner/workspacePersistence'
import type { ConversationTree } from '../../runner/treeTypes'

interface PendingSwap {
  count: number
  swap: () => void
}

export interface UseDirtyEditModalResult {
  /** Run `swap()` now if clean; else defer behind the confirm modal. */
  guardedSwap: (tree: ConversationTree | null, swap: () => void) => void
  modalElement: React.ReactElement | null
  isPending: boolean
}

export interface UseDirtyEditModalOptions {
  /** Reject new dirty-swap prompts while another modal owns the slot. */
  blocked?: boolean
}

export function useDirtyEditModal(options: UseDirtyEditModalOptions = {}): UseDirtyEditModalResult {
  const { blocked = false } = options
  const [pending, setPending] = useState<PendingSwap | null>(null)
  // Mirrors `pending` so the stable guardedSwap closure can detect an
  // in-flight decision without depending on render state.
  const pendingRef = useRef<PendingSwap | null>(null)
  const blockedRef = useRef(blocked)
  useEffect(() => {
    blockedRef.current = blocked
  }, [blocked])

  const guardedSwap = useCallback((tree: ConversationTree | null, swap: () => void) => {
    if (blockedRef.current) {
      console.error(
        'useDirtyEditModal: guardedSwap ignored — another modal decision is already pending',
      )
      return
    }
    if (!hasUnrefreshedEdits(tree)) {
      swap()
      return
    }
    // A decision is already pending; ignore the new swap so the operator
    // resolves the current modal first (mirrors PR6.4's concurrent guard).
    if (pendingRef.current !== null) {
      console.error(
        'useDirtyEditModal: concurrent guardedSwap ignored — a decision is already pending',
      )
      return
    }
    const decision: PendingSwap = { count: countUnrefreshedEdits(tree), swap }
    pendingRef.current = decision
    setPending(decision)
  }, [])

  const onDiscard = () => {
    if (pending === null) return
    pending.swap()
    pendingRef.current = null
    setPending(null)
  }
  const onCancel = () => {
    if (pending === null) return
    pendingRef.current = null
    setPending(null)
  }

  const modalElement =
    pending !== null ? (
      <DirtyEditModal count={pending.count} onDiscard={onDiscard} onCancel={onCancel} />
    ) : null

  return { guardedSwap, modalElement, isPending: pending !== null }
}

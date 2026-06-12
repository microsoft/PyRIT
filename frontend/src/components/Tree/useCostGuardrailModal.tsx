// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * React-state-backed `CostGuardrail` provider per spec §8.1.
 *
 * Bridges the runner's `approve(count, kind): Promise<boolean>` contract
 * to a Fluent Dialog: waves below `confirmThresholdCount` resolve true
 * synchronously (one-click); waves at/above threshold render the modal
 * and resolve on the operator's Refresh/Cancel decision. "Don't ask
 * again this session" suppresses subsequent at-threshold prompts; the
 * 2× safety floor forces the modal back regardless.
 *
 * The returned `guardrail` reference is stable across re-renders so the
 * shim's wiring doesn't re-bind. The returned `modalElement` is a
 * ReactNode the host renders somewhere stable (typically near the
 * TreeCanvas mount).
 */

import { useMemo, useRef, useState } from 'react'

import { CostGuardrailModal } from './CostGuardrailModal'
import type { CostGuardrail, WaveTriggerKind } from '../../runner/treeTypes'

export interface UseCostGuardrailModalOptions {
  /** Default 20 per spec §8.1; sourced from WorkspaceSettings in production. */
  confirmThresholdCount: number
}

export interface UseCostGuardrailModalResult {
  guardrail: CostGuardrail
  modalElement: React.ReactElement | null
}

interface PendingDecision {
  count: number
  kind: WaveTriggerKind
  resolve: (approved: boolean) => void
}

export function useCostGuardrailModal(
  options: UseCostGuardrailModalOptions,
): UseCostGuardrailModalResult {
  const { confirmThresholdCount } = options
  const [pending, setPending] = useState<PendingDecision | null>(null)
  // Suppression flag the approve() closure reads from event-handler
  // context. Mutated only from button click handlers — never from render
  // — to keep the new react-hooks/refs rule happy.
  const suppressedRef = useRef<boolean>(false)
  // Mirrors `pending` so the stable approve() closure can sync-reject a
  // concurrent caller without overwriting an in-flight resolver.
  const pendingRef = useRef<PendingDecision | null>(null)

  const guardrail = useMemo<CostGuardrail>(
    () => ({
      approve: (count, kind) =>
        new Promise<boolean>((resolve) => {
          if (count < confirmThresholdCount) {
            resolve(true)
            return
          }
          const safetyFloor = confirmThresholdCount * 2
          if (count < safetyFloor && suppressedRef.current) {
            resolve(true)
            return
          }
          if (pendingRef.current !== null) {
            console.error(
              'useCostGuardrailModal: concurrent approve() call rejected — a decision is already pending',
            )
            resolve(false)
            return
          }
          const decision: PendingDecision = { count, kind, resolve }
          pendingRef.current = decision
          setPending(decision)
        }),
    }),
    [confirmThresholdCount],
  )

  const onRefresh = (commitSuppression: boolean) => {
    if (pending === null) return
    if (commitSuppression) suppressedRef.current = true
    pending.resolve(true)
    pendingRef.current = null
    setPending(null)
  }
  const onCancel = () => {
    if (pending === null) return
    pending.resolve(false)
    pendingRef.current = null
    setPending(null)
  }

  const modalElement =
    pending !== null ? (
      <CostGuardrailModal
        count={pending.count}
        triggerKind={pending.kind}
        threshold={confirmThresholdCount}
        onRefresh={onRefresh}
        onCancel={onCancel}
      />
    ) : null

  return { guardrail, modalElement }
}

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

import { useEffect, useMemo, useRef, useState } from 'react'

import { CostGuardrailModal } from './CostGuardrailModal'
import type { CostGuardrail, WaveTriggerKind } from '../../runner/treeTypes'

export interface UseCostGuardrailModalOptions {
  /** Default 20 per spec §8.1; sourced from WorkspaceSettings in production. */
  confirmThresholdCount: number
  /**
   * Persisted "don't ask again this session" flag, sourced from
   * `WorkspaceSettings.suppressConfirmModalThisSession` (spec §13.1). When
   * provided, it seeds + drives suppression so it survives reload via
   * sessionStorage. When omitted, suppression is internal/session-only
   * (legacy behavior). The 2× safety floor overrides it regardless.
   */
  suppressed?: boolean
  /**
   * Called when the operator commits "Don't ask again" via the modal's
   * Refresh button. The host persists it into WorkspaceSettings (and thus
   * sessionStorage). When omitted, suppression stays internal.
   */
  onChangeSuppressed?: (next: boolean) => void
}

export interface UseCostGuardrailModalResult {
  guardrail: CostGuardrail
  modalElement: React.ReactElement | null
  isPending: boolean
}

interface PendingDecision {
  count: number
  kind: WaveTriggerKind
  resolve: (approved: boolean) => void
}

export function useCostGuardrailModal(
  options: UseCostGuardrailModalOptions,
): UseCostGuardrailModalResult {
  const { confirmThresholdCount, suppressed, onChangeSuppressed } = options
  const [pending, setPending] = useState<PendingDecision | null>(null)
  // Suppression flag the approve() closure reads from event-handler
  // context. Seeded from the controlled `suppressed` prop (persisted via
  // WorkspaceSettings) and kept in sync via an effect so the stable
  // approve() closure reads the latest value without re-binding. Mutated
  // outside render to satisfy the react-hooks/refs rule.
  const suppressedRef = useRef<boolean>(suppressed ?? false)
  useEffect(() => {
    if (suppressed !== undefined) suppressedRef.current = suppressed
  }, [suppressed])
  // Mirrors `onChangeSuppressed` so the stable onRefresh handler always
  // notifies the latest callback.
  const onChangeSuppressedRef = useRef(onChangeSuppressed)
  useEffect(() => {
    onChangeSuppressedRef.current = onChangeSuppressed
  }, [onChangeSuppressed])
  // Mirrors `pending` so the stable approve() closure can sync-reject a
  // concurrent caller without overwriting an in-flight resolver.
  const pendingRef = useRef<PendingDecision | null>(null)

  useEffect(
    () => () => {
      const decision = pendingRef.current
      if (decision === null) return
      pendingRef.current = null
      decision.resolve(false)
    },
    [],
  )

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
    if (commitSuppression) {
      // Optimistic local set so a subsequent approve() in the same tick
      // honors it even before the persisted prop round-trips back.
      suppressedRef.current = true
      onChangeSuppressedRef.current?.(true)
    }
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

  return { guardrail, modalElement, isPending: pending !== null }
}

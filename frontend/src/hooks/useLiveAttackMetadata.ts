import { useEffect, useRef } from 'react'

import { SCENARIO_RUN_POLL_INTERVAL_MS } from '@/hooks/useScenarioRunProgress'
import { attacksApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { AttackMetadata } from '@/types'

/** Running scan attacks refresh at the same pace as the scan they belong to. */
export const LIVE_ATTACK_POLL_INTERVAL_MS = SCENARIO_RUN_POLL_INTERVAL_MS

interface UseLiveAttackMetadataOptions {
  /** The running attack to follow; null stops following. */
  readonly attackResultId: string | null
  /** Receives each read, including the first one that is no longer in progress. */
  readonly onMetadata: (metadata: AttackMetadata) => void
  /** The attack is neither running nor saved any more, such as after a cancelled scan. */
  readonly onGone: () => void
}

/**
 * Re-reads a running scan attack's metadata until a read says it is no longer in progress.
 * A failed read keeps the current metadata and is retried on the next interval.
 */
export function useLiveAttackMetadata({ attackResultId, onMetadata, onGone }: UseLiveAttackMetadataOptions): void {
  const callbacks = useRef({ onMetadata, onGone })
  useEffect(() => {
    callbacks.current = { onMetadata, onGone }
  }, [onMetadata, onGone])

  useEffect(() => {
    if (!attackResultId) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const schedule = (): void => {
      timer = setTimeout(() => { void read() }, LIVE_ATTACK_POLL_INTERVAL_MS)
    }
    const read = async (): Promise<void> => {
      try {
        const metadata = await attacksApi.getAttackMetadata(attackResultId)
        if (!active) return
        callbacks.current.onMetadata(metadata)
        if (metadata.in_progress) schedule()
      } catch (error: unknown) {
        if (!active) return
        if (toApiError(error).status === 404) callbacks.current.onGone()
        else schedule()
      }
    }
    schedule()
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [attackResultId])
}

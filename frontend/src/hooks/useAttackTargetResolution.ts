import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  AttackTargetResolutionStatus,
  TargetInfo,
  TargetInstance,
} from '@/types'
import {
  resolveTargetByIdentifierHash,
  targetIdentifierHash,
} from '@/utils/targetIdentity'
import type { TargetHashResolution } from '@/utils/targetIdentity'

const TARGET_PAGE_SIZE = 200
const TARGET_MAX_PAGES = 100

interface TargetSelection {
  target: TargetInstance | null
  source: 'none' | 'explicit' | 'route'
  attackId: string | null
}

interface RegistryResolution {
  attackId: string | null
  attackLoadSequence: number
  status: 'idle' | 'resolved' | 'unavailable' | 'ambiguous' | 'error'
  target?: TargetInstance
}

interface UseAttackTargetResolutionOptions {
  attackId: string | null
  attackLoadSequence: number
  attackTarget: TargetInfo | null
  attackTargetSource: 'persisted' | 'active-selection'
}

interface UseAttackTargetResolutionResult {
  activeTarget: TargetInstance | null
  setExplicitTarget: (target: TargetInstance) => void
  resolutionStatus: AttackTargetResolutionStatus
  retryResolution: () => void
}

function hasCompleteIdentifier(target: TargetInfo | null): target is TargetInfo {
  return Boolean(
    target
    && typeof target.identifier_hash === 'string'
    && target.identifier_hash.length > 0,
  )
}

async function listAllTargets(): Promise<TargetInstance[]> {
  const targets: TargetInstance[] = []
  const seenCursors = new Set<string>()
  let cursor: string | undefined
  let pageCount = 0

  do {
    pageCount += 1
    if (pageCount > TARGET_MAX_PAGES) {
      throw new Error('Target registry pagination exceeded the page limit')
    }
    const response = await targetsApi.listTargets(TARGET_PAGE_SIZE, cursor)
    targets.push(...response.items)
    if (!response.pagination.has_more) break

    const nextCursor = response.pagination.next_cursor ?? undefined
    if (!nextCursor || seenCursors.has(nextCursor)) {
      throw new Error('Target registry pagination did not advance')
    }
    seenCursors.add(nextCursor)
    cursor = nextCursor
  } while (cursor)

  return targets
}

async function resolvePersistedTarget(target: TargetInfo): Promise<TargetHashResolution> {
  if (target.target_registry_name) {
    try {
      const namedTarget = await targetsApi.getTarget(target.target_registry_name)
      if (
        typeof namedTarget?.identifier?.hash === 'string'
        && targetIdentifierHash(namedTarget) === target.identifier_hash
      ) {
        return { status: 'resolved' as const, target: namedTarget }
      }
    } catch (error) {
      if (toApiError(error).status !== 404) throw error
    }
  }

  return resolveTargetByIdentifierHash(target.identifier_hash, await listAllTargets())
}

export function useAttackTargetResolution({
  attackId,
  attackLoadSequence,
  attackTarget,
  attackTargetSource,
}: UseAttackTargetResolutionOptions): UseAttackTargetResolutionResult {
  const [selection, setSelection] = useState<TargetSelection>({
    target: null,
    source: 'none',
    attackId: null,
  })
  const selectionRef = useRef(selection)
  const [registryResolution, setRegistryResolution] = useState<RegistryResolution>({
    attackId: null,
    attackLoadSequence: 0,
    status: 'idle',
  })
  const [resolutionAttempt, setResolutionAttempt] = useState(0)

  const setExplicitTarget = useCallback((target: TargetInstance): void => {
    const next: TargetSelection = { target, source: 'explicit', attackId: null }
    selectionRef.current = next
    setSelection(next)
    setRegistryResolution({ attackId: null, attackLoadSequence: 0, status: 'idle' })
    setResolutionAttempt((attempt) => attempt + 1)
  }, [])

  useEffect(() => {
    if (!attackId || !hasCompleteIdentifier(attackTarget)) return
    if (attackTargetSource === 'active-selection') return
    const initialSelection = selectionRef.current
    if (
      initialSelection.source === 'explicit'
      && (
        !initialSelection.target
        || targetIdentifierHash(initialSelection.target) !== attackTarget.identifier_hash
      )
    ) return

    let cancelled = false
    const resolveTarget = async (): Promise<void> => {
      try {
        const resolution = await resolvePersistedTarget(attackTarget)
        if (cancelled) return

        const currentSelection = selectionRef.current
        if (currentSelection.source === 'explicit') {
          if (
            !currentSelection.target
            || targetIdentifierHash(currentSelection.target) !== attackTarget.identifier_hash
          ) return

          if (resolution.status === 'resolved') {
            setRegistryResolution({
              attackId,
              attackLoadSequence,
              status: 'resolved',
              target: resolution.target,
            })
            return
          }
          setRegistryResolution({
            attackId,
            attackLoadSequence,
            status: resolution.status,
          })
          return
        }

        if (resolution.status === 'resolved') {
          const nextSelection: TargetSelection = {
            target: resolution.target,
            source: 'route',
            attackId,
          }
          selectionRef.current = nextSelection
          setSelection(nextSelection)
          setRegistryResolution({
            attackId,
            attackLoadSequence,
            status: 'resolved',
            target: resolution.target,
          })
          return
        }
        setRegistryResolution({ attackId, attackLoadSequence, status: resolution.status })
      } catch {
        if (cancelled) return
        const currentSelection = selectionRef.current
        if (
          currentSelection.source === 'explicit'
          && currentSelection.target
          && targetIdentifierHash(currentSelection.target) !== attackTarget.identifier_hash
        ) return
        setRegistryResolution({ attackId, attackLoadSequence, status: 'error' })
      }
    }

    void resolveTarget()
    return () => {
      cancelled = true
    }
  }, [attackId, attackLoadSequence, attackTarget, attackTargetSource, resolutionAttempt])

  const resolutionStatus = useMemo<AttackTargetResolutionStatus>(() => {
    if (!attackId) return 'idle'
    if (!hasCompleteIdentifier(attackTarget)) return 'legacy'
    if (selection.source === 'explicit' && selection.target) {
      if (targetIdentifierHash(selection.target) !== attackTarget.identifier_hash) {
        return 'explicit-mismatch'
      }
      if (attackTargetSource === 'active-selection') return 'resolved'
    }
    if (attackTargetSource === 'active-selection') return 'unavailable'
    if (
      registryResolution.attackId !== attackId
      || registryResolution.attackLoadSequence !== attackLoadSequence
    ) return 'loading'
    return registryResolution.status
  }, [attackId, attackLoadSequence, attackTarget, attackTargetSource, registryResolution, selection])

  const activeTarget = useMemo<TargetInstance | null>(() => {
    if (selection.source !== 'route') return selection.target
    if (!attackId) return null
    if (
      resolutionStatus === 'resolved'
      && selection.attackId === attackId
      && registryResolution.target === selection.target
    ) {
      return selection.target
    }
    return null
  }, [attackId, registryResolution.target, resolutionStatus, selection])

  const retryResolution = useCallback((): void => {
    setRegistryResolution({ attackId: null, attackLoadSequence: 0, status: 'idle' })
    setResolutionAttempt((attempt) => attempt + 1)
  }, [])

  return {
    activeTarget,
    setExplicitTarget,
    resolutionStatus,
    retryResolution,
  }
}

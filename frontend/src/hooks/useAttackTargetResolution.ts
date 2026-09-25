import { useCallback, useEffect, useState } from 'react'

import { useRuntime } from '@/hooks/useRuntime'
import { targetsApi } from '@/services/api'
import { listRegisteredTargets } from '@/services/targetRegistry'
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
  attackTargetSource: 'persisted' | 'created'
  createdTarget?: TargetInstance | null
  createdTargetGeneration?: string
}

interface UseAttackTargetResolutionResult {
  activeTarget: TargetInstance | null
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

  return resolveTargetByIdentifierHash(target.identifier_hash, await listRegisteredTargets())
}

export function useAttackTargetResolution({
  attackId,
  attackLoadSequence,
  attackTarget,
  attackTargetSource,
  createdTarget,
  createdTargetGeneration,
}: UseAttackTargetResolutionOptions): UseAttackTargetResolutionResult {
  const { generation, ready } = useRuntime()
  const useCreatedTarget = attackTargetSource === 'created'
    && createdTargetGeneration === generation
    && Boolean(createdTarget)
  const [registryResolution, setRegistryResolution] = useState<RegistryResolution>({
    attackId: null,
    attackLoadSequence: 0,
    status: 'idle',
  })
  const [resolutionAttempt, setResolutionAttempt] = useState(0)

  useEffect(() => {
    if (!ready || !attackId || !hasCompleteIdentifier(attackTarget)) return
    if (useCreatedTarget) return

    let cancelled = false
    const resolveTarget = async (): Promise<void> => {
      try {
        const resolution = await resolvePersistedTarget(attackTarget)
        if (cancelled) return

        if (resolution.status === 'resolved') {
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
        setRegistryResolution({ attackId, attackLoadSequence, status: 'error' })
      }
    }

    void resolveTarget()
    return () => {
      cancelled = true
    }
  }, [attackId, attackLoadSequence, attackTarget, resolutionAttempt, generation, ready, useCreatedTarget])

  const getResolutionStatus = (): AttackTargetResolutionStatus => {
    if (!attackId) return 'idle'
    if (!hasCompleteIdentifier(attackTarget)) return 'legacy'
    if (useCreatedTarget) {
      return createdTarget && targetIdentifierHash(createdTarget) === attackTarget.identifier_hash
        ? 'resolved'
        : 'unavailable'
    }
    if (
      registryResolution.attackId !== attackId
      || registryResolution.attackLoadSequence !== attackLoadSequence
    ) return 'loading'
    return registryResolution.status
  }
  const resolutionStatus = getResolutionStatus()
  const activeTarget = resolutionStatus === 'resolved'
    ? (useCreatedTarget ? createdTarget : registryResolution.target) ?? null
    : null

  const retryResolution = useCallback((): void => {
    setRegistryResolution({ attackId: null, attackLoadSequence: 0, status: 'idle' })
    setResolutionAttempt((attempt) => attempt + 1)
  }, [])

  return {
    activeTarget,
    resolutionStatus,
    retryResolution,
  }
}

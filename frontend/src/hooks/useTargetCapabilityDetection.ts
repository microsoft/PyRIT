import { useEffect, useRef, useState } from 'react'
import { targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { TargetInstance } from '@/types'

interface DetectionResult {
  state: 'Waiting' | 'Checking...' | 'Saved' | 'Failed'
  error?: string
  target?: TargetInstance
}

export interface TargetCapabilityDetection {
  running: boolean
  results: Record<string, DetectionResult>
  start: (targets: TargetInstance[]) => Promise<void>
}

export function useTargetCapabilityDetection({
  generation,
  onTargetDetected,
}: {
  generation: string
  onTargetDetected?: (updated: TargetInstance, previous: TargetInstance) => void
}): TargetCapabilityDetection {
  const [state, setState] = useState<{
    generation: string
    running: boolean
    results: Record<string, DetectionResult>
  }>({ generation, running: false, results: {} })
  const active = useRef({ valid: true, running: false })
  const callback = useRef(onTargetDetected)
  useEffect(() => { callback.current = onTargetDetected }, [onTargetDetected])
  useEffect(() => {
    const lifetime = { valid: true, running: false }
    active.current = lifetime
    return () => { lifetime.valid = false }
  }, [generation])

  const current = state.generation === generation ? state : { running: false, results: {} }
  const start = async (targets: TargetInstance[]): Promise<void> => {
    const lifetime = active.current
    if (lifetime.running || targets.length === 0) return
    lifetime.running = true
    const batch = [...targets]
    setState({
      generation,
      running: true,
      results: Object.fromEntries(batch.map((target) => [target.target_registry_name, { state: 'Waiting' }])),
    })
    try {
      for (const target of batch) {
        if (!lifetime.valid) break
        const name = target.target_registry_name
        setState((previous) => ({
          ...previous, results: { ...previous.results, [name]: { state: 'Checking...' } },
        }))
        try {
          const updated = await targetsApi.detectCapabilities(target)
          if (!lifetime.valid) break
          callback.current?.(updated, target)
          setState((previous) => ({
            ...previous, results: { ...previous.results, [name]: { state: 'Saved', target: updated } },
          }))
        } catch (error: unknown) {
          if (!lifetime.valid) break
          setState((previous) => ({
            ...previous, results: { ...previous.results, [name]: { state: 'Failed', error: toApiError(error).detail } },
          }))
        }
      }
    } finally {
      lifetime.running = false
      if (lifetime.valid) setState((previous) => ({ ...previous, running: false }))
    }
  }
  return { ...current, start }
}

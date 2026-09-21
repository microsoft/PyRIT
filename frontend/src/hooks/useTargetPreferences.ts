import { useRef, useState } from 'react'

import type { TargetInstance, TargetPreferences, TargetReference } from '@/types'
import { targetIdentifierHash } from '@/utils/targetIdentity'
import {
  EMPTY_TARGET_PREFERENCES,
  readTargetPreferences,
  writeTargetPreferences,
} from '@/utils/targetPreferences'

interface PreferenceState {
  preferences: TargetPreferences
  error: string | null
}

function readPreferences(accountKey: string | null): PreferenceState {
  if (accountKey === null) return { preferences: EMPTY_TARGET_PREFERENCES, error: null }
  try {
    return { preferences: readTargetPreferences(accountKey), error: null }
  } catch {
    return {
      preferences: EMPTY_TARGET_PREFERENCES,
      error: 'Could not read saved target defaults. Select or clear them in the target registry.',
    }
  }
}

/** The owning component must be keyed by account to isolate its state and drafts. */
export function useTargetPreferences(accountKey: string | null, targets: TargetInstance[]) {
  const [state, setState] = useState<PreferenceState>(() => readPreferences(accountKey))
  const preferencesRef = useRef(state.preferences)

  const setDefault = (role: keyof TargetPreferences, target: TargetInstance | null): void => {
    const reference: TargetReference | null = target ? {
      registryName: target.target_registry_name,
      identifierHash: targetIdentifierHash(target),
    } : null
    const preferences = { ...preferencesRef.current, [role]: reference }
    preferencesRef.current = preferences
    let error: string | null = null
    try {
      if (accountKey === null) throw new Error('Account identity is not ready')
      writeTargetPreferences(accountKey, preferences)
    } catch {
      error = 'Target defaults apply in this session only. Could not save them in this browser.'
    }
    setState({ preferences, error })
  }

  const resolve = (reference: TargetReference | null): TargetInstance | null => (
    reference ? targets.find((target: TargetInstance) => (
      target.target_registry_name === reference.registryName
      && targetIdentifierHash(target) === reference.identifierHash
    )) ?? null : null
  )

  const objectiveTarget = resolve(state.preferences.objective)
  const resolvedAdversarial = resolve(state.preferences.adversarial)
  const adversarialTarget = resolvedAdversarial?.capabilities?.supports_multi_turn === true
    ? resolvedAdversarial : null

  return {
    preferences: state.preferences,
    error: state.error,
    objectiveTarget,
    adversarialTarget,
    setDefault,
  }
}

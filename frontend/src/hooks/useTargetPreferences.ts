import type { TargetInstance, TargetPreferences, TargetReference } from '@/types'
import { resolveDefaultAdversarialTarget, resolveTargetReference, targetReference } from '@/utils/targetIdentity'

import { useUserPreferences } from './useUserPreferences'

export function useTargetPreferences(targets: TargetInstance[]) {
  const { preferences: userPreferences, updatePreferences } = useUserPreferences()
  const preferences = userPreferences.targets

  const setDefault = (role: keyof TargetPreferences, target: TargetInstance | null): void => {
    const reference: TargetReference | null = target ? targetReference(target) : null
    updatePreferences((current) => ({
      ...current,
      targets: { ...current.targets, [role]: reference },
    }))
  }

  const objectiveTarget = resolveTargetReference(preferences.objective, targets)
  const adversarialTarget = resolveDefaultAdversarialTarget(preferences, targets)

  return {
    preferences,
    objectiveTarget,
    adversarialTarget,
    setDefault,
  }
}

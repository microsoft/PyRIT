import type { TargetInstance, TargetPreferences, TargetReference } from '@/types'
import { resolveTargetReference, targetReference } from '@/utils/targetIdentity'

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
  const resolvedAdversarial = preferences.adversarial
    ? resolveTargetReference(preferences.adversarial, targets)
    : targets.find((target: TargetInstance) => target.target_registry_name === 'adversarial_chat')
  const adversarialTarget = resolvedAdversarial?.capabilities?.supports_multi_turn === true
    ? resolvedAdversarial : null

  return {
    preferences,
    objectiveTarget,
    adversarialTarget,
    setDefault,
  }
}

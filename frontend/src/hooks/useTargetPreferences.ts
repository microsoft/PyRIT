import type { TargetInstance, TargetReference } from '@/types'
import { resolveDefaultAdversarialTarget, resolveTargetReference, targetReference } from '@/utils/targetIdentity'

import { useUserPreferences } from './useUserPreferences'

export function useTargetPreferences(targets: TargetInstance[]) {
  const { preferences: userPreferences, updatePreferences } = useUserPreferences()
  const preferences = userPreferences.targets

  const setDefault = (role: 'adversarial', target: TargetInstance | null): void => {
    const reference: TargetReference | null = target ? targetReference(target) : null
    updatePreferences((current) => ({
      ...current,
      targets: { ...current.targets, [role]: reference },
    }))
  }

  const objectiveReference = userPreferences.lastUsedTargets?.objective ?? null
  const objectiveTarget = resolveTargetReference(objectiveReference, targets)
  const adversarialTarget = resolveDefaultAdversarialTarget(preferences, targets)
  const lastAdversarialTarget = resolveTargetReference(userPreferences.lastUsedTargets?.adversarial ?? null, targets)
  const scanAdversarialTarget = preferences.adversarial
    || targets.some((target: TargetInstance) => target.target_registry_name === 'adversarial_chat')
    ? adversarialTarget : lastAdversarialTarget?.capabilities?.supports_multi_turn === true ? lastAdversarialTarget : null

  return {
    preferences,
    objectiveReference,
    objectiveTarget,
    adversarialTarget,
    scanAdversarialTarget,
    setDefault,
  }
}

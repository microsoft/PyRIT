import type { TargetPreferences, TargetReference } from '@/types'

const STORAGE_PREFIX = 'pyrit.targetDefaults.v1.'

export const EMPTY_TARGET_PREFERENCES: TargetPreferences = {
  objective: null,
  adversarial: null,
}

function isTargetReference(value: unknown): value is TargetReference | null {
  return value === null || (
    typeof value === 'object'
    && 'registryName' in value
    && typeof value.registryName === 'string'
    && value.registryName.length > 0
    && 'identifierHash' in value
    && typeof value.identifierHash === 'string'
    && value.identifierHash.length > 0
  )
}

export function readTargetPreferences(accountKey: string): TargetPreferences {
  const stored = window.localStorage.getItem(STORAGE_PREFIX + accountKey)
  if (stored === null) return EMPTY_TARGET_PREFERENCES
  const value: unknown = JSON.parse(stored)
  if (
    typeof value !== 'object'
    || value === null
    || !('objective' in value)
    || !('adversarial' in value)
    || !isTargetReference(value.objective)
    || !isTargetReference(value.adversarial)
  ) {
    throw new Error('Saved target defaults are invalid. Select or clear your defaults in the target registry.')
  }
  return { objective: value.objective, adversarial: value.adversarial }
}

export function writeTargetPreferences(accountKey: string, preferences: TargetPreferences): void {
  window.localStorage.setItem(STORAGE_PREFIX + accountKey, JSON.stringify(preferences))
}

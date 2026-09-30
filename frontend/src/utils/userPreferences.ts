import { isThemeMode } from '@/themes/themePresets'
import type { TargetReference, UserPreferences } from '@/types'

const STORAGE_PREFIX = 'pyrit.userPreferences.v1.'

export const DEFAULT_USER_PREFERENCES: UserPreferences = {
  targets: { objective: null, adversarial: null },
  hiddenTargetRegistryNames: [],
  labels: {},
  theme: 'system',
  chatMarkdown: false,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isTargetReference(value: unknown): value is TargetReference | null {
  return value === null || (
    isRecord(value)
    && typeof value.registryName === 'string'
    && value.registryName.length > 0
    && typeof value.identifierHash === 'string'
    && value.identifierHash.length > 0
  )
}

function readLegacyHiddenTargetRegistryNames(accountKey: string): string[] {
  if (accountKey !== 'local') return []

  const stored = window.localStorage.getItem('pyrit.hiddenTargetRegistryNames')
  if (stored === null) return []
  const value: unknown = JSON.parse(stored)
  if (!Array.isArray(value) || !value.every((registryName: unknown) => typeof registryName === 'string')) {
    throw new Error('Saved hidden targets are invalid.')
  }
  return value
}

function parsePreferences(raw: string, legacyHiddenTargetRegistryNames: string[]): UserPreferences {
  const value: unknown = JSON.parse(raw)
  if (
    !isRecord(value)
    || !isRecord(value.targets)
    || !isTargetReference(value.targets.objective)
    || !isTargetReference(value.targets.adversarial)
    || !isRecord(value.labels)
    || !Object.values(value.labels).every((label: unknown) => label === null || typeof label === 'string')
    || !isThemeMode(value.theme)
    || typeof value.chatMarkdown !== 'boolean'
    || (
      value.hiddenTargetRegistryNames !== undefined
      && (
        !Array.isArray(value.hiddenTargetRegistryNames)
        || !value.hiddenTargetRegistryNames.every((registryName: unknown) => typeof registryName === 'string')
      )
    )
  ) {
    throw new Error('Saved user preferences are invalid.')
  }
  return {
    targets: { objective: value.targets.objective, adversarial: value.targets.adversarial },
    hiddenTargetRegistryNames: value.hiddenTargetRegistryNames ?? legacyHiddenTargetRegistryNames,
    labels: Object.fromEntries(
      Object.entries(value.labels).map(([key, label]) => [key, typeof label === 'string' ? label : null]),
    ),
    theme: value.theme,
    chatMarkdown: value.chatMarkdown,
  }
}

/** Legacy browser-wide settings have no account owner, so only the local profile can inherit them. */
function readLegacyPreferences(accountKey: string): UserPreferences {
  const preferences = { ...DEFAULT_USER_PREFERENCES }
  const targets = window.localStorage.getItem(`pyrit.targetDefaults.v1.${accountKey}`)
  if (targets !== null) {
    const value: unknown = JSON.parse(targets)
    if (!isRecord(value) || !isTargetReference(value.objective) || !isTargetReference(value.adversarial)) {
      throw new Error('Saved target defaults are invalid.')
    }
    preferences.targets = { objective: value.objective, adversarial: value.adversarial }
  }
  if (accountKey !== 'local') return preferences

  preferences.hiddenTargetRegistryNames = readLegacyHiddenTargetRegistryNames(accountKey)
  const labels = window.localStorage.getItem('pyrit.globalLabels')
  if (labels !== null) {
    const value: unknown = JSON.parse(labels)
    if (!isRecord(value) || !Object.values(value).every((label: unknown) => typeof label === 'string')) {
      throw new Error('Saved run labels are invalid.')
    }
    preferences.labels = Object.fromEntries(
      Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string'),
    )
  }
  const theme = window.localStorage.getItem('pyrit.themeMode')
  if (theme !== null) {
    if (!isThemeMode(theme)) throw new Error('Saved theme is invalid.')
    preferences.theme = theme
  }
  const markdown = window.localStorage.getItem('pyrit.chatMarkdownMode')
  if (markdown !== null) {
    if (markdown !== 'raw' && markdown !== 'markdown') throw new Error('Saved chat display mode is invalid.')
    preferences.chatMarkdown = markdown === 'markdown'
  }
  return preferences
}

export function readUserPreferences(accountKey: string): UserPreferences {
  const stored = window.localStorage.getItem(userPreferencesStorageKey(accountKey))
  if (stored === null) return readLegacyPreferences(accountKey)

  const legacyHiddenTargetRegistryNames = readLegacyHiddenTargetRegistryNames(accountKey)
  return parsePreferences(stored, legacyHiddenTargetRegistryNames)
}

export function writeUserPreferences(accountKey: string, preferences: UserPreferences): void {
  window.localStorage.setItem(userPreferencesStorageKey(accountKey), JSON.stringify(preferences))
}

export function userPreferencesStorageKey(accountKey: string): string {
  return STORAGE_PREFIX + accountKey
}

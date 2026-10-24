import { isThemeMode } from '@/themes/themePresets'
import type { RegisteredComponentReference, OperationSelection, TargetReference, UserPreferences } from '@/types'

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

function isOperationSelection(value: unknown): value is OperationSelection | null {
  if (value === null) return true
  if (!isRecord(value) || Object.keys(value).length !== 1) return false
  const reference = value.id ?? value.name
  return typeof reference === 'string' && reference.length > 0
}

function isScorerReference(value: unknown): value is RegisteredComponentReference | null {
  return value === null || (
    isRecord(value) && typeof value.registry_name === 'string' && value.registry_name.length > 0
    && typeof value.identifier_hash === 'string' && value.identifier_hash.length > 0
  )
}

/** Earlier versions kept the Operation's name as a label. It becomes a name selection that the user must resolve. */
function withMigratedOperation(preferences: UserPreferences): UserPreferences {
  if (!('operation' in preferences.labels)) return preferences
  const { operation: legacyName, ...labels } = preferences.labels
  const operation = preferences.operation !== undefined ? preferences.operation
    : legacyName === null ? null : { name: legacyName }
  return { ...preferences, labels, operation }
}

function parsePreferences(raw: string): UserPreferences {
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
    || (value.chatScorer !== undefined && !isScorerReference(value.chatScorer))
    || (value.operation !== undefined && !isOperationSelection(value.operation))
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
    hiddenTargetRegistryNames: value.hiddenTargetRegistryNames ?? [],
    labels: Object.fromEntries(
      Object.entries(value.labels).map(([key, label]) => [key, typeof label === 'string' ? label : null]),
    ),
    ...(value.operation !== undefined ? { operation: value.operation } : {}),
    theme: value.theme,
    chatMarkdown: value.chatMarkdown,
    ...(value.chatScorer !== undefined ? { chatScorer: value.chatScorer } : {}),
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
  return withMigratedOperation(stored === null ? readLegacyPreferences(accountKey) : parsePreferences(stored))
}

export function writeUserPreferences(accountKey: string, preferences: UserPreferences): void {
  window.localStorage.setItem(userPreferencesStorageKey(accountKey), JSON.stringify(preferences))
}

export function userPreferencesStorageKey(accountKey: string): string {
  return STORAGE_PREFIX + accountKey
}

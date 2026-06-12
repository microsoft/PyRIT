// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Pure workspace-persistence helpers per spec 01 §13.1 (V1.0 minimal
 * Workspace + schema-versioned sessionStorage) + §9.4.2 (beforeunload
 * dirty-edit predicate). Pure functions over an injected `Storage`
 * so tests use an in-memory implementation. The React hook in
 * `components/Tree/useWorkspacePersistence.ts` (PR7f.2) composes
 * these against `window.sessionStorage`.
 */

import type {
  ConversationTree,
  ConversationTreeId,
  WorkspaceSettings,
} from './treeTypes'

// ============================================================================
// Constants
// ============================================================================

/** Bump this when any pyrit.* sessionStorage shape changes (drop-on-mismatch). */
export const SCHEMA_VERSION = 1

export const STORAGE_KEYS = {
  schemaVersion: 'pyrit.schemaVersion',
  recentTreeIds: 'pyrit.workspace.recentTreeIds',
  settings: 'pyrit.workspace.settings',
} as const

export const DEFAULT_WORKSPACE_SETTINGS: WorkspaceSettings = {
  reflogCapPerNode: 50,
  confirmThresholdCount: 20,
  suppressConfirmModalThisSession: false,
}

const KEY_PREFIX = 'pyrit.'

// ============================================================================
// Public types
// ============================================================================

export interface PersistedWorkspace {
  recentTreeIds: ConversationTreeId[]
  settings: WorkspaceSettings
}

// ============================================================================
// Schema-version check (drop-on-mismatch)
// ============================================================================

/**
 * Per spec §13.1 boot step 0: read `pyrit.schemaVersion`; if absent OR not
 * equal to the current version, wipe every key under `pyrit.*` and write
 * the current version. Returns `true` if a wipe happened so callers can
 * surface the operator-visible toast.
 */
export function wipeIfSchemaMismatch(storage: Storage): boolean {
  const raw = storage.getItem(STORAGE_KEYS.schemaVersion)
  if (raw === String(SCHEMA_VERSION)) return false
  // Drop everything under pyrit.* (including parentSourceConversationId.*).
  const toDelete: string[] = []
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i)
    if (key !== null && key.startsWith(KEY_PREFIX)) toDelete.push(key)
  }
  for (const k of toDelete) storage.removeItem(k)
  storage.setItem(STORAGE_KEYS.schemaVersion, String(SCHEMA_VERSION))
  // Absent (never-written) is NOT a wipe — it's first-load. Mismatched is.
  return raw !== null
}

// ============================================================================
// Load / write
// ============================================================================

/**
 * Reads `recentTreeIds` and `settings` from storage. Fails soft on corrupt
 * or wrong-shape values: returns defaults rather than throwing. Per spec
 * §13.1: all pyrit.* keys are recoverable, so the wrong-shape recovery
 * path is "revert to defaults" not "blow up the boot."
 */
export function loadWorkspaceFromStorage(storage: Storage): PersistedWorkspace {
  return {
    recentTreeIds: parseRecentTreeIds(storage.getItem(STORAGE_KEYS.recentTreeIds)),
    settings: parseSettings(storage.getItem(STORAGE_KEYS.settings)),
  }
}

function parseRecentTreeIds(raw: string | null): ConversationTreeId[] {
  if (raw === null) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const out: ConversationTreeId[] = []
    for (const item of parsed) {
      if (typeof item === 'string') out.push(item as ConversationTreeId)
    }
    return out
  } catch {
    return []
  }
}

function parseSettings(raw: string | null): WorkspaceSettings {
  if (raw === null) return DEFAULT_WORKSPACE_SETTINGS
  try {
    const parsed: unknown = JSON.parse(raw)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return DEFAULT_WORKSPACE_SETTINGS
    }
    const obj = parsed as Record<string, unknown>
    return {
      reflogCapPerNode:
        typeof obj.reflogCapPerNode === 'number' ? obj.reflogCapPerNode : DEFAULT_WORKSPACE_SETTINGS.reflogCapPerNode,
      confirmThresholdCount:
        typeof obj.confirmThresholdCount === 'number'
          ? obj.confirmThresholdCount
          : DEFAULT_WORKSPACE_SETTINGS.confirmThresholdCount,
      suppressConfirmModalThisSession:
        typeof obj.suppressConfirmModalThisSession === 'boolean'
          ? obj.suppressConfirmModalThisSession
          : DEFAULT_WORKSPACE_SETTINGS.suppressConfirmModalThisSession,
    }
  } catch {
    return DEFAULT_WORKSPACE_SETTINGS
  }
}

export function writeWorkspaceToStorage(
  storage: Storage,
  workspace: PersistedWorkspace,
): void {
  storage.setItem(STORAGE_KEYS.recentTreeIds, JSON.stringify(workspace.recentTreeIds))
  storage.setItem(STORAGE_KEYS.settings, JSON.stringify(workspace.settings))
}

// ============================================================================
// hasUnrefreshedEdits (§9.4.2)
// ============================================================================

/**
 * Per spec §9.4.2: the predicate the beforeunload guard + the §13.1a
 * in-app dirty-edit modal check. True when any node in the current
 * tree carries operator edits not yet flushed via Refresh (state
 * 'edited' or 'draft'). Runner-driven failure states (failed /
 * cancelled / stale) are NOT operator edits and don't trigger the
 * guard.
 */
export function hasUnrefreshedEdits(tree: ConversationTree | null): boolean {
  if (tree === null) return false
  return tree.nodes.some((n) => n.state === 'edited' || n.state === 'draft')
}

/**
 * Per spec §13.1a: the count surfaced in the dirty-edit modal body
 * ("You have N unsaved edits..."). Counts nodes in operator-edit states
 * ('edited' / 'draft'); runner-driven failure states don't count.
 */
export function countUnrefreshedEdits(tree: ConversationTree | null): number {
  if (tree === null) return 0
  return tree.nodes.reduce((acc, n) => (n.state === 'edited' || n.state === 'draft' ? acc + 1 : acc), 0)
}

// ============================================================================
// URL fragment helpers
// ============================================================================

const FRAGMENT_KEY = 'conversation_tree_id'

/**
 * Parse a URL fragment (with or without leading `#`) for the canonical
 * `conversation_tree_id=X` parameter. Returns `null` when absent or empty.
 */
export function parseTreeIdFromUrlFragment(fragment: string): string | null {
  const body = fragment.startsWith('#') ? fragment.slice(1) : fragment
  if (body === '') return null
  for (const part of body.split('&')) {
    const eq = part.indexOf('=')
    if (eq === -1) continue
    const key = part.slice(0, eq)
    const value = part.slice(eq + 1)
    if (key === FRAGMENT_KEY && value !== '') {
      return decodeURIComponent(value)
    }
  }
  return null
}

/**
 * Build the canonical fragment string `#conversation_tree_id=X`. Returns
 * an empty string for `null` (operator closed the tree → fragment cleared).
 * Per reviewer F: this write is IMMEDIATE on tree.id change, not debounced.
 */
export function serializeTreeIdToUrlFragment(treeId: string | null): string {
  if (treeId === null) return ''
  return `#${FRAGMENT_KEY}=${encodeURIComponent(treeId)}`
}

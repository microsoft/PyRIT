// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the pure workspace-persistence helpers per spec 01 §13.1
 * (V1.0 minimal Workspace + schema-versioned sessionStorage) + §9.4.2
 * (beforeunload guard).
 */

import {
  SCHEMA_VERSION,
  DEFAULT_WORKSPACE_SETTINGS,
  STORAGE_KEYS,
  loadWorkspaceFromStorage,
  writeWorkspaceToStorage,
  wipeIfSchemaMismatch,
  hasUnrefreshedEdits,
  countUnrefreshedEdits,
  parseTreeIdFromUrlFragment,
  serializeTreeIdToUrlFragment,
} from './workspacePersistence'
import { mkRoot, mkSend, mkTree, treeId } from './testHelpers'
import type { ConversationTree } from './treeTypes'

// ============================================================================
// Test scaffolding
// ============================================================================

class MemoryStorage implements Storage {
  private store = new Map<string, string>()
  get length(): number {
    return this.store.size
  }
  clear(): void {
    this.store.clear()
  }
  getItem(key: string): string | null {
    return this.store.get(key) ?? null
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null
  }
  removeItem(key: string): void {
    this.store.delete(key)
  }
  setItem(key: string, value: string): void {
    this.store.set(key, value)
  }
}

// ============================================================================
// wipeIfSchemaMismatch
// ============================================================================

describe('wipeIfSchemaMismatch', () => {
  it('returns false and writes the schema version when storage is empty', () => {
    const storage = new MemoryStorage()
    const wiped = wipeIfSchemaMismatch(storage)
    expect(wiped).toBe(false)
    expect(storage.getItem(STORAGE_KEYS.schemaVersion)).toBe(String(SCHEMA_VERSION))
  })

  it('returns false when the schema version matches', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.schemaVersion, String(SCHEMA_VERSION))
    storage.setItem(STORAGE_KEYS.recentTreeIds, JSON.stringify(['t-1']))
    const wiped = wipeIfSchemaMismatch(storage)
    expect(wiped).toBe(false)
    expect(storage.getItem(STORAGE_KEYS.recentTreeIds)).toBe(JSON.stringify(['t-1']))
  })

  it('returns true and wipes ALL pyrit.* keys when the schema version mismatches', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.schemaVersion, '0')
    storage.setItem(STORAGE_KEYS.recentTreeIds, JSON.stringify(['t-1', 't-2']))
    storage.setItem(STORAGE_KEYS.settings, JSON.stringify({ confirmThresholdCount: 5 }))
    storage.setItem('pyrit.workspace.parentSourceConversationId.t-1', 'conv-1')
    storage.setItem('non-pyrit-key', 'should-survive')

    const wiped = wipeIfSchemaMismatch(storage)

    expect(wiped).toBe(true)
    expect(storage.getItem(STORAGE_KEYS.schemaVersion)).toBe(String(SCHEMA_VERSION))
    expect(storage.getItem(STORAGE_KEYS.recentTreeIds)).toBeNull()
    expect(storage.getItem(STORAGE_KEYS.settings)).toBeNull()
    expect(storage.getItem('pyrit.workspace.parentSourceConversationId.t-1')).toBeNull()
    // Non-pyrit keys survive.
    expect(storage.getItem('non-pyrit-key')).toBe('should-survive')
  })
})

// ============================================================================
// loadWorkspaceFromStorage
// ============================================================================

describe('loadWorkspaceFromStorage', () => {
  it('returns defaults when storage is empty', () => {
    const storage = new MemoryStorage()
    const ws = loadWorkspaceFromStorage(storage)
    expect(ws.recentTreeIds).toEqual([])
    expect(ws.settings).toEqual(DEFAULT_WORKSPACE_SETTINGS)
  })

  it('returns persisted recentTreeIds and settings when present', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.recentTreeIds, JSON.stringify(['t-a', 't-b']))
    storage.setItem(
      STORAGE_KEYS.settings,
      JSON.stringify({ reflogCapPerNode: 100, confirmThresholdCount: 5, suppressConfirmModalThisSession: true }),
    )
    const ws = loadWorkspaceFromStorage(storage)
    expect(ws.recentTreeIds).toEqual(['t-a', 't-b'])
    expect(ws.settings).toEqual({
      reflogCapPerNode: 100,
      confirmThresholdCount: 5,
      suppressConfirmModalThisSession: true,
    })
  })

  it('returns defaults when stored value is corrupt JSON (fail-soft)', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.recentTreeIds, '{not-valid}')
    storage.setItem(STORAGE_KEYS.settings, 'also-not-valid')
    const ws = loadWorkspaceFromStorage(storage)
    expect(ws.recentTreeIds).toEqual([])
    expect(ws.settings).toEqual(DEFAULT_WORKSPACE_SETTINGS)
  })

  it('returns defaults when stored values are wrong shape', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.recentTreeIds, '{"not": "array"}')
    storage.setItem(STORAGE_KEYS.settings, '"not-object"')
    const ws = loadWorkspaceFromStorage(storage)
    expect(ws.recentTreeIds).toEqual([])
    expect(ws.settings).toEqual(DEFAULT_WORKSPACE_SETTINGS)
  })
})

// ============================================================================
// writeWorkspaceToStorage
// ============================================================================

describe('writeWorkspaceToStorage', () => {
  it('writes recentTreeIds and settings as JSON', () => {
    const storage = new MemoryStorage()
    writeWorkspaceToStorage(storage, {
      recentTreeIds: [treeId('t-1')],
      settings: { reflogCapPerNode: 75, confirmThresholdCount: 10, suppressConfirmModalThisSession: false },
    })
    expect(JSON.parse(storage.getItem(STORAGE_KEYS.recentTreeIds) ?? '')).toEqual(['t-1'])
    expect(JSON.parse(storage.getItem(STORAGE_KEYS.settings) ?? '')).toEqual({
      reflogCapPerNode: 75,
      confirmThresholdCount: 10,
      suppressConfirmModalThisSession: false,
    })
  })

  it('round-trips through loadWorkspaceFromStorage', () => {
    const storage = new MemoryStorage()
    const before = {
      recentTreeIds: [treeId('t-x'), treeId('t-y')],
      settings: { reflogCapPerNode: 33, confirmThresholdCount: 7, suppressConfirmModalThisSession: true },
    }
    writeWorkspaceToStorage(storage, before)
    const after = loadWorkspaceFromStorage(storage)
    expect(after.recentTreeIds).toEqual(['t-x', 't-y'])
    expect(after.settings).toEqual(before.settings)
  })
})

// ============================================================================
// hasUnrefreshedEdits
// ============================================================================

describe('hasUnrefreshedEdits', () => {
  it('returns false when tree is null', () => {
    expect(hasUnrefreshedEdits(null)).toBe(false)
  })

  it('returns false when no node is edited or draft', () => {
    const tree: ConversationTree = mkTree('root', [mkRoot('root'), mkSend('send-1', 'root')])
    expect(hasUnrefreshedEdits(tree)).toBe(false)
  })

  it('returns true when any node has state="edited"', () => {
    const tree: ConversationTree = mkTree('root', [
      mkRoot('root'),
      mkSend('send-1', 'root', undefined, { state: 'edited' }),
    ])
    expect(hasUnrefreshedEdits(tree)).toBe(true)
  })

  it('returns true when any node has state="draft"', () => {
    const tree: ConversationTree = mkTree('root', [
      mkRoot('root'),
      mkSend('send-1', 'root', undefined, { state: 'draft' }),
    ])
    expect(hasUnrefreshedEdits(tree)).toBe(true)
  })

  it('returns false when only failed / cancelled / stale states are present', () => {
    const tree: ConversationTree = mkTree('root', [
      mkRoot('root'),
      mkSend('send-1', 'root', undefined, { state: 'failed' }),
      mkSend('send-2', 'root', undefined, { state: 'cancelled' }),
    ])
    // These are runner-driven failure states, not operator edits.
    expect(hasUnrefreshedEdits(tree)).toBe(false)
  })
})

// ============================================================================
// countUnrefreshedEdits
// ============================================================================

describe('countUnrefreshedEdits', () => {
  it('returns 0 when tree is null', () => {
    expect(countUnrefreshedEdits(null)).toBe(0)
  })

  it('returns 0 when no node is edited or draft', () => {
    const tree: ConversationTree = mkTree('root', [mkRoot('root'), mkSend('send-1', 'root')])
    expect(countUnrefreshedEdits(tree)).toBe(0)
  })

  it('counts edited + draft nodes only', () => {
    const tree: ConversationTree = mkTree('root', [
      mkRoot('root'),
      mkSend('send-1', 'root', undefined, { state: 'edited' }),
      mkSend('send-2', 'root', undefined, { state: 'draft' }),
      mkSend('send-3', 'root', undefined, { state: 'failed' }),
    ])
    expect(countUnrefreshedEdits(tree)).toBe(2)
  })
})

// ============================================================================
// URL fragment helpers
// ============================================================================

describe('parseTreeIdFromUrlFragment', () => {
  it('returns null when the fragment is empty', () => {
    expect(parseTreeIdFromUrlFragment('')).toBeNull()
    expect(parseTreeIdFromUrlFragment('#')).toBeNull()
  })

  it('returns the conversation_tree_id when present in the fragment', () => {
    expect(parseTreeIdFromUrlFragment('#conversation_tree_id=abc-123')).toBe('abc-123')
    expect(parseTreeIdFromUrlFragment('conversation_tree_id=abc-123')).toBe('abc-123')
    // Mixed with other params.
    expect(parseTreeIdFromUrlFragment('#foo=bar&conversation_tree_id=abc-123&baz=qux')).toBe('abc-123')
  })

  it('returns null when the fragment does not contain conversation_tree_id', () => {
    expect(parseTreeIdFromUrlFragment('#foo=bar')).toBeNull()
    expect(parseTreeIdFromUrlFragment('#conversation_id=abc')).toBeNull()
  })
})

describe('serializeTreeIdToUrlFragment', () => {
  it('returns an empty string for null', () => {
    expect(serializeTreeIdToUrlFragment(null)).toBe('')
  })

  it('returns the canonical #conversation_tree_id=X form for a non-null id', () => {
    expect(serializeTreeIdToUrlFragment('abc-123')).toBe('#conversation_tree_id=abc-123')
  })

  it('round-trips through parseTreeIdFromUrlFragment', () => {
    const id = 'tree-uuid-here'
    const frag = serializeTreeIdToUrlFragment(id)
    expect(parseTreeIdFromUrlFragment(frag)).toBe(id)
  })
})

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { act, renderHook } from '@testing-library/react'

import { useWorkspacePersistence, type WorkspacePersistenceDeps } from './useWorkspacePersistence'
import {
  DEFAULT_WORKSPACE_SETTINGS,
  SCHEMA_VERSION,
  STORAGE_KEYS,
} from '../../runner/workspacePersistence'
import { mkRoot, mkSend, mkTree, treeId } from '../../runner/testHelpers'
import type { ConversationTree, WorkspaceSettings } from '../../runner/treeTypes'

class MemoryStorage implements Storage {
  private m = new Map<string, string>()
  get length(): number {
    return this.m.size
  }
  clear(): void {
    this.m.clear()
  }
  getItem(key: string): string | null {
    return this.m.get(key) ?? null
  }
  key(index: number): string | null {
    return Array.from(this.m.keys())[index] ?? null
  }
  removeItem(key: string): void {
    this.m.delete(key)
  }
  setItem(key: string, value: string): void {
    this.m.set(key, value)
  }
}

function mkTreeA(id: string): ConversationTree {
  return mkTree('root', [mkRoot('root'), mkSend('send-1', 'root')], { id })
}

function makeDeps(opts: { initialHash?: string; storage?: Storage } = {}): {
  deps: WorkspacePersistenceDeps
  getHashCalls: string[]
  setHashCalls: string[]
  beforeUnloadHandlers: Array<(e: BeforeUnloadEvent) => void>
} {
  const hash = { value: opts.initialHash ?? '' }
  const getHashCalls: string[] = []
  const setHashCalls: string[] = []
  const beforeUnloadHandlers: Array<(e: BeforeUnloadEvent) => void> = []
  const deps: WorkspacePersistenceDeps = {
    storage: opts.storage ?? new MemoryStorage(),
    getHash: () => {
      getHashCalls.push(hash.value)
      return hash.value
    },
    setHash: (next) => {
      setHashCalls.push(next)
      hash.value = next
    },
    addBeforeUnloadListener: (handler) => {
      beforeUnloadHandlers.push(handler)
      return () => {
        const idx = beforeUnloadHandlers.indexOf(handler)
        if (idx >= 0) beforeUnloadHandlers.splice(idx, 1)
      }
    },
    setTimeoutFn: ((cb: () => void, ms?: number) => setTimeout(cb, ms)) as unknown as WorkspacePersistenceDeps['setTimeoutFn'],
    clearTimeoutFn: clearTimeout,
  }
  return { deps, getHashCalls, setHashCalls, beforeUnloadHandlers }
}

describe('useWorkspacePersistence - boot', () => {
  it('loads workspace state from storage and parses URL fragment once', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.schemaVersion, String(SCHEMA_VERSION))
    storage.setItem(STORAGE_KEYS.recentTreeIds, JSON.stringify(['t-1', 't-2']))
    storage.setItem(
      STORAGE_KEYS.settings,
      JSON.stringify({
        reflogCapPerNode: 99,
        confirmThresholdCount: 7,
        suppressConfirmModalThisSession: true,
      }),
    )
    const { deps } = makeDeps({ initialHash: '#conversation_tree_id=t-2', storage })

    const { result } = renderHook(() =>
      useWorkspacePersistence({
        tree: null,
        recentTreeIds: [],
        settings: DEFAULT_WORKSPACE_SETTINGS,
        deps,
      }),
    )

    expect(result.current.boot.schemaWiped).toBe(false)
    expect(result.current.boot.treeIdFromFragment).toBe('t-2')
    expect(result.current.boot.workspace.recentTreeIds).toEqual(['t-1', 't-2'])
    expect(result.current.boot.workspace.settings).toEqual({
      reflogCapPerNode: 99,
      confirmThresholdCount: 7,
      suppressConfirmModalThisSession: true,
    })
  })

  it('wipes pyrit keys when schema mismatches and reports schemaWiped=true', () => {
    const storage = new MemoryStorage()
    storage.setItem(STORAGE_KEYS.schemaVersion, '0')
    storage.setItem(STORAGE_KEYS.recentTreeIds, JSON.stringify(['t-stale']))
    storage.setItem('pyrit.workspace.parentSourceConversationId.t-stale', 'conv-1')
    const { deps } = makeDeps({ storage })

    const { result } = renderHook(() =>
      useWorkspacePersistence({
        tree: null,
        recentTreeIds: [],
        settings: DEFAULT_WORKSPACE_SETTINGS,
        deps,
      }),
    )

    expect(result.current.boot.schemaWiped).toBe(true)
    expect(storage.getItem(STORAGE_KEYS.recentTreeIds)).toBeNull()
    expect(storage.getItem('pyrit.workspace.parentSourceConversationId.t-stale')).toBeNull()
    expect(storage.getItem(STORAGE_KEYS.schemaVersion)).toBe(String(SCHEMA_VERSION))
  })
})

describe('useWorkspacePersistence - URL fragment write', () => {
  it('writes URL fragment immediately when tree.id changes', () => {
    const { deps, setHashCalls } = makeDeps()
    const t1 = mkTreeA('t-1')
    const t2 = mkTreeA('t-2')

    const { rerender } = renderHook(
      ({ tree }: { tree: ConversationTree | null }) =>
        useWorkspacePersistence({
          tree,
          recentTreeIds: [treeId('t-1')],
          settings: DEFAULT_WORKSPACE_SETTINGS,
          deps,
        }),
      { initialProps: { tree: t1 as ConversationTree | null } },
    )

    expect(setHashCalls[setHashCalls.length - 1]).toBe('#conversation_tree_id=t-1')

    rerender({ tree: t2 })
    expect(setHashCalls[setHashCalls.length - 1]).toBe('#conversation_tree_id=t-2')

    rerender({ tree: null })
    expect(setHashCalls[setHashCalls.length - 1]).toBe('')
  })
})

describe('useWorkspacePersistence - debounced storage write', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.runOnlyPendingTimers()
    jest.useRealTimers()
  })

  it('debounces writes for recentTreeIds/settings by 500ms', () => {
    const storage = new MemoryStorage()
    const { deps } = makeDeps({ storage })
    const settingsA: WorkspaceSettings = {
      reflogCapPerNode: 50,
      confirmThresholdCount: 20,
      suppressConfirmModalThisSession: false,
    }
    const settingsB: WorkspaceSettings = {
      reflogCapPerNode: 60,
      confirmThresholdCount: 10,
      suppressConfirmModalThisSession: true,
    }

    const { rerender } = renderHook(
      ({ ids, settings }: { ids: string[]; settings: WorkspaceSettings }) =>
        useWorkspacePersistence({
          tree: null,
          recentTreeIds: ids.map((x) => treeId(x)),
          settings,
          deps,
        }),
      { initialProps: { ids: ['t-1'], settings: settingsA } },
    )

    // Before debounce window, nothing written yet.
    expect(storage.getItem(STORAGE_KEYS.recentTreeIds)).toBeNull()

    // Update quickly before timer fires; only latest should persist.
    rerender({ ids: ['t-2', 't-3'], settings: settingsB })

    act(() => {
      jest.advanceTimersByTime(499)
    })
    expect(storage.getItem(STORAGE_KEYS.recentTreeIds)).toBeNull()

    act(() => {
      jest.advanceTimersByTime(1)
    })

    expect(JSON.parse(storage.getItem(STORAGE_KEYS.recentTreeIds) ?? '')).toEqual(['t-2', 't-3'])
    expect(JSON.parse(storage.getItem(STORAGE_KEYS.settings) ?? '')).toEqual(settingsB)
  })
})

describe('useWorkspacePersistence - beforeunload dirty guard', () => {
  it('registers beforeunload and blocks unload when tree has edited/draft nodes', () => {
    const { deps, beforeUnloadHandlers } = makeDeps()
    const dirtyTree = mkTree('root', [
      mkRoot('root'),
      mkSend('send-1', 'root', undefined, { state: 'edited' }),
    ])

    renderHook(() =>
      useWorkspacePersistence({
        tree: dirtyTree,
        recentTreeIds: [],
        settings: DEFAULT_WORKSPACE_SETTINGS,
        deps,
      }),
    )

    expect(beforeUnloadHandlers).toHaveLength(1)
    const event = { preventDefault: jest.fn(), returnValue: '' } as unknown as BeforeUnloadEvent
    beforeUnloadHandlers[0](event)
    expect(event.preventDefault).toHaveBeenCalled()
    expect(event.returnValue).toBe('')
  })

  it('allows unload when tree has no unrefreshed edits', () => {
    const { deps, beforeUnloadHandlers } = makeDeps()
    const cleanTree = mkTreeA('t-clean')

    renderHook(() =>
      useWorkspacePersistence({
        tree: cleanTree,
        recentTreeIds: [],
        settings: DEFAULT_WORKSPACE_SETTINGS,
        deps,
      }),
    )

    const event = { preventDefault: jest.fn(), returnValue: '' } as unknown as BeforeUnloadEvent
    beforeUnloadHandlers[0](event)
    expect(event.preventDefault).not.toHaveBeenCalled()
  })
}
)

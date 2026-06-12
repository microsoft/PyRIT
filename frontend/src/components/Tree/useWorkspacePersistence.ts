// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * React hook that composes the pure workspace-persistence helpers
 * from runner/workspacePersistence.ts against browser APIs.
 *
 * Responsibilities (PR7f.2):
 * - Boot once: schema wipe check + storage load + URL fragment parse.
 * - Persist recentTreeIds + settings to sessionStorage (debounced).
 * - Persist current tree id to URL fragment immediately on change.
 * - Install beforeunload guard for unrefreshed edits.
 */

import { useEffect, useMemo, useState } from 'react'

import {
  hasUnrefreshedEdits,
  loadWorkspaceFromStorage,
  parseTreeIdFromUrlFragment,
  serializeTreeIdToUrlFragment,
  wipeIfSchemaMismatch,
  writeWorkspaceToStorage,
} from '../../runner/workspacePersistence'
import type { ConversationTree, ConversationTreeId, WorkspaceSettings } from '../../runner/treeTypes'
import type { PersistedWorkspace } from '../../runner/workspacePersistence'

export interface WorkspacePersistenceDeps {
  storage: Storage
  getHash: () => string
  setHash: (nextHash: string) => void
  addBeforeUnloadListener: (handler: (event: BeforeUnloadEvent) => void) => () => void
  setTimeoutFn: (cb: () => void, ms: number) => ReturnType<typeof setTimeout>
  clearTimeoutFn: (handle: ReturnType<typeof setTimeout>) => void
}

export interface UseWorkspacePersistenceArgs {
  tree: ConversationTree | null
  recentTreeIds: ConversationTreeId[]
  settings: WorkspaceSettings
  debounceMs?: number
  deps?: WorkspacePersistenceDeps
}

export interface WorkspacePersistenceBootState {
  schemaWiped: boolean
  treeIdFromFragment: string | null
  workspace: PersistedWorkspace
}

export interface UseWorkspacePersistenceResult {
  boot: WorkspacePersistenceBootState
}

function defaultDeps(): WorkspacePersistenceDeps {
  return {
    storage: window.sessionStorage,
    getHash: () => window.location.hash,
    setHash: (nextHash: string) => {
      const next = nextHash.startsWith('#') || nextHash === '' ? nextHash : `#${nextHash}`
      const { pathname, search } = window.location
      const url = `${pathname}${search}${next}`
      window.history.replaceState(window.history.state, '', url)
    },
    addBeforeUnloadListener: (handler) => {
      window.addEventListener('beforeunload', handler)
      return () => window.removeEventListener('beforeunload', handler)
    },
    setTimeoutFn: (cb, ms) => setTimeout(cb, ms),
    clearTimeoutFn: (h) => clearTimeout(h),
  }
}

function computeBootState(deps: WorkspacePersistenceDeps): WorkspacePersistenceBootState {
  const schemaWiped = wipeIfSchemaMismatch(deps.storage)
  const workspace = loadWorkspaceFromStorage(deps.storage)
  const treeIdFromFragment = parseTreeIdFromUrlFragment(deps.getHash())
  return { schemaWiped, workspace, treeIdFromFragment }
}

export function useWorkspacePersistence({
  tree,
  recentTreeIds,
  settings,
  debounceMs = 500,
  deps,
}: UseWorkspacePersistenceArgs): UseWorkspacePersistenceResult {
  const resolvedDeps = useMemo(() => deps ?? defaultDeps(), [deps])

  // Boot phase runs once per hook instance.
  const [boot] = useState<WorkspacePersistenceBootState>(() => computeBootState(resolvedDeps))

  // URL fragment writes are immediate on tree-id changes (reviewer decision).
  useEffect(() => {
    resolvedDeps.setHash(serializeTreeIdToUrlFragment(tree?.id ?? null))
  }, [resolvedDeps, tree?.id])

  // sessionStorage writes are debounced.
  useEffect(() => {
    const handle = resolvedDeps.setTimeoutFn(() => {
      writeWorkspaceToStorage(resolvedDeps.storage, { recentTreeIds, settings })
    }, debounceMs)
    return () => resolvedDeps.clearTimeoutFn(handle)
  }, [debounceMs, recentTreeIds, resolvedDeps, settings])

  // Dirty-edit unload guard.
  useEffect(() => {
    const dispose = resolvedDeps.addBeforeUnloadListener((event) => {
      if (!hasUnrefreshedEdits(tree)) return
      event.preventDefault()
      event.returnValue = ''
    })
    return dispose
  }, [resolvedDeps, tree])

  return { boot }
}

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  childPageFromNode,
  mergeRootPage,
  previewHasAllChildren,
  type HierarchyChildPage,
} from '@/components/Scenarios/scenarioHierarchy'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  ScenarioHierarchyDiagnostics,
  ScenarioHierarchyGroup,
  ScenarioHierarchyNode,
  ScenarioProgressCounts,
  ScenarioRunState,
} from '@/types'

export const HIERARCHY_MAX_NODES = 1000
export const HIERARCHY_CHILD_PAGE_SIZE = 200

export interface HierarchyRootState {
  readonly status: 'loading' | 'ready' | 'error'
  /** Run state when the hierarchy was loaded; a run that is still going keeps adding results. */
  readonly runStatus: ScenarioRunState | null
  readonly groups: ScenarioHierarchyGroup[]
  readonly overallCounts: ScenarioProgressCounts | null
  readonly diagnostics: ScenarioHierarchyDiagnostics | null
  /** The preview left out deferred descendants, deep levels or results it could not place. */
  readonly truncated: boolean
  readonly nextCursor: string | null
  readonly loadingMore: boolean
  readonly error: string | null
  /** The root cursor no longer matches the saved run; paging has to restart. */
  readonly staleCursor: boolean
}

export interface HierarchyChildrenState {
  readonly pages: HierarchyChildPage[]
  readonly childrenCount: number | null
  readonly nextCursor: string | null
  readonly loading: boolean
  readonly error: string | null
  /** The child cursor no longer matches the parent's saved references; paging has to restart. */
  readonly staleCursor: boolean
}

const INITIAL_ROOTS: HierarchyRootState = {
  status: 'loading',
  runStatus: null,
  groups: [],
  overallCounts: null,
  diagnostics: null,
  truncated: false,
  nextCursor: null,
  loadingMore: false,
  error: null,
  staleCursor: false,
}

const EMPTY_CHILDREN: HierarchyChildrenState = {
  pages: [],
  childrenCount: null,
  nextCursor: null,
  loading: false,
  error: null,
  staleCursor: false,
}

export interface UseScenarioResultHierarchyResult {
  readonly roots: HierarchyRootState
  readonly children: ReadonlyMap<string, HierarchyChildrenState>
  readonly expanded: ReadonlySet<string>
  /** Expands or collapses a node; expanding shows its children, loading them if needed. */
  readonly toggleNode: (key: string, node: ScenarioHierarchyNode) => void
  readonly reloadRoots: () => void
  readonly loadMoreRoots: () => void
  /** Shows a node's children, from the preview when it is complete and otherwise from the API. */
  readonly openChildren: (key: string, node: ScenarioHierarchyNode) => void
  readonly loadMoreChildren: (key: string, node: ScenarioHierarchyNode) => void
  readonly restartChildren: (key: string, node: ScenarioHierarchyNode) => void
}

/** Mount a keyed owner per run so saved relationship caches cannot carry over to another run. */
export function useScenarioResultHierarchy(
  scenarioResultId: string,
  enabled: boolean = true,
): UseScenarioResultHierarchyResult {
  const [roots, setRoots] = useState<HierarchyRootState>(INITIAL_ROOTS)
  const [children, setChildren] = useState<ReadonlyMap<string, HierarchyChildrenState>>(new Map())
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const [rootEpoch, setRootEpoch] = useState(0)
  const controllers = useRef(new Set<AbortController>())
  /** The child request in flight for each node, so a restart can drop a page that is still loading. */
  const childControllers = useRef(new Map<string, AbortController>())

  const track = useCallback((): AbortController => {
    const controller = new AbortController()
    controllers.current.add(controller)
    return controller
  }, [])

  const release = useCallback((controller: AbortController) => {
    controllers.current.delete(controller)
  }, [])

  const resetHierarchy = useCallback((): void => {
    for (const controller of controllers.current) controller.abort()
    controllers.current.clear()
    childControllers.current.clear()
    setRoots(INITIAL_ROOTS)
    setChildren(new Map())
    setExpanded(new Set())
  }, [])

  useEffect(() => {
    const active = controllers.current
    return () => {
      for (const controller of active) {
        controller.abort()
      }
      active.clear()
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    const controller = track()
    scenariosApi
      .getRunHierarchy(scenarioResultId, { max_nodes: HIERARCHY_MAX_NODES }, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return
        setRoots({
          status: 'ready',
          runStatus: page.status,
          groups: mergeRootPage([], page),
          overallCounts: page.overall_counts,
          diagnostics: page.diagnostics,
          truncated: page.truncated,
          nextCursor: page.has_more ? page.next_cursor ?? null : null,
          loadingMore: false,
          error: null,
          staleCursor: false,
        })
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) {
          return
        }
        setRoots({ ...INITIAL_ROOTS, status: 'error', error: toApiError(err).detail })
      })
      .finally(() => release(controller))
    return () => controller.abort()
  }, [scenarioResultId, enabled, rootEpoch, track, release])

  const reloadRoots = useCallback(() => {
    resetHierarchy()
    setRootEpoch((epoch) => epoch + 1)
  }, [resetHierarchy])

  const loadMoreRoots = useCallback(() => {
    const cursor = roots.nextCursor
    if (!cursor || roots.loadingMore) {
      return
    }
    const controller = track()
    setRoots((state) => ({ ...state, loadingMore: true, error: null, staleCursor: false }))
    scenariosApi
      .getRunHierarchy(scenarioResultId, { max_nodes: HIERARCHY_MAX_NODES, cursor }, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return
        setRoots((state) => ({
          ...state,
          runStatus: page.status,
          groups: mergeRootPage(state.groups, page),
          overallCounts: page.overall_counts,
          diagnostics: page.diagnostics,
          truncated: state.truncated || page.truncated,
          nextCursor: page.has_more ? page.next_cursor ?? null : null,
          loadingMore: false,
        }))
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) {
          return
        }
        const error = toApiError(err)
        setRoots((state) => ({
          ...state,
          loadingMore: false,
          error: error.detail,
          staleCursor: error.status === 400,
        }))
      })
      .finally(() => release(controller))
  }, [roots.nextCursor, roots.loadingMore, scenarioResultId, track, release])

  const fetchChildren = useCallback(
    (key: string, node: ScenarioHierarchyNode, cursor: string | null) => {
      childControllers.current.get(key)?.abort()
      const controller = track()
      childControllers.current.set(key, controller)
      setChildren((map) => {
        const next = new Map(map)
        const current = cursor ? map.get(key) ?? EMPTY_CHILDREN : EMPTY_CHILDREN
        next.set(key, { ...current, loading: true, error: null, staleCursor: false })
        return next
      })
      scenariosApi
        .getRunHierarchyNode(
          scenarioResultId,
          node.attack_result_id,
          cursor
            ? { limit: HIERARCHY_CHILD_PAGE_SIZE, children_cursor: cursor }
            : { limit: HIERARCHY_CHILD_PAGE_SIZE },
          controller.signal,
        )
        .then((page) => {
          if (controller.signal.aborted) return
          setChildren((map) => {
            const next = new Map(map)
            const current = map.get(key) ?? EMPTY_CHILDREN
            next.set(key, {
              pages: [...current.pages, childPageFromNode(page)],
              childrenCount: page.children_count ?? null,
              nextCursor: page.next_children_cursor ?? null,
              loading: false,
              error: null,
              staleCursor: false,
            })
            return next
          })
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) {
            return
          }
          const error = toApiError(err)
          setChildren((map) => {
            const next = new Map(map)
            const current = map.get(key) ?? EMPTY_CHILDREN
            next.set(key, {
              ...current,
              loading: false,
              error: error.detail,
              staleCursor: cursor !== null && error.status === 400,
            })
            return next
          })
        })
        .finally(() => {
          release(controller)
          if (childControllers.current.get(key) === controller) {
            childControllers.current.delete(key)
          }
        })
    },
    [scenarioResultId, track, release],
  )

  const openChildren = useCallback(
    (key: string, node: ScenarioHierarchyNode) => {
      const existing = children.get(key)
      if (existing && (existing.pages.length > 0 || existing.loading)) {
        return
      }
      if (previewHasAllChildren(node)) {
        setChildren((map) => {
          const next = new Map(map)
          next.set(key, {
            ...EMPTY_CHILDREN,
            pages: [childPageFromNode(node)],
            childrenCount: node.children_count ?? node.child_attack_result_ids.length,
          })
          return next
        })
        return
      }
      fetchChildren(key, node, null)
    },
    [children, fetchChildren],
  )

  const loadMoreChildren = useCallback(
    (key: string, node: ScenarioHierarchyNode) => {
      const state = children.get(key)
      if (!state?.nextCursor || state.loading) {
        return
      }
      fetchChildren(key, node, state.nextCursor)
    },
    [children, fetchChildren],
  )

  const restartChildren = useCallback(
    (key: string, node: ScenarioHierarchyNode) => {
      // Descendants loaded under the old child pages may no longer match; forget them too.
      const prefix = `${key}/`
      for (const [childKey, controller] of childControllers.current) {
        if (childKey.startsWith(prefix)) {
          controller.abort()
          childControllers.current.delete(childKey)
          release(controller)
        }
      }
      setChildren((map) => new Map([...map].filter(([childKey]) => !childKey.startsWith(prefix))))
      setExpanded((keys) => new Set([...keys].filter((childKey) => !childKey.startsWith(prefix))))
      fetchChildren(key, node, null)
    },
    [fetchChildren, release],
  )

  const toggleNode = useCallback(
    (key: string, node: ScenarioHierarchyNode) => {
      if (expanded.has(key)) {
        setExpanded((keys) => {
          const next = new Set(keys)
          next.delete(key)
          return next
        })
        return
      }
      setExpanded((keys) => new Set(keys).add(key))
      openChildren(key, node)
    },
    [expanded, openChildren],
  )

  return {
    roots,
    children,
    expanded,
    toggleNode,
    reloadRoots,
    loadMoreRoots,
    openChildren,
    loadMoreChildren,
    restartChildren,
  }
}

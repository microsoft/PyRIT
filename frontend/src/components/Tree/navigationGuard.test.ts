// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `guardedNavigate` — the pure decision helper that routes a
 * view change through the tree's dirty-edit guard when leaving the tree
 * view. Keeps App.tsx's navigation glue thin + testable per the agreed
 * coverage approach.
 */

import { guardedNavigate } from './navigationGuard'
import { mkRoot, mkSend, mkTree } from '../../runner/testHelpers'
import type { ConversationTree } from '../../runner/treeTypes'

function dirtyTree(): ConversationTree {
  return mkTree('root', [mkRoot('root'), mkSend('send-1', 'root', undefined, { state: 'edited' })])
}

describe('guardedNavigate', () => {
  it('routes leaving the tree view through the guard', () => {
    const navigate = jest.fn()
    const guardedSwap = jest.fn()
    guardedNavigate({
      currentView: 'tree',
      target: 'history',
      tree: dirtyTree(),
      guardedSwap,
      navigate,
    })
    // The guard owns the decision; navigate is deferred into the guard's swap.
    expect(guardedSwap).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
    // The deferred swap navigates when invoked.
    const swap = guardedSwap.mock.calls[0][1] as () => void
    swap()
    expect(navigate).toHaveBeenCalledWith('history')
  })

  it('navigates directly when NOT currently in the tree view', () => {
    const navigate = jest.fn()
    const guardedSwap = jest.fn()
    guardedNavigate({
      currentView: 'chat',
      target: 'history',
      tree: null,
      guardedSwap,
      navigate,
    })
    expect(navigate).toHaveBeenCalledWith('history')
    expect(guardedSwap).not.toHaveBeenCalled()
  })

  it('navigates directly when re-selecting the tree view (target === tree)', () => {
    const navigate = jest.fn()
    const guardedSwap = jest.fn()
    guardedNavigate({
      currentView: 'tree',
      target: 'tree',
      tree: dirtyTree(),
      guardedSwap,
      navigate,
    })
    expect(navigate).toHaveBeenCalledWith('tree')
    expect(guardedSwap).not.toHaveBeenCalled()
  })

  it('navigates directly when no guard is available (host not mounted yet)', () => {
    const navigate = jest.fn()
    guardedNavigate({
      currentView: 'tree',
      target: 'home',
      tree: dirtyTree(),
      guardedSwap: null,
      navigate,
    })
    expect(navigate).toHaveBeenCalledWith('home')
  })
})

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `useDirtyEditModal` — the in-app tree-swap guard per spec
 * 01 §13.1a. When the current tree has unrefreshed edits (`edited` /
 * `draft` nodes), `guardedSwap(tree, swap)` defers `swap()` behind a
 * confirm modal; on "Discard and continue" it runs, on "Cancel" it's
 * abandoned. With no unrefreshed edits, `swap()` runs synchronously and
 * no modal renders.
 *
 * Distinct from the §9.4.2 beforeunload guard (PR7f.2): that catches
 * reload/tab-close; this catches in-app swaps (Switch tree / new / close).
 */

import { act, fireEvent, render, screen, within } from '@testing-library/react'

import { useDirtyEditModal } from './useDirtyEditModal'
import { mkRoot, mkSend, mkTree } from '../../runner/testHelpers'
import type { ConversationTree } from '../../runner/treeTypes'

function mountHook() {
  let latest: ReturnType<typeof useDirtyEditModal> | null = null
  function Harness() {
    latest = useDirtyEditModal()
    return <>{latest.modalElement}</>
  }
  render(<Harness />)
  if (latest === null) throw new Error('Harness did not render')
  return {
    get current() {
      if (latest === null) throw new Error('hook unmounted')
      return latest
    },
  }
}

function cleanTree(id = 't-clean'): ConversationTree {
  return mkTree('root', [mkRoot('root'), mkSend('send-1', 'root')], { id })
}

function dirtyTree(id = 't-dirty'): ConversationTree {
  return mkTree('root', [
    mkRoot('root'),
    mkSend('send-1', 'root', undefined, { state: 'edited' }),
  ], { id })
}

// ============================================================================
// Clean tree — swap runs synchronously, no modal
// ============================================================================

describe('useDirtyEditModal — clean tree', () => {
  it('runs swap() synchronously and renders no modal when the tree is clean', () => {
    const h = mountHook()
    const swap = jest.fn()
    act(() => {
      h.current.guardedSwap(cleanTree(), swap)
    })
    expect(swap).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('runs swap() synchronously when tree is null (greenfield)', () => {
    const h = mountHook()
    const swap = jest.fn()
    act(() => {
      h.current.guardedSwap(null, swap)
    })
    expect(swap).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// ============================================================================
// Dirty tree — modal gates the swap
// ============================================================================

describe('useDirtyEditModal — dirty tree', () => {
  it('shows the modal and defers swap() when the tree has unrefreshed edits', () => {
    const h = mountHook()
    const swap = jest.fn()
    act(() => {
      h.current.guardedSwap(dirtyTree(), swap)
    })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(swap).not.toHaveBeenCalled()
  })

  it('runs swap() and dismisses on "Discard and continue"', () => {
    const h = mountHook()
    const swap = jest.fn()
    act(() => {
      h.current.guardedSwap(dirtyTree(), swap)
    })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /discard/i }))
    expect(swap).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('does NOT run swap() and dismisses on "Cancel"', () => {
    const h = mountHook()
    const swap = jest.fn()
    act(() => {
      h.current.guardedSwap(dirtyTree(), swap)
    })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /^cancel$/i }))
    expect(swap).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('surfaces the unrefreshed-edit count in the modal body', () => {
    const h = mountHook()
    const tree = mkTree('root', [
      mkRoot('root'),
      mkSend('send-1', 'root', undefined, { state: 'edited' }),
      mkSend('send-2', 'root', undefined, { state: 'draft' }),
    ])
    act(() => {
      h.current.guardedSwap(tree, jest.fn())
    })
    // 2 unrefreshed edits (one edited + one draft).
    expect(screen.getByRole('dialog').textContent).toMatch(/2/)
  })
})

// ============================================================================
// Concurrent guardedSwap — second call ignored while pending
// ============================================================================

describe('useDirtyEditModal — concurrent guard', () => {
  it('ignores a second guardedSwap while a decision is pending (modal stays on first)', () => {
    const h = mountHook()
    const firstSwap = jest.fn()
    const secondSwap = jest.fn()
    act(() => {
      h.current.guardedSwap(dirtyTree('t-a'), firstSwap)
    })
    act(() => {
      h.current.guardedSwap(dirtyTree('t-b'), secondSwap)
    })
    // Resolve the (first) pending decision.
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: /discard/i }))
    expect(firstSwap).toHaveBeenCalledTimes(1)
    expect(secondSwap).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

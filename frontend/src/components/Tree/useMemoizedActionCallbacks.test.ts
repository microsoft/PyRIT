// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `useMemoizedActionCallbacks` — the TreeCanvas-boundary hook
 * that wraps the host-supplied `ActionCallbacks` bag in a useMemo keyed
 * on individual callback identities. The PR5h reviewer's Finding B:
 * without this, a host passing a new bag identity each render (which is
 * easy to do accidentally — e.g., inline object literal in JSX) forces
 * every card to re-render through the ActionCallbacksContext. PR6c's
 * wave-state subscription will make that an actual perf cliff; pin the
 * contract now.
 *
 * Pinned:
 *   - same bag identity + same callbacks → same context value
 *   - NEW bag identity + same callback identities → same context value
 *     (the key win)
 *   - same bag identity OR new bag + DIFFERENT callback → new context
 *     value (so card props that depend on the callback do change)
 *   - undefined input → null context value (consistent with the
 *     TreeCanvas null-default convention)
 */

import { renderHook } from '@testing-library/react'

import { useMemoizedActionCallbacks } from './useMemoizedActionCallbacks'
import type { ActionCallbacks } from './actionRail'

describe('useMemoizedActionCallbacks', () => {
  it('returns null when input is undefined', () => {
    const { result } = renderHook(() => useMemoizedActionCallbacks(undefined))
    expect(result.current).toBeNull()
  })

  it('returns the same value across renders when bag identity AND callbacks are stable', () => {
    const onRefresh = jest.fn()
    const callbacks: ActionCallbacks = { onRefresh }
    const { result, rerender } = renderHook(
      ({ cb }) => useMemoizedActionCallbacks(cb),
      { initialProps: { cb: callbacks } },
    )
    const first = result.current
    rerender({ cb: callbacks })
    expect(result.current).toBe(first)
  })

  it('returns the same value across renders when bag identity changes but callbacks are stable (the key win)', () => {
    const onRefresh = jest.fn()
    const onBranch = jest.fn()
    const { result, rerender } = renderHook(
      ({ cb }) => useMemoizedActionCallbacks(cb),
      { initialProps: { cb: { onRefresh, onBranch } as ActionCallbacks } },
    )
    const first = result.current
    // New bag literal each rerender — same callbacks inside.
    rerender({ cb: { onRefresh, onBranch } })
    rerender({ cb: { onRefresh, onBranch } })
    expect(result.current).toBe(first)
  })

  it('returns a new value when a callback identity changes (so cards see the update)', () => {
    const onRefreshA = jest.fn()
    const onRefreshB = jest.fn()
    const { result, rerender } = renderHook(
      ({ cb }) => useMemoizedActionCallbacks(cb),
      { initialProps: { cb: { onRefresh: onRefreshA } as ActionCallbacks } },
    )
    const first = result.current
    rerender({ cb: { onRefresh: onRefreshB } })
    expect(result.current).not.toBe(first)
    expect(result.current?.onRefresh).toBe(onRefreshB)
  })

  it('returns a new value when a new callback is added to the bag', () => {
    const onRefresh = jest.fn()
    const onBranch = jest.fn()
    const { result, rerender } = renderHook(
      ({ cb }) => useMemoizedActionCallbacks(cb),
      { initialProps: { cb: { onRefresh } as ActionCallbacks } },
    )
    const first = result.current
    rerender({ cb: { onRefresh, onBranch } })
    expect(result.current).not.toBe(first)
    expect(result.current?.onBranch).toBe(onBranch)
  })

  it('returns a new value when a callback is removed from the bag', () => {
    const onRefresh = jest.fn()
    const onBranch = jest.fn()
    const { result, rerender } = renderHook(
      ({ cb }) => useMemoizedActionCallbacks(cb),
      { initialProps: { cb: { onRefresh, onBranch } as ActionCallbacks } },
    )
    const first = result.current
    rerender({ cb: { onRefresh } })
    expect(result.current).not.toBe(first)
    expect(result.current?.onBranch).toBeUndefined()
  })
})

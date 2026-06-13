// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * TreeCanvas-boundary defensive memoization for the host-supplied
 * `ActionCallbacks` bag. Without this, a host passing a fresh object
 * literal each render (a common pattern even when the underlying
 * callbacks are stable) forces every card to re-render through the
 * ActionCallbacksContext. PR6c's wave-state subscription will fire
 * the host's `setState` multiple times per second during an active
 * wave; without per-callback-identity memoization the canvas re-runs
 * every card's render path for every WaveEvent tick.
 *
 * Contract: returns a stable reference when EVERY individual callback
 * has a stable reference, regardless of the input bag's identity. A
 * NEW reference is returned only when a callback changes identity, is
 * added, or is removed. Undefined input maps to `null` to match the
 * `ActionCallbacksContext`'s null-default convention.
 *
 * V1.0 deliberately does not memoize the callbacks themselves —
 * stable refs are the host's responsibility (typically via
 * useCallback). This hook only protects against the "fresh bag,
 * stable callbacks" re-render trap.
 */

import { useMemo } from 'react'

import type { ActionCallbacks } from './actionRail'

export function useMemoizedActionCallbacks(
  callbacks: ActionCallbacks | undefined,
): ActionCallbacks | null {
  // useMemo deps are reference-compared via Object.is; listing each
  // callback by identity means a new bag literal with the same inner
  // refs returns the cached value. When a single callback changes,
  // its dep entry differs and useMemo recomputes — yielding the new
  // bag passed in.
  //
  // `isPresent` distinguishes `undefined` from a defined-but-empty
  // bag — both leave every callback dep as undefined, but the closed-
  // over `callbacks` value still differs. Without this, a
  // defined-then-undefined transition would silently keep returning
  // the old bag.
  const isPresent = callbacks !== undefined
  return useMemo(
    () => callbacks ?? null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      isPresent,
      callbacks?.onRefresh,
      callbacks?.onBranch,
      callbacks?.onDelete,
      callbacks?.onOpenLinear,
      callbacks?.onEdgeInsert,
      callbacks?.onAppendChild,
      callbacks?.onCreateFanFromNode,
      callbacks?.onPickFanChild,
      callbacks?.onEditUserTurnText,
      callbacks?.onEditRootPromptParams,
      callbacks?.onSetUserTurnConverterPipeline,
    ],
  )
}

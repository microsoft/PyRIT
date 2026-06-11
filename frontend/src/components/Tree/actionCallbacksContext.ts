// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Context that carries the per-node action callbacks from TreeCanvas
 * down to the per-kind card components.
 *
 * Using a context (vs threading the callbacks through `data.callbacks`
 * on every adapter-emitted node) keeps the adapter pure: callbacks
 * don't perturb the adapter's identity-stable output, so a render that
 * only changes callbacks doesn't re-adapt the tree. The cards read the
 * context only when they render the rail.
 */

import { createContext, useContext } from 'react'

import type { ActionCallbacks } from './actionRail'

/**
 * `null` means "no callbacks provided" — cards skip the rail entirely.
 * Distinct from `{}` (provided but empty) so a host can intentionally
 * disable the rail without surfacing all-button-hidden empty-rail
 * wrappers via the always-render path.
 */
export const ActionCallbacksContext = createContext<ActionCallbacks | null>(null)

export function useActionCallbacks(): ActionCallbacks | null {
  return useContext(ActionCallbacksContext)
}

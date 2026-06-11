// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Context surfacing the host-supplied list of converters available
 * to UserTurn cards' `⚡ Converter palette` (spec §2.2). Host (typically
 * App-level after pre-fetching `convertersApi.listConverters`) wires
 * the list through `TreeCanvasProps.availableConverters`; TreeCanvas
 * publishes via this context; UserTurnCard reads it.
 *
 * Empty / unwired (default `null`) hides the ⚡ button entirely —
 * there's nothing to pick from.
 */

import { createContext, useContext } from 'react'

export interface AvailableConverter {
  /** Stable converter identifier; becomes `ConverterRef.converterId`. */
  id: string
  /** Display label shown in the palette menu. */
  label: string
}

export type AvailableConvertersValue = ReadonlyArray<AvailableConverter> | null

export const AvailableConvertersContext = createContext<AvailableConvertersValue>(null)

export function useAvailableConverters(): AvailableConvertersValue {
  return useContext(AvailableConvertersContext)
}

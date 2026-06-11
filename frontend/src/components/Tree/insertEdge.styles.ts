// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Styles for the per-edge insert chip.
 *
 * The chip is positioned absolutely via EdgeLabelRenderer (which itself
 * portals into a fixed layer above the SVG path); the wrapper applies
 * the `translate(-50%, -50%) translate(labelX, labelY)` math react-flow
 * recommends so the chip is centered on the edge midpoint.
 */

import { makeStyles, tokens } from '@fluentui/react-components'

export const useInsertEdgeStyles = makeStyles({
  chipWrapper: {
    position: 'absolute',
    pointerEvents: 'all',
  },
  chipButton: {
    // Small circular button so the chip reads as an inline affordance
    // rather than a primary CTA. Brand-color background per the
    // primary appearance keeps it discoverable against the orthogonal
    // smoothstep stroke without competing visually with the node cards.
    minWidth: 'unset',
    width: '20px',
    height: '20px',
    borderRadius: tokens.borderRadiusCircular,
    padding: '0',
  },
})

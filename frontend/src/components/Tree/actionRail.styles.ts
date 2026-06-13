// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Styles for the per-node action rail.
 *
 * Visibility: per spec §2.2 ("rail floats below each node card on hover
 * or focus"), the rail is hidden by default and revealed when the parent
 * CardFrame is hovered, contains a focused descendant, OR carries
 * `data-selected="true"`. The hover/focus/selected selectors live on
 * the `frame` style in nodeCards.styles.ts (Griffel's `&` reference
 * lets a parent style gate a descendant's visibility via attribute
 * selectors); this file owns only the default-hidden state.
 */

import { makeStyles, tokens } from '@fluentui/react-components'

export const useActionRailStyles = makeStyles({
  rail: {
    position: 'absolute',
    left: '50%',
    bottom: '-38px',
    transform: 'translateX(-50%)',
    zIndex: 5,
    display: 'flex',
    gap: tokens.spacingHorizontalXXS,
    padding: `${tokens.spacingVerticalXXS} ${tokens.spacingHorizontalXS}`,
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusMedium,
    boxShadow: tokens.shadow8,
    minWidth: 'max-content',
    opacity: 0,
    transitionProperty: 'opacity',
    transitionDuration: '120ms',
  },
})

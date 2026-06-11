// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Styles for the per-node action rail.
 *
 * Visibility: per the operator-facing convention, rails appear on hover
 * or when the card is selected. The CSS selectors here pair with the
 * `[data-selected="true"]` attribute CardFrame emits + the `:hover`
 * pseudo-class on the card wrapper.
 */

import { makeStyles, tokens } from '@fluentui/react-components'

export const useActionRailStyles = makeStyles({
  rail: {
    display: 'flex',
    gap: tokens.spacingHorizontalXXS,
    marginTop: tokens.spacingVerticalXS,
    paddingTop: tokens.spacingVerticalXS,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    // PR5c: always-visible until PR5e/PR5f wire the hover/selected
    // behavior alongside the Stack rendering. The :hover + [data-
    // selected="true"] visibility flip lands as a CardFrame-side CSS
    // update once we have an integration test that can drive jsdom
    // hover (currently the test surface uses the data attributes only).
    opacity: 1,
  },
})

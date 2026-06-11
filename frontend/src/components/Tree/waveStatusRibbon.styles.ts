// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { makeStyles, tokens } from '@fluentui/react-components'

export const useWaveStatusRibbonStyles = makeStyles({
  ribbon: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalS}`,
    fontSize: tokens.fontSizeBase200,
    minHeight: '32px',
  },
  progressWrap: {
    flex: '1 1 200px',
    maxWidth: '320px',
  },
  counter: {
    fontFamily: tokens.fontFamilyMonospace,
    color: tokens.colorNeutralForeground2,
  },
  outcome: {
    fontFamily: tokens.fontFamilyMonospace,
    color: tokens.colorNeutralForeground2,
  },
  queueChip: {
    padding: `1px ${tokens.spacingHorizontalXS}`,
    backgroundColor: tokens.colorNeutralBackground2,
    color: tokens.colorNeutralForeground2,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusSmall,
    fontSize: tokens.fontSizeBase100,
  },
})

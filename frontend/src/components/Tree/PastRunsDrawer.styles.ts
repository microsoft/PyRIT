// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { makeStyles, tokens } from '@fluentui/react-components'

export const usePastRunsDrawerStyles = makeStyles({
  drawer: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    padding: tokens.spacingVerticalS,
  },
  empty: {
    color: tokens.colorNeutralForeground3,
    fontStyle: 'italic',
    margin: 0,
  },
  entry: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalS}`,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusSmall,
    backgroundColor: tokens.colorNeutralBackground1,
    fontSize: tokens.fontSizeBase200,
    '&[data-current="true"]': {
      borderLeftWidth: '3px',
      borderLeftColor: tokens.colorBrandStroke1,
    },
    '&[data-pinned="true"]': {
      backgroundColor: tokens.colorNeutralBackground2,
    },
  },
  outcome: {
    fontFamily: tokens.fontFamilyMonospace,
  },
  id: {
    fontFamily: tokens.fontFamilyMonospace,
    color: tokens.colorNeutralForeground2,
  },
  timestamp: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
  },
  wave: {
    fontFamily: tokens.fontFamilyMonospace,
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
  },
  currentTag: {
    color: tokens.colorBrandForeground1,
    fontWeight: tokens.fontWeightSemibold,
    fontSize: tokens.fontSizeBase100,
    textTransform: 'uppercase',
  },
  actions: {
    marginLeft: 'auto',
    display: 'flex',
    gap: tokens.spacingHorizontalXXS,
  },
})

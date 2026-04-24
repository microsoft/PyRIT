// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { makeStyles, tokens } from '@fluentui/react-components'

export const useUserAccountButtonStyles = makeStyles({
  wrapper: {
    marginLeft: 'auto',
    display: 'flex',
    alignItems: 'center',
  },
  popoverContent: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  accountName: {
    fontWeight: tokens.fontWeightSemibold,
  },
  accountEmail: {
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
  },
})

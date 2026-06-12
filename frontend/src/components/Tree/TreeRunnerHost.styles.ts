// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { makeStyles, tokens } from '@fluentui/react-components'

export const useTreeRunnerHostStyles = makeStyles({
  root: {
    display: 'grid',
    width: '100%',
    height: '100%',
    gridTemplateColumns: '1fr auto',
    gridTemplateRows: 'auto 1fr',
    gridTemplateAreas: `
      "ribbon ribbon"
      "canvas drawer"
    `,
    position: 'relative',
  },
  ribbon: {
    gridArea: 'ribbon',
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground2,
  },
  canvas: {
    gridArea: 'canvas',
    minWidth: 0,
    minHeight: 0,
    position: 'relative',
  },
  drawer: {
    gridArea: 'drawer',
    width: '0px',
  },
  toast: {
    position: 'absolute',
    bottom: tokens.spacingVerticalM,
    right: tokens.spacingHorizontalM,
    pointerEvents: 'none',
  },
  modal: {
    position: 'absolute',
    inset: 0,
    pointerEvents: 'none',
  },
  greenfield: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: '100%',
    height: '100%',
    color: tokens.colorNeutralForeground3,
    fontStyle: 'italic',
  },
})

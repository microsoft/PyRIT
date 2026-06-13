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
  drawerOpen: {
    gridArea: 'drawer',
    width: '360px',
    minWidth: '320px',
    borderLeft: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground1,
    color: tokens.colorNeutralForeground1,
    overflow: 'auto',
    padding: tokens.spacingHorizontalM,
  },
  drawerHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
  },
  drawerNode: {
    marginTop: tokens.spacingVerticalM,
    paddingTop: tokens.spacingVerticalS,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  drawerNodeKind: {
    fontWeight: tokens.fontWeightSemibold,
    textTransform: 'capitalize',
  },
  drawerNodeState: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase100,
  },
  drawerNodeText: {
    whiteSpace: 'pre-wrap',
    margin: 0,
    marginTop: tokens.spacingVerticalXS,
    fontFamily: tokens.fontFamilyBase,
    fontSize: tokens.fontSizeBase200,
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

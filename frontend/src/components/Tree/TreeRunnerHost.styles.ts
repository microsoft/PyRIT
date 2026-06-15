// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { makeStyles, tokens } from '@fluentui/react-components'

export const useTreeRunnerHostStyles = makeStyles({
  root: {
    display: 'grid',
    width: '100%',
    height: '100%',
    gridTemplateColumns: '1fr',
    gridTemplateRows: 'auto 1fr',
    gridTemplateAreas: `
      "ribbon"
      "canvas"
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
  splitter: {
    gridArea: 'splitter',
    cursor: 'col-resize',
    backgroundColor: tokens.colorNeutralBackground3,
    borderLeft: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRight: `1px solid ${tokens.colorNeutralStroke2}`,
    minWidth: '8px',
    selectors: {
      '&:focus-visible': {
        outline: `2px solid ${tokens.colorStrokeFocus2}`,
        outlineOffset: '-2px',
      },
    },
  },
  pathChat: {
    gridArea: 'pathChat',
    minWidth: 0,
    minHeight: 0,
    overflow: 'auto',
    backgroundColor: tokens.colorNeutralBackground1,
    color: tokens.colorNeutralForeground1,
    borderLeft: `1px solid ${tokens.colorNeutralStroke2}`,
    padding: tokens.spacingHorizontalM,
  },
  pathChatHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: tokens.spacingVerticalM,
  },
  pathChatTitle: {
    fontWeight: tokens.fontWeightSemibold,
  },
  pathChatList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  pathChatBubble: {
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusMedium,
    padding: tokens.spacingVerticalS,
    backgroundColor: tokens.colorNeutralBackground2,
    color: tokens.colorNeutralForeground1,
    textAlign: 'left',
    cursor: 'pointer',
  },
  pathChatBubbleSelected: {
    border: `1px solid ${tokens.colorBrandStroke1}`,
    boxShadow: `0 0 0 1px ${tokens.colorBrandStroke1}`,
  },
  pathChatRole: {
    fontSize: tokens.fontSizeBase100,
    color: tokens.colorNeutralForeground3,
    marginBottom: tokens.spacingVerticalXXS,
  },
  pathChatText: {
    whiteSpace: 'pre-wrap',
    margin: 0,
    fontFamily: tokens.fontFamilyBase,
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground1,
  },
  pathChatComposer: {
    display: 'grid',
    gap: tokens.spacingVerticalS,
    marginTop: tokens.spacingVerticalL,
    paddingTop: tokens.spacingVerticalM,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  drawerHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
  },
  drawerSelectedNode: {
    marginTop: tokens.spacingVerticalM,
    paddingBottom: tokens.spacingVerticalM,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  drawerSectionTitle: {
    marginTop: tokens.spacingVerticalM,
    fontWeight: tokens.fontWeightSemibold,
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
    maxHeight: '40vh',
    overflow: 'auto',
  },
  drawerMetaList: {
    display: 'grid',
    gridTemplateColumns: 'max-content minmax(0, 1fr)',
    gap: `${tokens.spacingVerticalXXS} ${tokens.spacingHorizontalS}`,
    marginTop: tokens.spacingVerticalS,
    fontSize: tokens.fontSizeBase100,
  },
  drawerMetaLabel: {
    color: tokens.colorNeutralForeground3,
  },
  drawerMetaValue: {
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    fontFamily: tokens.fontFamilyMonospace,
  },
  drawerError: {
    marginTop: tokens.spacingVerticalS,
    padding: tokens.spacingVerticalS,
    backgroundColor: tokens.colorPaletteRedBackground2,
    color: tokens.colorPaletteRedForeground2,
    borderRadius: tokens.borderRadiusSmall,
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

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { makeStyles, tokens } from '@fluentui/react-components'

export const useTreeCanvasStyles = makeStyles({
  root: {
    width: '100%',
    height: '100%',
    '& .react-flow__minimap': {
      backgroundColor: tokens.colorNeutralBackground2,
      border: `1px solid ${tokens.colorNeutralStroke2}`,
      borderRadius: tokens.borderRadiusMedium,
      boxShadow: tokens.shadow8,
    },
    '& .react-flow__minimap-mask': {
      fill: tokens.colorNeutralBackground3,
      opacity: 0.7,
    },
    '& .react-flow__minimap-node': {
      fill: tokens.colorBrandBackground,
      stroke: tokens.colorBrandStroke1,
    },
    '& .react-flow__controls': {
      backgroundColor: tokens.colorNeutralBackground1,
      border: `1px solid ${tokens.colorNeutralStroke2}`,
      borderRadius: tokens.borderRadiusMedium,
      boxShadow: tokens.shadow8,
      overflow: 'hidden',
    },
    '& .react-flow__controls-button': {
      backgroundColor: tokens.colorNeutralBackground1,
      borderBottomColor: tokens.colorNeutralStroke2,
      color: tokens.colorNeutralForeground1,
    },
    '& .react-flow__controls-button:hover': {
      backgroundColor: tokens.colorNeutralBackground1Hover,
    },
    '& .react-flow__controls-button svg': {
      fill: tokens.colorNeutralForeground1,
    },
    '& .react-flow__controls-button:disabled svg': {
      fill: tokens.colorNeutralForegroundDisabled,
    },
    '& .react-flow__attribution': {
      backgroundColor: tokens.colorNeutralBackground1,
      color: tokens.colorNeutralForeground3,
      borderTopLeftRadius: tokens.borderRadiusSmall,
    },
    '& .react-flow__attribution a': {
      color: tokens.colorNeutralForeground3,
    },
  },
})

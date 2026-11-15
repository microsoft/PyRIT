import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useGenerationSettingsStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: tokens.spacingVerticalXXS,
    position: 'relative',
    zIndex: 1,
  },
  button: {
    minWidth: 0,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalXS}`,
    ...mobileTouchTarget,
  },
  chevron: {
    fontSize: tokens.fontSizeBase100,
  },
  value: {
    maxWidth: '4rem',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  override: {
    color: tokens.colorBrandForeground1,
  },
  invalid: {
    color: tokens.colorPaletteRedForeground1,
  },
  surface: {
    width: '17rem',
    maxWidth: 'calc(100vw - 2rem)',
    boxSizing: 'border-box',
  },
  content: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  actions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: tokens.spacingHorizontalXS,
    flexWrap: 'wrap',
  },
})

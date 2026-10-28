import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useConverterCardStyles = makeStyles({
  root: {
    display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalXXS,
    padding: tokens.spacingVerticalS, borderRadius: tokens.borderRadiusMedium,
    border: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  header: {
    display: 'flex', alignItems: 'center', gap: tokens.spacingHorizontalS,
    cursor: 'grab', userSelect: 'none', ':active': { cursor: 'grabbing' },
  },
  dragHandle: { ...mobileTouchTarget, cursor: 'grab', flexShrink: 0 },
  remove: { ...mobileTouchTarget, marginLeft: 'auto', flexShrink: 0 },
  name: { overflowWrap: 'anywhere', minWidth: 0 },
  hint: { color: tokens.colorNeutralForeground3 },
  footer: { display: 'flex', justifyContent: 'flex-end' },
  settings: { ...mobileTouchTarget, flexShrink: 0 },
  llmBadge: {
    padding: `0 ${tokens.spacingHorizontalXXS}`, borderRadius: tokens.borderRadiusSmall,
    backgroundColor: tokens.colorPalettePurpleBackground2, color: tokens.colorPalettePurpleForeground2,
    fontSize: tokens.fontSizeBase100, fontWeight: tokens.fontWeightSemibold, flexShrink: 0,
  },
})

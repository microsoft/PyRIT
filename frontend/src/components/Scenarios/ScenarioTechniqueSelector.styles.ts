import { makeStyles, tokens } from '@fluentui/react-components'

import {
  mobileTouchTargetHeight,
  NARROW_VIEWPORT_QUERY,
} from '@/styles/touchTargets'

export const useScenarioTechniqueSelectorStyles = makeStyles({
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  hint: {
    color: tokens.colorNeutralForeground3,
  },
  errorText: {
    color: tokens.colorPaletteRedForeground1,
  },
  techniqueList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  pinnedTokens: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  techniqueOption: {
    display: 'grid',
    gridTemplateColumns: 'minmax(12rem, 35%) minmax(0, 1fr)',
    gap: tokens.spacingHorizontalM,
    alignItems: 'start',
    padding: tokens.spacingVerticalS,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusMedium,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(0, 1fr)',
      gap: tokens.spacingVerticalXS,
    },
  },
  selectionControl: {
    ...mobileTouchTargetHeight,
  },
  techniqueDetails: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    minWidth: 0,
  },
})

import { makeStyles, tokens } from '@fluentui/react-components'

import {
  mobileTouchTarget,
  NARROW_VIEWPORT_QUERY,
  TOUCH_INPUT_QUERY,
  MINIMUM_TOUCH_TARGET_SIZE,
} from '@/styles/touchTargets'
import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useDatasetCatalogStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    width: '100%',
    minWidth: 0,
    padding: tokens.spacingVerticalXXL,
    overflowX: 'hidden',
    overflowY: 'auto',
    backgroundColor: WORKSPACE_CANVAS_BACKGROUND,
    [NARROW_VIEWPORT_QUERY]: {
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}`,
    },
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingVerticalL,
    marginBottom: tokens.spacingVerticalL,
    [NARROW_VIEWPORT_QUERY]: {
      flexDirection: 'column',
      alignItems: 'stretch',
    },
  },
  headerText: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    minWidth: 0,
  },
  subtitle: {
    maxWidth: '70ch',
    color: tokens.colorNeutralForeground3,
  },
  filters: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    alignItems: 'center',
    marginBottom: tokens.spacingVerticalL,
    [NARROW_VIEWPORT_QUERY]: {
      alignItems: 'stretch',
    },
  },
  search: {
    minWidth: '16rem',
    [NARROW_VIEWPORT_QUERY]: {
      minWidth: 0,
      flex: '1 1 100%',
    },
    [TOUCH_INPUT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
  },
  filterControl: {
    minWidth: '12rem',
    [NARROW_VIEWPORT_QUERY]: {
      minWidth: 0,
      flex: '1 1 12rem',
    },
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
  centeredState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalXXXL,
    textAlign: 'center',
    color: tokens.colorNeutralForeground3,
  },
  resultCount: {
    margin: `0 0 ${tokens.spacingVerticalS}`,
    color: tokens.colorNeutralForeground3,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(18rem, 1fr))',
    gap: tokens.spacingHorizontalL,
    margin: 0,
    padding: 0,
    listStyleType: 'none',
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(0, 1fr)',
    },
  },
})

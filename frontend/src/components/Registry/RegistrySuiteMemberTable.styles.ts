import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useRegistrySuiteMemberTableStyles = makeStyles({
  container: {
    minWidth: 0,
    overflowX: 'auto',
  },
  table: {
    width: '100%',
    [NARROW_VIEWPORT_QUERY]: {
      display: 'block',
    },
  },
  header: {
    [NARROW_VIEWPORT_QUERY]: {
      display: 'none',
    },
  },
  body: {
    [NARROW_VIEWPORT_QUERY]: {
      display: 'block',
    },
  },
  row: {
    [NARROW_VIEWPORT_QUERY]: {
      display: 'flex',
      flexDirection: 'column',
      padding: `${tokens.spacingVerticalS} 0`,
    },
  },
  brokenRow: {
    backgroundColor: tokens.colorStatusWarningBackground1,
  },
  cell: {
    verticalAlign: 'top',
    paddingTop: tokens.spacingVerticalS,
    paddingBottom: tokens.spacingVerticalS,
    overflowWrap: 'anywhere',
    [NARROW_VIEWPORT_QUERY]: {
      display: 'block',
      width: 'auto',
    },
  },
  scenarioColumn: {
    width: '50%',
  },
  // Wide enough for Edit and Remove side by side, as the actions read as one group.
  actionsColumn: {
    width: '14rem',
  },
  stack: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  secondary: {
    color: tokens.colorNeutralForeground3,
  },
  issue: {
    color: tokens.colorStatusWarningForeground1,
  },
  actions: {
    display: 'flex',
    flexWrap: 'nowrap',
    gap: tokens.spacingHorizontalXS,
    [NARROW_VIEWPORT_QUERY]: {
      flexWrap: 'wrap',
    },
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
})

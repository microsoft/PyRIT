import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'
import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useScenarioPresetLibraryStyles = makeStyles({
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
  },
  subtitle: {
    color: tokens.colorNeutralForeground3,
  },
  headerActions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    alignItems: 'center',
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
  sourceLine: {
    color: tokens.colorNeutralForeground3,
    marginBottom: tokens.spacingVerticalM,
    wordBreak: 'break-all',
  },
  centeredState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalXXL,
    textAlign: 'center',
  },
  tableContainer: {
    width: '100%',
    overflowX: 'auto',
  },
  table: {
    minWidth: '900px',
    backgroundColor: tokens.colorNeutralBackground1,
  },
  nameCell: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  cardTitleRow: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  cardMeta: {
    color: tokens.colorNeutralForeground3,
  },
  cardActions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
    alignItems: 'center',
  },
  issueList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    margin: 0,
    paddingLeft: tokens.spacingHorizontalL,
  },
  dialogBody: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
})

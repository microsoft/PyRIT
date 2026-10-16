import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'
import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useAnalyticsPageStyles = makeStyles({
  root: {
    height: '100%',
    overflowY: 'auto',
    minWidth: 0,
    backgroundColor: WORKSPACE_CANVAS_BACKGROUND,
  },
  header: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalXXL}`,
    backgroundColor: tokens.colorNeutralBackground3,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    [NARROW_VIEWPORT_QUERY]: { paddingInline: tokens.spacingHorizontalM },
  },
  title: { margin: 0 },
  row: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
  },
  body: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalXXL}`,
    [NARROW_VIEWPORT_QUERY]: { paddingInline: tokens.spacingHorizontalM },
  },
  section: {
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  toolbar: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'end',
    gap: tokens.spacingHorizontalL,
  },
  note: {
    color: tokens.colorNeutralForeground2,
    overflowWrap: 'anywhere',
  },
  control: { minWidth: 0, maxWidth: '100%', [NARROW_VIEWPORT_QUERY]: { width: '100%' } },
  button: { minWidth: 0, maxWidth: '100%', ...mobileTouchTargetHeight },
  empty: {
    padding: `${tokens.spacingVerticalXXL} ${tokens.spacingHorizontalL}`,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: tokens.spacingVerticalM,
  },
  skeleton: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
  },
})

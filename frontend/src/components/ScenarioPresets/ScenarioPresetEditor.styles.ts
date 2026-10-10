import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'
import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useScenarioPresetEditorStyles = makeStyles({
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
  headerText: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    marginBottom: tokens.spacingVerticalL,
  },
  subtitle: {
    color: tokens.colorNeutralForeground3,
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    maxWidth: '60rem',
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalL,
    borderRadius: tokens.borderRadiusMedium,
    border: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  control: {
    maxWidth: '32rem',
  },
  dynamicParameters: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  actions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    alignItems: 'center',
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
  centeredState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalXXL,
    textAlign: 'center',
  },
})

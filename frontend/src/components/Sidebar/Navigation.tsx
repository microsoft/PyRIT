import type { ReactElement } from 'react'

import {
  Button,
  Menu,
  MenuItemRadio,
  MenuList,
  MenuPopover,
  MenuTrigger,
  Tooltip,
  mergeClasses,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import type { MenuCheckedValueChangeData, MenuCheckedValueChangeEvent } from '@fluentui/react-components'
import {
  ChatRegular,
  DatabaseRegular,
  HomeRegular,
  SettingsRegular,
  HistoryRegular,
  DataBarVerticalRegular,
  BriefcaseRegular,
  PersonFeedbackRegular,
  ScriptRegular,
  TargetRegular,
  WeatherMoonRegular,
  WeatherSunnyRegular,
  PanelLeftContractRegular,
  PanelLeftExpandRegular,
} from '@fluentui/react-icons'

import { useTheme } from '@/hooks/useTheme'
import { isThemeMode, THEME_PRESETS } from '@/themes/themePresets'
import type { ThemePreset } from '@/types'

import { useNavigationStyles } from './Navigation.styles'

export type ViewName =
  | 'home'
  | 'chat'
  | 'history'
  | 'analytics'
  | 'operations'
  | 'registry'
  | 'configuration'
  | 'scenarios'
  | 'datasets'

interface NavigationProps {
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onOpenFeedback: () => void
  canManageConfiguration: boolean
  expanded: boolean
  onToggleExpanded: () => void
}

interface NavigationItem {
  readonly view: ViewName
  readonly label: string
  readonly icon: ReactElement
}

const THEME_MENU_NAME = 'theme'
const NAVIGATION_ITEMS: NavigationItem[] = [
  { view: 'home', label: 'Home', icon: <HomeRegular /> },
  { view: 'chat', label: 'Attacks', icon: <ChatRegular /> },
  { view: 'scenarios', label: 'Scanner', icon: <TargetRegular /> },
  { view: 'history', label: 'History', icon: <HistoryRegular /> },
  { view: 'analytics', label: 'Analytics', icon: <DataBarVerticalRegular /> },
  { view: 'operations', label: 'Operations', icon: <BriefcaseRegular /> },
  { view: 'datasets', label: 'Datasets', icon: <DatabaseRegular /> },
  { view: 'registry', label: 'Registry', icon: <ScriptRegular /> },
  { view: 'configuration', label: 'Configuration', icon: <SettingsRegular /> },
]

export default function Navigation({
  currentView,
  onNavigate,
  onOpenFeedback,
  canManageConfiguration,
  expanded,
  onToggleExpanded,
}: NavigationProps) {
  const styles = useNavigationStyles()
  const { mode, resolved, setMode } = useTheme()
  const feedbackRestoreFocusTarget = useRestoreFocusTarget()
  const sidebarRestoreFocusTarget = useRestoreFocusTarget()

  const handleThemeChange = (
    _: MenuCheckedValueChangeEvent,
    data: MenuCheckedValueChangeData,
  ) => {
    const next = data.checkedItems[0]
    if (isThemeMode(next)) {
      setMode(next)
    }
  }

  const triggerIcon = resolved === 'dark' ? <WeatherMoonRegular /> : <WeatherSunnyRegular />
  const triggerLabel = `Theme: ${mode === 'system' ? 'System' : THEME_PRESETS[mode].label}`

  return (
    <div className={mergeClasses(styles.root, expanded && styles.expanded)} data-tour="sidebar-nav">
      <Button
        {...sidebarRestoreFocusTarget}
        className={styles.navButton}
        appearance="subtle"
        icon={expanded ? <PanelLeftContractRegular /> : <PanelLeftExpandRegular />}
        aria-label={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
        title={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
        aria-expanded={expanded}
        data-testid="sidebar-toggle"
        onClick={onToggleExpanded}
      />
      <nav aria-label="Primary" className={styles.primaryNavigation}>
        {NAVIGATION_ITEMS.filter((item: NavigationItem) =>
          item.view !== 'configuration' || canManageConfiguration,
        ).map((item: NavigationItem) => {
          const button = (
            <Button
              key={item.view}
              className={styles.navButton}
              data-active={currentView === item.view}
              appearance="subtle"
              icon={item.icon}
              aria-label={item.label}
              aria-current={currentView === item.view ? 'page' : undefined}
              onClick={() => onNavigate(item.view)}
            >
              {expanded && item.label}
            </Button>
          )
          return expanded ? button : (
            <Tooltip key={item.view} content={item.label} relationship="label" positioning="after">
              {button}
            </Tooltip>
          )
        })}
      </nav>

      <div className={styles.spacer} />

      <Button
        {...feedbackRestoreFocusTarget}
        className={styles.navButton}
        appearance="subtle"
        icon={<PersonFeedbackRegular />}
        title="Feedback"
        aria-label="Feedback"
        onClick={onOpenFeedback}
      >
        {expanded && 'Feedback'}
      </Button>
      <Menu
        checkedValues={{ [THEME_MENU_NAME]: [mode] }}
        onCheckedValueChange={handleThemeChange}
        positioning={{ autoSize: 'height', overflowBoundary: 'window' }}
      >
        <MenuTrigger disableButtonEnhancement>
          <Button
            className={styles.navButton}
            appearance="subtle"
            icon={triggerIcon}
            title={triggerLabel}
            aria-label={triggerLabel}
          >
            {expanded && 'Theme'}
          </Button>
        </MenuTrigger>
        <MenuPopover className={styles.themeMenu}>
          <MenuList>
            <MenuItemRadio className={styles.themeMenuItem} name={THEME_MENU_NAME} value="system">
              System
            </MenuItemRadio>
            {Object.entries(THEME_PRESETS).map(([id, preset]: [string, ThemePreset]) => (
              <MenuItemRadio key={id} className={styles.themeMenuItem} name={THEME_MENU_NAME} value={id}>
                <span className={styles.themeOption}>
                  <span>{preset.label}</span>
                  <span
                    aria-hidden="true"
                    className={styles.themePreview}
                    style={{
                      backgroundColor: preset.theme.colorNeutralBackground2,
                      backgroundImage: preset.background
                        ? `url("${preset.background.imageUrl}")`
                        : undefined,
                    }}
                  />
                </span>
              </MenuItemRadio>
            ))}
          </MenuList>
        </MenuPopover>
      </Menu>
    </div>
  )
}

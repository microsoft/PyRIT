import {
  Button,
  Menu,
  MenuItemRadio,
  MenuList,
  MenuPopover,
  MenuTrigger,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import type { MenuCheckedValueChangeData, MenuCheckedValueChangeEvent } from '@fluentui/react-components'
import {
  ChatRegular,
  HomeRegular,
  SettingsRegular,
  HistoryRegular,
  PersonFeedbackRegular,
  ScriptRegular,
  TargetRegular,
  WeatherMoonRegular,
  WeatherSunnyRegular,
} from '@fluentui/react-icons'
import { useTheme } from '@/hooks/useTheme'
import { isThemeMode, THEME_PRESETS } from '@/themes/themePresets'
import type { ThemePreset } from '@/types'

import { useNavigationStyles } from './Navigation.styles'

export type ViewName =
  | 'home'
  | 'chat'
  | 'history'
  | 'registry'
  | 'configuration'
  | 'scenarios'

interface NavigationProps {
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onOpenFeedback: () => void
  canManageConfiguration: boolean
}

const THEME_MENU_NAME = 'theme'

export default function Navigation({
  currentView,
  onNavigate,
  onOpenFeedback,
  canManageConfiguration,
}: NavigationProps) {
  const styles = useNavigationStyles()
  const { mode, resolved, setMode } = useTheme()
  const feedbackRestoreFocusTarget = useRestoreFocusTarget()

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
    <div className={styles.root} data-tour="sidebar-nav">
      <nav aria-label="Primary" className={styles.primaryNavigation}>
        <Button
          className={styles.navButton}
          data-active={currentView === 'home'}
          appearance="subtle"
          icon={<HomeRegular />}
          title="Home"
          aria-label="Home"
          aria-current={currentView === 'home' ? 'page' : undefined}
          onClick={() => onNavigate('home')}
        />

        <Button
          className={styles.navButton}
          data-active={currentView === 'chat'}
          appearance="subtle"
          icon={<ChatRegular />}
          title="Chat"
          aria-label="Chat"
          aria-current={currentView === 'chat' ? 'page' : undefined}
          onClick={() => onNavigate('chat')}
        />

        <Button
          className={styles.navButton}
          data-active={currentView === 'history'}
          appearance="subtle"
          icon={<HistoryRegular />}
          title="History"
          aria-label="History"
          aria-current={currentView === 'history' ? 'page' : undefined}
          onClick={() => onNavigate('history')}
        />

        <Button
          className={styles.navButton}
          data-active={currentView === 'scenarios'}
          appearance="subtle"
          icon={<TargetRegular />}
          title="Scanner"
          aria-label="Scanner"
          aria-current={currentView === 'scenarios' ? 'page' : undefined}
          onClick={() => onNavigate('scenarios')}
        />

        <Button
          className={styles.navButton}
          data-active={currentView === 'registry'}
          appearance="subtle"
          icon={<ScriptRegular />}
          title="Registry"
          aria-label="Registry"
          aria-current={currentView === 'registry' ? 'page' : undefined}
          onClick={() => onNavigate('registry')}
        />

        {canManageConfiguration && (
          <Button
            className={styles.navButton}
            data-active={currentView === 'configuration'}
            appearance="subtle"
            icon={<SettingsRegular />}
            title="Configuration"
            aria-label="Configuration"
            aria-current={currentView === 'configuration' ? 'page' : undefined}
            onClick={() => onNavigate('configuration')}
          />
        )}

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
      />
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
          />
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

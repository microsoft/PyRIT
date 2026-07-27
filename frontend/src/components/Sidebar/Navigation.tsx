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
  WeatherMoonRegular,
  WeatherSunnyRegular,
} from '@fluentui/react-icons'
import { useTheme } from '../../hooks/useTheme'
import type { ThemeMode } from '../../hooks/useTheme'
import { useNavigationStyles } from './Navigation.styles'

export type ViewName = 'home' | 'chat' | 'history' | 'config'

interface NavigationProps {
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onOpenFeedback: () => void
}

const THEME_MENU_NAME = 'theme'

const THEME_LABELS: Record<ThemeMode, string> = {
  system: 'System',
  light: 'Light',
  dark: 'Dark',
}


export default function Navigation({ currentView, onNavigate, onOpenFeedback }: NavigationProps) {
  const styles = useNavigationStyles()
  const { mode, resolved, setMode } = useTheme()
  const feedbackRestoreFocusTarget = useRestoreFocusTarget()

  const handleThemeChange = (
    _: MenuCheckedValueChangeEvent,
    data: MenuCheckedValueChangeData,
  ) => {
    const next = data.checkedItems[0]
    if (next === 'system' || next === 'light' || next === 'dark') {
      setMode(next)
    }
  }

  const triggerIcon = resolved === 'dark' ? <WeatherMoonRegular /> : <WeatherSunnyRegular />
  const triggerLabel = `Theme: ${THEME_LABELS[mode]}`

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
          title="Attack History"
          aria-label="Attack History"
          aria-current={currentView === 'history' ? 'page' : undefined}
          onClick={() => onNavigate('history')}
        />

        <Button
          className={styles.navButton}
          data-active={currentView === 'config'}
          appearance="subtle"
          icon={<SettingsRegular />}
          title="Configuration"
          aria-label="Configuration"
          aria-current={currentView === 'config' ? 'page' : undefined}
          onClick={() => onNavigate('config')}
        />
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
        <MenuPopover>
          <MenuList>
            <MenuItemRadio name={THEME_MENU_NAME} value="system">
              {THEME_LABELS.system}
            </MenuItemRadio>
            <MenuItemRadio name={THEME_MENU_NAME} value="light">
              {THEME_LABELS.light}
            </MenuItemRadio>
            <MenuItemRadio name={THEME_MENU_NAME} value="dark">
              {THEME_LABELS.dark}
            </MenuItemRadio>
          </MenuList>
        </MenuPopover>
      </Menu>
    </div>
  )
}

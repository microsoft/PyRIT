import {
  Button,
} from '@fluentui/react-components'
import {
  ChatRegular,
  SettingsRegular,
  HistoryRegular,
  WeatherMoonRegular,
  WeatherSunnyRegular,
} from '@fluentui/react-icons'
import { useNavigationStyles } from './Navigation.styles'

export type ViewName = 'chat' | 'history' | 'config'

interface NavigationProps {
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onToggleTheme: () => void
  isDarkMode: boolean
}

export default function Navigation({ currentView, onNavigate, onToggleTheme, isDarkMode }: NavigationProps) {
  const styles = useNavigationStyles()

  return (
    <div className={styles.root}>
      <Button
        className={styles.navButton}
        data-active={currentView === 'chat'}
        appearance="subtle"
        icon={<ChatRegular />}
        title="Chat"
        aria-label="Chat"
        onClick={() => onNavigate('chat')}
      />

      <Button
        className={styles.navButton}
        data-active={currentView === 'history'}
        appearance="subtle"
        icon={<HistoryRegular />}
        title="Attack History"
        aria-label="Attack History"
        onClick={() => onNavigate('history')}
      />

      <Button
        className={styles.navButton}
        data-active={currentView === 'config'}
        appearance="subtle"
        icon={<SettingsRegular />}
        title="Configuration"
        aria-label="Configuration"
        onClick={() => onNavigate('config')}
      />

      <div className={styles.spacer} />

      <Button
        className={styles.navButton}
        appearance="subtle"
        icon={isDarkMode ? <WeatherSunnyRegular /> : <WeatherMoonRegular />}
        onClick={onToggleTheme}
        title={isDarkMode ? 'Light Mode' : 'Dark Mode'}
        aria-label={isDarkMode ? 'Light Mode' : 'Dark Mode'}
      />
    </div>
  )
}

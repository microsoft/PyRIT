import {
  Button,
} from '@fluentui/react-components'
import {
  ChatRegular,
  HomeRegular,
  SettingsRegular,
  HistoryRegular,
  BranchRegular,
  WeatherMoonRegular,
  WeatherSunnyRegular,
} from '@fluentui/react-icons'
import { useNavigationStyles } from './Navigation.styles'
import { isTreeUiEnabled } from '../../featureFlags'

export type ViewName = 'home' | 'chat' | 'history' | 'config' | 'tree'

interface NavigationProps {
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onToggleTheme: () => void
  isDarkMode: boolean
}

export default function Navigation({ currentView, onNavigate, onToggleTheme, isDarkMode }: NavigationProps) {
  const styles = useNavigationStyles()
  const treeUiEnabled = isTreeUiEnabled()

  return (
    <div className={styles.root}>
      <Button
        className={styles.navButton}
        data-active={currentView === 'home'}
        appearance="subtle"
        icon={<HomeRegular />}
        title="Home"
        aria-label="Home"
        onClick={() => onNavigate('home')}
      />

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

      {treeUiEnabled && (
        <Button
          className={styles.navButton}
          data-active={currentView === 'tree'}
          appearance="subtle"
          icon={<BranchRegular />}
          title="Tree View"
          aria-label="Tree View"
          onClick={() => onNavigate('tree')}
        />
      )}

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

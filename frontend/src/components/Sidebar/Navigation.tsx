import {
  Button,
} from '@fluentui/react-components'
import {
  ChatRegular,
  HomeRegular,
  QuestionCircleRegular,
  SettingsRegular,
  HistoryRegular,
  PersonFeedbackRegular,
  WeatherMoonRegular,
  WeatherSunnyRegular,
} from '@fluentui/react-icons'
import { useNavigationStyles } from './Navigation.styles'

export type ViewName = 'home' | 'chat' | 'history' | 'config'

interface NavigationProps {
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onToggleTheme: () => void
  isDarkMode: boolean
  onStartTour?: () => void
  onOpenFeedback: () => void
}

export default function Navigation({ currentView, onNavigate, onToggleTheme, isDarkMode, onStartTour, onOpenFeedback }: NavigationProps) {
  const styles = useNavigationStyles()

  return (
    <div className={styles.root} data-tour="sidebar-nav">
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

      <div className={styles.spacer} />

      {onStartTour && (
        <Button
          className={styles.navButton}
          appearance="subtle"
          icon={<QuestionCircleRegular />}
          onClick={onStartTour}
          title="Take a tour"
          aria-label="Take a tour"
        />
      )}

      <Button
        className={styles.navButton}
        appearance="subtle"
        icon={<PersonFeedbackRegular />}
        title="Feedback"
        aria-label="Feedback"
        onClick={onOpenFeedback}
      />

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

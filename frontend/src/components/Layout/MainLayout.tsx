import { useEffect, useState } from 'react'
import {
  Button,
  Text,
  Tooltip,
} from '@fluentui/react-components'
import { QuestionCircleRegular } from '@fluentui/react-icons'
import { versionApi } from '../../services/api'
import Navigation, { type ViewName } from '../Sidebar/Navigation'
import { UserAccountButton } from '../UserAccountButton'
import { useMainLayoutStyles } from './MainLayout.styles'

interface MainLayoutProps {
  children: React.ReactNode
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onOpenFeedback: () => void
  onStartTour?: () => void
}

export default function MainLayout({
  children,
  currentView,
  onNavigate,
  onOpenFeedback,
  onStartTour,
}: MainLayoutProps) {
  const styles = useMainLayoutStyles()
  const [version, setVersion] = useState<string>('Loading...')
  const [databaseInfo, setDatabaseInfo] = useState<string | null>(null)

  useEffect(() => {
    versionApi.getVersion()
      .then(data => {
        setVersion(data.display || data.version)
        setDatabaseInfo(data.database_info ?? null)
      })
      .catch(() => setVersion('Unknown'))
  }, [])

  return (
    <div className={styles.root}>
      <div className={styles.topBar}>
        <Tooltip content={<>{`PyRIT ${version}`}{databaseInfo && <><br />{databaseInfo}</>}</>} relationship="label">
          <img
            src="/roakey.png"
            alt="Co-PyRIT Logo"
            className={styles.logo}
          />
        </Tooltip>
        <Text className={styles.title}>Co-PyRIT</Text>
        <Text className={styles.subtitle}>Python Risk Identification Tool</Text>
        <div className={styles.spacer} />
        {onStartTour && (
          <Button
            appearance="subtle"
            icon={<QuestionCircleRegular />}
            onClick={onStartTour}
            data-testid="start-tour"
            className={styles.tourButton}
          >
            Take a tour
          </Button>
        )}
        <UserAccountButton />
      </div>
      <div className={styles.contentArea}>
        <aside className={styles.sidebar}>
          <Navigation
            currentView={currentView}
            onNavigate={onNavigate}
            onOpenFeedback={onOpenFeedback}
          />
        </aside>
        <main className={styles.main}>{children}</main>
      </div>
    </div>
  )
}

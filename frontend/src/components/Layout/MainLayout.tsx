import { useEffect, useState } from 'react'
import {
  Text,
  Tooltip,
} from '@fluentui/react-components'
import { versionApi } from '../../services/api'
import Navigation, { type ViewName } from '../Sidebar/Navigation'
import { UserAccountButton } from '../UserAccountButton'
import { useMainLayoutStyles } from './MainLayout.styles'

interface MainLayoutProps {
  children: React.ReactNode
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onToggleTheme: () => void
  isDarkMode: boolean
}

export default function MainLayout({
  children,
  currentView,
  onNavigate,
  onToggleTheme,
  isDarkMode,
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
        <UserAccountButton />
      </div>
      <div className={styles.contentArea}>
        <aside className={styles.sidebar}>
          <Navigation
            currentView={currentView}
            onNavigate={onNavigate}
            onToggleTheme={onToggleTheme}
            isDarkMode={isDarkMode}
          />
        </aside>
        <main className={styles.main}>{children}</main>
      </div>
    </div>
  )
}

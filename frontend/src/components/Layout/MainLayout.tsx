import { useEffect, useState } from 'react'
import {
  Button,
  Text,
  Tooltip,
  mergeClasses,
} from '@fluentui/react-components'
import { QuestionCircleRegular } from '@fluentui/react-icons'

import LabelsBar from '@/components/Labels/LabelsBar'
import { useTheme } from '@/hooks/useTheme'

import { versionApi } from '../../services/api'
import Navigation, { type ViewName } from '../Sidebar/Navigation'
import { UserAccountButton } from '../UserAccountButton'
import { useMainLayoutStyles } from './MainLayout.styles'

interface MainLayoutProps {
  children: React.ReactNode
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onOpenFeedback: () => void
  canManageConfiguration: boolean
  labels: Record<string, string>
  onLabelsChange: (labels: Record<string, string>) => void
  toolbarRef?: React.Ref<HTMLDivElement>
  onStartTour?: () => void
}

export default function MainLayout({
  children,
  currentView,
  onNavigate,
  onOpenFeedback,
  canManageConfiguration,
  labels,
  onLabelsChange,
  toolbarRef,
  onStartTour,
}: MainLayoutProps) {
  const styles = useMainLayoutStyles()
  const { background } = useTheme()
  const [version, setVersion] = useState<string>('Loading...')
  const [commit, setCommit] = useState<string | null>(null)
  const [databaseInfo, setDatabaseInfo] = useState<string | null>(null)

  useEffect(() => {
    versionApi.getVersion()
      .then(data => {
        setVersion(data.version)
        document.title = `Co-PyRIT ${data.version}`
        setCommit(data.version.includes('.dev') ? data.commit ?? null : null)
        setDatabaseInfo(data.database_info ?? null)
      })
      .catch(() => {
        setVersion('Unknown')
        document.title = 'Co-PyRIT'
      })
  }, [])

  const title = version === 'Unknown' ? 'Co-PyRIT' : `Co-PyRIT ${version}`

  return (
    <div className={styles.root}>
      <a href="#main-content" className={styles.skipLink}>
        Skip to main content
      </a>
      <div className={styles.topBar}>
        <Tooltip
          content={
            <>
              {`PyRIT ${version}`}
              {commit && <><br />{`Commit: ${commit}`}</>}
              {databaseInfo && <><br />{databaseInfo}</>}
            </>
          }
          relationship="label"
        >
          <img
            src="/roakey.png"
            alt="Co-PyRIT Logo"
            className={styles.logo}
          />
        </Tooltip>
        <Text className={styles.title} title={title}>{title}</Text>
        <Text className={styles.subtitle}>Python Risk Identification Tool</Text>
        <div className={styles.spacer} />
        {onStartTour && (
          <Tooltip content="Take a tour" relationship="description">
            <Button
              appearance="subtle"
              icon={<QuestionCircleRegular />}
              onClick={onStartTour}
              data-testid="start-tour"
              className={styles.tourButton}
              aria-label="Take a tour"
            >
              <span className={styles.tourLabel}>Take a tour</span>
            </Button>
          </Tooltip>
        )}
        <UserAccountButton />
      </div>
      <div className={styles.contentArea}>
        <aside className={styles.sidebar}>
          <Navigation
            currentView={currentView}
            onNavigate={onNavigate}
            onOpenFeedback={onOpenFeedback}
            canManageConfiguration={canManageConfiguration}
          />
        </aside>
        <main
          id="main-content"
          tabIndex={-1}
          className={mergeClasses(styles.main, background && styles.decorated)}
        >
          {background && (
            <div
              aria-hidden="true"
              data-testid="workspace-background"
              className={styles.background}
              style={{
                backgroundImage: `url("${background.imageUrl}")`,
                opacity: background.opacity,
              }}
            />
          )}
          <section
            className={styles.labelsSection}
            aria-label="Default Labels"
            data-tour="labels-card"
          >
            <div className={styles.labelsRow}>
              <div className={styles.labelsControls}>
                <LabelsBar labels={labels} onLabelsChange={onLabelsChange} />
              </div>
              <div ref={toolbarRef} className={styles.toolbarSlot} />
            </div>
          </section>
          {children}
        </main>
      </div>
    </div>
  )
}

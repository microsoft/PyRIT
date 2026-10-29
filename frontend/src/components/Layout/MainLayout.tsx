import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  DrawerBody,
  OverlayDrawer,
  Text,
  Tooltip,
  mergeClasses,
} from '@fluentui/react-components'
import { QuestionCircleRegular } from '@fluentui/react-icons'

import LabelsBar, { type OperationControlProps } from '@/components/Labels/LabelsBar'
import Navigation, { type ViewName } from '@/components/Sidebar/Navigation'
import { UserAccountButton } from '@/components/UserAccountButton'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useTheme } from '@/hooks/useTheme'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import { versionApi } from '@/services/api'
import { NARROW_SCREEN_QUERY } from '@/styles/touchTargets'
import type { UserPreferences } from '@/types'

import { useMainLayoutStyles } from './MainLayout.styles'

interface MainLayoutProps extends OperationControlProps {
  children: React.ReactNode
  currentView: ViewName
  onNavigate: (view: ViewName) => void
  onOpenFeedback: () => void
  canManageConfiguration: boolean
  labels: Record<string, string>
  onLabelsChange: (labels: Record<string, string>) => void
  operatorReadOnly?: boolean
  toolbarRef?: React.Ref<HTMLDivElement>
  onStartTour?: () => void
  featureNavigation?: React.ReactNode
}

export default function MainLayout({
  children,
  currentView,
  onNavigate,
  onOpenFeedback,
  canManageConfiguration,
  labels,
  onLabelsChange,
  operation,
  onOperationChange,
  onRetryOperation,
  operatorReadOnly,
  toolbarRef,
  onStartTour,
  featureNavigation,
}: MainLayoutProps) {
  const styles = useMainLayoutStyles()
  const { background } = useTheme()
  const { preferences, updatePreferences } = useUserPreferences()
  const [version, setVersion] = useState<string>('Loading...')
  const [commit, setCommit] = useState<string | null>(null)
  const [databaseInfo, setDatabaseInfo] = useState<string | null>(null)
  const sidebarExpanded = preferences.sidebarExpanded ?? true
  const [drawerOpen, setDrawerOpen] = useState(false)
  const closeDrawer = useCallback((): void => {
    setDrawerOpen(false)
  }, [])
  const narrow = useMediaQuery(NARROW_SCREEN_QUERY, closeDrawer)

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
        <aside className={mergeClasses(styles.sidebar, !narrow && sidebarExpanded && styles.sidebarExpanded)}>
          <Navigation
            currentView={currentView}
            onNavigate={onNavigate}
            onOpenFeedback={onOpenFeedback}
            canManageConfiguration={canManageConfiguration}
            expanded={!narrow && sidebarExpanded}
            onToggleExpanded={() => {
              if (narrow) setDrawerOpen(true)
              else updatePreferences((current: UserPreferences) => ({
                ...current,
                sidebarExpanded: !(current.sidebarExpanded ?? true),
              }))
            }}
          />
        </aside>
        {narrow && (
          <OverlayDrawer
            open={drawerOpen}
            onOpenChange={(_, data) => { setDrawerOpen(data.open) }}
            position="start"
            className={styles.navigationDrawer}
            aria-label="Navigation"
          >
            <DrawerBody className={styles.drawerBody}>
              <Navigation
                currentView={currentView}
                onNavigate={(view: ViewName) => { onNavigate(view); setDrawerOpen(false) }}
                onOpenFeedback={() => { setDrawerOpen(false); onOpenFeedback() }}
                canManageConfiguration={canManageConfiguration}
                expanded
                onToggleExpanded={() => { setDrawerOpen(false) }}
              />
            </DrawerBody>
          </OverlayDrawer>
        )}
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
                <LabelsBar labels={labels} onLabelsChange={onLabelsChange} operatorReadOnly={operatorReadOnly}
              operation={operation} onOperationChange={onOperationChange} onRetryOperation={onRetryOperation} />
              </div>
            </div>
          </section>
          {featureNavigation}
          <div ref={toolbarRef} className={styles.toolbarSlot} />
          {children}
        </main>
      </div>
    </div>
  )
}

import { useState, useCallback, useEffect } from 'react'
import { FluentProvider, webLightTheme, webDarkTheme } from '@fluentui/react-components'
import MainLayout from './components/Layout/MainLayout'
import ChatWindow from './components/Chat/ChatWindow'
import TargetConfig from './components/Config/TargetConfig'
import AttackHistory from './components/History/AttackHistory'
import { DEFAULT_HISTORY_FILTERS } from './components/History/historyFilters'
import type { HistoryFilters } from './components/History/historyFilters'
import { ConnectionBanner } from './components/ConnectionBanner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ConnectionHealthProvider, useConnectionHealth } from './hooks/useConnectionHealth'
import { DEFAULT_GLOBAL_LABELS } from './components/Labels/labelDefaults'
import type { ViewName } from './components/Sidebar/Navigation'
import type { TargetInstance, TargetInfo } from './types'
import { attacksApi, versionApi } from './services/api'

const AUTO_DISMISS_MS = 5_000

function ConnectionBannerContainer() {
  const { status, reconnectCount } = useConnectionHealth()
  const [showReconnected, setShowReconnected] = useState(false)

  useEffect(() => {
    if (reconnectCount > 0) {
      setShowReconnected(true)
      const timer = setTimeout(() => setShowReconnected(false), AUTO_DISMISS_MS)
      return () => clearTimeout(timer)
    }
  }, [reconnectCount])

  if (status === 'connected' && !showReconnected) {
    return null
  }

  return <ConnectionBanner status={status} />
}

function App() {
  const [isDarkMode, setIsDarkMode] = useState(true)
  const [currentView, setCurrentView] = useState<ViewName>('chat')
  const [activeTarget, setActiveTarget] = useState<TargetInstance | null>(null)
  const [globalLabels, setGlobalLabels] = useState<Record<string, string>>({ ...DEFAULT_GLOBAL_LABELS })
  /** True while loading a historical attack from the history view */
  const [isLoadingAttack, setIsLoadingAttack] = useState(false)
  /** Persisted filter state for the history view */
  const [historyFilters, setHistoryFilters] = useState<HistoryFilters>({ ...DEFAULT_HISTORY_FILTERS })

  // Fetch default labels from backend configuration on startup
  useEffect(() => {
    versionApi.getVersion()
      .then((data) => {
        if (data.default_labels && Object.keys(data.default_labels).length > 0) {
          setGlobalLabels(prev => ({ ...prev, ...data.default_labels }))
        }
      })
      .catch(() => { /* version fetch handled elsewhere */ })
  }, [])

  const handleSetActiveTarget = useCallback((target: TargetInstance) => {
    setActiveTarget(prev => {
      const isSame = prev &&
        prev.target_registry_name === target.target_registry_name &&
        prev.target_type === target.target_type &&
        (prev.endpoint ?? '') === (target.endpoint ?? '') &&
        (prev.model_name ?? '') === (target.model_name ?? '')
      if (isSame) return prev
      // Switching targets no longer clears the loaded attack.  The cross-target
      // guard in ChatWindow prevents sending to a mismatched target, and the
      // backend enforces this server-side as well.  Clearing state here was
      // confusing because navigating to config to pick the *correct* target
      // would wipe the conversation the user was trying to continue.
      return target
    })
  }, [])
  /** The AttackResult's primary key (set on first message). */
  const [attackResultId, setAttackResultId] = useState<string | null>(null)
  /** The attack's primary conversation_id (set on first message). */
  const [conversationId, setConversationId] = useState<string | null>(null)
  /** The currently active conversation (may be main or a related conversation). */
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  /** Labels that the currently loaded attack was created with (for operator locking). */
  const [attackLabels, setAttackLabels] = useState<Record<string, string> | null>(null)
  /** Target info from the currently loaded historical attack (for cross-target guard). */
  const [attackTarget, setAttackTarget] = useState<TargetInfo | null>(null)
  /** Number of related conversations for the currently loaded attack. */
  const [relatedConversationCount, setRelatedConversationCount] = useState(0)

  const clearAttackState = useCallback(() => {
    setAttackResultId(null)
    setConversationId(null)
    setActiveConversationId(null)
    setAttackLabels(null)
    setAttackTarget(null)
    setRelatedConversationCount(0)
  }, [])

  const handleNewAttack = () => {
    clearAttackState()
  }

  const handleConversationCreated = useCallback((arId: string, convId: string) => {
    setAttackResultId(arId)
    setConversationId(convId)
    setActiveConversationId(convId)
    // New attack was created by the current user — use their global labels
    setAttackLabels(null)
    // Record the target used for this attack so the cross-target guard
    // fires if the user switches targets mid-conversation.
    if (activeTarget) {
      const { target_type, endpoint, model_name } = activeTarget
      setAttackTarget({ target_type, endpoint, model_name })
    }
  }, [activeTarget])

  const handleSelectConversation = useCallback((convId: string) => {
    setActiveConversationId(convId)
    // Messages will be loaded by ChatWindow's useEffect
  }, [])

  const handleOpenAttack = useCallback(async (openAttackResultId: string) => {
    setAttackResultId(openAttackResultId)
    setIsLoadingAttack(true)
    setCurrentView('chat')
    // Fetch attack info to get conversation_id and stored labels (for operator locking)
    try {
      const attack = await attacksApi.getAttack(openAttackResultId)
      setConversationId(attack.conversation_id)
      setActiveConversationId(attack.conversation_id)
      setAttackLabels(attack.labels ?? {})
      setAttackTarget(attack.target ?? null)
      setRelatedConversationCount(attack.related_conversation_ids?.length ?? 0)
    } catch {
      clearAttackState()
    } finally {
      setIsLoadingAttack(false)
    }
  }, [clearAttackState])

  const toggleTheme = () => {
    setIsDarkMode(!isDarkMode)
  }

  return (
    <ErrorBoundary>
      <ConnectionHealthProvider>
        <FluentProvider theme={isDarkMode ? webDarkTheme : webLightTheme}>
          <ConnectionBannerContainer />
          <MainLayout
            currentView={currentView}
            onNavigate={setCurrentView}
            onToggleTheme={toggleTheme}
            isDarkMode={isDarkMode}
          >
            {currentView === 'chat' && (
              <ChatWindow
                onNewAttack={handleNewAttack}
                activeTarget={activeTarget}
                attackResultId={attackResultId}
                conversationId={conversationId}
                activeConversationId={activeConversationId}
                onConversationCreated={handleConversationCreated}
                onSelectConversation={handleSelectConversation}
                labels={globalLabels}
                onLabelsChange={setGlobalLabels}
                onNavigate={setCurrentView}
                attackLabels={attackLabels}
                attackTarget={attackTarget}
                isLoadingAttack={isLoadingAttack}
                relatedConversationCount={relatedConversationCount}
              />
            )}
            {currentView === 'config' && (
              <TargetConfig
                activeTarget={activeTarget}
                onSetActiveTarget={handleSetActiveTarget}
              />
            )}
            {currentView === 'history' && (
              <AttackHistory
                onOpenAttack={handleOpenAttack}
                filters={historyFilters}
                onFiltersChange={setHistoryFilters}
              />
            )}
          </MainLayout>
        </FluentProvider>
      </ConnectionHealthProvider>
    </ErrorBoundary>
  )
}

export default App

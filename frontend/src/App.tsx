import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { Routes, Route, Navigate, useNavigate, useLocation, useSearchParams, matchPath } from 'react-router-dom'
import { useMsal } from '@azure/msal-react'
import { Joyride } from 'react-joyride'
import { useTheme } from './hooks/useTheme'
import MainLayout from './components/Layout/MainLayout'
import ChatWindow from './components/Chat/ChatWindow'
import AttackNotFound from './components/Chat/AttackNotFound'
import Home from './components/Home/Home'
import TargetConfig from './components/Config/TargetConfig'
import AttackHistory from './components/History/AttackHistory'
import FeedbackDialog from './components/Feedback/FeedbackDialog'
import type { HistoryFilters } from './components/History/historyFilters'
import { ConnectionBanner } from './components/ConnectionBanner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ConnectionHealthProvider, useConnectionHealth } from './hooks/useConnectionHealth'
import { DEFAULT_GLOBAL_LABELS } from './components/Labels/labelDefaults'
import { filtersFromSearchParams, filtersToSearchParams } from './components/History/historyFilters'
import type { ViewName } from './components/Sidebar/Navigation'
import type { TargetInstance, TargetInfo } from './types'
import {
  targetEndpoint,
  targetIdentifierHash,
  targetModelName,
  targetType,
} from './utils/targetIdentity'
import { attacksApi, versionApi } from './services/api'
import { toApiError } from './services/errors'
import { useTour } from './hooks/useTour'

const AUTO_DISMISS_MS = 5_000

/** Maps each navigable view to its canonical URL path. */
const VIEW_PATHS: Record<ViewName, string> = {
  home: '/',
  chat: '/chat',
  history: '/history',
  config: '/config',
}

/** Resolves the active view from a URL path, defaulting to home for unknown paths. */
function viewFromPath(pathname: string): ViewName {
  const match = (Object.entries(VIEW_PATHS) as [ViewName, string][]).find(
    ([, path]) => path === pathname,
  )
  return match ? match[0] : 'home'
}

/** Status of the in-flight attack load for an /attacks/:id route. */
type AttackLoadStatus = 'loading' | 'success' | 'not-found' | 'error'

/** Attack data named by the URL; `id` marks which attack the data belongs to. */
interface LoadedAttack {
  id: string
  mainConversationId: string | null
  labels: Record<string, string> | null
  target: TargetInfo | null
  relatedConversationIds: string[]
  status: AttackLoadStatus
}

const attackPath = (attackId: string) => `/attacks/${attackId}`
const conversationPath = (attackId: string, conversationId: string) =>
  `/attacks/${attackId}/conversations/${conversationId}`

function ConnectionBannerContainer() {
  const { status, reconnectCount } = useConnectionHealth()
  // Track how many reconnects the user has already had the banner dismissed for.
  // `showReconnected` is derived: the banner is visible whenever there are
  // un-dismissed reconnects. The auto-dismiss timer bumps `dismissedCount` so
  // we avoid calling setState synchronously in an effect body.
  const [dismissedCount, setDismissedCount] = useState(0)
  const showReconnected = reconnectCount > dismissedCount

  useEffect(() => {
    if (!showReconnected) return
    const timer = setTimeout(() => setDismissedCount(reconnectCount), AUTO_DISMISS_MS)
    return () => clearTimeout(timer)
  }, [showReconnected, reconnectCount])

  if (status === 'connected' && !showReconnected) {
    return null
  }

  return <ConnectionBanner status={status} />
}

function App() {
  const { instance } = useMsal()
  const navigate = useNavigate()
  const location = useLocation()

  // The URL is the source of truth for which attack/conversation is open.
  const conversationMatch = matchPath(
    { path: '/attacks/:attackId/conversations/:conversationId', end: true },
    location.pathname,
  )
  const attackMatch = matchPath({ path: '/attacks/:attackId', end: true }, location.pathname)
  const routeAttackId = conversationMatch?.params.attackId ?? attackMatch?.params.attackId ?? null
  const routeConversationId = conversationMatch?.params.conversationId ?? null
  const currentView: ViewName = routeAttackId !== null ? 'chat' : viewFromPath(location.pathname)

  const [activeTarget, setActiveTarget] = useState<TargetInstance | null>(null)
  const [globalLabels, setGlobalLabels] = useState<Record<string, string>>({ ...DEFAULT_GLOBAL_LABELS })

  // History filters live in the URL query string so they are shareable and
  // survive refresh. The breadcrumb ref remembers the last /history query so
  // the History nav button can restore filters after visiting another view.
  const [searchParams, setSearchParams] = useSearchParams()
  const historyFilters = useMemo(() => filtersFromSearchParams(searchParams), [searchParams])
  const lastHistorySearch = useRef('')
  useEffect(() => {
    if (location.pathname === VIEW_PATHS.history) {
      lastHistorySearch.current = location.search
    }
  }, [location.pathname, location.search])

  const handleFiltersChange = useCallback((filters: HistoryFilters) => {
    setSearchParams(filtersToSearchParams(filters), { replace: true })
  }, [setSearchParams])

    /** App version display, attached to feedback context */
  const [appVersion, setAppVersion] = useState<string>('')
  /** Whether the feedback dialog is currently open */
  const [feedbackOpen, setFeedbackOpen] = useState(false)

  /** Attack named by the URL, hydrated by the loader effect below. */
  const [loadedAttack, setLoadedAttack] = useState<LoadedAttack | null>(null)
  // When set, the loader skips exactly one fetch for this id — used after
  // first-message/branch creation seeds the data, avoiding a redundant getAttack.
  const skipNextLoadForAttackId = useRef<string | null>(null)
  // The attack whose deep-linked conversation id we have already validated.
  const validatedConversationForAttack = useRef<string | null>(null)

  // Fetch default labels from backend, then override operator with active account if available
  useEffect(() => {
    let ignore = false

    async function initLabels() {
      let defaultLabels: Record<string, string> = {}
      try {
        const data = await versionApi.getVersion()
        if (data.default_labels && Object.keys(data.default_labels).length > 0) {
          defaultLabels = data.default_labels
        }
        if (data.display || data.version) {
          if (!ignore) setAppVersion(data.display ?? data.version ?? '')
        }
      } catch {
        /* version fetch handled elsewhere */
      }

      if (ignore) return

      const account = instance.getActiveAccount?.()
      const alias = account?.username ? account.username.split('@')[0].toLowerCase() : null

      setGlobalLabels(prev => {
        const next = { ...prev, ...defaultLabels }
        if (alias) {
          next.operator = alias
        }
        return next
      })
    }

    initLabels()
    return () => { ignore = true }
  }, [instance])

  const handleSetActiveTarget = useCallback((target: TargetInstance) => {
    setActiveTarget(prev => {
      const isSame = prev &&
        prev.target_registry_name === target.target_registry_name &&
        targetType(prev) === targetType(target) &&
        (targetEndpoint(prev) ?? '') === (targetEndpoint(target) ?? '') &&
        (targetModelName(prev) ?? '') === (targetModelName(target) ?? '')
      if (isSame) return prev
      // Switching targets no longer clears the loaded attack.  The cross-target
      // guard in ChatWindow prevents sending to a mismatched target, and the
      // backend enforces this server-side as well.  Clearing state here was
      // confusing because navigating to config to pick the *correct* target
      // would wipe the conversation the user was trying to continue.
      return target
    })
  }, [])
  // Hydrate loadedAttack from the routed attack id. Depends on routeAttackId
  // ONLY, so switching conversations within an attack never refetches.
  useEffect(() => {
    if (!routeAttackId) {
      // Intentional cleanup of async-sourced state, not a derivable render value.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoadedAttack(null)
      validatedConversationForAttack.current = null
      return
    }
    if (skipNextLoadForAttackId.current === routeAttackId) {
      skipNextLoadForAttackId.current = null
      return
    }
    let cancelled = false
    setLoadedAttack({
      id: routeAttackId,
      status: 'loading',
      mainConversationId: null,
      labels: null,
      target: null,
      relatedConversationIds: [],
    })
    attacksApi
      .getAttack(routeAttackId)
      .then(attack => {
        if (cancelled) return
        setLoadedAttack({
          id: routeAttackId,
          mainConversationId: attack.conversation_id,
          labels: attack.labels ?? {},
          target: attack.target ?? null,
          relatedConversationIds: attack.related_conversation_ids ?? [],
          status: 'success',
        })
      })
      .catch(err => {
        if (cancelled) return
        // A genuine 404 means the id is wrong/deleted; any other failure
        // (network, timeout, 5xx) is transient and must not be reported as
        // "not found", which would wrongly imply the attack does not exist.
        const isMissing = toApiError(err).status === 404
        setLoadedAttack({
          id: routeAttackId,
          status: isMissing ? 'not-found' : 'error',
          mainConversationId: null,
          labels: null,
          target: null,
          relatedConversationIds: [],
        })
      })
    // Drop a stale response once the route has moved on to another attack.
    return () => { cancelled = true }
  }, [routeAttackId])

  // Only the attack named by the current URL may drive the chat. While a new
  // attack is loading, loadedAttack still holds the previous one, so this keeps
  // its data from being mixed with the new route's id (the stale-conv guard).
  const attackForRoute = loadedAttack && loadedAttack.id === routeAttackId ? loadedAttack : null
  const readyAttack = attackForRoute?.status === 'success' ? attackForRoute : null
  const isAttackNotFound = attackForRoute?.status === 'not-found'
  const isAttackError = attackForRoute?.status === 'error'
  const isLoadingAttack = routeAttackId !== null && !readyAttack && !isAttackNotFound && !isAttackError
  const activeConversationId = readyAttack
    ? routeConversationId ?? readyAttack.mainConversationId
    : null

  // Validate a deep-linked conversation id once per attack load. In-app
  // conversation navigation is trusted (it targets conversations ChatWindow
  // just created or listed), so only the initial URL is checked.
  useEffect(() => {
    if (!readyAttack) return
    if (validatedConversationForAttack.current === readyAttack.id) return
    validatedConversationForAttack.current = readyAttack.id
    if (routeConversationId) {
      const isKnown =
        routeConversationId === readyAttack.mainConversationId ||
        readyAttack.relatedConversationIds.includes(routeConversationId)
      if (!isKnown) {
        navigate(attackPath(readyAttack.id), { replace: true })
      }
    }
  }, [readyAttack, routeConversationId, navigate])

  const handleNavigate = useCallback((view: ViewName) => {
    // Re-attach the last filter query so returning to history restores filters.
    if (view === 'history') {
      navigate(VIEW_PATHS.history + lastHistorySearch.current)
      return
    }
    navigate(VIEW_PATHS[view])
  }, [navigate])

  const handleNewAttack = useCallback(() => {
    navigate(VIEW_PATHS.chat)
  }, [navigate])

  const handleConversationCreated = useCallback((arId: string, convId: string) => {
    // Seed the freshly-created attack synchronously and tell the loader to skip
    // its next fetch for this id, so the attack opens without a redundant load.
    const target: TargetInfo | null = activeTarget
      ? {
          target_type: targetType(activeTarget),
          endpoint: targetEndpoint(activeTarget),
          model_name: targetModelName(activeTarget),
          identifier_hash: targetIdentifierHash(activeTarget),
        }
      : null
    skipNextLoadForAttackId.current = arId
    setLoadedAttack({
      id: arId,
      mainConversationId: convId,
      // New attack uses the current user's labels, so it is never operator-locked.
      labels: null,
      target,
      relatedConversationIds: [],
      status: 'success',
    })
    // Replace when promoting an empty /chat to its attack url (first message);
    // push when branching from an existing attack so Back returns to the source.
    navigate(attackPath(arId), { replace: routeAttackId === null })
  }, [activeTarget, routeAttackId, navigate])

  const handleSelectConversation = useCallback((convId: string) => {
    if (!routeAttackId) return
    navigate(conversationPath(routeAttackId, convId))
  }, [routeAttackId, navigate])

  const handleOpenAttack = useCallback((openAttackResultId: string) => {
    navigate(attackPath(openAttackResultId))
  }, [navigate])

  const chatElement = isAttackNotFound || isAttackError ? (
    <AttackNotFound
      attackId={routeAttackId ?? ''}
      variant={isAttackError ? 'error' : 'not-found'}
      onStartNew={() => navigate(VIEW_PATHS.chat)}
      onBackToHistory={() => navigate(VIEW_PATHS.history)}
    />
  ) : (
    <ChatWindow
      onNewAttack={handleNewAttack}
      activeTarget={activeTarget}
      attackResultId={readyAttack ? readyAttack.id : null}
      conversationId={readyAttack ? readyAttack.mainConversationId : null}
      activeConversationId={activeConversationId}
      onConversationCreated={handleConversationCreated}
      onSelectConversation={handleSelectConversation}
      labels={globalLabels}
      onLabelsChange={setGlobalLabels}
      onNavigate={handleNavigate}
      attackLabels={readyAttack ? readyAttack.labels : null}
      attackTarget={readyAttack ? readyAttack.target : null}
      isLoadingAttack={isLoadingAttack}
      relatedConversationCount={readyAttack ? readyAttack.relatedConversationIds.length : 0}
    />
  )

  // Onboarding tour — pass handleNavigate so the tour can switch views between steps.
  // The tour does not auto-start; users launch it from the "Take a tour" button in the top bar.
  const { resolved } = useTheme()
  const { startTour, tourProps } = useTour(handleNavigate, resolved === 'dark', currentView)

  return (
    <ErrorBoundary>
      <ConnectionHealthProvider>
          <Joyride {...tourProps} />
          <ConnectionBannerContainer />
          <MainLayout
            currentView={currentView}
            onNavigate={handleNavigate}
            onOpenFeedback={() => setFeedbackOpen(true)}
            onStartTour={startTour}
          >
            <Routes>
              <Route
                path="/"
                element={
                  <Home
                    labels={globalLabels}
                    onLabelsChange={setGlobalLabels}
                    activeTarget={activeTarget}
                    onNavigate={handleNavigate}
                    onOpenAttack={handleOpenAttack}
                  />
                }
              />
              <Route
                path="/chat"
                element={chatElement}
              />
              <Route
                path="/attacks/:attackId"
                element={chatElement}
              />
              <Route
                path="/attacks/:attackId/conversations/:conversationId"
                element={chatElement}
              />
              <Route
                path="/config"
                element={
                  <TargetConfig
                    activeTarget={activeTarget}
                    onSetActiveTarget={handleSetActiveTarget}
                  />
                }
              />
              <Route
                path="/history"
                element={
                  <AttackHistory
                    onOpenAttack={handleOpenAttack}
                    filters={historyFilters}
                    onFiltersChange={handleFiltersChange}
                  />
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </MainLayout>
          {feedbackOpen && (
            <FeedbackDialog
              open={feedbackOpen}
              onClose={() => setFeedbackOpen(false)}
              context={{
                app_version: appVersion || undefined,
                current_view: currentView,
                target_type: activeTarget ? targetType(activeTarget) : undefined,
              }}
            />
          )}
      </ConnectionHealthProvider>
    </ErrorBoundary>
  )
}

export default App

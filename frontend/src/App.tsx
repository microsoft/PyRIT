import { useState, useCallback, useEffect, useRef, useMemo, useTransition } from 'react'
import { Routes, Route, Navigate, useNavigate, useLocation, useParams, useSearchParams, matchPath } from 'react-router'
import { useMsal } from '@azure/msal-react'
import { Button, MessageBar, MessageBarBody, Spinner } from '@fluentui/react-components'
import { Joyride } from 'react-joyride'
import { ThemeProvider, useTheme } from './hooks/useTheme'
import { UserPreferencesProvider, useUserPreferences } from './hooks/useUserPreferences'
import MainLayout from './components/Layout/MainLayout'
import ChatWindow from './components/Chat/ChatWindow'
import AttackNotFound from './components/Chat/AttackNotFound'
import Home from './components/Home/Home'
import TargetConfig from './components/Config/TargetConfig'
import ConverterRegistry from './components/Registry/ConverterRegistry'
import RegistryLayout from './components/Registry/RegistryLayout'
import Configuration from './components/Configuration/Configuration'
import AttackHistory from './components/History/AttackHistory'
import HistoryPage from './components/History/HistoryPage'
import type { HistoryTab } from './components/History/HistoryPage'
import ScenarioHistory from './components/History/ScenarioHistory'
import ScenarioCatalog from './components/Scenarios/ScenarioCatalog'
import ScenarioDetail from './components/Scenarios/ScenarioDetail'
import ScenarioRunPage from './components/Scenarios/ScenarioRunPage'
import FeedbackDialog from './components/Feedback/FeedbackDialog'
import type { HistoryFilters } from './components/History/historyFilters'
import { ConnectionBanner } from './components/ConnectionBanner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { useAttackTargetResolution } from './hooks/useAttackTargetResolution'
import { useAuthConfig } from './auth/AuthConfigContext'
import { useTargetPreferences } from './hooks/useTargetPreferences'
import { useTargetRegistry } from './hooks/useTargetRegistry'
import { ConnectionHealthProvider, useConnectionHealth } from './hooks/useConnectionHealth'
import { DEFAULT_GLOBAL_LABELS } from './components/Labels/labelDefaults'
import { filtersFromSearchParams, filtersToSearchParams } from './components/History/historyFilters'
import {
  scenarioHistoryFiltersFromSearchParams,
  scenarioHistoryFiltersToSearchParams,
} from './components/History/scenarioHistoryFilters'
import type { ScenarioHistoryFilters } from './components/History/scenarioHistoryFilters'
import type { ViewName } from './components/Sidebar/Navigation'
import type { AttackOutcome, AttackSummary, BackendScore, TargetInfo, TargetInstance, TargetPreferences, TargetReference, UserPreferences } from './types'
import {
  resolveTargetReference,
  targetReference,
  targetEndpoint,
  targetIdentifierHash,
  targetModelName,
  targetType,
} from './utils/targetIdentity'
import { attacksApi, authApi, versionApi } from './services/api'
import { toApiError } from './services/errors'
import { useTour } from './hooks/useTour'
import {
  attackConversationRoutePath,
  attackRoutePath,
  routerPathParamValue,
  scenarioRunProvenance,
  scenarioRunRoutePath,
} from './utils/routeParams'

const AUTO_DISMISS_MS = 5_000
const HISTORY_ATTACKS_PATH = '/history/attacks'
const HISTORY_SCANNER_PATH = '/history/scanner'

/** Maps each navigable view to its canonical URL path. */
const VIEW_PATHS: Record<ViewName, string> = {
  home: '/',
  chat: '/chat',
  history: HISTORY_ATTACKS_PATH,
  registry: '/registry/targets',
  scenarios: '/scanner',
  configuration: '/config',
}

/**
 * Resolves the active view from a URL path, defaulting to home for unknown
 * paths. Scanner catalog routes and persisted scanner-history routes are
 * prefix-matched since they carry path parameters rather than single canonical
 * `VIEW_PATHS` entries.
 */
function viewFromPath(pathname: string): ViewName {
  if (pathname === '/history' || pathname.startsWith('/history/') || pathname.startsWith('/scanner-history/')) {
    return 'history'
  }
  if (pathname === '/targets' || pathname.startsWith('/registry')) {
    return 'registry'
  }
  if (
    pathname === VIEW_PATHS.scenarios
    || pathname.startsWith(`${VIEW_PATHS.scenarios}/`)
  ) {
    return 'scenarios'
  }
  const match = (Object.entries(VIEW_PATHS) as [ViewName, string][]).find(
    ([, path]) => path === pathname,
  )
  return match ? match[0] : 'home'
}

function LegacyScenarioRunRedirect() {
  const { scenarioResultId } = useParams<{ scenarioResultId: string }>()
  return <Navigate replace to={scenarioRunRoutePath(routerPathParamValue(scenarioResultId))} />
}

function LegacyScenarioHistoryRedirect() {
  const location = useLocation()
  return <Navigate replace to={`${HISTORY_SCANNER_PATH}${location.search}`} />
}

function LegacyAttackHistoryRedirect() {
  const location = useLocation()
  return <Navigate replace to={`${HISTORY_ATTACKS_PATH}${location.search}`} />
}

/** Status of the in-flight attack load for an /attacks/:id route. */
type AttackLoadStatus = 'loading' | 'success' | 'not-found' | 'error'

/** Attack data named by the URL; `id` marks which attack the data belongs to. */
interface LoadedAttack {
  id: string
  loadSequence: number
  targetSource: 'persisted' | 'created'
  mainConversationId: string | null
  labels: Record<string, string> | null
  operator: string | null
  target: TargetInfo | null
  createdTarget?: TargetInstance
  relatedConversationIds: string[]
  objective: string
  outcome: NonNullable<AttackSummary['outcome']>
  automatedScore: BackendScore | null
  humanScore: BackendScore | null
  lastResponseMessagePieceId: string | null
  status: AttackLoadStatus
}

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

function AppContent({ operatorAlias }: { operatorAlias: string | null }) {
  const navigate = useNavigate()
  const [isNavigatingToCreatedAttack, startCreatedAttackTransition] = useTransition()
  const location = useLocation()
  const registry = useTargetRegistry()
  const { preferences, updatePreferences, error: preferenceError } = useUserPreferences()
  const targetDefaults = useTargetPreferences(registry.targets)
  const [draftSession, setDraftSession] = useState<{
    pathname: string
    key: number
    target: TargetReference | null
  }>({ pathname: location.pathname, key: 0, target: targetDefaults.preferences.objective })
  if (draftSession.pathname !== location.pathname) {
    setDraftSession({
      pathname: location.pathname,
      key: draftSession.key + (location.pathname === VIEW_PATHS.chat ? 1 : 0),
      target: targetDefaults.preferences.objective,
    })
  }
  const draftTarget = !registry.loading && !registry.error && draftSession.target
    ? resolveTargetReference(draftSession.target, registry.targets) : null
  const setDefaultTarget = (role: keyof TargetPreferences, target: TargetInstance | null): void => {
    if (target) registry.rememberTarget(target)
    targetDefaults.setDefault(role, target)
  }

  // The URL is the source of truth for which attack/conversation is open.
  const conversationMatch = matchPath(
    { path: '/attacks/:attackId/conversations/:conversationId', end: true },
    location.pathname,
  )
  const attackMatch = matchPath({ path: '/attacks/:attackId', end: true }, location.pathname)
  const routeAttackId = conversationMatch?.params.attackId ?? attackMatch?.params.attackId ?? null
  const routeConversationId = conversationMatch?.params.conversationId ?? null
  const currentView: ViewName = routeAttackId !== null ? 'chat' : viewFromPath(location.pathname)
  const [canManageConfiguration, setCanManageConfiguration] = useState(false)
  const [chatToolbarContainer, setChatToolbarContainer] = useState<HTMLDivElement | null>(null)

  useEffect(() => {
    let cancelled = false
    authApi.getAccess()
      .then((access) => {
        if (!cancelled) setCanManageConfiguration(access.isAdmin)
      })
      .catch(() => {
        if (!cancelled) setCanManageConfiguration(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const [defaultLabels, setDefaultLabels] = useState<Record<string, string>>(DEFAULT_GLOBAL_LABELS)
  const globalLabels = useMemo<Record<string, string>>(() => Object.fromEntries(
    Object.entries({
      ...defaultLabels,
      ...preferences.labels,
      ...(operatorAlias ? { operator: operatorAlias } : {}),
    }).filter((entry): entry is [string, string] => entry[1] !== null),
  ), [defaultLabels, preferences.labels, operatorAlias])

  const handleGlobalLabelsChange = useCallback((labels: Record<string, string>) => {
    const changedKeys = Object.keys({ ...globalLabels, ...labels })
      .filter((key: string) => !(operatorAlias && key === 'operator') && labels[key] !== globalLabels[key])
    updatePreferences((current: UserPreferences) => {
      const overrides = { ...current.labels }
      for (const key of changedKeys) {
        if (labels[key] === defaultLabels[key]) {
          delete overrides[key]
        } else {
          overrides[key] = labels[key] ?? null
        }
      }
      return { ...current, labels: overrides }
    })
  }, [defaultLabels, globalLabels, operatorAlias, updatePreferences])

  // History filters live in the URL query string so they are shareable and
  // survive refresh. The breadcrumb ref remembers the last /history query so
  // the History nav button can restore filters after visiting another view.
  const [searchParams, setSearchParams] = useSearchParams()
  const historyFilters = useMemo(() => filtersFromSearchParams(searchParams), [searchParams])
  const scenarioHistoryFilters = useMemo(
    () => scenarioHistoryFiltersFromSearchParams(searchParams),
    [searchParams],
  )
  const scenarioResultId = useMemo(
    () => scenarioRunProvenance(searchParams),
    [searchParams],
  )
  const lastHistorySearch = useRef('')
  const lastScenarioHistorySearch = useRef('')
  useEffect(() => {
    if (location.pathname === HISTORY_ATTACKS_PATH) {
      lastHistorySearch.current = location.search
    }
    if (location.pathname === HISTORY_SCANNER_PATH) {
      lastScenarioHistorySearch.current = location.search
    }
  }, [location.pathname, location.search])

  const handleFiltersChange = useCallback((filters: HistoryFilters) => {
    setSearchParams(filtersToSearchParams(filters), { replace: true })
  }, [setSearchParams])

  const handleScenarioHistoryFiltersChange = useCallback((filters: ScenarioHistoryFilters) => {
    setSearchParams(scenarioHistoryFiltersToSearchParams(filters), { replace: true })
  }, [setSearchParams])

  const handleHistoryTabChange = useCallback((tab: HistoryTab) => {
    const path = tab === 'attacks' ? HISTORY_ATTACKS_PATH : HISTORY_SCANNER_PATH
    const search = tab === 'attacks' ? lastHistorySearch.current : lastScenarioHistorySearch.current
    navigate(path + search)
  }, [navigate])

  /** App version display, attached to feedback context */
  const [appVersion, setAppVersion] = useState<string>('')
  /** Whether the feedback dialog is currently open */
  const [feedbackOpen, setFeedbackOpen] = useState(false)

  /** Attack named by the URL, hydrated by the loader effect below. */
  const [loadedAttack, setLoadedAttack] = useState<LoadedAttack | null>(null)
  // When set, the loader skips exactly one fetch for this id — used after
  // first-message/branch creation seeds the data, avoiding a redundant getAttack.
  const skipNextLoadForAttackId = useRef<string | null>(null)
  const attackLoadSequence = useRef(0)
  // The attack whose deep-linked conversation id we have already validated.
  const validatedConversationForAttack = useRef<string | null>(null)

  // User choices remain separate, so a late response cannot replace an edit.
  useEffect(() => {
    let ignore = false

    async function initLabels() {
      try {
        const data = await versionApi.getVersion()
        if (ignore) return
        setDefaultLabels({ ...DEFAULT_GLOBAL_LABELS, ...data.default_labels })
        if (data.display || data.version) {
          if (!ignore) setAppVersion(data.display ?? data.version ?? '')
        }
      } catch {
        /* version fetch handled elsewhere */
      }

    }

    initLabels()
    return () => { ignore = true }
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
    const loadSequence = attackLoadSequence.current + 1
    attackLoadSequence.current = loadSequence
    setLoadedAttack({
      id: routeAttackId,
      loadSequence,
      targetSource: 'persisted',
      status: 'loading',
      mainConversationId: null,
      labels: null,
      operator: null,
      target: null,
      relatedConversationIds: [],
      objective: '',
      outcome: 'undetermined',
      automatedScore: null,
      humanScore: null,
      lastResponseMessagePieceId: null,
    })
    attacksApi
      .getAttack(routeAttackId)
      .then(attack => {
        if (cancelled) return
        setLoadedAttack({
          id: routeAttackId,
          loadSequence,
          targetSource: 'persisted',
          mainConversationId: attack.conversation_id,
          labels: attack.labels ?? {},
          operator: attack.operator ?? null,
          target: attack.target ?? null,
          relatedConversationIds: attack.related_conversations
            ? attack.related_conversations
                .filter((reference) => reference.conversation_type === 'pruned')
                .map((reference) => reference.conversation_id)
            : (attack.related_conversation_ids ?? []),
          objective: attack.objective ?? '',
          outcome: attack.outcome ?? 'undetermined',
          automatedScore: attack.automated_score ?? null,
          humanScore: attack.human_score ?? null,
          lastResponseMessagePieceId: attack.last_response?.id ?? null,
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
          loadSequence,
          targetSource: 'persisted',
          status: isMissing ? 'not-found' : 'error',
          mainConversationId: null,
          labels: null,
          operator: null,
          target: null,
          relatedConversationIds: [],
          objective: '',
          outcome: 'undetermined',
          automatedScore: null,
          humanScore: null,
          lastResponseMessagePieceId: null,
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
  const isLoadingAttack = isNavigatingToCreatedAttack
    || (routeAttackId !== null && !readyAttack && !isAttackNotFound && !isAttackError)
  const {
    activeTarget: resolvedChatTarget,
    resolutionStatus: targetResolutionStatus,
    retryResolution: retryTargetResolution,
  } = useAttackTargetResolution({
    attackId: readyAttack?.id ?? null,
    attackLoadSequence: readyAttack?.loadSequence ?? 0,
    attackTarget: readyAttack?.target ?? null,
    attackTargetSource: readyAttack?.targetSource ?? 'persisted',
    createdTarget: readyAttack?.createdTarget,
  })
  const activeTarget = routeAttackId ? resolvedChatTarget : draftTarget
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
        navigate(attackRoutePath(readyAttack.id, scenarioResultId), { replace: true })
      }
    }
  }, [readyAttack, routeConversationId, navigate, scenarioResultId])

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

  const handleConversationCreated = useCallback((
    arId: string,
    convId: string,
    objective?: string,
    selectedTarget?: TargetInstance,
  ) => {
    // Seed the freshly-created attack synchronously and tell the loader to skip
    // its next fetch for this id, so the attack opens without a redundant load.
    const createdTarget = selectedTarget ?? activeTarget
    const target: TargetInfo | null = createdTarget
      ? {
          target_type: targetType(createdTarget),
          target_registry_name: createdTarget.target_registry_name,
          endpoint: targetEndpoint(createdTarget),
          model_name: targetModelName(createdTarget),
          identifier_hash: targetIdentifierHash(createdTarget),
        }
      : null
    skipNextLoadForAttackId.current = arId
    const loadSequence = attackLoadSequence.current + 1
    attackLoadSequence.current = loadSequence
    setLoadedAttack({
      id: arId,
      loadSequence,
      targetSource: 'created',
      mainConversationId: convId,
      // New attack uses the current user's labels, so it is never operator-locked.
      labels: null,
      operator: null,
      target,
      createdTarget: createdTarget ?? undefined,
      relatedConversationIds: [],
      objective: objective ?? '',
      outcome: 'undetermined',
      automatedScore: null,
      humanScore: null,
      lastResponseMessagePieceId: null,
      status: 'success',
    })
    // Replace when promoting an empty /chat to its attack url (first message);
    // push when branching from an existing attack so Back returns to the source.
    // Keep sends blocked until the new route exposes the created attack's identity.
    startCreatedAttackTransition(() => {
      navigate(attackRoutePath(arId), { replace: routeAttackId === null })
    })
  }, [activeTarget, routeAttackId, navigate, startCreatedAttackTransition])

  const handleObjectiveChange = useCallback((objective: string) => {
    setLoadedAttack((current) => current ? { ...current, objective } : current)
  }, [])

  const handleHumanScoreChange = useCallback((humanScore: BackendScore | null, outcome: AttackOutcome) => {
    setLoadedAttack((current) => current?.id === routeAttackId ? { ...current, humanScore, outcome } : current)
  }, [routeAttackId])

  const handleAttackChange = useCallback((attack: AttackSummary) => {
    setLoadedAttack((current) => (
      current && current.id === attack.attack_result_id
        ? {
            ...current,
            objective: attack.objective ?? '',
            outcome: attack.outcome ?? 'undetermined',
            automatedScore: attack.automated_score ?? null,
            humanScore: attack.human_score ?? null,
            lastResponseMessagePieceId: attack.last_response?.id ?? null,
          }
        : current
    ))
  }, [])

  const handleSelectConversation = useCallback((convId: string) => {
    if (!routeAttackId) return
    navigate(attackConversationRoutePath(routeAttackId, convId, scenarioResultId))
  }, [routeAttackId, navigate, scenarioResultId])

  const handleOpenAttack = useCallback((openAttackResultId: string) => {
    navigate(attackRoutePath(openAttackResultId))
  }, [navigate])

  const handleOpenScenarioRun = useCallback((scenarioResultId: string) => {
    navigate(scenarioRunRoutePath(scenarioResultId), {
      state: {
        fromScenarioHistory: true,
        scenarioHistorySearch: location.search,
      },
    })
  }, [location.search, navigate])

  const chatElement = isAttackNotFound || isAttackError ? (
    <AttackNotFound
      attackId={routeAttackId ?? ''}
      variant={isAttackError ? 'error' : 'not-found'}
      onStartNew={() => navigate(VIEW_PATHS.chat)}
      onBackToHistory={() => navigate(VIEW_PATHS.history)}
    />
  ) : (
    <ChatWindow
      key={draftSession.key}
      toolbarContainer={chatToolbarContainer}
      onNewAttack={handleNewAttack}
      activeTarget={activeTarget}
      availableTargets={registry.targets}
      targetsLoading={registry.loading}
      targetsError={registry.error}
      onRefreshTargets={registry.refresh}
      onSelectTarget={(target: TargetInstance | null) => setDraftSession((current) => ({
        ...current, target: target ? targetReference(target) : null,
      }))}
      defaultBranchTarget={targetDefaults.objectiveTarget}
      attackResultId={readyAttack ? readyAttack.id : null}
      conversationId={readyAttack ? readyAttack.mainConversationId : null}
      activeConversationId={activeConversationId}
      onConversationCreated={handleConversationCreated}
      onSelectConversation={handleSelectConversation}
      onObjectiveChange={handleObjectiveChange}
      onHumanScoreChange={handleHumanScoreChange}
      onAttackChange={handleAttackChange}
      labels={globalLabels}
      onNavigate={handleNavigate}
      attackOperator={readyAttack ? readyAttack.operator : null}
      attackTarget={readyAttack ? readyAttack.target : null}
      targetResolutionStatus={targetResolutionStatus}
      onRetryTargetResolution={retryTargetResolution}
      isLoadingAttack={isLoadingAttack}
      relatedConversationCount={readyAttack ? readyAttack.relatedConversationIds.length : 0}
      objective={readyAttack ? readyAttack.objective : ''}
      outcome={readyAttack?.outcome}
      automatedScore={readyAttack?.automatedScore}
      humanScore={readyAttack?.humanScore}
      lastResponseMessagePieceId={readyAttack?.lastResponseMessagePieceId}
      scenarioResultId={readyAttack ? scenarioResultId : null}
    />
  )

  // Onboarding tour — pass handleNavigate so the tour can switch views between steps.
  // The tour does not auto-start; users launch it from the "Take a tour" button in the top bar.
  const { resolved } = useTheme()
  const { startTour, tourProps } = useTour(
    handleNavigate,
    resolved === 'dark',
    currentView,
    targetDefaults.objectiveTarget !== null,
  )

  return (
    <ErrorBoundary>
      <ConnectionHealthProvider>
          <Joyride {...tourProps} />
          <ConnectionBannerContainer />
          <MainLayout
            currentView={currentView}
            onNavigate={handleNavigate}
            onOpenFeedback={() => setFeedbackOpen(true)}
            canManageConfiguration={canManageConfiguration}
            onStartTour={startTour}
            labels={globalLabels}
            onLabelsChange={handleGlobalLabelsChange}
            operatorReadOnly={Boolean(operatorAlias)}
            toolbarRef={setChatToolbarContainer}
          >
            {preferenceError && (
              <MessageBar intent="warning">
                <MessageBarBody>{preferenceError}</MessageBarBody>
              </MessageBar>
            )}
            {!registry.loading && !registry.error && (
              (['objective', 'adversarial'] as const).map((role) => (
                targetDefaults.preferences[role]
                && !(role === 'objective' ? targetDefaults.objectiveTarget : targetDefaults.adversarialTarget)
                ? (
                  <MessageBar key={role} intent="warning">
                    <MessageBarBody>
                      The saved default {role} target is unavailable or has changed. Select a new default in the registry.
                      <Button appearance="subtle" onClick={() => setDefaultTarget(role, null)}>
                        Clear {role} default
                      </Button>
                    </MessageBarBody>
                  </MessageBar>
                ) : null
              ))
            )}
            <Routes>
              <Route
                path="/"
                element={
                  <Home
                    activeTarget={targetDefaults.objectiveTarget}
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
              <Route path="/registry" element={<RegistryLayout />}>
                <Route index element={<Navigate to="/registry/targets" replace />} />
                <Route
                  path="targets"
                  element={
                    <TargetConfig
                      defaultObjectiveTarget={targetDefaults.objectiveTarget}
                      defaultAdversarialTarget={targetDefaults.adversarialTarget}
                      onSetDefaultObjectiveTarget={(target: TargetInstance | null) => setDefaultTarget('objective', target)}
                      onSetDefaultAdversarialTarget={(target: TargetInstance | null) => setDefaultTarget('adversarial', target)}
                      onTargetsLoaded={registry.synchronizeTargets}
                    />
                  }
                />
                <Route path="converters" element={<ConverterRegistry />} />
              </Route>
              <Route path="/targets" element={<Navigate to="/registry/targets" replace />} />
              <Route path="/scanner" element={<ScenarioCatalog />} />
              <Route
                path="/scanner/:scenarioName"
                element={
                  registry.loading ? <Spinner label="Loading target defaults..." /> : registry.error ? (
                    <MessageBar intent="error">
                      <MessageBarBody>
                        {registry.error}
                        <Button onClick={registry.refresh}>Retry targets</Button>
                      </MessageBarBody>
                    </MessageBar>
                  ) : <ScenarioDetail
                    targets={registry.targets}
                    defaultObjectiveTarget={targetDefaults.objectiveTarget}
                    defaultAdversarialTarget={targetDefaults.adversarialTarget}
                    labels={globalLabels}
                    onNavigate={handleNavigate}
                  />
                }
              />
              <Route path="/scanner-history" element={<LegacyScenarioHistoryRedirect />} />
              <Route path="/scanner-history/:scenarioResultId/:attackResultId" element={<ScenarioRunPage />} />
              <Route path="/scanner-history/:scenarioResultId" element={<ScenarioRunPage />} />
              <Route path="/scenario-history" element={<LegacyScenarioHistoryRedirect />} />
              <Route path="/scenario-history/:scenarioResultId" element={<LegacyScenarioRunRedirect />} />
              <Route path="/config" element={<Configuration />} />
              <Route path="/history" element={<LegacyAttackHistoryRedirect />} />
              <Route
                path={HISTORY_ATTACKS_PATH}
                element={
                  <HistoryPage selectedTab="attacks" onTabChange={handleHistoryTabChange}>
                    <AttackHistory
                      onOpenAttack={handleOpenAttack}
                      filters={historyFilters}
                      onFiltersChange={handleFiltersChange}
                      activeTarget={targetDefaults.objectiveTarget}
                      onNavigate={handleNavigate}
                      showTitle={false}
                    />
                  </HistoryPage>
                }
              />
              <Route
                path={HISTORY_SCANNER_PATH}
                element={
                  <HistoryPage selectedTab="scanner" onTabChange={handleHistoryTabChange}>
                    <ScenarioHistory
                      filters={scenarioHistoryFilters}
                      onFiltersChange={handleScenarioHistoryFiltersChange}
                      onOpenRun={handleOpenScenarioRun}
                      onNavigate={handleNavigate}
                      showTitle={false}
                    />
                  </HistoryPage>
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

function App() {
  const { instance, accounts = [] } = useMsal()
  const authConfig = useAuthConfig()
  const account = instance.getActiveAccount?.() ?? accounts[0]
  const accountKey = account?.homeAccountId
    ? `${account.tenantId}:${account.homeAccountId}`
    : authConfig.clientId ? null : 'local'
  const operatorAlias = account?.username ? account.username.split('@')[0].toLowerCase() : null
  return (
    <UserPreferencesProvider key={accountKey ?? 'account-loading'} accountKey={accountKey}>
      <ThemeProvider>
        <AppContent operatorAlias={operatorAlias} />
      </ThemeProvider>
    </UserPreferencesProvider>
  )
}

export default App

import { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from 'react'
import type { ChangeEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Button,
  Field,
  Input,
  Breadcrumb,
  BreadcrumbDivider,
  BreadcrumbItem,
  Drawer,
  Menu,
  MenuItem,
  MenuList,
  MenuPopover,
  MenuTrigger,
  mergeClasses,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  Spinner,
  Switch,
  Text,
  Tooltip,
  useRestoreFocusSource,
  useRestoreFocusTarget,
} from '@fluentui/react-components'
import type { SwitchOnChangeData } from '@fluentui/react-components'
import { AddRegular, ArrowDownloadRegular, ArrowShuffleRegular, EditRegular, PanelRightRegular } from '@fluentui/react-icons'
import { Link, useNavigate } from 'react-router'
import MessageList from './MessageList'
import ChatInputArea from './ChatInputArea'
import MultiSendProgress from './MultiSendProgress'
import ConversationPanel from './ConversationPanel'
import FindingEvidenceDialog from './FindingEvidenceDialog'
import ConverterPanel from './ConverterPanel'
import TargetBadge from './TargetBadge'
import ChatTargetPicker from './ChatTargetPicker'
import { generateClientId } from '@/utils/clientId'
import ObjectiveHeader from './ObjectiveHeader'
import ConversationEditor from './ConversationEditor'
import type { ConversationEditorHandle } from './ConversationEditor'
import {
  attachmentDraft, draftDataTypes, draftMessageToComposer, editorTargetDisabledReason, historyReplayDisabledReason,
  newDraftPiece, toConversationDraft,
} from '@/utils/conversationDraft'
import { useConversationSave } from '@/hooks/useConversationSave'
import { useConversationDraft } from '@/hooks/useConversationDraft'
import { useConversationMessages } from '@/hooks/useConversationMessages'
import { useAttackMutations } from '@/hooks/useAttackMutations'
import { useConversationExport } from '@/hooks/useConversationExport'
import type { PieceConversion } from './converterTypes'
import { useChatConverters } from '@/hooks/useChatConverters'
import { useRuntime } from '@/hooks/useRuntime'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import { buildTemperatureTarget } from '@/services/targetRegistry'
import {
  basenameFromValue,
  applyConvertedValues,
  buildMediaUrl,
  buildDraftPieceIds,
  buildConverterInputs,
  buildRequestConverterConfigurations,
  dataTypeToAttachmentKind,
  isPathDataType,
  withDraftIdentity,
} from './converterTypes'
import type { ChatInputAreaHandle } from './ChatInputArea'
import { attacksApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import {
  buildMessagePieces,
  backendMessageToOriginalDraft,
  backendMessagesToFrontend,
} from '../../utils/messageMapper'
import type {
  AddMessageResponse,
  AttackOutcome,
  AttackSummary,
  AttackTargetResolutionStatus,
  BackendMessage,
  BackendScore,
  ChatSendOutcome,
  ChatConverterSnapshot,
  ConversationMessagesResponse,
  ConversationDraftPiece,
  ConverterPipelineStage,
  CreateAttackRequest,
  CreateConversationRequest,
  Message,
  MessageAttachment,
  MessageSendConversation,
  MessageSendRequest,
  MessageSendStatus,
  MultiSendOptions,
  NewAttackContext,
  TargetInstance,
  TargetInfo,
} from '../../types'
import { isTargetResolutionBlocking, targetInfoMatchesTarget } from '../../utils/targetIdentity'
import { scenarioRunRoutePath } from '../../utils/routeParams'
import type { ViewName } from '../Sidebar/Navigation'
import { CHAT_NOTICE_FADE_DURATION_MS, useChatWindowStyles } from './ChatWindow.styles'

const NARROW_SCREEN_QUERY = '(max-width: 600px)'
const RETRYABLE_TARGET_RESPONSE_ERROR = 'processing'
const CHAT_NOTICE_VISIBLE_DURATION_MS = 5_000

interface ChatInfoNotice {
  readonly id: string
  readonly message: string
  readonly fading: boolean
}

type ConversationCopyDestination = 'same_attack' | 'new_attack' | 'technique'

interface RecoverableSendDraft {
  history: Array<Pick<BackendMessage, 'role'>>
  conversationId: string
  failedRequestTurnNumber: number
  failedResponseTurnNumber: number
  historyCutoffIndex: number
  failedRequestMessageIndex: number
  originalValue: string
  textMetadata?: Record<string, unknown>
  attachments: MessageAttachment[]
  conversions: Record<string, PieceConversion>
  source: 'live' | 'persisted'
  converterGeneration?: string
  pipelines?: Record<string, ConverterPipelineStage[]>
  missingConverterSelections: boolean
}

interface PendingSend {
  readonly submissionId: string
  readonly controller: AbortController
  readonly draftRevision: number | undefined
  readonly originalValue: string
  readonly textMetadata?: Record<string, unknown>
  readonly attachments: MessageAttachment[]
  readonly conversions: Record<string, PieceConversion>
  readonly priorUserPieceIds: Set<string>
  readonly initialMessages: Message[]
  readonly navigationRevision: number
  readonly converterGeneration: string
  readonly converterSnapshot?: ChatConverterSnapshot
  readonly pipelines?: Record<string, ConverterPipelineStage[]>
  attackResultId: string | null
  conversationId: string
  needsRefresh: boolean
  responseReadId?: number
  progress?: MessageSendStatus
  repeatGroup?: RepeatSendGroup
}

interface UndoableSend {
  attackResultId: string
  conversationId: string
  historySignature: string
  cutoffIndex: number
  originalValue: string
  textMetadata?: Record<string, unknown>
  attachments: MessageAttachment[]
  converters: ChatConverterSnapshot
  history: Array<Pick<BackendMessage, 'role'>>
}

interface RepeatSendGroup {
  source: PendingSend
  progress: MessageSendStatus
  conversations: Map<string, PendingSend>
  read?: Promise<MessageSendStatus>
}

interface RepeatSendView {
  progress: MessageSendStatus
  needsRefresh: boolean
}

interface SendIssue {
  description: string
  blocking: boolean
  draft?: PendingSend
}

function isSendFinished(progress: MessageSendConversation): boolean {
  return ['completed', 'failed', 'interrupted'].includes(progress.state)
}

function userPieceIds(response: ConversationMessagesResponse): Set<string> {
  return new Set(response.messages.filter((message) => message.role === 'user')
    .flatMap((message) => message.message_pieces.map((piece) => piece.id)))
}

function conversationPieceSignature(response: ConversationMessagesResponse): string {
  return JSON.stringify(response.messages.map((message: BackendMessage) => message.message_pieces.map((piece) => piece.id)))
}

function getRecoveryHistoryCutoff(messages: BackendMessage[], failedRequestTurnNumber: number): number {
  let precedingUserTurnNumber: number | undefined
  for (const message of messages) {
    if (message.turn_number >= failedRequestTurnNumber) {
      break
    }
    if (message.role === 'user') {
      precedingUserTurnNumber = message.turn_number
    }
    for (const piece of message.message_pieces) {
      if (piece.response_error === RETRYABLE_TARGET_RESPONSE_ERROR) {
        // Later replies can depend on the failed turn, so retain only its preceding history.
        return (precedingUserTurnNumber ?? message.turn_number) - 1
      }
    }
  }
  return failedRequestTurnNumber - 1
}

function getPersistedProcessingRecovery(
  conversationId: string,
  response: ConversationMessagesResponse,
): RecoverableSendDraft | undefined {
  const responseStatus = response.target_response_status
  if (responseStatus?.response_error !== RETRYABLE_TARGET_RESPONSE_ERROR) {
    return undefined
  }

  const failedRequest = response.messages.find(
    (message) => (
      message.role === 'user'
      && message.turn_number === responseStatus.request_turn_number
    ),
  )
  const errorMessageIndex = response.messages.findIndex(
    (message) => (
      message.role === 'assistant'
      && message.turn_number === responseStatus.response_turn_number
    ),
  )
  if (!failedRequest || errorMessageIndex < 0) {
    return undefined
  }

  const originalDraft = backendMessageToOriginalDraft(failedRequest)
  const textMetadata = failedRequest.message_pieces.filter((piece) => piece.original_value_data_type === 'text')
    .reduce<Record<string, unknown>>((metadata: Record<string, unknown>, piece) => ({ ...metadata, ...piece.prompt_metadata }), {})
  const cutoffIndex = getRecoveryHistoryCutoff(response.messages, responseStatus.request_turn_number)
  return {
    conversationId,
    failedRequestTurnNumber: responseStatus.request_turn_number,
    failedResponseTurnNumber: responseStatus.response_turn_number,
    historyCutoffIndex: cutoffIndex,
    history: response.messages.filter((message: BackendMessage) => message.turn_number <= cutoffIndex)
      .map((message: BackendMessage) => ({ role: message.role })),
    failedRequestMessageIndex: response.messages.indexOf(failedRequest),
    originalValue: originalDraft.content,
    textMetadata: Object.keys(textMetadata).length ? textMetadata : undefined,
    attachments: (originalDraft.attachments ?? []).map((attachment) => ({ ...attachment })),
    conversions: {},
    source: 'persisted',
    missingConverterSelections: failedRequest.message_pieces.some((piece) => Boolean(piece.converter_identifiers?.length)),
  }
}

function matchesNarrowScreen(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia(NARROW_SCREEN_QUERY).matches
}

interface ChatWindowProps {
  /** Shared layout slot; standalone chat renders its toolbar inline. */
  toolbarContainer?: HTMLElement | null
  onNewAttack: () => void
  activeTarget: TargetInstance | null
  availableTargets: TargetInstance[]
  targetsLoading: boolean
  targetsError: string | null
  onRefreshTargets: () => void
  onSelectTarget: (target: TargetInstance | null) => void
  defaultBranchTarget: TargetInstance | null
  attackResultId: string | null
  conversationId: string | null
  activeConversationId: string | null
  onConversationCreated: (
    attackResultId: string,
    conversationId: string,
    objective?: string,
    target?: TargetInstance | null,
  ) => void
  onSelectConversation: (conversationId: string) => void
  onObjectiveChange?: (objective: string) => void
  onHumanScoreChange?: (score: BackendScore | null, outcome: AttackOutcome) => void
  onAttackChange?: (attack: AttackSummary) => void
  labels?: Record<string, string>
  /** The saved Operation that new attacks join, or null for none. */
  operationId?: string | null
  /** False while the current generation's server defaults or Operation selection are not ready; launching is gated. */
  defaultsReady?: boolean
  /** Why new attacks are unavailable when the defaults have loaded but the Operation selection is not ready. */
  newActivityBlockedReason?: string
  onNavigate?: (view: ViewName) => void
  /** Operator from the loaded attack (for operator locking). Null for new attacks. */
  attackOperator?: string | null
  /** Target info that the current attack was started with (for cross-target guard). */
  attackTarget?: TargetInfo | null
  /** Result of resolving the persisted attack target against the current registry. */
  targetResolutionStatus?: AttackTargetResolutionStatus
  /** Re-run target registry resolution after a transient or unavailable result. */
  onRetryTargetResolution?: () => void
  /** True while a historical attack is being loaded from the history view. */
  isLoadingAttack?: boolean
  /** Number of related (non-main) conversations in the loaded attack. */
  relatedConversationCount?: number
  /** The loaded attack's objective (empty for new/manual attacks). */
  objective?: string
  /** The loaded attack's current outcome. */
  outcome?: AttackOutcome
  automatedScore?: BackendScore | null
  humanScore?: BackendScore | null
  lastResponseMessagePieceId?: string | null
  /** Validated scenario-run provenance for attacks opened from a run dashboard. */
  scenarioResultId?: string | null
  findingEvidenceId?: string | null
}

export default function ChatWindow({
  toolbarContainer,
  onNewAttack,
  activeTarget,
  availableTargets,
  targetsLoading,
  targetsError,
  onRefreshTargets,
  onSelectTarget,
  defaultBranchTarget,
  attackResultId,
  conversationId,
  activeConversationId,
  onConversationCreated,
  onSelectConversation,
  onObjectiveChange,
  onHumanScoreChange,
  onAttackChange,
  labels,
  operationId = null,
  defaultsReady = false,
  newActivityBlockedReason,
  onNavigate,
  attackOperator,
  attackTarget,
  targetResolutionStatus = 'idle',
  onRetryTargetResolution,
  isLoadingAttack,
  relatedConversationCount,
  objective = '',
  outcome,
  automatedScore,
  humanScore,
  lastResponseMessagePieceId,
  scenarioResultId,
  findingEvidenceId,
}: ChatWindowProps) {
  const styles = useChatWindowStyles()
  const restoreFocusTargetAttributes = useRestoreFocusTarget()
  const restoreFocusSourceAttributes = useRestoreFocusSource()
  const navigate = useNavigate()
  const [isFindingDialogOpen, setIsFindingDialogOpen] = useState(false)
  const outcomeButtonRef = useRef<HTMLButtonElement>(null)
  const [temperature, setTemperature] = useState('')
  const [temperatureExplanationId, setTemperatureExplanationId] = useState<string | null>(null)
  const [temperatureAttackId, setTemperatureAttackId] = useState(attackResultId)
  if (temperatureAttackId !== attackResultId) {
    setTemperatureAttackId(attackResultId)
    setTemperature('')
  }
  const runtime = useRuntime()
  const newAttackContext: NewAttackContext = {
    generation: runtime.generation, ready: runtime.ready && defaultsReady, labels, operationId,
  }
  const editor = useConversationDraft(newAttackContext, { pendingUserMessage: true })
  const { draft: editDraft, discard: discardEditor, changeObjective: setEditorObjective } = editor
  const editorTarget = editDraft?.target ?? null
  const editorObjective = editDraft?.objective ?? ''
  const isSavingEditor = editor.saving
  const editorRef = useRef<ConversationEditorHandle>(null)
  const [editorInitialConverters, setEditorInitialConverters] = useState<ChatConverterSnapshot>()
  const [isLoadingEdit, setIsLoadingEdit] = useState(false)
  const copyingRef = useRef(false)
  const copySave = useConversationSave(newAttackContext)
  const [editorNotice, setEditorNoticeState] = useState<ChatInfoNotice | null>(null)
  const setEditorNotice = useCallback((message: string | null): void => {
    setEditorNoticeState(message === null ? null : { id: generateClientId(), message, fading: false })
  }, [])
  const noticeId = editorNotice?.id
  useEffect(() => {
    if (!noticeId) return
    const fade = window.setTimeout(() => {
      setEditorNoticeState((current: ChatInfoNotice | null) => current?.id === noticeId
        ? { ...current, fading: true } : current)
    }, CHAT_NOTICE_VISIBLE_DURATION_MS)
    const dismiss = window.setTimeout(() => {
      setEditorNoticeState((current: ChatInfoNotice | null) => current?.id === noticeId ? null : current)
    }, CHAT_NOTICE_VISIBLE_DURATION_MS + CHAT_NOTICE_FADE_DURATION_MS)
    return () => { window.clearTimeout(fade); window.clearTimeout(dismiss) }
  }, [noticeId])
  const [editorError, setEditorError] = useState<string | null>(null)
  const exitSaveRef = useRef<Promise<boolean> | null>(null)
  // Track sending state per conversation so parallel conversations can send independently
  const [sendingConversations, setSendingConversations] = useState<Set<string>>(new Set())
  const isSending = activeConversationId ? sendingConversations.has(activeConversationId) : Boolean(sendingConversations.size)
  const [isPanelOpen, setIsPanelOpen] = useState(false)
  const [isNarrowScreen, setIsNarrowScreen] = useState(matchesNarrowScreen)
  const [isConverterPanelOpen, setIsConverterPanelOpen] = useState(false)
  const launchStateRef = useRef({ generation: runtime.generation, ready: runtime.ready, defaultsReady })
  useLayoutEffect(() => {
    launchStateRef.current = { generation: runtime.generation, ready: runtime.ready, defaultsReady }
  }, [runtime.generation, runtime.ready, defaultsReady])
  // Conversation-wide preference for rendering message text as Markdown.
  const { preferences, updatePreferences } = useUserPreferences()
  const globalMarkdown = preferences.chatMarkdown
  const [chatInputText, setChatInputText] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [draftAttachments, setDraftAttachments] = useState<MessageAttachment[]>([])
  const converters = useChatConverters(chatInputText, draftAttachments)
  const { applied: activePieceConversions, restore: restoreConversions } = converters
  const [undoableSend, setUndoableSend] = useState<UndoableSend | null>(null)
  const [isUndoingSend, setIsUndoingSend] = useState(false)
  const undoInFlight = useRef(false)
  const [recoverableSends, setRecoverableSends] = useState<Record<string, RecoverableSendDraft>>({})
  const [isRecoveringProcessingError, setIsRecoveringProcessingError] = useState(false)
  const [recoveredDraftWarningConversationId, setRecoveredDraftWarningConversationId] = useState<string | null>(null)
  const [panelRefreshKey, setPanelRefreshKey] = useState(0)
  const inputBoxRef = useRef<ChatInputAreaHandle>(null)
  const recoveryInFlightRef = useRef(false)
  const viewedConversationId = activeConversationId ?? conversationId
  useEffect(() => {
    if (editDraft && (editDraft.sourceConversationId !== viewedConversationId || editDraft.sourceAttackId !== attackResultId)) {
      discardEditor()
    }
  }, [editDraft, viewedConversationId, attackResultId, discardEditor])
  // Synchronous ref tracking which conversations have an in-flight send.
  const sendingConvIdsRef = useRef<Set<string>>(new Set())
  // Pending user messages per conversation that may not be stored server-side yet.
  // Used to restore the user's input when switching back to an in-flight conversation.
  const pendingUserMessagesRef = useRef<Map<string, Message[]>>(new Map())
  const pendingSendsRef = useRef<Map<string, PendingSend>>(new Map())
  const loadedUserPieceIdsRef = useRef<Map<string, Set<string>>>(new Map())
  const isSendInFlight = useCallback(
    (convId: string): boolean => sendingConvIdsRef.current.has(convId),
    [],
  )
  const handleConversationLoaded = useCallback((
    convId: string, response: ConversationMessagesResponse, frontendMessages: Message[],
  ): Message[] => {
    const savedUserIds = userPieceIds(response)
    loadedUserPieceIdsRef.current.set(convId, savedUserIds)
    const persistedRecovery = getPersistedProcessingRecovery(convId, response)
    setRecoverableSends((currentRecoveries) => {
      const currentRecovery = currentRecoveries[convId]
      if (persistedRecovery) {
        if (
          currentRecovery?.source === 'live'
          && currentRecovery.failedRequestTurnNumber === persistedRecovery.failedRequestTurnNumber
          && currentRecovery.failedResponseTurnNumber === persistedRecovery.failedResponseTurnNumber
        ) {
          return {
            ...currentRecoveries,
            [convId]: {
              ...currentRecovery,
              failedRequestMessageIndex: persistedRecovery.failedRequestMessageIndex,
              historyCutoffIndex: persistedRecovery.historyCutoffIndex,
            },
          }
        }
        return { ...currentRecoveries, [convId]: persistedRecovery }
      }
      if (!currentRecovery) {
        return currentRecoveries
      }
      const nextRecoveries = { ...currentRecoveries }
      delete nextRecoveries[convId]
      return nextRecoveries
    })
    // If this conversation has an in-flight send, append any pending user
    // messages (that the server may not have stored yet) and a loading indicator.
    if (sendingConvIdsRef.current.has(convId)) {
      const operation = pendingSendsRef.current.get(convId)
      const pending = pendingUserMessagesRef.current.get(convId) ?? []
      const requestStored = operation?.progress?.request_turn_number != null
        ? response.messages.some((message: BackendMessage) => (
          message.role === 'user' && message.turn_number === operation.progress?.request_turn_number
        ))
        : operation && [...savedUserIds].some((id) => !operation.priorUserPieceIds.has(id))
      if (!operation?.progress || !isSendFinished(operation.progress)) {
        if (!requestStored) { frontendMessages.push(...pending) }
        frontendMessages.push({
          role: 'assistant',
          content: '...',
          timestamp: new Date().toISOString(),
          isLoading: true,
        })
      }
    }
    return frontendMessages
  }, [])
  const {
    messages,
    setMessages,
    loadedConversationId,
    isLoadingMessages,
    awaitingConversationLoad,
    conversationLoadIssue,
    isEvidenceUnavailable,
    loadConversation,
    requestForcedLoad,
    markConversationLoaded,
    resetConversationMessages,
    invalidateConversationLoads,
    claimConversationRead,
    isLatestConversationRead,
    getViewedConversationId,
    setViewedConversationId,
    getLoadedConversationId,
  } = useConversationMessages({
    attackResultId,
    conversationId,
    activeConversationId,
    findingEvidenceId,
    sendingConversations,
    isSendInFlight,
    onConversationLoaded: handleConversationLoaded,
  })
  const savedRecovery = viewedConversationId
    ? recoverableSends[viewedConversationId]
    : undefined
  const recoverableSend = useMemo<RecoverableSendDraft | undefined>(() => (
    savedRecovery?.converterGeneration !== undefined && savedRecovery.converterGeneration !== runtime.generation
      ? {
          ...savedRecovery, conversions: {}, pipelines: undefined, source: 'persisted',
          missingConverterSelections: savedRecovery.missingConverterSelections
            || Object.keys(savedRecovery.conversions).length > 0 || Boolean(Object.keys(savedRecovery.pipelines ?? {}).length),
        }
      : savedRecovery
  ), [savedRecovery, runtime.generation])
  const [sendIssues, setSendIssues] = useState<Record<string, SendIssue>>({})
  const sendIssueConversationId = attackResultId ? viewedConversationId : loadedConversationId ?? viewedConversationId
  const sendIssue = sendIssues[sendIssueConversationId ?? '__pending__']
  const [repeatSends, setRepeatSends] = useState<RepeatSendView[]>([])
  const latestSendRef = useRef<string | null>(null)
  const viewedAttackRef = useRef(attackResultId)
  const navigationRevisionRef = useRef(0)

  useLayoutEffect(() => {
    viewedAttackRef.current = attackResultId
    navigationRevisionRef.current += 1
  }, [attackResultId, activeConversationId, conversationId])

  useEffect(() => {
    const pendingSends = pendingSendsRef.current
    return () => {
      for (const operation of pendingSends.values()) {
        operation.controller.abort()
      }
      pendingSends.clear()
    }
  }, [])

  const handleMarkdownChange = useCallback((
    _event: ChangeEvent<HTMLInputElement>,
    data: SwitchOnChangeData,
  ): void => {
    updatePreferences((current) => ({ ...current, chatMarkdown: data.checked }))
  }, [updatePreferences])

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return
    }

    const mediaQuery = window.matchMedia(NARROW_SCREEN_QUERY)
    const handleChange = (event: MediaQueryListEvent) => {
      setIsNarrowScreen(event.matches)
    }
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [])

  const conversionRevisionKey = useMemo(
    () => JSON.stringify({
      applied: activePieceConversions, pipelines: converters.pipelines, editRevision: converters.editRevision,
    }),
    [activePieceConversions, converters.pipelines, converters.editRevision],
  )

  // Apply the historical attack's drawer default once. Late send refreshes
  // must not override the operator's open/closed choice.
  const [panelInitializedForAttack, setPanelInitializedForAttack] = useState<string | null>(null)
  if (attackResultId && attackResultId !== panelInitializedForAttack) {
    setPanelInitializedForAttack(attackResultId)
    if (!isNarrowScreen && relatedConversationCount && relatedConversationCount > 0) {
      setIsPanelOpen(true)
    }
  }

  const supportsSystemPrompt = activeTarget?.capabilities?.supports_system_prompt === true
  const isTargetResolutionLocked = Boolean(
    attackResultId
    && isTargetResolutionBlocking(targetResolutionStatus),
  )
  const currentOperator = labels?.operator
  // Existing attacks are operator-locked when their operator differs from the current one.
  const isOperatorLocked = Boolean(
    attackResultId && attackOperator && attackOperator !== currentOperator,
  )
  // They are cross-target locked when the selected target's canonical hash differs from the persisted target.
  const isCrossTargetLocked = Boolean(
    attackResultId
    && attackTarget
    && activeTarget
    && !targetInfoMatchesTarget(attackTarget, activeTarget),
  )
  // Any failed invariant keeps all mutation controls and handlers read-only.
  const isMutationLocked = isOperatorLocked || isCrossTargetLocked || isTargetResolutionLocked || isEvidenceUnavailable
  const isScoreLocked = isOperatorLocked || Boolean(isLoadingAttack) || isLoadingMessages
    || awaitingConversationLoad || isEvidenceUnavailable
  const {
    pendingObjective,
    currentObjective,
    resetPendingObjective,
    updateHumanScore,
    removeHumanScore,
    changeObjective,
  } = useAttackMutations({
    attackResultId,
    activeConversationId,
    objective,
    lastResponseMessagePieceId,
    humanScore,
    isScoreLocked,
    loadConversation,
    getViewedConversationId,
    onHumanScoreChange,
    onAttackChange,
    onObjectiveChange,
  })

  // Clear internal messages when attack state is reset (e.g. New Attack).
  // Uses the "adjust state during render" pattern (see React docs:
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders)
  // instead of a useEffect so we don't trigger react-hooks/set-state-in-effect.
  const [prevAttackResultId, setPrevAttackResultId] = useState<string | null>(attackResultId)
  if (attackResultId !== prevAttackResultId) {
    setPrevAttackResultId(attackResultId)
    if (!attackResultId) {
      setRecoverableSends({})
      resetConversationMessages()
      setSystemPrompt('')
      resetPendingObjective()
    }
  }

  // Clear a retained system prompt when switching to a target that can't use it,
  // so it isn't silently dropped on send. Preserved across supporting targets to
  // keep the A/B-testing workflow intact.
  if (activeTarget && !supportsSystemPrompt && systemPrompt) {
    setSystemPrompt('')
  }

  const isFindingActionDisabled = Boolean(editDraft !== null || !attackResultId || !activeConversationId
    || isEvidenceUnavailable || isLoadingAttack || isLoadingMessages || awaitingConversationLoad)

  // Handle conversation selection from the panel
  // For a different ID the useEffect handles loading; for same ID force a refresh
  const handlePanelSelectConversation = useCallback(async (convId: string): Promise<void> => {
    const editing = editorRef.current
    const previousId = getViewedConversationId()
    if (editing && !await editing.finishEditing()) return
    if (editing && convId === previousId) return
    requestForcedLoad()
    onSelectConversation(convId)
    if (isNarrowScreen) {
      setIsPanelOpen(false)
    }
    if (convId === activeConversationId && attackResultId) {
      loadConversation(attackResultId, convId)
    }
  }, [attackResultId, activeConversationId, getViewedConversationId, isNarrowScreen, onSelectConversation, loadConversation, requestForcedLoad])

  const isCurrentSend = (operation: PendingSend): boolean => (
    !operation.controller.signal.aborted
    && pendingSendsRef.current.get(operation.conversationId) === operation
  )
  const checkSendPreparation = (operation: PendingSend, requireDefaults: boolean): void => {
    const current = launchStateRef.current
    if (current.generation !== operation.converterGeneration || !current.ready
      || (requireDefaults && !current.defaultsReady)) {
      throw new Error('Runtime or default labels changed while preparing this message. Your draft is preserved. Retry after default labels finish loading.')
    }
  }
  const isViewingSend = (operation: PendingSend): boolean => (
    viewedAttackRef.current === operation.attackResultId
    && (
      getViewedConversationId() === operation.conversationId
      || (getViewedConversationId() === null && operation.conversationId === '__pending__'
        && navigationRevisionRef.current === operation.navigationRevision)
    )
  )
  const setSendIssue = (conversation: string, issue?: SendIssue): void => {
    setSendIssues((previous) => {
      const next = { ...previous }
      if (issue) { next[conversation] = issue } else { delete next[conversation] }
      return next
    })
  }
  const finishTracking = (operation: PendingSend): void => {
    if (!isCurrentSend(operation) || !sendingConvIdsRef.current.has(operation.conversationId)) { return }
    if (operation.responseReadId === undefined
      || isLatestConversationRead(operation.conversationId, operation.responseReadId)) {
      invalidateConversationLoads(operation.conversationId)
    }
    if (isViewingSend(operation)) {
      setMessages((previous) => previous.filter((message) => !message.isLoading))
    }
    sendingConvIdsRef.current.delete(operation.conversationId)
    pendingUserMessagesRef.current.delete(operation.conversationId)
    setSendingConversations((previous) => {
      const next = new Set(previous)
      next.delete(operation.conversationId)
      return next
    })
    setPanelRefreshKey((key) => key + 1)
  }
  const retireSend = (operation: PendingSend): void => {
    if (isCurrentSend(operation) && !operation.needsRefresh
      && operation.progress?.failure_stage !== 'preparation') {
      pendingSendsRef.current.delete(operation.conversationId)
    }
  }
  const stopSendSpinner = (operation: PendingSend): void => {
    if (!isViewingSend(operation)) { return }
    const hasMatchingTranscript = getLoadedConversationId() === operation.conversationId
    const pending = pendingUserMessagesRef.current.get(operation.conversationId) ?? []
    setMessages((previous) => (hasMatchingTranscript
      ? previous
      : [...operation.initialMessages, ...pending]
    ).filter((message) => !message.isLoading))
    markConversationLoaded(operation.conversationId)
  }

  const applySendResponse = (
    operation: PendingSend, response: AddMessageResponse, isLatestRead: boolean,
  ): ChatSendOutcome => {
    const effectiveConvId = operation.conversationId
    const targetResponseStatus = response.messages.target_response_status
    const recovery = operation.progress
      && !['interrupted', 'finalization'].includes(operation.progress.failure_stage ?? '')
      ? getPersistedProcessingRecovery(effectiveConvId, response.messages)
      : undefined
    const processingFailure = recovery?.failedRequestTurnNumber === operation.progress?.request_turn_number
      && recovery !== undefined
    const status: ChatSendOutcome['status'] = operation.progress?.failure_stage === 'preparation'
      || processingFailure
      ? 'retryable_failure'
      : operation.progress?.failure_stage
        || (targetResponseStatus?.response_error && targetResponseStatus.response_error !== 'none')
        ? 'non_retryable_failure'
        : 'sent'
    const backendMessages = backendMessagesToFrontend(response.messages.messages)
    const recoveredDraft: RecoverableSendDraft | undefined = processingFailure && recovery ? {
      ...recovery,
      originalValue: operation.originalValue,
      textMetadata: operation.textMetadata,
      attachments: operation.attachments,
      conversions: operation.conversions,
      converterGeneration: operation.converterGeneration,
      pipelines: operation.pipelines,
      source: 'live',
      missingConverterSelections: false,
    } : recovery
    if (isLatestRead) {
      loadedUserPieceIdsRef.current.set(effectiveConvId, userPieceIds(response.messages))
      setRecoverableSends((currentRecoveries) => {
        const next = { ...currentRecoveries }
        if (recoveredDraft) { next[effectiveConvId] = recoveredDraft } else { delete next[effectiveConvId] }
        return next
      })
    }
    if (isLatestRead && isViewingSend(operation)) {
      invalidateConversationLoads(effectiveConvId)
      setMessages(backendMessages)
      markConversationLoaded(effectiveConvId)
      onAttackChange?.(response.attack)
      const lastMessage = response.messages.messages[response.messages.messages.length - 1]
      if (
        status === 'sent' && operation.progress?.state === 'completed'
        && latestSendRef.current === operation.submissionId
        && !operation.repeatGroup && operation.converterSnapshot && operation.attackResultId
        && typeof targetResponseStatus?.request_turn_number === 'number'
        && targetResponseStatus.request_turn_number === operation.progress.request_turn_number
        && lastMessage?.role === 'assistant' && lastMessage.message_pieces.length > 0
        && lastMessage.message_pieces.every((piece) => piece.converted_value_data_type === 'text')
      ) {
        setUndoableSend({
          attackResultId: operation.attackResultId,
          conversationId: effectiveConvId,
          historySignature: conversationPieceSignature(response.messages),
          cutoffIndex: targetResponseStatus.request_turn_number - 1,
          originalValue: operation.originalValue,
          textMetadata: operation.textMetadata,
          attachments: operation.attachments,
          converters: operation.converterSnapshot,
          history: response.messages.messages.filter(
            (message: BackendMessage) => message.turn_number < targetResponseStatus.request_turn_number,
          ).map((message: BackendMessage) => ({ role: message.role })),
        })
      }
    }
    return {
      status,
      clearDraft: status !== 'retryable_failure' && latestSendRef.current === operation.submissionId
        && (!operation.repeatGroup || operation.repeatGroup.source === operation),
    }
  }

  const updateRepeatView = (group: RepeatSendGroup): void => {
    if (group.source.controller.signal.aborted) return
    const needsRefresh = [...group.conversations.values()].some(
      (operation: PendingSend) => isCurrentSend(operation) && operation.needsRefresh,
    )
    setRepeatSends((previous: RepeatSendView[]) => previous.map((view: RepeatSendView) => (
      view.progress.send_id === group.progress.send_id ? { progress: group.progress, needsRefresh } : view
    )))
  }

  const receiveRepeatProgress = (group: RepeatSendGroup, progress: MessageSendStatus): void => {
    if (group.source.controller.signal.aborted) return
    group.progress = progress
    let addedConversations = false
    for (const conversation of progress.conversations ?? []) {
      if (group.conversations.has(conversation.conversation_id)) continue
      addedConversations = true
      const operation: PendingSend = {
        ...group.source,
        conversationId: conversation.conversation_id,
        draftRevision: undefined,
        responseReadId: undefined,
        needsRefresh: false,
        progress: { ...progress, ...conversation },
      }
      group.conversations.set(operation.conversationId, operation)
      // A finished sibling may already have a newer send by the time this snapshot arrives.
      if (pendingSendsRef.current.has(operation.conversationId)) continue
      pendingSendsRef.current.set(operation.conversationId, operation)
      sendingConvIdsRef.current.add(operation.conversationId)
      const pending = pendingUserMessagesRef.current.get(group.source.conversationId)
      if (pending) pendingUserMessagesRef.current.set(operation.conversationId, [...pending])
      setSendingConversations((previous: Set<string>) => new Set(previous).add(operation.conversationId))
      void trackSend(operation).then(() => { retireSend(operation) })
    }
    if (addedConversations) setPanelRefreshKey((key: number) => key + 1)
    updateRepeatView(group)
  }

  const readSendProgress = async (operation: PendingSend): Promise<MessageSendStatus> => {
    const { attackResultId: sendAttackId, progress: previousProgress } = operation
    if (!sendAttackId || !previousProgress) throw new Error('The send has no progress handle.')
    const group = operation.repeatGroup
    const read = (): Promise<MessageSendStatus> => attacksApi.getMessageSend(
      sendAttackId, previousProgress.send_id, operation.controller.signal,
    )
    if (!group) return read()
    // Every conversation uses the same in-flight GET, but settles its own evidence reads and ownership.
    group.read ??= read().then((progress: MessageSendStatus) => {
      receiveRepeatProgress(group, progress)
      return progress
    }).finally(() => { group.read = undefined })
    const progress = await group.read
    const conversation = progress.conversations?.find(
      (candidate: MessageSendConversation) => candidate.conversation_id === operation.conversationId,
    )
    return conversation ? { ...progress, ...conversation } : progress
  }

  const trackSend = async (operation: PendingSend): Promise<ChatSendOutcome> => {
    try {
      if (!operation.attackResultId) { throw new Error('The send has no attack ID.') }
      while (operation.progress) {
        const repeatProgress = operation.repeatGroup?.progress
        // The last conversation can release ownership before the operation publishes its final summary.
        const awaitingSummary = repeatProgress && !isSendFinished(repeatProgress)
          && repeatProgress.conversations?.length && repeatProgress.conversations.every(isSendFinished)
        if (isSendFinished(operation.progress) && !awaitingSummary) break
        let progress: MessageSendStatus | undefined
        try {
          progress = await readSendProgress(operation)
        } catch (err) {
          if (toApiError(err).status !== 404) { throw err }
          // Lost handles permit evidence reads, never a new submission or a claim of delivery.
        }
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        operation.progress = progress
      }
      operation.responseReadId = claimConversationRead(operation.conversationId)
      const [attack, conversation] = await Promise.all([
        attacksApi.getAttack(operation.attackResultId),
        attacksApi.getMessages(operation.attackResultId, operation.conversationId),
      ])
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
      const isLatestRead = isLatestConversationRead(operation.conversationId, operation.responseReadId)
      const outcome = applySendResponse(operation, { attack, messages: conversation }, isLatestRead)
      const progress = operation.progress
      const hasProcessingRecovery = conversation.target_response_status?.response_error === 'processing'
        && conversation.target_response_status.request_turn_number === progress?.request_turn_number
      if (!progress || progress.failure_stage === 'interrupted' || progress.failure_stage === 'finalization'
        || (progress.failure_stage === 'sending' && !hasProcessingRecovery)) {
        operation.needsRefresh = true
        setSendIssue(operation.conversationId, {
          description: `${progress?.error ?? 'Send acceptance is unknown.'} Refresh saved messages only; do not resend.`,
          blocking: true,
        })
        return { status: 'non_retryable_failure', clearDraft: false }
      }
      setSendIssue(operation.conversationId, progress.failure_stage === 'preparation'
        ? {
          description: progress.error ?? 'Message preparation failed before target dispatch.',
          blocking: false,
          draft: operation,
        }
        : undefined)
      operation.needsRefresh = false
      return outcome
    } catch (err) {
      if (isCurrentSend(operation)) {
        operation.needsRefresh = true
        const error = toApiError(err)
        const detail = error.isTimeout ? 'The read timed out.'
          : error.isNetworkError ? 'The backend could not be reached.' : error.detail
        const phase = operation.progress && isSendFinished(operation.progress)
          ? `${operation.progress.error ?? 'Sending finished.'} Saved messages or attack details could not be loaded.`
          : 'Send status is unavailable. Delivery may be unknown.'
        setSendIssue(operation.conversationId, {
          description: `${phase} ${detail} Refresh only; do not resend.`,
          blocking: true,
        })
        stopSendSpinner(operation)
      }
      return { status: 'non_retryable_failure', clearDraft: false }
    } finally {
      finishTracking(operation)
      if (operation.repeatGroup) updateRepeatView(operation.repeatGroup)
    }
  }

  const refreshSend = async (conversationIdToRefresh = sendIssueConversationId ?? '__pending__'): Promise<void> => {
    const operation = pendingSendsRef.current.get(conversationIdToRefresh)
    if (!operation || sendingConvIdsRef.current.has(operation.conversationId)) { return }
    sendingConvIdsRef.current.add(operation.conversationId)
    setSendingConversations((previous) => new Set(previous).add(operation.conversationId))
    const outcome = await trackSend(operation)
    if (isCurrentSend(operation) && isViewingSend(operation) && outcome.clearDraft
      && inputBoxRef.current
      && inputBoxRef.current?.getDraftRevision() === operation.draftRevision) {
      inputBoxRef.current.restoreDraft('', [])
      setChatInputText('')
      setDraftAttachments([])
      converters.clearAll()
    }
    retireSend(operation)
  }

  const restorePreparationDraft = (): void => {
    const operation = sendIssue?.draft
    if (!operation || !isCurrentSend(operation) || !isViewingSend(operation)) return
    const sameGeneration = operation.converterGeneration === runtime.generation
    const attachments = operation.attachments.map(withDraftIdentity)
    setChatInputText(operation.originalValue)
    setDraftAttachments(attachments)
    inputBoxRef.current?.restoreDraft(operation.originalValue, attachments, operation.textMetadata)
    restoreConversions(
      operation.originalValue, attachments, sameGeneration ? operation.conversions : {},
      sameGeneration ? operation.pipelines : undefined,
    )
    if (!sameGeneration) setSendIssue(operation.conversationId, {
      ...sendIssue,
      description: 'Prompt restored. Converter choices could not be restored after the runtime changed; review them before sending.',
    })
  }

  const handleSend = async (
    originalValue: string,
    convertedValue: string | undefined,
    attachments: MessageAttachment[],
    options?: MultiSendOptions,
  ): Promise<ChatSendOutcome> => {
    if (
      !runtime.ready
      || (!attackResultId && !defaultsReady)
      || !activeTarget
      || editDraft !== null
      || isLoadingAttack
      || isLoadingMessages
      || awaitingConversationLoad
      || isMutationLocked
      || sendIssue?.blocking
      || isUndoingSend
      || unboundHistoryError
    ) {
      return { status: 'retryable_failure', clearDraft: false }
    }

    const initialSendConvId = activeConversationId ?? conversationId ?? '__pending__'
    if (sendingConvIdsRef.current.has(initialSendConvId)) {
      return { status: 'retryable_failure', clearDraft: false }
    }

    setEditorNotice(null)
    setUndoableSend(null)
    invalidateConversationLoads(initialSendConvId)
    setRecoverableSends((currentRecoveries) => {
      if (!currentRecoveries[initialSendConvId]) {
        return currentRecoveries
      }
      const nextRecoveries = { ...currentRecoveries }
      delete nextRecoveries[initialSendConvId]
      return nextRecoveries
    })

    // Capture all piece conversions upfront before any async work or state clears
    const conversions = { ...activePieceConversions }
    const count = options?.count ?? 1
    const converterMode = options?.requestConverterMode ?? 'shared'
    const pipelines = count > 1 ? { ...converters.pipelines } : undefined
    const operation: PendingSend = {
      submissionId: generateClientId(),
      controller: new AbortController(),
      draftRevision: inputBoxRef.current?.getDraftRevision(),
      originalValue,
      textMetadata: inputBoxRef.current?.getTextMetadata(),
      attachments: attachments.map((attachment) => ({ ...attachment })),
      conversions,
      priorUserPieceIds: new Set(loadedUserPieceIdsRef.current.get(initialSendConvId)),
      initialMessages: [...messages],
      navigationRevision: navigationRevisionRef.current,
      converterGeneration: runtime.generation,
      converterSnapshot: count === 1 ? converters.snapshot() : undefined,
      pipelines,
      attackResultId,
      conversationId: initialSendConvId,
      needsRefresh: false,
    }
    pendingSendsRef.current.set(initialSendConvId, operation)
    latestSendRef.current = operation.submissionId
    setSendIssue(initialSendConvId)
    const submittedNavigationRevision = navigationRevisionRef.current
    let submissionAttempted = false
    const textConversion = conversions['text']
    const isTextTextConversion = textConversion?.convertedDataType === 'text'
    const isTextFileConversion = Boolean(textConversion) && !isTextTextConversion

    // Mark synchronously so the useEffect guard sees it immediately
    sendingConvIdsRef.current.add(initialSendConvId)

    // When a text→text converter is active, show the converted text as the bubble's
    // primary content. When a text→file converter is active, keep the typed text
    // as content and synthesize a file attachment so the bubble shows both.
    const displayContent = isTextTextConversion && convertedValue != null ? convertedValue : originalValue
    const optimisticAttachments: MessageAttachment[] = [...attachments]
    if (isTextFileConversion && textConversion) {
      const url = buildMediaUrl(textConversion.convertedValue)
      const kind = dataTypeToAttachmentKind(textConversion.convertedDataType)
      optimisticAttachments.push({
        type: kind,
        name: basenameFromValue(textConversion.convertedValue, `output.${kind}`),
        url,
        mimeType: 'application/octet-stream',
      })
    }

    // Add user message with attachments for display
    const userMessage: Message = {
      role: 'user',
      content: displayContent,
      timestamp: new Date().toISOString(),
      attachments: optimisticAttachments.length > 0 ? optimisticAttachments : undefined,
      originalContent: isTextTextConversion ? originalValue : undefined,
    }
    setMessages(prev => [...prev, userMessage])

    // Track as pending so switching back before the server stores it still shows it
    const pending = pendingUserMessagesRef.current.get(initialSendConvId) ?? []
    pending.push(userMessage)
    pendingUserMessagesRef.current.set(initialSendConvId, pending)

    // Show loading indicator
    setSendingConversations(prev => new Set(prev).add(initialSendConvId))
    const loadingMessage: Message = {
      role: 'assistant',
      content: '...',
      timestamp: new Date().toISOString(),
      isLoading: true,
    }
    setMessages(prev => [...prev, loadingMessage])

    try {
      // Build message pieces from text + attachments — always use original text
      const pieceIds = buildDraftPieceIds(originalValue, attachments, conversions)
      const originalPieces = await buildMessagePieces(originalValue, attachments, operation.textMetadata)
      if (textConversion && !originalValue.trim()) {
        originalPieces.unshift({ data_type: 'text', original_value: originalValue })
      }
      const pieces = applyConvertedValues(
        originalPieces,
        pieceIds,
        conversions,
      )
      const requestConfigurations = count > 1 && converterMode === 'per_branch' ? buildRequestConverterConfigurations(
        buildConverterInputs(originalValue, attachments), pieceIds, pipelines ?? {}, conversions,
      ) : []
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }

      // Create attack lazily on first message
      let currentAttackResultId = attackResultId
      let currentConversationId = conversationId
      let currentActiveConversationId = activeConversationId
      if (!currentAttackResultId) {
        checkSendPreparation(operation, true)
        let selectedTarget = activeTarget
        if (temperature.trim()) {
          selectedTarget = await buildTemperatureTarget(activeTarget, Number(temperature))
          if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        }
        checkSendPreparation(operation, true)
        const createRequest: CreateAttackRequest = {
          target_registry_name: activeTarget.target_registry_name,
          ...(selectedTarget.binding ? { target_binding: selectedTarget.binding } : {}),
          name: pendingObjective || undefined,
          // TODO(PyRIT 1.4): Pass only dedicated attribution after legacy label aliases are removed.
          // The create-attack API normalizes these aliases through _AttackAttributionInput.
          labels,
          operation_id: operationId ?? undefined,
          system_prompt: supportsSystemPrompt ? systemPrompt.trim() || undefined : undefined,
        }
        const createResponse = await attacksApi.createAttack(createRequest)
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        currentAttackResultId = createResponse.attack_result_id
        currentConversationId = createResponse.conversation_id
        currentActiveConversationId = currentConversationId
        // Mark new ID in synchronous ref *before* triggering the state
        // update that changes activeConversationId (and fires the useEffect)
        sendingConvIdsRef.current.delete('__pending__')
        sendingConvIdsRef.current.add(currentConversationId!)
        // Move pending messages to the real conversation ID
        const pendingMsgs = pendingUserMessagesRef.current.get('__pending__')
        if (pendingMsgs) {
          pendingUserMessagesRef.current.delete('__pending__')
          pendingUserMessagesRef.current.set(currentConversationId!, pendingMsgs)
        }
        pendingSendsRef.current.delete('__pending__')
        operation.attackResultId = currentAttackResultId
        operation.conversationId = currentConversationId
        pendingSendsRef.current.set(currentConversationId, operation)
        if (navigationRevisionRef.current === submittedNavigationRevision) {
          if (selectedTarget.binding) {
            onConversationCreated(currentAttackResultId, currentConversationId, pendingObjective || undefined, selectedTarget)
          } else {
            onConversationCreated(currentAttackResultId, currentConversationId, pendingObjective || undefined)
          }
          viewedAttackRef.current = currentAttackResultId
          setViewedConversationId(currentConversationId)
        }
        // Update sending tracker to use real ID instead of __pending__
        setSendingConversations(prev => {
          const next = new Set(prev)
          next.delete('__pending__')
          next.add(currentConversationId!)
          return next
        })
      }

      // The effective conversation we're sending for
      const effectiveConvId = currentActiveConversationId ?? currentConversationId

      // Send message to target
      if (!currentAttackResultId || !effectiveConvId) {
        throw new Error('Message send is missing an attack or conversation ID.')
      }
      const addMessageRequest: MessageSendRequest = {
        role: 'user',
        pieces,
        send: true,
        target_registry_name: activeTarget.target_registry_name,
        target_conversation_id: effectiveConvId,
        submission_id: operation.submissionId,
        ...(count > 1 ? {
          count,
          request_converter_mode: converterMode,
        } : {}),
        ...(requestConfigurations.length ? { request_converter_configurations: requestConfigurations } : {}),
      }
      if (targetResolutionStatus === 'unbound' && temperature.trim()) {
        const selectedTarget = await buildTemperatureTarget(activeTarget, Number(temperature))
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        addMessageRequest.target_binding = selectedTarget.binding
      }
      checkSendPreparation(operation, false)
      submissionAttempted = true
      operation.progress = await attacksApi.submitMessageSend(currentAttackResultId, addMessageRequest)
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
      if (count > 1) {
        const group: RepeatSendGroup = {
          source: operation,
          progress: operation.progress,
          conversations: new Map([[operation.conversationId, operation]]),
        }
        operation.repeatGroup = group
        setRepeatSends((previous: RepeatSendView[]) => [
          ...previous.filter((view: RepeatSendView) => !isSendFinished(view.progress)),
          { progress: group.progress, needsRefresh: false },
        ])
        receiveRepeatProgress(group, group.progress)
        const sourceProgress = group.progress.conversations?.find(
          (conversation: MessageSendConversation) => conversation.conversation_id === operation.conversationId,
        )
        if (sourceProgress) operation.progress = { ...group.progress, ...sourceProgress }
        if (isViewingSend(operation) && !isNarrowScreen) setIsPanelOpen(true)
        setPanelRefreshKey((key: number) => key + 1)
      }
      return await trackSend(operation)
    } catch (err) {
      if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
      const sendConvId = operation.conversationId
      const apiError = toApiError(err)
      if (submissionAttempted && ![400, 401, 403, 404, 409, 422, 429].includes(apiError.status ?? 0)) {
        operation.needsRefresh = true
        const detail = apiError.isTimeout ? 'Request timed out.'
          : apiError.isNetworkError ? 'Network error. The backend could not be reached.' : apiError.detail
        setSendIssue(sendConvId, {
          description: `${detail} Send acceptance is unknown. Refresh saved messages only; do not resend.`,
          blocking: true,
        })
        stopSendSpinner(operation)
        return { status: 'non_retryable_failure', clearDraft: false }
      }
      const viewedConversationId = getViewedConversationId()
      const isViewingFailedConversation = isViewingSend(operation)

      // Only show error in UI if user is still on this conversation
      if (isViewingFailedConversation) {
        const hasMatchingTranscript = getLoadedConversationId() === sendConvId
        // Mark the viewed conversation as loaded so first-send failures do not
        // get stuck behind the "Loading conversation..." placeholder.
        if (viewedConversationId) {
          markConversationLoaded(viewedConversationId)
        } else if (sendConvId !== '__pending__') {
          markConversationLoaded(sendConvId)
        }

        let description: string
        if (apiError.isNetworkError) {
          description = 'Network error — check that the backend is running and reachable.'
        } else if (apiError.isTimeout) {
          description = 'Request timed out. The server may be busy — please try again.'
        } else {
          description = apiError.detail
        }

        const errorMessage: Message = {
          role: 'assistant',
          content: '',
          timestamp: new Date().toISOString(),
          error: {
            type: apiError.isNetworkError ? 'network' : apiError.isTimeout ? 'timeout' : 'unknown',
            description,
          },
        }
        setMessages(prev => {
          // A pending navigation load may still leave a different conversation in state.
          const failedMessages = hasMatchingTranscript ? prev : [...messages, userMessage]
          if (failedMessages.length > 0 && failedMessages[failedMessages.length - 1].isLoading) {
            return [...failedMessages.slice(0, -1), errorMessage]
          }
          return [...failedMessages, errorMessage]
        })

      }
      return {
        status: 'retryable_failure',
        clearDraft: false,
      }
    } finally {
      finishTracking(operation)
      retireSend(operation)
    }
  }

  const appendConversationCreationError = useCallback((error: unknown): void => {
    const apiError = toApiError(error)
    setMessages((previousMessages) => [
      ...previousMessages,
      {
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        error: {
          type: 'unknown',
          description: `Could not create a new conversation. ${apiError.detail}`,
        },
      },
    ])
  }, [setMessages])

  const createAndSelectConversation = useCallback(async (
    request: CreateConversationRequest,
  ): Promise<boolean> => {
    if (!attackResultId || isMutationLocked) { return false }

    try {
      const response = await attacksApi.createConversation(attackResultId, request)
      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
      return true
    } catch (err) {
      appendConversationCreationError(err)
      return false
    }
  }, [
    appendConversationCreationError,
    attackResultId,
    isNarrowScreen,
    isMutationLocked,
    onSelectConversation,
  ])

  const handleNewConversation = useCallback(
    (): Promise<boolean> => createAndSelectConversation({}),
    [createAndSelectConversation],
  )

  const restoreRecoverableDraft = useCallback((destinationConversationId: string): void => {
    if (!recoverableSend) { return }
    const attachments = recoverableSend.attachments.map(withDraftIdentity)
    setChatInputText(recoverableSend.originalValue)
    setDraftAttachments(attachments)
    restoreConversions(
      recoverableSend.originalValue, attachments, recoverableSend.conversions, recoverableSend.pipelines,
    )
    inputBoxRef.current?.restoreDraft(
      recoverableSend.originalValue,
      attachments,
      recoverableSend.textMetadata,
    )
    inputBoxRef.current?.focus()
    setRecoveredDraftWarningConversationId(recoverableSend.missingConverterSelections ? destinationConversationId : null)
  }, [restoreConversions, recoverableSend])

  const handleRecoverProcessingError = useCallback(async (): Promise<void> => {
    if (
      !attackResultId
      || !recoverableSend
      || isMutationLocked
      || sendIssue?.blocking
      || isSending
      || recoveryInFlightRef.current
    ) {
      return
    }

    const supportsMultiTurn = Boolean(
      activeTarget && activeTarget.capabilities?.supports_multi_turn !== false,
    )
    const cutoffIndex = recoverableSend.historyCutoffIndex
    const replayError = activeTarget
      ? historyReplayDisabledReason(activeTarget, supportsMultiTurn ? recoverableSend.history : []) : undefined
    if (replayError) {
      setEditorError(replayError)
      return
    }
    const recoveryRequest: CreateConversationRequest = supportsMultiTurn && cutoffIndex >= 0
      ? {
          source_conversation_id: recoverableSend.conversationId,
          cutoff_index: cutoffIndex,
        }
      : {}
    const sourceConversationId = recoverableSend.conversationId
    const draftRevision = inputBoxRef.current?.getDraftRevision()

    recoveryInFlightRef.current = true
    setIsRecoveringProcessingError(true)
    try {
      const response = await attacksApi.createConversation(attackResultId, recoveryRequest)
      setPanelRefreshKey((currentKey) => currentKey + 1)

      const isStillViewingSource = getViewedConversationId() === sourceConversationId
      const isDraftUnchanged = inputBoxRef.current?.getDraftRevision() === draftRevision
      if (!isStillViewingSource || !isDraftUnchanged) {
        return
      }

      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
      restoreRecoverableDraft(response.conversation_id)
    } catch (err) {
      if (getViewedConversationId() === sourceConversationId) {
        appendConversationCreationError(err)
      }
    } finally {
      recoveryInFlightRef.current = false
      setIsRecoveringProcessingError(false)
    }
  }, [
    activeTarget,
    appendConversationCreationError,
    attackResultId,
    getViewedConversationId,
    isMutationLocked,
    isSending,
    isNarrowScreen,
    onSelectConversation,
    recoverableSend,
    sendIssue?.blocking,
    restoreRecoverableDraft,
  ])

  const copyMessageToInput = useCallback((message: Message): void => {
    const inputBox = inputBoxRef.current
    if (!inputBox) { return }

    if (message.content) {
      inputBox.setText(message.content)
    }
    for (const attachment of message.attachments ?? []) {
      inputBox.addAttachment(attachment)
    }
  }, [])

  /** 1. Copy the clicked message's content/attachments into the current conversation's input box */
  const handleCopyToInput = useCallback((messageIndex: number) => {
    const msg = messages[messageIndex]
    if (!msg) { return }
    copyMessageToInput(msg)
  }, [copyMessageToInput, messages])

  const handleChangeMainConversation = useCallback(async (convId: string) => {
    if (
      !attackResultId
      || isMutationLocked
    ) {
      return
    }

    try {
      await attacksApi.changeMainConversation(attackResultId, convId)
      setPanelRefreshKey(k => k + 1)
    } catch (err) {
      console.error('Failed to change main conversation:', err)
    }
  }, [
    attackResultId,
    isMutationLocked,
  ])

  const handleAddObjective = useCallback(async (newObjective: string, expectedObjective: string): Promise<void> => {
    if (editDraft !== null) {
      setEditorObjective(newObjective)
      return
    }
    await changeObjective(newObjective, expectedObjective)
  }, [changeObjective, editDraft, setEditorObjective])

  const beginEdit = async (
    target: TargetInstance | null = activeTarget,
  ): Promise<void> => {
    if (editDraft || isSending || isLoadingEdit || isLoadingMessages || isLoadingAttack || awaitingConversationLoad) return
    const sourceId = viewedConversationId
    setIsLoadingEdit(true)
    setEditorError(null)
    setEditorNotice(null)
    try {
      const source = attackResultId && sourceId ? await attacksApi.getMessages(attackResultId, sourceId) : null
      if (sourceId !== getViewedConversationId()) return
      const sourceMessages = source?.messages ?? []
      const draftMessages = toConversationDraft(sourceMessages)
      if (chatInputText || draftAttachments.length || activePieceConversions.text) {
        const pieces: ConversationDraftPiece[] = [
          ...(chatInputText || activePieceConversions.text
            ? [{ ...newDraftPiece(), draftId: 'text', original_value: chatInputText }] : []),
          ...draftAttachments.map(attachmentDraft),
        ].map((piece: ConversationDraftPiece) => {
          const conversion = activePieceConversions[piece.draftId]
          return conversion ? {
            ...piece, converted_value: conversion.convertedValue,
            converted_value_data_type: conversion.convertedDataType,
            applied_converter_ids: conversion.converterInstanceIds,
            applied_converter_provenance: conversion.converterProvenance,
          } : piece
        })
        draftMessages.push({ id: generateClientId(), role: 'user', pieces })
      }
      editor.begin({
        messages: draftMessages, objective: currentObjective,
        initialObjective: currentObjective, sourceConversationId: sourceId,
        sourceAttackId: attackResultId, target, labels,
      })
      setEditorInitialConverters(converters.snapshot())
      setIsConverterPanelOpen(false)
      onRefreshTargets()
    } catch (error) {
      setEditorError(toApiError(error).detail)
    } finally {
      setIsLoadingEdit(false)
    }
  }

  const handleEditorSaved = (response: AddMessageResponse, savedTarget?: TargetInstance | null): void => {
    editor.discard()
    setEditorNotice('Conversation saved.')
    setMessages(backendMessagesToFrontend(response.messages.messages))
    markConversationLoaded(response.messages.conversation_id)
    if (response.attack.attack_result_id === attackResultId) {
      onSelectConversation(response.messages.conversation_id)
    } else {
      onConversationCreated(response.attack.attack_result_id, response.messages.conversation_id, response.attack.objective, savedTarget ?? editorTarget)
    }

    onAttackChange?.(response.attack)
    setPanelRefreshKey((key: number) => key + 1)
  }

  const restoreEditorPrompt = (
    prompt: ReturnType<typeof draftMessageToComposer> | undefined, snapshot: ChatConverterSnapshot,
  ): void => {
    const text = prompt?.text ?? ''
    const attachments = prompt?.attachments.map((attachment: MessageAttachment) => attachment.file
      ? { ...attachment, url: URL.createObjectURL(attachment.file) } : attachment) ?? []
    setChatInputText(text)
    setDraftAttachments(attachments)
    if (prompt && snapshot.generation === launchStateRef.current.generation) {
      converters.restoreSnapshot(prompt.converters, text, attachments)
    } else {
      restoreConversions(text, attachments, {}, snapshot.pipelines)
      if (prompt) setEditorNotice('Prompt restored. The runtime changed; run the converter pipeline again before sending.')
    }
    inputBoxRef.current?.restoreDraft(text, attachments, prompt?.textMetadata)
    inputBoxRef.current?.focus()
    if (prompt?.notice) setEditorNotice(prompt.notice)
  }

  const handleSaveEditor = (
    destination: 'same_attack' | 'new_attack', reuseUnsentConversation = false,
  ): Promise<boolean> => {
    if (!editDraft) return Promise.resolve(false)
    if (destination === 'same_attack' && sameAttackDisabledReason) {
      setEditorError(sameAttackDisabledReason)
      return Promise.resolve(false)
    }
    setEditorError(null)
    const lastMessage = editDraft.messages[editDraft.messages.length - 1]
    const snapshot = editorRef.current?.getConverters() ?? converters.snapshot()
    const pendingPrompt = lastMessage?.role === 'user'
      ? draftMessageToComposer(lastMessage, snapshot) : undefined
    const history = pendingPrompt ? editDraft.messages.slice(0, -1) : editDraft.messages
    return editor.save(destination, (response: AddMessageResponse, target?: TargetInstance | null) => {
      handleEditorSaved(response, target)
      restoreEditorPrompt(pendingPrompt, snapshot)
    }, history, reuseUnsentConversation && destination === 'same_attack'
      && Boolean(editDraft.sourceConversationId && editDraft.sourceAttackId === attackResultId))
  }

  const handleExitEditor = (): Promise<boolean> => {
    if (exitSaveRef.current) {
      return exitSaveRef.current
    }
    if (!editor.dirty) {
      const snapshot = editorRef.current?.getConverters() ?? converters.snapshot()
      const lastMessage = editDraft?.messages[editDraft.messages.length - 1]
      const prompt = (chatInputText || draftAttachments.length || activePieceConversions.text) && lastMessage?.role === 'user'
        ? draftMessageToComposer(lastMessage, snapshot) : undefined
      editor.discard()
      restoreEditorPrompt(prompt, snapshot)
      return Promise.resolve(true)
    }
    if (attackResultId && sameAttackDisabledReason) {
      setEditorError(sameAttackDisabledReason)
      return Promise.resolve(false)
    }
    const pending = handleSaveEditor(
      attackResultId ? 'same_attack' : 'new_attack', true,
    ).finally(() => { if (exitSaveRef.current === pending) exitSaveRef.current = null })
    exitSaveRef.current = pending
    return pending
  }

  const copyConversation = async (
    messageIndex: number, destination: ConversationCopyDestination,
  ): Promise<void> => {
    if (!attackResultId || !viewedConversationId || copyingRef.current || isSending || isLoadingEdit) return
    if (destination === 'same_attack' && isMutationLocked) return
    if (destination !== 'same_attack' && (!runtime.ready || !defaultsReady)) return
    if (destination === 'same_attack' && recoverableSend
      && messageIndex === recoverableSend.failedRequestMessageIndex) {
      await handleRecoverProcessingError()
      return
    }
    const sourceId = viewedConversationId
    const draftRevision = inputBoxRef.current?.getDraftRevision()
    copyingRef.current = true
    setIsLoadingEdit(true)
    setEditorError(null)
    setEditorNotice(null)
    try {
      const source = await attacksApi.getMessages(attackResultId, sourceId)
      if (destination !== 'same_attack' && (
        launchStateRef.current.generation !== runtime.generation
        || !launchStateRef.current.ready || !launchStateRef.current.defaultsReady
      )) {
        throw new Error('Runtime or default labels changed while loading this conversation. Retry after default labels finish loading.')
      }
      const isFailedPrompt = destination === 'new_attack' && recoverableSend && messageIndex === recoverableSend.failedRequestMessageIndex
      const sourceMessages = isFailedPrompt
        ? source.messages.filter((message: BackendMessage) => message.turn_number <= recoverableSend.historyCutoffIndex)
        : source.messages.slice(0, messageIndex + 1)
      let copiedMessages = toConversationDraft(sourceMessages)
      if (isFailedPrompt && activeTarget
        && editorTargetDisabledReason(activeTarget, draftDataTypes(copiedMessages), copiedMessages)) {
        copiedMessages = []
      }
      if (destination === 'same_attack' && targetResolutionStatus !== 'unbound' && activeTarget) {
        const reason = editorTargetDisabledReason(activeTarget, draftDataTypes(copiedMessages), copiedMessages)
        if (reason) throw new Error(reason)
      }
      let target = activeTarget
      if (destination === 'technique' || (destination === 'same_attack' && targetResolutionStatus === 'unbound')) {
        target = null
      } else if (destination === 'new_attack' && activeTarget && !isFailedPrompt
        && editorTargetDisabledReason(activeTarget, draftDataTypes(copiedMessages), copiedMessages)) {
        target = null
      }
      if (isFailedPrompt && copiedMessages.length === 0) {
        const response = await attacksApi.createAttack({
          target_registry_name: target?.target_registry_name,
          name: objective || undefined,
          labels,
        })
        if (getViewedConversationId() !== sourceId || inputBoxRef.current?.getDraftRevision() !== draftRevision) return
        onConversationCreated(response.attack_result_id, response.conversation_id, objective, target)
        restoreRecoverableDraft(response.conversation_id)
        setIsPanelOpen(true)
        setPanelRefreshKey((key: number) => key + 1)
        return
      }
      const response = await copySave.save({
        sourceAttackId: attackResultId,
        sourceConversationId: sourceId,
        initialObjective: objective,
        objective,
        target,
        labels,
        messages: copiedMessages,
      }, destination === 'same_attack' ? 'same_attack' : 'new_attack')
      if (getViewedConversationId() !== sourceId) return
      if (destination === 'technique') {
        const params = new URLSearchParams({ prepended_conversation_id: response.messages.conversation_id })
        navigate(`/scanner/technique.execute?${params.toString()}`)
        return
      }
      if (isFailedPrompt && inputBoxRef.current?.getDraftRevision() !== draftRevision) return
      if (destination === 'same_attack') onSelectConversation(response.messages.conversation_id)
      else onConversationCreated(response.attack.attack_result_id, response.messages.conversation_id, response.attack.objective, target)
      onAttackChange?.(response.attack)
      if (isFailedPrompt) restoreRecoverableDraft(response.messages.conversation_id)
      setIsPanelOpen(true)
      setPanelRefreshKey((key: number) => key + 1)
    } catch (error) {
      if (getViewedConversationId() === sourceId) setEditorError(toApiError(error).detail)
    } finally {
      copyingRef.current = false
      setIsLoadingEdit(false)
    }
  }

  const sameAttackDisabledReason = !attackResultId ? 'No saved attack exists yet.'
    : editDraft?.temperature.trim() ? 'Choose New attack to change the temperature.'
    : attackOperator && attackOperator !== currentOperator ? 'This attack belongs to another operator.'
    : attackTarget && (!editorTarget || !targetInfoMatchesTarget(attackTarget, editorTarget))
      ? 'The selected target differs from this attack. Choose New attack.'
    : targetResolutionStatus === 'unbound' && editorTarget
      ? 'Choose New attack to select a target while editing an unbound attack.'
    : isTargetResolutionLocked ? 'The source target cannot be safely resolved. Choose New attack.'
    : targetResolutionStatus !== 'unbound' && activeTarget ? historyReplayDisabledReason(activeTarget, editor.history)
    : undefined
  const newAttackDisabledReason = !runtime.ready || !defaultsReady
    ? newActivityBlockedReason ?? 'Default labels are not ready. Retry after default labels finish loading.'
    : undefined
  const editorDataTypes = draftDataTypes(editDraft?.messages ?? [])
  const temperatureTarget = editDraft !== null ? editorTarget : activeTarget
  const temperatureReadOnly = editDraft === null && Boolean(attackResultId) && targetResolutionStatus !== 'unbound'
  const temperatureExplanation = temperatureTarget && !temperatureTarget.supports_temperature_override
    ? temperatureTarget.reconstruction_error ?? 'This target does not support a separate temperature setting.'
    : temperatureReadOnly ? 'Temperature can only be modified in a new attack' : ''
  const temperatureExplanationContext = attackResultId ?? temperatureTarget?.identifier.hash
  const unboundHistoryError = targetResolutionStatus === 'unbound' && activeTarget
    ? historyReplayDisabledReason(activeTarget, messages) : undefined
  const repeatDisabledReason = converters.repeatDisabledReason
    ?? (activeTarget ? historyReplayDisabledReason(activeTarget, messages) : undefined)
  const undoHistory = undoableSend?.attackResultId === attackResultId
    && undoableSend.conversationId === viewedConversationId ? undoableSend.history : messages
  const showUndoLastSend = !activeTarget || !historyReplayDisabledReason(activeTarget, undoHistory)
  const branchConversationDisabledReason = (index: number): string | undefined => {
    if (targetResolutionStatus === 'unbound') return undefined
    if (!activeTarget) return 'Configure a target before branching this conversation.'
    if (recoverableSend?.failedRequestMessageIndex === index) {
      return historyReplayDisabledReason(
        activeTarget, activeTarget.capabilities?.supports_multi_turn ? recoverableSend.history : [],
      )
    }
    return historyReplayDisabledReason(activeTarget, messages.slice(0, index + 1))
  }

  const undoDisabledReason = !undoableSend
    ? 'Undo is available after a single successful send from this tab, with its converters still in memory.'
    : !runtime.ready || undoableSend.converters.generation !== runtime.generation
      ? 'The runtime changed. The previous converter state is no longer available.'
    : isUndoingSend || sendingConversations.size > 0 || converters.isConverting
      ? 'Wait for the current operation to finish before undoing a send.'
    : editDraft !== null || isLoadingEdit || isLoadingAttack || isLoadingMessages || awaitingConversationLoad
      ? 'Finish loading or editing the conversation before undoing a send.'
    : isMutationLocked || sendIssue?.blocking
      ? 'This conversation is currently read-only.'
    : undoableSend.attackResultId !== attackResultId || undoableSend.conversationId !== viewedConversationId
      ? 'Open the conversation containing the last send to undo it.'
    : activeTarget && historyReplayDisabledReason(activeTarget, undoableSend.history)
      ? 'This target cannot replay edited conversation history.'
    : (chatInputText || draftAttachments.length || Object.entries(converters.workingInputs).some(
      ([id, value]: [string, string]) => value !== converters.inputs.find((input) => input.id === id)?.value,
    ) || Object.keys(converters.applied).length)
      ? 'Clear the current prompt and conversion edits before undoing the previous send.'
    : JSON.stringify(converters.pipelines) !== JSON.stringify(undoableSend.converters.pipelines)
      ? 'The converter pipeline changed after sending. Undo is unavailable for this turn.'
    : Object.keys(undoableSend.converters.errors).length > 0
      ? 'The previous conversion was incomplete. Undo is unavailable for this turn.'
    : undefined

  const handleUndoLastSend = async (): Promise<void> => {
    if (undoDisabledReason || !undoableSend || undoInFlight.current) return
    const saved = undoableSend
    const revision = inputBoxRef.current?.getDraftRevision()
    let branchAttempted = false
    undoInFlight.current = true
    setIsUndoingSend(true)
    setIsLoadingEdit(true)
    setEditorError(null)
    setEditorNotice(null)
    try {
      const source = await attacksApi.getMessages(saved.attackResultId, saved.conversationId)
      if (conversationPieceSignature(source) !== saved.historySignature) {
        setUndoableSend(null)
        throw new Error('The conversation changed after this send. Undo is no longer available.')
      }
      const draftUnchanged = (): boolean => viewedAttackRef.current === saved.attackResultId
        && getViewedConversationId() === saved.conversationId
        && inputBoxRef.current?.getDraftRevision() === revision
      if (!draftUnchanged() || launchStateRef.current.generation !== saved.converters.generation) {
        throw new Error('The view, prompt, or runtime changed. Your current draft was not replaced.')
      }
      branchAttempted = true
      const branch = await attacksApi.createConversation(saved.attackResultId, saved.cutoffIndex < 0 ? {} : {
        source_conversation_id: saved.conversationId, cutoff_index: saved.cutoffIndex,
      })
      const [history, attack] = await Promise.all([
        attacksApi.getMessages(saved.attackResultId, branch.conversation_id),
        attacksApi.getAttack(saved.attackResultId),
      ])
      setUndoableSend(null)
      if (!draftUnchanged() || launchStateRef.current.generation !== saved.converters.generation) {
        setEditorNotice(`Undo created conversation ${branch.conversation_id} without replacing your current draft.`)
        return
      }
      setMessages(backendMessagesToFrontend(history.messages))
      markConversationLoaded(branch.conversation_id)
      onSelectConversation(branch.conversation_id)
      onAttackChange?.(attack)
      setChatInputText(saved.originalValue)
      setDraftAttachments(saved.attachments)
      inputBoxRef.current?.restoreDraft(saved.originalValue, saved.attachments, saved.textMetadata)
      converters.restoreSnapshot(saved.converters, saved.originalValue, saved.attachments)
      inputBoxRef.current?.focus()
      setEditorNotice('Last send undone. The original sent conversation remains in history.')
    } catch (error) {
      if (branchAttempted) setUndoableSend(null)
      const detail = toApiError(error).detail
      setEditorError(branchAttempted
        ? `Undo could not finish. Check the conversation list before trying again. ${detail}`
        : detail)
    } finally {
      if (branchAttempted) setPanelRefreshKey((key: number) => key + 1)
      undoInFlight.current = false
      setIsUndoingSend(false)
      setIsLoadingEdit(false)
    }
  }

  const singleTurnLimitReached = activeTarget?.capabilities?.supports_multi_turn === false && messages.some(m => m.role === 'user')
  const hasProcessingError = Boolean(recoverableSend)
  const inputDisabledReasons = [
    ...(isUndoingSend ? ['The previous send is being restored. Wait for undo to finish.'] : []),
    ...(!runtime.ready ? ['The runtime is not ready. Wait for initialization to finish.'] : []),
    ...(isSending ? ['A message is being sent. Wait for the response.'] : []),
    ...(isLoadingAttack ? ['The attack is loading. Wait for it to finish.'] : []),
    ...(isEvidenceUnavailable ? ['This finding evidence is unavailable. Sending is disabled.'] : []),
    ...(hasProcessingError
      ? ['This conversation contains a target error. Use Copy or Branch on the failed prompt to branch into a new conversation or attack.']
      : []),
  ]
  const sendDisabledReasons = [
    ...(!activeTarget ? ['Select a target to send a message.'] : []),
    ...(!attackResultId && !defaultsReady
      ? [newActivityBlockedReason ?? 'Default labels are not ready. Wait for them to load.'] : []),
    ...(editDraft !== null ? ['Finish editing the conversation before sending.'] : []),
    ...(isLoadingMessages || awaitingConversationLoad ? ['The conversation is loading. Wait for it to finish.'] : []),
    ...(sendIssue?.blocking ? [sendIssue.description] : []),
    ...(converters.isConverting ? ['A conversion is running. Wait for it to finish.'] : []),
    ...(unboundHistoryError ? [`${unboundHistoryError} Choose a target that can replay this saved conversation before sending.`] : []),
  ]

  const handleUseAsTemplate = (): void => { void beginEdit(defaultBranchTarget ?? activeTarget) }

  const { canExport: canExportConversation, isExporting, exportAs } = useConversationExport({
    messages,
    conversationId: activeConversationId ?? conversationId,
    blocked: isSending || Boolean(isLoadingAttack) || isLoadingMessages || awaitingConversationLoad,
  })

  // Chat owns these handlers and states even when the layout hosts the controls.
  const toolbar = (
    <div
      className={toolbarContainer ? styles.sharedToolbar : styles.ribbon}
      role="group"
      aria-label="Chat controls"
      data-conversation-editor-controls
    >
      <div className={styles.conversationControls} role="group" aria-label="Conversation settings">
        <div className={mergeClasses(styles.conversationInfo, toolbarContainer ? styles.sharedTarget : undefined)}>
          {(!attackResultId || targetResolutionStatus === 'unbound' || editDraft !== null) && !isLoadingAttack ? (
            <ChatTargetPicker
              target={editDraft !== null ? editorTarget : activeTarget}
              targets={availableTargets}
              loading={targetsLoading}
              error={targetsError}
              disabled={isSending || isSavingEditor}
              onSelect={(target) => {
                setTemperature('')
                if (editDraft !== null) editor.changeTarget(target)
                else onSelectTarget(target)
              }}
              disabledReason={editDraft !== null
                ? (target: TargetInstance) => editorTargetDisabledReason(target, editorDataTypes, editor.history)
                : targetResolutionStatus === 'unbound'
                  ? (target: TargetInstance) => historyReplayDisabledReason(target, messages) : undefined}
            />
          ) : activeTarget ? (
            <TargetBadge target={activeTarget} />
          ) : (
            <Text size={200} className={styles.noTarget}>
              No target selected
            </Text>
          )}
        </div>
        {temperatureTarget && (
          <Field label="Temperature:" orientation="horizontal" size="small" className={styles.temperatureField}
            hint={!temperatureReadOnly && temperatureTarget.supports_temperature_override && editDraft !== null
              ? 'Changes apply only to a new attack.' : undefined}>
            <Tooltip content={temperatureExplanation} relationship="description"
              visible={Boolean(temperatureExplanation) && temperatureExplanationId === temperatureExplanationContext}
              onVisibleChange={(_, data) => {
                setTemperatureExplanationId(data.visible ? temperatureExplanationContext ?? null : null)
              }}>
              <span
                tabIndex={!temperatureTarget.supports_temperature_override || temperatureReadOnly && (isSending || isSavingEditor) ? 0 : undefined}
                onPointerDownCapture={() => {
                  if (temperatureReadOnly) setTemperatureExplanationId(temperatureExplanationContext ?? null)
                }}>
                <Input type="number" min={0} max={2} step={0.1} size="small" className={styles.temperatureInput}
                  aria-label="Temperature"
                  value={temperatureReadOnly
                    ? String(temperatureTarget.binding?.temperature ?? temperatureTarget.identifier.temperature ?? '')
                    : editDraft !== null ? editDraft.temperature : temperature}
                  placeholder={String(temperatureTarget.binding?.temperature ?? temperatureTarget.identifier.temperature ?? 'Default')}
                  readOnly={temperatureReadOnly}
                  disabled={isSending || isSavingEditor || !temperatureTarget.supports_temperature_override}
                  onChange={(_, data) => {
                    if (editDraft !== null) editor.changeTemperature(data.value)
                    else setTemperature(data.value)
                  }} />
              </span>
            </Tooltip>
          </Field>
        )}
        <div className={styles.editActions}>
          <Button appearance="subtle" className={styles.ribbonAction} icon={<EditRegular />}
            disabled={editDraft !== null || isSending || isLoadingEdit || isLoadingAttack || isLoadingMessages || awaitingConversationLoad}
            onClick={() => { void beginEdit() }}
          >{isLoadingEdit ? 'Loading editor...' : 'Edit Conversation'}</Button>
          {editDraft !== null && <Button appearance="subtle" className={styles.ribbonAction} icon={<ArrowShuffleRegular />}
            disabled={isSavingEditor} onClick={() => editorRef.current?.convertConversation()}>Convert Conversation</Button>}
        </div>
      </div>
      <div className={mergeClasses(styles.ribbonActions, styles.sharedActions)}
        role="group" aria-label="Conversation actions">
        <Tooltip content="Render all messages as Markdown by default" relationship="label">
          <Switch
            checked={globalMarkdown}
            onChange={handleMarkdownChange}
            label="Markdown"
            data-testid="global-markdown-toggle"
          />
        </Tooltip>
        <Menu>
          <MenuTrigger disableButtonEnhancement>
            <Tooltip content="Export conversation" relationship="label">
              <Button
                appearance="subtle"
                className={styles.ribbonAction}
                icon={isExporting ? <Spinner size="tiny" /> : <ArrowDownloadRegular />}
                disabled={!canExportConversation}
                aria-label="Export conversation"
                data-testid="export-conversation-btn"
              />
            </Tooltip>
          </MenuTrigger>
          <MenuPopover>
            <MenuList>
              <MenuItem
                onClick={() => exportAs('markdown')}
                disabled={isExporting}
                data-testid="export-markdown-item"
              >
                Export as Markdown (.md)
              </MenuItem>
              <MenuItem onClick={() => exportAs('json')} disabled={isExporting} data-testid="export-json-item">
                Export as JSON (.json)
              </MenuItem>
              <MenuItem onClick={() => exportAs('html')} disabled={isExporting} data-testid="export-html-item">
                Export as HTML (.html)
              </MenuItem>
            </MenuList>
          </MenuPopover>
        </Menu>
        <Tooltip content="Toggle conversations panel" relationship="label">
          <Button
            {...restoreFocusTargetAttributes}
            appearance="subtle"
            className={styles.ribbonAction}
            icon={<PanelRightRegular />}
            onClick={() => setIsPanelOpen((open) => !open)}
            disabled={!attackResultId}
            data-testid="toggle-panel-btn"
            aria-label="Toggle conversations panel"
            aria-expanded={isPanelOpen}
            aria-controls="conversation-panel"
          />
        </Tooltip>
        <Tooltip content={editDraft !== null
          ? editor.targetError ?? editor.validationError ?? newAttackDisabledReason ?? 'Save to new attack'
          : 'New Attack'} relationship="description">
          <Button
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => {
              if (editDraft !== null) editorRef.current?.saveToNewAttack()
              else {
                navigationRevisionRef.current += 1
                setIsPanelOpen(false)
                onNewAttack()
              }
            }}
            disabled={isSavingEditor
              || (editDraft !== null && Boolean(newAttackDisabledReason || editor.targetError || editor.validationError))
              || (editDraft === null && !attackResultId)}
            data-testid="new-attack-btn"
            aria-label={editDraft !== null ? 'Save to new attack' : 'New Attack'}
            className={styles.newAttackButton}
          >
            <span className={styles.newAttackLabel}>{editDraft !== null ? 'Save to new attack' : 'New Attack'}</span>
          </Button>
        </Tooltip>
      </div>
    </div>
  )

  return (
    <div className={styles.root}>
      <h1 className={styles.pageHeading}>Chat</h1>
      {isConverterPanelOpen && (
        <ConverterPanel
          onClose={() => setIsConverterPanelOpen(false)}
          controller={converters}
        />
      )}
      <div className={styles.chatArea} data-testid="chat-area">
        {scenarioResultId && (
          <div className={styles.breadcrumbBar}>
            <Breadcrumb aria-label="Attack provenance" size="small">
              <BreadcrumbItem>
                <Text size={200}>Scanner History</Text>
              </BreadcrumbItem>
              <BreadcrumbDivider />
              <BreadcrumbItem>
                <Link
                  className={styles.breadcrumbLink}
                  to={scenarioRunRoutePath(scenarioResultId)}
                  aria-label={`Return to scenario run ${scenarioResultId}`}
                >
                  Scenario run {scenarioResultId.slice(0, 8)}
                </Link>
              </BreadcrumbItem>
            </Breadcrumb>
          </div>
        )}
        {toolbarContainer ? createPortal(toolbar, toolbarContainer) : toolbar}
        <ObjectiveHeader
          key={`${attackResultId ?? 'new'}-${editDraft === null ? 'saved' : 'draft'}`}
          objective={editDraft === null ? currentObjective : editorObjective}
          draftMode={editDraft !== null}
          outcome={outcome}
          automatedScore={automatedScore}
          humanScore={humanScore}
          canUpdateOutcome={
            editDraft === null
            &&
            Boolean(attackResultId)
            && Boolean(lastResponseMessagePieceId)
            && Boolean(currentObjective.trim())
            && !isScoreLocked
          }
          canRemoveHumanScore={
            editDraft === null
            &&
            Boolean(attackResultId)
            && Boolean(humanScore)
            && !isScoreLocked
          }
          onUpdateHumanScore={updateHumanScore}
          onRemoveHumanScore={removeHumanScore}
          canAdd={
            !isSavingEditor
            && !isLoadingAttack
            && !isLoadingMessages
            && !awaitingConversationLoad
            && (editDraft !== null || !isOperatorLocked)
          }
          onAdd={handleAddObjective}
          onAddFinding={() => setIsFindingDialogOpen(true)}
          canAddFinding={!isFindingActionDisabled}
          outcomeButtonRef={outcomeButtonRef}
        />
        <FindingEvidenceDialog
          attackResultId={isEvidenceUnavailable ? '' : attackResultId ?? ''}
          defaultOperationId={operationId} disabled={isFindingActionDisabled}
          open={isFindingDialogOpen} onOpenChange={setIsFindingDialogOpen} returnFocusRef={outcomeButtonRef} />
        {editorError && <MessageBar intent="error"><MessageBarBody>{editorError}</MessageBarBody></MessageBar>}
        {editorNotice && <MessageBar intent="success"
          className={mergeClasses(styles.infoNotice, editorNotice.fading && styles.fadingInfoNotice)}>
          <MessageBarBody>{editorNotice.message}</MessageBarBody>
        </MessageBar>}
        {editDraft === null && unboundHistoryError && <MessageBar intent="warning"><MessageBarBody>
          {unboundHistoryError} Choose a target that can replay this saved conversation before sending.
        </MessageBarBody></MessageBar>}
        {editDraft !== null && <ConversationEditor
          key={editDraft.id}
          ref={editorRef}
          initialConverters={editorInitialConverters}
          controller={editor}
          sameAttackDisabledReason={sameAttackDisabledReason}
          newAttackDisabledReason={newAttackDisabledReason}
          onSaved={handleEditorSaved}
          onSave={handleSaveEditor}
          onExit={handleExitEditor}
          saveDestination={attackResultId ? 'same_attack' : 'new_attack'}
          saveLabel="Save"
        />}
        {editDraft === null && conversationLoadIssue?.conversationId === viewedConversationId && (
          <MessageBar intent={conversationLoadIssue.unavailable ? 'warning' : 'error'}>
            <MessageBarBody>{conversationLoadIssue.detail}{' '}
              {!conversationLoadIssue.unavailable && attackResultId && viewedConversationId && (
                <Button onClick={() => { void loadConversation(attackResultId, viewedConversationId) }}>
                  Retry conversation load
                </Button>
              )}
            </MessageBarBody>
          </MessageBar>
        )}
        {editDraft === null && <MessageList
          messages={isEvidenceUnavailable ? [] : messages}
          onCopyToInput={handleCopyToInput}
          onCopyToNewConversation={(index: number) => { void copyConversation(index, 'same_attack') }}
          onCopyToNewAttack={newAttackDisabledReason ? undefined : (index: number) => { void copyConversation(index, 'new_attack') }}
          onUseInTechnique={newAttackDisabledReason ? undefined : (index: number) => { void copyConversation(index, 'technique') }}
          copyConversationDisabled={isSending || isLoadingEdit || isRecoveringProcessingError || Boolean(sendIssue?.blocking)}
          newConversationDisabledReason={isMutationLocked ? "This attack is read-only. Copy to a new attack instead." : undefined}
          getBranchConversationDisabledReason={branchConversationDisabledReason}
          isLoading={isLoadingAttack || isLoadingMessages || awaitingConversationLoad}
          globalMarkdown={globalMarkdown}
          onEditConversation={() => { void beginEdit() }}
        />}
        {repeatSends.filter((view: RepeatSendView) => view.progress.attack_result_id === attackResultId).map(
          (view: RepeatSendView) => (
            <MultiSendProgress
              key={view.progress.send_id}
              progress={view.progress}
              needsRefresh={view.needsRefresh}
              onSelectConversation={handlePanelSelectConversation}
              onDismiss={() => setRepeatSends((previous: RepeatSendView[]) => previous.filter(
                (candidate: RepeatSendView) => candidate.progress.send_id !== view.progress.send_id,
              ))}
              onRefresh={() => {
                for (const operation of pendingSendsRef.current.values()) {
                  if (operation.repeatGroup?.progress.send_id === view.progress.send_id && operation.needsRefresh) {
                    void refreshSend(operation.conversationId)
                  }
                }
              }}
            />
          ),
        )}
        {sendIssue && (
          <MessageBar intent="error" aria-label="Message send status">
            <MessageBarBody>{sendIssue.description}</MessageBarBody>
            <MessageBarActions>
              {sendIssue.draft && (
                <Button
                  className={styles.ribbonAction} disabled={isSending || isMutationLocked || !runtime.ready}
                  onClick={restorePreparationDraft}
                >
                  Restore prompt
                </Button>
              )}
              <Button className={styles.ribbonAction} disabled={isSending} onClick={() => { void refreshSend() }}>
                Refresh saved messages
              </Button>
            </MessageBarActions>
          </MessageBar>
        )}
        <div hidden={editDraft !== null}>
        {Boolean(recoveredDraftWarningConversationId) && recoveredDraftWarningConversationId === viewedConversationId && (
          <MessageBar intent="warning">
            <MessageBarBody>Converter choices could not be restored. Select and apply converters again before sending.</MessageBarBody>
          </MessageBar>
        )}
        <ChatInputArea
          ref={inputBoxRef}
          undoLastSend={showUndoLastSend ? {
            disabledReason: undoDisabledReason,
            isUndoing: isUndoingSend,
            onUndo: () => { void handleUndoLastSend() },
          } : undefined}
          onSend={handleSend}
          sendDisabled={sendDisabledReasons.length > 0}
          sendDisabledReasons={sendDisabledReasons}
          repeatDisabledReason={repeatDisabledReason}
          conversionRevisionKey={conversionRevisionKey}
          disabledReasons={inputDisabledReasons}
          showSystemPrompt={!attackResultId}
          supportsSystemPrompt={supportsSystemPrompt}
          systemPrompt={systemPrompt}
          onSystemPromptChange={setSystemPrompt}
          disabled={
            !runtime.ready
            || isSending
            || editDraft !== null
            || isLoadingAttack
            || singleTurnLimitReached
            || isMutationLocked
            || hasProcessingError
            || isUndoingSend
          }
          activeTarget={activeTarget}
          singleTurnLimitReached={singleTurnLimitReached}
          onNewConversation={handleNewConversation}
          operatorLocked={isOperatorLocked}
          crossTargetLocked={isCrossTargetLocked}
          targetResolutionStatus={targetResolutionStatus}
          onRetryTargetResolution={onRetryTargetResolution}
          onUseAsTemplate={handleUseAsTemplate}
          attackOperator={isOperatorLocked ? attackOperator ?? undefined : undefined}
          onConfigureTarget={() => onNavigate?.('registry')}
          onToggleConverterPanel={() => setIsConverterPanelOpen(prev => !prev)}
          isConverterPanelOpen={isConverterPanelOpen}
          onInputChange={setChatInputText}
          onAttachmentsChange={setDraftAttachments}
          convertedValue={activePieceConversions['text']?.convertedDataType === 'text' ? (activePieceConversions['text']?.convertedValue ?? null) : null}
          originalValue={activePieceConversions['text']?.originalValue ?? null}
          onClearConversion={() => converters.clear('text')}
          onClearAllConversions={() => {
            converters.clearAll()
            setRecoveredDraftWarningConversationId(null)
          }}
          onConvertedValueChange={(val: string) => converters.editConvertedValue('text', val)}
          convertedFileChip={(() => {
            const tc = activePieceConversions['text']
            if (!tc || tc.convertedDataType === 'text') return null
            if (!isPathDataType(tc.convertedDataType)) return null
            return {
              name: basenameFromValue(tc.convertedValue, 'output'),
              url: buildMediaUrl(tc.convertedValue),
              iconKind: dataTypeToAttachmentKind(tc.convertedDataType),
            }
          })()}
          onClearConvertedFileChip={() => converters.clear('text')}
          converterOutputDataTypes={Object.values(activePieceConversions).map((c) => c.convertedDataType)}
          mediaConversions={Object.entries(activePieceConversions)
            .filter(([k]) => k !== 'text')
            .map(([, conversion]) => conversion)}
          onClearMediaConversion={converters.clear}
        />
        </div>
      </div>
      <Drawer
        as="aside"
        {...restoreFocusSourceAttributes}
        type={isNarrowScreen ? 'overlay' : 'inline'}
        position="end"
        separator
        open={isPanelOpen}
        onOpenChange={(_, { open }) => setIsPanelOpen(open)}
        className={mergeClasses(
          styles.conversationDrawer,
          isNarrowScreen && styles.narrowConversationDrawer,
        )}
        aria-label="Attack Conversations"
      >
        <ConversationPanel
          attackResultId={attackResultId}
          activeConversationId={activeConversationId}
          onSelectConversation={handlePanelSelectConversation}
          onNewConversation={handleNewConversation}
          onChangeMainConversation={handleChangeMainConversation}
          onClose={() => setIsPanelOpen(false)}
          lockedReason={
            !activeTarget ? 'Configure a target to enable this action.'
            : isOperatorLocked ? 'Cannot modify — attack belongs to a different operator.'
            : isCrossTargetLocked ? 'Cannot modify — attack was created with a different target.'
            : isTargetResolutionLocked ? 'Cannot modify — the attack target could not be safely resolved.'
            : undefined
          }
          refreshKey={panelRefreshKey}
        />
      </Drawer>
    </div>
  )
}

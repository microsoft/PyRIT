import { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo, useId } from 'react'
import type { ChangeEvent, MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Button,
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
import { Link, useNavigate, useLocation } from 'react-router'
import MessageList from './MessageList'
import ChatInputArea from './ChatInputArea'
import MultiSendProgress from './MultiSendProgress'
import ConversationPanel from './ConversationPanel'
import FindingEvidenceDialog from './FindingEvidenceDialog'
import ConverterPanel from './ConverterPanel'
import TargetBadge from './TargetBadge'
import ChatTargetPicker from './ChatTargetPicker'
import { generateClientId } from '@/utils/clientId'
import FileDropZone from '@/components/Media/FileDropZone'
import ObjectiveHeader from './ObjectiveHeader'
import type { ApplyScorerStatus, RegisteredComponentReference, ScoringFailure, ManualContinuation, ScorerInstance, RequestGenerationDraft } from '@/types'
import ConversationEditor from './ConversationEditor'
import type { ConversationEditorHandle } from './ConversationEditor'
import {
  attachmentDraft, canBranchFirstPrompt, draftDataTypes, draftMessageToComposer, editorTargetDisabledReason, historyReplayDisabledReason,
  newDraftPiece, toConversationDraft,
} from '@/utils/conversationDraft'
import { useConversationDraft } from '@/hooks/useConversationDraft'
import { useConversationMessages } from '@/hooks/useConversationMessages'
import { useAttackMutations } from '@/hooks/useAttackMutations'
import { useConversationExport } from '@/hooks/useConversationExport'
import { useConversationCopy } from '@/hooks/useConversationCopy'
import { conversationPieceSignature, preparePromptRestore } from '@/utils/promptRestore'
import { useChatDraftHandoff } from '@/hooks/useChatDraftHandoff'
import {
  shouldApplyPreservedDraft,
  canApplyPendingImport,
  hasChatDraftState,
  isChatDraftDirty,
  computeAttachmentSignature,
  stableMetadataString,
} from '@/utils/chatDraft'
import { resolveTargetReference, sameTarget, targetReference } from '@/utils/targetIdentity'
import { mergeImportedHistory } from '@/utils/seedImport'
import { resolveChatScorerReference } from '@/utils/scorerPresentation'
import type { PieceConversion } from './converterTypes'
import { useChatConverters } from '@/hooks/useChatConverters'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useRuntime } from '@/hooks/useRuntime'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import { generationConfigError, parseGenerationConfig, withRequestGenerationConfig } from '@/utils/requestGeneration'
import { NARROW_SCREEN_QUERY } from '@/styles/touchTargets'
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
  BackendMessagePiece,
  BackendScore,
  ChatSendOutcome,
  ChatConverterSnapshot,
  ChatDraftContent,
  ChatDraftContextState,
  ConversationMessagesResponse,
  ConversationDraftMessage,
  ConversationDraftPiece,
  ConverterInputPiece,
  ConverterPipelineStage,
  CreateAttackRequest,
  CreateConversationRequest,
  Message,
  MessageAttachment,
  MessageSendConversation,
  MessageSendRequest,
  MessageSendStatus,
  MessageSendObservation,
  MultiSendOptions,
  NewAttackContext,
  TargetInstance,
  TargetInfo,
  TargetReference,
} from '../../types'
import { isTargetResolutionBlocking, targetInfoMatchesTarget } from '../../utils/targetIdentity'
import { attackConversationRoutePath, scenarioRunRoutePath } from '../../utils/routeParams'
import type { ViewName } from '../Sidebar/Navigation'
import { CHAT_NOTICE_FADE_DURATION_MS, useChatWindowStyles } from './ChatWindow.styles'

const RETRYABLE_TARGET_RESPONSE_ERROR = 'processing'
const CHAT_NOTICE_VISIBLE_DURATION_MS = 5_000
const SAVED_MESSAGE_PAGE_SIZE = 100
const SAVED_REFERENCE_PAGE_SIZE = 50
const INCOMPLETE_TRANSCRIPT_REASON = 'Load the complete saved transcript before continuing, copying, exporting, or assessing this result.'

interface ChatInfoNotice {
  readonly id: string
  readonly message: string
  readonly fading: boolean
}

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
}

interface PendingSend {
  readonly generationDraft: RequestGenerationDraft
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
  readonly target: TargetReference
  readonly chatScorerExplicit: boolean
  chatScorerChoice?: RegisteredComponentReference | null
  observation?: MessageSendObservation
  readonly converterSnapshot?: ChatConverterSnapshot
  readonly pipelines?: Record<string, ConverterPipelineStage[]>
  attackResultId: string | null
  conversationId: string
  needsRefresh: boolean
  responseReadId?: number
  progress?: MessageSendStatus
  repeatGroup?: RepeatSendGroup
  preservedDraft?: Pick<ChatDraftContextState, 'originatingAttackId' | 'originatingConversationId' | 'sequence'>
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

/** The backend's scoring projection for a conversation's latest response. */
interface ConversationScoring {
  readonly conversationId: string
  readonly score: BackendScore | null
  readonly failure: ScoringFailure | null
  readonly outcome?: AttackOutcome
  readonly latestResponseId: string | null
}

/** An Apply result, shown only for the context it was requested in. */
interface ConversationApplyStatus extends ApplyScorerStatus {
  readonly conversationId: string
  readonly responseId: string
  readonly scorerHash: string
  readonly objective: string
}

function conversationScoringOf(conversationId: string, response: ConversationMessagesResponse): ConversationScoring {
  return {
    conversationId,
    score: response.automated_score ?? null,
    failure: response.scoring_failure ?? null,
    outcome: response.outcome ?? undefined,
    latestResponseId: response.latest_response_id ?? null,
  }
}

function isSendFinished(progress: MessageSendConversation): boolean {
  return ['completed', 'failed', 'interrupted'].includes(progress.state)
}

function userPieceIds(response: ConversationMessagesResponse): Set<string> {
  return new Set(response.messages.filter((message) => message.role === 'user')
    .flatMap((message) => message.message_pieces.map((piece) => piece.id)))
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
  }
}

interface ChatWindowProps {
  /** Shared layout slot; standalone chat renders its toolbar inline. */
  toolbarContainer?: HTMLElement | null
  activeTarget: TargetInstance | null
  draftTargetReference?: TargetReference | null
  /** Route identity is available before the attack's metadata has loaded. */
  draftContext?: Pick<ChatDraftContextState, 'originatingAttackId' | 'originatingConversationId'>
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
  scorerDefaultsReady?: boolean
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
  chatScorer?: RegisteredComponentReference | null
  chatScorerInherited?: boolean
  executionReadOnly?: boolean
  manualContinuation?: ManualContinuation | null
  defaultChatScorer?: RegisteredComponentReference | null
  availableScorers?: ScorerInstance[]
  scorersLoading?: boolean
  scorersError?: string | null
  humanScore?: BackendScore | null
  lastResponseMessagePieceId?: string | null
  /** Validated scenario-run provenance for attacks opened from a run dashboard. */
  scenarioResultId?: string | null
  onReturnToScan?: () => void
  findingEvidenceId?: string | null
}

export default function ChatWindow({
  toolbarContainer,
  activeTarget,
  draftTargetReference,
  draftContext,
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
  scorerDefaultsReady = true,
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
  chatScorer = null,
  chatScorerInherited = false,
  executionReadOnly = false,
  manualContinuation,
  defaultChatScorer,
  availableScorers = [],
  scorersLoading = false,
  scorersError,
  humanScore,
  lastResponseMessagePieceId,
  scenarioResultId,
  onReturnToScan,
  findingEvidenceId,
}: ChatWindowProps) {
  const styles = useChatWindowStyles()
  const transcriptStatusId = useId()
  const transcriptDisabledReasonId = useId()
  const transcriptStatusRef = useRef<HTMLSpanElement>(null)
  const chatAreaRef = useRef<HTMLDivElement>(null)
  const wasSavedEvidenceRef = useRef(executionReadOnly)
  const restoreFocusTargetAttributes = useRestoreFocusTarget()
  const restoreFocusSourceAttributes = useRestoreFocusSource()
  const navigate = useNavigate()
  const [isFindingDialogOpen, setIsFindingDialogOpen] = useState(false)
  const outcomeButtonRef = useRef<HTMLButtonElement>(null)
  const [generationDraft, setGenerationDraft] = useState<RequestGenerationDraft>({})
  const [generationTarget, setGenerationTarget] = useState(activeTarget)
  const [generationAttackId, setGenerationAttackId] = useState(attackResultId)
  const [createdGenerationAttackId, setCreatedGenerationAttackId] = useState<string | null>(null)
  if (generationAttackId !== attackResultId) {
    setGenerationAttackId(attackResultId)
    // Creating the first attack is not navigation; retain the next-send setting.
    if (attackResultId !== createdGenerationAttackId) setGenerationDraft({})
    setCreatedGenerationAttackId(null)
  }
  if (activeTarget && !sameTarget(activeTarget, generationTarget)) {
    setGenerationTarget(activeTarget)
    setGenerationDraft({})
  }
  const [conversationScoring, setConversationScoring] = useState<ConversationScoring | null>(null)
  const scoringRevisionRef = useRef(0)
  const [scoringRevision, setScoringRevision] = useState(0)
  const applyingScorer = useRef(false)
  const [applyingConversationId, setApplyingConversationId] = useState<string | null>(null)
  const isApplyingScorer = applyingConversationId !== null
  const [applyStatus, setApplyStatus] = useState<ConversationApplyStatus | null>(null)
  const [draftScorer, setDraftScorer] = useState<RegisteredComponentReference | null | undefined>(undefined)
  const selectedScorer = attackResultId ? chatScorer : draftScorer === undefined ? defaultChatScorer ?? null : draftScorer
  const scoringAttackRef = useRef(attackResultId)
  const scoringSelectionRef = useRef(JSON.stringify([attackResultId, objective, selectedScorer?.identifier_hash]))
  const [installedScoringSelection, setInstalledScoringSelection] = useState(
    JSON.stringify([attackResultId, objective, selectedScorer?.identifier_hash]),
  )
  const scorerSelectionReady = scorerDefaultsReady || draftScorer !== undefined
  const [scoringNotice, setScoringNotice] = useState<{ id: string; message: string; conversationId: string } | null>(null)
  const [scoringConversations, setScoringConversations] = useState<Set<string>>(new Set())
  const scoringNoticeId = scoringNotice?.id
  useEffect(() => {
    if (!scoringNoticeId) return
    const timer = window.setTimeout(() => {
      setScoringNotice((current) => current?.id === scoringNoticeId ? null : current)
    }, 10000)
    return () => { window.clearTimeout(timer) }
  }, [scoringNoticeId])
  const runtime = useRuntime()
  const newAttackContext: NewAttackContext = {
    generation: runtime.generation, ready: runtime.ready && defaultsReady && scorerSelectionReady, labels, operationId,
    chatScorer: executionReadOnly ? undefined
      : attackResultId || draftScorer === undefined ? defaultChatScorer : draftScorer,
    chatScorerExplicit: !attackResultId && draftScorer !== undefined,
  }
  const editor = useConversationDraft(newAttackContext, { pendingUserMessage: true })
  const { draft: editDraft, discard: discardEditor, changeObjective: setEditorObjective } = editor
  const editorTarget = editDraft?.target ?? null
  const editorObjective = editDraft?.objective ?? ''
  const isSavingEditor = editor.saving
  const editorRef = useRef<ConversationEditorHandle>(null)
  const [editorInitialConverters, setEditorInitialConverters] = useState<ChatConverterSnapshot>()
  const [isLoadingEdit, setIsLoadingEdit] = useState(false)
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
  const [importSystemConflict, setImportSystemConflict] = useState<string | null>(null)
  const [importSystemChoice, setImportSystemChoice] = useState<{ id: string; choice: 'destination' | 'imported' } | null>(null)
  const exitSaveRef = useRef<Promise<boolean> | null>(null)
  // Track sending state per conversation so parallel conversations can send independently
  const [sendingConversations, setSendingConversations] = useState<Set<string>>(new Set())
  const isSending = activeConversationId ? sendingConversations.has(activeConversationId) : Boolean(sendingConversations.size)
  const [isPanelOpen, setIsPanelOpen] = useState(false)
  const isNarrowScreen = useMediaQuery(NARROW_SCREEN_QUERY)
  const [isConverterPanelOpen, setIsConverterPanelOpen] = useState(false)
  const launchStateRef = useRef({ generation: runtime.generation, ready: runtime.ready, defaultsReady, scorerSelectionReady })
  useLayoutEffect(() => {
    launchStateRef.current = { generation: runtime.generation, ready: runtime.ready, defaultsReady, scorerSelectionReady }
  }, [runtime.generation, runtime.ready, defaultsReady, scorerSelectionReady])
  // Conversation-wide preference for rendering message text as Markdown.
  const { preferences, updatePreferences, rememberUse, observeChatSend } = useUserPreferences()
  const globalMarkdown = preferences.chatMarkdown
  const location = useLocation()
  const viewedConversationId = activeConversationId ?? conversationId
  useLayoutEffect(() => {
    const area = chatAreaRef.current
    if (area && (executionReadOnly || wasSavedEvidenceRef.current)) area.scrollTop = 0
    wasSavedEvidenceRef.current = executionReadOnly
  }, [executionReadOnly, attackResultId, viewedConversationId])
  const draftAttackId = draftContext ? draftContext.originatingAttackId : attackResultId
  const draftConversationId = draftContext ? draftContext.originatingConversationId : viewedConversationId
  const draftHandoff = useChatDraftHandoff()
  const [composerIdentity, setComposerIdentity] = useState({
    attackId: draftAttackId,
    conversationId: draftConversationId,
  })
  const composerIdentityMatches = composerIdentity.attackId === draftAttackId
    && composerIdentity.conversationId === draftConversationId
  const retainedComposerIdentityRef = useRef<string | null>(null)
  const matchingPreservedDraft = useMemo(() => {
    const candidate = draftHandoff.getPreservedDraft(draftAttackId, draftConversationId)
    return candidate && shouldApplyPreservedDraft(
      candidate,
      draftAttackId,
      draftConversationId,
    ) ? candidate.draft : null
  }, [draftHandoff, draftAttackId, draftConversationId])

  const [chatInputText, setChatInputText] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [draftAttachments, setDraftAttachments] = useState<MessageAttachment[]>([])

  const composerRevisionRef = useRef(0)
  const discardDraftOnUnmountRef = useRef(false)
  const lastTextRef = useRef(chatInputText)
  const lastAttachmentsSignatureRef = useRef(computeAttachmentSignature(draftAttachments))

  const handleComposerInputChange = useCallback((text: string): void => {
    discardDraftOnUnmountRef.current = false
    if (lastTextRef.current !== text) {
      lastTextRef.current = text
      composerRevisionRef.current += 1
    }
    setChatInputText(text)
  }, [])
  const handleComposerAttachmentsChange = useCallback((attachments: MessageAttachment[]): void => {
    discardDraftOnUnmountRef.current = false
    const nextSignature = computeAttachmentSignature(attachments)
    if (lastAttachmentsSignatureRef.current !== nextSignature) {
      lastAttachmentsSignatureRef.current = nextSignature
      composerRevisionRef.current += 1
    }
    setDraftAttachments(attachments)
  }, [])
  const converters = useChatConverters(chatInputText, draftAttachments)
  const convertersRef = useRef(converters)
  useLayoutEffect(() => { convertersRef.current = converters }, [converters])
  const { applied: activePieceConversions, restore: restoreConversions } = converters
  const [undoableSend, setUndoableSend] = useState<UndoableSend | null>(null)
  const [isUndoingSend, setIsUndoingSend] = useState(false)
  const undoInFlight = useRef(false)

  const handoffRestoredRef = useRef<number | null>(null)
  const importInFlightRef = useRef<string | null>(null)
  const importFailedRef = useRef<string | null>(null)
  const importMountedRef = useRef(true)
  useEffect(() => {
    importMountedRef.current = true
    return () => { importMountedRef.current = false }
  }, [])
  const currentAttackIdRef = useRef(draftAttackId)
  const currentConversationIdRef = useRef(draftConversationId)
  const pendingImportRef = useRef(draftHandoff.pendingImport)
  const activeSequenceRef = useRef(draftHandoff.activeSequence)
  const isActiveDraftDirtyRef = useRef(false)
  useLayoutEffect(() => {
    currentAttackIdRef.current = draftAttackId
    currentConversationIdRef.current = draftConversationId
    pendingImportRef.current = draftHandoff.pendingImport
    activeSequenceRef.current = draftHandoff.activeSequence
  }, [draftAttackId, draftConversationId, draftHandoff.pendingImport, draftHandoff.activeSequence])
  const ownsDraftRef = useRef(false)
  const preserveOnUnmountRef = useRef<(() => void) | null>(null)
  useEffect(() => () => { preserveOnUnmountRef.current?.() }, [])
  const [recoverableSends, setRecoverableSends] = useState<Record<string, RecoverableSendDraft>>({})
  const [isRecoveringProcessingError, setIsRecoveringProcessingError] = useState(false)
  const [panelRefreshKey, setPanelRefreshKey] = useState(0)
  const inputBoxRef = useRef<ChatInputAreaHandle>(null)
  const recoveryInFlightRef = useRef(false)
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
  const loadedBackendMessagesRef = useRef<Map<string, BackendMessage[]>>(new Map())
  const isSendInFlight = useCallback(
    (convId: string): boolean => sendingConvIdsRef.current.has(convId),
    [],
  )
  const handleConversationLoaded = useCallback((
    convId: string, response: ConversationMessagesResponse, frontendMessages: Message[],
  ): Message[] => {
    setConversationScoring(conversationScoringOf(convId, response))
    const savedUserIds = userPieceIds(response)
    loadedUserPieceIdsRef.current.set(convId, savedUserIds)
    if (response.messages) {
      loadedBackendMessagesRef.current.set(convId, response.messages)
    }
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
    messagePagination,
    loadedPieceCount,
    isTranscriptIncomplete,
    isLoadingMoreMessages,
    messageContinuationIssue,
    loadMoreMessages,
    loadConversation,
    refreshConversationScores,
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
    messagePageSize: executionReadOnly ? SAVED_MESSAGE_PAGE_SIZE : undefined,
    sendingConversations,
    isSendInFlight,
    onConversationLoaded: handleConversationLoaded,
  })
  const incompleteTranscriptReason = isTranscriptIncomplete ? INCOMPLETE_TRANSCRIPT_REASON : undefined
  const loadedPieceSummary = `${loadedPieceCount} message ${loadedPieceCount === 1 ? 'piece' : 'pieces'} loaded.`
  const savedRecovery = viewedConversationId
    ? recoverableSends[viewedConversationId]
    : undefined
  const recoverableSend = useMemo<RecoverableSendDraft | undefined>(() => (
    savedRecovery?.converterGeneration !== undefined && savedRecovery.converterGeneration !== runtime.generation
      ? {
          ...savedRecovery, conversions: {}, pipelines: undefined, source: 'persisted',
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
  const isMutationLocked = executionReadOnly || isOperatorLocked || isCrossTargetLocked || isTargetResolutionLocked
    || isEvidenceUnavailable || isTranscriptIncomplete
  const isDraftDestinationReady = !isLoadingAttack && !isLoadingMessages && !awaitingConversationLoad && !isSending
    && conversationLoadIssue?.conversationId !== viewedConversationId
    && !isMutationLocked && !editDraft && (attackResultId === null || viewedConversationId !== null)

  const isScoreLocked = isOperatorLocked || Boolean(isLoadingAttack) || isLoadingMessages
    || awaitingConversationLoad || isEvidenceUnavailable || isTranscriptIncomplete
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
    defaultChatScorer,
    lastResponseMessagePieceId,
    humanScore,
    isScoreLocked,
    refreshConversationScores,
    getViewedConversationId,
    onHumanScoreChange,
    onAttackChange,
    onObjectiveChange,
  })
  const scoringSelection = JSON.stringify([attackResultId, currentObjective, selectedScorer?.identifier_hash])
  const invalidateScoringSelection = useCallback((selection: string): void => {
    if (scoringSelectionRef.current === selection) return
    const attackChanged = scoringAttackRef.current !== attackResultId
    scoringAttackRef.current = attackResultId
    scoringSelectionRef.current = selection
    setInstalledScoringSelection(selection)
    scoringRevisionRef.current += 1
    setScoringRevision(scoringRevisionRef.current)
    setConversationScoring((current) => (
      attackChanged && current?.conversationId === viewedConversationId ? current : null
    ))
    setApplyStatus(null)
    // A first send can own this conversation's read before its created route commits.
    if (!attackChanged && viewedConversationId) invalidateConversationLoads(viewedConversationId)
  }, [attackResultId, viewedConversationId, invalidateConversationLoads])
  useLayoutEffect(() => {
    invalidateScoringSelection(scoringSelection)
  }, [scoringSelection, invalidateScoringSelection])
  const selectionIsCurrent = installedScoringSelection === scoringSelection
  const viewedScoring = selectionIsCurrent && conversationScoring?.conversationId === viewedConversationId
    ? conversationScoring : null
  const scoringInvalidated = scoringRevision !== 0 || !selectionIsCurrent
  const activeDraft: ChatDraftContent = {
    text: chatInputText,
    attachments: draftAttachments,
    systemPrompt,
    objective: attackResultId ? undefined : pendingObjective,
    generationDraft,
    ...(draftScorer !== undefined ? { chatScorer: draftScorer } : {}),
    target: draftTargetReference !== undefined
      ? draftTargetReference : activeTarget ? targetReference(activeTarget) : null,
    converters: converters.snapshot(),
    ...(editDraft ? {
      conversation: {
        messages: editDraft.messages,
        objective: editDraft.objective,
        initialObjective: editDraft.initialObjective,
        sourceConversationId: editDraft.sourceConversationId,
        sourceAttackId: editDraft.sourceAttackId,
        target: editDraft.target ? targetReference(editDraft.target) : null,
        pendingPromptId: editDraft.pendingPromptId,
        sourcePieceIds: editDraft.sourcePieceIds,
        unsavedImport: editDraft.unsavedImport,
      },
    } : {}),
  }
  const isActiveDraftDirty = isChatDraftDirty(activeDraft)
  const activateComposerIdentity = useCallback((reset: boolean): void => {
    if (reset) {
      preserveOnUnmountRef.current = null
      ownsDraftRef.current = false
      handoffRestoredRef.current = null
      lastTextRef.current = ''
      lastAttachmentsSignatureRef.current = ''
      inputBoxRef.current?.detachDraft()
      setChatInputText('')
      setDraftAttachments([])
      setSystemPrompt('')
      setGenerationDraft({})
      setDraftScorer(undefined)
      resetPendingObjective()
      restoreConversions('', [], {}, {})
    }
    setComposerIdentity({ attackId: draftAttackId, conversationId: draftConversationId })
  }, [draftAttackId, draftConversationId, resetPendingObjective, restoreConversions])
  useLayoutEffect(() => {
    if (composerIdentityMatches) return
    const identity = JSON.stringify([draftAttackId, draftConversationId])
    const isRetainedComposer = retainedComposerIdentityRef.current === identity
    const isResolvingConversation = composerIdentity.attackId === draftAttackId
      && composerIdentity.conversationId === null
    retainedComposerIdentityRef.current = null
    const reset = !isRetainedComposer && !isResolvingConversation
    if (reset) {
      preserveOnUnmountRef.current?.()
      const operation = pendingSendsRef.current.get(composerIdentity.conversationId ?? '__pending__')
      if (operation && inputBoxRef.current?.getDraftRevision() === operation.draftRevision) {
        operation.preservedDraft = draftHandoff.getPreservedDraft(
          composerIdentity.attackId, composerIdentity.conversationId,
        ) ?? undefined
      }
    }
    activateComposerIdentity(reset)
  }, [
    composerIdentityMatches, composerIdentity, draftAttackId, draftConversationId, draftHandoff,
    activateComposerIdentity,
  ])
  useLayoutEffect(() => {
    if (!composerIdentityMatches) return
    isActiveDraftDirtyRef.current = isActiveDraftDirty
    if (isDraftDestinationReady || isActiveDraftDirty) ownsDraftRef.current = true
    const savedDraft = { ...activeDraft, textMetadata: inputBoxRef.current?.getTextMetadata() }
    const preservedForThis = draftHandoff.getPreservedDraft(draftAttackId, draftConversationId)
    const isWaitingForRestore = matchingPreservedDraft !== null
      && handoffRestoredRef.current !== preservedForThis?.sequence
      && !isActiveDraftDirty
    const hasUnrelatedDraft = preservedForThis === null && draftHandoff.preservedDraft !== null
      && handoffRestoredRef.current === null && draftAttackId !== null
    const hasEditedContent = isActiveDraftDirty || Boolean(pendingObjective.trim())
      || Object.keys(generationDraft).length > 0 || draftScorer !== undefined
      || Object.values(activeDraft.converters?.pipelines ?? {}).some((stages: ConverterPipelineStage[]) => stages.length > 0)
    preserveOnUnmountRef.current = (): void => {
      const shouldDiscard = discardDraftOnUnmountRef.current
      discardDraftOnUnmountRef.current = false
      if (shouldDiscard || !ownsDraftRef.current || isWaitingForRestore
        || !hasChatDraftState(activeDraft) || (hasUnrelatedDraft && !hasEditedContent)) return
      draftHandoff.preserveDraft({
        originatingAttackId: draftAttackId,
        originatingConversationId: draftConversationId,
        originatingPath: location.pathname + location.search + location.hash,
        originatingState: location.state,
        draft: savedDraft,
      }, draftHandoff.activeSequence, draftHandoff.pendingImport?.id)
    }
  })

  const restoreComposerDraft = useCallback((draft: ChatDraftContent): MessageAttachment[] => {
    const attachments = draft.attachments.map(withDraftIdentity)
    // The next layout publishes the restored draft. Effect replay must not preserve the old snapshot.
    preserveOnUnmountRef.current = null
    lastTextRef.current = draft.text
    lastAttachmentsSignatureRef.current = computeAttachmentSignature(attachments)
    setChatInputText(draft.text)
    setDraftAttachments(attachments)
    if (draft.systemPrompt !== undefined) setSystemPrompt(draft.systemPrompt)
    if (!attackResultId && draft.objective !== undefined) void changeObjective(draft.objective, '')
    if (draft.generationDraft !== undefined) setGenerationDraft(draft.generationDraft)
    if ('chatScorer' in draft) setDraftScorer(draft.chatScorer)
    inputBoxRef.current?.restoreDraft(draft.text, attachments, draft.textMetadata)
    return attachments
  }, [attackResultId, changeObjective])
  const restoreConverterSnapshot = converters.restoreSnapshot

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
    || isEvidenceUnavailable || isLoadingAttack || isLoadingMessages || awaitingConversationLoad || isTranscriptIncomplete)

  const pending = draftHandoff.pendingImport
  const canApplyImportNow = Boolean(
    pending
    && isDraftDestinationReady
    && runtime.ready
    && (matchingPreservedDraft === null || pending.replaceDirty || !draftHandoff.isDraftDirty(matchingPreservedDraft))
    && canApplyPendingImport(pending, draftAttackId, draftConversationId)
    && pending.sequence >= draftHandoff.activeSequence
    && (!isActiveDraftDirty || pending.replaceDirty),
  )

  useEffect(() => {
    if (!pending) {
      importInFlightRef.current = null
      importFailedRef.current = null
    } else if (importFailedRef.current && importFailedRef.current !== pending.id) {
      importFailedRef.current = null
    }
    if (pending && pending.sequence < draftHandoff.activeSequence) {
      draftHandoff.dismissPendingImport(pending.id)
      return
    }
    if (!composerIdentityMatches || !runtime.ready || !isDraftDestinationReady || !inputBoxRef.current) return
    const preserved = draftHandoff.getPreservedDraft(draftAttackId, draftConversationId)
    const restoringPreserved = Boolean(matchingPreservedDraft && preserved
      && handoffRestoredRef.current !== preserved.sequence
      && !canApplyImportNow)
    let draft: ChatDraftContent
    let focus = false
    if (restoringPreserved && preserved) {
      if (isActiveDraftDirty) {
        draftHandoff.clearPreservedDraft(preserved.sequence, draftAttackId, draftConversationId)
        return
      }
      const consumed = draftHandoff.consumePreservedDraft(draftAttackId, draftConversationId, preserved.sequence)
      if (!consumed) return
      handoffRestoredRef.current = preserved.sequence
      draft = { ...consumed, systemPrompt: consumed.systemPrompt ?? '' }
      if (consumed.conversation) {
        let editorTarget: TargetInstance | null = consumed.conversation.target === null ? null : activeTarget
        if (consumed.conversation.target) {
          const resolved = resolveTargetReference(consumed.conversation.target, availableTargets)
          if (resolved) {
            editorTarget = resolved
          } else {
            editorTarget = null
            queueMicrotask(() => {
              setEditorError("The conversation's configured target is no longer available in the registry. Select an available target.")
            })
          }
        }
        editor.begin({
          messages: consumed.conversation.messages,
          objective: consumed.conversation.objective ?? currentObjective,
          initialObjective: consumed.conversation.initialObjective ?? currentObjective,
          sourceConversationId: consumed.conversation.sourceConversationId ?? null,
          sourceAttackId: consumed.conversation.sourceAttackId ?? draftAttackId,
          target: editorTarget,
          labels,
          pendingPromptId: consumed.conversation.pendingPromptId,
          sourcePieceIds: consumed.conversation.sourcePieceIds,
          unsavedImport: consumed.conversation.unsavedImport,
        })
      }
    } else {
      if (!canApplyImportNow || !pending) return
      if (importInFlightRef.current === pending.id || importFailedRef.current === pending.id) return

      const executeImportHandoff = async (): Promise<void> => {
        const snapshotGeneration = runtime.generation
        const snapshotPendingId = pending.id
        const snapshotTargetAttackId = draftAttackId
        const snapshotTargetConversationId = draftConversationId
        const snapshotDestinationType = pending.destinationType
        const snapshotSequence = pending.sequence
        const snapshotComposerRevision = composerRevisionRef.current
        const snapshotConverterEditRevision = convertersRef.current.editRevision
        const snapshotTextMetadata = stableMetadataString(inputBoxRef.current?.getTextMetadata())
        const snapshotReplaceDirty = pending.replaceDirty === true

        importInFlightRef.current = snapshotPendingId
        let existingTurns: ConversationDraftMessage[] = []
        let backendMsgs: BackendMessage[] = []
        if (pending.conversation && pending.destinationType === 'current_conversation' && draftConversationId && draftAttackId) {
          backendMsgs = loadedBackendMessagesRef.current.get(draftConversationId) ?? []
          if (backendMsgs.length === 0) {
            try {
              const res = await attacksApi.getMessages(draftAttackId, draftConversationId)
              backendMsgs = res?.messages ?? []
            } catch (err) {
              importInFlightRef.current = null
              if (!importMountedRef.current || pendingImportRef.current?.id !== snapshotPendingId
                || currentAttackIdRef.current !== snapshotTargetAttackId
                || currentConversationIdRef.current !== snapshotTargetConversationId
                || launchStateRef.current.generation !== snapshotGeneration) return
              importFailedRef.current = snapshotPendingId
              setEditorError(toApiError(err).detail || 'Failed to load conversation history. Preserved pending import.')
              return
            }
          }
        }

        // Validate state before proceeding to consume the import
        const currentPending = pendingImportRef.current
        if (!importMountedRef.current || launchStateRef.current.generation !== snapshotGeneration
          || !launchStateRef.current.ready || !currentPending || currentPending.id !== snapshotPendingId) {
          importInFlightRef.current = null
          return
        }
        if (currentPending.sequence !== snapshotSequence) {
          importInFlightRef.current = null
          return
        }
        if (currentPending.destinationType !== snapshotDestinationType) {
          importInFlightRef.current = null
          return
        }
        if (
          currentAttackIdRef.current !== snapshotTargetAttackId ||
          currentConversationIdRef.current !== snapshotTargetConversationId
        ) {
          importInFlightRef.current = null
          return
        }
        if (
          composerRevisionRef.current !== snapshotComposerRevision ||
          convertersRef.current.editRevision !== snapshotConverterEditRevision ||
          stableMetadataString(inputBoxRef.current?.getTextMetadata()) !== snapshotTextMetadata
        ) {
          importInFlightRef.current = null
          return
        }
        if (isActiveDraftDirtyRef.current && !snapshotReplaceDirty) {
          importInFlightRef.current = null
          return
        }
        if (
          !canApplyPendingImport(currentPending, currentAttackIdRef.current, currentConversationIdRef.current) ||
          currentPending.sequence < activeSequenceRef.current
        ) {
          importInFlightRef.current = null
          return
        }
        if (importInFlightRef.current !== snapshotPendingId || importFailedRef.current === snapshotPendingId) {
          return
        }

        if (pending.conversation && pending.destinationType === 'current_conversation' && draftConversationId && draftAttackId) {
          if (backendMsgs.length > 0) loadedBackendMessagesRef.current.set(draftConversationId, backendMsgs)
          if (backendMsgs.some((message: BackendMessage) => message.role === 'assistant' || message.role === 'tool'
            || message.message_pieces.some((piece: BackendMessagePiece) => piece.prompt_metadata?.pyrit_request_trace
              || piece.prompt_metadata?.pyrit_target_request))) {
            importInFlightRef.current = null
            importFailedRef.current = snapshotPendingId
            setEditorError('This conversation contains sent evidence. Choose Import to new conversation or Import to new attack instead.')
            return
          }
          try {
            existingTurns = toConversationDraft(backendMsgs)
          } catch (err) {
            importInFlightRef.current = null
            importFailedRef.current = snapshotPendingId
            setEditorError(toApiError(err).detail || 'Failed to parse conversation history. Preserved pending import.')
            return
          }
        }

        const setup = pending.setup ?? matchingPreservedDraft
        const sourcePieceIds = existingTurns.flatMap((message: ConversationDraftMessage) => message.pieces
          .flatMap((piece: ConversationDraftPiece) => piece.source_piece_id ? [piece.source_piece_id] : []))
        if (pending.conversation && !existingTurns.some((message: ConversationDraftMessage) =>
          message.role === 'system' || message.role === 'developer') && (setup?.systemPrompt || systemPrompt)) {
          existingTurns.unshift({ id: generateClientId(), role: 'system',
            pieces: [{ ...newDraftPiece(), original_value: setup?.systemPrompt || systemPrompt }] })
        }
        const importedMessages = pending.conversation ? mergeImportedHistory(existingTurns, pending.conversation.messages,
          importSystemChoice?.id === pending.id ? importSystemChoice.choice : undefined) : []
        if (!importedMessages) {
          importInFlightRef.current = null
          setImportSystemConflict(pending.id)
          return
        }
        const consumed = draftHandoff.consumePendingImport(
          snapshotPendingId,
          currentAttackIdRef.current,
          currentConversationIdRef.current,
        )
        importInFlightRef.current = null
        if (!consumed) return
        importFailedRef.current = null
        setEditorError(null)
        setImportSystemConflict(null)
        const importedObjective = consumed.objective ?? consumed.conversation?.objective
        const composerDraft = consumed.conversation?.composerDraft
          ?? (consumed.content.text || consumed.content.attachments?.length ? consumed.content : undefined)
        let pendingPromptId: string | undefined
        if (consumed.conversation) {
          const allMessages = importedMessages
          if (composerDraft) {
            pendingPromptId = generateClientId()
            allMessages.push({
              id: pendingPromptId, role: 'user',
              pieces: [
                ...(composerDraft.text ? [{ ...newDraftPiece(), original_value: composerDraft.text,
                  prompt_metadata: composerDraft.textMetadata }] : []),
                ...(composerDraft.attachments ?? []).map(attachmentDraft),
              ],
            })
          }
          const isNewAttack = !currentAttackIdRef.current
          const initialObjective = isNewAttack
            ? (importedObjective ?? setup?.objective ?? currentObjective)
            : currentObjective
          const objective = importedObjective ?? setup?.objective ?? currentObjective

          editor.begin({
            messages: allMessages,
            objective,
            initialObjective,
            sourceConversationId: currentConversationIdRef.current ?? null,
            sourceAttackId: currentAttackIdRef.current,
            sourcePieceIds,
            target: activeTarget,
            unsavedImport: true,
            labels,
            pendingPromptId,
          })
        }
        if (preserved && (matchingPreservedDraft || (consumed.setup && consumed.replaceDirty))) {
          draftHandoff.clearPreservedDraft(preserved.sequence, draftAttackId, draftConversationId)
        }
        draft = {
          ...setup,
          text: consumed.conversation ? '' : consumed.content.text,
          attachments: consumed.conversation ? [] : consumed.content.attachments ?? [],
          textMetadata: consumed.conversation ? undefined : consumed.content.textMetadata,
          objective: currentAttackIdRef.current ? undefined : importedObjective ?? setup?.objective,
        }
        focus = consumed.focus === true
        const attachments = restoreComposerDraft(draft)
        restoreConversions(draft.text, attachments, {}, draft.converters?.pipelines ?? {})
        if (focus) inputBoxRef.current?.focus()
      }
      void executeImportHandoff()
      return
    }
    // Claiming a handoff synchronizes the imperative input and its parent-owned draft atomically.
    const attachments = restoreComposerDraft(draft)
    if (restoringPreserved) {
      if (draft.converters?.generation === runtime.generation) {
        restoreConverterSnapshot(draft.converters, draft.text, attachments)
      } else {
        restoreConversions(draft.text, attachments, {}, Object.fromEntries(
          Object.entries(draft.converters?.pipelines ?? {}).map(([pieceType, stages]: [string, ConverterPipelineStage[]]) => [
            pieceType, stages.map((stage: ConverterPipelineStage) => ({ id: stage.id, converterId: stage.converterId })),
          ]),
        ))
      }
    }
    if (focus) inputBoxRef.current.focus()
  }, [
    canApplyImportNow, pending, draftHandoff, draftAttackId, draftConversationId, converters,
    isDraftDestinationReady, composerIdentityMatches, matchingPreservedDraft, isActiveDraftDirty, runtime.ready, runtime.generation,
    restoreComposerDraft, restoreConverterSnapshot, restoreConversions,
    activeTarget, currentObjective, editor, labels, setEditorNotice, systemPrompt, availableTargets, importSystemChoice,
  ])

  // Handle conversation selection from the panel
  // For a different ID the useEffect handles loading; for same ID force a refresh
  const handlePanelSelectConversation = useCallback(async (convId: string): Promise<void> => {
    const editing = editorRef.current
    const previousId = getViewedConversationId()
    if (editing && !await editing.finishEditing()) return
    if (editing && convId === previousId) return
    if (convId !== activeConversationId) requestForcedLoad()
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
      || (requireDefaults && (!current.defaultsReady || !current.scorerSelectionReady))) {
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
    const finishedConversationId = operation.conversationId
    setScoringConversations((previous) => {
      const next = new Set(previous)
      next.delete(finishedConversationId)
      return next
    })
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
    } : recovery
    if (isLatestRead) {
      loadedUserPieceIdsRef.current.set(effectiveConvId, userPieceIds(response.messages))
      if (response.messages?.messages) {
        loadedBackendMessagesRef.current.set(effectiveConvId, response.messages.messages)
      }
      setRecoverableSends((currentRecoveries) => {
        const next = { ...currentRecoveries }
        if (recoveredDraft) { next[effectiveConvId] = recoveredDraft } else { delete next[effectiveConvId] }
        return next
      })
    }
    if (isLatestRead && isViewingSend(operation)) {
      invalidateConversationLoads(effectiveConvId)
      setMessages(backendMessages)
      setConversationScoring(conversationScoringOf(effectiveConvId, response.messages))
      markConversationLoaded(effectiveConvId)
      onAttackChange?.(response.attack)
      const scoringError = operation.progress?.scoring_error
      const result = response.messages.scoring_failure
      if (scoringError && result && result.conversation_id === effectiveConvId) {
        setScoringNotice({
          id: operation.submissionId, conversationId: effectiveConvId,
          message: 'A scoring error occurred. Open the score above for details.',
        })
      }
      const lastMessage = response.messages.messages[response.messages.messages.length - 1]
      if (
        status === 'sent' && operation.progress?.state === 'completed'
        && latestSendRef.current === operation.submissionId
        && !operation.repeatGroup && operation.converterSnapshot && operation.attackResultId
        && typeof targetResponseStatus?.request_turn_number === 'number'
        && targetResponseStatus.request_turn_number === operation.progress.request_turn_number
        && lastMessage?.role === 'assistant' && lastMessage.message_pieces.length > 0
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
    const read = (): Promise<MessageSendStatus> => operation.observation
      ? operation.observation.readProgress(operation.needsRefresh)
      : attacksApi.getMessageSend(sendAttackId, previousProgress.send_id, operation.controller.signal)
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
        const scoringConversationId = operation.conversationId
        const isScoring = progress?.is_scoring === true
        setScoringConversations((previous) => {
          const next = new Set(previous)
          if (isScoring) next.add(scoringConversationId)
          else next.delete(scoringConversationId)
          return next
        })
      }
      operation.responseReadId = claimConversationRead(operation.conversationId)
      const [attack, conversation] = await Promise.all([
        operation.observation?.readAttack(operation.needsRefresh) ?? attacksApi.getAttack(operation.attackResultId),
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
      if (outcome.status !== 'retryable_failure' && operation.preservedDraft) {
        const preserved = operation.preservedDraft
        draftHandoff.clearPreservedDraft(
          preserved.sequence, preserved.originatingAttackId, preserved.originatingConversationId,
        )
        operation.preservedDraft = undefined
      }
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
      lastTextRef.current = ''
      lastAttachmentsSignatureRef.current = ''
      inputBoxRef.current.restoreDraft('', [])
      setChatInputText('')
      setDraftAttachments([])
      converters.clearAll()
      draftHandoff.clearPreservedDraft(undefined, draftAttackId, draftConversationId)
    }
    retireSend(operation)
  }

  const restorePreparationDraft = (): void => {
    const operation = sendIssue?.draft
    if (!operation || !isCurrentSend(operation) || !isViewingSend(operation)) return
    const sameGeneration = operation.converterGeneration === runtime.generation
    const attachments = operation.attachments.map(withDraftIdentity)
    lastTextRef.current = operation.originalValue
    lastAttachmentsSignatureRef.current = computeAttachmentSignature(attachments)
    setChatInputText(operation.originalValue)
    setDraftAttachments(attachments)
    inputBoxRef.current?.restoreDraft(operation.originalValue, attachments, operation.textMetadata)
    restoreConversions(
      operation.originalValue, attachments, sameGeneration ? operation.conversions : {},
      sameGeneration ? operation.pipelines : undefined,
    )
    if (!sameGeneration) setSendIssue(operation.conversationId, {
      ...sendIssue,
      description: 'Prompt restored after the runtime changed. Review converters before sending.',
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
      || (!attackResultId && (!defaultsReady || !scorerSelectionReady))
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
      generationDraft: { ...generationDraft },
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
      target: targetReference(activeTarget),
      chatScorerExplicit: draftScorer !== undefined,
      chatScorerChoice: attackResultId ? chatScorer : draftScorer === undefined ? defaultChatScorer : draftScorer,
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
      const generationConfig = parseGenerationConfig(operation.generationDraft, activeTarget)
      // Build message pieces from text + attachments — always use original text
      const pieceIds = buildDraftPieceIds(originalValue, attachments, conversions)
      const originalPieces = await buildMessagePieces(originalValue, attachments, operation.textMetadata)
      if (textConversion && !originalValue.trim()) {
        originalPieces.unshift({ data_type: 'text', original_value: originalValue })
      }
      const pieces = withRequestGenerationConfig(applyConvertedValues(
        originalPieces,
        pieceIds,
        conversions,
      ), generationConfig)
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
        const selectedTarget = activeTarget
        checkSendPreparation(operation, true)
        const createRequest: CreateAttackRequest = {
          target_registry_name: activeTarget.target_registry_name,
          ...(selectedTarget.binding ? { target_binding: selectedTarget.binding } : {}),
          objective: pendingObjective || undefined,
          // TODO(PyRIT 1.4): Pass only dedicated attribution after legacy label aliases are removed.
          // The create-attack API normalizes these aliases through _AttackAttributionInput.
          labels,
          operation_id: operationId ?? undefined,
          system_prompt: supportsSystemPrompt ? systemPrompt.trim() || undefined : undefined,
          ...(draftScorer !== undefined ? { chat_scorer: draftScorer }
            : defaultChatScorer === null || pendingObjective.trim() && defaultChatScorer
              ? { chat_scorer: defaultChatScorer } : {}),
        }
        const createResponse = await attacksApi.createAttack(createRequest)
        operation.chatScorerChoice = createRequest.chat_scorer
        if (!isCurrentSend(operation)) { return { status: 'non_retryable_failure', clearDraft: false } }
        currentAttackResultId = createResponse.attack_result_id
        setCreatedGenerationAttackId(createResponse.attack_result_id)
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
          retainedComposerIdentityRef.current = JSON.stringify([currentAttackResultId, currentConversationId])
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
      checkSendPreparation(operation, false)
      submissionAttempted = true
      const acceptance = attacksApi.submitMessageSend(currentAttackResultId, addMessageRequest)
      operation.observation = observeChatSend(
        currentAttackResultId, acceptance, operation.target,
        operation.chatScorerChoice, operation.chatScorerExplicit, availableScorers,
      )
      operation.progress = await acceptance
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
          const lastFailedMessage = failedMessages[failedMessages.length - 1]
          if (failedMessages.length > 0 && lastFailedMessage?.isLoading) {
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

  const restoreRecoverableDraft = useCallback((): void => {
    if (!recoverableSend) { return }
    const attachments = recoverableSend.attachments.map(withDraftIdentity)
    lastTextRef.current = recoverableSend.originalValue
    lastAttachmentsSignatureRef.current = computeAttachmentSignature(attachments)
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

      retainedComposerIdentityRef.current = JSON.stringify([attackResultId, response.conversation_id])
      onSelectConversation(response.conversation_id)
      setIsPanelOpen(!isNarrowScreen)
      restoreRecoverableDraft()
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
    if (isTranscriptIncomplete) return
    if (activeTarget?.capabilities?.supports_multi_turn === false && messages.some((message: Message) => message.role === 'user')) return
    const msg = messages[messageIndex]
    if (!msg) { return }
    copyMessageToInput(msg)
  }, [activeTarget, copyMessageToInput, messages, isTranscriptIncomplete])

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
    if (executionReadOnly) return
    if (editDraft !== null) {
      setEditorObjective(newObjective)
      return
    }
    const revision = navigationRevisionRef.current
    await changeObjective(newObjective, expectedObjective)
    if (navigationRevisionRef.current !== revision) return
    invalidateScoringSelection(JSON.stringify([attackResultId, newObjective, selectedScorer?.identifier_hash]))
    if (
      attackResultId && viewedConversationId && !isSendInFlight(viewedConversationId)
      && getViewedConversationId() === viewedConversationId
    ) {
      await loadConversation(attackResultId, viewedConversationId)
    }
  }, [
    attackResultId, changeObjective, editDraft, setEditorObjective, executionReadOnly,
    viewedConversationId, getViewedConversationId, isSendInFlight, loadConversation, invalidateScoringSelection, selectedScorer,
  ])

  const handleScorerUpdate = async (reference: RegisteredComponentReference | null): Promise<void> => {
    if (!attackResultId) { setDraftScorer(reference); return }
    if (executionReadOnly || isScoreLocked || isSending) return
    const revision = navigationRevisionRef.current
    const updated = await attacksApi.updateAttack(attackResultId, { chat_scorer: reference })
    if (navigationRevisionRef.current !== revision) return
    invalidateScoringSelection(JSON.stringify([attackResultId, currentObjective, reference?.identifier_hash]))
    onAttackChange?.(updated)
    if (viewedConversationId && getViewedConversationId() === viewedConversationId) {
      await loadConversation(attackResultId, viewedConversationId)
    }
  }

  const handleApplyScorer = async (): Promise<void> => {
    const conversationId = viewedConversationId
    const responseId = viewedScoring?.latestResponseId
    const scorer = selectedScorer
    const objective = currentObjective
    if (!attackResultId || !conversationId || !scorer || !responseId || applyingScorer.current) return
    const revision = navigationRevisionRef.current
    const scoringRevision = scoringRevisionRef.current
    applyingScorer.current = true
    setApplyingConversationId(conversationId)
    setApplyStatus(null)
    let status: ApplyScorerStatus
    try {
      const result = await attacksApi.applyScorer(attackResultId, {
        conversation_id: conversationId, response_id: responseId, scorer, objective,
      })
      if (!chatScorerInherited) rememberUse({}, resolveChatScorerReference(scorer, availableScorers))
      status = {
        intent: 'info',
        message: result.reused
          ? 'Already scored. Kept the existing valid result. No new evaluation.'
          : 'Latest response scored. Earlier responses and other conversations are unchanged.',
      }
    } catch (cause: unknown) {
      status = { intent: 'error', message: toApiError(cause).detail }
    }
    try {
      if (navigationRevisionRef.current !== revision || scoringRevisionRef.current !== scoringRevision) return
      setApplyStatus({ ...status, conversationId, responseId, scorerHash: scorer.identifier_hash, objective })
      if (getViewedConversationId() !== conversationId) return
      const [updated] = await Promise.all([
        attacksApi.getAttack(attackResultId),
        loadConversation(attackResultId, conversationId),
      ])
      if (navigationRevisionRef.current === revision && scoringRevisionRef.current === scoringRevision) onAttackChange?.(updated)
    } catch (cause: unknown) {
      console.error('Failed to refresh the attack after applying the scorer:', cause)
    } finally {
      applyingScorer.current = false
      setApplyingConversationId(null)
    }
  }

  const beginEdit = async (
    target: TargetInstance | null = activeTarget,
  ): Promise<void> => {
    if (executionReadOnly || isTranscriptIncomplete || editDraft || isSending || isLoadingEdit || isLoadingMessages || isLoadingAttack || awaitingConversationLoad) return
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
            ? [{
              ...newDraftPiece(), draftId: 'text', original_value: chatInputText,
              prompt_metadata: inputBoxRef.current?.getTextMetadata(),
            }] : []),
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

  const handleEditorSaved = (response: AddMessageResponse): void => {
    editor.discard()
    setEditorNotice('Conversation saved.')
    setMessages(handleConversationLoaded(
      response.messages.conversation_id, response.messages, backendMessagesToFrontend(response.messages.messages),
    ))
    markConversationLoaded(response.messages.conversation_id)
    retainedComposerIdentityRef.current = JSON.stringify([response.attack.attack_result_id, response.messages.conversation_id])
    if (response.attack.attack_result_id === attackResultId) {
      onSelectConversation(response.messages.conversation_id)
    } else {
      onConversationCreated(response.attack.attack_result_id, response.messages.conversation_id, response.attack.objective, editorTarget)
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
    lastTextRef.current = text
    lastAttachmentsSignatureRef.current = computeAttachmentSignature(attachments)
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
    const snapshot = editorRef.current?.getConverters() ?? converters.snapshot()
    let pendingPrompt: ReturnType<typeof draftMessageToComposer> | undefined
    let history: ConversationDraftMessage[]
    if (editDraft.unsavedImport && editDraft.pendingPromptId !== editDraft.messages[editDraft.messages.length - 1]?.id) {
      pendingPrompt = undefined
      history = editDraft.messages
    } else {
      const lastMessage = editDraft.messages[editDraft.messages.length - 1]
      pendingPrompt = lastMessage?.role === 'user'
        ? draftMessageToComposer(lastMessage, snapshot) : undefined
      history = pendingPrompt ? editDraft.messages.slice(0, -1) : editDraft.messages
    }
    return editor.save(destination, (response: AddMessageResponse) => {
      handleEditorSaved(response)
      restoreEditorPrompt(pendingPrompt, snapshot)
    }, history, (reuseUnsentConversation || editDraft.unsavedImport === true) && destination === 'same_attack'
      && Boolean(editDraft.sourceConversationId && editDraft.sourceAttackId === attackResultId))
  }

  const handleExitEditor = (): Promise<boolean> => {
    if (exitSaveRef.current) {
      return exitSaveRef.current
    }
    if (!editor.dirty) {
      const snapshot = editorRef.current?.getConverters() ?? converters.snapshot()
      let prompt: ReturnType<typeof draftMessageToComposer> | undefined
      if (editDraft?.unsavedImport) {
        prompt = undefined
      } else {
        const lastMessage = editDraft?.messages[editDraft.messages.length - 1]
        prompt = (chatInputText || draftAttachments.length || activePieceConversions.text) && lastMessage?.role === 'user'
          ? draftMessageToComposer(lastMessage, snapshot) : undefined
      }
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

  const promptRestoreDisabledReason = (snapshot?: ChatConverterSnapshot): string | undefined => {
    if (converters.isConverting) return 'Wait for the current conversion to finish before restoring a sent prompt.'
    if (chatInputText || draftAttachments.length || Object.entries(converters.workingInputs).some(
      ([id, value]: [string, string]) => value !== converters.inputs.find((input: ConverterInputPiece) => input.id === id)?.value,
    ) || Object.keys(converters.applied).length) {
      return 'Clear the current prompt and conversion edits before restoring a sent prompt.'
    }
    const { pipelineChanged } = preparePromptRestore(snapshot, converters.snapshot())
    if (snapshot && Object.keys(snapshot.errors).length > 0 && !pipelineChanged) {
      return 'The previous conversion was incomplete. Restore is unavailable for this prompt.'
    }
    return undefined
  }

  const firstPromptSend = undoableSend?.attackResultId === attackResultId
    && undoableSend.conversationId === viewedConversationId
    && undoableSend.converters.generation === runtime.generation
    && undoableSend.history.every((message: Pick<BackendMessage, 'role'>) => message.role === 'system' || message.role === 'developer')
    ? undoableSend : undefined

  const { copyConversation, branchDisabledReason: branchConversationDisabledReason } = useConversationCopy({
    attackResultId,
    viewedConversationId,
    messages,
    activeTarget,
    targetResolutionStatus,
    objective,
    labels,
    defaultChatScorer,
    newAttackContext,
    runtimeGeneration: runtime.generation,
    runtimeReady: runtime.ready,
    defaultsReady,
    scorerSelectionReady,
    executionReadOnly,
    isMutationLocked,
    isBusy: isSending || isLoadingEdit || isLoadingMessages || awaitingConversationLoad || isTranscriptIncomplete,
    recoverableSend,
    firstPromptSend,
    converters,
    promptRestoreDisabledReason,
    getViewedConversationId,
    getViewedAttackId: () => viewedAttackRef.current,
    getNavigationRevision: () => navigationRevisionRef.current,
    getDraftRevision: () => inputBoxRef.current?.getDraftRevision(),
    getLaunchState: () => launchStateRef.current,
    getConverters: () => convertersRef.current,
    setBusy: setIsLoadingEdit,
    setEditorError,
    setEditorNotice,
    recoverProcessingError: handleRecoverProcessingError,
    restoreRecoverableDraft,
    restoreEditorPrompt,
    clearUndoableSend: () => { setUndoableSend(null) },
    showConversation: (response: ConversationMessagesResponse) => {
      setMessages(handleConversationLoaded(
        response.conversation_id, response, backendMessagesToFrontend(response.messages),
      ))
      markConversationLoaded(response.conversation_id)
    },
    refreshPanel: () => { setPanelRefreshKey((key: number) => key + 1) },
    openPanel: () => { setIsPanelOpen(true) },
    navigate: (path: string) => { void navigate(path) },
    onSelectConversation,
    onConversationCreated,
    onAttackChange,
    onRestoreDestination: (attackId: string, conversationId: string) => {
      retainedComposerIdentityRef.current = JSON.stringify([attackId, conversationId])
    },
  })

  const sameAttackDisabledReason = !attackResultId ? 'No saved attack exists yet.'
    : executionReadOnly ? 'Automated results are read-only. Choose Continue manually.'
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
    : !scorerSelectionReady ? 'The remembered chat scorer is loading. Wait for it to load.'
    : undefined
  const editorDataTypes = draftDataTypes(editDraft?.messages ?? [])
  const unboundHistoryError = targetResolutionStatus === 'unbound' && activeTarget
    ? historyReplayDisabledReason(activeTarget, messages) : undefined
  const repeatDisabledReason = converters.repeatDisabledReason
    ?? (activeTarget ? historyReplayDisabledReason(activeTarget, messages) : undefined)
  const undoHistory = undoableSend?.attackResultId === attackResultId
    && undoableSend.conversationId === viewedConversationId ? undoableSend.history : messages
  const showUndoLastSend = !activeTarget || !historyReplayDisabledReason(activeTarget, undoHistory)

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
    : promptRestoreDisabledReason(undoableSend.converters)

  const handleUndoLastSend = async (): Promise<void> => {
    if (undoDisabledReason || !undoableSend || undoInFlight.current) return
    const saved = undoableSend
    const revision = inputBoxRef.current?.getDraftRevision()
    const navigationRevision = navigationRevisionRef.current
    const converterSnapshot = converters.snapshot()
    const { snapshot: restoredConverters } = preparePromptRestore(saved.converters, converterSnapshot)
    const draftUnchanged = (): boolean => viewedAttackRef.current === saved.attackResultId
      && getViewedConversationId() === saved.conversationId
      && navigationRevisionRef.current === navigationRevision
      && inputBoxRef.current?.getDraftRevision() === revision
      && launchStateRef.current.generation === saved.converters.generation && launchStateRef.current.ready
      && !convertersRef.current.isConverting
      && JSON.stringify(convertersRef.current.snapshot()) === JSON.stringify(converterSnapshot)
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
      if (!draftUnchanged()) {
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
      if (!draftUnchanged()) {
        setEditorNotice(`Undo created conversation ${branch.conversation_id} without replacing your current draft.`)
        return
      }
      setMessages(handleConversationLoaded(branch.conversation_id, history, backendMessagesToFrontend(history.messages)))
      markConversationLoaded(branch.conversation_id)
      retainedComposerIdentityRef.current = JSON.stringify([saved.attackResultId, branch.conversation_id])
      onSelectConversation(branch.conversation_id)
      onAttackChange?.(attack)
      restoreEditorPrompt({
        text: saved.originalValue, attachments: saved.attachments, textMetadata: saved.textMetadata,
        converters: restoredConverters,
      }, restoredConverters)
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
  const singleTurnDisabledReason = singleTurnLimitReached ? 'This target only supports single-turn conversations.' : undefined
  const hasProcessingError = Boolean(recoverableSend)
  const inputDisabledReasons = [
    ...(singleTurnDisabledReason ? [singleTurnDisabledReason] : []),
    ...(incompleteTranscriptReason ? [incompleteTranscriptReason] : []),
    ...(executionReadOnly ? ['Automated results are read-only. Choose Continue manually.'] : []),
    ...(isUndoingSend ? ['The previous send is being restored. Wait for undo to finish.'] : []),
    ...(!runtime.ready ? ['The runtime is not ready. Wait for initialization to finish.'] : []),
    ...(isSending ? ['A message is being sent. Wait for the response.'] : []),
    ...(isApplyingScorer ? ['The latest response is being scored. Wait for scoring to finish.'] : []),
    ...(isLoadingAttack ? ['The attack is loading. Wait for it to finish.'] : []),
    ...(editDraft !== null ? ['Finish editing the conversation before sending.'] : []),
    ...(isOperatorLocked ? ['This attack belongs to another operator.'] : []),
    ...(isCrossTargetLocked ? ['This attack uses a different target.'] : []),
    ...(isTargetResolutionLocked ? ['The target used by this attack could not be resolved.'] : []),
    ...(isEvidenceUnavailable ? ['This finding evidence is unavailable. Sending is disabled.'] : []),
    ...(hasProcessingError
      ? ['This conversation contains a target error. Use Copy or Branch on the failed prompt to branch into a new conversation or attack.']
      : []),
  ]
  const generationError = generationConfigError(generationDraft, activeTarget)
  const sendDisabledReasons = [
    ...(generationError ? [generationError] : []),
    ...(!activeTarget ? ['Select a target to send a message.'] : []),
    ...(!attackResultId && !defaultsReady
      ? [newActivityBlockedReason ?? 'Default labels are not ready. Wait for them to load.'] : []),
    ...(!attackResultId && !scorerSelectionReady
      ? ['The remembered chat scorer is loading. Wait for it to load.'] : []),
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
    blocked: isSending || Boolean(isLoadingAttack) || isLoadingMessages || awaitingConversationLoad || isTranscriptIncomplete,
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
                setGenerationDraft({})
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
        <div className={styles.editActions}>
          <Button appearance="subtle" className={styles.ribbonAction} icon={<EditRegular />}
            disabled={executionReadOnly || editDraft !== null || isSending || isLoadingEdit || isLoadingAttack || isLoadingMessages || awaitingConversationLoad}
            onClick={() => { void beginEdit() }}
          >{isLoadingEdit ? 'Loading editor...' : 'Edit Conversation'}</Button>
          {editDraft !== null && <Button appearance="subtle" className={styles.ribbonAction} icon={<ArrowShuffleRegular />}
            disabled={isSavingEditor} onClick={() => editorRef.current?.convertConversation()}>Convert Conversation</Button>}
        </div>
      </div>
      <div className={mergeClasses(styles.ribbonActions, styles.sharedActions)}
        role="group" aria-label="Conversation actions">
        {scenarioResultId && (
          <Link
            className={styles.breadcrumbLink}
            to={scenarioRunRoutePath(scenarioResultId)}
            state={location.state ?? { fromScenarioHistory: true }}
            onClick={(event: React.MouseEvent<HTMLAnchorElement>) => {
              if (onReturnToScan && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
                event.preventDefault()
                onReturnToScan()
              }
            }}
          >
            Back to scan
          </Link>
        )}
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
            <Tooltip content={incompleteTranscriptReason ?? 'Export conversation'} relationship="description">
              <Button
                appearance="subtle"
                className={styles.ribbonAction}
                icon={isExporting ? <Spinner size="tiny" /> : <ArrowDownloadRegular />}
                disabled={!canExportConversation}
                aria-label="Export conversation"
                aria-describedby={messagePagination && isTranscriptIncomplete ? transcriptDisabledReasonId : undefined}
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
        {editDraft !== null && <Tooltip
          content={editor.targetError ?? editor.validationError ?? newAttackDisabledReason ?? 'Save to new attack'}
          relationship="description">
          <Button
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => { editorRef.current?.saveToNewAttack() }}
            disabled={isSavingEditor || Boolean(newAttackDisabledReason || editor.targetError || editor.validationError)}
            data-testid="new-attack-btn"
            aria-label="Save to new attack"
            className={styles.newAttackButton}
          >
            <span className={styles.newAttackLabel}>Save to new attack</span>
          </Button>
        </Tooltip>}
      </div>
    </div>
  )

  const viewedApplyStatus = applyStatus && applyStatus.conversationId === viewedConversationId
    && applyStatus.responseId === viewedScoring?.latestResponseId
    && applyStatus.scorerHash === selectedScorer?.identifier_hash && applyStatus.objective === currentObjective
    ? applyStatus : null

  return (
    <div className={styles.root}>
      <h2 className={styles.pageHeading}>Chat</h2>
      {incompleteTranscriptReason && <span id={transcriptDisabledReasonId} hidden>{incompleteTranscriptReason}</span>}
      {isConverterPanelOpen && (
        <ConverterPanel
          onClose={() => setIsConverterPanelOpen(false)}
          controller={converters}
        />
      )}
      <FileDropZone ref={chatAreaRef} className={mergeClasses(styles.chatArea, executionReadOnly && styles.savedChatArea)} testId="chat-area"
        disabled={inputDisabledReasons.length > 0}
        disabledReason={inputDisabledReasons.join(' ')}
        onFiles={(files: File[]) => inputBoxRef.current?.addFiles(files)}>
        {toolbarContainer ? createPortal(toolbar, toolbarContainer) : toolbar}
        {executionReadOnly && <MessageBar intent="info">
          <MessageBarBody>
            This automated result is read-only. Continue in a separate manual attack to keep the original execution unchanged.
          </MessageBarBody>
          <MessageBarActions><Button className={styles.ribbonAction}
            disabled={isLoadingEdit || isLoadingAttack || isLoadingMessages || awaitingConversationLoad
              || isTranscriptIncomplete || Boolean(newAttackDisabledReason)}
            aria-describedby={messagePagination && isTranscriptIncomplete ? transcriptDisabledReasonId : undefined}
            onClick={() => { void copyConversation(recoverableSend?.failedRequestMessageIndex ?? messages.length - 1, 'new_attack') }}>
            {isLoadingEdit ? 'Creating continuation...' : 'Continue manually'}
          </Button></MessageBarActions>
        </MessageBar>}
        {manualContinuation && <MessageBar intent="info"><MessageBarBody>
          Manual continuation of{' '}
          <Link to={attackConversationRoutePath(
            manualContinuation.source_attack_result_id, manualContinuation.source_conversation_id,
          )}>
            the original automated result
          </Link>.
        </MessageBarBody></MessageBar>}
        <ObjectiveHeader
          key={`${attackResultId ?? 'new'}-${viewedConversationId ?? 'new'}-${editDraft === null ? 'saved' : 'draft'}`}
          objective={editDraft === null ? currentObjective : editorObjective}
          draftMode={editDraft !== null}
          outcome={humanScore ? outcome : viewedScoring?.outcome ?? (scoringInvalidated ? 'undetermined' : outcome)}
          automatedScore={viewedScoring ? viewedScoring.score : scoringInvalidated ? null : automatedScore}
          chatScorer={selectedScorer}
          chatScorerInherited={Boolean(attackResultId) && chatScorerInherited}
          scoringFailure={editDraft !== null ? null : viewedScoring?.failure ?? null}
          scorers={availableScorers}
          scorersLoading={scorersLoading}
          scorersError={scorersError}
          canUpdateScorer={!executionReadOnly && editDraft === null && !isScoreLocked && !isSending && !isApplyingScorer && runtime.ready}
          onUpdateScorer={handleScorerUpdate}
          canApplyScorer={!executionReadOnly && editDraft === null && !isScoreLocked && !isSending && !isApplyingScorer && runtime.ready
            && Boolean(attackResultId && selectedScorer && currentObjective.trim() && viewedScoring?.latestResponseId)}
          onApplyScorer={attackResultId ? handleApplyScorer : undefined}
          applyingScorer={applyingConversationId !== null && applyingConversationId === viewedConversationId}
          applyStatus={viewedApplyStatus}
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
            !executionReadOnly
            && !isSavingEditor
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
        {scoringConversations.has(viewedConversationId ?? '__pending__') && <Text role="status">Scoring response…</Text>}
        {scoringNotice && scoringNotice.conversationId === viewedConversationId && <MessageBar intent="warning" role="status">
          <MessageBarBody>{scoringNotice.message}</MessageBarBody>
        </MessageBar>}
        {editorError && <MessageBar intent="error"><MessageBarBody>{editorError}</MessageBarBody></MessageBar>}
        {pending && importSystemConflict === pending.id && <MessageBar intent="warning">
          <MessageBarBody>
            The destination and imported example have different system or developer instructions. Choose which setup to keep.
            <Button onClick={() => setImportSystemChoice({ id: pending.id, choice: 'destination' })}>Keep destination setup</Button>
            <Button onClick={() => setImportSystemChoice({ id: pending.id, choice: 'imported' })}>Use imported setup</Button>
            <Button onClick={() => draftHandoff.dismissPendingImport(pending.id)}>Cancel import</Button>
          </MessageBarBody>
        </MessageBar>}
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
          onDiscard={(navigatingAway) => {
            if (navigatingAway) {
              discardDraftOnUnmountRef.current = true
              draftHandoff.clearPreservedDraft(undefined, draftAttackId, draftConversationId)
            } else {
              discardDraftOnUnmountRef.current = false
            }
            if (editDraft?.unsavedImport) {
              lastTextRef.current = ''
              lastAttachmentsSignatureRef.current = ''
              setChatInputText('')
              setDraftAttachments([])
              inputBoxRef.current?.restoreDraft('', [])
              draftHandoff.clearPreservedDraft(undefined, draftAttackId, draftConversationId)
            }
          }}
          saveDestination={attackResultId ? 'same_attack' : 'new_attack'}
          saveLabel="Save"
          blockNavigation={!editDraft?.unsavedImport}
        />}
        {editDraft === null && conversationLoadIssue?.conversationId === viewedConversationId && (
          <MessageBar intent={conversationLoadIssue.unavailable ? 'warning' : 'error'}>
            <MessageBarBody>{conversationLoadIssue.detail}{' '}
              {(!conversationLoadIssue.unavailable || !findingEvidenceId) && attackResultId && viewedConversationId && (
                <Button className={styles.ribbonAction} disabled={isLoadingMessages || isLoadingMoreMessages}
                  onClick={() => { void loadConversation(attackResultId, viewedConversationId) }}>
                  Retry conversation load
                </Button>
              )}
            </MessageBarBody>
          </MessageBar>
        )}
        {editDraft === null && <MessageList
          messages={isEvidenceUnavailable ? [] : messages}
          showGenerationInfo={activeTarget?.capabilities?.supports_temperature !== false}
          onCopyToInput={handleCopyToInput}
          copyToInputDisabledReason={incompleteTranscriptReason ?? singleTurnDisabledReason}
          onCopyToNewConversation={(index: number) => { void copyConversation(index, 'same_attack') }}
          onCopyToNewAttack={newAttackDisabledReason ? undefined : (index: number) => { void copyConversation(index, 'new_attack') }}
          onUseInTechnique={newAttackDisabledReason ? undefined : (index: number) => { void copyConversation(index, 'technique') }}
          copyConversationDisabled={isSending || isLoadingEdit || isRecoveringProcessingError || Boolean(sendIssue?.blocking)}
          copyConversationDisabledReason={incompleteTranscriptReason}
          newConversationDisabledReason={isMutationLocked ? "This attack is read-only. Copy to a new attack instead." : undefined}
          getBranchConversationDisabledReason={branchConversationDisabledReason}
          getBranchConversationDescription={(index: number) => activeTarget && canBranchFirstPrompt(activeTarget, messages, index)
            ? 'Restore this prompt as an unsent draft in a new conversation in the same attack. The response is not copied.'
            : undefined}
          isLoading={isLoadingAttack || isLoadingMessages || awaitingConversationLoad}
          autoScroll={!executionReadOnly}
          savedEvidence={executionReadOnly}
          globalMarkdown={globalMarkdown}
          onEditConversation={executionReadOnly ? undefined : () => { void beginEdit() }}
        />}
        {editDraft === null && executionReadOnly && messagePagination && !isEvidenceUnavailable && (
          <section className={styles.savedTranscriptControls} aria-label="Saved transcript paging">
            {messageContinuationIssue && (
              <MessageBar intent="error"><MessageBarBody>
                {messageContinuationIssue.detail} Loaded evidence is still shown.
              </MessageBarBody></MessageBar>
            )}
            <div className={styles.savedTranscriptActions}>
              <Text ref={transcriptStatusRef} tabIndex={-1} id={transcriptStatusId} role="status" aria-live="polite" size={200}>
                {isLoadingMessages || awaitingConversationLoad ? 'Loading saved transcript... '
                  : isLoadingMoreMessages ? 'Loading more message pieces... ' : ''}
                {isTranscriptIncomplete
                  ? `${loadedPieceSummary} Transcript incomplete.`
                  : `End of saved transcript. ${loadedPieceSummary}`}
              </Text>
              {messagePagination.has_more && (
                <Button className={styles.ribbonAction} disabled={isLoadingMoreMessages || isLoadingMessages || awaitingConversationLoad}
                  icon={isLoadingMoreMessages ? <Spinner size="tiny" /> : undefined}
                  onClick={(event: MouseEvent<HTMLButtonElement>) => {
                    if (event.currentTarget === document.activeElement) transcriptStatusRef.current?.focus({ preventScroll: true })
                    if (messageContinuationIssue?.restartRequired && attackResultId && viewedConversationId) {
                      void loadConversation(attackResultId, viewedConversationId)
                    } else { void loadMoreMessages() }
                  }}>
                  {isLoadingMoreMessages ? 'Loading more message pieces...'
                    : messageContinuationIssue?.restartRequired ? 'Restart transcript loading'
                    : messageContinuationIssue ? 'Retry more message pieces' : 'Load more message pieces'}
                </Button>
              )}
            </div>
          </section>
        )}
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
        <ChatInputArea
          fileDropEnabled={false}
          ref={inputBoxRef}
          generationDraft={generationDraft}
          onGenerationDraftChange={setGenerationDraft}
          undoLastSend={showUndoLastSend ? {
            disabledReason: undoDisabledReason,
            isUndoing: isUndoingSend,
            onUndo: () => { void handleUndoLastSend() },
          } : undefined}
          initialText={chatInputText}
          initialAttachments={draftAttachments}
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
          disabled={inputDisabledReasons.length > 0}
          activeTarget={activeTarget}
          operatorLocked={isOperatorLocked}
          crossTargetLocked={isCrossTargetLocked}
          targetResolutionStatus={targetResolutionStatus}
          onRetryTargetResolution={onRetryTargetResolution}
          onUseAsTemplate={handleUseAsTemplate}
          attackOperator={isOperatorLocked ? attackOperator ?? undefined : undefined}
          onConfigureTarget={() => onNavigate?.('registry')}
          onToggleConverterPanel={() => setIsConverterPanelOpen(prev => !prev)}
          isConverterPanelOpen={isConverterPanelOpen}
          onInputChange={handleComposerInputChange}
          onAttachmentsChange={handleComposerAttachmentsChange}
          convertedValue={activePieceConversions['text']?.convertedDataType === 'text' ? (activePieceConversions['text']?.convertedValue ?? null) : null}
          originalValue={activePieceConversions['text']?.originalValue ?? null}
          onClearConversion={() => converters.clear('text')}
          onClearAllConversions={converters.clearAll}
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
      </FileDropZone>
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
          referencePageSize={executionReadOnly ? SAVED_REFERENCE_PAGE_SIZE : undefined}
          activeConversationId={activeConversationId}
          onSelectConversation={handlePanelSelectConversation}
          onNewConversation={handleNewConversation}
          onChangeMainConversation={handleChangeMainConversation}
          onClose={() => setIsPanelOpen(false)}
          lockedReason={
            executionReadOnly ? 'Automated results are read-only. Choose Continue manually.'
            : !activeTarget ? 'Configure a target to enable this action.'
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

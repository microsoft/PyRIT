import { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo, useId } from 'react'
import type { ChangeEvent, MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  Button,
  Input,
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
import HelpField from '@/components/HelpField'
import ObjectiveHeader from './ObjectiveHeader'
import type { ApplyScorerStatus, RegisteredComponentReference, ScoringFailure, ManualContinuation, ScorerInstance } from '@/types'
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
import { useChatSend, useChatSendState } from '@/hooks/useChatSend'
import { PENDING_SEND_CONVERSATION_ID } from '@/utils/chatSendState'
import type { RepeatSendView } from '@/utils/chatSendState'
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
import { resolveTargetReference, targetReference } from '@/utils/targetIdentity'
import { mergeImportedHistory } from '@/utils/seedImport'
import { resolveChatScorerReference } from '@/utils/scorerPresentation'
import { useChatConverters } from '@/hooks/useChatConverters'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useRuntime } from '@/hooks/useRuntime'
import { useUserPreferences } from '@/hooks/useUserPreferences'
import { NARROW_SCREEN_QUERY } from '@/styles/touchTargets'
import {
  basenameFromValue,
  buildMediaUrl,
  dataTypeToAttachmentKind,
  isPathDataType,
  withDraftIdentity,
} from './converterTypes'
import type { ChatInputAreaHandle } from './ChatInputArea'
import { attacksApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import { backendMessagesToFrontend } from '../../utils/messageMapper'
import type {
  AddMessageResponse,
  AttackOutcome,
  AttackSummary,
  AttackTargetResolutionStatus,
  BackendMessage,
  BackendMessagePiece,
  BackendScore,
  ChatConverterSnapshot,
  ChatDraftContent,
  ChatDraftContextState,
  ConversationMessagesResponse,
  ConversationDraftMessage,
  ConversationDraftPiece,
  ConverterInputPiece,
  ConverterPipelineStage,
  CreateConversationRequest,
  Message,
  MessageAttachment,
  NewAttackContext,
  TargetInstance,
  TargetInfo,
  TargetReference,
} from '../../types'
import { isTargetResolutionBlocking, targetInfoMatchesTarget } from '../../utils/targetIdentity'
import { attackConversationRoutePath, scenarioRunRoutePath } from '../../utils/routeParams'
import type { ViewName } from '../Sidebar/Navigation'
import { CHAT_NOTICE_FADE_DURATION_MS, useChatWindowStyles } from './ChatWindow.styles'

const CHAT_NOTICE_VISIBLE_DURATION_MS = 5_000
const SAVED_MESSAGE_PAGE_SIZE = 100
const SAVED_REFERENCE_PAGE_SIZE = 50
const INCOMPLETE_TRANSCRIPT_REASON = 'Load the complete saved transcript before continuing, copying, exporting, or assessing this result.'

interface ChatInfoNotice {
  readonly id: string
  readonly message: string
  readonly fading: boolean
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
  const [temperature, setTemperature] = useState('')
  const [temperatureExplanationId, setTemperatureExplanationId] = useState<string | null>(null)
  const [temperatureAttackId, setTemperatureAttackId] = useState(attackResultId)
  if (temperatureAttackId !== attackResultId) {
    setTemperatureAttackId(attackResultId)
    setTemperature('')
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
  const [editorUploadError, setEditorUploadError] = useState<string>()
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
  const [isRecoveringProcessingError, setIsRecoveringProcessingError] = useState(false)
  const [panelRefreshKey, setPanelRefreshKey] = useState(0)
  const inputBoxRef = useRef<ChatInputAreaHandle>(null)
  const recoveryInFlightRef = useRef(false)
  useEffect(() => {
    if (editDraft && (editDraft.sourceConversationId !== viewedConversationId || editDraft.sourceAttackId !== attackResultId)) {
      discardEditor()
    }
  }, [editDraft, viewedConversationId, attackResultId, discardEditor])
  const sendState = useChatSendState({ activeConversationId, viewedConversationId, runtimeGeneration: runtime.generation })
  const {
    isSending, recoverableSend, isSendInFlight, reconcileLoadedConversation, getPendingSend,
    getLoadedBackendMessages, rememberBackendMessages, clearUndoableSend, clearRecoveries,
  } = sendState
  const undoableSend = sendState.state.undoable
  const handleConversationLoaded = useCallback((
    convId: string, response: ConversationMessagesResponse, frontendMessages: Message[],
  ): Message[] => {
    setConversationScoring(conversationScoringOf(convId, response))
    return reconcileLoadedConversation(convId, response, frontendMessages)
  }, [reconcileLoadedConversation])
  const conversationLoad = useConversationMessages({
    attackResultId,
    conversationId,
    activeConversationId,
    findingEvidenceId,
    messagePageSize: executionReadOnly ? SAVED_MESSAGE_PAGE_SIZE : undefined,
    sendingConversations: sendState.state.sending,
    isSendInFlight,
    onConversationLoaded: handleConversationLoaded,
  })
  const {
    messages,
    setMessages,
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
    getViewedConversationId,
  } = conversationLoad
  const incompleteTranscriptReason = isTranscriptIncomplete ? INCOMPLETE_TRANSCRIPT_REASON : undefined
  const loadedPieceSummary = `${loadedPieceCount} message ${loadedPieceCount === 1 ? 'piece' : 'pieces'} loaded.`
  const viewedAttackRef = useRef(attackResultId)
  const navigationRevisionRef = useRef(0)

  useLayoutEffect(() => {
    viewedAttackRef.current = attackResultId
    navigationRevisionRef.current += 1
  }, [attackResultId, activeConversationId, conversationId])

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
    temperature,
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
        temperature: editDraft.temperature,
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
      setTemperature('')
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
      const operation = getPendingSend(composerIdentity.conversationId ?? PENDING_SEND_CONVERSATION_ID)
      if (operation && inputBoxRef.current?.getDraftRevision() === operation.draftRevision) {
        operation.preservedDraft = draftHandoff.getPreservedDraft(
          composerIdentity.attackId, composerIdentity.conversationId,
        ) ?? undefined
      }
    }
    activateComposerIdentity(reset)
  }, [
    composerIdentityMatches, composerIdentity, draftAttackId, draftConversationId, draftHandoff,
    activateComposerIdentity, getPendingSend,
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
    const hasEditedContent = isActiveDraftDirty || Boolean(pendingObjective.trim() || temperature) || draftScorer !== undefined
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
    if (draft.temperature !== undefined) setTemperature(draft.temperature)
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
      clearRecoveries()
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
          temperature: consumed.conversation.temperature ?? '',
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
          backendMsgs = getLoadedBackendMessages(draftConversationId) ?? []
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
          if (backendMsgs.length > 0) rememberBackendMessages(draftConversationId, backendMsgs)
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
            temperature: setup?.temperature ?? '',
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
    getLoadedBackendMessages, rememberBackendMessages,
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

  const unboundHistoryError = targetResolutionStatus === 'unbound' && activeTarget
    ? historyReplayDisabledReason(activeTarget, messages) : undefined
  const {
    sendIssue,
    repeatSends,
    send: handleSend,
    refreshSend,
    refreshRepeatSend,
    dismissRepeatSend,
    restorePreparationDraft,
  } = useChatSend({
    tracking: sendState,
    route: {
      attackResultId,
      conversationId,
      activeConversationId,
      viewedConversationId,
      getViewedAttackId: () => viewedAttackRef.current,
      setViewedAttackId: (attackId: string) => { viewedAttackRef.current = attackId },
      getNavigationRevision: () => navigationRevisionRef.current,
    },
    readiness: {
      runtimeReady: runtime.ready,
      runtimeGeneration: runtime.generation,
      defaultsReady,
      scorerSelectionReady,
      isSendBlocked: editDraft !== null || Boolean(isLoadingAttack) || isLoadingMessages || awaitingConversationLoad
        || isMutationLocked || isUndoingSend || Boolean(unboundHistoryError),
      getLaunchState: () => launchStateRef.current,
    },
    request: {
      activeTarget,
      targetResolutionStatus,
      newAttack: {
        objective: pendingObjective, temperature, systemPrompt, supportsSystemPrompt, labels, operationId,
        draftScorer, defaultChatScorer,
      },
      chatScorer,
      availableScorers,
    },
    composer: {
      converters,
      getDraftRevision: () => inputBoxRef.current?.getDraftRevision(),
      getTextMetadata: () => inputBoxRef.current?.getTextMetadata(),
      clearSentDraft: (draftRevision: number | undefined) => {
        const inputBox = inputBoxRef.current
        if (!inputBox || inputBox.getDraftRevision() !== draftRevision) return
        lastTextRef.current = ''
        lastAttachmentsSignatureRef.current = ''
        inputBox.restoreDraft('', [])
        setChatInputText('')
        setDraftAttachments([])
        converters.clearAll()
        draftHandoff.clearPreservedDraft(undefined, draftAttackId, draftConversationId)
      },
      restoreSendDraft: (text, attachments, textMetadata, conversions, pipelines) => {
        lastTextRef.current = text
        lastAttachmentsSignatureRef.current = computeAttachmentSignature(attachments)
        setChatInputText(text)
        setDraftAttachments(attachments)
        inputBoxRef.current?.restoreDraft(text, attachments, textMetadata)
        restoreConversions(text, attachments, conversions, pipelines)
      },
      clearPreservedDraft: draftHandoff.clearPreservedDraft,
    },
    loader: conversationLoad,
    callbacks: {
      observeChatSend,
      showConversationScoring: (convId: string, response: ConversationMessagesResponse) => {
        setConversationScoring(conversationScoringOf(convId, response))
      },
      showScoringNotice: setScoringNotice,
      setEditorNotice,
      revealRepeatConversations: () => { if (!isNarrowScreen) setIsPanelOpen(true) },
      refreshPanel: () => { setPanelRefreshKey((key: number) => key + 1) },
      onConversationCreated,
      onCreatedDestination: (attackId: string, convId: string) => {
        retainedComposerIdentityRef.current = JSON.stringify([attackId, convId])
      },
      onAttackChange,
    },
  })

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

  const handleEditorSaved = (response: AddMessageResponse, savedTarget?: TargetInstance | null): void => {
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
    return editor.save(destination, (response: AddMessageResponse, target?: TargetInstance | null) => {
      handleEditorSaved(response, target)
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
    clearUndoableSend,
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
    : !scorerSelectionReady ? 'The remembered chat scorer is loading. Wait for it to load.'
    : undefined
  const editorDataTypes = draftDataTypes(editDraft?.messages ?? [])
  const temperatureTarget = editDraft !== null ? editorTarget : activeTarget
  const temperatureReadOnly = editDraft === null && Boolean(attackResultId) && targetResolutionStatus !== 'unbound'
  const temperatureExplanation = temperatureTarget && !temperatureTarget.supports_temperature_override
    ? temperatureTarget.reconstruction_error ?? 'This target does not support a separate temperature setting.'
    : temperatureReadOnly ? 'Temperature can only be modified in a new attack' : ''
  const temperatureExplanationContext = attackResultId ?? temperatureTarget?.identifier.hash
  const repeatDisabledReason = converters.repeatDisabledReason
    ?? (activeTarget ? historyReplayDisabledReason(activeTarget, messages) : undefined)
  const undoHistory = undoableSend?.attackResultId === attackResultId
    && undoableSend.conversationId === viewedConversationId ? undoableSend.history : messages
  const showUndoLastSend = !activeTarget || !historyReplayDisabledReason(activeTarget, undoHistory)

  const undoDisabledReason = !undoableSend
    ? 'Undo is available after a single successful send from this tab, with its converters still in memory.'
    : !runtime.ready || undoableSend.converters.generation !== runtime.generation
      ? 'The runtime changed. The previous converter state is no longer available.'
    : isUndoingSend || sendState.state.sending.size > 0 || converters.isConverting
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
        clearUndoableSend()
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
      clearUndoableSend()
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
      if (branchAttempted) clearUndoableSend()
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
  const sendDisabledReasons = [
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
          <HelpField label="Temperature:" orientation="horizontal" size="small" className={styles.temperatureField}
            help="Controls variation in generated responses. Lower values are usually more consistent; higher values can produce more variation. Some targets do not support changes."
            hint={!temperatureReadOnly && temperatureTarget.supports_temperature_override && editDraft !== null
              ? 'Changes apply only to a new attack.' : undefined}>
            <Tooltip content={temperatureExplanation} relationship="description"
              visible={Boolean(temperatureExplanation) && temperatureExplanationId === temperatureExplanationContext}
              onVisibleChange={(_, data) => {
                setTemperatureExplanationId(data.visible ? temperatureExplanationContext ?? null : null)
              }}>
              <span
                // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- focusable so keyboard users can reach the tooltip explaining why the control is disabled
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
          </HelpField>
        )}
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
          content={editor.targetError ?? editor.validationError ?? newAttackDisabledReason ?? editorUploadError ?? 'Save to new attack'}
          relationship="description">
          <Button
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => { editorRef.current?.saveToNewAttack() }}
            disabled={isSavingEditor || Boolean(newAttackDisabledReason || editor.targetError || editor.validationError || editorUploadError)}
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
        {sendState.state.scoring.has(viewedConversationId ?? PENDING_SEND_CONVERSATION_ID) && <Text role="status">Scoring response…</Text>}
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
          onUploadErrorChange={setEditorUploadError}
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
        {repeatSends.map(
          (view: RepeatSendView) => (
            <MultiSendProgress
              key={view.progress.send_id}
              progress={view.progress}
              needsRefresh={view.needsRefresh}
              onSelectConversation={handlePanelSelectConversation}
              onDismiss={() => { dismissRepeatSend(view.progress.send_id) }}
              onRefresh={() => { refreshRepeatSend(view.progress.send_id) }}
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

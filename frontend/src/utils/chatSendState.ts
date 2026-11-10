import type {
  BackendMessage,
  ChatConverterSnapshot,
  ChatDraftContextState,
  ConversationMessagesResponse,
  ConverterPipelineStage,
  Message,
  MessageAttachment,
  MessageSendConversation,
  MessageSendObservation,
  MessageSendStatus,
  PieceConversion,
  RegisteredComponentReference,
  TargetReference,
} from '@/types'
import { backendMessageToOriginalDraft } from '@/utils/messageMapper'

/** Lock key for a first send whose attack and conversation are not created yet. */
export const PENDING_SEND_CONVERSATION_ID = '__pending__'

const RETRYABLE_TARGET_RESPONSE_ERROR = 'processing'

/** A failed prompt that can be restored into a new conversation without resending the failed turn. */
export interface RecoverableSendDraft {
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

/** One submitted send. Its identity is the lock owner for `conversationId`; fields mutate as the send advances. */
export interface PendingSend {
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

/** The last single send from this tab, kept while its converters can still be restored. */
export interface UndoableSend {
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

/** A repeated send whose conversations share one progress handle. */
export interface RepeatSendGroup {
  source: PendingSend
  progress: MessageSendStatus
  conversations: Map<string, PendingSend>
  read?: Promise<MessageSendStatus>
}

export interface RepeatSendView {
  progress: MessageSendStatus
  needsRefresh: boolean
}

export interface SendIssue {
  description: string
  blocking: boolean
  draft?: PendingSend
}

/** Rendered send state. The synchronous per-conversation locks live beside it in `useChatSendState`. */
export interface ChatSendState {
  /** Conversations with an in-flight send, so parallel conversations can send independently. */
  readonly sending: ReadonlySet<string>
  /** Conversations whose latest response is being scored by an in-flight send. */
  readonly scoring: ReadonlySet<string>
  readonly issues: Readonly<Record<string, SendIssue>>
  readonly recoveries: Readonly<Record<string, RecoverableSendDraft>>
  readonly repeatSends: readonly RepeatSendView[]
  readonly undoable: UndoableSend | null
}

export type ChatSendAction =
  /** A new submission owns the conversation: clear its previous issue, recovery and the tab's undo. */
  | { readonly type: 'send-started'; readonly conversationId: string }
  /** The first send created its attack; move its lock from the pending key to the created conversation. */
  | { readonly type: 'send-conversation-created'; readonly from: string; readonly to: string }
  /** A refresh or repeat sibling resumed tracking an accepted send. */
  | { readonly type: 'tracking-started'; readonly conversationId: string }
  | { readonly type: 'tracking-finished'; readonly conversationId: string }
  | { readonly type: 'scoring-observed'; readonly conversationId: string; readonly scoring: boolean }
  | { readonly type: 'issue-changed'; readonly conversationId: string; readonly issue?: SendIssue }
  /** A conversation load reported its persisted recovery, or the lack of one. */
  | { readonly type: 'recovery-loaded'; readonly conversationId: string; readonly recovery?: RecoverableSendDraft }
  /** A send's own response read replaces the conversation's recovery. */
  | { readonly type: 'recovery-replaced'; readonly conversationId: string; readonly recovery?: RecoverableSendDraft }
  | { readonly type: 'recoveries-cleared' }
  | { readonly type: 'repeat-started'; readonly progress: MessageSendStatus }
  | { readonly type: 'repeat-progressed'; readonly progress: MessageSendStatus; readonly needsRefresh: boolean }
  | { readonly type: 'repeat-dismissed'; readonly sendId: string }
  | { readonly type: 'undo-recorded'; readonly undoable: UndoableSend }
  | { readonly type: 'undo-cleared' }

export const INITIAL_CHAT_SEND_STATE: ChatSendState = {
  sending: new Set(),
  scoring: new Set(),
  issues: {},
  recoveries: {},
  repeatSends: [],
  undoable: null,
}

function withMember(set: ReadonlySet<string>, member: string): ReadonlySet<string> {
  return set.has(member) ? set : new Set(set).add(member)
}

function withoutMember(set: ReadonlySet<string>, member: string): ReadonlySet<string> {
  if (!set.has(member)) return set
  const next = new Set(set)
  next.delete(member)
  return next
}

function withEntry<T>(record: Readonly<Record<string, T>>, key: string, value: T | undefined): Readonly<Record<string, T>> {
  if (value === undefined) {
    if (!(key in record)) return record
    const next = { ...record }
    delete next[key]
    return next
  }
  return { ...record, [key]: value }
}

/** Keeps live draft content when a reload reports the same failed turn the send already recovered. */
export function mergeLoadedRecovery(
  current: RecoverableSendDraft | undefined,
  persisted: RecoverableSendDraft | undefined,
): RecoverableSendDraft | undefined {
  if (!persisted) return undefined
  if (
    current?.source === 'live'
    && current.failedRequestTurnNumber === persisted.failedRequestTurnNumber
    && current.failedResponseTurnNumber === persisted.failedResponseTurnNumber
  ) {
    return {
      ...current,
      failedRequestMessageIndex: persisted.failedRequestMessageIndex,
      historyCutoffIndex: persisted.historyCutoffIndex,
    }
  }
  return persisted
}

export function chatSendReducer(state: ChatSendState, action: ChatSendAction): ChatSendState {
  switch (action.type) {
    case 'send-started':
      return {
        ...state,
        sending: withMember(state.sending, action.conversationId),
        issues: withEntry(state.issues, action.conversationId, undefined),
        recoveries: withEntry(state.recoveries, action.conversationId, undefined),
        undoable: null,
      }
    case 'send-conversation-created': {
      const sending = new Set(state.sending)
      sending.delete(action.from)
      sending.add(action.to)
      return { ...state, sending }
    }
    case 'tracking-started': {
      const sending = withMember(state.sending, action.conversationId)
      return sending === state.sending ? state : { ...state, sending }
    }
    case 'tracking-finished': {
      const sending = withoutMember(state.sending, action.conversationId)
      const scoring = withoutMember(state.scoring, action.conversationId)
      return sending === state.sending && scoring === state.scoring ? state : { ...state, sending, scoring }
    }
    case 'scoring-observed': {
      const scoring = action.scoring
        ? withMember(state.scoring, action.conversationId)
        : withoutMember(state.scoring, action.conversationId)
      return scoring === state.scoring ? state : { ...state, scoring }
    }
    case 'issue-changed': {
      const issues = withEntry(state.issues, action.conversationId, action.issue)
      return issues === state.issues ? state : { ...state, issues }
    }
    case 'recovery-loaded': {
      const recovery = mergeLoadedRecovery(state.recoveries[action.conversationId], action.recovery)
      const recoveries = withEntry(state.recoveries, action.conversationId, recovery)
      return recoveries === state.recoveries ? state : { ...state, recoveries }
    }
    case 'recovery-replaced': {
      const recoveries = withEntry(state.recoveries, action.conversationId, action.recovery)
      return recoveries === state.recoveries ? state : { ...state, recoveries }
    }
    case 'recoveries-cleared':
      return Object.keys(state.recoveries).length === 0 ? state : { ...state, recoveries: {} }
    case 'repeat-started':
      return {
        ...state,
        repeatSends: [
          ...state.repeatSends.filter((view: RepeatSendView) => !isSendFinished(view.progress)),
          { progress: action.progress, needsRefresh: false },
        ],
      }
    case 'repeat-progressed':
      return {
        ...state,
        repeatSends: state.repeatSends.map((view: RepeatSendView) => (
          view.progress.send_id === action.progress.send_id
            ? { progress: action.progress, needsRefresh: action.needsRefresh } : view
        )),
      }
    case 'repeat-dismissed':
      return {
        ...state,
        repeatSends: state.repeatSends.filter((view: RepeatSendView) => view.progress.send_id !== action.sendId),
      }
    case 'undo-recorded':
      return { ...state, undoable: action.undoable }
    case 'undo-cleared':
      return state.undoable === null ? state : { ...state, undoable: null }
  }
}

export function isSendFinished(progress: MessageSendConversation): boolean {
  return ['completed', 'failed', 'interrupted'].includes(progress.state)
}

export function userPieceIds(response: ConversationMessagesResponse): Set<string> {
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

export function getPersistedProcessingRecovery(
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

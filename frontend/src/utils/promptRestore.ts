import type { BackendMessage, ChatConverterSnapshot, ConversationMessagesResponse, ConverterPipelineStage } from '@/types'

/**
 * Picks the converter snapshot to restore with a sent prompt. A saved snapshot is reused only when the
 * current pipeline still matches it; otherwise the current pipeline is kept and its results are cleared.
 */
export function preparePromptRestore(
  saved: ChatConverterSnapshot | undefined, current: ChatConverterSnapshot,
): { snapshot: ChatConverterSnapshot; pipelineChanged: boolean } {
  const pipelineChanged = saved
    ? JSON.stringify(current.pipelines) !== JSON.stringify(saved.pipelines)
    : Object.values(current.pipelines).some((stages: ConverterPipelineStage[]) => stages.length > 0)
  return {
    pipelineChanged,
    snapshot: saved && !pipelineChanged ? saved : {
      ...current, workingInputs: {}, stageResults: {}, errors: {}, applied: {},
    },
  }
}

/** Identifies a conversation's persisted pieces so a stale restore can be detected. */
export function conversationPieceSignature(response: ConversationMessagesResponse): string {
  return JSON.stringify(response.messages.map((message: BackendMessage) => message.message_pieces.map((piece) => piece.id)))
}

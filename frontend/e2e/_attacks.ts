import type { AddMessageResponse, BackendMessage } from "@/types";

export function makeAddMessageResponse(
  attackResultId: string,
  conversationId: string,
  messages: BackendMessage[],
): AddMessageResponse {
  return {
    attack: {
      attack_result_id: attackResultId,
      conversation_id: conversationId,
      attack_type: "PromptSendingAttack",
      objective: "",
      outcome: "undetermined",
      converters: [],
      message_count: messages.length,
      related_conversation_ids: [],
      labels: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
    messages: { conversation_id: conversationId, messages },
  };
}

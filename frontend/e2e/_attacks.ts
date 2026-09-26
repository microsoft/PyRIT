import type { APIRequestContext, Route } from "@playwright/test";

import type { AddMessageResponse, BackendMessage, MessageSendRequest, MessageSendStatus, TargetResponseStatus } from "@/types";

export function makeAddMessageResponse(
  attackResultId: string,
  conversationId: string,
  messages: BackendMessage[],
  targetResponseStatus: TargetResponseStatus | null = null,
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
    messages: {
      conversation_id: conversationId,
      messages,
      target_response_status: targetResponseStatus,
    },
  };
}

/** Reuse a transcript fixture through the separate submission, progress and read endpoints. */
export async function fulfillMessageSend(route: Route, response: AddMessageResponse): Promise<void> {
  const page = route.request().frame().page();
  const request: MessageSendRequest = route.request().postDataJSON();
  const attackId = response.attack.attack_result_id;
  const conversationId = request.target_conversation_id;
  const attackPath = `/api/attacks/${encodeURIComponent(attackId)}`;
  const statusPath = `${attackPath}/message-sends/${encodeURIComponent(request.submission_id)}`;
  const processingError = response.messages.target_response_status?.response_error === "processing";
  const progress: MessageSendStatus = {
    send_id: request.submission_id,
    attack_result_id: attackId,
    conversation_id: conversationId,
    request_turn_number: response.messages.target_response_status?.request_turn_number ?? null,
    state: processingError ? "failed" : "completed",
    failure_stage: processingError ? "sending" : null,
    error: processingError ? "Target processing failed." : null,
  };
  await page.route((url: URL) => url.pathname === statusPath, async (read: Route) => {
    await read.fulfill({ json: progress });
  });
  await page.route((url: URL) => url.pathname === attackPath, async (read: Route) => {
    if (read.request().method() !== "GET") { await read.fallback(); return; }
    await read.fulfill({ json: response.attack });
  });
  await page.route((url: URL) => url.pathname === `${attackPath}/messages`
    && url.searchParams.get("conversation_id") === conversationId, async (read: Route) => {
    await read.fulfill({ json: { ...response.messages, conversation_id: conversationId } });
  });
  await route.fulfill({ status: 202, json: { ...progress, state: "queued", failure_stage: null, error: null } });
}

/** Observe a real accepted send without another submission, then read its persisted views. */
export async function readMessageSendResult(
  request: APIRequestContext, accepted: MessageSendStatus,
): Promise<AddMessageResponse> {
  let progress = accepted;
  const attackPath = `/api/attacks/${encodeURIComponent(accepted.attack_result_id)}`;
  while (!["completed", "failed", "interrupted"].includes(progress.state)) {
    const response = await request.get(
      `${attackPath}/message-sends/${encodeURIComponent(progress.send_id)}?wait_ms=1000`,
    );
    if (!response.ok()) { throw new Error(`Status read failed: ${response.status()}`); }
    progress = await response.json();
  }
  const [attack, messages] = await Promise.all([
    request.get(attackPath),
    request.get(`${attackPath}/messages?conversation_id=${encodeURIComponent(progress.conversation_id)}`),
  ]);
  if (!attack.ok() || !messages.ok()) { throw new Error("Could not read completed send evidence"); }
  return { attack: await attack.json(), messages: await messages.json() };
}

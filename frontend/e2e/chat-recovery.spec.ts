import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import {
  test as base,
  expect,
  type APIRequestContext,
  type Page,
  type Request,
  type Route,
} from "./_fixtures";
import { compatibilityHeaders } from "./_compatibility";
import type {
  AddMessageRequest,
  AddMessageResponse,
  ConversationMessagesResponse,
  CreateAttackResponse,
  CreateConversationResponse,
  MessageSendStatus,
  AttackConversationsResponse,
  TargetInstance,
} from "@/types";
import { readMessageSendResult } from "./_attacks";

interface LocalTarget {
  registryName: string;
  requestBodies: string[];
  setProcessingFailure: (enabled: boolean) => void;
  holdResponse: () => void;
  releaseResponse: () => void;
}

const test = base.extend<{ localTarget: LocalTarget; imageConverterId: string }>({
  imageConverterId: async ({ request }, runTest) => {
    const name = `recovery-image-${randomUUID()}`;
    const created = await request.post("/api/converters", {
      headers: compatibilityHeaders(),
      data: {
        name,
        type: "ImageRotationConverter",
        params: { angle: 90, output_format: "PNG" },
      },
    });
    expect(created.status()).toBe(201);
    try {
      await runTest(name);
    } finally {
      const deleted = await request.delete(`/api/converters/${encodeURIComponent(name)}`, {
        headers: compatibilityHeaders(),
      });
      expect(deleted.status()).toBe(204);
    }
  },
  localTarget: async ({ page, request }, runTest) => {
    const requestBodies: string[] = [];
    const errors: Error[] = [];
    const pageErrors: Error[] = [];
    page.on("pageerror", (error: Error) => { pageErrors.push(error); });
    let processingFailure = false;
    let holdResponse = false;
    let heldResponse: (() => void) | undefined;
    const server = createServer((incoming: IncomingMessage, response: ServerResponse) => {
      if (incoming.method !== "POST" || incoming.url !== "/v1/chat/completions") {
        errors.push(new Error(`Unexpected provider request: ${incoming.method} ${incoming.url}`));
        response.writeHead(404);
        response.end();
        incoming.resume();
        return;
      }
      const chunks: Buffer[] = [];
      incoming.on("data", (chunk: Buffer) => { chunks.push(chunk); });
      incoming.on("error", (error: Error) => {
        errors.push(error);
        response.destroy(error);
      });
      incoming.on("end", () => {
        requestBodies.push(Buffer.concat(chunks).toString("utf8"));
        response.setHeader("Content-Type", "application/json");
        // Invalid provider JSON exercises the real normalizer's persisted processing-error path.
        const reply = processingFailure ? '{"choices":' : JSON.stringify({
          id: `local-${requestBodies.length}`,
          object: "chat.completion",
          created: Math.floor(Date.now() / 1000),
          model: "local-recovery-test",
          choices: [{
            index: 0,
            finish_reason: "stop",
            message: { role: "assistant", content: "Local target response" },
          }],
          usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
        });
        const deliver = (): void => { response.end(reply); };
        if (holdResponse) { heldResponse = deliver; } else { deliver(); }
      });
    });
    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    try {
      const address = server.address();
      if (!address || typeof address === "string") {
        throw new Error("Expected a loopback provider port");
      }
      const created = await request.post("/api/targets", {
        headers: compatibilityHeaders(),
        data: {
          type: "OpenAIChatTarget",
          auth_mode: "api_key",
          params: {
            endpoint: `http://127.0.0.1:${address.port}/v1`,
            model_name: `recovery-test-${randomUUID()}`,
            api_key: "local-recovery-test-placeholder",
          },
        },
      });
      expect(created.ok()).toBeTruthy();
      const target: TargetInstance = await created.json();
      await runTest({
        registryName: target.target_registry_name,
        requestBodies,
        setProcessingFailure: (enabled: boolean): void => { processingFailure = enabled; },
        holdResponse: (): void => { holdResponse = true; },
        releaseResponse: (): void => {
          holdResponse = false;
          heldResponse?.();
          heldResponse = undefined;
        },
      });
      expect(errors).toEqual([]);
      expect(pageErrors).toEqual([]);
    } finally {
      await new Promise<void>((resolve, reject) => {
        server.close((error?: Error) => {
          if (error) reject(error);
          else resolve();
        });
        server.closeAllConnections();
      });
    }
  },
});

function isMessagePost(request: Request): boolean {
  return request.method() === "POST"
    && /\/api\/attacks\/[^/]+\/message-sends$/.test(new URL(request.url()).pathname);
}

async function sendFromComposer(page: Page, text?: string): Promise<AddMessageResponse> {
  if (text !== undefined) {
    await expect(page.getByTestId("chat-input")).toBeEnabled();
    await page.getByTestId("chat-input").fill(text);
  }
  const sendButton = page.getByRole("button", { name: "Send message", exact: true });
  await expect(sendButton).toBeEnabled();
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => isMessagePost(candidate.request())),
    sendButton.click(),
  ]);
  expect(response.status()).toBe(202);
  const accepted: MessageSendStatus = await response.json();
  return readMessageSendResult(page.request, accepted);
}

async function createConversation(request: APIRequestContext, attackId: string): Promise<string> {
  const response = await request.post(`/api/attacks/${attackId}/conversations`, {
    data: {}, headers: compatibilityHeaders(),
  });
  expect(response.status()).toBe(201);
  const created: CreateConversationResponse = await response.json();
  return created.conversation_id;
}

async function selectConversation(page: Page, conversationId: string): Promise<void> {
  if (!await page.getByTestId("conversation-panel").isVisible()) {
    await page.getByRole("button", { name: "Toggle conversations panel", exact: true }).click();
  }
  await page.getByRole("button", { name: `Select conversation ${conversationId}`, exact: true }).click();
}

async function installDeferredFileReads(page: Page): Promise<void> {
  await page.evaluate(() => {
    const NativeFileReader = window.FileReader;
    const pending: Array<(() => void) | undefined> = [];
    window.FileReader = class extends NativeFileReader {
      override readAsDataURL(blob: Blob): void {
        if (document.documentElement.dataset.deferRecoveryReads === "true") {
          pending.push(() => super.readAsDataURL(blob));
          document.documentElement.dataset.recoveryReadCount = String(pending.length);
          return;
        }
        super.readAsDataURL(blob);
      }
    };
    const release = (index: number): void => {
      const read = pending[index];
      pending[index] = undefined;
      read?.();
    };
    document.addEventListener("release-recovery-read", (event: Event) => {
      if (!(event instanceof CustomEvent)) return;
      if (event.detail === "all") {
        delete document.documentElement.dataset.deferRecoveryReads;
        for (let index = 0; index < pending.length; index++) release(index);
      } else if (typeof event.detail === "number") {
        release(event.detail);
      }
    });
  });
}

test.describe("Chat processing recovery @seeded", () => {
  test.setTimeout(90_000);

  test.beforeEach(async ({ page, localTarget }) => {
    await page.goto("/");
    await page.getByTitle("Registry", { exact: true }).click();
    await page.getByRole("combobox", { name: "Default objective target", exact: true })
      .selectOption(localTarget.registryName);
    await page.getByTitle("Chat", { exact: true }).click();
  });

  test("repeats and nests only the selected conversation through the real backend", async ({ page, request, localTarget }, testInfo) => {
    let submissions = 0;
    page.on("request", (outgoing: Request) => { if (isMessagePost(outgoing)) submissions += 1; });
    const initial = await sendFromComposer(page, "History shared by the repeats");
    const attackId = initial.attack.attack_result_id;
    let selectedId = initial.messages.conversation_id;
    const path = `/api/attacks/${encodeURIComponent(attackId)}`;
    const readConversation = async (conversationId: string): Promise<ConversationMessagesResponse> => {
      const response = await request.get(`${path}/messages?conversation_id=${encodeURIComponent(conversationId)}`, {
        headers: compatibilityHeaders(),
      });
      expect(response.ok()).toBeTruthy();
      return response.json();
    };
    let previousIds = [selectedId];
    for (const count of [5, 3]) {
      const before = new Map(await Promise.all(previousIds.map(async (id: string) => (
        [id, await readConversation(id)] as const
      ))));
      const source = before.get(selectedId);
      if (!source) throw new Error("Expected the selected history");
      await expect(page.getByRole("button", { name: "Repetitions: 1", exact: true })).toBeEnabled();
      await page.getByRole("button", { name: "Repetitions: 1", exact: true }).click();
      for (let index = 1; index < count; index++) {
        await page.getByRole("button", { name: "Increase repetitions", exact: true }).click();
      }
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("chat-input")).toBeEnabled();
      await page.getByTestId("chat-input").fill(`Repeat ${count}`);
      const [submitted] = await Promise.all([
        page.waitForResponse((response) => isMessagePost(response.request())),
        page.getByTestId("chat-input").press("Enter"),
      ]);
      expect(submitted.status(), await submitted.text()).toBe(202);
      const accepted: MessageSendStatus = await submitted.json();
      expect(accepted.count).toBe(count);
      expect(accepted.conversation_id).toBe(selectedId);
      await readMessageSendResult(request, accepted);
      const completedResponse = await request.get(`${path}/message-sends/${accepted.send_id}`, {
        headers: compatibilityHeaders(),
      });
      const completed: MessageSendStatus = await completedResponse.json();
      expect(completed.state).toBe("completed");
      expect(completed.conversations).toHaveLength(count);
      const repeatedIds = completed.conversations?.map((conversation) => conversation.conversation_id);
      if (!repeatedIds) throw new Error("Expected committed conversation IDs");
      expect(repeatedIds[0]).toBe(selectedId);
      const sourceOrigins = source.messages.flatMap((message) => message.message_pieces.map(
        (piece) => piece.original_prompt_id,
      ));
      for (const id of repeatedIds) {
        const conversation = await readConversation(id);
        expect(conversation.messages).toHaveLength(source.messages.length + 2);
        expect(conversation.messages.slice(0, -2).flatMap((message) => message.message_pieces.map(
          (piece) => piece.original_prompt_id,
        ))).toEqual(sourceOrigins);
        expect(conversation.messages.at(-2)?.message_pieces[0].original_value).toBe(`Repeat ${count}`);
        await expect(page.getByRole("button", { name: `Open conversation ${id}`, exact: true })).toBeVisible();
      }
      for (const id of previousIds.filter((candidate: string) => candidate !== selectedId)) {
        expect(await readConversation(id)).toEqual(before.get(id));
      }
      const listingResponse = await request.get(`${path}/conversations`, { headers: compatibilityHeaders() });
      const listing: AttackConversationsResponse = await listingResponse.json();
      expect(listing.conversations).toHaveLength(count === 5 ? 5 : 7);
      previousIds = listing.conversations.map((conversation) => conversation.conversation_id);
      selectedId = repeatedIds[1];
      await selectConversation(page, selectedId);
      await expect(page.getByTestId("chat-input")).toBeEnabled();
      await expect(page.getByRole("button", { name: "Repetitions: 1", exact: true })).toBeVisible();
    }
    expect(submissions).toBe(3);
    expect(localTarget.requestBodies).toHaveLength(9);
    await expect(page.getByText("Repeat 3", { exact: true })).toBeVisible();
    await expect(page.getByText("Loading conversation...", { exact: true })).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath("repeat-desktop.png"), animations: "disabled" });
    if (await page.getByTestId("conversation-panel").isVisible()) {
      await page.getByTestId("close-panel-btn").click();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Repetitions: 1", exact: true }).click();
    await expect(page.getByRole("button", { name: "Increase repetitions", exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("repeat-mobile.png"), animations: "disabled" });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });

  test("accepts before target completion and continues after leaving chat", async ({ page, request, localTarget }) => {
    localTarget.holdResponse();
    let submissions = 0;
    page.on("request", (request: Request) => { if (isMessagePost(request)) submissions += 1; });
    await page.getByTestId("chat-input").fill("Continue independently");
    const [acceptedResponse] = await Promise.all([
      page.waitForResponse((response) => isMessagePost(response.request())),
      page.getByRole("button", { name: "Send message", exact: true }).click(),
    ]);
    expect(acceptedResponse.status()).toBe(202);
    const accepted: MessageSendStatus = await acceptedResponse.json();
    expect(accepted.state).toBe("queued");
    // First use can initialize the target's HTTP client after the submission has already been accepted.
    await expect.poll(() => localTarget.requestBodies.length, { timeout: 30_000 }).toBe(1);
    const live = await request.get(`/api/attacks/${accepted.attack_result_id}/message-sends/${accepted.send_id}`, {
      headers: compatibilityHeaders(),
    });
    expect((await live.json()).state).toBe("sending");
    await expect(page).toHaveURL((url: URL) => url.pathname.includes(accepted.attack_result_id));
    const chatUrl = page.url();
    await page.getByTitle("Registry", { exact: true }).click();
    localTarget.releaseResponse();
    const result = await readMessageSendResult(request, accepted);
    expect(result.messages.messages.map((message) => message.role)).toEqual(["user", "assistant"]);
    await page.goto(chatUrl);
    await expect(page.getByTestId("message-list").getByText("Local target response", { exact: true })).toBeVisible();
    expect(localTarget.requestBodies).toHaveLength(1);
    expect(submissions).toBe(1);
  });

  test("refreshes a completed send after an attack-details read fails without a second submission", async ({
    page, localTarget,
  }) => {
    localTarget.holdResponse();
    let submissions = 0;
    page.on("request", (request: Request) => { if (isMessagePost(request)) submissions += 1; });
    await page.getByTestId("chat-input").fill("Keep this accepted draft");
    const [acceptedResponse] = await Promise.all([
      page.waitForResponse((response) => isMessagePost(response.request())),
      page.getByRole("button", { name: "Send message", exact: true }).click(),
    ]);
    const accepted: MessageSendStatus = await acceptedResponse.json();
    await expect.poll(() => localTarget.requestBodies.length).toBe(1);
    const path = new RegExp(`/api/attacks/${accepted.attack_result_id}$`);
    await page.route(path, async (route: Route) => {
      await route.fulfill({ status: 503, json: { detail: "Controlled metadata read failure" } });
    });
    localTarget.releaseResponse();
    await expect(page.getByText(/Saved messages or attack details could not be loaded/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
    await page.unroute(path);
    await page.getByRole("button", { name: "Refresh saved messages" }).click();
    await expect(page.getByTestId("message-list").getByText("Local target response", { exact: true })).toBeVisible();
    await expect(page.getByTestId("chat-input")).toHaveValue("");
    expect(submissions).toBe(1);
    expect(localTarget.requestBodies).toHaveLength(1);
  });

  test("keeps recovery on the submitted prompt without adding a user message", async ({ page, request, localTarget }) => {
    localTarget.setProcessingFailure(true);
    const failed = await sendFromComposer(page, "Explain the target error");
    expect(failed.messages.target_response_status?.response_error).toBe("processing");
    expect(failed.messages.messages.map((message) => message.role)).toEqual(["user", "assistant"]);
    const bubbles = page.getByTestId(/^message-bubble-/);
    await expect(bubbles).toHaveCount(2);
    await expect(bubbles.first()).toContainText("Explain the target error");
    await expect(bubbles.first().getByRole("button", { name: "Copy conversation", exact: true })).toBeVisible();
    await expect(bubbles.last()).toContainText("JSONDecodeError");
    await expect(bubbles.last().getByRole("button", { name: "Copy conversation", exact: true })).toHaveCount(0);
    await page.reload();
    await expect(bubbles).toHaveCount(2);
    await expect(bubbles.first()).toContainText("Explain the target error");
    const originalViewport = page.viewportSize();
    await page.setViewportSize({ width: 1280, height: 2800 });
    await test.info().attach("detailed-target-error", {
      body: await bubbles.last().screenshot({ animations: "disabled" }),
      contentType: "image/png",
    });
    if (originalViewport) {
      await page.setViewportSize(originalViewport);
    }
    const disabledPrompt = page.getByLabel("Why the prompt box is disabled");
    await disabledPrompt.click({ position: { x: 10, y: 10 } });
    await expect(page.getByText(/This conversation contains a target error/)).toBeVisible();
    await test.info().attach("disabled-prompt-reasons", {
      body: await page.screenshot({ animations: "disabled" }),
      contentType: "image/png",
    });
    await page.keyboard.press("Escape");
    await bubbles.first().getByRole("button", { name: "Copy conversation", exact: true }).click();
    await test.info().attach("original-prompt-copy", {
      body: await page.screenshot({ animations: "disabled" }),
      contentType: "image/png",
    });
    const attackId = failed.attack.attack_result_id;
    const [cloneResponse] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === `/api/attacks/${attackId}/conversations`),
      page.getByRole("menuitem", { name: "New conversation", exact: true }).click(),
    ]);
    expect(cloneResponse.status()).toBe(201);
    expect(cloneResponse.request().postDataJSON()).toEqual({});
    const cloned: CreateConversationResponse = await cloneResponse.json();
    await expect(page.getByTestId("chat-input")).toHaveValue("Explain the target error");
    await expect(page.getByTestId("chat-input")).toBeEnabled();
    await expect(bubbles).toHaveCount(0);
    const historyResponse = await request.get(
      `/api/attacks/${attackId}/messages?conversation_id=${cloned.conversation_id}`,
      { headers: compatibilityHeaders() },
    );
    expect(historyResponse.ok()).toBeTruthy();
    const history: ConversationMessagesResponse = await historyResponse.json();
    expect(history.messages).toHaveLength(0);
    expect(localTarget.requestBodies).toHaveLength(1);
  });

  for (const keepSafePrefix of [false, true]) {
    test(`recovers the latest failed draft without earlier errors, safe prefix ${keepSafePrefix}`, async ({
      page, request, localTarget,
    }) => {
      if (keepSafePrefix) {
        await sendFromComposer(page, "Earlier safe context");
      }
      localTarget.setProcessingFailure(true);
      const first = await sendFromComposer(page, "First failed draft");
      expect(first.messages.target_response_status?.response_error).toBe("processing");
      const attackId = first.attack.attack_result_id;
      const sourceId = first.attack.conversation_id;
      const later = await request.post(`/api/attacks/${attackId}/messages`, {
        headers: compatibilityHeaders(),
        data: {
          role: "user",
          pieces: [{ data_type: "text", original_value: "Latest failed draft" }],
          send: true,
          target_registry_name: localTarget.registryName,
          target_conversation_id: sourceId,
        },
      });
      expect(later.status()).toBe(200);
      const laterResponse: AddMessageResponse = await later.json();
      expect(laterResponse.messages.target_response_status?.response_error).toBe("processing");
      await page.reload();
      const errorPiece = laterResponse.messages.messages.flatMap((message) => message.message_pieces)
        .find((piece) => piece.response_error === "processing");
      expect(errorPiece?.converted_value).toBeTruthy();
      await expect(page.getByTestId("message-list")).toContainText(errorPiece?.converted_value ?? "");
      await expect(page.getByRole("button", { name: "Edit in clean conversation", exact: true })).toHaveCount(0);
      const disabledPrompt = page.getByLabel("Why the prompt box is disabled");
      await disabledPrompt.hover({ position: { x: 5, y: 5 } });
      await expect(page.getByText(/This conversation contains a target error/)).toBeVisible();
      await page.keyboard.press("Escape");
      await disabledPrompt.click({ position: { x: 10, y: 10 } });
      await expect(page.getByText(/This conversation contains a target error/)).toBeVisible();
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Copy conversation", exact: true }).last().click();
      const recover = page.getByRole("menuitem", { name: "New conversation", exact: true });
      await expect(recover).toBeEnabled();
      await test.info().attach("processing-recovery", {
        body: await page.screenshot(),
        contentType: "image/png",
      });
      const [cloneResponse] = await Promise.all([
        page.waitForResponse((response) => response.request().method() === "POST"
          && new URL(response.url()).pathname === `/api/attacks/${attackId}/conversations`),
        recover.click(),
      ]);
      expect(cloneResponse.status()).toBe(201);
      expect(cloneResponse.request().postDataJSON()).toEqual(keepSafePrefix
        ? { source_conversation_id: sourceId, cutoff_index: 1 }
        : {});
      const cloned: CreateConversationResponse = await cloneResponse.json();
      await expect(page.getByTestId("chat-input")).toHaveValue("Latest failed draft");
      await expect(page.getByTestId("chat-input")).toBeEnabled();
      await expect(recover).toHaveCount(0);
      const historyResponse = await request.get(
        `/api/attacks/${attackId}/messages?conversation_id=${cloned.conversation_id}`,
        { headers: compatibilityHeaders() },
      );
      expect(historyResponse.ok()).toBeTruthy();
      const history: ConversationMessagesResponse = await historyResponse.json();
      expect(history.messages).toHaveLength(keepSafePrefix ? 2 : 0);
      expect(history.target_response_status).toBeNull();
      if (keepSafePrefix) {
        expect(history.messages.map((message) => message.role)).toEqual(["user", "simulated_assistant"]);
      }
      expect(localTarget.requestBodies).toHaveLength(keepSafePrefix ? 3 : 2);

      localTarget.setProcessingFailure(false);
      const sent = await sendFromComposer(page);
      expect(sent.messages.target_response_status?.response_error).toBe("none");
      const targetContext = localTarget.requestBodies[localTarget.requestBodies.length - 1];
      expect(targetContext).toContain("Latest failed draft");
      expect(targetContext).not.toContain("First failed draft");
      expect(targetContext).not.toMatch(/Traceback|JSONDecodeError/);
      if (keepSafePrefix) expect(targetContext).toContain("Earlier safe context");
    });
  }

  for (const reload of [false, true]) {
    test(`restores the failed prompt in a new attack without adding it to history, reload ${reload}`, async ({
      page, request, localTarget,
    }) => {
      await sendFromComposer(page, "Safe context for the new attack");
      localTarget.setProcessingFailure(true);
      const failed = await sendFromComposer(page, "Edit this failed prompt");
      const sourceAttackId = failed.attack.attack_result_id;
      if (reload) await page.reload();
      await page.getByRole("button", { name: "Copy conversation", exact: true }).last().click();
      const [savedResponse] = await Promise.all([
        page.waitForResponse((response) => response.request().method() === "POST"
          && new URL(response.url()).pathname === "/api/attacks/save-conversation"),
        page.getByRole("menuitem", { name: "New attack", exact: true }).click(),
      ]);
      expect(savedResponse.status(), await savedResponse.text()).toBe(200);
      const saved: AddMessageResponse = await savedResponse.json();
      expect(saved.attack.attack_result_id).not.toBe(sourceAttackId);
      expect(savedResponse.request().postDataJSON().messages).toHaveLength(2);
      await expect(page).toHaveURL((url: URL) => url.pathname.includes(saved.attack.attack_result_id));
      await expect(page.getByTestId("chat-input")).toHaveValue("Edit this failed prompt");
      await expect(page.getByTestId("chat-input")).toBeEnabled();
      await expect(page.getByTestId(/^message-bubble-/)).toHaveCount(2);
      const historyResponse = await request.get(
        `/api/attacks/${saved.attack.attack_result_id}/messages?conversation_id=${saved.messages.conversation_id}`,
        { headers: compatibilityHeaders() },
      );
      expect(historyResponse.ok()).toBeTruthy();
      const history: ConversationMessagesResponse = await historyResponse.json();
      expect(history.messages.map((message) => message.role)).toEqual(["user", "simulated_assistant"]);
      expect(history.messages.flatMap((message) => message.message_pieces).some(
        (piece) => piece.original_value === "Edit this failed prompt" || piece.response_error === "processing",
      )).toBe(false);
      expect(localTarget.requestBodies).toHaveLength(2);
      localTarget.setProcessingFailure(false);
      const retried = await sendFromComposer(page, "Revised prompt");
      expect(retried.attack.attack_result_id).toBe(saved.attack.attack_result_id);
      expect(retried.messages.target_response_status?.response_error).toBe("none");
      const targetContext = localTarget.requestBodies.at(-1);
      expect(targetContext).toContain("Safe context for the new attack");
      expect(targetContext).toContain("Revised prompt");
      expect(targetContext).not.toContain("Edit this failed prompt");
      expect(targetContext).not.toMatch(/Traceback|JSONDecodeError/);
    });
  }

  test("warns about lost converter choices when recovering a saved failure to a new attack", async ({
    page, request, localTarget, imageConverterId,
  }) => {
    await page.getByTestId("file-input").setInputFiles({
      name: "evidence.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAIAAAAuKetIAAAAaElEQVR4nNXOQREAIAzAsFJxCEMTAhGxB9coyNrnUiZxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEufvwNQDpI4B3CU+2fUAAAAASUVORK5CYII=",
        "base64",
      ),
    });
    await page.getByTestId("toggle-converter-panel-btn").click();
    const panel = page.getByTestId("converter-panel");
    await panel.getByRole("tab", { name: "Image", exact: true }).click();
    await panel.getByRole("combobox", { name: "Add converter", exact: true }).click();
    await page.getByTestId(`converter-option-${imageConverterId}`).click();
    await panel.getByRole("button", { name: "Convert", exact: true }).click();
    await expect(page.getByTestId("converter-preview-result")).toBeVisible();
    await panel.getByRole("button", { name: "Add converted value", exact: true }).click();
    await panel.getByRole("button", { name: "Close converters", exact: true }).click();
    localTarget.setProcessingFailure(true);
    const failed = await sendFromComposer(page, "Restore the original image");
    expect(failed.messages.target_response_status?.response_error).toBe("processing");
    const originalImage = failed.messages.messages[0].message_pieces[1];
    expect(originalImage.original_filename).toBeTruthy();
    await page.reload();
    await page.getByRole("button", { name: "Copy conversation", exact: true }).last().click();
    const [createdResponse] = await Promise.all([
      page.waitForResponse((response) => response.request().method() === "POST"
        && new URL(response.url()).pathname === "/api/attacks"),
      page.getByRole("menuitem", { name: "New attack", exact: true }).click(),
    ]);
    expect(createdResponse.status()).toBe(201);
    const created: CreateAttackResponse = await createdResponse.json();
    expect(created.attack_result_id).not.toBe(failed.attack.attack_result_id);
    expect(createdResponse.request().postDataJSON().target_registry_name).toBe(localTarget.registryName);
    await expect(page).toHaveURL((url: URL) => url.pathname.includes(created.attack_result_id));
    await expect(page.getByTestId("chat-input")).toHaveValue("Restore the original image");
    await expect(page.getByRole("button", { name: `Remove ${originalImage.original_filename}`, exact: true })).toBeVisible();
    await expect(page.getByText(/Converter choices could not be restored/)).toBeVisible();
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(0);
    await expect(page.getByTestId(/^message-bubble-/)).toHaveCount(0);
    expect(localTarget.requestBodies).toHaveLength(1);
    localTarget.setProcessingFailure(false);
    const [retryRequest, retried] = await Promise.all([
      page.waitForRequest(isMessagePost),
      sendFromComposer(page),
    ]);
    const payload: AddMessageRequest = retryRequest.postDataJSON();
    expect(payload.pieces).toHaveLength(2);
    expect(payload.pieces.every((piece) => !piece.applied_converter_ids?.length)).toBe(true);
    expect(retried.messages.messages[0].message_pieces[1].original_value)
      .toBe(originalImage.original_value);
    await expect(page.getByText(/Converter choices could not be restored/)).toHaveCount(0);
    const historyResponse = await request.get(
      `/api/attacks/${retried.attack.attack_result_id}/messages?conversation_id=${retried.messages.conversation_id}`,
      { headers: compatibilityHeaders() },
    );
    expect(historyResponse.ok()).toBeTruthy();
    const history: ConversationMessagesResponse = await historyResponse.json();
    expect(history.messages).toHaveLength(2);
  });

  test("waits for the selected conversation and exports only its history after a rejected send", async ({
    page, request,
  }) => {
    const first = await sendFromComposer(page, "Only conversation A history");
    const attackId = first.attack.attack_result_id;
    const otherId = await createConversation(request, attackId);
    const stored = await request.post(`/api/attacks/${attackId}/messages`, {
      headers: compatibilityHeaders(),
      data: {
        role: "user",
        pieces: [{ data_type: "text", original_value: "Only conversation B history" }],
        send: false,
        target_conversation_id: otherId,
      },
    });
    expect(stored.ok()).toBeTruthy();
    await page.getByTestId("chat-input").fill("Retain this unsent draft");
    let releaseLoad: () => void = () => {};
    const loadGate = new Promise<void>((resolve) => { releaseLoad = resolve; });
    let loadStarted = false;
    let postCount = 0;
    await page.route(new RegExp(`/api/attacks/${attackId}/(?:messages|message-sends)(?:\\?|$)`), async (route: Route) => {
      if (route.request().method() === "GET"
        && new URL(route.request().url()).searchParams.get("conversation_id") === otherId) {
        loadStarted = true;
        await loadGate;
        await route.continue();
      } else if (isMessagePost(route.request())) {
        postCount += 1;
        await route.abort("connectionrefused");
      } else {
        await route.continue();
      }
    });
    try {
      await selectConversation(page, otherId);
      await expect.poll(() => loadStarted).toBe(true);
      await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Export conversation", exact: true })).toBeDisabled();
      expect(postCount).toBe(0);
    } finally {
      releaseLoad();
    }
    await expect(page.getByTestId("message-list").getByText("Only conversation B history")).toBeVisible();
    await page.getByRole("button", { name: "Send message", exact: true }).click();
    await expect(page.getByText(/Network error/)).toBeVisible();
    expect(postCount).toBe(1);
    await expect(page.getByTestId("chat-input")).toHaveValue("Retain this unsent draft");
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      (async () => {
        await page.getByRole("button", { name: "Export conversation", exact: true }).click();
        await page.getByTestId("export-json-item").click();
      })(),
    ]);
    const downloadPath = await download.path();
    if (!downloadPath) throw new Error("Expected a downloaded JSON conversation");
    const exported = await readFile(downloadPath, "utf8");
    expect(JSON.parse(exported).conversation_id).toBe(otherId);
    expect(exported).toContain("Only conversation B history");
    expect(exported).not.toContain("Only conversation A history");
  });

  test("preserves an image converter while a recovered attachment is serialized for sending", async ({
    page, request, localTarget, imageConverterId,
  }) => {
    await installDeferredFileReads(page);
    await page.getByTestId("file-input").setInputFiles({
      name: "evidence.png",
      mimeType: "image/png",
      buffer: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAIAAAAuKetIAAAAaElEQVR4nNXOQREAIAzAsFJxCEMTAhGxB9coyNrnUiZxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEufvwNQDpI4B3CU+2fUAAAAASUVORK5CYII=",
        "base64",
      ),
    });
    await page.getByTestId("toggle-converter-panel-btn").click();
    const panel = page.getByTestId("converter-panel");
    await panel.getByRole("tab", { name: "Image", exact: true }).click();
    await panel.getByRole("combobox", { name: "Add converter", exact: true }).click();
    await page.getByTestId(`converter-option-${imageConverterId}`).click();
    await expect(panel.getByTestId(`converter-item-${imageConverterId}`)).toBeVisible();
    await panel.getByRole("button", { name: "Convert", exact: true }).click();
    await expect(page.getByTestId("converter-preview-result")).toBeVisible();
    await panel.getByRole("button", { name: "Add converted value", exact: true }).click();
    await panel.getByRole("button", { name: "Close converters", exact: true }).click();
    localTarget.setProcessingFailure(true);
    const [originalRequest, first] = await Promise.all([
      page.waitForRequest(isMessagePost),
      sendFromComposer(page, "Recover this image"),
    ]);
    expect(first.messages.target_response_status?.response_error).toBe("processing");
    const originalSend: AddMessageRequest = originalRequest.postDataJSON();
    expect(originalSend.pieces).toEqual([
      expect.objectContaining({ data_type: "text", original_value: "Recover this image" }),
      expect.objectContaining({ data_type: "image_path" }),
    ]);
    expect(originalSend.pieces[1].applied_converter_ids).toEqual([imageConverterId]);
    expect(originalSend.request_converter_configurations).toBeUndefined();
    expect(originalSend).not.toHaveProperty("converter_ids");
    const attackId = first.attack.attack_result_id;
    const otherId = await createConversation(request, attackId);
    await selectConversation(page, otherId);
    await expect(page.getByTestId("chat-input")).toBeEnabled();
    await page.getByTestId("remove-attachment-0").click();
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(0);
    await selectConversation(page, first.attack.conversation_id);
    await page.getByRole("button", { name: "Copy conversation", exact: true }).last().click();
    const recover = page.getByRole("menuitem", { name: "New conversation", exact: true });
    await expect(recover).toBeEnabled();
    await page.evaluate(() => { document.documentElement.dataset.deferRecoveryReads = "true"; });
    try {
      await recover.click();
      await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeEnabled();
      await expect(page.getByTestId("chat-input")).toHaveValue("Recover this image");
      await expect(page.getByTestId("clear-media-conversion-image")).toBeVisible();
      expect(await page.evaluate(
        () => document.documentElement.dataset.recoveryReadCount,
      )).toBeUndefined();
      localTarget.setProcessingFailure(false);
      const [resentRequest, sent] = await Promise.all([
        page.waitForRequest(isMessagePost),
        sendFromComposer(page),
        (async () => {
          await expect.poll(() => page.evaluate(
            () => document.documentElement.dataset.recoveryReadCount,
          )).toBe("1");
          await expect(page.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
          expect(localTarget.requestBodies).toHaveLength(1);
          await page.evaluate(() => {
            document.dispatchEvent(new CustomEvent("release-recovery-read", { detail: 0 }));
          });
        })(),
      ]);
      const resent: AddMessageRequest = resentRequest.postDataJSON();
      expect(resent.pieces[1].applied_converter_ids).toEqual([imageConverterId]);
      expect(resent.pieces).toEqual(originalSend.pieces);
      expect(resent).not.toHaveProperty("converter_ids");
      expect(sent.messages.target_response_status?.response_error).toBe("none");
      const initialConverters = first.messages.messages[0].message_pieces[1].converter_identifiers;
      expect(initialConverters).toHaveLength(1);
      expect(sent.messages.messages[0].message_pieces[1].converter_identifiers).toEqual(initialConverters);
    } finally {
      await page.evaluate(() => {
        document.dispatchEvent(new CustomEvent("release-recovery-read", { detail: "all" }));
      });
    }
  });
});

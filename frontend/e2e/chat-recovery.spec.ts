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
} from "@playwright/test";
import type {
  AddMessageRequest,
  AddMessageResponse,
  ConversationMessagesResponse,
  CreateConversationResponse,
  TargetInstance,
} from "@/types";

interface LocalTarget {
  registryName: string;
  requestBodies: string[];
  setProcessingFailure: (enabled: boolean) => void;
}

const test = base.extend<{ localTarget: LocalTarget; imageConverterId: string }>({
  imageConverterId: async ({ request }, runTest) => {
    const name = `recovery-image-${randomUUID()}`;
    const created = await request.post("/api/converters", {
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
      const deleted = await request.delete(`/api/converters/${encodeURIComponent(name)}`);
      expect(deleted.status()).toBe(204);
    }
  },
  localTarget: async ({ page, request }, runTest) => {
    const requestBodies: string[] = [];
    const errors: Error[] = [];
    const pageErrors: Error[] = [];
    page.on("pageerror", (error: Error) => { pageErrors.push(error); });
    let processingFailure = false;
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
        response.end(processingFailure ? '{"choices":' : JSON.stringify({
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
        }));
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
    && /\/api\/attacks\/[^/]+\/messages$/.test(new URL(request.url()).pathname);
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
  expect(response.status()).toBe(200);
  return response.json();
}

async function createConversation(request: APIRequestContext, attackId: string): Promise<string> {
  const response = await request.post(`/api/attacks/${attackId}/conversations`, { data: {} });
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
      const recover = page.getByRole("button", { name: "Edit in clean conversation", exact: true });
      await expect(recover).toBeEnabled();
      await expect(page.getByText(/history from the first failed prompt onward will be left out/i)).toBeVisible();
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
      );
      expect(historyResponse.ok()).toBeTruthy();
      const history: ConversationMessagesResponse = await historyResponse.json();
      expect(history.messages).toHaveLength(keepSafePrefix ? 2 : 0);
      if (keepSafePrefix) {
        expect(history.target_response_status).toEqual({
          response_error: "none",
          request_turn_number: 0,
          response_turn_number: 1,
        });
      } else {
        expect(history.target_response_status).toBeNull();
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

  test("waits for the selected conversation and exports only its history after a rejected send", async ({
    page, request,
  }) => {
    const first = await sendFromComposer(page, "Only conversation A history");
    const attackId = first.attack.attack_result_id;
    const otherId = await createConversation(request, attackId);
    const stored = await request.post(`/api/attacks/${attackId}/messages`, {
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
    await page.route(new RegExp(`/api/attacks/${attackId}/messages`), async (route: Route) => {
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
    await expect(page.getByTestId("message-list").getByText(/Network error/)).toBeVisible();
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
    expect(originalSend.request_converter_configurations).toEqual([
      { converter_ids: [imageConverterId], indexes_to_apply: [1] },
    ]);
    expect(originalSend).not.toHaveProperty("converter_ids");
    const attackId = first.attack.attack_result_id;
    const otherId = await createConversation(request, attackId);
    await selectConversation(page, otherId);
    await expect(page.getByTestId("chat-input")).toBeEnabled();
    await page.getByTestId("remove-attachment-0").click();
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(0);
    await selectConversation(page, first.attack.conversation_id);
    const recover = page.getByRole("button", { name: "Edit in clean conversation", exact: true });
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
      expect(resent.request_converter_configurations).toEqual(originalSend.request_converter_configurations);
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

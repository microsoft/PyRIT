import { readFileSync } from "node:fs";

import {
  test,
  expect,
  type APIRequestContext,
  type Locator,
  type Page,
} from "@playwright/test";

import type { AddMessageResponse } from "@/types";

// ---------------------------------------------------------------------------
// Mode detection
// ---------------------------------------------------------------------------

/**
 * Set E2E_LIVE_MODE=true to run live tests that call real OpenAI endpoints.
 * Without it, only seeded tests run (safe for CI, no credentials needed).
 */
const LIVE_MODE = process.env.E2E_LIVE_MODE === "true";
const AZURE_OPENAI_HOSTNAME_SUFFIXES = [
  ".openai.azure.com",
  ".ai.azure.com",
  ".services.ai.azure.com",
  ".cognitiveservices.azure.com",
];

type AuthMode = "api_key" | "identity";

function isAzureOpenAiEndpoint(endpoint: string): boolean {
  try {
    const hostname = new URL(endpoint).hostname.toLowerCase();
    return AZURE_OPENAI_HOSTNAME_SUFFIXES.some((suffix) =>
      hostname.endsWith(suffix),
    );
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Helpers - shared between seeded and live modes
// ---------------------------------------------------------------------------

/** Poll the health endpoint until the backend is ready. */
async function waitForBackend(request: APIRequestContext): Promise<void> {
  const maxWait = 30_000;
  const interval = 1_000;
  const start = Date.now();
  while (Date.now() - start < maxWait) {
    try {
      const resp = await request.get("/api/health");
      if (resp.ok()) return;
    } catch {
      // Backend not ready yet
    }
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error("Backend did not become healthy within 30 seconds");
}

/** Create a target via the API, returning its registry name. */
async function createTarget(
  request: APIRequestContext,
  targetType: string,
  params: Record<string, unknown> = {},
  authMode: AuthMode = "api_key",
): Promise<string> {
  const resp = await request.post("/api/targets", {
    data: { type: targetType, params, auth_mode: authMode },
  });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  return body.target_registry_name;
}

interface SeededAttack {
  attackResultId: string;
  conversationId: string;
}

/** Create an attack via the real API. */
async function seedAttack(
  request: APIRequestContext,
  targetRegistryName: string,
): Promise<SeededAttack> {
  const resp = await request.post("/api/attacks", {
    data: { target_registry_name: targetRegistryName },
  });
  expect(resp.status()).toBe(201);
  const body = await resp.json();
  return {
    attackResultId: body.attack_result_id,
    conversationId: body.conversation_id,
  };
}

interface MessagePiece {
  data_type: string;
  original_value: string;
  mime_type?: string;
}

/** Store a message without calling the target (send=false). */
async function storeMessage(
  request: APIRequestContext,
  attackResultId: string,
  role: string,
  pieces: MessagePiece[],
  targetConversationId: string,
): Promise<void> {
  const data: Record<string, unknown> = {
    role,
    pieces,
    send: false,
    target_conversation_id: targetConversationId,
  };
  const resp = await request.post(
    `/api/attacks/${encodeURIComponent(attackResultId)}/messages`,
    { data },
  );
  expect(resp.ok()).toBeTruthy();
}

/** Send a message to the real target (send=true). */
async function sendMessage(
  request: APIRequestContext,
  attackResultId: string,
  targetRegistryName: string,
  pieces: MessagePiece[],
  targetConversationId: string,
): Promise<void> {
  const data: Record<string, unknown> = {
    role: "user",
    pieces,
    send: true,
    target_registry_name: targetRegistryName,
    target_conversation_id: targetConversationId,
  };
  const resp = await request.post(
    `/api/attacks/${encodeURIComponent(attackResultId)}/messages`,
    { data },
  );
  expect(resp.ok()).toBeTruthy();
  const body: AddMessageResponse = await resp.json();
  const messages = body.messages.messages;
  const assistantMessage = messages[messages.length - 1];
  if (!assistantMessage || assistantMessage.role !== "assistant") {
    throw new Error("Target response did not include an assistant message");
  }
  const responseErrors = assistantMessage.message_pieces
    .map((piece) => piece.response_error)
    .filter((errorType) => errorType !== "none");
  if (responseErrors.length > 0) {
    throw new Error(
      `Target returned response errors: ${responseErrors.join(", ")}`,
    );
  }
}

/** Convenience: store a text-only message. */
async function storeTextMessage(
  request: APIRequestContext,
  attackResultId: string,
  role: string,
  text: string,
  targetConversationId: string,
): Promise<void> {
  await storeMessage(
    request,
    attackResultId,
    role,
    [{ data_type: "text", original_value: text }],
    targetConversationId,
  );
}

/** Create a related conversation for an attack (optionally branching). */
async function createConversation(
  request: APIRequestContext,
  attackResultId: string,
  opts?: { sourceConversationId: string; cutoffIndex: number },
): Promise<string> {
  const data: Record<string, unknown> = {};
  if (opts) {
    data.source_conversation_id = opts.sourceConversationId;
    data.cutoff_index = opts.cutoffIndex;
  }
  const resp = await request.post(
    `/api/attacks/${encodeURIComponent(attackResultId)}/conversations`,
    { data },
  );
  expect(resp.status()).toBe(201);
  const body = await resp.json();
  return body.conversation_id;
}

/** Activate an exact target instance via the Configuration view. */
async function activateTarget(
  page: Page,
  targetRegistryName: string,
): Promise<void> {
  await page.getByTitle("Configuration").click();
  await expect(page.getByText("Target Configuration")).toBeVisible({ timeout: 10_000 });
  const row = page.getByTestId(`target-row-${targetRegistryName}`);
  await expect(row).toBeVisible({ timeout: 10_000 });
  const setActiveButton = row.getByRole("button", { name: /set active/i });
  if (await setActiveButton.isVisible()) {
    await setActiveButton.click();
  }
  await page.getByTitle("Chat").click();
  await expect(page.getByTestId("new-attack-btn")).toBeVisible({ timeout: 5_000 });
}

/** Navigate to an attack by opening the History view and clicking its row. */
async function openAttackInHistory(
  page: Page,
  attackResultId: string,
): Promise<void> {
  await page.getByTitle("Attack History").click();
  await expect(page.getByTestId("attacks-table")).toBeVisible({
    timeout: 10_000,
  });
  await page.getByTestId("refresh-btn").click();
  const row = page.getByTestId(`attack-row-${attackResultId}`);
  await expect(row).toBeVisible({ timeout: 10_000 });
  await row.click();
  await expect(page).toHaveURL(new RegExp(`/attacks/${attackResultId}$`));
  await expect(page.getByTestId("toggle-panel-btn")).toBeEnabled({
    timeout: 10_000,
  });
  await expect(page.getByTestId("loading-state")).not.toBeVisible({
    timeout: 10_000,
  });
}

/** Open the conversation side-panel (idempotent — does nothing if already open). */
async function openConversationPanel(page: Page): Promise<void> {
  const panel = page.getByTestId("conversation-panel");
  if (await panel.isVisible()) { return; }
  await page.getByTestId("toggle-panel-btn").click();
  await expect(panel).toBeVisible({ timeout: 5_000 });
}

/** Locate text only within rendered chat messages, excluding history previews. */
function getMessageText(page: Page, text: string): Locator {
  return page
    .locator('[data-testid^="message-bubble-"]')
    .getByText(text, { exact: true });
}

// ---------------------------------------------------------------------------
// Target variant configurations
// ---------------------------------------------------------------------------

const INPUT_IMAGE_BASE64 = readFileSync(
  new URL("../public/roakey.png", import.meta.url),
).toString("base64");

const DUMMY_OPENAI_PARAMS = {
  endpoint: "https://e2e-dummy.openai.azure.com",
  api_key: "e2e-dummy-key",
  model_name: "e2e-dummy-model",
};

/** Describes one target variant under test. */
interface TargetVariant {
  /** Human-readable label. */
  label: string;
  /** Target class name. */
  targetType: string;
  /** Constructor kwargs for seeded mode (dummy credentials). */
  targetParams: Record<string, unknown>;
  /** Environment variables used to configure the target in live mode. */
  liveEnvironment: {
    endpoint: string;
    apiKey: string;
    model: string;
  };
  /** Whether the target supports multi-turn conversations. */
  multiTurn: boolean;
  /** User turn pieces (seeded mode uses these directly). */
  userPieces: MessagePiece[];
  /** In live mode, only the user-sendable subset (text + image) is sent. */
  liveUserPieces?: MessagePiece[];
  /** Assistant response pieces (seeded mode only). */
  assistantPieces: MessagePiece[];
  /** Assertions for the assistant response in seeded mode. */
  expectAssistantSeeded: {
    text?: string;
    hasImage?: boolean;
    hasVideo?: boolean;
    hasAudio?: boolean;
  };
  /** Assertions for the assistant response in live mode. */
  expectAssistantLive: {
    hasText?: boolean;
    hasImage?: boolean;
    hasVideo?: boolean;
    hasAudio?: boolean;
  };
}

const TARGET_VARIANTS: TargetVariant[] = [
  {
    label: "OpenAIChatTarget (text to text)",
    targetType: "OpenAIChatTarget",
    targetParams: DUMMY_OPENAI_PARAMS,
    multiTurn: true,
    liveEnvironment: {
      endpoint: "OPENAI_CHAT_ENDPOINT",
      apiKey: "OPENAI_CHAT_KEY",
      model: "OPENAI_CHAT_MODEL",
    },
    userPieces: [{ data_type: "text", original_value: "Hello chat" }],
    assistantPieces: [
      { data_type: "text", original_value: "Chat text response" },
    ],
    expectAssistantSeeded: { text: "Chat text response" },
    expectAssistantLive: { hasText: true },
  },
  {
    label: "OpenAIChatTarget (text+image to text)",
    targetType: "OpenAIChatTarget",
    targetParams: DUMMY_OPENAI_PARAMS,
    multiTurn: true,
    liveEnvironment: {
      endpoint: "OPENAI_CHAT_ENDPOINT",
      apiKey: "OPENAI_CHAT_KEY",
      model: "OPENAI_CHAT_MODEL",
    },
    userPieces: [
      { data_type: "text", original_value: "Describe this image" },
      {
        data_type: "image_path",
        original_value: INPUT_IMAGE_BASE64,
        mime_type: "image/png",
      },
    ],
    assistantPieces: [
      { data_type: "text", original_value: "Vision text response" },
    ],
    expectAssistantSeeded: { text: "Vision text response" },
    expectAssistantLive: { hasText: true },
  },
  {
    label: "OpenAIImageTarget (text to image)",
    targetType: "OpenAIImageTarget",
    targetParams: DUMMY_OPENAI_PARAMS,
    multiTurn: false,
    liveEnvironment: {
      endpoint: "OPENAI_IMAGE_ENDPOINT",
      apiKey: "OPENAI_IMAGE_API_KEY",
      model: "OPENAI_IMAGE_MODEL",
    },
    userPieces: [
      { data_type: "text", original_value: "Generate a red dot" },
    ],
    assistantPieces: [
      {
        data_type: "image_path",
        original_value: INPUT_IMAGE_BASE64,
        mime_type: "image/png",
      },
    ],
    expectAssistantSeeded: { hasImage: true },
    expectAssistantLive: { hasImage: true },
  },
  {
    label: "OpenAIImageTarget (text+image to image)",
    targetType: "OpenAIImageTarget",
    targetParams: DUMMY_OPENAI_PARAMS,
    multiTurn: false,
    liveEnvironment: {
      endpoint: "OPENAI_IMAGE_ENDPOINT",
      apiKey: "OPENAI_IMAGE_API_KEY",
      model: "OPENAI_IMAGE_MODEL",
    },
    userPieces: [
      { data_type: "text", original_value: "Edit this image" },
      {
        data_type: "image_path",
        original_value: INPUT_IMAGE_BASE64,
        mime_type: "image/png",
      },
    ],
    assistantPieces: [
      {
        data_type: "image_path",
        original_value: INPUT_IMAGE_BASE64,
        mime_type: "image/png",
      },
    ],
    expectAssistantSeeded: { hasImage: true },
    expectAssistantLive: { hasImage: true },
  },
  {
    label: "OpenAIVideoTarget (text to video)",
    targetType: "OpenAIVideoTarget",
    targetParams: DUMMY_OPENAI_PARAMS,
    multiTurn: false,
    liveEnvironment: {
      endpoint: "OPENAI_VIDEO_ENDPOINT",
      apiKey: "OPENAI_VIDEO_KEY",
      model: "OPENAI_VIDEO_MODEL",
    },
    userPieces: [
      { data_type: "text", original_value: "Generate a video" },
    ],
    assistantPieces: [
      {
        data_type: "video_path",
        original_value: "data:video/mp4;base64,AAAA",
        mime_type: "video/mp4",
      },
    ],
    expectAssistantSeeded: { hasVideo: true },
    expectAssistantLive: { hasVideo: true },
  },
  {
    label: "OpenAITTSTarget (text to audio)",
    targetType: "OpenAITTSTarget",
    targetParams: DUMMY_OPENAI_PARAMS,
    multiTurn: false,
    liveEnvironment: {
      endpoint: "OPENAI_TTS_ENDPOINT",
      apiKey: "OPENAI_TTS_KEY",
      model: "OPENAI_TTS_MODEL",
    },
    userPieces: [{ data_type: "text", original_value: "Say hello" }],
    assistantPieces: [
      {
        data_type: "audio_path",
        original_value: "data:audio/mp3;base64,AAAA",
        mime_type: "audio/mp3",
      },
    ],
    expectAssistantSeeded: { hasAudio: true },
    expectAssistantLive: { hasAudio: true },
  },
  {
    label: "OpenAIResponseTarget (text to text)",
    targetType: "OpenAIResponseTarget",
    targetParams: DUMMY_OPENAI_PARAMS,
    multiTurn: true,
    liveEnvironment: {
      endpoint: "OPENAI_RESPONSES_ENDPOINT",
      apiKey: "OPENAI_RESPONSES_KEY",
      model: "OPENAI_RESPONSES_MODEL",
    },
    userPieces: [
      { data_type: "text", original_value: "Hello responses API" },
    ],
    assistantPieces: [
      { data_type: "text", original_value: "Response API reply" },
    ],
    expectAssistantSeeded: { text: "Response API reply" },
    expectAssistantLive: { hasText: true },
  },
  {
    label: "OpenAIResponseTarget (text+image to text)",
    targetType: "OpenAIResponseTarget",
    targetParams: DUMMY_OPENAI_PARAMS,
    multiTurn: true,
    liveEnvironment: {
      endpoint: "OPENAI_RESPONSES_ENDPOINT",
      apiKey: "OPENAI_RESPONSES_KEY",
      model: "OPENAI_RESPONSES_MODEL",
    },
    userPieces: [
      {
        data_type: "text",
        original_value: "Describe this via Responses",
      },
      {
        data_type: "image_path",
        original_value: INPUT_IMAGE_BASE64,
        mime_type: "image/png",
      },
    ],
    assistantPieces: [
      { data_type: "text", original_value: "Response API vision reply" },
    ],
    expectAssistantSeeded: { text: "Response API vision reply" },
    expectAssistantLive: { hasText: true },
  },
];

// ---------------------------------------------------------------------------
// Assertion helpers
// ---------------------------------------------------------------------------

/** Assert the seeded assistant response is visible in the UI. */
async function assertSeededAssistant(
  page: Page,
  exp: TargetVariant["expectAssistantSeeded"],
): Promise<void> {
  if (exp.text) {
    await expect(getMessageText(page, exp.text)).toBeVisible({
      timeout: 10_000,
    });
  }
  if (exp.hasImage) {
    // Look for rendered image with a generated filename (alt text like "image_xxxxx.png")
    await expect(
      page.locator('img[alt*="image_"]').first(),
    ).toBeVisible({ timeout: 10_000 });
  }
  if (exp.hasVideo) {
    // Seeded test data uses invalid base64, so the <video> element fires an error.
    // MediaWithFallback renders a "Video failed to load" fallback with data-testid="video-error".
    await expect(page.getByTestId("video-error").first()).toBeVisible({
      timeout: 10_000,
    });
  }
  if (exp.hasAudio) {
    // Same fallback pattern for audio.
    await expect(page.getByTestId("audio-error").first()).toBeVisible({
      timeout: 10_000,
    });
  }
}

/** Assert a live assistant response appeared (allows longer timeouts). */
async function assertLiveAssistant(
  page: Page,
  exp: TargetVariant["expectAssistantLive"],
): Promise<void> {
  const assistantBubble = page.getByTestId("message-bubble-1");
  if (exp.hasText) {
    await expect(assistantBubble).toContainText(/\S/, { timeout: 90_000 });
  }
  if (exp.hasImage) {
    await expect(assistantBubble.locator("img").first()).toBeVisible({
      timeout: 90_000,
    });
  }
  if (exp.hasVideo) {
    await expect(assistantBubble.locator("video").first()).toBeVisible({
      timeout: 90_000,
    });
  }
  if (exp.hasAudio) {
    await expect(assistantBubble.locator("audio").first()).toBeVisible({
      timeout: 90_000,
    });
  }
}

// ---------------------------------------------------------------------------
// Setup helpers per mode
// ---------------------------------------------------------------------------

/**
 * Seeded mode: store user + assistant messages directly in the DB.
 */
async function seedFullTurn(
  request: APIRequestContext,
  targetRegistryName: string,
  variant: TargetVariant,
): Promise<SeededAttack> {
  const attack = await seedAttack(request, targetRegistryName);
  await storeMessage(
    request,
    attack.attackResultId,
    "user",
    variant.userPieces,
    attack.conversationId,
  );
  await storeMessage(
    request,
    attack.attackResultId,
    "assistant",
    variant.assistantPieces,
    attack.conversationId,
  );
  return attack;
}

/** Check whether live mode can use an API key or Azure identity. */
function hasLiveConfiguration(variant: TargetVariant): boolean {
  const { endpoint, apiKey, model } = variant.liveEnvironment;
  const endpointValue = process.env[endpoint];
  if (!endpointValue || !process.env[model]) {
    return false;
  }
  return !!process.env[apiKey] || isAzureOpenAiEndpoint(endpointValue);
}

function getLiveAuthMode(variant: TargetVariant): AuthMode {
  return process.env[variant.liveEnvironment.apiKey] ? "api_key" : "identity";
}

// ---------------------------------------------------------------------------
// Parametrized tests
// ---------------------------------------------------------------------------

for (const variant of TARGET_VARIANTS) {
  // ===========================================================
  // SEEDED MODE - runs in CI, no credentials needed
  // ===========================================================
  test.describe(`Flows @seeded: ${variant.label}`, () => {
    let targetRegistryName: string;

    test.beforeAll(async ({ request }) => {
      await waitForBackend(request);
      targetRegistryName = await createTarget(
        request,
        variant.targetType,
        variant.targetParams,
      );
    });

    test.beforeEach(async ({ page }) => {
      await page.goto("/");
      await activateTarget(page, targetRegistryName);
    });

    test("should display seeded messages @seeded", async ({
      page,
      request,
    }) => {
      const { attackResultId } = await seedFullTurn(
        request,
        targetRegistryName,
        variant,
      );

      await openAttackInHistory(page, attackResultId);

      // Assert user text
      const userText = variant.userPieces.find(
        (p) => p.data_type === "text",
      );
      if (userText) {
        await expect(
          getMessageText(page, userText.original_value),
        ).toBeVisible({ timeout: 10_000 });
      }

      // Assert user image
      if (variant.userPieces.some((p) => p.data_type === "image_path")) {
        await expect(
          page
            .locator('[data-testid^="message-bubble-"]')
            .locator("img")
            .first(),
        ).toBeVisible({ timeout: 10_000 });
      }

      // Assert assistant response
      await assertSeededAssistant(page, variant.expectAssistantSeeded);
    });

    test("should create a new conversation and switch @seeded", async ({
      page,
      request,
    }) => {
      const { attackResultId } = await seedFullTurn(
        request,
        targetRegistryName,
        variant,
      );
      await openAttackInHistory(page, attackResultId);

      const userText = variant.userPieces.find(
        (p) => p.data_type === "text",
      );
      if (userText) {
        await expect(
          getMessageText(page, userText.original_value),
        ).toBeVisible({ timeout: 10_000 });
      }

      await openConversationPanel(page);
      const items = page.locator('[data-testid^="conversation-item-"]');
      await expect(items).toHaveCount(1, { timeout: 5_000 });

      await page.getByTestId("conversation-panel").getByTestId("new-conversation-btn").click();
      await expect(items).toHaveCount(2, { timeout: 5_000 });
      await items.nth(1).click();

      if (userText) {
        await expect(
          getMessageText(page, userText.original_value),
        ).not.toBeVisible({ timeout: 5_000 });
      }
    });

    test("should isolate messages between conversations @seeded", async ({
      page,
      request,
    }) => {
      const { attackResultId, conversationId } = await seedFullTurn(
        request,
        targetRegistryName,
        variant,
      );
      const newConversationId = await createConversation(request, attackResultId);
      await storeTextMessage(
        request,
        attackResultId,
        "user",
        "Branch-only text message",
        newConversationId,
      );

      await openAttackInHistory(page, attackResultId);

      const userText = variant.userPieces.find(
        (p) => p.data_type === "text",
      );
      if (userText) {
        await expect(
          getMessageText(page, userText.original_value),
        ).toBeVisible({ timeout: 10_000 });
      }
      // Deterministically ensure main conversation is active: open the
      // panel, select the main conversation, then close it.  This avoids
      // race conditions where the panel auto-opens and loads the branch.
      await openConversationPanel(page);
      await page.getByTestId(`conversation-item-${conversationId}`).click();
      if (userText) {
        await expect(
          getMessageText(page, userText.original_value),
        ).toBeVisible({ timeout: 5_000 });
      }
      await page.getByTestId("toggle-panel-btn").click();
      await expect(page.getByTestId("conversation-panel")).not.toBeVisible({ timeout: 3_000 });
      await expect(
        getMessageText(page, "Branch-only text message"),
      ).not.toBeVisible();

      await openConversationPanel(page);
      await page.getByTestId(`conversation-item-${newConversationId}`).click();
      await expect(
        getMessageText(page, "Branch-only text message"),
      ).toBeVisible({ timeout: 5_000 });
      if (userText) {
        await expect(
          getMessageText(page, userText.original_value),
        ).not.toBeVisible();
      }

      await page
        .getByTestId(`conversation-item-${conversationId}`)
        .click();
      if (userText) {
        await expect(
          getMessageText(page, userText.original_value),
        ).toBeVisible({ timeout: 5_000 });
      }
    });

    test("should change main conversation @seeded", async ({
      page,
      request,
    }) => {
      const { attackResultId } = await seedFullTurn(
        request,
        targetRegistryName,
        variant,
      );
      const newConversationId = await createConversation(request, attackResultId);

      await openAttackInHistory(page, attackResultId);

      // Wait for the chat view to fully load before opening the panel
      const userText = variant.userPieces.find(
        (p) => p.data_type === "text",
      );
      if (userText) {
        await expect(
          getMessageText(page, userText.original_value),
        ).toBeVisible({ timeout: 10_000 });
      } else {
        await page.waitForTimeout(3_000);
      }

      await openConversationPanel(page);

      const starBtn = page.getByTestId(`star-btn-${newConversationId}`);
      await expect(starBtn).toBeVisible({ timeout: 5_000 });

      // Promote via the UI star button (tests the full click → API → refresh flow)
      await starBtn.click();

      await expect
        .poll(
          async () => {
            const resp = await request.get(
              `/api/attacks/${encodeURIComponent(attackResultId)}/conversations`,
            );
            const data = await resp.json();
            return data.main_conversation_id;
          },
          { timeout: 10_000 },
        )
        .toBe(newConversationId);
    });

    test("should branch from an assistant message @seeded", async ({
      page,
      request,
    }) => {
      const { attackResultId, conversationId } = await seedFullTurn(
        request,
        targetRegistryName,
        variant,
      );
      await storeTextMessage(
        request,
        attackResultId,
        "user",
        "Second turn user",
        conversationId,
      );
      await storeTextMessage(
        request,
        attackResultId,
        "assistant",
        "Second turn assistant",
        conversationId,
      );

      if (variant.multiTurn) {
        // Multi-turn: branch via the UI button
        await openAttackInHistory(page, attackResultId);

        const expText = variant.expectAssistantSeeded.text;
        if (expText) {
          await expect(getMessageText(page, expText)).toBeVisible({
            timeout: 10_000,
          });
        } else {
          await page.waitForTimeout(3_000);
        }

        const branchBtn = page.getByTestId("branch-conv-btn-1");
        await expect(branchBtn).toBeVisible({ timeout: 5_000 });
        await branchBtn.click();
      } else {
        // Single-turn targets disable branch buttons in the UI.
        // Branch via the API instead to test the backend operation.
        await createConversation(request, attackResultId, {
          sourceConversationId: conversationId,
          cutoffIndex: 1,
        });
      }

      await expect
        .poll(
          async () => {
            const resp = await request.get(
              `/api/attacks/${encodeURIComponent(attackResultId)}/conversations`,
            );
            return (await resp.json()).conversations.length;
          },
          { timeout: 10_000 },
        )
        .toBeGreaterThan(1);

      const convResp = await request.get(
        `/api/attacks/${encodeURIComponent(attackResultId)}/conversations`,
      );
      const convData = await convResp.json();
      const branchConv = convData.conversations.find(
        (c: { conversation_id: string }) => c.conversation_id !== convData.main_conversation_id,
      );
      expect(branchConv).toBeDefined();
      expect(branchConv.message_count).toBeGreaterThanOrEqual(2);
    });

    test("should show correct message counts @seeded", async ({
      page,
      request,
    }) => {
      const { attackResultId, conversationId } = await seedFullTurn(
        request,
        targetRegistryName,
        variant,
      );
      await storeTextMessage(request, attackResultId, "user", "Turn 2", conversationId);
      await storeTextMessage(
        request,
        attackResultId,
        "assistant",
        "Reply 2",
        conversationId,
      );

      await openAttackInHistory(page, attackResultId);
      await openConversationPanel(page);

      const items = page.locator('[data-testid^="conversation-item-"]');
      await expect(items).toHaveCount(1, { timeout: 5_000 });
      await expect(
        items.first().locator('.fui-Badge'),
      ).toContainText("4", { timeout: 5_000 });
    });

    test("full lifecycle: seed, open, branch, switch, promote @seeded", async ({
      page,
      request,
    }) => {
      test.slow(); // This test performs many sequential steps
      const { attackResultId, conversationId } = await seedFullTurn(
        request,
        targetRegistryName,
        variant,
      );
      await storeTextMessage(
        request,
        attackResultId,
        "user",
        "Second turn",
        conversationId,
      );
      await storeTextMessage(
        request,
        attackResultId,
        "assistant",
        "Second reply",
        conversationId,
      );

      await openAttackInHistory(page, attackResultId);

      // Wait for the chat view to fully load
      const expText = variant.expectAssistantSeeded.text;
      if (expText) {
        await expect(getMessageText(page, expText)).toBeVisible({
          timeout: 10_000,
        });
      } else {
        await page.waitForTimeout(3_000);
      }

      // Branch via API (UI branch button is disabled for single-turn targets)
      const branchConversationId = await createConversation(
        request,
        attackResultId,
        { sourceConversationId: conversationId, cutoffIndex: 1 },
      );

      await openConversationPanel(page);
      const items = page.locator('[data-testid^="conversation-item-"]');
      await expect(items).toHaveCount(2, { timeout: 10_000 });

      await page
        .getByTestId(`conversation-item-${branchConversationId}`)
        .click();

      await expect(getMessageText(page, "Second turn")).not.toBeVisible({
        timeout: 5_000,
      });

      // Promote via the UI star button (tests the full click → API → refresh flow)
      const starBtn = page.getByTestId(`star-btn-${branchConversationId}`);
      await expect(starBtn).toBeVisible({ timeout: 5_000 });
      await starBtn.click();

      await expect
        .poll(
          async () => {
            const resp = await request.get(
              `/api/attacks/${encodeURIComponent(attackResultId)}/conversations`,
            );
            const data = await resp.json();
            return data.main_conversation_id;
          },
          { timeout: 10_000 },
        )
        .toBe(branchConversationId);
    });
  });

  // ===========================================================
  // LIVE MODE - requires real credentials, run manually
  // ===========================================================
  test.describe(`Flows @live: ${variant.label}`, () => {
    // Skip entire describe block when not in live mode
    test.skip(!LIVE_MODE, "Set E2E_LIVE_MODE=true to run live tests");

    let targetRegistryName: string;

    test.beforeAll(async ({ request }) => {
      if (!hasLiveConfiguration(variant)) return;
      await waitForBackend(request);
      targetRegistryName = await createTarget(
        request,
        variant.targetType,
        {},
        getLiveAuthMode(variant),
      );
    });

    test.beforeEach(async ({ page }) => {
      test.skip(
        !hasLiveConfiguration(variant),
        "Missing endpoint/model and API key or Azure identity configuration for " +
          variant.label,
      );
      await page.goto("/");
      await activateTarget(page, targetRegistryName);
    });

    test("should send a real message and display the response @live", async ({
      page,
      request,
    }) => {
      // Increase timeout - real API calls can be slow (especially video)
      test.setTimeout(120_000);

      const { attackResultId, conversationId } = await seedAttack(
        request,
        targetRegistryName,
      );
      const pieces = variant.liveUserPieces ?? variant.userPieces;
      await sendMessage(
        request,
        attackResultId,
        targetRegistryName,
        pieces,
        conversationId,
      );

      await openAttackInHistory(page, attackResultId);
      await assertLiveAssistant(page, variant.expectAssistantLive);
    });

    test("should branch from a live response @live", async ({
      page,
      request,
    }) => {
      test.setTimeout(180_000);

      const { attackResultId, conversationId } = await seedAttack(
        request,
        targetRegistryName,
      );
      // First turn: real API call
      const pieces = variant.liveUserPieces ?? variant.userPieces;
      await sendMessage(
        request,
        attackResultId,
        targetRegistryName,
        pieces,
        conversationId,
      );
      // Second turn: seeded so we have a branch point
      await storeTextMessage(
        request,
        attackResultId,
        "user",
        "Follow-up for branching",
        conversationId,
      );
      await storeTextMessage(
        request,
        attackResultId,
        "assistant",
        "Seeded follow-up reply",
        conversationId,
      );

      await openAttackInHistory(page, attackResultId);
      await assertLiveAssistant(page, variant.expectAssistantLive);

      if (variant.multiTurn) {
        const branchBtn = page.getByTestId("branch-conv-btn-1");
        await expect(branchBtn).toBeVisible({ timeout: 10_000 });
        await branchBtn.click();
      } else {
        await createConversation(request, attackResultId, {
          sourceConversationId: conversationId,
          cutoffIndex: 1,
        });
      }

      await expect
        .poll(
          async () => {
            const resp = await request.get(
              `/api/attacks/${encodeURIComponent(attackResultId)}/conversations`,
            );
            return (await resp.json()).conversations.length;
          },
          { timeout: 15_000 },
        )
        .toBeGreaterThan(1);
    });

    test("full live lifecycle: send, branch, promote @live", async ({
      page,
      request,
    }) => {
      test.setTimeout(180_000);

      // 1. Create attack and send real message
      const { attackResultId, conversationId } = await seedAttack(
        request,
        targetRegistryName,
      );
      const pieces = variant.liveUserPieces ?? variant.userPieces;
      await sendMessage(
        request,
        attackResultId,
        targetRegistryName,
        pieces,
        conversationId,
      );

      // 2. Add second turn (seeded) for branching
      await storeTextMessage(
        request,
        attackResultId,
        "user",
        "Lifecycle second turn",
        conversationId,
      );
      await storeTextMessage(
        request,
        attackResultId,
        "assistant",
        "Lifecycle second reply",
        conversationId,
      );

      // 3. Open and verify
      await openAttackInHistory(page, attackResultId);
      await assertLiveAssistant(page, variant.expectAssistantLive);

      const branchConversationId = await createConversation(
        request,
        attackResultId,
        { sourceConversationId: conversationId, cutoffIndex: 1 },
      );

      await openConversationPanel(page);
      const items = page.locator('[data-testid^="conversation-item-"]');
      await expect(items).toHaveCount(2, { timeout: 15_000 });

      // 5. Switch to branch
      await page
        .getByTestId(`conversation-item-${branchConversationId}`)
        .click();

      // Second-turn messages should not be in branch
      await expect(
        page.getByText("Lifecycle second turn"),
      ).not.toBeVisible({ timeout: 5_000 });

      // 6. Promote
      await page
        .getByTestId(`star-btn-${branchConversationId}`)
        .click();

      await expect
        .poll(
          async () => {
            const resp = await request.get(
              `/api/attacks/${encodeURIComponent(attackResultId)}/conversations`,
            );
            const data = await resp.json();
            return data.main_conversation_id;
          },
          { timeout: 10_000 },
        )
        .toBe(branchConversationId);
    });
  });
}

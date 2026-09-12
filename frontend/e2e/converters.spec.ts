import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

import { test, expect, type APIRequestContext, type Locator, type Page, type Request } from "./_fixtures";

import type {
  AddMessageRequest,
  AddMessageResponse,
  BackendMessage,
  ConverterPreviewRequest,
  TargetInstance,
} from "@/types";

import { compatibilityHeaders, mockVersion } from "./_compatibility";
import { makeTarget } from "./_targets";

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_CONVERTER_TYPES = {
  items: [
    {
      converter_type: "Base64Converter",
      supported_input_types: ["text"],
      supported_output_types: ["text"],
      parameters: [
        {
          name: "encoding_func",
          type_name: "Literal['b64encode', 'urlsafe_b64encode']",
          required: false,
          default_value: "b64encode",
          choices: ["b64encode", "urlsafe_b64encode"],
          description: "The base64 encoding function to use.",
        },
      ],
      is_llm_based: false,
      description: "Converter that encodes text to base64 format.",
    },
    {
      converter_type: "CaesarConverter",
      supported_input_types: ["text"],
      supported_output_types: ["text"],
      parameters: [
        {
          name: "caesar_offset",
          type_name: "int",
          required: true,
          default_value: null,
          choices: null,
          description: "Offset for caesar cipher.",
        },
      ],
      is_llm_based: false,
      description: "Encodes text using the Caesar cipher.",
    },
    {
      converter_type: "ImageCompressionConverter",
      supported_input_types: ["image_path"],
      supported_output_types: ["image_path"],
      parameters: [],
      is_llm_based: false,
      description: "Compresses images.",
    },
    {
      converter_type: "AddImageTextConverter",
      supported_input_types: ["text"],
      supported_output_types: ["image_path"],
      parameters: [],
      is_llm_based: false,
      description: "Renders text onto a generated image.",
    },
    {
      converter_type: "TranslationConverter",
      supported_input_types: ["text"],
      supported_output_types: ["text"],
      parameters: [],
      is_llm_based: true,
      description: "Translates prompts using an LLM.",
    },
  ],
};

const MOCK_CONVERSATION_ID = "e2e-conv-001";

// 1x1 transparent PNG returned by the mock /api/media route so that the
// inline image preview <img> element can resolve its src in headless mode.
const TINY_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGIAAgAABQABDQotsgAAAABJRU5ErkJggg==";

// Map of mock image-output converter types → path returned by the preview
// mock. Used to decide whether to emit text vs. image_path output.
const IMAGE_OUTPUT_CONVERTERS: Record<string, string> = {
  AddImageTextConverter: "/tmp/output.png",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Register all backend API mocks needed for converter tests.
 *
 * Follows the same pattern as chat.spec.ts — more specific patterns first,
 * accumulates messages for multi-turn, and mirrors real API shapes.
 */
async function mockBackendAPIs(page: Page) {
  let accumulatedMessages: Record<string, unknown>[] = [];
  // Track the converter type for each registered converter instance so the
  // preview mock can decide between text and image_path output.
  const converterTypeById: Record<string, string> = Object.fromEntries(
    MOCK_CONVERTER_TYPES.items.map((item) => [item.converter_type, item.converter_type]),
  );
  let registeredConverters = MOCK_CONVERTER_TYPES.items.map((item) => ({
    converter_id: item.converter_type,
    identifier: {
      class_name: item.converter_type,
      class_module: `pyrit.converter.${item.converter_type}`,
      hash: `${item.converter_type}-hash`,
      pyrit_version: "0.0.0",
      supported_input_types: item.supported_input_types,
      supported_output_types: item.supported_output_types,
    },
    is_llm_based: item.is_llm_based,
    description: item.description,
  }));

  await page.route(/\/api\/auth\/config$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ auth_enabled: false }),
    });
  });

  // ── Converter-specific routes ──────────────────────────────────────────

  // Converter class metadata from ConverterRegistry
  await page.route(/\/api\/converters\/types/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_CONVERTER_TYPES),
    });
  });

  // Converter preview
  await page.route(/\/api\/converters\/preview/, async (route) => {
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      const converterIds: string[] = body.converter_ids ?? [];
      let currentValue = body.original_value ?? "";
      let currentDataType = body.original_value_data_type ?? "text";
      const steps = converterIds.map((converterId) => {
        const converterType = converterTypeById[converterId] ?? "";
        const inputValue = currentValue;
        const inputDataType = currentDataType;
        currentValue = IMAGE_OUTPUT_CONVERTERS[converterType]
          ?? Buffer.from(currentValue).toString("base64");
        currentDataType = IMAGE_OUTPUT_CONVERTERS[converterType] ? "image_path" : "text";
        return {
          converter_id: converterId,
          converter_type: converterType,
          input_value: inputValue,
          input_data_type: inputDataType,
          output_value: currentValue,
          output_data_type: currentDataType,
        };
      });

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          original_value: body.original_value,
          original_value_data_type: body.original_value_data_type ?? "text",
          converted_value: currentValue,
          converted_value_data_type: currentDataType,
          steps,
        }),
      });
    }
  });

  // List and create registered converter instances
  await page.route(/\/api\/converters$/, async (route) => {
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      const converterId = body.name;
      converterTypeById[converterId] = body.type;
      const converterType = MOCK_CONVERTER_TYPES.items.find((item) => item.converter_type === body.type);
      const converter = {
        converter_id: converterId,
        identifier: {
          class_name: body.type,
          class_module: `pyrit.converter.${body.type}`,
          hash: `${converterId}-hash`,
          pyrit_version: "0.0.0",
          supported_input_types: converterType?.supported_input_types ?? [],
          supported_output_types: converterType?.supported_output_types ?? [],
        },
        is_llm_based: converterType?.is_llm_based ?? false,
        description: converterType?.description,
      };
      registeredConverters.push(converter);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(converter),
      });
    } else if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: registeredConverters }),
      });
    } else {
      await route.continue();
    }
  });

  await page.route(/\/api\/converters\/[^/]+$/, async (route) => {
    if (route.request().method() !== "DELETE") {
      await route.fallback();
      return;
    }
    const converterId = decodeURIComponent(route.request().url().split("/").pop() ?? "");
    registeredConverters = registeredConverters.filter((item) => item.converter_id !== converterId);
    delete converterTypeById[converterId];
    await route.fulfill({ status: 204 });
  });

  // Media route — serves the generated image referenced by the preview
  // mock. Returning a tiny valid PNG keeps the <img> element layout-stable.
  await page.route(/\/api\/media\?path=/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(TINY_PNG_BASE64, "base64"),
    });
  });

  // ── Standard chat routes (matching chat.spec.ts pattern) ───────────────

  // Targets list
  await page.route(/\/api\/targets/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            makeTarget({
              target_registry_name: "mock-openai-chat",
              target_type: "OpenAIChatTarget",
              endpoint: "https://mock.openai.com",
              model_name: "gpt-4o-mock",
              capabilities: {
                supports_multi_turn: true,
                supports_multi_message_pieces: false,
                supports_json_schema: false,
                supports_json_output: false,
                supports_editable_history: false,
                supports_system_prompt: false,
                supported_input_data_types: ["text"],
                supported_output_data_types: ["text"],
              },
            }),
          ],
          pagination: { limit: 50, has_more: false },
        }),
      });
    } else {
      await route.continue();
    }
  });

  // Add message — MUST be registered BEFORE create-attack route
  await page.route(/\/api\/attacks\/[^/]+\/messages/, async (route) => {
    if (route.request().method() === "POST") {
      let userText = "your message";
      let convertedText: string | null = null;
      let converterIds: string[] = [];
      try {
        const body = JSON.parse(route.request().postData() ?? "{}");
        const textPiece = body?.pieces?.find(
          (p: Record<string, string>) => p.data_type === "text",
        );
        userText = textPiece?.original_value || "your message";
        convertedText = textPiece?.converted_value ?? null;
        converterIds = textPiece?.applied_converter_ids ?? body?.request_converter_configurations
          ?.flatMap((configuration: { converter_ids?: string[] }) => configuration.converter_ids ?? [])
          ?? body?.converter_ids
          ?? [];
      } catch {
        // ignore
      }

      // Simulate backend conversion when the request carries converter configurations.
      if (convertedText === null && converterIds.length > 0) {
        convertedText = Buffer.from(userText).toString("base64");
      }

      const displayText = convertedText ?? userText;
      const turnNumber = Math.floor(accumulatedMessages.length / 2) + 1;

      const userMsg = {
        turn_number: turnNumber,
        role: "user",
        created_at: new Date().toISOString(),
        message_pieces: [
          {
            id: `piece-u-${turnNumber}`,
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: userText,
            converted_value: displayText,
            scores: [],
            response_error: "none",
          },
        ],
      };
      const assistantMsg = {
        turn_number: turnNumber,
        role: "assistant",
        created_at: new Date().toISOString(),
        message_pieces: [
          {
            id: `piece-a-${turnNumber}`,
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: `Mock response for: ${displayText}`,
            converted_value: `Mock response for: ${displayText}`,
            scores: [],
            response_error: "none",
          },
        ],
      };

      accumulatedMessages.push(userMsg, assistantMsg);

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          attack: {
            attack_result_id: "e2e-attack-001",
            conversation_id: MOCK_CONVERSATION_ID,
            attack_type: "ManualAttack",
            converters: converterIds.length > 0 ? ["Base64Converter"] : [],
            outcome: "undetermined",
            message_count: accumulatedMessages.length,
            related_conversation_ids: [],
            labels: {},
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
          messages: { messages: [...accumulatedMessages] },
        }),
      });
    } else if (route.request().method() === "GET") {
      // FIX: Handle GET so loadConversation doesn't hang in mock mode.
      // See detailed comment in chat.spec.ts mockBackendAPIs.
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ messages: [...accumulatedMessages] }),
      });
    } else {
      await route.continue();
    }
  });
  await page.route(/\/api\/attacks\/[^/]+\/conversations/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          main_conversation_id: MOCK_CONVERSATION_ID,
          conversations: [
            {
              conversation_id: MOCK_CONVERSATION_ID,
              is_main: true,
              message_count: 1,
              created_at: new Date().toISOString(),
            },
          ],
        }),
      });
    }
  });

  // Create attack — resets accumulated messages
  await page.route(/\/api\/attacks$/, async (route) => {
    if (route.request().method() === "POST") {
      accumulatedMessages = [];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          attack_result_id: "e2e-attack-001",
          conversation_id: MOCK_CONVERSATION_ID,
        }),
      });
    } else {
      await route.continue();
    }
  });

  // List attacks (for history view)
  await page.route(/\/api\/attacks\?/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              attack_result_id: "e2e-attack-001",
              conversation_id: MOCK_CONVERSATION_ID,
              attack_type: "ManualAttack",
              target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o-mock" },
              converters: ["Base64Converter"],
              outcome: "undetermined",
              last_message_preview: "Mock response",
              message_count: 2,
              related_conversation_ids: [],
              labels: {},
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            },
          ],
          pagination: { limit: 25, has_more: false },
        }),
      });
    }
  });

  // Converter options (for history filter)
  await page.route(/\/api\/attacks\/converter-options/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ converter_types: ["Base64Converter"] }),
    });
  });

  // Attack type options
  await page.route(/\/api\/attacks\/attack-options/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ attack_types: ["ManualAttack"] }),
    });
  });

  // Labels
  await page.route(/\/api\/labels/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ source: "attacks", labels: {} }),
    });
  });

  // Health + version
  await page.route(/\/api\/health/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "healthy" }) });
  });
  await page.route(/\/api\/version/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(mockVersion()) });
  });
}

/** Navigate to the target registry, set the objective default, then return to chat. */
async function activateMockTarget(page: Page) {
  await page.getByTitle("Registry").click();
  await expect(page.getByText("Target Registry")).toBeVisible({ timeout: 10000 });

  const objectiveDefault = page.getByRole("combobox", { name: "Default objective target", exact: true });
  await expect(objectiveDefault).toBeVisible({ timeout: 5000 });
  await objectiveDefault.selectOption({ index: 1 });

  await page.getByTitle("Chat", { exact: true }).click();
  await expect(page.getByTestId("new-attack-btn")).toBeVisible({ timeout: 5000 });
}

/** Open converter panel and add a registered converter to the active pipeline. */
async function selectConverter(page: Page, converterName: string) {
  // Open panel
  await page.getByTestId("toggle-converter-panel-btn").click();
  await expect(page.getByTestId("converter-panel")).toBeVisible({ timeout: 5000 });

  // Open the picker and add the registered instance
  const combobox = page.getByTestId("converter-panel-select");
  await combobox.click();
  await page.getByTestId(`converter-option-${converterName}`).click();

  // Wait for the pipeline card
  await expect(page.getByTestId(`converter-item-${converterName}`)).toBeVisible({ timeout: 5000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

async function registerConverter(
  request: APIRequestContext,
  type: string,
  params: Record<string, unknown> = {},
): Promise<string> {
  const name = `e2e-${randomUUID()}`;
  const response = await request.post("/api/converters", { headers: compatibilityHeaders(), data: { name, type, params } });
  expect(response.status()).toBe(201);
  return name;
}

async function addPipelineConverter(page: Page, converterId: string): Promise<void> {
  await page.getByTestId("converter-panel-select").click();
  await page.getByTestId(`converter-option-${converterId}`).click();
  await expect(page.getByTestId(`converter-item-${converterId}`)).toBeVisible();
}

async function selectText(editor: Locator, start: number, length: number): Promise<void> {
  await editor.focus();
  await editor.press("ControlOrMeta+Home");
  for (let index = 0; index < start; index += 1) {
    await editor.press("ArrowRight");
  }
  for (let index = 0; index < length; index += 1) {
    await editor.press("Shift+ArrowRight");
  }
}

test.describe("Shared per-piece converter pipelines @seeded", () => {
  test.setTimeout(90_000);

  let targetRegistryName: string;
  let base64Id: string;
  let caesarId: string;
  let suffixId: string;
  let imageId: string;
  const registeredConverters: string[] = [];
  const image = readFileSync(new URL("../public/roakey.png", import.meta.url));

  test.beforeAll(async ({ request }) => {
    const targetResponse = await request.post("/api/targets", {
      headers: compatibilityHeaders(),
      data: { type: "TextTarget", name: `e2e-converters-${randomUUID()}`, params: {} },
    });
    expect(targetResponse.status()).toBe(201);
    const target: TargetInstance = await targetResponse.json();
    targetRegistryName = target.target_registry_name;

    base64Id = await registerConverter(request, "Base64Converter");
    registeredConverters.push(base64Id);
    caesarId = await registerConverter(request, "CaesarConverter", { caesar_offset: 1 });
    registeredConverters.push(caesarId);
    suffixId = await registerConverter(request, "SuffixAppendConverter", { suffix: "tail" });
    registeredConverters.push(suffixId);
    imageId = await registerConverter(request, "ImageCompressionConverter", {
      output_format: "PNG",
      min_compression_threshold: 0,
      fallback_to_original: false,
    });
    registeredConverters.push(imageId);
  });

  test.afterAll(async ({ request }) => {
    for (const converterId of registeredConverters) {
      const response = await request.delete(`/api/converters/${encodeURIComponent(converterId)}`, {
        headers: compatibilityHeaders(),
      });
      expect(response.status()).toBe(204);
    }
  });

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await page.getByTitle("Registry", { exact: true }).click();
    await page.getByRole("combobox", { name: "Default objective target", exact: true })
      .selectOption(targetRegistryName);
    await page.getByTitle("Chat", { exact: true }).click();
    await expect(page.getByTestId("chat-input")).toBeEnabled();
  });

  test("should align highlighted text through wrapping, scrolling and resizing", async ({ page }) => {
    await selectConverter(page, base64Id);
    const editor = page.getByRole("textbox", { name: "Working input - Text", exact: true });
    const highlight = page.getByTestId("conversion-highlight-layer");
    const text = `${"plain words \u27eamarked text\u27eb ".repeat(12)}\n`.repeat(20);
    await editor.fill(text);
    const dimensions = (element: HTMLElement) => ({
      width: element.clientWidth, height: element.clientHeight, scrollHeight: element.scrollHeight,
    });

    expect(await highlight.evaluate(dimensions)).toEqual(await editor.evaluate(dimensions));
    await editor.press("ControlOrMeta+End");
    await expect.poll(() => editor.evaluate((element: HTMLElement) => element.scrollTop)).toBeGreaterThan(0);
    await expect.poll(() => highlight.evaluate((element: HTMLElement) => element.scrollTop))
      .toBe(await editor.evaluate((element: HTMLElement) => element.scrollTop));

    const bounds = await editor.boundingBox();
    if (!bounds) throw new Error("Editor is not visible");
    await page.mouse.move(bounds.x + bounds.width - 3, bounds.y + bounds.height - 3);
    await page.mouse.down();
    await page.mouse.move(bounds.x + bounds.width - 3, bounds.y + bounds.height + 60);
    await page.mouse.up();
    await expect.poll(() => editor.evaluate((element: HTMLElement) => element.clientHeight))
      .toBeGreaterThan(bounds.height);
    expect(await highlight.evaluate(dimensions)).toEqual(await editor.evaluate(dimensions));
    await editor.press("ControlOrMeta+Home");
    await expect.poll(() => editor.evaluate((element: HTMLElement) => element.scrollTop))
      .toBeLessThan(await editor.evaluate((element: HTMLElement) => element.clientHeight));
    await expect.poll(() => highlight.evaluate((element: HTMLElement) => element.scrollTop))
      .toBe(await editor.evaluate((element: HTMLElement) => element.scrollTop));
    await expect(editor).toHaveValue(text);
  });

  test("should keep the same stage focused through repeated keyboard moves, including duplicates", async ({ page }) => {
    await page.getByTestId("chat-input").fill("hello");
    await selectConverter(page, base64Id);
    await addPipelineConverter(page, caesarId);
    await addPipelineConverter(page, base64Id);

    const handles = page.getByRole("button", { name: /^Reorder converter/ });
    await handles.nth(2).focus();
    const focusedStage = await handles.nth(2).elementHandle();
    expect(focusedStage).not.toBeNull();
    for (const key of ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowUp", "ArrowUp"]) {
      await page.keyboard.press(key);
      expect(await focusedStage?.evaluate((element: HTMLElement) => document.activeElement === element)).toBe(true);
    }
    expect(await handles.evaluateAll((elements: HTMLElement[]) => (
      elements.map((element: HTMLElement) => element.getAttribute("aria-label"))
    ))).toEqual([
      `Reorder converter ${base64Id}, stage 1 of 2`,
      `Reorder converter ${base64Id}, stage 2 of 2`,
      `Reorder converter ${caesarId}`,
    ]);
    const previewResponse = page.waitForResponse((response) => (
      response.request().method() === "POST" && new URL(response.url()).pathname === "/api/converters/preview"
    ));
    await page.getByRole("button", { name: "Convert", exact: true }).click();
    const response = await previewResponse;
    expect(response.ok()).toBeTruthy();
    expect(response.request().postDataJSON().converter_ids).toEqual([base64Id, base64Id, caesarId]);
    await expect(page.getByTestId("converter-preview-result")).toBeVisible();
  });

  test("should upload attachments and create pipeline stages without crypto.randomUUID", async ({ page }) => {
    await page.evaluate(() => {
      Object.defineProperty(crypto, "randomUUID", { configurable: true, value: undefined });
    });
    await page.locator('input[type="file"]').setInputFiles([
      { name: "first.png", mimeType: "image/png", buffer: image },
      { name: "second.png", mimeType: "image/png", buffer: image },
    ]);
    await expect(page.getByTestId("remove-attachment-0")).toBeVisible();
    await expect(page.getByTestId("remove-attachment-1")).toBeVisible();
    await page.getByTestId("toggle-converter-panel-btn").click();
    await page.getByRole("tab", { name: "Image", exact: true }).click();
    await addPipelineConverter(page, imageId);
    await page.getByRole("button", { name: "Convert", exact: true }).click();
    await expect(page.getByTestId("converter-preview-result")).toHaveCount(2);
    await page.getByRole("button", { name: "Add converted value", exact: true }).click();
    await page.getByRole("button", { name: "Close converters", exact: true }).click();
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(2);
  });

  test("should preserve an ordered pipeline and its output across close and reopen, then persist the send", async ({
    page, request,
  }) => {
    await page.getByTestId("chat-input").fill("hello");
    await selectConverter(page, base64Id);
    await addPipelineConverter(page, caesarId);

    await page.getByRole("button", { name: "Close converters", exact: true }).click();
    await expect(page.getByTestId("converter-panel")).toHaveCount(0);
    await page.getByTestId("toggle-converter-panel-btn").click();
    await expect(page.getByTestId(`converter-item-${base64Id}`)).toBeVisible();
    await expect(page.getByTestId(`converter-item-${caesarId}`)).toBeVisible();
    await expect(page.getByTestId("converter-preview-result")).toHaveCount(0);

    await page.getByRole("button", { name: "Convert", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Stage 1 output - Text", exact: true })).toHaveValue("aGVsbG8=");
    await expect(page.getByRole("textbox", { name: "Stage 2 output - Text", exact: true })).toHaveValue("bHWtcH9=");
    await page.getByRole("button", { name: "Add converted value", exact: true }).click();
    await expect(page.getByTestId("converted-value-input")).toHaveValue("bHWtcH9=");

    await page.getByRole("button", { name: "Close converters", exact: true }).click();
    await page.getByTestId("toggle-converter-panel-btn").click();
    await expect(page.getByTestId("converter-preview-result").getByRole("textbox")).toHaveValue("bHWtcH9=");
    await page.getByRole("button", { name: "Close converters", exact: true }).click();

    const [response] = await Promise.all([
      page.waitForResponse((candidate) => candidate.request().method() === "POST"
        && /\/api\/attacks\/[^/]+\/messages$/.test(new URL(candidate.url()).pathname)),
      page.getByRole("button", { name: "Send message", exact: true }).click(),
    ]);
    expect(response.status()).toBe(200);
    const sentRequest: AddMessageRequest = response.request().postDataJSON();
    expect(sentRequest.pieces).toHaveLength(1);
    expect(sentRequest.pieces[0]).toMatchObject({
      data_type: "text",
      original_value: "hello",
      converted_value: "bHWtcH9=",
      converted_value_data_type: "text",
    });
    expect(sentRequest.pieces[0].applied_converter_ids).toEqual([base64Id, caesarId]);
    expect(sentRequest.request_converter_configurations).toBeUndefined();

    const sent: AddMessageResponse = await response.json();
    const historyResponse = await request.get(
      `/api/attacks/${sent.attack.attack_result_id}/messages?conversation_id=${sent.attack.conversation_id}`,
      { headers: compatibilityHeaders() },
    );
    expect(historyResponse.ok()).toBeTruthy();
    const history: AddMessageResponse["messages"] = await historyResponse.json();
    const userMessage = history.messages.find((message: BackendMessage) => message.role === "user");
    expect(userMessage?.message_pieces).toEqual([
      expect.objectContaining({
        original_value: "hello",
        converted_value: "bHWtcH9=",
        converted_value_data_type: "text",
      }),
    ]);
  });

  test("should resume an empty edited stage without rerunning its prefix", async ({ page }) => {
    await page.getByTestId("chat-input").fill("hello");
    await selectConverter(page, base64Id);
    await addPipelineConverter(page, suffixId);
    await page.getByRole("button", { name: "Convert", exact: true }).click();
    const first = page.getByRole("textbox", { name: "Stage 1 output - Text", exact: true });
    await expect(first).toHaveValue("aGVsbG8=");
    await first.fill("");
    const responsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST" && new URL(response.url()).pathname === "/api/converters/preview"
    ));
    await page.getByRole("button", { name: "Convert Text from stage 2 to end", exact: true }).click();
    const response = await responsePromise;
    expect(response.ok()).toBeTruthy();
    expect(response.request().postDataJSON()).toEqual({
      original_value: "", original_value_data_type: "text", converter_ids: [suffixId],
    });
    await expect(first).toHaveValue("");
    await expect(page.getByRole("textbox", { name: "Stage 2 output - Text", exact: true })).toHaveValue(" tail");
  });

  test("should partially convert editable drafts through the remaining chain and send the exact applied result", async ({
    page, request,
  }) => {
    const source = "The original chat message stays unchanged.";
    const firstOutput = "keep Y2F0 tail";
    const editedOutput = "stay \u27eadog\u27eb tail";
    const remainingOutput = "stay eph tail";
    const finalOutput = Buffer.from(remainingOutput).toString("base64");
    const appliedOutput = `${finalOutput}\nReviewed exactly.`;
    const previewRequests: ConverterPreviewRequest[] = [];
    page.on("request", (preview: Request) => {
      if (preview.method() === "POST" && new URL(preview.url()).pathname === "/api/converters/preview") {
        previewRequests.push(preview.postDataJSON());
      }
    });

    await page.getByTestId("chat-input").fill(source);
    await selectConverter(page, base64Id);
    await addPipelineConverter(page, caesarId);
    await addPipelineConverter(page, base64Id);
    const workingInput = page.getByRole("textbox", { name: "Working input - Text", exact: true });
    await expect(workingInput).toHaveValue(source);
    await workingInput.fill("keep cat tail");
    await selectText(workingInput, 5, 3);
    await page.getByRole("button", { name: "Convert selection only in Working input - Text", exact: true }).click();
    await expect(workingInput).toHaveValue("keep \u27eacat\u27eb tail");
    await expect(page.getByTestId("chat-input")).toHaveValue(source);

    await page.getByRole("button", { name: "Convert", exact: true }).click();
    const firstStage = page.getByRole("textbox", { name: "Stage 1 output - Text", exact: true });
    const secondStage = page.getByRole("textbox", { name: "Stage 2 output - Text", exact: true });
    const finalStage = page.getByRole("textbox", { name: "Stage 3 output - Text", exact: true });
    await expect(firstStage).toHaveValue(firstOutput);
    await expect(secondStage).toHaveValue("lffq Z3G1 ubjm");
    await expect(finalStage).toHaveValue(Buffer.from("lffq Z3G1 ubjm").toString("base64"));
    expect(previewRequests).toEqual([{
      original_value: "keep \u27eacat\u27eb tail",
      original_value_data_type: "text",
      converter_ids: [base64Id, caesarId, base64Id],
    }]);

    await firstStage.fill("stay dog tail");
    await expect(page.getByTestId("converter-preview-result")).toHaveCount(0);
    await selectText(firstStage, 5, 3);
    await page.getByRole("button", { name: "Convert selection only in Stage 1 output - Text", exact: true }).click();
    await expect(firstStage).toHaveValue(editedOutput);
    await page.getByRole("button", { name: "Convert Text from stage 2 to end", exact: true }).click();
    await expect(firstStage).toHaveValue(editedOutput);
    await expect(secondStage).toHaveValue(remainingOutput);
    await expect(finalStage).toHaveValue(finalOutput);
    expect(previewRequests).toHaveLength(2);
    expect(previewRequests[1]).toEqual({
      original_value: editedOutput,
      original_value_data_type: "text",
      converter_ids: [caesarId, base64Id],
    });
    await expect(page.getByRole("button", { name: "Convert Text from stage 4 to end", exact: true })).toHaveCount(0);
    await expect(workingInput).toHaveValue("keep \u27eacat\u27eb tail");
    await expect(page.getByTestId("chat-input")).toHaveValue(source);

    await finalStage.fill(appliedOutput);
    await page.getByRole("button", { name: "Add converted value", exact: true }).click();
    await expect(page.getByTestId("converted-value-input")).toHaveValue(appliedOutput);
    await expect(page.getByTestId("chat-input")).toHaveValue(source);
    await page.getByRole("button", { name: "Close converters", exact: true }).click();

    const [response] = await Promise.all([
      page.waitForResponse((candidate) => candidate.request().method() === "POST"
        && /\/api\/attacks\/[^/]+\/messages$/.test(new URL(candidate.url()).pathname)),
      page.getByRole("button", { name: "Send message", exact: true }).click(),
    ]);
    expect(response.status()).toBe(200);
    const sentRequest: AddMessageRequest = response.request().postDataJSON();
    expect(sentRequest.pieces).toEqual([expect.objectContaining({
      data_type: "text",
      original_value: source,
      converted_value: appliedOutput,
      converted_value_data_type: "text",
    })]);
    expect(sentRequest.pieces[0].applied_converter_ids).toEqual([base64Id, caesarId, base64Id]);
    expect(sentRequest.request_converter_configurations).toBeUndefined();
    expect(previewRequests).toHaveLength(2);
    const sent: AddMessageResponse = await response.json();
    const expectedPiece = expect.objectContaining({
      original_value: source,
      original_value_data_type: "text",
      converted_value: appliedOutput,
      converted_value_data_type: "text",
    });
    expect(sent.messages.messages.find((message: BackendMessage) => message.role === "user")?.message_pieces)
      .toEqual([expectedPiece]);

    await expect(page).toHaveURL((url: URL) => url.pathname.includes(sent.attack.attack_result_id));
    await expect(page.getByTestId("message-piece-0-0")).toHaveText(appliedOutput);
    await page.reload();
    await expect(page.getByTestId("original-section")).toContainText(source);
    await expect(page.getByTestId("message-piece-0-0")).toHaveText(appliedOutput);
    const historyResponse = await request.get(
      `/api/attacks/${sent.attack.attack_result_id}/messages?conversation_id=${sent.attack.conversation_id}`,
    );
    expect(historyResponse.ok()).toBeTruthy();
    const history: AddMessageResponse["messages"] = await historyResponse.json();
    expect(history.messages.find((message: BackendMessage) => message.role === "user")?.message_pieces)
      .toEqual([expectedPiece]);
  });

  test("should convert every configured input and keep duplicate image pieces independent", async ({ page }) => {
    const previewRequests: ConverterPreviewRequest[] = [];
    page.on("request", (request: Request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/converters/preview") {
        previewRequests.push(request.postDataJSON());
      }
    });
    await page.getByTestId("chat-input").fill("hello");
    await page.locator('input[type="file"]').setInputFiles([
      { name: "duplicate.png", mimeType: "image/png", buffer: image },
      { name: "duplicate.png", mimeType: "image/png", buffer: image },
    ]);
    await selectConverter(page, base64Id);
    await page.getByRole("tab", { name: "Image", exact: true }).click();
    await expect(page.getByTestId("converter-input-value")).toHaveCount(2);
    await addPipelineConverter(page, imageId);

    await page.getByRole("button", { name: "Convert", exact: true }).click();
    const results = page.getByTestId("converter-preview-result");
    await expect(results).toHaveCount(2);
    await expect(page.getByRole("button", { name: "Add converted value", exact: true })).toBeEnabled();
    expect(previewRequests).toHaveLength(3);
    expect(previewRequests.filter((preview: ConverterPreviewRequest) => preview.original_value_data_type === "text"))
      .toEqual([{ original_value: "hello", original_value_data_type: "text", converter_ids: [base64Id] }]);
    expect(previewRequests.filter((preview: ConverterPreviewRequest) => preview.original_value_data_type === "image_path"))
      .toEqual([
        { original_value: `data:image/png;base64,${image.toString("base64")}`, original_value_data_type: "image_path", converter_ids: [imageId] },
        { original_value: `data:image/png;base64,${image.toString("base64")}`, original_value_data_type: "image_path", converter_ids: [imageId] },
      ]);
    for (const result of await results.all()) {
      const preview = result.getByRole("img");
      await expect(preview).toBeVisible();
      await expect.poll(() => preview.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    }
    const firstOutput = await results.nth(0).getByRole("img").getAttribute("src");
    const secondOutput = await results.nth(1).getByRole("img").getAttribute("src");
    expect(firstOutput).not.toBe(secondOutput);

    await page.getByRole("button", { name: "Add converted value", exact: true }).click();
    await page.getByRole("button", { name: "Close converters", exact: true }).click();
    await expect(page.getByTestId("converted-value-input")).toHaveValue("aGVsbG8=");
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(2);

    await page.getByTestId("clear-media-conversion-image").nth(0).click();
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(1);
    await page.getByTestId("remove-attachment-0").click();
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(1);

    await page.getByTestId("toggle-converter-panel-btn").click();
    await expect(page.getByTestId("converter-preview-result").getByRole("textbox")).toHaveValue("aGVsbG8=");
    await page.getByRole("tab", { name: "Image (1)", exact: true }).click();
    await expect(page.getByTestId(`converter-item-${imageId}`)).toBeVisible();
    await expect(page.getByTestId("converter-input-value")).toHaveCount(1);
    await expect(results).toHaveCount(1);
    await expect(results.getByRole("img")).toHaveAttribute("src", secondOutput ?? "");
    expect(previewRequests).toHaveLength(3);
  });

  test("should apply only successful current pieces after an image failure and a text edit", async ({ page }) => {
    await page.getByTestId("chat-input").fill("original text");
    await page.locator('input[type="file"]').setInputFiles([
      { name: "valid.png", mimeType: "image/png", buffer: image },
      { name: "broken.png", mimeType: "image/png", buffer: Buffer.from("not an image") },
    ]);
    await selectConverter(page, base64Id);
    await page.getByRole("tab", { name: "Image", exact: true }).click();
    await addPipelineConverter(page, imageId);
    await page.getByRole("button", { name: "Convert", exact: true }).click();

    await expect(page.getByTestId("converter-preview-error")).toContainText("broken.png");
    await expect(page.getByTestId("converter-preview-result")).toHaveCount(1);
    await expect(page.getByRole("button", { name: "Add converted value", exact: true })).toBeEnabled();
    await page.getByRole("tab", { name: "Text (1)", exact: true }).click();
    await expect(page.getByTestId("converter-preview-result").getByRole("textbox")).toHaveValue(
      Buffer.from("original text").toString("base64"),
    );
    await page.getByTestId("chat-input").fill("changed after conversion");
    await expect(page.getByTestId("converter-preview-result")).toHaveCount(0);
    await page.getByRole("button", { name: "Add converted value", exact: true }).click();
    await page.getByRole("button", { name: "Close converters", exact: true }).click();

    await expect(page.getByTestId("chat-input")).toHaveValue("changed after conversion");
    await expect(page.getByTestId("converted-value-input")).toHaveCount(0);
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(1);
    await page.getByTestId("remove-attachment-0").click();
    await expect(page.getByTestId("clear-media-conversion-image")).toHaveCount(0);
    await expect(page.getByText("broken.png", { exact: false })).toBeVisible();
  });
});

test.describe("Converter Panel", () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendAPIs(page);
    await page.goto("/");
    await activateMockTarget(page);
  });

  test("should open converter panel and display registered converters", async ({ page }) => {
    // Click the converter toggle button
    await page.getByTestId("toggle-converter-panel-btn").click();

    // Panel should appear with the picker
    await expect(page.getByTestId("converter-panel")).toBeVisible({ timeout: 5000 });
    const combobox = page.getByTestId("converter-panel-select");
    await expect(combobox).toBeVisible();

    // Open dropdown — registered converters and the create action are listed
    await combobox.click();
    await expect(page.getByTestId("create-converter-option")).toBeVisible();
    await expect(page.getByTestId("converter-option-Base64Converter")).toBeVisible();
    await expect(page.getByTestId("converter-option-CaesarConverter")).toBeVisible();
    await expect(page.getByTestId("converter-option-TranslationConverter")).toBeVisible();
  });

  test("should select a converter, show details and convert output", async ({ page }) => {
    // Type text BEFORE opening panel
    await page.getByTestId("chat-input").fill("hello");

    // Select Base64Converter
    await selectConverter(page, "Base64Converter");

    // The editable working input starts from the chat input.
    await expect(page.getByRole("textbox", { name: "Working input - Text", exact: true })).toHaveValue("hello");

    // Description should be visible
    await expect(
      page.getByTestId("converter-item-Base64Converter")
        .getByText("Converter that encodes text to base64 format."),
    ).toBeVisible();

    // Nothing converts until Convert is pressed
    await expect(page.getByTestId("converter-preview-result")).toHaveCount(0);
    await page.getByTestId("converter-preview-btn").click();
    await expect(page.getByTestId("converter-preview-result").getByRole("textbox")).toHaveValue("aGVsbG8=");
  });

  test("should apply converted value and send message with original+converted sections", async ({ page }) => {
    // Type text BEFORE opening the converter panel
    await page.getByTestId("chat-input").fill("hello");

    // Select converter and convert the pipeline.
    await selectConverter(page, "Base64Converter");
    await page.getByTestId("converter-preview-btn").click();
    await expect(page.getByTestId("converter-preview-result")).toBeVisible({ timeout: 10000 });

    // Click "Add converted value"
    await page.getByTestId("use-converted-btn").click();

    // Original badge should appear in input area
    await expect(page.getByTestId("original-banner")).toBeVisible();
    // Converted indicator should appear below input
    await expect(page.getByTestId("converted-indicator")).toBeVisible();

    // Close converter panel before sending
    await page.getByTestId("close-converter-panel-btn").click();
    await expect(page.getByTestId("converter-panel")).not.toBeVisible();

    // Send the message
    await page.getByTestId("send-message-btn").click();

    // Wait for the user message to appear (local optimistic display)
    // The converted value (base64 of "hello") should be shown
    await expect(page.locator('[data-testid="original-section"]')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('[data-testid="converted-label"]')).toBeVisible({ timeout: 5000 });
  });

  test("should show converter badge in attack history after sending with converter", async ({ page }) => {
    // Type text BEFORE opening panel
    await page.getByTestId("chat-input").fill("hello");
    await selectConverter(page, "Base64Converter");
    await page.getByTestId("converter-preview-btn").click();
    await expect(page.getByTestId("use-converted-btn")).toBeEnabled({ timeout: 10000 });
    await page.getByTestId("use-converted-btn").click();

    // Close converter panel before sending
    await page.getByTestId("close-converter-panel-btn").click();

    await page.getByTestId("send-message-btn").click();

    // Wait for response to confirm send completed
    await expect(page.getByText(/Mock response for:/)).toBeVisible({ timeout: 15000 });

    // Navigate to History view
    await page.getByTitle("History").click();

    // Converter badge should appear in the attack table
    await expect(page.getByText("Base64Converter")).toBeVisible({ timeout: 10000 });
  });

  test("should select an already configured converter without showing constructor fields", async ({ page }) => {
    await page.getByTestId("chat-input").fill("hello");

    // Select CaesarConverter — its constructor params were fixed at registration
    await selectConverter(page, "CaesarConverter");

    await expect(page.getByTestId("converter-item-CaesarConverter")).toBeVisible();
    await expect(page.getByTestId("converter-params")).toHaveCount(0);
  });

  test("should keep the picker available for an ordered converter chain", async ({ page }) => {
    await page.getByTestId("chat-input").fill("hello");
    await page.getByTestId("toggle-converter-panel-btn").click();
    const combobox = page.getByTestId("converter-panel-select");

    await combobox.click();
    await page.getByTestId("converter-option-Base64Converter").click();
    await expect(page.getByTestId("converter-item-Base64Converter")).toBeVisible();

    await combobox.click();
    await page.getByTestId("converter-option-CaesarConverter").click();

    await expect(page.getByTestId("converter-item-Base64Converter")).toBeVisible();
    await expect(page.getByTestId("converter-item-CaesarConverter")).toBeVisible();
    await expect(page.getByTestId("converter-stage-output-0")).toContainText(
      "Choose Convert above to see this stage output.",
    );
    await expect(page.getByTestId("converter-stage-output-1")).toContainText(
      "Choose Convert above to see this stage output.",
    );

    await page.getByTestId("converter-preview-btn").click();

    await expect(page.getByRole("textbox", { name: "Stage 1 output - Text", exact: true })).toHaveValue("aGVsbG8=");
    await expect(page.getByRole("textbox", { name: "Stage 2 output - Text", exact: true })).toHaveValue(
      Buffer.from("aGVsbG8=").toString("base64"),
    );
  });

  test("should only show text-input converters when no media is attached", async ({ page }) => {
    // Open converter panel (text-only input, no attachments)
    await page.getByTestId("toggle-converter-panel-btn").click();
    await expect(page.getByTestId("converter-panel")).toBeVisible({ timeout: 5000 });

    // Open combobox
    const combobox = page.getByTestId("converter-panel-select");
    await combobox.click();

    // Text converters should be visible
    await expect(page.getByTestId("converter-option-Base64Converter")).toBeVisible();
    await expect(page.getByTestId("converter-option-CaesarConverter")).toBeVisible();

    // Image-only converter should NOT appear
    await expect(page.getByTestId("converter-option-ImageCompressionConverter")).not.toBeVisible();
  });

  test("should show converter type in history filter options", async ({ page }) => {
    // Navigate to History view
    await page.getByTitle("History").click();

    // The converter badge should be visible in the attack table
    await expect(page.getByText("Base64Converter")).toBeVisible({ timeout: 10000 });
  });

  test("should render inline image preview in input box after a text→image conversion", async ({ page }) => {
    // Type a prompt before opening the panel
    await page.getByTestId("chat-input").fill("hello world");

    // Select AddImageTextConverter (text input → image_path output)
    await selectConverter(page, "AddImageTextConverter");

    // Convert the pipeline explicitly
    await page.getByTestId("converter-preview-btn").click();
    await expect(page.getByTestId("converter-preview-result")).toBeVisible({ timeout: 10000 });

    // Apply the converted value to the input
    await page.getByTestId("use-converted-btn").click();

    // Close converter panel so the input area is unobscured
    await page.getByTestId("close-converter-panel-btn").click();
    await expect(page.getByTestId("converter-panel")).not.toBeVisible();

    // The converted-file-chip block is rendered in the input area for image_path outputs
    const chip = page.getByTestId("converted-file-chip");
    await expect(chip).toBeVisible();
    await expect(chip).toContainText("output.png");

    // The inline image preview is rendered alongside the filename + Open link
    const preview = page.getByTestId("converted-file-preview-image");
    await expect(preview).toBeVisible({ timeout: 10000 });
    await expect(preview).toHaveAttribute("src", /\/api\/media\?path=/);
    await expect(preview).toHaveAttribute("alt", "output.png");

    // The Open link is still present alongside the preview
    await expect(page.getByTestId("converted-file-open")).toHaveAttribute("href", /\/api\/media\?path=/);

    // Audio / video previews must NOT be rendered for an image conversion
    await expect(page.getByTestId("converted-file-preview-audio")).toHaveCount(0);
    await expect(page.getByTestId("converted-file-preview-video")).toHaveCount(0);

    // Dismissing the chip clears the entire block (including the preview)
    await page.getByTestId("clear-converted-file-chip").click();
    await expect(chip).not.toBeVisible();
    await expect(page.getByTestId("converted-file-preview-image")).toHaveCount(0);
  });
});

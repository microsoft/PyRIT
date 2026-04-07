import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_CATALOG = {
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

  // ── Converter-specific routes ──────────────────────────────────────────

  // Converter catalog
  await page.route(/\/api\/converters\/catalog/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_CATALOG),
    });
  });

  // Converter preview
  await page.route(/\/api\/converters\/preview/, async (route) => {
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      const converted = Buffer.from(body.original_value ?? "").toString("base64");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          original_value: body.original_value,
          original_value_data_type: body.original_value_data_type ?? "text",
          converted_value: converted,
          converted_value_data_type: "text",
          steps: [],
        }),
      });
    }
  });

  // Create converter instance
  await page.route(/\/api\/converters$/, async (route) => {
    if (route.request().method() === "POST") {
      const body = JSON.parse(route.request().postData() ?? "{}");
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          converter_id: "mock-converter-001",
          converter_type: body.type,
          display_name: null,
        }),
      });
    } else {
      await route.continue();
    }
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
            {
              target_registry_name: "mock-openai-chat",
              target_type: "OpenAIChatTarget",
              endpoint: "https://mock.openai.com",
              model_name: "gpt-4o-mock",
              supports_multi_turn: true,
            },
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
        convertedText = textPiece?.converted_value || null;
        converterIds = body?.converter_ids || [];
      } catch {
        // ignore
      }

      // Simulate backend conversion: when converter_ids are provided but no
      // converted_value was set client-side, the backend applies the converter.
      if (!convertedText && converterIds.length > 0) {
        convertedText = Buffer.from(userText).toString("base64");
      }

      const displayText = convertedText ?? userText;
      const turnNumber = Math.floor(accumulatedMessages.length / 2) + 1;

      const userMsg = {
        turn_number: turnNumber,
        role: "user",
        created_at: new Date().toISOString(),
        pieces: [
          {
            piece_id: `piece-u-${turnNumber}`,
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
        pieces: [
          {
            piece_id: `piece-a-${turnNumber}`,
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
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ version: "0.11.1" }) });
  });
}

/** Navigate to config, set the mock target as active, then return to chat. */
async function activateMockTarget(page: Page) {
  await page.getByTitle("Configuration").click();
  await expect(page.getByText("Target Configuration")).toBeVisible({ timeout: 10000 });

  const setActiveBtn = page.getByRole("button", { name: /set active/i });
  await expect(setActiveBtn).toBeVisible({ timeout: 5000 });
  await setActiveBtn.click();

  await page.getByTitle("Chat", { exact: true }).click();
  await expect(page.getByText("PyRIT Attack")).toBeVisible({ timeout: 5000 });
}

/** Open converter panel and select a converter by name. */
async function selectConverter(page: Page, converterName: string) {
  // Open panel
  await page.getByTestId("toggle-converter-panel-btn").click();
  await expect(page.getByTestId("converter-panel")).toBeVisible({ timeout: 5000 });

  // Open combobox and select
  const combobox = page.getByTestId("converter-panel-select");
  await combobox.click();
  await page.getByTestId(`converter-option-${converterName}`).click();

  // Wait for detail card
  await expect(page.getByTestId(`converter-item-${converterName}`)).toBeVisible({ timeout: 5000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Converter Panel", () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendAPIs(page);
    await page.goto("/");
    await activateMockTarget(page);
  });

  test("should open converter panel and display converter catalog", async ({ page }) => {
    // Click the converter toggle button
    await page.getByTestId("toggle-converter-panel-btn").click();

    // Panel should appear with combobox
    await expect(page.getByTestId("converter-panel")).toBeVisible({ timeout: 5000 });
    const combobox = page.getByTestId("converter-panel-select");
    await expect(combobox).toBeVisible();

    // Open dropdown — converters should be listed
    await combobox.click();
    await expect(page.getByTestId("converter-option-Base64Converter")).toBeVisible();
    await expect(page.getByTestId("converter-option-CaesarConverter")).toBeVisible();
    await expect(page.getByTestId("converter-option-TranslationConverter")).toBeVisible();
  });

  test("should select a converter, show details and preview output", async ({ page }) => {
    // Type text BEFORE opening panel
    await page.getByTestId("chat-input").fill("hello");

    // Select Base64Converter
    await selectConverter(page, "Base64Converter");

    // Description should be visible
    await expect(page.getByText("Converter that encodes text to base64 format.")).toBeVisible();

    // Auto-preview should fire (non-LLM text converter)
    await expect(page.getByTestId("converter-preview-result")).toBeVisible({ timeout: 10000 });
  });

  test("should apply converted value and send message with original+converted sections", async ({ page }) => {
    // Type text BEFORE opening the converter panel
    await page.getByTestId("chat-input").fill("hello");

    // Select converter and wait for auto-preview
    await selectConverter(page, "Base64Converter");
    await expect(page.getByTestId("converter-preview-result")).toBeVisible({ timeout: 10000 });

    // Click "Use Converted Value"
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
    await expect(page.getByTestId("use-converted-btn")).toBeVisible({ timeout: 10000 });
    await page.getByTestId("use-converted-btn").click();

    // Close converter panel before sending
    await page.getByTestId("close-converter-panel-btn").click();

    await page.getByTestId("send-message-btn").click();

    // Wait for response to confirm send completed
    await expect(page.getByText(/Mock response for:/)).toBeVisible({ timeout: 15000 });

    // Navigate to History view
    await page.getByTitle("Attack History").click();

    // Converter badge should appear in the attack table
    await expect(page.getByText("Base64Converter")).toBeVisible({ timeout: 10000 });
  });

  test("should show validation error when required parameter is missing", async ({ page }) => {
    // Type text
    // Type text BEFORE opening panel
    await page.getByTestId("chat-input").fill("hello");

    // Select CaesarConverter (has required caesar_offset param)
    await selectConverter(page, "CaesarConverter");

    // Parameters section should be visible with empty required field
    await expect(page.getByText("Parameters")).toBeVisible();
    await expect(page.getByTestId("param-caesar_offset")).toBeVisible();

    // Click Preview without filling required param
    await page.getByTestId("converter-preview-btn").click();

    // Red "Required" validation text should appear
    await expect(page.getByText("Required")).toBeVisible();
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
    await page.getByTitle("Attack History").click();

    // The converter badge should be visible in the attack table
    await expect(page.getByText("Base64Converter")).toBeVisible({ timeout: 10000 });
  });
});

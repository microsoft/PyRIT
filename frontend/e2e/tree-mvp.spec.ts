import { test, expect, type Page, type TestInfo } from "@playwright/test";

interface MockAttackSummary {
  attack_result_id: string;
  conversation_id: string;
  attack_type: string;
  target?: { target_registry_name?: string | null; target_type: string; model_name?: string | null } | null;
  converters: string[];
  outcome?: "success" | "failure" | "undetermined" | null;
  last_message_preview?: string | null;
  message_count: number;
  related_conversation_ids: string[];
  labels: Record<string, string>;
  created_at: string;
  updated_at: string;
}

const ATTACK_ID = "atk-tree-mvp";
const MAIN_CONVERSATION_ID = "conv-main";
const BRANCH_CONVERSATION_ID = "conv-branch";
const TREE_ID = "tree-mvp";

const ATTACK: MockAttackSummary = {
  attack_result_id: ATTACK_ID,
  conversation_id: MAIN_CONVERSATION_ID,
  attack_type: "ManualAttack",
  target: { target_registry_name: "OpenAIChatTarget::mvp", target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
  converters: [],
  outcome: "undetermined",
  last_message_preview: "Answer A",
  message_count: 8,
  related_conversation_ids: [BRANCH_CONVERSATION_ID],
  labels: { operator: "tree_mvp", operation: "tree_mvp", conversation_tree_id: TREE_ID },
  created_at: "2026-06-12T00:00:00Z",
  updated_at: "2026-06-12T00:01:00Z",
};

function piece(turn: number, role: string, value: string, pieceId: string) {
  return {
    turn_number: turn,
    role,
    pieces: [
      {
        piece_id: pieceId,
        original_value_data_type: "text",
        converted_value_data_type: "text",
        original_value: value,
        converted_value: value,
        scores: [],
        response_error: "none",
        original_prompt_id: pieceId,
        converter_identifiers: [],
      },
    ],
    created_at: "2026-06-12T00:00:00Z",
  };
}

const MAIN_MESSAGES = {
  conversation_id: MAIN_CONVERSATION_ID,
  messages: [
    piece(1, "user", "Root prompt", "main-p1"),
    piece(2, "assistant", "Shared answer", "main-p2"),
    piece(3, "user", "Follow A", "main-p3"),
    piece(4, "assistant", "Answer A", "main-p4"),
  ],
};

const BRANCH_MESSAGES = {
  conversation_id: BRANCH_CONVERSATION_ID,
  messages: [
    piece(1, "user", "Root prompt", "branch-p1"),
    piece(2, "assistant", "Shared answer", "branch-p2"),
    piece(3, "user", "Follow B", "branch-p3"),
    piece(4, "assistant", "Answer B", "branch-p4"),
  ],
};

async function mockMvpApis(page: Page) {
  await page.route(/\/api\/auth\/config/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ enabled: false }) });
  });
  await page.route(/\/api\/version/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ version: "test", default_labels: { operator: "tree_mvp", operation: "tree_mvp" } }),
    });
  });
  await page.route(/\/api\/health/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
  });
  await page.route(/\/api\/labels/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ source: "attacks", labels: {} }) });
  });
  await page.route(/\/api\/attacks\/attack-options/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ attack_types: ["ManualAttack"] }) });
  });
  await page.route(/\/api\/attacks\/converter-options/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ converter_types: [] }) });
  });
  await page.route(/\/api\/converters\/catalog(?:\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            converter_type: "Base64Converter",
            supported_input_types: ["text"],
            supported_output_types: ["text"],
            is_llm_based: false,
            description: "Encode text as base64.",
            parameters: [
              {
                name: "encoding_func",
                type_name: "Literal['b64encode', 'urlsafe_b64encode']",
                required: false,
                default_value: "b64encode",
                choices: ["b64encode", "urlsafe_b64encode"],
                description: "Encoding function",
              },
            ],
          },
        ],
      }),
    });
  });
  await page.route(/\/api\/converters(?:\?|$)/, async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ converter_id: "configured-base64", converter_type: "Base64Converter" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [{ converter_id: "base64", converter_type: "Base64Converter", display_name: "Base64" }] }),
    });
  });
  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}$`), async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(ATTACK) });
  });
  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}/conversations$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        attack_result_id: ATTACK_ID,
        main_conversation_id: MAIN_CONVERSATION_ID,
        conversations: [
          { conversation_id: MAIN_CONVERSATION_ID, message_count: 4, last_message_preview: "Answer A" },
          { conversation_id: BRANCH_CONVERSATION_ID, message_count: 4, last_message_preview: "Answer B" },
        ],
      }),
    });
  });
  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}/messages`), async (route) => {
    const url = new URL(route.request().url());
    const conversationId = url.searchParams.get("conversation_id");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(conversationId === BRANCH_CONVERSATION_ID ? BRANCH_MESSAGES : MAIN_MESSAGES),
    });
  });
  await page.route(/\/api\/attacks(?:\?|$)/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [ATTACK], pagination: { limit: 25, has_more: false, next_cursor: null, prev_cursor: null } }),
    });
  });
}

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
}

test.describe("Tree UI MVP acceptance", () => {
  test.beforeEach(async ({ page }) => {
    await mockMvpApis(page);
  });

  test("opens the loaded Chat attack as a merged tree with path chat", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByTitle("Attack History").click();
    await expect(page.getByTestId(`attack-row-${ATTACK_ID}`)).toBeVisible({ timeout: 10_000 });
    await page.getByTestId(`open-attack-${ATTACK_ID}`).click();
    await expect(page.getByText("Shared answer")).toBeVisible({ timeout: 10_000 });

    await page.getByTestId("open-chat-attack-as-tree-btn").click();
    await expect(page.locator("[data-tree-path-chat-pane]")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("main")).toContainText("Root prompt");
    await expect(page.getByText("Follow A")).toBeVisible();
    await expect(page.getByText("Follow B")).toBeVisible();
    await expect(page.locator("[data-tree-path-chat-splitter]")).toBeVisible();
    await expect(page.locator("main")).not.toContainText(/\bSend\b|coming later|future release/i);
    await screenshot(page, testInfo, "chat-open-merged-tree");
  });

  test("adding a follow-up prompt creates a pending response", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByTitle("Attack History").click();
    await page.getByTestId(`open-attack-as-tree-${ATTACK_ID}`).click();
    await expect(page.getByText("Shared answer")).toBeVisible({ timeout: 10_000 });

    await page.locator("[data-tree-node-id]").filter({ hasText: "Shared answer" }).locator('button[aria-label="Focus in path chat"]').first().click();
    await page.getByRole("textbox", { name: "Follow-up prompt" }).fill("New prompt from path chat");
    await page.getByRole("button", { name: "Run" }).click();
    await expect(page.locator("[data-tree-path-chat-pane]")).toContainText("New prompt from path chat");
    await expect(page.locator("[data-tree-path-chat-pane]")).toContainText("Pending response");
    await screenshot(page, testInfo, "pending-response-follow-up");
  });

  test("attempt fan can be pruned to the picked path", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByTitle("Attack History").click();
    await page.getByTestId(`open-attack-as-tree-${ATTACK_ID}`).click();
    await expect(page.getByText("Shared answer")).toBeVisible({ timeout: 10_000 });

    await page.locator('button[aria-label="Fan out response attempts"]').first().click();
    await page.getByRole("spinbutton", { name: "Attempt count" }).fill("3");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.locator("main")).toContainText(/3 variants/);
    await page.locator('button[aria-label="Pick this attempt"]').first().click();
    await page.locator('button[aria-label^="Prune to picked slot"]').click();
    await page.getByRole("button", { name: /^Prune$/ }).click();
    await expect(page.locator("main")).not.toContainText(/3 variants/);
    await screenshot(page, testInfo, "pruned-fan");
  });

  test("converter insertion creates a visible transform branch with direct baseline", async ({ page }, testInfo) => {
    await page.goto("/");
    await page.getByTitle("Attack History").click();
    await page.getByTestId(`open-attack-as-tree-${ATTACK_ID}`).click();
    await expect(page.getByText("Follow A")).toBeVisible({ timeout: 10_000 });

    await page.getByRole("button", { name: "Insert after user turn" }).first().click();
    await page.getByRole("menuitem", { name: "Append converter" }).click();
    await expect(page.locator("[data-tree-node-id]").filter({ hasText: "Choose converter" }).first()).toBeVisible();
    await expect(page.getByText("Answer A")).toBeVisible();
    await expect(page.getByText("Pending response")).toBeVisible();

    await page.getByRole("button", { name: "Choose converter" }).click();
    await page.getByRole("menuitem", { name: "Configure converter..." }).click();
    await page.getByRole("combobox", { name: "Converter type" }).selectOption("Base64Converter");
    await page.getByTestId("param-encoding_func").selectOption("urlsafe_b64encode");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.locator("[data-tree-node-id]").filter({ hasText: "Base64Converter" }).first()).toBeVisible();
    await screenshot(page, testInfo, "converter-transform-branch");
  });
});

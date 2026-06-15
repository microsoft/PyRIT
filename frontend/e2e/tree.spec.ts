import { test, expect, type Page } from "@playwright/test";

interface MockAttackSummary {
  attack_result_id: string;
  conversation_id: string;
  attack_type: string;
  target?: { target_registry_name?: string | null; target_type: string; endpoint?: string | null; model_name?: string | null } | null;
  converters: string[];
  outcome?: "success" | "failure" | "undetermined" | null;
  last_message_preview?: string | null;
  message_count: number;
  related_conversation_ids: string[];
  labels: Record<string, string>;
  created_at: string;
  updated_at: string;
}

const TREE_ID = "tree-e2e-1";
const ATTACK_ID = "atk-tree-e2e";
const CONVERSATION_ID = "conv-tree-e2e";

const ATTACK: MockAttackSummary = {
  attack_result_id: ATTACK_ID,
  conversation_id: CONVERSATION_ID,
  attack_type: "ManualAttack",
  target: { target_registry_name: "OpenAIChatTarget::target1234", target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
  converters: [],
  outcome: "undetermined",
  last_message_preview: "Seed assistant follow-up response",
  message_count: 4,
  related_conversation_ids: [],
  labels: { operator: "tree_e2e", operation: "tree_ui", conversation_tree_id: TREE_ID },
  created_at: "2026-06-12T00:00:00Z",
  updated_at: "2026-06-12T00:01:00Z",
};

const ATTACK_WITHOUT_TARGET: MockAttackSummary = {
  ...ATTACK,
  target: null,
};

let activeAttack: MockAttackSummary = ATTACK;

const MESSAGES_RESPONSE = {
  conversation_id: CONVERSATION_ID,
  messages: [
    message(1, "user", "Seed root prompt", "p1"),
    message(2, "assistant", "Seed assistant response", "p2"),
    message(3, "user", "Seed follow-up prompt", "p3"),
    message(4, "assistant", "Seed assistant follow-up response", "p4"),
  ],
};

function message(turn: number, role: string, value: string, pieceId: string) {
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

async function mockTreeApis(page: Page) {
  await page.route(/\/api\/auth\/config/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ enabled: false }) });
  });
  await page.route(/\/api\/version/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ version: "test", default_labels: { operator: "tree_e2e", operation: "tree_ui" } }),
    });
  });
  await page.route(/\/api\/health/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
  });
  await page.route(/\/api\/labels/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ source: "attacks", labels: { operator: ["tree_e2e"], operation: ["tree_ui"] } }),
    });
  });
  await page.route(/\/api\/attacks\/attack-options/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ attack_types: ["ManualAttack"] }) });
  });
  await page.route(/\/api\/attacks\/converter-options/, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ converter_types: [] }) });
  });
  await page.route(/\/api\/converters(?:\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [{ converter_id: "base64", converter_type: "Base64Converter", display_name: "Base64" }] }),
    });
  });
  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}$`), async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(activeAttack) });
  });
  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}/conversations$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        attack_result_id: ATTACK_ID,
        main_conversation_id: CONVERSATION_ID,
        conversations: [
          { conversation_id: CONVERSATION_ID, message_count: 4, last_message_preview: "Seed assistant follow-up response" },
        ],
      }),
    });
  });
  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}/messages`), async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MESSAGES_RESPONSE) });
  });
  await page.route(/\/api\/attacks(?:\?|$)/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const url = new URL(route.request().url());
    const labels = url.searchParams.getAll("label");
    const wantsTree = labels.includes(`conversation_tree_id:${TREE_ID}`);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: wantsTree || labels.length === 0 ? [activeAttack] : [],
        pagination: { limit: Number(url.searchParams.get("limit") ?? 25), has_more: false, next_cursor: null, prev_cursor: null },
      }),
    });
  });
}

async function openSeedTree(page: Page) {
  await page.getByTitle("Attack History").click();
  await page.waitForTimeout(300);
  const discard = page.getByRole("button", { name: /Discard and continue/i });
  if (await discard.isVisible().catch(() => false)) {
    await discard.click();
  }
  const row = page.getByTestId(`attack-row-${ATTACK_ID}`);
  await expect(row).toBeVisible({ timeout: 10_000 });
  await page.getByTestId(`open-attack-as-tree-${ATTACK_ID}`).click();
  if (await discard.isVisible().catch(() => false)) {
    await discard.click();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await page.getByTestId(`open-attack-as-tree-${ATTACK_ID}`).click();
  }
  await expect(page.getByText("Seed assistant response")).toBeVisible({ timeout: 10_000 });
}

test.describe("Tree UI", () => {
  test.beforeEach(async ({ page }) => {
    activeAttack = ATTACK;
    await mockTreeApis(page);
  });

  test("opens history attack as a tree and reconstructs response previews", async ({ page }) => {
    await page.goto("/");
    await page.getByTitle("Tree View").click();
    await expect(page.getByText(/No tree loaded/i)).toBeVisible();

    await page.getByTitle("Attack History").click();
    await expect(page.getByTestId(`attack-row-${ATTACK_ID}`)).toBeVisible({ timeout: 10_000 });
    await page.getByTestId(`open-attack-as-tree-${ATTACK_ID}`).click();

    await expect(page).toHaveURL(new RegExp(`conversation_tree_id=${TREE_ID}`));
    await expect(page.getByText("Seed root prompt").first()).toBeVisible();
    await expect(page.getByText("OpenAIChatTarget::target1234")).toBeVisible();
    await expect(page.getByText("Seed assistant response")).toBeVisible();
    await expect(page.getByText("Seed follow-up prompt")).toBeVisible();
    await expect(page.getByText("Seed assistant follow-up response")).toBeVisible();
  });

  test("adds a follow-up prompt after a response", async ({ page }) => {
    await page.goto("/");
    await openSeedTree(page);

    await page.getByRole("button", { name: "Add follow-up prompt" }).first().click();
    await expect(page.getByText("New prompt")).toBeVisible();
  });

  test("creates an attempt fan from a response", async ({ page }) => {
    await page.goto("/");
    await openSeedTree(page);

    await page.getByRole("button", { name: "Fan out response attempts" }).first().click();
    await expect(page.getByRole("dialog", { name: "Fan out response attempts" })).toBeVisible();
    await page.getByRole("spinbutton", { name: "Attempt count" }).fill("5");
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.locator("main")).toContainText(/axis:\s*attempt/);
    await expect(page.locator("main")).toContainText(/5 variants/i);
  });

  test("creates a converter fan from a response", async ({ page }) => {
    await page.goto("/");
    await openSeedTree(page);

    await page.getByRole("button", { name: "Compare converters" }).first().click();
    await expect(page.locator("main")).toContainText(/axis:\s*converter/);
    await expect(page.locator("main")).toContainText("New prompt");
  });

  test("shows no-target preflight before refreshing a recovered tree without a target", async ({ page }) => {
    activeAttack = ATTACK_WITHOUT_TARGET;
    await page.goto("/");
    await openSeedTree(page);

    await expect(page.getByText("No target")).toBeVisible();
    await page.getByRole("button", { name: "Edit root prompt" }).click();
    await page.getByRole("textbox", { name: "Prompt text" }).fill("Edited seed root prompt");
    await page.getByRole("button", { name: "Save" }).click();
    await page.getByRole("button", { name: /Refresh/ }).first().click();
    await expect(page.getByRole("dialog", { name: "No target selected" })).toBeVisible();
  });
});

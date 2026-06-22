import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers – mock backend API responses so URL-driven navigation can be
// exercised without a real backend. These tests are the only e2e coverage
// that asserts on the browser address bar (deep links, refresh, back/forward).
// ---------------------------------------------------------------------------

/** Attacks the mocked backend "knows about". Unknown ids return 404. */
const KNOWN_ATTACKS: Record<string, { outcome: "success" | "failure" }> = {
  "atk-1": { outcome: "success" },
  "atk-success": { outcome: "success" },
  "atk-failure": { outcome: "failure" },
};

/** Build an attack summary for the single-attack (getAttack) endpoint. */
function makeAttackSummary(attackResultId: string, outcome: "success" | "failure") {
  return {
    attack_result_id: attackResultId,
    conversation_id: `conv-${attackResultId}`,
    attack_type: "SingleTurnAttack",
    target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
    converters: [],
    outcome,
    last_message_preview: null,
    message_count: 1,
    related_conversation_ids: [],
    labels: { operator: "alice" },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

/** Build a single assistant message whose text identifies its attack. */
function makeMessage(attackResultId: string) {
  const text = `Loaded from ${attackResultId}`;
  return {
    turn_number: 1,
    role: "assistant",
    created_at: new Date().toISOString(),
    message_pieces: [
      {
        id: `piece-${attackResultId}`,
        original_value_data_type: "text",
        converted_value_data_type: "text",
        original_value: text,
        converted_value: text,
        scores: [],
        response_error: "none",
      },
    ],
  };
}

/** A row in the attack history list. */
function makeAttackRow(attackResultId: string, outcome: "success" | "failure") {
  return {
    attack_result_id: attackResultId,
    conversation_id: `conv-${attackResultId}`,
    attack_type: "SingleTurnAttack",
    target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
    converters: [],
    outcome,
    last_message_preview: null,
    message_count: 1,
    related_conversation_ids: [],
    labels: { operator: "alice" },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

const ATTACK_ROWS = [
  makeAttackRow("atk-success", "success"),
  makeAttackRow("atk-failure", "failure"),
];

/** Register every API mock the routing tests rely on. */
async function mockRoutingAPIs(page: Page) {
  // No active target configured – the chat ribbon shows "No target selected".
  await page.route(/\/api\/targets/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    });
  });

  await page.route(/\/api\/attacks\/attack-options/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ attack_types: ["SingleTurnAttack"] }),
    });
  });

  await page.route(/\/api\/attacks\/converter-options/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ converter_types: [] }),
    });
  });

  await page.route(/\/api\/labels/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ source: "attacks", labels: { operator: ["alice"], operation: [] } }),
    });
  });

  // Conversation list for the loaded attack's side panel.
  await page.route(/\/api\/attacks\/[^/]+\/conversations/, async (route) => {
    const attackResultId = new URL(route.request().url()).pathname.split("/")[3] ?? "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        attack_result_id: attackResultId,
        main_conversation_id: `conv-${attackResultId}`,
        conversations: [],
      }),
    });
  });

  // Messages for the loaded conversation – text encodes the attack id so a
  // test can prove the URL's attack actually hydrated the chat.
  await page.route(/\/api\/attacks\/[^/]+\/messages/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const attackResultId = new URL(route.request().url()).pathname.split("/")[3] ?? "";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        conversation_id: `conv-${attackResultId}`,
        messages: [makeMessage(attackResultId)],
      }),
    });
  });

  // Single attack (getAttack). Unknown ids 404 to drive the not-found UX.
  await page.route(/\/api\/attacks\/[^/]+$/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const attackResultId = new URL(route.request().url()).pathname.split("/")[3] ?? "";
    const known = KNOWN_ATTACKS[attackResultId];
    if (!known) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Attack not found" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(makeAttackSummary(attackResultId, known.outcome)),
    });
  });

  // Attacks list with outcome filtering (mirrors the real query contract).
  await page.route(/\/api\/attacks(?:\?|$)/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const outcome = new URL(route.request().url()).searchParams.get("outcome");
    const items = outcome ? ATTACK_ROWS.filter((a) => a.outcome === outcome) : ATTACK_ROWS;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items,
        pagination: { limit: 25, has_more: false, next_cursor: null, prev_cursor: null },
      }),
    });
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("URL-driven routing", () => {
  test.beforeEach(async ({ page }) => {
    await mockRoutingAPIs(page);
  });

  test("history filters round-trip through the URL and survive a reload", async ({ page }) => {
    await page.goto("/history");
    await expect(page.getByTestId("attacks-table")).toBeVisible({ timeout: 10_000 });

    // Select the "Success" outcome filter.
    await page.getByTestId("outcome-filter").click();
    await page.getByRole("option", { name: "Success" }).click();

    // The filter is reflected in the query string and the list narrows.
    await expect(page).toHaveURL(/[?&]outcome=success/);
    await expect(page.getByTestId("attack-row-atk-success")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-failure")).not.toBeVisible();

    // A full page reload restores the filter from the URL alone.
    await page.reload();
    await expect(page).toHaveURL(/[?&]outcome=success/);
    await expect(page.getByTestId("attack-row-atk-success")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("attack-row-atk-failure")).not.toBeVisible();
  });

  test("deep-links into an attack and hydrates its conversation", async ({ page }) => {
    await page.goto("/attacks/atk-1");

    // The router keeps the deep link, and the attack named by the URL – not
    // some default/empty conversation – drives the chat window.
    await expect(page).toHaveURL(/\/attacks\/atk-1$/);
    await expect(page.getByText("Loaded from atk-1")).toBeVisible({ timeout: 10_000 });
  });

  test("shows the not-found screen for an unknown attack id", async ({ page }) => {
    await page.goto("/attacks/bogus-id-12345");

    await expect(page.getByTestId("attack-not-found")).toBeVisible({ timeout: 10_000 });
  });

  test("browser back returns from an opened attack to history", async ({ page }) => {
    await page.goto("/history");
    await expect(page.getByTestId("attacks-table")).toBeVisible({ timeout: 10_000 });

    await page.getByTestId("attack-row-atk-success").click();
    await expect(page).toHaveURL(/\/attacks\/atk-success$/);

    await page.goBack();
    await expect(page).toHaveURL(/\/history$/);
    await expect(page.getByTestId("attacks-table")).toBeVisible();
  });
});

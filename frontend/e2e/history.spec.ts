import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers – mock backend API responses for history tests
// ---------------------------------------------------------------------------

/** A single attack summary matching the AttackSummary type. */
interface MockAttackSummary {
  attack_result_id: string;
  conversation_id: string;
  attack_type: string;
  target?: { target_type: string; endpoint?: string; model_name?: string } | null;
  converters: string[];
  outcome?: "success" | "failure" | "undetermined" | null;
  last_message_preview?: string | null;
  message_count: number;
  related_conversation_ids: string[];
  labels: Record<string, string>;
  created_at: string;
  updated_at: string;
}

function makeAttack(overrides: Partial<MockAttackSummary> & { attack_result_id: string }): MockAttackSummary {
  return {
    conversation_id: `conv-${overrides.attack_result_id}`,
    attack_type: "SingleTurnAttack",
    target: null,
    converters: [],
    outcome: "undetermined",
    last_message_preview: null,
    message_count: 0,
    related_conversation_ids: [],
    labels: {},
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

// Sample attacks with varied characteristics
const ATTACKS: MockAttackSummary[] = [
  makeAttack({
    attack_result_id: "atk-alice-a",
    attack_type: "SingleTurnAttack",
    target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
    outcome: "success",
    labels: { operator: "alice", operation: "test_a" },
    message_count: 3,
    last_message_preview: "Hello from alice",
  }),
  makeAttack({
    attack_result_id: "atk-bob-b",
    attack_type: "MultiTurnAttack",
    target: { target_type: "OpenAIImageTarget", model_name: "dall-e-3" },
    outcome: "failure",
    labels: { operator: "bob", operation: "test_b" },
    message_count: 5,
    last_message_preview: "Hello from bob",
  }),
  makeAttack({
    attack_result_id: "atk-alice-b",
    attack_type: "SingleTurnAttack",
    target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
    outcome: "undetermined",
    labels: { operator: "alice", operation: "test_b" },
    message_count: 1,
  }),
  makeAttack({
    attack_result_id: "atk-bob-a",
    attack_type: "MultiTurnAttack",
    target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
    outcome: "success",
    labels: { operator: "bob", operation: "test_a" },
    message_count: 2,
    last_message_preview: "Hello again from bob",
  }),
];

/** Generate many attacks for pagination testing. */
function generatePaginatedAttacks(count: number): MockAttackSummary[] {
  return Array.from({ length: count }, (_, i) =>
    makeAttack({
      attack_result_id: `atk-page-${String(i).padStart(3, "0")}`,
      attack_type: i % 2 === 0 ? "SingleTurnAttack" : "MultiTurnAttack",
      outcome: "undetermined",
      labels: { operator: "paginator" },
      message_count: 1,
    }),
  );
}

/** Build the standard mock response for the attacks list API. */
function buildAttacksListResponse(items: MockAttackSummary[], hasMore = false, nextCursor: string | null = null) {
  return {
    items,
    pagination: { limit: 25, has_more: hasMore, next_cursor: nextCursor, prev_cursor: null },
  };
}

/** Register all API mocks needed for the history view. */
async function mockHistoryAPIs(
  page: Page,
  opts: {
    attacks?: MockAttackSummary[];
    attackTypes?: string[];
    converterTypes?: string[];
    operatorLabels?: string[];
    operationLabels?: string[];
    /** If provided, use a custom handler for the attacks list endpoint. */
    attacksRouteHandler?: (route: import("@playwright/test").Route) => Promise<void>;
  } = {},
) {
  const {
    attacks = ATTACKS,
    attackTypes = ["SingleTurnAttack", "MultiTurnAttack"],
    converterTypes = [],
    operatorLabels = ["alice", "bob"],
    operationLabels = ["test_a", "test_b"],
  } = opts;

  // Attack options
  await page.route(/\/api\/attacks\/attack-options/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ attack_types: attackTypes }),
    });
  });

  // Converter options
  await page.route(/\/api\/attacks\/converter-options/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ converter_types: converterTypes }),
    });
  });

  // Labels
  await page.route(/\/api\/labels/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        source: "attacks",
        labels: {
          operator: operatorLabels,
          operation: operationLabels,
        },
      }),
    });
  });

  // Attacks list (supports custom handler for pagination tests)
  if (opts.attacksRouteHandler) {
    await page.route(/\/api\/attacks(?:\?|$)/, opts.attacksRouteHandler);
  } else {
    await page.route(/\/api\/attacks(?:\?|$)/, async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      const url = new URL(route.request().url());
      const attackType = url.searchParams.get("attack_type");
      const outcome = url.searchParams.get("outcome");
      const labelParams = url.searchParams.getAll("label");

      let filtered = [...attacks];
      if (attackType) {
        filtered = filtered.filter((a) => a.attack_type === attackType);
      }
      if (outcome) {
        filtered = filtered.filter((a) => a.outcome === outcome);
      }
      if (labelParams.length > 0) {
        filtered = filtered.filter((a) =>
          labelParams.every((lp) => {
            const [key, val] = lp.split(":");
            return a.labels[key] === val;
          }),
        );
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(buildAttacksListResponse(filtered)),
      });
    });
  }
}

/** Navigate to the Attack History view. */
async function goToHistory(page: Page) {
  await page.goto("/");
  await page.getByTitle("Attack History").click();
  await expect(page.getByTestId("attacks-table")).toBeVisible({ timeout: 10_000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Attack History Filters", () => {
  test("should filter by attack type", async ({ page }) => {
    await mockHistoryAPIs(page);
    await goToHistory(page);

    // All 4 attacks visible initially
    await expect(page.getByTestId("attack-row-atk-alice-a")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-bob-b")).toBeVisible();

    // Open the attack class dropdown and select SingleTurnAttack
    const dropdown = page.getByTestId("attack-class-filter");
    await dropdown.click();
    await page.getByRole("option", { name: "SingleTurnAttack" }).click();

    // Only SingleTurnAttack attacks should be visible
    await expect(page.getByTestId("attack-row-atk-alice-a")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-alice-b")).toBeVisible();
    // MultiTurnAttack attacks should not be visible
    await expect(page.getByTestId("attack-row-atk-bob-b")).not.toBeVisible();
    await expect(page.getByTestId("attack-row-atk-bob-a")).not.toBeVisible();
  });

  test("should filter by outcome", async ({ page }) => {
    await mockHistoryAPIs(page);
    await goToHistory(page);

    // Open outcome dropdown and select "Success"
    const outcomeDropdown = page.getByTestId("outcome-filter");
    await outcomeDropdown.click();
    await page.getByRole("option", { name: "Success" }).click();

    // Only success attacks
    await expect(page.getByTestId("attack-row-atk-alice-a")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-bob-a")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-bob-b")).not.toBeVisible();
    await expect(page.getByTestId("attack-row-atk-alice-b")).not.toBeVisible();
  });

  test("should filter by operator label", async ({ page }) => {
    await mockHistoryAPIs(page);
    await goToHistory(page);

    // Open operator dropdown and select "bob"
    const operatorDropdown = page.getByTestId("operator-filter");
    await operatorDropdown.click();
    await page.getByRole("option", { name: "bob" }).click();

    // Only bob's attacks
    await expect(page.getByTestId("attack-row-atk-bob-b")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-bob-a")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-alice-a")).not.toBeVisible();
    await expect(page.getByTestId("attack-row-atk-alice-b")).not.toBeVisible();
  });

  test("should reset filters", async ({ page }) => {
    await mockHistoryAPIs(page);
    await goToHistory(page);

    // Apply a filter first
    const outcomeDropdown = page.getByTestId("outcome-filter");
    await outcomeDropdown.click();
    await page.getByRole("option", { name: "Failure" }).click();

    // Only failure attack visible
    await expect(page.getByTestId("attack-row-atk-bob-b")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-alice-a")).not.toBeVisible();

    // Click Reset
    await page.getByTestId("reset-filters-btn").click();

    // All attacks should return
    await expect(page.getByTestId("attack-row-atk-alice-a")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-bob-b")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-alice-b")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-bob-a")).toBeVisible();
  });

  test("should paginate attacks", async ({ page }) => {
    const allAttacks = generatePaginatedAttacks(30);
    const page1 = allAttacks.slice(0, 25);
    const page2 = allAttacks.slice(25);

    await mockHistoryAPIs(page, {
      attacks: allAttacks,
      attacksRouteHandler: async (route) => {
        if (route.request().method() !== "GET") {
          await route.continue();
          return;
        }
        const url = new URL(route.request().url());
        const cursor = url.searchParams.get("cursor");

        if (cursor === "page2") {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(buildAttacksListResponse(page2, false, null)),
          });
        } else {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(buildAttacksListResponse(page1, true, "page2")),
          });
        }
      },
    });

    await goToHistory(page);

    // Page 1 visible
    await expect(page.getByText("Page 1")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-page-000")).toBeVisible();

    // Prev/First should be disabled on page 1
    await expect(page.getByTestId("prev-page-btn")).toBeDisabled();
    // Next should be enabled (has_more = true)
    await expect(page.getByTestId("next-page-btn")).toBeEnabled();

    // Go to page 2
    await page.getByTestId("next-page-btn").click();
    await expect(page.getByText("Page 2")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-page-025")).toBeVisible({ timeout: 5_000 });

    // First button should be enabled now
    await expect(page.getByTestId("prev-page-btn")).toBeEnabled();
    // Next should be disabled (no more pages)
    await expect(page.getByTestId("next-page-btn")).toBeDisabled();

    // Go back to first page
    await page.getByTestId("prev-page-btn").click();
    await expect(page.getByText("Page 1")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-page-000")).toBeVisible({ timeout: 5_000 });
  });
});

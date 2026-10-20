import { test, expect, type Page } from "./_fixtures";

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
  operator?: string | null;
  operation?: string | null;
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
    operator: null,
    operation: null,
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
    operator: "alice",
    operation: "test_a",
    message_count: 3,
    last_message_preview: "Hello from alice",
  }),
  makeAttack({
    attack_result_id: "atk-bob-b",
    attack_type: "MultiTurnAttack",
    target: { target_type: "OpenAIImageTarget", model_name: "dall-e-3" },
    outcome: "failure",
    operator: "bob",
    operation: "test_b",
    message_count: 5,
    last_message_preview: "Hello from bob",
  }),
  makeAttack({
    attack_result_id: "atk-alice-b",
    attack_type: "SingleTurnAttack",
    target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
    outcome: "undetermined",
    operator: "alice",
    operation: "test_b",
    message_count: 1,
  }),
  makeAttack({
    attack_result_id: "atk-bob-a",
    attack_type: "MultiTurnAttack",
    target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
    outcome: "success",
    operator: "bob",
    operation: "test_a",
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
      operator: "paginator",
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
    /** Extra label keys surfaced as "key:value" filter options. */
    labels?: Record<string, string[]>;
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
    labels = {},
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
        labels,
        operators: operatorLabels,
        operations: operationLabels,
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
      const attackTypeParams = url.searchParams.getAll("attack_types");
      const outcome = url.searchParams.get("outcome");
      const operatorParams = url.searchParams.getAll("operator");
      const operationParams = url.searchParams.getAll("operation");
      const labelParams = url.searchParams.getAll("label");

      let filtered = [...attacks];
      if (attackTypeParams.length > 0) {
        filtered = filtered.filter((a) => attackTypeParams.includes(a.attack_type));
      }
      if (outcome) {
        filtered = filtered.filter((a) => a.outcome === outcome);
      }
      if (operatorParams.length > 0) {
        filtered = filtered.filter((a) => a.operator != null && operatorParams.includes(a.operator));
      }
      if (operationParams.length > 0) {
        filtered = filtered.filter((a) => a.operation != null && operationParams.includes(a.operation));
      }
      if (labelParams.length > 0) {
        // Group repeated label keys into OR-sets; combine across keys with AND.
        const grouped = new Map<string, string[]>();
        for (const lp of labelParams) {
          const [key, val] = lp.split(":");
          if (!key) continue;
          const bucket = grouped.get(key) ?? [];
          bucket.push(val ?? "");
          grouped.set(key, bucket);
        }
        filtered = filtered.filter((a) =>
          Array.from(grouped.entries()).every(([key, vals]) => vals.includes(a.labels[key] ?? "")),
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
  await page.getByTitle("History").click();
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

    // Open the attack type combobox. Multiselect Combobox renders items as
    // role="menuitemcheckbox", not role="option".
    const dropdown = page.getByTestId("attack-type-filter");
    await dropdown.click();
    await page.getByRole("menuitemcheckbox", { name: "SingleTurnAttack" }).click();

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

    // Open the operator combobox. Multiselect Combobox renders items as
    // role="menuitemcheckbox", not role="option".
    const operatorDropdown = page.getByTestId("operator-filter");
    await operatorDropdown.click();
    await page.getByRole("menuitemcheckbox", { name: "bob" }).click();

    // Only bob's attacks
    await expect(page.getByTestId("attack-row-atk-bob-b")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-bob-a")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-alice-a")).not.toBeVisible();
    await expect(page.getByTestId("attack-row-atk-alice-b")).not.toBeVisible();
  });

  test("should keep the virtualized label list bounded and let Home and End reach every filtered label", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 800 });
    await mockHistoryAPIs(page, {
      labels: {
        team: Array.from({ length: 60 }, (_, i) => String(i).padStart(3, "0")),
        env: Array.from({ length: 40 }, (_, i) => String(i).padStart(3, "0")),
      },
    });
    await goToHistory(page);

    const labelFilter = page.getByTestId("label-filter");
    await labelFilter.click();

    // Narrow the list to the 60 team labels; the popup stays open while typing.
    await labelFilter.pressSequentially("team:", { delay: 60 });
    const surface = page.getByTestId("label-list-scroll-surface");
    await expect(surface).toBeVisible();
    // The multiselect popup root carries the option roles.
    const popup = page.getByTestId("label-list-scroll-surface").locator("xpath=ancestor-or-self::*[@role='menu'][1]");

    // The popup is bounded by the inner scroll surface instead of growing to
    // whatever height the viewport allows.
    const popupBox = await popup.boundingBox();
    expect(popupBox).not.toBeNull();
    expect(popupBox!.height).toBeLessThanOrEqual(320);

    // Virtualization really mounts only a window of the 60 matches.
    const mountedOptions = await surface.locator("[data-label-index]").count();
    expect(mountedOptions).toBeGreaterThan(0);
    expect(mountedOptions).toBeLessThan(60);

    const activeOptionText = () =>
      popup.locator("[data-activedescendant]").evaluate((el) => el.textContent?.trim() ?? "");
    const assertOptionWithinSurface = async (index: number) => {
      const optionBox = await surface.locator(`[data-label-index="${index}"]`).boundingBox();
      const surfaceBox = await surface.boundingBox();
      expect(optionBox).not.toBeNull();
      expect(surfaceBox).not.toBeNull();
      expect(optionBox!.y).toBeGreaterThanOrEqual(surfaceBox!.y - 1);
      expect(optionBox!.y + optionBox!.height).toBeLessThanOrEqual(surfaceBox!.y + surfaceBox!.height + 1);
    };

    // End reaches the true last match, not the last mounted option, and scrolls
    // it into view.
    await labelFilter.press("End");
    await expect.poll(activeOptionText).toBe("team:059");
    await assertOptionWithinSurface(59);

    // Home reaches the first match even from the bottom of the list.
    await labelFilter.press("Home");
    await expect.poll(activeOptionText).toBe("team:000");
    await assertOptionWithinSurface(0);

    // Keyboard selection still works after the jump: Enter selects the active
    // option and the label filter lands in the shareable URL state.
    await labelFilter.press("End");
    await expect.poll(activeOptionText).toBe("team:059");
    await labelFilter.press("Enter");
    await labelFilter.press("Escape");
    await expect(page).toHaveURL(/label=team%3A059/);

    // A second, mouse driven selection joins the first in the URL state.
    await labelFilter.click();
    await labelFilter.pressSequentially("team:", { delay: 60 });
    await page.getByRole("menuitemcheckbox", { name: "team:000" }).click();
    await labelFilter.press("Escape");
    await expect(page).toHaveURL(/label=team%3A059&label=team%3A000/);

    // Both selections stay checked while the filtered list is virtualized.
    await labelFilter.click();
    await labelFilter.pressSequentially("team:", { delay: 60 });
    await labelFilter.press("End");
    await expect.poll(activeOptionText).toBe("team:059");
    await expect(surface.locator('[data-label-index="59"]')).toHaveAttribute("aria-checked", "true");
    await labelFilter.press("Home");
    await expect.poll(activeOptionText).toBe("team:000");
    await expect(surface.locator('[data-label-index="0"]')).toHaveAttribute("aria-checked", "true");
  });

  test("should let the newest of two rapid label navigation keys win", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 800 });
    await mockHistoryAPIs(page, {
      labels: {
        team: Array.from({ length: 60 }, (_, i) => String(i).padStart(3, "0")),
        env: Array.from({ length: 40 }, (_, i) => String(i).padStart(3, "0")),
      },
    });
    await goToHistory(page);

    const labelFilter = page.getByTestId("label-filter");
    await labelFilter.click();
    await labelFilter.pressSequentially("team:", { delay: 60 });
    const surface = page.getByTestId("label-list-scroll-surface");
    await expect(surface).toBeVisible();
    const popup = surface.locator("xpath=ancestor-or-self::*[@role='menu'][1]");
    const activeOptionText = () =>
      popup.locator("[data-activedescendant]").evaluate((el) => el.textContent?.trim() ?? "");

    for (let round = 0; round < 5; round++) {
      // Re-anchor at the top, then press End and Home back to back so the
      // End activation is still retrying when Home lands.
      await labelFilter.press("Home");
      await page.waitForTimeout(400);
      await labelFilter.press("End");
      await labelFilter.press("Home");

      await expect.poll(activeOptionText).toBe("team:000");
      // The last key must still be the winner once every pending retry has
      // had time to fire.
      await page.waitForTimeout(900);
      await expect.poll(activeOptionText).toBe("team:000");
      expect(await surface.evaluate((el) => (el as HTMLElement).scrollTop)).toBeLessThanOrEqual(40);
    }
  });

  test("should keep a single filtered label fully visible", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 800 });
    await mockHistoryAPIs(page, {
      labels: {
        team: Array.from({ length: 60 }, (_, i) => String(i).padStart(3, "0")),
        env: Array.from({ length: 40 }, (_, i) => String(i).padStart(3, "0")),
      },
    });
    await goToHistory(page);

    const labelFilter = page.getByTestId("label-filter");
    await labelFilter.click();
    await labelFilter.pressSequentially("team:005", { delay: 60 });
    const surface = page.getByTestId("label-list-scroll-surface");
    await expect(surface).toBeVisible();

    const only = surface.locator('[data-label-index="0"]');
    await expect(only).toHaveText("team:005");

    // The whole option row must fit inside the scrolling surface; a block
    // container let the virtualizer spacers halve the visible height.
    const optionBox = await only.boundingBox();
    const surfaceBox = await surface.boundingBox();
    expect(optionBox).not.toBeNull();
    expect(surfaceBox).not.toBeNull();
    expect(optionBox!.height).toBeGreaterThanOrEqual(30);
    expect(optionBox!.y + optionBox!.height).toBeLessThanOrEqual(surfaceBox!.y + surfaceBox!.height + 1);
  });

  test("should preserve every label search character during rapid URL updates", async ({ page }) => {
    await mockHistoryAPIs(page, {
      labels: {
        team: Array.from({ length: 60 }, (_, i) => String(i).padStart(3, "0")),
      },
    });
    await goToHistory(page);

    const labelFilter = page.getByTestId("label-filter");
    await labelFilter.click();
    for (const search of ["team:005", "team:059", "team:001", "team:020"]) {
      await labelFilter.fill("");
      await labelFilter.pressSequentially(search);
      await expect(labelFilter).toHaveValue(search);
      await expect.poll(() => new URL(page.url()).searchParams.get("labelSearch")).toBe(search);
      await expect(page.getByRole("menuitemcheckbox", { name: search, exact: true })).toBeVisible();
    }
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
    const resetButton = page.getByTestId("reset-filters-btn");
    await resetButton.click();

    // All attacks should return
    await expect(page.getByTestId("attack-row-atk-alice-a")).toBeVisible({ timeout: 5_000 });
    await expect(page.getByTestId("attack-row-atk-bob-b")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-alice-b")).toBeVisible();
    await expect(page.getByTestId("attack-row-atk-bob-a")).toBeVisible();

    // Reset disables itself without dropping keyboard focus.
    await expect(resetButton).toBeFocused();
    await expect(resetButton).toHaveAttribute("aria-disabled", "true");
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

test.describe("Attack History empty state", () => {
  test("guides keyboard users to target configuration and preserves Back navigation", async ({ page }) => {
    await mockHistoryAPIs(page, { attacks: [] });
    await page.route(/\/api\/targets(?:\?|$)/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [],
          pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
        }),
      });
    });

    await page.goto("/history");

    const configureTargetButton = page.getByRole("button", { name: "Configure target" });
    await expect(configureTargetButton).toBeVisible();
    await configureTargetButton.focus();
    await expect(configureTargetButton).toBeFocused();
    await configureTargetButton.press("Enter");

    await expect(page).toHaveURL(/\/registry\/targets$/);
    await expect(page.getByRole("heading", { level: 1, name: "Target Registry" })).toBeVisible();

    await page.goBack();
    await expect(page).toHaveURL(/\/history\/attacks$/);
    await expect(page.getByRole("button", { name: "Configure target" })).toBeVisible();
  });
});

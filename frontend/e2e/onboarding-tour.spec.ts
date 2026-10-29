import { expect, test, type Page } from "./_fixtures";

import type { RegisteredScenario } from "../src/types";

import { makeTarget } from "./_targets";

const TOUR_SCENARIO: RegisteredScenario = {
  scenario_name: "tour-scenario",
  scenario_type: "TourScenario",
  scenario_version: 1,
  description: "Mock scenario for tour navigation.",
  description_markdown: "Mock scenario for tour navigation.",
  all_techniques: ["prompt_sending"],
  aggregate_techniques: [],
  aggregate_technique_expansions: {},
  default_technique: "prompt_sending",
  default_techniques: ["prompt_sending"],
  technique_summaries: [
    { name: "prompt_sending", description: null, tags: [] },
  ],
  default_datasets: [],
  baseline_policy: "disabled",
  include_baseline_by_default: false,
  supported_parameters: [],
  supported_parameter_names: [
    "objective_target", "scenario_techniques", "dataset_config", "memory_labels",
    "max_concurrency", "max_retries", "include_baseline",
  ],
  default_run_size: {
    status: "unavailable",
    dataset_size: { kind: "indeterminate", detail: "Mock estimate only." },
    dataset_limit: { state: "scenario_default" },
    estimated_attack_count: null,
    components: [],
    datasets: [],
    note: "Mock estimate only.",
  },
};

/** Serves a one-scenario catalog so the detail route is reachable by click. */
async function mockScenarioCatalog(page: Page): Promise<void> {
  await page.route(/\/api\/datasets(?:\?|$)/, async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
  await page.route(/\/api\/scenarios\/catalog(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      json: {
        items: [TOUR_SCENARIO],
        pagination: {
          limit: 200,
          has_more: false,
          next_cursor: null,
          prev_cursor: null,
        },
      },
    });
  });
  await page.route(
    /\/api\/scenarios\/catalog\/[^/]+$/,
    async (route) => {
      await route.fulfill({ json: TOUR_SCENARIO });
    }
  );
  await page.route(
    /\/api\/scenarios\/catalog\/[^/]+\/estimate(?:\?.*)?$/,
    async (route) => {
      await route.fulfill({ json: TOUR_SCENARIO.default_run_size });
    }
  );
  await page.route(/\/api\/scenario-(?:presets|suites)(?:\?.*)?$/, async (route) => {
    await route.fulfill({ json: { source: "tour", items: [] } });
  });
}

async function seedLastUsedTarget(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const key = "pyrit.userPreferences.v1.local";
    if (localStorage.getItem(key) !== null) return;
    localStorage.setItem(key, JSON.stringify({
      targets: { adversarial: null },
      lastUsedTargets: {
        objective: { registryName: "tour-target", identifierHash: "tour-target-hash" },
        adversarial: null,
      },
      hiddenTargetRegistryNames: [],
      labels: {},
      theme: "system",
      chatMarkdown: false,
    }));
  });
}

test.describe("Onboarding tour", () => {
  test("guides a user with no last-used target through target selection", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Take a tour" }).click();

    const dialog = page.getByRole("alertdialog");
    await page.getByRole("navigation", { name: "Primary" })
      .getByRole("button", { name: "Registry", exact: true }).click();
    await expect(page).toHaveURL(/\/registry\/targets$/);
    await expect(page.getByRole("heading", { name: "Target Registry" })).toBeVisible();
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(dialog).toContainText("Use New attack to start a conversation");
    await expect(dialog).toContainText("Configure scan to choose an automated evaluation");
    await expect(dialog).toContainText("Recent work and saved operations are below");
    await expect(page.locator('[data-tour="home-workflows"]')).toBeVisible();

    await page
      .getByRole("button", { name: "New attack", exact: true })
      .click();
    await expect(page).toHaveURL(/\/attack\/chat$/);
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Use New attack to start a conversation");

    await dialog.getByRole("button", { name: "Back", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(dialog).toContainText(
      "The labels bar stays available across views, including scanner setup."
    );
    await expect(dialog).toContainText('Set "operator", "operation", and other labels here');
    await expect(dialog).toContainText("Existing runs keep their original labels.");
    await expect(page.getByRole("region", { name: "Default Labels" })).toBeVisible();

    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await page
      .getByRole("button", { name: "New attack", exact: true })
      .click();
    await expect(page).toHaveURL(/\/attack\/chat$/);
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/attack\/chat$/);
    await expect(dialog).toContainText(
      "Click Select a target in the chat ribbon to enable the message composer"
    );
    await expect(dialog).toContainText(
      "open the Target Registry to create one"
    );
    await expect(dialog).toContainText(
      "Saved chats automatically select their original registered target"
    );
    await expect(
      page.locator('[data-tour="chat-prerequisite"]').getByRole("combobox", { name: "Objective target" })
    ).toBeVisible();
    await expect(page.getByTestId("no-target-banner")).toHaveCount(0);
  });

  test("guides a user with a last-used target to the visible converter control", async ({
    page,
  }) => {
    await page.route(/\/api\/targets(?:\?.*)?$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            makeTarget({
              target_registry_name: "tour-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            }),
          ],
          pagination: {
            limit: 200,
            has_more: false,
            next_cursor: null,
            prev_cursor: null,
          },
        }),
      });
    });

    await seedLastUsedTarget(page);
    await page.goto("/");
    await expect(page.locator('[data-tour="home-workflows"]')).toBeVisible();

    await page.getByRole("button", { name: "Take a tour" }).click();
    const dialog = page.getByRole("alertdialog");
    await page.getByRole("navigation", { name: "Primary" })
      .getByRole("button", { name: "Registry", exact: true }).click();
    await expect(page).toHaveURL(/\/registry\/targets$/);
    await expect(page.getByRole("heading", { name: "Target Registry" })).toBeVisible();
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(dialog).toContainText("Use New attack to start a conversation");
    await expect(dialog).toContainText("Configure scan to choose an automated evaluation");
    await expect(page.locator('[data-tour="home-workflows"]')).toBeVisible();

    await page
      .getByRole("button", { name: "New attack", exact: true })
      .click();
    await expect(page).toHaveURL(/\/attack\/chat$/);
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Use New attack to start a conversation");

    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/attack\/chat$/);
    await expect(dialog).toContainText("Chat shows the message composer");
    await expect(dialog).toContainText("Toggle converter panel");
    await expect(page.getByRole("combobox", { name: "Chat target", exact: true })).toHaveValue("tour-target");
    await expect(page.getByRole("textbox", { name: "Type prompt here", exact: true })).toBeVisible();
    await expect(page.locator('[data-tour="converter-toggle"]')).toHaveAttribute(
      "aria-label",
      "Toggle converter panel"
    );
    await expect(page.getByTestId("no-target-banner")).toHaveCount(0);
    expect(await page.evaluate(() => JSON.parse(
      localStorage.getItem("pyrit.userPreferences.v1.local") ?? "{}",
    ).lastUsedTargets.objective.registryName)).toBe("tour-target");
  });

  test("selects a per-chat target during the tour without creating an objective default", async ({
    page,
  }) => {
    await page.route(/\/api\/targets(?:\?.*)?$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            makeTarget({
              target_registry_name: "tour-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            }),
          ],
          pagination: {
            limit: 200,
            has_more: false,
            next_cursor: null,
            prev_cursor: null,
          },
        }),
      });
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Take a tour" }).click();

    const dialog = page.getByRole("alertdialog");
    await page.getByRole("navigation", { name: "Primary" })
      .getByRole("button", { name: "Registry", exact: true }).click();

    await expect(page).toHaveURL(/\/registry\/targets$/);
    await expect(dialog).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Default objective target", exact: true })).toHaveCount(0);
    await expect(dialog).toContainText("Registry is where you manage targets and converters");

    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.locator('[data-tour="home-workflows"]')).toBeVisible();
    await expect(dialog).toContainText("Use New attack to start a conversation");
    await page.getByRole("button", { name: "New attack", exact: true }).click();
    await expect(page).toHaveURL(/\/attack\/chat$/);
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/attack\/chat$/);
    await expect(dialog).toContainText("Click Select a target in the chat ribbon");
    await page.getByRole("combobox", { name: "Objective target", exact: true }).selectOption("tour-target");
    await expect(page.getByRole("textbox", { name: "Type prompt here", exact: true })).toBeVisible();
    await expect(page.locator('[data-tour="converter-toggle"]')).toHaveAttribute(
      "aria-label",
      "Toggle converter panel"
    );
    await expect(page.getByTestId("no-target-banner")).toHaveCount(0);
    expect(await page.evaluate(() => JSON.parse(
      localStorage.getItem("pyrit.userPreferences.v1.local") ?? "{}",
    ).lastUsedTargets)).toBeUndefined();
    expect(await page.evaluate(() => JSON.parse(
      localStorage.getItem("pyrit.userPreferences.v1.local") ?? "{}",
    ).targets?.objective)).toBeUndefined();
  });

  test("recovers the Scanner step when Next is pressed from a scenario detail route", async ({
    page,
  }) => {
    await mockScenarioCatalog(page);

    // Seed the history stack before starting the tour. The spotlight overlay
    // swallows every click outside the current step's target, so once the tour
    // is running the browser Back button is the only way onto another route.
    await page.goto("/");
    await page.getByRole("button", { name: "Scanner", exact: true }).click();
    await expect(page).toHaveURL(/\/scanner\/launch$/);
    await page
      .getByRole("link", { name: TOUR_SCENARIO.scenario_name, exact: true })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/scanner/launch/${TOUR_SCENARIO.scenario_name}$`)
    );
    await page.getByRole("button", { name: "Home", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);

    await page.getByRole("button", { name: "Take a tour" }).click();

    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page).toHaveURL(/\/attack\/chat$/);

    // Walk back onto the Scanner detail route. The Scanner view owns both the
    // catalog and the detail page, but only the catalog carries the anchor.
    await page.goBack();
    await expect(page).toHaveURL(/\/$/);
    await page.goBack();
    await expect(page).toHaveURL(
      new RegExp(`/scanner/launch/${TOUR_SCENARIO.scenario_name}$`)
    );
    await expect(page.locator('[data-tour="scanner-catalog"]')).toHaveCount(0);

    // `currentView` already equals `scenarios`, so the view-change effect never
    // fires. The hook has to notice the missing anchor, return to the catalog,
    // and advance after paint rather than stranding the tooltip.
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/scanner\/launch$/);
    await expect(page.locator('[data-tour="scanner-catalog"]')).toBeVisible();
    await expect(dialog).toContainText("Scanner runs a whole campaign for you");
  });
});

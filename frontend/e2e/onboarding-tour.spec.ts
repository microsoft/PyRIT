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
}

test.describe("Onboarding tour", () => {
  test("guides a user with no objective default through target selection", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Take a tour" }).click();

    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(dialog).toContainText(
      "Select a target from the Chat dropdown"
    );
    await expect(dialog).toContainText("save objective and adversarial defaults");
    await expect(dialog).toContainText("for your account in this browser");
    await expect(page.locator('[data-tour="target-card"]')).toBeVisible();

    await page
      .getByRole("button", { name: "Configure a target", exact: true })
      .click();
    await expect(page).toHaveURL(/\/registry\/targets$/);
    await expect(
      page.getByRole("heading", { name: "Target Registry" })
    ).toBeVisible();
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(
      "Select a target from the Chat dropdown"
    );

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
      .getByRole("button", { name: "Configure a target", exact: true })
      .click();
    await expect(page).toHaveURL(/\/registry\/targets$/);
    await expect(dialog).toBeVisible();

    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/chat$/);
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
      page.locator('[data-tour="chat-prerequisite"]').getByRole("combobox", { name: "Chat target" })
    ).toBeVisible();
    await expect(page.getByTestId("no-target-banner")).toHaveCount(0);
  });

  test("guides a user with an objective default to the visible converter control", async ({
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
    await page
      .getByRole("button", { name: "Registry", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Target Registry" })
    ).toBeVisible();
    await page.getByRole("combobox", { name: "Default objective target", exact: true }).selectOption("tour-target");
    await page.getByRole("button", { name: "Home", exact: true }).click();
    await expect(page.getByTestId("home-target-active")).toContainText("gpt-4o");

    await page.getByRole("button", { name: "Take a tour" }).click();
    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(dialog).toContainText("default objective target for new chats and scanner runs");
    await expect(dialog).toContainText("change your objective or adversarial defaults");
    await expect(page.locator('[data-tour="target-card"]')).toBeVisible();

    await page
      .getByRole("button", { name: "Manage targets", exact: true })
      .click();
    await expect(page).toHaveURL(/\/registry\/targets$/);
    await expect(
      page.getByRole("heading", { name: "Target Registry" })
    ).toBeVisible();
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("default objective target for new chats and scanner runs");

    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/chat$/);
    await expect(dialog).toContainText("Chat shows the message composer");
    await expect(dialog).toContainText("Toggle converter panel");
    await expect(page.getByRole("textbox")).toBeVisible();
    await expect(page.locator('[data-tour="converter-toggle"]')).toHaveAttribute(
      "aria-label",
      "Toggle converter panel"
    );
    await expect(page.getByTestId("no-target-banner")).toHaveCount(0);
  });

  test("adapts step 4 when an objective default is saved during step 3", async ({
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
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await page
      .getByRole("button", { name: "Configure a target", exact: true })
      .click();

    await expect(page).toHaveURL(/\/registry\/targets$/);
    await expect(dialog).toBeVisible();
    await page.getByRole("combobox", { name: "Default objective target", exact: true }).selectOption("tour-target");
    await expect(page.locator("table").getByText("Objective", { exact: true })).toBeVisible();
    await expect(dialog).toContainText("default objective target for new chats and scanner runs");

    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/chat$/);
    await expect(dialog).toContainText("Chat shows the message composer");
    await expect(dialog).toContainText("Toggle converter panel");
    await expect(page.getByRole("textbox")).toBeVisible();
    await expect(page.locator('[data-tour="converter-toggle"]')).toHaveAttribute(
      "aria-label",
      "Toggle converter panel"
    );
    await expect(page.getByTestId("no-target-banner")).toHaveCount(0);
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
    await expect(page).toHaveURL(/\/scanner$/);
    await page
      .getByRole("link", { name: TOUR_SCENARIO.scenario_name, exact: true })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/scanner/${TOUR_SCENARIO.scenario_name}$`)
    );
    await page.getByRole("button", { name: "Home", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);

    await page.getByRole("button", { name: "Take a tour" }).click();

    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page).toHaveURL(/\/chat$/);

    // Walk back onto the Scanner detail route. The Scanner view owns both the
    // catalog and the detail page, but only the catalog carries the anchor.
    await page.goBack();
    await expect(page).toHaveURL(/\/$/);
    await page.goBack();
    await expect(page).toHaveURL(
      new RegExp(`/scanner/${TOUR_SCENARIO.scenario_name}$`)
    );
    await expect(page.locator('[data-tour="scanner-catalog"]')).toHaveCount(0);

    // `currentView` already equals `scenarios`, so the view-change effect never
    // fires. The hook has to notice the missing anchor, return to the catalog,
    // and advance after paint rather than stranding the tooltip.
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/scanner$/);
    await expect(page.locator('[data-tour="scanner-catalog"]')).toBeVisible();
    await expect(dialog).toContainText("Scanner runs a whole campaign for you");
  });
});

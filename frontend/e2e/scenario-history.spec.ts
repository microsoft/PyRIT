import { expect, test, type Page } from "@playwright/test";

const RUN_ID = "123e4567-e89b-12d3-a456-426614174000";
const ACTIVE_RUN_ID = "123e4567-e89b-12d3-a456-426614174001";
const QUEUED_RUN_ID = "123e4567-e89b-12d3-a456-426614174002";
const ATTACK_ID = "attack-result-1";
const SCENARIO_NAME = "airt.jailbreak";
const RAW_IMAGE_HTML = '<img src=x onerror="alert(1)">unsafe';

const scenarioDescription = `Jailbreak scenario implementation for PyRIT.

Tests how vulnerable a model is to jailbreak templates. A run is the cross-product of three selectors:

- **dataset** — the harmful objectives (HarmBench).
- **techniques** — compatible direct deliveries. Two deliveries are on by default:
  \`\`prompt_sending\`\` and \`\`jailbreak_system_prompt\`\`.
- **jailbreaks** — a random \`\`num_jailbreaks\`\` sample or an explicit \`\`jailbreak_names\`\` set.

${RAW_IMAGE_HTML}`;

const datasetSummary = {
  name: "harmbench",
  kind: "dataset",
  logical_seed_group_count: 5,
  selected_seed_group_count: 4,
  configured_caps: [{
    label: "Jailbreak templates",
    count: 2,
    configured_on: "configuration",
    dataset_name: null,
  }],
  selection_note: "One incompatible logical group is excluded.",
};

const configuredEstimate = {
  version: 1,
  status: "exact",
  total_attack_count: 8,
  minimum_attack_count: 8,
  maximum_attack_count: 8,
  condition: null,
  components: [{
    label: "Prompt sending",
    count: 8,
    factors: [
      { label: "jailbreak templates", count: 2 },
      { label: "selected seed groups", count: 4 },
      { label: "concrete techniques", count: 1 },
      { label: "attempts", count: 1 },
    ],
    is_baseline: false,
    note: null,
  }],
  datasets: [datasetSummary],
  adaptive_details: null,
  note: "The backend total is authoritative.",
  retries_included: false,
};

const catalogScenario = {
  scenario_name: SCENARIO_NAME,
  scenario_type: "Jailbreak",
  scenario_version: 4,
  description: "Tests how vulnerable a model is to jailbreak templates.",
  description_markdown: scenarioDescription,
  default_technique: "default",
  default_techniques: ["prompt_sending", "jailbreak_system_prompt"],
  aggregate_techniques: ["default", "easy"],
  aggregate_technique_expansions: {
    default: ["prompt_sending", "jailbreak_system_prompt"],
    easy: ["prompt_sending"],
  },
  all_techniques: ["prompt_sending", "jailbreak_system_prompt", "flip"],
  default_datasets: ["harmbench"],
  dataset_size_limit: {
    default_scope: "per_dataset",
    default_count: 4,
    override_scope: "per_dataset",
  },
  baseline_policy: "enabled",
  include_baseline_by_default: false,
  supported_parameters: [
    {
      name: "num_jailbreaks",
      type_name: "int",
      required: false,
      default: null,
      choices: null,
      is_list: false,
      description: "Draw this many random jailbreak templates for the run.",
    },
    {
      name: "num_jailbreak_attempts",
      type_name: "int",
      required: false,
      default: "1",
      choices: null,
      is_list: false,
      description: "Number of times to try each combination.",
    },
    {
      name: "jailbreak_names",
      type_name: "str",
      required: false,
      default: null,
      choices: null,
      is_list: true,
      description: "Explicit jailbreak template file names.",
    },
  ],
  default_run_size: {
    version: 1,
    status: "exact",
    total_attack_count: 16,
    minimum_attack_count: 16,
    maximum_attack_count: 16,
    condition: null,
    components: [{
      label: "Default attacks",
      count: 16,
      factors: [
        { label: "jailbreak templates", count: 2 },
        { label: "selected seed groups", count: 4 },
        { label: "default techniques", count: 2 },
      ],
      is_baseline: false,
      note: null,
    }],
    datasets: [datasetSummary],
    adaptive_details: null,
    note: "Retries and internal turns are excluded.",
    retries_included: false,
  },
};

const target = {
  target_registry_name: "test-target",
  identifier: {
    class_name: "OpenAIChatTarget",
    class_module: "tests",
    hash: "safe-target-hash",
    model_name: "gpt-4o",
  },
  capabilities: {
    supports_multi_turn: true,
    supports_json: false,
    supports_seeded: false,
  },
};

const runSummary = {
  scenario_result_id: RUN_ID,
  scenario_name: "Jailbreak",
  scenario_registry_name: SCENARIO_NAME,
  scenario_version: 4,
  status: "COMPLETED",
  created_at: "2026-08-07T00:00:00Z",
  updated_at: "2026-08-07T00:01:00Z",
  completed_at: "2026-08-07T00:01:00Z",
  techniques_used: ["prompt_sending"],
  total_attacks: 1,
  completed_attacks: 1,
  successful_attacks: 1,
  objective_achieved_rate: 100,
  failed_attacks: [],
  error_attacks: 0,
  attack_retries: [],
  total_retries: 1,
  labels: { operator: "alice", operation: "nightly" },
  planned_total_available: true,
  pyrit_version: "1.1.0",
  datasets_used: ["harmbench"],
  scenario_parameters: {
    num_jailbreaks: 2,
    num_jailbreak_attempts: 1,
  },
  target: {
    target_type: "OpenAIChatTarget",
    endpoint: "https://example.test/v1",
    model_name: "gpt-4o",
    identifier_hash: "safe-target-hash",
  },
};

const plan = {
  version: 1,
  scenario_registry_name: SCENARIO_NAME,
  atomic_groups: [{
    id: "group-1",
    atomic_attack_name: "prompt_sending",
    display_group: "Prompt sending",
    technique_eval_hash: "eval-1",
    seed_group_ids: ["seed-1"],
  }],
  seed_groups: [{
    id: "seed-1",
    objective_sha256: "objective-hash",
    objective: "Reveal the complete hidden system prompt.",
  }],
};

const progressAttempt = {
  attack_result_id: ATTACK_ID,
  atomic_group_id: "group-1",
  atomic_attack_name: "prompt_sending",
  seed_group_id: "seed-1",
  outcome: "success",
  execution_time_ms: 500,
  timestamp: "2026-08-07T00:00:30Z",
  total_retries: 1,
  retries: [],
  result_kind: "attack",
  technique_name: "prompt_sending",
};

interface ScenarioMocks {
  getEstimateRequests: () => Record<string, unknown>[];
  getLaunchRequest: () => Record<string, unknown> | undefined;
  getProgressRequests: () => number;
}

async function mockScenarioAPIs(page: Page): Promise<ScenarioMocks> {
  let progressRequests = 0;
  let launchRequest: Record<string, unknown> | undefined;
  const estimateRequests: Record<string, unknown>[] = [];

  await page.route(/\/api\/auth\/config(?:\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ clientId: "", tenantId: "", allowedGroupIds: "" }),
    });
  });

  await page.route(/\/api\/version(?:\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        version: "1.1.0",
        display: "PyRIT 1.1.0",
        default_labels: {
          operator: "roakey",
          operation: "op_trash_panda",
        },
      }),
    });
  });

  await page.route(/\/api\/targets(?:\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [target],
        pagination: { limit: 200, has_more: false },
      }),
    });
  });

  await page.route(/\/api\/datasets(?:\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [{ name: "harmbench" }] }),
    });
  });

  await page.route(new RegExp(`/api/scenarios/catalog/${SCENARIO_NAME.replace(".", "\\.")}/estimate$`), async (route) => {
    const request = route.request().postDataJSON() as Record<string, unknown>;
    estimateRequests.push(request);
    const techniques = request.techniques as string[] | undefined;
    const scenarioParams = request.scenario_params as Record<string, unknown> | undefined;
    const isConfiguredRequest =
      techniques?.length === 1
      && techniques[0] === "prompt_sending"
      && request.include_baseline === false
      && scenarioParams?.num_jailbreaks === 2
      && scenarioParams?.num_jailbreak_attempts === 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(isConfiguredRequest ? configuredEstimate : catalogScenario.default_run_size),
    });
  });

  await page.route(new RegExp(`/api/scenarios/catalog/${SCENARIO_NAME.replace(".", "\\.")}$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(catalogScenario),
    });
  });

  await page.route(/\/api\/scenarios\/catalog(?:\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [catalogScenario],
        pagination: { limit: 200, has_more: false },
      }),
    });
  });

  await page.route(/\/api\/labels(?:\?|$)/, async (route) => {
    const source = new URL(route.request().url()).searchParams.get("source") ?? "attacks";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        source,
        labels: {
          operator: ["alice", "bob"],
          operation: ["nightly"],
          team: ["safety"],
        },
      }),
    });
  });

  await page.route(new RegExp(`/api/scenarios/runs/${RUN_ID}/progress(?:\\?|$)`), async (route) => {
    progressRequests += 1;
    const isInitialPage = !new URL(route.request().url()).searchParams.has("since");
    const completed = progressRequests > 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        run: {
          scenario_result_id: RUN_ID,
          scenario_name: "Jailbreak",
          scenario_registry_name: SCENARIO_NAME,
          scenario_version: 4,
          status: completed ? "COMPLETED" : "IN_PROGRESS",
          created_at: runSummary.created_at,
          completed_at: completed ? runSummary.completed_at : null,
          pyrit_version: runSummary.pyrit_version,
          target: runSummary.target,
          techniques_used: runSummary.techniques_used,
          datasets_used: runSummary.datasets_used,
          scenario_parameters: runSummary.scenario_parameters,
          labels: runSummary.labels,
        },
        plan,
        reset: isInitialPage,
        active_atomic_group_ids: completed ? [] : ["group-1"],
        results: isInitialPage ? [progressAttempt] : [],
        next_cursor: "progress-cursor",
        has_more: false,
        plan_complete: true,
      }),
    });
  });

  await page.route(/\/api\/scenarios\/runs(?:\?|$)/, async (route) => {
    if (route.request().method() === "POST") {
      launchRequest = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({ ...runSummary, status: "CREATED", completed_at: null }),
      });
      return;
    }

    const url = new URL(route.request().url());
    const labelFilters = url.searchParams.getAll("label");
    const items = labelFilters.includes("operator:bob") ? [] : [runSummary];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items,
        pagination: { limit: 25, has_more: false, next_cursor: null },
      }),
    });
  });

  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}(?:\\?|$)`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        attack_result_id: ATTACK_ID,
        conversation_id: "conversation-1",
        attack_type: "SingleTurnAttack",
        target: runSummary.target,
        converters: [],
        outcome: "success",
        message_count: 0,
        related_conversation_ids: [],
        labels: {},
        created_at: runSummary.created_at,
        updated_at: runSummary.updated_at,
      }),
    });
  });

  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}/conversations`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        attack_result_id: ATTACK_ID,
        main_conversation_id: "conversation-1",
        conversations: [],
      }),
    });
  });

  await page.route(new RegExp(`/api/attacks/${ATTACK_ID}/messages`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversation_id: "conversation-1", messages: [] }),
    });
  });

  return {
    getEstimateRequests: () => estimateRequests,
    getLaunchRequest: () => launchRequest,
    getProgressRequests: () => progressRequests,
  };
}

async function configurePromptSendingRun(page: Page): Promise<void> {
  await expect(page.getByTestId("scenario-target-select")).toHaveValue("test-target");
  await page.getByTestId("technique-mode-custom").click();
  await expect(page.getByTestId("technique-prompt_sending")).toBeChecked();
  await expect(page.getByTestId("technique-jailbreak_system_prompt")).toBeChecked();
  await page.getByTestId("technique-jailbreak_system_prompt").click();
  await page.getByTestId("scenario-param-num_jailbreaks").fill("2");
  await page.getByTestId("scenario-param-num_jailbreak_attempts").fill("1");
  await expect(page.getByTestId("baseline-checkbox")).not.toBeChecked();
  await expect(page.getByRole("group", {
    name: "1 technique multiplied by 4 objectives multiplied by 2 jailbreak templates multiplied by 1 attempt equals 8 planned attacks.",
  })).toBeVisible();
}

test.describe("Scenario catalog, history, and live run routing", () => {
  test("opens the Configure page from the semantic launch index with complete safe metadata", async ({ page }) => {
    await mockScenarioAPIs(page);
    await page.goto("/scanner");

    const primaryNavigation = page.getByRole("navigation", { name: "Primary" });
    const primaryButtons = primaryNavigation.getByRole("button");
    await expect(primaryButtons).toHaveCount(7);
    expect(await primaryButtons.evaluateAll((buttons) =>
      buttons.map((button) => button.getAttribute("aria-label")))).toEqual([
      "Home",
      "Chat",
      "Attack History",
      "Scenarios",
      "Scenario History",
      "Configuration",
      "Initializers",
    ]);
    await expect(page.getByTitle("Scenarios")).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("table", { name: "Registered scenarios" })).toBeVisible();
    await expect(page.getByRole("columnheader")).toHaveText([
      "Scenario / purpose",
      "Configure",
      "Default dataset size",
      "Default techniques",
      "Default run size",
    ]);

    const row = page.getByTestId(`scenario-card-${SCENARIO_NAME}`);
    const cells = row.getByRole("cell");
    await expect(cells).toHaveCount(5);
    const configureButton = cells.nth(1).getByRole("button", { name: "Configure run" });
    await expect(configureButton).toBeVisible();
    const [scenarioCellBox, configureCellBox, datasetCellBox] = await Promise.all([
      cells.nth(0).boundingBox(),
      cells.nth(1).boundingBox(),
      cells.nth(2).boundingBox(),
    ]);
    expect(scenarioCellBox).not.toBeNull();
    expect(configureCellBox).not.toBeNull();
    expect(datasetCellBox).not.toBeNull();
    expect(configureCellBox!.x).toBeGreaterThan(scenarioCellBox!.x);
    expect(configureCellBox!.x).toBeLessThan(datasetCellBox!.x);
    await expect(page.getByRole("button", { name: /show details|hide details/i })).toHaveCount(0);

    await configureButton.click();
    await expect(page).toHaveURL(`/scanner/${SCENARIO_NAME}`);
    await expect(page.getByRole("heading", { name: SCENARIO_NAME, level: 1 })).toBeVisible();
    await expect(page.getByText("Jailbreak · v4")).toBeVisible();
    const description = page.getByTestId("scenario-detail-description");
    await expect(description.getByText("dataset")).toHaveCSS("font-weight", /^(600|700)$/);
    await expect(description.locator("code").filter({ hasText: "num_jailbreaks" })).toBeVisible();
    await expect(description.locator("img")).toHaveCount(0);
    await expect(description).toContainText(RAW_IMAGE_HTML);
    await expect(page.getByRole("radio", { name: /Recommended \(default\).*2 techniques/ })).toBeChecked();
    await expect(page.getByRole("radio", { name: /Easy.*1 technique/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: "Custom" })).toBeVisible();
    const members = page.getByTestId("selected-technique-set-members");
    await expect(members.getByText("prompt_sending")).toBeVisible();
    await expect(members.getByText("jailbreak_system_prompt")).toBeVisible();
    const preview = page.getByRole("complementary", { name: "Run preview" });
    await expect(preview.getByText("Jailbreak templates: 2")).toBeVisible();
    await expect(preview.getByRole("group", {
      name: "2 techniques multiplied by 4 objectives multiplied by 2 jailbreak templates equals 16 planned attacks.",
    })).toBeVisible();
    await expect(page.getByText("Include direct baseline comparison")).toBeVisible();
    await expect(page.getByText(/Also send each selected objective directly/)).toBeVisible();

    await page.getByTitle("Scenario History").click();
    await expect(page).toHaveURL("/scenario-history");
    await expect(page.getByTitle("Scenario History")).toHaveAttribute("aria-current", "page");
    await page.getByTitle("Scenarios").click();
    await expect(page).toHaveURL("/scanner");
    await expect(page.getByTitle("Scenarios")).toHaveAttribute("aria-current", "page");
  });

  test("sends one exact configuration to estimate and launch, then completes live polling", async ({ page }) => {
    const mocks = await mockScenarioAPIs(page);
    await page.goto(`/scanner/${SCENARIO_NAME}`);

    const form = page.getByRole("form", { name: "Scenario run configuration" });
    const preview = page.getByRole("complementary", { name: "Run preview" });
    const formBox = await form.boundingBox();
    const previewBox = await preview.boundingBox();
    expect(formBox).not.toBeNull();
    expect(previewBox).not.toBeNull();
    expect(previewBox!.x).toBeGreaterThan(formBox!.x + formBox!.width);
    expect(previewBox!.y).toBeLessThan(formBox!.y + formBox!.height);

    await configurePromptSendingRun(page);

    const expectedEstimateRequest = {
      target_name: "test-target",
      techniques: ["prompt_sending"],
      include_baseline: false,
      scenario_params: {
        num_jailbreaks: 2,
        num_jailbreak_attempts: 1,
      },
    };
    await expect.poll(() => {
      const requests = mocks.getEstimateRequests();
      return requests[requests.length - 1];
    }).toEqual(expectedEstimateRequest);
    await expect(preview.getByRole("group", {
      name: "1 technique multiplied by 4 objectives multiplied by 2 jailbreak templates multiplied by 1 attempt equals 8 planned attacks.",
    })).toBeVisible();
    await expect(preview).not.toContainText("context_compliance");

    await page.getByTestId("launch-scenario-btn").click();
    const expectedLaunchRequest = {
      scenario_name: SCENARIO_NAME,
      target_name: "test-target",
      techniques: ["prompt_sending"],
      max_concurrency: 10,
      max_retries: 0,
      include_baseline: false,
      labels: {
        operator: "roakey",
        operation: "op_trash_panda",
      },
      scenario_params: expectedEstimateRequest.scenario_params,
    };
    await expect.poll(mocks.getLaunchRequest).toEqual(expectedLaunchRequest);
    expect(mocks.getLaunchRequest()?.techniques).toEqual(expectedEstimateRequest.techniques);
    expect(mocks.getLaunchRequest()?.scenario_params).toEqual(expectedEstimateRequest.scenario_params);
    expect(mocks.getLaunchRequest()?.include_baseline).toBe(expectedEstimateRequest.include_baseline);
    expect(mocks.getLaunchRequest()?.techniques).not.toContain("default");
    expect(mocks.getLaunchRequest()?.techniques).not.toContain("context_compliance");

    await expect(page).toHaveURL(`/scenario-history/${RUN_ID}`);
    await expect(page.getByTestId("run-state-badge")).toHaveText("In progress");
    await expect(page.getByText("gpt-4o").first()).toBeVisible();
    await expect(page.getByText("harmbench")).toBeVisible();
    await expect(page.getByTestId("run-state-badge")).toHaveText("Completed", { timeout: 6_000 });
    expect(mocks.getProgressRequests()).toBeGreaterThanOrEqual(2);
  });

  test("stacks the configured run preview without overflow and keeps touch controls usable", async ({ page }) => {
    await mockScenarioAPIs(page);
    const client = await page.context().newCDPSession(page);
    await client.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/scanner");
    const catalogRow = page.getByTestId(`scenario-card-${SCENARIO_NAME}`);
    const configureButton = catalogRow.getByRole("button", { name: "Configure run" });
    await expect(catalogRow).toBeVisible();
    expect(await catalogRow.getByRole("cell").allInnerTexts()).toEqual([
      expect.stringContaining("Scenario / purpose"),
      expect.stringContaining("Configure"),
      expect.stringContaining("Default dataset size"),
      expect.stringContaining("Default techniques"),
      expect.stringContaining("Default run size"),
    ]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expect((await configureButton.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await configureButton.press("Enter");
    await expect(page).toHaveURL(`/scanner/${SCENARIO_NAME}`);
    await configurePromptSendingRun(page);

    const formBox = await page.getByRole("form", { name: "Scenario run configuration" }).boundingBox();
    const previewBox = await page.getByRole("complementary", { name: "Run preview" }).boundingBox();
    expect(formBox).not.toBeNull();
    expect(previewBox).not.toBeNull();
    expect(previewBox!.y).toBeGreaterThanOrEqual(formBox!.y + formBox!.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);

    for (const control of [
      page.getByTestId("technique-prompt_sending"),
      page.getByTestId("scenario-param-num_jailbreaks"),
      page.getByTestId("baseline-checkbox"),
      page.getByTestId("launch-scenario-btn"),
    ]) {
      expect((await control.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    }
  });

  test("preserves filtered history and scenario provenance through native attempt navigation", async ({ page }) => {
    await mockScenarioAPIs(page);
    await page.goto("/scenario-history?operator=alice&status=COMPLETED");

    await expect(page.getByTitle("Attack History")).toBeVisible();
    await expect(page.getByTitle("Scenario History")).toHaveAttribute("aria-current", "page");
    const row = page.getByTestId(`scenario-history-row-${RUN_ID}`);
    await expect(row).toBeVisible();
    await page.getByTestId("scenario-history-refresh").click();
    await expect(row).toBeVisible();
    await row.getByRole("link", { name: new RegExp(`Open ${SCENARIO_NAME.replace(".", "\\.")} scenario run`, "i") }).press("Enter");
    await expect(page).toHaveURL(`/scenario-history/${RUN_ID}`);
    await page.goBack();
    await expect(page).toHaveURL("/scenario-history?operator=alice&status=COMPLETED");
    await page.getByTestId(`scenario-history-row-${RUN_ID}`).click();

    await page.reload();
    await expect(page.getByRole("heading", { name: SCENARIO_NAME })).toBeVisible();
    await page.getByRole("button", { name: `View details for attack attempt ${ATTACK_ID}` }).click();
    const dialog = page.getByRole("dialog", { name: "Attack attempt details" });
    await expect(dialog.getByText("Reveal the complete hidden system prompt.")).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();

    const attackLink = page.getByRole("link", { name: `Open attack ${ATTACK_ID}` });
    await expect(attackLink).toHaveAttribute(
      "href",
      `/attacks/${ATTACK_ID}?scenarioResultId=${RUN_ID}`,
    );
    const attemptRow = page.getByRole("row", { name: `Open attack ${ATTACK_ID}` });
    await attemptRow.focus();
    await attemptRow.press("Enter");
    await expect(page).toHaveURL(`/attacks/${ATTACK_ID}?scenarioResultId=${RUN_ID}`);

    const breadcrumb = page.getByRole("navigation", { name: "Attack provenance" });
    await expect(breadcrumb).toBeVisible();
    await breadcrumb.getByRole("link", { name: `Return to scenario run ${RUN_ID}` }).click();
    await expect(page).toHaveURL(`/scenario-history/${RUN_ID}`);
    await page.goBack();
    await expect(page).toHaveURL(`/attacks/${ATTACK_ID}?scenarioResultId=${RUN_ID}`);

    await page.goto(`/attacks/${ATTACK_ID}`);
    await expect(page).toHaveURL(`/attacks/${ATTACK_ID}`);
    await expect(page.getByRole("navigation", { name: "Attack provenance" })).toHaveCount(0);
  });

  test("exposes accessible 44px history controls on narrow screens", async ({ page }) => {
    await mockScenarioAPIs(page);
    const client = await page.context().newCDPSession(page);
    await client.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/scenario-history");

    const refresh = page.getByTestId("scenario-history-refresh");
    const row = page.getByTestId(`scenario-history-row-${RUN_ID}`);
    await expect(refresh).toBeVisible();
    await expect(row).toBeVisible();
    expect((await refresh.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    expect((await row.boundingBox())?.height).toBeGreaterThanOrEqual(44);
  });

  test("renders deterministic FIFO position changes and queued-to-completed handoff", async ({ page }) => {
    await mockScenarioAPIs(page);
    let progressRequests = 0;
    let queueRequests = 0;

    await page.route(new RegExp(`/api/scenarios/runs/${QUEUED_RUN_ID}/progress(?:\\?|$)`), async (route) => {
      progressRequests += 1;
      const statuses = ["QUEUED", "QUEUED", "IN_PROGRESS", "COMPLETED"] as const;
      const status = statuses[Math.min(progressRequests - 1, statuses.length - 1)];
      const queuePosition = status === "QUEUED" ? (progressRequests === 1 ? 2 : 1) : null;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          run: {
            ...runSummary,
            scenario_result_id: QUEUED_RUN_ID,
            status,
            completed_at: status === "COMPLETED" ? runSummary.completed_at : null,
            queue_position: queuePosition,
            active_scenario_result_id: status === "QUEUED" ? ACTIVE_RUN_ID : QUEUED_RUN_ID,
            overload_summaries: [{
              component_role: "objective_target",
              count: 2,
              rate_limit_count: 1,
              server_error_count: 1,
              status_codes: [429, 503],
              latest_timestamp: "2026-08-07T00:00:45Z",
            }],
          },
          plan: progressRequests === 1 ? plan : null,
          reset: progressRequests === 1,
          active_atomic_group_ids: status === "IN_PROGRESS" ? ["group-1"] : [],
          results: status === "COMPLETED" ? [progressAttempt] : [],
          next_cursor: `queue-progress-${progressRequests}`,
          has_more: false,
          plan_complete: true,
        }),
      });
    });

    await page.route(/\/api\/scenarios\/runs\/queue(?:\?|$)/, async (route) => {
      queueRequests += 1;
      const active = {
        scenario_result_id: ACTIVE_RUN_ID,
        scenario_name: "Active scenario",
        scenario_registry_name: "active.scenario",
        state: "IN_PROGRESS",
        created_at: "2026-08-07T00:00:00Z",
        enqueued_at: "2026-08-07T00:00:00Z",
        started_at: "2026-08-07T00:00:01Z",
      };
      const queued = {
        scenario_result_id: QUEUED_RUN_ID,
        scenario_name: "Jailbreak",
        scenario_registry_name: SCENARIO_NAME,
        state: "QUEUED",
        position: queueRequests === 1 ? 2 : 1,
        created_at: runSummary.created_at,
        enqueued_at: runSummary.created_at,
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          revision: queueRequests,
          snapshot_at: `2026-08-07T00:00:0${Math.min(queueRequests, 9)}Z`,
          active: queueRequests < 3 ? active : queueRequests === 3 ? { ...queued, state: "IN_PROGRESS", position: null } : null,
          queued: queueRequests < 3
            ? [
                ...(queueRequests === 1 ? [{ ...queued, scenario_result_id: RUN_ID, position: 1 }] : []),
                queued,
              ]
            : [],
        }),
      });
    });

    await page.goto(`/scenario-history/${QUEUED_RUN_ID}`);

    await expect(page.getByTestId("run-state-badge")).toHaveText("Queued");
    await expect(page.getByTestId("queued-run-progress")).toContainText("Position 2");
    await expect(page.getByTestId("queued-run-progress")).not.toContainText("%");
    await expect(page.getByRole("link", { name: new RegExp(ACTIVE_RUN_ID) })).toHaveAttribute(
      "href",
      `/scenario-history/${ACTIVE_RUN_ID}`,
    );
    const warning = page.getByTestId("scenario-overload-warning");
    await expect(warning).toContainText("Objective target");
    await expect(warning).toContainText("2 × HTTP 429/503");
    await expect(warning).toContainText("without adaptive throttling");
    await page.setViewportSize({ width: 390, height: 844 });
    expect((await page.getByRole("button", { name: "Cancel run" }).boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await expect(page.getByTestId("queued-run-progress")).toContainText("Position 1", { timeout: 6_000 });
    await expect(page.getByTestId("run-state-badge")).toHaveText("In progress", { timeout: 6_000 });
    await expect(page.getByTestId("run-state-badge")).toHaveText("Completed", { timeout: 6_000 });
    expect(progressRequests).toBe(4);

    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});

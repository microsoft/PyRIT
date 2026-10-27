import { test, expect, type Page } from "./_fixtures";
import type { OperationSelection } from "../src/types";
import { mockVersion } from "./_compatibility";
import { READY_RUNTIME, waitForAppReady } from "./_runtime";

// ---------------------------------------------------------------------------
// The operation picker's size and placement are decided by Fluent's floating
// positioning at runtime. jsdom has no layout engine, so the unit suite cannot
// see any of it — several sizing regressions shipped past a green Jest run.
// These tests measure the rendered box in a real browser.
// ---------------------------------------------------------------------------

const LONG_OPERATION = "op_2026_08_a_very_long_operation_name_that_would_be_clipped";

function operations(count: number): string[] {
  return Array.from(
    { length: count },
    (_, i) => `op_2026_08_run_${String(i).padStart(3, "0")}`,
  );
}

function operationId(index: number): string {
  return `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
}

async function waitForSavedOperation(
  page: Page,
  operation: OperationSelection | null,
): Promise<void> {
  await expect.poll(() => page.evaluate(() =>
    JSON.parse(localStorage.getItem("pyrit.userPreferences.v1.local") ?? "{}").operation,
  )).toEqual(operation);
}

async function rememberOperation(page: Page, id: string): Promise<void> {
  await page.addInitScript((operationId: string) => {
    window.localStorage.setItem("pyrit.userPreferences.v1.local", JSON.stringify({
      targets: { objective: null, adversarial: null },
      hiddenTargetRegistryNames: [],
      labels: {},
      theme: "system",
      chatMarkdown: false,
      operation: { id: operationId },
    }));
  }, id);
}

async function setupMocks(
  page: Page,
  operationLabels: string[],
  options: {
    versionDelayMs?: number;
    defaultLabels?: Record<string, string>;
    defaultOperation?: string;
    operatorLabels?: string[];
    savedOperations?: string[];
  } = {},
): Promise<void> {
  let versionRequests = 0;
  const savedOperations = (options.savedOperations ?? operationLabels).map((name, index) => ({
    id: operationId(index),
    name,
    created_at: "2026-01-01T00:00:00Z",
  }));
  // Everything the app calls while booting, so the run does not depend on a
  // dev-server proxy with no backend behind it.
  await page.route(/\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, "");

    if (path === "/health") {
      return route.fulfill(json({ status: "healthy" }));
    }
    if (path === "/auth/config") {
      return route.fulfill(json({ clientId: "", tenantId: "", allowedGroupIds: "" }));
    }
    if (path === "/runtime") {
      return route.fulfill(json(READY_RUNTIME));
    }
    if (path === "/version") {
      versionRequests += 1;
      if (options.versionDelayMs && versionRequests > 1) {
        await new Promise((resolve) => setTimeout(resolve, options.versionDelayMs));
      }
      return route.fulfill(json(mockVersion({
        version: "picker-test",
        display: "picker-test",
        ...(options.defaultLabels ? { default_labels: options.defaultLabels } : {}),
        default_operation: options.defaultOperation ?? null,
      })));
    }
    if (path === "/labels") {
      return route.fulfill(json({
        source: "attacks",
        labels: {
          operator: options.operatorLabels ?? ["roakey"],
          operation: operationLabels,
        },
      }));
    }
    if (path === "/operations") {
      return route.fulfill(json({ items: savedOperations }));
    }
    if (path.startsWith("/operations/")) {
      const operation = savedOperations.find(item => path === `/operations/${item.id}`);
      return route.fulfill(operation ? json(operation) : {
        ...json({ detail: "Operation not found" }), status: 404,
      });
    }
    if (path === "/attacks") {
      return route.fulfill(json({ items: [], total: 0, limit: 5, offset: 0 }));
    }
    return route.fulfill(json({}));
  });
}

function json(body: unknown) {
  return {
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  };
}

/** Opens the picker from the labels bar and returns the rendered listbox. */
async function openOperationPicker(page: Page) {
  await page.goto("/");
  const chip = page.getByTestId("edit-label-operation");
  await expect(chip).toBeVisible();
  await chip.click();

  const listbox = page.getByRole("listbox");
  await expect(listbox).toBeVisible();
  return listbox;
}

test.describe("operation picker placement", () => {
  test("highlights the placeholder operator and requires resolving a proposed operation", async ({ page }) => {
    await setupMocks(page, [], {
      defaultLabels: { operator: "roakey" },
      defaultOperation: "op_trash_panda",
      savedOperations: ["op_trash_panda", "op_real"],
    });
    await page.goto("/");

    const operator = page.getByTestId("edit-label-operator").locator("..");
    const operation = page.getByTestId("edit-label-operation").locator("..");
    await expect(operator).toHaveCSS("background-color", "rgb(254, 247, 178)");
    await expect(page.getByTestId("edit-label-operation")).toHaveValue("op_trash_panda");
    await expect(operation).toHaveCSS("background-color", "rgb(254, 247, 178)");
    await expect(page.getByTestId("edit-label-operation")).toHaveAccessibleDescription("Not registered");
    await expect(page.getByRole("button", { name: "Register", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Choose existing", exact: true })).toHaveCount(0);

    await page.getByTestId("edit-label-operator").fill("alice");
    await page.getByTestId("edit-label-operator").press("Enter");
    await expect(operator).not.toHaveCSS("background-color", "rgb(254, 247, 178)");

    await page.getByTestId("edit-label-operation").click();
    await page.getByRole("option", { name: "op_real", exact: true }).click();
    await expect(page.getByTestId("edit-label-operation")).toHaveValue("op_real");
    await expect(page.getByText("Not registered", { exact: true })).toHaveCount(0);
    await expect(operation).not.toHaveCSS("background-color", "rgb(254, 247, 178)");
  });

  test("keeps metadata editable in a narrow bar without duplicating it in Default Labels", async ({ page }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await setupMocks(page, ["op_alpha", "op_beta"]);
    await page.goto("/");

    const bar = page.getByRole("region", { name: "Default Labels" });
    await expect(bar.getByText("New run labels", { exact: true })).toHaveCount(0);
    await expect(bar.getByText("Used for new attacks and scans.", { exact: false })).toHaveCount(0);

    await bar.getByRole("combobox", { name: "Operation" }).click();
    await page.getByRole("option", { name: "op_beta", exact: true }).click();
    await expect(bar.getByRole("combobox", { name: "Operation" })).toBeVisible();

    await bar.getByRole("textbox", { name: "Operator" }).click();
    await page.getByRole("textbox", { name: "Operator" }).fill("alice");
    await page.getByRole("textbox", { name: "Operator" }).press("Enter");
    await expect(bar.getByRole("textbox", { name: "Operator" })).toBeVisible();

    await bar.getByTestId("labels-icon-btn").click();
    const popover = page.getByRole("group").filter({
      has: page.getByRole("heading", { name: "Default Labels" }),
    });
    await expect(popover.getByRole("heading", { name: "Default Labels" })).toBeVisible();
    await expect(popover.getByText("added to new attacks and scans", { exact: true })).toBeVisible();
    await expect(popover.getByText("Run metadata", { exact: true })).toHaveCount(0);
    await expect(popover.getByText("Add Label", { exact: true })).toHaveCount(0);
    await expect(popover.getByRole("button", { name: /^Edit (operator|operation),/ })).toHaveCount(0);
    await expect(popover.getByRole("textbox", { name: "Label key" })).toBeVisible();
    await expect(popover.getByRole("button", { name: "Add", exact: true })).toBeVisible();
  });

  test("keeps the list on screen when it opens above the input", async ({
    page,
  }) => {
    // The shared bar is normally near the top. Move it down to exercise
    // upward collision handling independently of the Home page layout.
    await page.setViewportSize({ width: 1280, height: 420 });
    await setupMocks(page, operations(60));
    await page.goto("/");
    await page.getByRole("region", { name: "Default Labels" }).evaluate(
      (bar: HTMLElement) => { bar.style.marginTop = "240px"; },
    );
    await page.getByTestId("edit-label-operation").click();

    const listbox = page.getByRole("listbox");
    await expect(listbox).toBeVisible();

    const box = (await listbox.boundingBox())!;
    const input = (await page
      .getByTestId("edit-label-operation")
      .boundingBox())!;
    const viewport = page.viewportSize()!;

    expect(box.y).toBeLessThan(input.y);
    expect(box.y + box.height).toBeLessThanOrEqual(input.y);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  });

  test("keeps the whole editor inside the labels bar on a laptop screen", async ({
    page,
  }) => {
    // The card is narrowest just after the home grid splits into two columns,
    // which is where an editor that cannot shrink loses its chevron.
    await page.setViewportSize({ width: 1024, height: 800 });
    await setupMocks(page, ["op_alpha", "op_beta"]);
    await openOperationPicker(page);

    // The input is sized inside the control, so measure the control itself —
    // it is the part that carries the dropdown chevron.
    const overhang = await page
      .getByTestId("edit-label-operation")
      .evaluate((input) => {
        const control = input.parentElement!.getBoundingClientRect();
        const bar = input
          .closest("[data-testid='labels-bar']")!
          .getBoundingClientRect();
        return control.right - bar.right;
      });

    // Sub-pixel rounding is fine; a lost chevron is 27px.
    expect(overhang).toBeLessThan(2);
  });

  test("sizes the list to its content so long names are not clipped", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, [LONG_OPERATION, "op_short"]);
    await openOperationPicker(page);

    const option = page.getByRole("option", { name: LONG_OPERATION });
    await expect(option).toBeVisible();

    const overflow = await option.evaluate((el) => ({
      scrollWidth: el.scrollWidth,
      clientWidth: el.clientWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth);
  });

  test("keeps the list on screen in a short window", async ({
    page,
  }) => {
    const viewportHeight = 200;
    await page.setViewportSize({ width: 1280, height: viewportHeight });
    await setupMocks(page, operations(60));
    const listbox = await openOperationPicker(page);

    const box = (await listbox.boundingBox())!;
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(viewportHeight);
  });

  test("keeps the operation in use on the list, wherever it was chosen", async ({
    page,
  }) => {
    // A saved Operation need not appear among historical name labels.
    await page.setViewportSize({ width: 1280, height: 800 });
    await rememberOperation(page, operationId(2));
    await setupMocks(page, ["op_alpha", "op_beta"], {
      savedOperations: ["op_alpha", "op_beta", "op_chosen_elsewhere"],
    });
    await openOperationPicker(page);

    await expect(
      page.getByRole("option", { name: "op_chosen_elsewhere", exact: true }),
    ).toBeVisible();

    // ...and it must not offer to create the name that is already set.
    await page.getByTestId("edit-label-operation").fill("op_chosen_elsewhere");
    await expect(page.getByRole("option", { name: /Create/ })).toHaveCount(0);
  });

  test("stays usable when memory holds far more operations than fit", async ({
    page,
  }) => {
    // The reason for the cap: Fluent renders every option as a real component,
    // so an uncapped list stalls the tab. Measured before the cap, 50k options
    // took ~27s for a single keystroke.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, operations(600));
    const listbox = await openOperationPicker(page);

    await expect(listbox.getByRole("option").first()).toBeVisible();
    expect(await listbox.getByRole("option").count()).toBeLessThanOrEqual(210);

    // Typing has to stay responsive, which is the thing that was broken.
    const started = Date.now();
    await page.getByTestId("edit-label-operation").fill("run_599");
    await expect(
      page.getByRole("option", { name: "op_2026_08_run_599", exact: true }),
    ).toBeVisible();
    expect(Date.now() - started).toBeLessThan(3000);
  });

  test("retains an unsaved legacy operation until explicitly cleared, including after reload", async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "pyrit.globalLabels",
        JSON.stringify({ operator: "roakey", operation: "op_chosen_elsewhere" }),
      );
    });
    await setupMocks(page, operations(600));
    await page.goto("/");
    await waitForAppReady(page);

    await expect(page.getByTestId("edit-label-operation")).toHaveValue("op_chosen_elsewhere");
    await expect(page.getByTestId("edit-label-operation")).toHaveAccessibleDescription("Not registered");
    await expect(page.getByRole("button", { name: "Register", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Choose existing", exact: true })).toHaveCount(0);
    await page.getByRole("region", { name: "Default Labels" }).getByRole("button", { name: "Clear", exact: true }).click();
    await expect(page.getByTestId("edit-label-operation")).toHaveValue("");
    await waitForSavedOperation(page, null);
    await page.reload();
    await waitForAppReady(page);
    await expect(page.getByTestId("edit-label-operation")).toHaveValue("");
    await expect(page.getByText("Not registered", { exact: true })).toHaveCount(0);
  });

  test("keeps an operation the saved list already holds past the cap", async ({
    page,
  }) => {
    // The usual case: the operation in use is in the response, just not near
    // the front of it.
    const inUseName = "op_2026_08_run_400";
    await page.setViewportSize({ width: 1280, height: 800 });
    await rememberOperation(page, operationId(400));
    await setupMocks(page, operations(600));
    await page.goto("/");
    await expect(page.getByTestId("edit-label-operation")).toHaveValue(inUseName);
    await openOperationPicker(page);

    await expect(
      page.getByRole("option", { name: inUseName, exact: true }),
    ).toHaveCount(1);
  });
});

test.describe("operation picker persistence", () => {
  test("remembers the chosen operation across a refresh", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha", "op_beta"]);
    await openOperationPicker(page);
    await waitForAppReady(page);

    await page.getByRole("option", { name: "op_beta", exact: true }).click();
    await expect(page.getByTestId("edit-label-operation")).toHaveValue("op_beta");
    await waitForSavedOperation(page, { id: operationId(1) });

    await page.reload();
    await waitForAppReady(page);

    await expect(page.getByTestId("edit-label-operation")).toHaveValue("op_beta");
  });

  test("keeps an operation picked while the app was still starting up", async ({
    page,
  }) => {
    // The version request carries the backend's default labels and can land
    // well after the bar is usable.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "pyrit.globalLabels",
        JSON.stringify({ operator: "roakey", operation: "op_from_storage" }),
      );
    });
    await setupMocks(page, ["op_from_storage", "op_picked_early"], {
      versionDelayMs: 4000,
    });
    await openOperationPicker(page);

    await page
      .getByRole("option", { name: "op_picked_early", exact: true })
      .click();
    await expect(page.getByTestId("edit-label-operation")).toHaveValue(
      "op_picked_early",
    );

    // Let the slow response land; it must not undo the choice.
    await page.waitForTimeout(5000);
    await expect(page.getByTestId("edit-label-operation")).toHaveValue(
      "op_picked_early",
    );
  });

  test("keeps an operation picked before the backend's own default arrives", async ({
    page,
  }) => {
    // Nothing is stored, and the backend supplies its own `operation` default
    // that lands after the bar is already usable. The only thing standing
    // between the pick and that late response is that the value on screen is
    // no longer the untouched default.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha", "op_picked_early"], {
      versionDelayMs: 4000,
      defaultOperation: "op_configured",
    });
    await openOperationPicker(page);

    await page
      .getByRole("option", { name: "op_picked_early", exact: true })
      .click();
    await expect(page.getByTestId("edit-label-operation")).toHaveValue(
      "op_picked_early",
    );

    await page.waitForTimeout(5000);
    await expect(page.getByTestId("edit-label-operation")).toHaveValue(
      "op_picked_early",
    );
    // What is on screen is also what a refresh would restore.
    const savedPreferences =
      await page.evaluate(() =>
        window.localStorage.getItem("pyrit.userPreferences.v1.local"),
      );
    expect(savedPreferences).not.toBeNull();
    expect(JSON.parse(savedPreferences ?? "null")).toMatchObject({
      operation: { id: "00000000-0000-4000-8000-000000000001" },
    });
  });

  test("lets the backend still name the operator after you pick an operation", async ({
    page,
  }) => {
    // Picking an operation must not freeze the placeholder operator into
    // storage, where it would outrank a deployment's configured default.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha", "op_beta"]);
    await openOperationPicker(page);
    await waitForAppReady(page);

    await page.getByRole("option", { name: "op_beta", exact: true }).click();
    await expect(page.getByTestId("edit-label-operation")).toHaveValue("op_beta");
    await waitForSavedOperation(page, { id: operationId(1) });

    // A later visit, once the deployment configures an operator.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await setupMocks(page, ["op_alpha", "op_beta"], {
      defaultLabels: { operator: "configured_user" },
    });
    await page.reload();
    await waitForAppReady(page);

    await expect(page.getByTestId("edit-label-operator")).toHaveValue(
      "configured_user",
    );
    await expect(page.getByTestId("edit-label-operation")).toHaveValue("op_beta");
  });

  test("lets the backend change a label it supplied, after you pick", async ({
    page,
  }) => {
    // The operator here came from the deployment's config, not from a choice,
    // so picking an operation must not capture it as one.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha", "op_beta"], {
      defaultLabels: { operator: "configured_day1" },
    });
    await openOperationPicker(page);
    await waitForAppReady(page);

    await page.getByRole("option", { name: "op_beta", exact: true }).click();
    await expect(page.getByTestId("edit-label-operator")).toHaveValue(
      "configured_day1",
    );
    await waitForSavedOperation(page, { id: operationId(1) });

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await setupMocks(page, ["op_alpha", "op_beta"], {
      defaultLabels: { operator: "configured_day2" },
    });
    await page.reload();
    await waitForAppReady(page);

    await expect(page.getByTestId("edit-label-operator")).toHaveValue(
      "configured_day2",
    );
    await expect(page.getByTestId("edit-label-operation")).toHaveValue("op_beta");
  });
});

test.describe("switching between labels", () => {
  test("keeps the label you click on next while leaving the picker", async ({
    page,
  }) => {
    // Both editors finish on blur a turn later, so the click that takes the
    // focus has already opened the next editor by then. jsdom does not order
    // blur and click the way a browser does, so only this can see it.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha", "op_beta"]);
    await openOperationPicker(page);

    await page.getByTestId("edit-label-operator").click();

    const operatorEditor = page.getByTestId("edit-label-operator");
    await expect(operatorEditor).toBeVisible();
    await expect(operatorEditor).toHaveValue("roakey");
    await page.waitForTimeout(500);
    await expect(operatorEditor).toBeVisible();
    await expect(operatorEditor).toHaveValue("roakey");
  });

  test("keeps the picker you open while leaving another label", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha", "op_beta"]);
    await page.goto("/");

    await page.getByTestId("edit-label-operator").click();
    await page.getByTestId("edit-label-operator").fill("alice");
    await page.getByTestId("edit-label-operation").click();

    await expect(page.getByRole("listbox")).toBeVisible();
    await page.waitForTimeout(500);
    await expect(page.getByRole("listbox")).toBeVisible();
    // The operator edit still went in; only its clean-up was skipped.
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("edit-label-operator")).toHaveValue("alice");
  });
});

test.describe("finishing an edit another way", () => {
  test("keeps a suggestion picked while the typed value was still saving", async ({
    page,
  }) => {
    // The input's blur schedules a save of what was typed, and only a browser
    // orders that blur against the click that picked the suggestion.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha"], { operatorLabels: ["roakey", "alice"] });
    await page.goto("/");

    await page.getByTestId("edit-label-operator").click();
    await page.getByTestId("edit-label-operator").fill("al");
    await page.getByText("alice", { exact: true }).click();

    await page.waitForTimeout(500);
    await expect(page.getByTestId("edit-label-operator")).toHaveValue("alice");
  });

  test("keeps metadata inputs visible and creation first on mobile", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 360, height: 800 });
    await setupMocks(page, ["op_alpha"]);
    await page.goto("/");

    const operator = page.getByRole("textbox", { name: "Operator" });
    const operation = page.getByRole("combobox", { name: "Operation" });
    await expect(operator).toBeVisible();
    await expect(operation).toBeVisible();
    await operation.click();
    await expect(page.getByRole("option").first()).toHaveText("New operation…");
    await operation.fill("missing");
    await expect(page.getByRole("option").first()).toHaveText("New operation…");
    await page.getByRole("option", { name: "New operation…" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("textbox", { name: "Name" })).toBeFocused();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(operation).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(360);
  });
});

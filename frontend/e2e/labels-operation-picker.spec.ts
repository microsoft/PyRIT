import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// The operation picker's size and placement are decided by Fluent's floating
// positioning at runtime. jsdom has no layout engine, so the unit suite cannot
// see any of it — several sizing regressions shipped past a green Jest run.
// These tests measure the rendered box in a real browser.
// ---------------------------------------------------------------------------

const LIST_MAX_HEIGHT = 240;
const LONG_OPERATION = "op_2026_08_a_very_long_operation_name_that_would_be_clipped";

function operations(count: number): string[] {
  return Array.from(
    { length: count },
    (_, i) => `op_2026_08_run_${String(i).padStart(3, "0")}`,
  );
}

async function setupMocks(
  page: Page,
  operationLabels: string[],
  options: {
    versionDelayMs?: number;
    defaultLabels?: Record<string, string>;
    operatorLabels?: string[];
  } = {},
): Promise<void> {
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
    if (path === "/version") {
      if (options.versionDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, options.versionDelayMs));
      }
      return route.fulfill(json({
        version: "picker-test",
        display: "picker-test",
        ...(options.defaultLabels ? { default_labels: options.defaultLabels } : {}),
      }));
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
  const chip = page.getByTestId("label-operation");
  await expect(chip).toBeVisible();
  await chip.click();

  const listbox = page.getByRole("listbox");
  await expect(listbox).toBeVisible();
  return listbox;
}

test.describe("operation picker placement", () => {
  test("caps the list height and anchors it to the input", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, operations(60));
    const listbox = await openOperationPicker(page);

    const box = (await listbox.boundingBox())!;
    const input = (await page
      .getByTestId("edit-label-operation")
      .boundingBox())!;

    expect(box.height).toBeLessThanOrEqual(LIST_MAX_HEIGHT);
    // Opens below the input and stays attached to it.
    expect(box.y).toBeGreaterThanOrEqual(input.y + input.height);
    expect(box.y - (input.y + input.height)).toBeLessThan(16);

    // The options that do not fit are reachable by scrolling, not lost.
    const scroll = await listbox.evaluate((el) => ({
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
    }));
    expect(scroll.scrollHeight).toBeGreaterThan(scroll.clientHeight);
  });

  test("keeps the list on screen when it opens above the input", async ({
    page,
  }) => {
    // Too little room below the labels bar, so Fluent flips the list upwards.
    await page.setViewportSize({ width: 1280, height: 420 });
    await setupMocks(page, operations(60));
    const listbox = await openOperationPicker(page);

    const box = (await listbox.boundingBox())!;
    const input = (await page
      .getByTestId("edit-label-operation")
      .boundingBox())!;
    const viewport = page.viewportSize()!;

    expect(box.y).toBeLessThan(input.y);
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

  test("shrinks below the cap when the window is too short for it", async ({
    page,
  }) => {
    // Shorter than the 240px cap, so a flat cap would hang off the screen.
    const viewportHeight = 200;
    await page.setViewportSize({ width: 1280, height: viewportHeight });
    await setupMocks(page, operations(60));
    const listbox = await openOperationPicker(page);

    const box = (await listbox.boundingBox())!;
    expect(box.height).toBeLessThan(LIST_MAX_HEIGHT);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(viewportHeight);
  });

  test("keeps the operation in use on the list, wherever it was chosen", async ({
    page,
  }) => {
    // The labels API only knows names that attacks have been stored under, so
    // one chosen in the other labels bar — or before a refresh — is missing
    // from this response.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "pyrit.globalLabels",
        JSON.stringify({ operator: "roakey", operation: "op_chosen_elsewhere" }),
      );
    });
    await setupMocks(page, ["op_alpha", "op_beta"]);
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

  test("keeps the operation in use reachable past the end of a long list", async ({
    page,
  }) => {
    // The value in use goes to the front of the list. Cap the wrong end and it
    // is the first thing to disappear — whether or not the request returned it.
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.addInitScript(() => {
      window.localStorage.setItem(
        "pyrit.globalLabels",
        JSON.stringify({ operator: "roakey", operation: "op_chosen_elsewhere" }),
      );
    });
    await setupMocks(page, operations(600));
    await openOperationPicker(page);

    const inUse = page.getByRole("option", {
      name: "op_chosen_elsewhere",
      exact: true,
    });
    await expect(inUse).toBeVisible();
    await inUse.click();
    await expect(page.getByTestId("label-operation")).toContainText(
      "op_chosen_elsewhere",
    );
  });

  test("keeps an operation the saved list already holds past the cap", async ({
    page,
  }) => {
    // The usual case: the operation in use is in the response, just not near
    // the front of it.
    const inUseName = "op_2026_08_run_400";
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.addInitScript((name) => {
      window.localStorage.setItem(
        "pyrit.globalLabels",
        JSON.stringify({ operator: "roakey", operation: name }),
      );
    }, inUseName);
    await setupMocks(page, operations(600));
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

    await page.getByRole("option", { name: "op_beta", exact: true }).click();
    await expect(page.getByTestId("label-operation")).toContainText("op_beta");

    await page.reload();

    await expect(page.getByTestId("label-operation")).toContainText("op_beta");
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
    await expect(page.getByTestId("label-operation")).toContainText(
      "op_picked_early",
    );

    // Let the slow response land; it must not undo the choice.
    await page.waitForTimeout(5000);
    await expect(page.getByTestId("label-operation")).toContainText(
      "op_picked_early",
    );
  });

  test("keeps an operation picked before the backend's own default arrives", async ({
    page,
  }) => {
    // Nothing is stored, and the backend supplies its own `operation` default
    // that lands after the bar is already usable. The only thing standing
    // between the pick and that late response is that the value on screen is
    // no longer the built-in placeholder.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha", "op_picked_early"], {
      versionDelayMs: 4000,
      defaultLabels: { operation: "op_configured" },
    });
    await openOperationPicker(page);

    await page
      .getByRole("option", { name: "op_picked_early", exact: true })
      .click();
    await expect(page.getByTestId("label-operation")).toContainText(
      "op_picked_early",
    );

    await page.waitForTimeout(5000);
    await expect(page.getByTestId("label-operation")).toContainText(
      "op_picked_early",
    );
    // What is on screen is also what a refresh would restore.
    expect(
      await page.evaluate(() =>
        window.localStorage.getItem("pyrit.globalLabels"),
      ),
    ).toContain("op_picked_early");
  });

  test("lets the backend still name the operator after you pick an operation", async ({
    page,
  }) => {
    // Picking an operation must not freeze the placeholder operator into
    // storage, where it would outrank a deployment's configured default.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha", "op_beta"]);
    await openOperationPicker(page);

    await page.getByRole("option", { name: "op_beta", exact: true }).click();
    await expect(page.getByTestId("label-operation")).toContainText("op_beta");

    // A later visit, once the deployment configures an operator.
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await setupMocks(page, ["op_alpha", "op_beta"], {
      defaultLabels: { operator: "configured_user" },
    });
    await page.reload();

    await expect(page.getByTestId("label-operator")).toContainText(
      "configured_user",
    );
    await expect(page.getByTestId("label-operation")).toContainText("op_beta");
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

    await page.getByRole("option", { name: "op_beta", exact: true }).click();
    await expect(page.getByTestId("label-operator")).toContainText(
      "configured_day1",
    );

    await page.unrouteAll({ behavior: "ignoreErrors" });
    await setupMocks(page, ["op_alpha", "op_beta"], {
      defaultLabels: { operator: "configured_day2" },
    });
    await page.reload();

    await expect(page.getByTestId("label-operator")).toContainText(
      "configured_day2",
    );
    await expect(page.getByTestId("label-operation")).toContainText("op_beta");
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

    await page.getByTestId("label-operator").click();

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

    await page.getByTestId("label-operator").click();
    await page.getByTestId("edit-label-operator").fill("alice");
    await page.getByTestId("label-operation").click();

    await expect(page.getByRole("listbox")).toBeVisible();
    await page.waitForTimeout(500);
    await expect(page.getByRole("listbox")).toBeVisible();
    // The operator edit still went in; only its clean-up was skipped.
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("label-operator")).toContainText("alice");
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

    await page.getByTestId("label-operator").click();
    await page.getByTestId("edit-label-operator").fill("al");
    await page.getByText("alice", { exact: true }).click();

    await page.waitForTimeout(500);
    await expect(page.getByTestId("label-operator")).toContainText("alice");
  });

  test("starts an edit when the chip is clicked beside the edit control", async ({
    page,
  }) => {
    // The pill's padding sits outside the control that opens the editor, and
    // only a real layout says where that padding actually is.
    await page.setViewportSize({ width: 1280, height: 800 });
    await setupMocks(page, ["op_alpha"]);
    await page.goto("/");

    const chip = page.getByTestId("label-operator");
    await expect(chip).toBeVisible();
    const badge = chip.locator("xpath=..");
    const box = await badge.boundingBox();
    if (!box) throw new Error("chip has no layout");

    // Two pixels in from the pill's left edge is padding, not the control.
    await page.mouse.click(box.x + 2, box.y + box.height / 2);

    await expect(page.getByTestId("edit-label-operator")).toBeVisible();
  });
});

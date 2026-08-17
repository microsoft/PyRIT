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
  options: { versionDelayMs?: number } = {},
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
      return route.fulfill(json({ version: "picker-test", display: "picker-test" }));
    }
    if (path === "/labels") {
      return route.fulfill(json({
        source: "attacks",
        labels: { operator: ["roakey"], operation: operationLabels },
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
});

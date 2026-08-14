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

async function setupMocks(page: Page, operationLabels: string[]): Promise<void> {
  await page.route(/\/api\/labels/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        source: "attacks",
        labels: { operator: ["roakey"], operation: operationLabels },
      }),
    });
  });

  await page.route(/\/api\/attacks(?:\?|$)/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0, limit: 5, offset: 0 }),
    });
  });
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
});

import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return a mock targets list response. */
function mockTargetsList(items: Record<string, unknown>[] = []) {
  return {
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      items,
      pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
    }),
  };
}

const SAMPLE_TARGETS = [
  {
    target_registry_name: "target-chat-1",
    target_type: "OpenAIChatTarget",
    endpoint: "https://api.openai.com",
    model_name: "gpt-4o",
  },
  {
    target_registry_name: "target-image-1",
    target_type: "OpenAIImageTarget",
    endpoint: "https://api.openai.com",
    model_name: "dall-e-3",
  },
];

/** Navigate to the config view. */
async function goToConfig(page: Page) {
  await page.goto("/");
  await page.getByTitle("Configuration").click();
  await expect(page.getByText("Target Configuration")).toBeVisible({ timeout: 10000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Target Configuration Page", () => {
  test("should show loading state then target list", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      // Small delay to see spinner
      await new Promise((r) => setTimeout(r, 200));
      await route.fulfill(mockTargetsList(SAMPLE_TARGETS));
    });

    await goToConfig(page);

    // Table should appear with both targets
    await expect(page.getByText("gpt-4o")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("dall-e-3")).toBeVisible();
    await expect(page.locator("table").getByText("OpenAIChatTarget")).toBeVisible();
    await expect(page.locator("table").getByText("OpenAIImageTarget")).toBeVisible();
  });

  test("should show empty state when no targets exist", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill(mockTargetsList([]));
    });

    await goToConfig(page);

    await expect(page.getByText("No Targets Configured")).toBeVisible();
    await expect(page.getByRole("button", { name: /create first target/i })).toBeVisible();
  });

  test("should show error state on API failure", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill({ status: 500, body: "Internal Server Error" });
    });

    await goToConfig(page);

    await expect(page.getByText(/error/i)).toBeVisible({ timeout: 10000 });
  });

  test("should set a target active", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill(mockTargetsList(SAMPLE_TARGETS));
    });

    await goToConfig(page);
    await expect(page.getByText("gpt-4o")).toBeVisible({ timeout: 10000 });

    // Both rows should have a "Set Active" button initially
    const setActiveBtns = page.getByRole("button", { name: /set active/i });
    await expect(setActiveBtns.first()).toBeVisible();
    await setActiveBtns.first().click();

    // After clicking, the first target should show "Active" badge
    await expect(page.locator("table").getByText("Active", { exact: true }).first()).toBeVisible();
  });

  test("should open create target dialog", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill(mockTargetsList([]));
    });

    await goToConfig(page);

    // Click the "New Target" button in the header
    await page.getByRole("button", { name: /new target/i }).click();

    // Dialog should open
    await expect(page.getByText("Create New Target")).toBeVisible();
    await expect(page.getByText("Create Target")).toBeVisible();
  });

  test("should refresh targets on Refresh click", async ({ page }) => {
    // Start with initial targets, then after refresh show an additional one.
    // Using a flag-based approach avoids React StrictMode double-mount issues.
    let showExtra = false;
    await page.route(/\/api\/targets/, async (route) => {
      const base = [SAMPLE_TARGETS[0]];
      const items = showExtra ? [...base, SAMPLE_TARGETS[1]] : base;
      await route.fulfill(mockTargetsList(items));
    });

    await goToConfig(page);
    // First load shows one target
    await expect(page.getByText("gpt-4o")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("dall-e-3")).not.toBeVisible();

    // Flip the flag and click refresh
    showExtra = true;
    await page.getByRole("button", { name: /refresh/i }).click();

    // Second target should now appear
    await expect(page.getByText("dall-e-3")).toBeVisible({ timeout: 10000 });
  });
});

test.describe("Create Target Dialog", () => {
  test("should create a target through the dialog", async ({ page }) => {
    let createdTarget: Record<string, unknown> | null = null;

    await page.route(/\/api\/targets/, async (route) => {
      if (route.request().method() === "POST") {
        const body = JSON.parse(route.request().postData() ?? "{}");
        createdTarget = {
          target_registry_name: "new-target-1",
          target_type: body.type,
          endpoint: body.params?.endpoint,
          model_name: body.params?.model_name,
        };
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify(createdTarget),
        });
      } else {
        // GET — return the created target if available
        const items = createdTarget ? [createdTarget] : [];
        await route.fulfill(mockTargetsList(items));
      }
    });

    await goToConfig(page);

    // Click "New Target" button
    await page.getByRole("button", { name: /new target/i }).click();
    await expect(page.getByText("Create New Target")).toBeVisible();

    // Fill form fields
    const dialog = page.locator('[role="dialog"]');

    // Select target type
    await dialog.locator("select").selectOption("OpenAIChatTarget");

    // Fill endpoint
    await dialog.getByPlaceholder("https://your-resource.openai.azure.com/").fill("https://my-endpoint.openai.azure.com/");

    // Fill model name
    await dialog.getByPlaceholder("e.g. gpt-4o, my-deployment").fill("gpt-4o-test");

    // Click Create Target
    await dialog.getByRole("button", { name: "Create Target" }).click();

    // Dialog should close and target should appear in the list
    await expect(page.getByText("Create New Target")).not.toBeVisible({ timeout: 5_000 });
    await expect(page.getByText("gpt-4o-test")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("OpenAIChatTarget")).toBeVisible();
  });

  test("should show validation errors for empty required fields", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill(mockTargetsList([]));
    });

    await goToConfig(page);

    // Open dialog
    await page.getByRole("button", { name: /new target/i }).click();
    await expect(page.getByText("Create New Target")).toBeVisible();

    // The Create Target button should be disabled when fields are empty
    const createBtn = page.locator('[role="dialog"]').getByRole("button", { name: "Create Target" });
    await expect(createBtn).toBeDisabled();

    // Fill only endpoint (no target type) — button should still be disabled
    await page.locator('[role="dialog"]').getByPlaceholder("https://your-resource.openai.azure.com/").fill("https://test.com");
    await expect(createBtn).toBeDisabled();

    // Clear endpoint, select type — button should still be disabled
    await page.locator('[role="dialog"]').getByPlaceholder("https://your-resource.openai.azure.com/").fill("");
    await page.locator('[role="dialog"]').locator("select").selectOption("OpenAIChatTarget");
    await expect(createBtn).toBeDisabled();

    // Fill both — button should be enabled
    await page.locator('[role="dialog"]').getByPlaceholder("https://your-resource.openai.azure.com/").fill("https://test.com");
    await expect(createBtn).toBeEnabled();
  });
});

test.describe("Target Config ↔ Chat Navigation", () => {
  test("should display active target info in chat after setting it", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill(mockTargetsList(SAMPLE_TARGETS));
    });

    await goToConfig(page);
    await expect(page.getByText("gpt-4o")).toBeVisible({ timeout: 10000 });

    // Set first target active
    await page.getByRole("button", { name: /set active/i }).first().click();

    // Navigate back to chat
    await page.getByTitle("Chat").click();
    await expect(page.getByText("PyRIT Attack")).toBeVisible();

    // Chat should show the active target type
    await expect(page.getByText("OpenAIChatTarget")).toBeVisible();
    await expect(page.getByText(/gpt-4o/)).toBeVisible();
  });

  test("should enable chat input after a target is set", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill(mockTargetsList(SAMPLE_TARGETS));
    });

    // Start in chat — no-target-banner should be visible
    await page.goto("/");
    await expect(page.getByTestId("no-target-banner")).toBeVisible();

    // Go to config, set a target
    await page.getByTitle("Configuration").click();
    await expect(page.getByText("gpt-4o")).toBeVisible({ timeout: 10000 });
    await page.getByRole("button", { name: /set active/i }).first().click();

    // Return to chat — send should be enabled when there's text
    await page.getByTitle("Chat").click();
    const input = page.getByRole("textbox");
    await input.fill("Hello");
    await expect(page.getByRole("button", { name: /send/i })).toBeEnabled();
  });
});

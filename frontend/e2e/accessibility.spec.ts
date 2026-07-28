import { test, expect, type Locator, type Page } from "@playwright/test";
import { makeTarget } from "./_targets";

async function expectMinimumTouchTarget(locator: Locator, minimum = 44): Promise<void> {
  await expect(locator).toBeVisible();

  const box = await locator.boundingBox();

  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(minimum);
  expect(box!.height).toBeGreaterThanOrEqual(minimum);
}

async function expectTourContained(page: Page, dialog: Locator, checkTouchTargets: boolean): Promise<void> {
  await expect(dialog).toBeVisible();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
      })
  );

  await expect
    .poll(async () => {
      const box = await dialog.boundingBox();
      const viewport = page.viewportSize();
      const dimensions = await page.evaluate(() => ({
        clientHeight: document.documentElement.clientHeight,
        clientWidth: document.documentElement.clientWidth,
        scrollHeight: document.documentElement.scrollHeight,
        scrollWidth: document.documentElement.scrollWidth,
      }));

      return (
        box !== null &&
        viewport !== null &&
        box.x >= 0 &&
        box.x + box.width <= viewport.width + 1 &&
        box.y >= 0 &&
        box.y + box.height <= viewport.height + 1 &&
        dimensions.scrollHeight <= dimensions.clientHeight + 1 &&
        dimensions.scrollWidth <= dimensions.clientWidth + 1
      );
    })
    .toBe(true);

  const actions = dialog.getByRole("button");
  const actionCount = await actions.count();

  expect(actionCount).toBeGreaterThan(0);

  for (let index = 0; index < actionCount; index += 1) {
    const action = actions.nth(index);
    await expect(action).toBeVisible();

    if (checkTouchTargets) {
      await expectMinimumTouchTarget(action);
    }
  }
}

test.describe("Accessibility", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("should have accessible form controls", async ({ page }) => {
    // Mock a target so the input area is rendered
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            makeTarget({
              target_registry_name: "a11y-form-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            }),
          ],
          pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
        }),
      });
    });

    // Navigate to config, set active, return to chat so input is enabled
    await page.getByTitle("Configuration").click();
    await expect(page.getByText("Target Configuration")).toBeVisible({ timeout: 10000 });
    const setActiveBtn = page.getByRole("button", { name: /set active/i });
    await expect(setActiveBtn).toBeVisible({ timeout: 5000 });
    await setActiveBtn.click();
    await page.getByTitle("Chat").click();

    // Input should be accessible
    const input = page.getByRole("textbox");
    await expect(input).toBeVisible({ timeout: 5000 });

    // Send button should have accessible name
    const sendButton = page.getByRole("button", { name: /send/i });
    await expect(sendButton).toBeVisible();

    // New Attack button should have accessible name
    const newAttackButton = page.getByRole("button", { name: /new attack/i });
    await expect(newAttackButton).toBeVisible();
  });

  test("should have accessible sidebar navigation", async ({ page }) => {
    // Chat button
    const chatBtn = page.getByTitle("Chat");
    await expect(chatBtn).toBeVisible();

    // Configuration button
    const configBtn = page.getByTitle("Configuration");
    await expect(configBtn).toBeVisible();

    // Theme toggle button (now a menu trigger with "Theme: <mode>" title)
    const themeBtn = page.getByTitle(/^Theme:/);
    await expect(themeBtn).toBeVisible();
  });

  test("should restore focus to Feedback after closing its dialog with Escape", async ({ page }) => {
    const feedbackButton = page.getByRole("button", { name: "Feedback" });
    await expect(feedbackButton).toBeVisible();
    await feedbackButton.focus();
    await feedbackButton.press("Enter");

    const dialog = page.getByRole("dialog", { name: "Send feedback" });
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(":focus")).toBeVisible();

    await page.keyboard.press("Escape");

    await expect(dialog).toBeHidden();
    await expect(feedbackButton).toBeFocused();
  });

  test("should restore focus to Feedback after cancelling its dialog", async ({ page }) => {
    const feedbackButton = page.getByRole("button", { name: "Feedback" });
    await feedbackButton.click();

    const dialog = page.getByRole("dialog", { name: "Send feedback" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();

    await expect(dialog).toBeHidden();
    await expect(feedbackButton).toBeFocused();
  });

  test("should be navigable with keyboard", async ({ page }) => {
    // Wait for the sidebar to render so there is a focusable element for Tab
    // to land on, and dispatch the Tab through `body` (rather than the bare
    // keyboard) to guarantee the document has focus when the keystroke fires.
    // Without both, Chromium sometimes leaves `:focus` empty under parallel
    // worker load.
    await expect(page.getByTitle("Home")).toBeVisible();
    await page.locator("body").press("Tab");
    const focused = page.locator(":focus");
    await expect(focused).toBeVisible();

    // Continue tabbing through elements
    await page.keyboard.press("Tab");
    await expect(page.locator(":focus")).toBeVisible();
  });

  test("should have proper focus management", async ({ page }) => {
    // Mock a target so the input is enabled
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            makeTarget({
              target_registry_name: "a11y-focus-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            }),
          ],
          pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
        }),
      });
    });

    // Navigate to config, set active, return to chat so input is enabled
    await page.getByTitle("Configuration").click();
    await expect(page.getByText("Target Configuration")).toBeVisible({ timeout: 10000 });
    const setActiveBtn = page.getByRole("button", { name: /set active/i });
    await expect(setActiveBtn).toBeVisible({ timeout: 5000 });
    await setActiveBtn.click();
    await page.getByTitle("Chat").click();

    const input = page.getByRole("textbox");
    await expect(input).toBeEnabled({ timeout: 5000 });

    // Focus input
    await input.focus();
    await expect(input).toBeFocused();

    // Type and verify focus is maintained
    await input.fill("Test");
    await expect(input).toBeFocused();
  });

  test("should have accessible target table in config view", async ({ page }) => {
    // Mock targets API for consistent test
    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            makeTarget({
              target_registry_name: "a11y-test-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            }),
            makeTarget({
              target_registry_name: "a11y-second-target",
              target_type: "TextTarget",
              endpoint: "https://test.com/text",
              model_name: "text-model",
            }),
          ],
          pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
        }),
      });
    });

    // Navigate to config
    await page.getByTitle("Configuration").click();
    await expect(page.getByText("Target Configuration")).toBeVisible();

    // Table should exist
    const table = page.getByRole("table");
    await expect(table).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Filter by type:" })).toBeVisible();
  });

  test("major views expose page headings and one primary navigation landmark", async ({ page }) => {
    const navigation = page.getByRole("navigation", { name: "Primary" });

    await expect(navigation).toHaveCount(1);
    await expect(
      page.getByRole("heading", { level: 1, name: "Welcome to Co-PyRIT" })
    ).toBeVisible();
    await expect(navigation.getByRole("button", { name: "Home" })).toHaveAttribute(
      "aria-current",
      "page"
    );

    const views = [
      { button: "Attack History", heading: "Attack History" },
      { button: "Configuration", heading: "Target Configuration" },
      { button: "Chat", heading: "Chat" },
    ];

    for (const view of views) {
      await navigation.getByRole("button", { name: view.button }).click();
      await expect(
        page.getByRole("heading", { level: 1, name: view.heading })
      ).toBeAttached();
      await expect(page.locator("main h1")).toHaveCount(1);
      await expect(navigation.getByRole("button", { name: view.button })).toHaveAttribute(
        "aria-current",
        "page"
      );
    }
  });

  test("mobile audit controls provide 44px touch targets", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await expectMinimumTouchTarget(page.getByRole("button", { name: "Labels" }));
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Configure a target" }));
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Take a tour" }));

    await page.route(/\/api\/targets/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            makeTarget({
              target_registry_name: "mobile-touch-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            }),
          ],
          pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
        }),
      });
    });

    await page.getByRole("button", { name: "Configuration" }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Target Configuration" })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Refresh" }));
    await expectMinimumTouchTarget(page.getByRole("button", { name: "New Target" }));
  });

  for (const viewport of [
    { name: "mobile", width: 390, height: 844 },
    { name: "desktop", width: 1280, height: 800 },
  ]) {
    test(`tour remains contained and actionable on ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.getByRole("button", { name: "Take a tour" }).click();

      const dialog = page.getByRole("alertdialog");

      for (let step = 0; step < 5; step += 1) {
        await expect(dialog).toContainText(`${step + 1} of 5`);
        await expectTourContained(page, dialog, viewport.name === "mobile");

        if (viewport.name === "desktop" && step === 0) {
          const targetBox = await page.locator('[data-tour="sidebar-nav"]').boundingBox();
          const dialogBox = await dialog.boundingBox();

          expect(targetBox).not.toBeNull();
          expect(dialogBox).not.toBeNull();
          expect(dialogBox!.x).toBeGreaterThanOrEqual(targetBox!.x + targetBox!.width);
        }

        if (step < 4) {
          await dialog.getByRole("button", { name: "Next", exact: true }).click();
        }
      }

      await dialog.getByRole("button", { name: "Anchors Away!", exact: true }).click();
      await expect(dialog).toBeHidden();
      await expect(page).toHaveURL(/\/$/);
    });
  }
});

test.describe("Visual Consistency", () => {
  test("should render without layout shifts", async ({ page }) => {
    await page.goto("/");

    // Wait for initial render then navigate to chat to measure the chat ribbon
    await expect(page.getByTitle("Chat")).toBeVisible();
    await page.getByTitle("Chat").click();
    const anchor = page.getByTestId("new-attack-btn");
    await expect(anchor).toBeVisible();

    // Take measurements
    const initialBox = await anchor.boundingBox();

    // Wait a moment for any delayed renders
    await page.waitForTimeout(500);

    // Verify position hasn't changed
    const finalBox = await anchor.boundingBox();

    if (initialBox && finalBox) {
      expect(finalBox.x).toBe(initialBox.x);
      expect(finalBox.y).toBe(initialBox.y);
    }
  });
});

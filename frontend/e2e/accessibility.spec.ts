import { test, expect } from "@playwright/test";

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
            {
              target_registry_name: "a11y-form-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            },
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

    // Theme toggle button
    const themeBtn = page.getByTitle(/light mode|dark mode/i);
    await expect(themeBtn).toBeVisible();
  });

  test("should be navigable with keyboard", async ({ page }) => {
    // Tab to the first interactive element
    await page.keyboard.press("Tab");
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
            {
              target_registry_name: "a11y-focus-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            },
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
            {
              target_registry_name: "a11y-test-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://test.com",
              model_name: "gpt-4o",
            },
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
  });
});

test.describe("Visual Consistency", () => {
  test("should render without layout shifts", async ({ page }) => {
    await page.goto("/");

    // Wait for initial render
    await expect(page.getByText("PyRIT Attack")).toBeVisible();

    // Take measurements
    const header = page.getByText("PyRIT Attack");
    const initialBox = await header.boundingBox();

    // Wait a moment for any delayed renders
    await page.waitForTimeout(500);

    // Verify position hasn't changed
    const finalBox = await header.boundingBox();

    if (initialBox && finalBox) {
      expect(finalBox.x).toBe(initialBox.x);
      expect(finalBox.y).toBe(initialBox.y);
    }
  });
});

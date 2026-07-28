import { test, expect, type Locator, type Page } from "@playwright/test";
import { makeTarget } from "./_targets";

async function expectMinimumTouchTarget(locator: Locator, minimum = 44): Promise<void> {
  await expect(locator).toBeVisible();

  const box = await locator.boundingBox();

  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(minimum);
  expect(box!.height).toBeGreaterThanOrEqual(minimum);
}

async function expectCompactDesktopTarget(locator: Locator, maximumHeight = 40): Promise<void> {
  await expect(locator).toBeVisible();

  const box = await locator.boundingBox();

  expect(box).not.toBeNull();
  expect(box!.height).toBeLessThanOrEqual(maximumHeight);
}

async function expectNoDocumentOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));

  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
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
    } else {
      await expectCompactDesktopTarget(action);
    }
  }
}

const MOBILE_AUDIT_ATTACK = {
  attack_result_id: "mobile-audit-attack",
  conversation_id: "mobile-audit-conversation",
  attack_type: "SingleTurnAttack",
  target: { target_type: "OpenAIChatTarget", model_name: "gpt-4o" },
  converters: [],
  outcome: "success",
  last_message_preview: "Mobile audit response",
  message_count: 2,
  related_conversation_ids: [],
  labels: { operator: "mobile", operation: "audit" },
  created_at: "2026-07-28T12:00:00.000Z",
  updated_at: "2026-07-28T12:00:00.000Z",
};

const MOBILE_AUDIT_TARGETS = [
  makeTarget({
    target_registry_name: "mobile-chat-target",
    target_type: "OpenAIChatTarget",
    endpoint: "https://test.com/chat",
    model_name: "gpt-4o",
    capabilities: { supports_multi_turn: true, supports_system_prompt: true },
  }),
  makeTarget({
    target_registry_name: "mobile-image-target",
    target_type: "OpenAIImageTarget",
    endpoint: "https://test.com/image",
    model_name: "image-model",
  }),
  makeTarget({
    target_registry_name: "mobile-round-robin-target",
    target_type: "RoundRobinTarget",
    target_specific_params: { weights: [1] },
    inner_targets: [
      {
        target_registry_name: "mobile-inner-target",
        target_type: "OpenAIChatTarget",
        endpoint: "https://test.com/inner",
        model_name: "inner-model",
      },
    ],
  }),
];

interface ResponsiveAuditRouteOptions {
  empty?: boolean;
  failAttacks?: boolean;
}

async function routeResponsiveAuditData(
  page: Page,
  { empty = false, failAttacks = false }: ResponsiveAuditRouteOptions = {}
): Promise<void> {
  await page.route(/\/api\/targets\/catalog(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          { target_type: "OpenAIChatTarget", parameters: [], supported_auth_modes: ["api_key"] },
          { target_type: "OpenAIImageTarget", parameters: [], supported_auth_modes: ["api_key"] },
          { target_type: "RoundRobinTarget", parameters: [], supported_auth_modes: ["api_key"] },
        ],
      }),
    });
  });
  await page.route(/\/api\/targets(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: empty ? [] : MOBILE_AUDIT_TARGETS,
        pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
      }),
    });
  });
  await page.route(/\/api\/attacks\/attack-options/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ attack_types: ["SingleTurnAttack"] }),
    });
  });
  await page.route(/\/api\/attacks\/converter-options/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ converter_types: ["Base64Converter"] }),
    });
  });
  await page.route(/\/api\/labels/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        source: "attacks",
        labels: { operator: ["mobile"], operation: ["audit"] },
      }),
    });
  });
  await page.route(/\/api\/attacks\/mobile-audit-attack\/messages/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        messages: {
          messages: [],
        },
      }),
    });
  });
  await page.route(/\/api\/attacks\/mobile-audit-attack$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOBILE_AUDIT_ATTACK),
    });
  });
  await page.route(/\/api\/attacks(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          attack_result_id: "mobile-audit-attack",
          conversation_id: "mobile-audit-conversation",
        }),
      });
      return;
    }

    if (failAttacks) {
      await route.fulfill({ status: 500, body: "History unavailable" });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: empty ? [] : [MOBILE_AUDIT_ATTACK],
        pagination: {
          limit: 25,
          has_more: !empty,
          next_cursor: empty ? null : "next",
          prev_cursor: null,
        },
      }),
    });
  });
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
    await routeResponsiveAuditData(page);
    await page.reload();

    await expectMinimumTouchTarget(page.getByRole("button", { name: "Labels" }));
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Configure a target" }));
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Take a tour" }));
    const recentAttackRows = page.locator('[data-testid^="home-open-attack-"]');
    await expect(recentAttackRows).toHaveCount(1);
    await expectMinimumTouchTarget(recentAttackRows.first());
    await expectNoDocumentOverflow(page);

    await page.getByRole("button", { name: "Configuration" }).click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Target Configuration" })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Refresh" }));
    await expectMinimumTouchTarget(page.getByRole("button", { name: "New Target" }));
    await expectMinimumTouchTarget(page.getByRole("combobox", { name: "Filter by type:" }));

    const setActiveButtons = page.getByRole("button", { name: "Set Active" });
    await expect(setActiveButtons).toHaveCount(3);
    for (let index = 0; index < await setActiveButtons.count(); index += 1) {
      await expectMinimumTouchTarget(setActiveButtons.nth(index));
    }
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Expand inner targets" }));
    await setActiveButtons.first().click();
    await expectNoDocumentOverflow(page);

    await page.getByRole("button", { name: "Attack History" }).click();
    await expect(page.getByTestId("attacks-table")).toBeVisible();
    await expectMinimumTouchTarget(page.getByTestId("refresh-btn"));
    for (const testId of [
      "attack-type-filter",
      "outcome-filter",
      "converter-filter",
      "operator-filter",
      "operation-filter",
      "label-filter",
    ]) {
      await expectMinimumTouchTarget(page.getByTestId(testId));
    }
    await expectMinimumTouchTarget(page.getByTestId("open-attack-mobile-audit-attack"));
    await expectMinimumTouchTarget(page.getByTestId("attack-row-mobile-audit-attack"));
    await expectMinimumTouchTarget(page.getByTestId("next-page-btn"));
    await expectNoDocumentOverflow(page);

    await page.getByRole("button", { name: "Chat" }).click();
    await expectMinimumTouchTarget(page.getByTestId("toggle-system-prompt-btn"));
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Attach files" }));
    await expectMinimumTouchTarget(page.getByTestId("toggle-converter-panel-btn"));
    await page.getByPlaceholder("Type prompt here").fill("Create a populated mobile chat");
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Send message" }));
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.getByTestId("toggle-panel-btn")).toBeEnabled();
    await expectMinimumTouchTarget(page.getByTestId("toggle-panel-btn"));
    await expectMinimumTouchTarget(page.getByTestId("new-attack-btn"));
    await expectNoDocumentOverflow(page);
  });

  test("empty mobile audit actions provide 44px touch targets", async ({ page }) => {
    await page.setViewportSize({ width: 600, height: 844 });
    await routeResponsiveAuditData(page, { empty: true });
    await page.reload();

    await expectMinimumTouchTarget(page.getByTestId("home-start-attack-btn"));
    await expectNoDocumentOverflow(page);

    await page.getByRole("button", { name: "Configuration" }).click();
    await expectMinimumTouchTarget(page.getByRole("button", { name: "Create First Target" }));
    await expectNoDocumentOverflow(page);

    await page.getByRole("button", { name: "Chat" }).click();
    await expectMinimumTouchTarget(page.getByTestId("configure-target-input-btn"));
    await expectNoDocumentOverflow(page);
  });

  test("mobile history retry action provides a 44px touch target", async ({ page }) => {
    await page.setViewportSize({ width: 600, height: 844 });
    await routeResponsiveAuditData(page, { failAttacks: true });
    await page.reload();

    await page.getByRole("button", { name: "Attack History" }).click();
    await expectMinimumTouchTarget(page.getByTestId("retry-btn"));
    await expectNoDocumentOverflow(page);
  });

  test("desktop audit controls retain compact density", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await routeResponsiveAuditData(page);
    await page.reload();

    await expectCompactDesktopTarget(page.getByTestId("home-open-attack-mobile-audit-attack"));

    await page.getByRole("button", { name: "Configuration" }).click();
    await expectCompactDesktopTarget(page.getByRole("combobox", { name: "Filter by type:" }));
    await expectCompactDesktopTarget(page.getByRole("button", { name: "Set Active" }).first());
    await expectCompactDesktopTarget(page.getByRole("button", { name: "Expand inner targets" }));
    await page.getByRole("button", { name: "Set Active" }).first().click();

    await page.getByRole("button", { name: "Attack History" }).click();
    await expect(page.getByTestId("attacks-table")).toBeVisible();
    await expectCompactDesktopTarget(page.getByTestId("refresh-btn"));
    await expectCompactDesktopTarget(page.getByTestId("attack-type-filter"));
    await expectCompactDesktopTarget(page.getByTestId("open-attack-mobile-audit-attack"));
    await expectCompactDesktopTarget(page.getByTestId("next-page-btn"));

    await page.getByRole("button", { name: "Chat" }).click();
    await expectCompactDesktopTarget(page.getByTestId("toggle-system-prompt-btn"));
    await expectCompactDesktopTarget(page.getByTestId("toggle-panel-btn"));
    await expectCompactDesktopTarget(page.getByTestId("new-attack-btn"));
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

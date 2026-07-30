import { expect, test } from "@playwright/test";

import { makeTarget } from "./_targets";

test.describe("Onboarding tour", () => {
  test("guides a user with no active target through the visible prerequisite", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Take a tour" }).click();

    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(dialog).toContainText(
      "target selection happens in Configuration"
    );
    await expect(dialog).toContainText("choose Configure a target");
    await expect(dialog).toContainText("use Set Active there");
    await expect(dialog).not.toContainText("come back here to select it");
    await expect(page.locator('[data-tour="target-card"]')).toBeVisible();

    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(page).toHaveURL(/\/chat$/);
    await expect(dialog).toContainText(
      "Chat needs an active target before the message composer is available"
    );
    await expect(dialog).toContainText(
      "After the tour, choose Configure Target"
    );
    await expect(dialog).toContainText(
      "The message input and converter control appear once a target is active"
    );
    await expect(
      page.locator('[data-tour="chat-prerequisite"]')
    ).toHaveAttribute("data-testid", "no-target-banner");
    await expect(page.locator('[data-tour="converter-toggle"]')).toHaveCount(0);
  });

  test("guides a user with an active target to the visible converter control", async ({
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
      .getByRole("button", { name: "Configuration", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Target Configuration" })
    ).toBeVisible();
    await page.getByRole("button", { name: "Set Active", exact: true }).click();
    await page.getByRole("button", { name: "Home", exact: true }).click();
    await expect(page.getByTestId("home-target-active")).toContainText("gpt-4o");

    await page.getByRole("button", { name: "Take a tour" }).click();
    const dialog = page.getByRole("alertdialog");
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();

    await expect(dialog).toContainText("target currently active for Chat");
    await expect(dialog).toContainText("use Set Active in Configuration");
    await expect(page.locator('[data-tour="target-card"]')).toBeVisible();

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
});

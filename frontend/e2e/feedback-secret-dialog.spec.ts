import { expect, test, type Page } from "@playwright/test";

const EMPTY_PAGINATION = {
  limit: 50,
  has_more: false,
  next_cursor: null,
  prev_cursor: null,
};

const API_RESPONSES: Record<string, unknown> = {
  "/auth/config": { clientId: "", tenantId: "", allowedGroupIds: "" },
  "/auth/access": { isAdmin: false },
  "/version": {
    version: "feedback-test",
    display: "feedback-test",
    default_labels: { operator: "test", operation: "feedback" },
  },
  "/labels": {
    source: "attacks",
    labels: { operator: ["test"], operation: ["feedback"] },
  },
  "/attacks": { items: [], pagination: EMPTY_PAGINATION },
};

async function installFeedbackMocks(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, "");
    const hasResponse = Object.hasOwn(API_RESPONSES, path);

    await route.fulfill({
      status: hasResponse ? 200 : 404,
      contentType: "application/json",
      body: JSON.stringify(
        hasResponse ? API_RESPONSES[path] : { detail: `No mock for ${path}` },
      ),
    });
  });
}

for (const submission of ["button click", "contact-field Enter"]) {
  for (const dismissal of ["Go back and fix", "Escape"]) {
    test(`${dismissal} restores feedback after ${submission}`, async ({ page }) => {
      await installFeedbackMocks(page);
      await page.goto("/");

      const feedbackButton = page.getByRole("button", { name: "Feedback", exact: true });
      await feedbackButton.click();
      const feedbackSurface = page
        .locator('[role="dialog"]')
        .filter({ hasText: "Send feedback" });
      const feedbackDialog = page.getByRole("dialog", { name: "Send feedback" });
      await expect(feedbackDialog).toBeVisible();

      await page.getByRole("combobox", { name: "Category" }).selectOption("bug");
      const description = page.getByTestId("feedback-bug-describe-input");
      const reproduction = page.getByTestId("feedback-bug-repro-input");
      const contact = page.getByTestId("feedback-contact-input");
      const descriptionText =
        "This is a sufficiently long bug description for the feedback form.";
      const reproductionText =
        "Use synthetic key sk-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345 to reproduce.";
      await description.fill(descriptionText);
      await reproduction.fill(reproductionText);
      await contact.fill("reviewer");

      const submitButton = page.getByTestId("feedback-submit-button");
      if (submission === "contact-field Enter") {
        await contact.press("Enter");
      } else {
        await submitButton.click();
      }
      const confirmDialog = page.getByTestId("feedback-confirm-dialog");
      await expect(confirmDialog).toBeVisible();

      if (dismissal === "Escape") {
        await page.getByTestId("feedback-confirm-cancel").press("Escape");
      } else {
        await page.getByTestId("feedback-confirm-cancel").click();
      }

      await expect(confirmDialog).toBeHidden();
      await expect(feedbackSurface).toBeVisible();
      await expect(feedbackSurface).not.toHaveAttribute("aria-hidden", "true");
      await expect(feedbackDialog).toBeVisible();
      await expect(submitButton).toBeFocused();
      await expect(description).toHaveValue(descriptionText);
      await expect(reproduction).toHaveValue(reproductionText);
      await expect(contact).toHaveValue("reviewer");

      await page.keyboard.press("Shift+Tab");
      const cancelButton = feedbackDialog.getByRole("button", { name: "Cancel" });
      await expect(cancelButton).toBeFocused();

      await description.click();
      await description.fill("Updated description after returning from the warning.");
      await expect(description).toHaveValue(
        "Updated description after returning from the warning.",
      );

      await cancelButton.click();
      await expect(feedbackDialog).toBeHidden();
      await expect(feedbackButton).toBeFocused();
    });
  }
}

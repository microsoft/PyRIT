import { expect, test } from "@playwright/test";

test.use({ isMobile: true, viewport: { width: 390, height: 844 } });

test("keeps initializer navigation usable under a touch-sized warning", async ({ page }) => {
  await page.goto("/e2e/fixtures/editor-workspace.html");

  const retry = page.getByRole("button", { name: "Retry refresh" });
  const navigation = page.getByRole("navigation", { name: "Custom initializer files" });
  const second = navigation.getByRole("button", { name: "second" });
  await expect(retry).toBeVisible();
  await expect.poll(async () => (await navigation.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
  await expect(second).toBeVisible();
  await second.click();
  await expect(second).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("second source")).toBeVisible();
  await expect(retry).toBeVisible();
});

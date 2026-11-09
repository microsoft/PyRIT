import { expect, test } from "@playwright/test";

test("@seeded saves sidebar expansion and keeps narrow navigation separate", async ({ page }) => {
  await page.goto("/attack/chat");
  const draft = page.getByRole("textbox", { name: "Type prompt here", exact: true });
  await draft.fill("Keep this draft when the sidebar changes.");
  await page.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
  await expect(page.getByRole("button", { name: "Attacks", exact: true })).toHaveText("");
  await expect(draft).toHaveValue("Keep this draft when the sidebar changes.");

  await page.reload();
  await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
  await expect(page.getByRole("button", { name: "Attacks", exact: true })).toHaveText("Attacks");
  await page.reload();
  await expect(page.getByRole("button", { name: "Collapse sidebar", exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  const opener = page.getByRole("button", { name: "Expand sidebar", exact: true });
  await opener.click();
  const navigation = page.getByRole("dialog", { name: "Navigation", exact: true });
  const close = navigation.getByRole("button", { name: "Collapse sidebar", exact: true });
  await expect(close).toBeFocused();
  await close.press("Escape");
  await expect(navigation).not.toBeVisible();
  await expect(opener).toBeFocused();

  await opener.click();
  await navigation.getByRole("button", { name: "Scanner", exact: true }).click();
  await expect(navigation).not.toBeVisible();
  await expect(page).toHaveURL(/\/scanner\/launch$/);
  await expect(page.getByRole("button", { name: "Scanner", exact: true })).toHaveAttribute("aria-current", "page");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await page.setViewportSize({ width: 1280, height: 900 });
  await expect(page.getByRole("button", { name: "Collapse sidebar", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Scanner", exact: true })).toHaveText("Scanner");
});

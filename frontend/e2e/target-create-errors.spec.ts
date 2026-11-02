import { test, expect, type Locator, type Page } from "./_fixtures";

// #3710: a failed create keeps focus in the dialog, focuses and announces the error, and marks the field.

const TARGET_TYPES = {
  items: [
    {
      target_type: "HTTPTarget",
      parameters: [
        {
          name: "http_request",
          type_name: "str",
          required: true,
          default: null,
          multiline: true,
          description: "The HTTP request template containing the prompt placeholder.",
        },
        {
          name: "use_tls",
          type_name: "bool",
          required: false,
          default: "True",
          description: "Whether to use TLS.",
        },
      ],
      supported_auth_modes: ["api_key"],
      description: "Sends prompts through a raw HTTP request template.",
    },
    {
      // One text input, so Enter submits the form implicitly.
      target_type: "WebhookTarget",
      parameters: [
        {
          name: "webhook_url",
          type_name: "str",
          required: true,
          default: null,
          description: "The URL that receives each prompt.",
        },
      ],
      supported_auth_modes: ["api_key"],
      description: "Posts prompts to a webhook.",
    },
  ],
};

interface HeldCreate {
  /** Resolves once the browser has sent the create request. */
  readonly requested: Promise<Record<string, unknown>>;
  /** Lets the held create request fail with a 400. */
  readonly release: () => void;
}

/** Mock the target routes, holding the create request until released and then rejecting it. */
async function holdCreateThenReject(page: Page, detail: string): Promise<HeldCreate> {
  let release: () => void = () => {};
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  let markRequested: (body: Record<string, unknown>) => void = () => {};
  const requested = new Promise<Record<string, unknown>>((resolve) => {
    markRequested = resolve;
  });

  await page.route(/\/api\/targets\/types(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TARGET_TYPES),
    });
  });
  await page.route(/\/api\/targets(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      markRequested(JSON.parse(route.request().postData() ?? "{}"));
      await released;
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [],
        pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
      }),
    });
  });

  return { requested, release };
}

async function openCreateTargetDialog(page: Page): Promise<Locator> {
  await page.goto("/");
  await page.getByRole("button", { name: "Registry", exact: true }).click();
  await expect(page.getByText("Target Registry")).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: /new target/i }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Create New Target")).toBeVisible();
  return dialog;
}

async function selectTargetType(page: Page, dialog: Locator, targetType: string): Promise<void> {
  const picker = dialog.getByRole("combobox", { name: "Target Type" });
  await expect(picker).toBeEnabled();
  await picker.click();
  await page.getByRole("option", {
    name: new RegExp(`Implementation: ${targetType}`),
  }).click();
}

/** Tag name of the focused element, so a failure reads e.g. `BODY`. */
async function activeElementTag(page: Page): Promise<string> {
  return page.evaluate(() => document.activeElement?.tagName ?? "none");
}

test.describe("Create Target errors (#3710)", () => {
  test("keeps focus on Create while in flight, then focuses the announced error", async ({ page }) => {
    const HTTP_ERROR = "Invalid HTTP request line";
    const create = await holdCreateThenReject(page, HTTP_ERROR);
    const dialog = await openCreateTargetDialog(page);

    await selectTargetType(page, dialog, "HTTPTarget");
    const httpRequest = dialog.getByLabel(/Http Request/);
    await httpRequest.fill("not an HTTP request");

    const createButton = dialog.getByRole("button", { name: /^(Create Target|Creating\.\.\.)$/ });
    await createButton.focus();
    await page.keyboard.press("Space");

    expect(await create.requested).toMatchObject({
      type: "HTTPTarget",
      params: { http_request: "not an HTTP request" },
    });
    // Chromium blurs a newly disabled control a tick later.
    await page.waitForTimeout(250);
    expect(await activeElementTag(page)).not.toBe("BODY");
    await expect(createButton).toBeFocused();

    create.release();

    const alert = dialog.getByRole("alert");
    await expect(alert).toContainText(HTTP_ERROR);
    await expect(alert).toBeFocused();
    await expect(httpRequest).toHaveAttribute("aria-invalid", "true");

    // Escape only reaches the dialog surface while focus is still inside it.
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });

  test("keeps focus in the field after Enter submits, then focuses the announced error", async ({ page }) => {
    const WEBHOOK_ERROR = "webhook_url must be an absolute URL";
    const create = await holdCreateThenReject(page, WEBHOOK_ERROR);
    const dialog = await openCreateTargetDialog(page);

    await selectTargetType(page, dialog, "WebhookTarget");
    const webhookUrl = dialog.getByLabel(/Webhook Url/);
    await webhookUrl.fill("not-a-url");
    await webhookUrl.press("Enter");

    expect(await create.requested).toMatchObject({
      type: "WebhookTarget",
      params: { webhook_url: "not-a-url" },
    });
    await page.waitForTimeout(250);
    expect(await activeElementTag(page)).not.toBe("BODY");
    await expect(webhookUrl).toBeFocused();

    create.release();

    const alert = dialog.getByRole("alert");
    await expect(alert).toContainText(WEBHOOK_ERROR);
    await expect(alert).toBeFocused();
    await expect(webhookUrl).toHaveAttribute("aria-invalid", "true");

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });
});

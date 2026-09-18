import { expect, test, type Page } from "@playwright/test";

interface RegisteredConverter {
  converter_id: string;
  identifier: {
    class_name: string;
    class_module: string;
    hash: string;
    pyrit_version: string;
    supported_input_types: string[];
    supported_output_types: string[];
  };
  is_llm_based: boolean;
  description?: string;
}

const CONVERTER_TYPES = {
  items: [
    {
      converter_type: "CaesarConverter",
      supported_input_types: ["text"],
      supported_output_types: ["text"],
      parameters: [
        {
          name: "caesar_offset",
          type_name: "int",
          required: true,
          default: null,
          choices: null,
          description: "Offset for the cipher.",
        },
      ],
      is_llm_based: false,
      description: "Applies a Caesar cipher.",
    },
    {
      converter_type: "PersuasionConverter",
      supported_input_types: ["text"],
      supported_output_types: ["text"],
      parameters: [
        {
          name: "converter_target",
          type_name: "PromptTarget",
          required: true,
          default: null,
          choices: null,
          reference_type: "target",
          description: "The target used to rewrite prompts.",
        },
      ],
      is_llm_based: true,
      description: "Rewrites prompts.",
    },
    ...Array.from({ length: 16 }, (_, index) => ({
      converter_type: `ViewportConverter${index}`,
      supported_input_types: ["text"],
      supported_output_types: ["text"],
      parameters: [],
      is_llm_based: false,
      description: `Viewport test converter ${index}.`,
    })),
  ],
};

async function installRegistryMocks(page: Page): Promise<void> {
  let registeredConverters: RegisteredConverter[] = [];

  await page.route(/\/api\/auth\/config$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ auth_enabled: false }),
    });
  });
  await page.route(/\/api\/auth\/access$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ isAdmin: true }),
    });
  });
  await page.route(/\/api\/version$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ version: "0.0.0" }),
    });
  });
  await page.route(/\/api\/health$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "healthy" }),
    });
  });
  await page.route(/\/api\/targets(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [
          {
            target_registry_name: "rewrite-target",
            identifier: {
              class_name: "OpenAIChatTarget",
              class_module: "pyrit.prompt_target",
              hash: "target-hash",
              pyrit_version: "0.0.0",
            },
          },
        ],
        pagination: { limit: 200, has_more: false },
      }),
    });
  });
  await page.route(/\/api\/converters\/types$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CONVERTER_TYPES),
    });
  });
  await page.route(/\/api\/converters\/[^/]+$/, async (route) => {
    if (route.request().method() !== "DELETE") {
      await route.fallback();
      return;
    }
    const converterId = decodeURIComponent(route.request().url().split("/").pop() ?? "");
    registeredConverters = registeredConverters.filter(
      (converter) => converter.converter_id !== converterId,
    );
    await route.fulfill({ status: 204 });
  });
  await page.route(/\/api\/converters$/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: registeredConverters }),
      });
      return;
    }

    const body = JSON.parse(route.request().postData() ?? "{}");
    const converterType = CONVERTER_TYPES.items.find(
      (item) => item.converter_type === body.type,
    );
    const converter: RegisteredConverter = {
      converter_id: body.name,
      identifier: {
        class_name: body.type,
        class_module: `pyrit.converter.${body.type}`,
        hash: `${body.name}-hash`,
        pyrit_version: "0.0.0",
        supported_input_types: converterType?.supported_input_types ?? [],
        supported_output_types: converterType?.supported_output_types ?? [],
      },
      is_llm_based: converterType?.is_llm_based ?? false,
      description: converterType?.description,
    };
    registeredConverters.push(converter);
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify(converter),
    });
  });
}

async function submitDuplicateName(page: Page): Promise<() => void> {
  let releaseCreate: (() => void) | undefined;
  await page.route(/\/api\/converters$/, async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Converter instance 'caesar-custom' already exists",
      }),
    });
  });

  await page.getByRole("button", { name: "Create First Converter" }).click();
  await page.getByRole("combobox", { name: "Converter type" }).click();
  await page.getByTestId("converter-type-option-CaesarConverter").click();
  await page.getByLabel("Registry name").fill("caesar-custom");
  await page.getByLabel("caesar_offset *").fill("5");
  await page.getByRole("button", { name: "Add Converter" }).click();
  await expect(page.getByRole("button", { name: "Adding..." })).toBeVisible();

  return () => releaseCreate?.();
}

test.describe("Converter Registry", () => {
  test.beforeEach(async ({ page }) => {
    await installRegistryMocks(page);
    await page.goto("/registry/converters");
  });

  test("adds and removes a named converter without an active action", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Converter Registry" })).toBeVisible();
    await expect(page.getByRole("button", { name: /set active/i })).toHaveCount(0);

    await page.getByRole("button", { name: "New Converter" }).click();
    await page.getByRole("combobox", { name: "Converter type" }).click();
    await page.getByTestId("converter-type-option-CaesarConverter").click();
    await page.getByLabel("Registry name").fill("caesar-custom");
    await page.getByLabel("caesar_offset *").fill("5");
    await page.getByRole("button", { name: "Add Converter" }).click();

    await expect(page.getByText("caesar-custom")).toBeVisible();
    await page.getByRole("button", { name: "Remove caesar-custom" }).click();
    await page.getByRole("button", { name: "Remove", exact: true }).click();

    await expect(page.getByText("caesar-custom")).toHaveCount(0);
    await expect(page.getByText("No Converters Registered")).toBeVisible();
  });

  test("restores focus to the trigger when the add dialog is dismissed", async ({ page }) => {
    const trigger = page.getByRole("button", { name: "New Converter" });
    await trigger.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();

    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test("moves focus to New Converter once the first converter is created", async ({ page }) => {
    await page.getByRole("button", { name: "Create First Converter" }).click();
    await page.getByRole("combobox", { name: "Converter type" }).click();
    await page.getByTestId("converter-type-option-CaesarConverter").click();
    await page.getByLabel("Registry name").fill("caesar-focus");
    await page.getByLabel("caesar_offset *").fill("5");
    await page.getByRole("button", { name: "Add Converter" }).click();

    // The empty-state trigger unmounts with the list it belonged to, so focus
    // falls back to the always-mounted header action.
    await expect(page.getByText("caesar-focus")).toBeVisible();
    await expect(page.getByRole("button", { name: "New Converter" })).toBeFocused();
  });

  test("keeps the add dialog operable while the create request is in flight", async ({ page }) => {
    const release = await submitDuplicateName(page);

    // Chromium runs the unfocusing steps for the disabled primary action a tick
    // after it is disabled, so give it time to land before checking.
    await page.waitForTimeout(250);
    const dialog = page.getByRole("dialog");
    await expect(dialog.locator(":focus")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    release();
  });

  test("moves focus to the error when the registry rejects the name", async ({ page }) => {
    const release = await submitDuplicateName(page);
    release();

    const alert = page.getByRole("alert");
    await expect(alert).toContainText(/already exists/i);
    await expect(alert).toBeFocused();

    // Escape only reaches the dialog surface while focus is still inside it.
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
  });

  test("uses the available viewport height for the converter type list", async ({ page }) => {
    await page.getByRole("button", { name: "New Converter" }).click();
    await page.getByRole("combobox", { name: "Converter type" }).click();

    const listbox = page.getByRole("listbox");
    await expect(listbox).toBeVisible();
    const bounds = await listbox.boundingBox();
    expect(bounds?.height).toBeGreaterThan(300);
  });
});

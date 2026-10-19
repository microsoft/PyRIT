import { expect, test, type Page } from "./_fixtures";
import { compatibilityHeaders, mockVersion } from "./_compatibility";

test("creates and selects a runtime technique with the real backend @seeded", async ({ page, request }) => {
  test.setTimeout(120_000);
  const headers = compatibilityHeaders();
  for (const name of ["objective_scorer_chat", "adversarial_chat", "registry_local_objective"]) {
    const existing = await request.get(`/api/targets/${name}`, { headers });
    if (existing.status() === 404) {
      const created = await request.post("/api/targets", { headers, data: name === "registry_local_objective"
        ? { name, type: "TextTarget", params: {} }
        : { name, type: "OpenAIChatTarget", auth_mode: "api_key", params: {
          endpoint: "http://127.0.0.1:9/v1", model_name: "local-test", api_key: "local-test-only",
        } } });
      expect(created.ok(), await created.text()).toBe(true);
    } else {
      expect(existing.ok(), await existing.text()).toBe(true);
    }
  }
  const warm = await request.get("/api/scenarios/catalog/airt.rapid_response", { headers });
  expect(warm.ok(), await warm.text()).toBe(true);
  const before = await warm.json();
  const name = `browser_${Date.now()}`;
  expect(before.all_techniques).not.toContain(name);
  const converterName = `${name}_b64`;
  const converter = await request.post("/api/converters", {
    headers, data: { name: converterName, type: "Base64Converter", params: {} },
  });
  expect(converter.ok(), await converter.text()).toBe(true);
  let replacementGeneration: string | undefined;
  await page.route("**/api/runtime", async (route) => {
    const response = await route.fetch();
    const readiness = await response.json();
    await route.fulfill({ json: { ...readiness, generation: replacementGeneration ?? readiness.generation } });
  });

  const promptRequests: string[] = [];
  const detailRequests: string[] = [];
  page.on("request", (outgoing) => {
    if (outgoing.method() === "POST" && /\/api\/(?:message-sends|attacks\/[^/]+\/messages|scenarios\/runs)(?:\/|\?|$)/.test(outgoing.url())) {
      promptRequests.push(outgoing.url());
    }
    if (outgoing.method() === "GET" && new URL(outgoing.url()).pathname === `/api/techniques/${name}`) {
      detailRequests.push(outgoing.url());
    }
  });
  await page.goto("/registry/techniques");
  await expect(page.getByRole("tab", { name: "Techniques" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Named configurations of existing attack techniques", { exact: true })).toBeVisible();
  await expect(page.getByText(/Runtime only|No attack runs when you add a technique/)).toHaveCount(0);
  await expect(page.getByRole("table", { name: "Registered techniques" })).toBeVisible();
  const builtinCalls = {
    crescendo_simulated: "AttackTechniqueFactory.with_simulated_conversation(\n" +
      "    name='crescendo_simulated',\n" +
      "    description='Escalates gradually over a simulated conversation toward the objective.',\n" +
      "    technique_tags=['single_turn'],\n)",
    tap: "AttackTechniqueFactory(\n" +
      "    name='tap',\n" +
      "    attack_class=TreeOfAttacksWithPruningAttack,\n" +
      "    description='Explores a tree of adversarial prompts, pruning weak branches to refine the attack.',\n" +
      "    technique_tags=['multi_turn'],\n)",
  };
  for (const [builtinName, statement] of Object.entries(builtinCalls)) {
    await page.getByRole("button", { name: `Details for ${builtinName}` }).click();
    await expect(page.getByLabel("Technique creation call", { exact: true })).toHaveText(statement);
    await expect(page.getByRole("heading", { name: "Configuration" })).toHaveCount(0);
    await page.getByRole("button", { name: "Close", exact: true }).click();
  }
  await page.getByRole("button", { name: "New technique" }).click();
  await page.getByRole("textbox", { name: "Registry name" }).fill(name);
  await page.getByRole("textbox", { name: "Description" }).fill("Local browser technique");
  await page.getByRole("textbox", { name: "Tags" }).fill("browser_test");
  await page.getByRole("combobox", { name: "Attack type", exact: true }).selectOption("PromptSendingAttack");
  await expect(page.getByRole("dialog")).not.toContainText(/Implementation of|This form shows|Runtime only|prompt_normalizer/);
  await page.getByLabel("max_attempts_on_failure").fill("0");
  await page.getByRole("combobox", { name: "Request converters", exact: true }).selectOption(converterName);
  await page.getByRole("button", { name: "Add to Request converters", exact: true }).click();
  await page.getByRole("button", { name: "Add to Request converters", exact: true }).click();
  const create = page.waitForResponse((response) => response.url().endsWith("/api/techniques") && response.request().method() === "POST");
  await page.getByRole("button", { name: "Add technique" }).click();
  const created = await create;
  expect(created.status()).toBe(201);
  const createdTechnique = await created.json();
  const body = created.request().postDataJSON();
  expect(body).toMatchObject({
    name, type: "PromptSendingAttack", params: { max_attempts_on_failure: 0 },
    request_converters: [converterName, converterName], response_converters: [],
  });
  expect(body).not.toHaveProperty("factory_options");
  expect(body).not.toHaveProperty("seed_technique");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("textbox", { name: "Search techniques" }).fill(name);
  await expect(page.getByRole("cell", { name, exact: true })).toBeVisible();
  await page.getByRole("button", { name: `Details for ${name}` }).click();
  const creationCall = page.getByLabel("Technique creation call", { exact: true });
  await expect(creationCall).toHaveText(createdTechnique.creation_statement);
  await expect(creationCall).toContainText("AttackTechniqueFactory(");
  await expect(creationCall).toContainText("'max_attempts_on_failure': 0");
  expect(createdTechnique).not.toHaveProperty("evaluation_identifier");
  expect(createdTechnique).not.toHaveProperty("configuration");
  await expect(page.getByRole("heading", { name: "Configuration" })).toHaveCount(0);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("button", { name: `Details for ${name}` })).toBeFocused();
  expect(detailRequests).toEqual([]);

  let finishCreate: (() => void) | undefined;
  const pendingCreate = new Promise<void>((resolve) => { finishCreate = resolve; });
  let markCreateStarted: (() => void) | undefined;
  const createStarted = new Promise<void>((resolve) => { markCreateStarted = resolve; });
  const original = await request.get(`/api/techniques/${name}`, { headers });
  const originalTechnique = await original.json();
  await page.route("**/api/techniques", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    markCreateStarted?.();
    await pendingCreate;
    await route.fulfill({ status: 201, json: { ...originalTechnique, name: "old_runtime_response" } });
  });
  await page.getByRole("button", { name: "New technique" }).click();
  await page.getByRole("textbox", { name: "Registry name" }).fill("old_runtime_response");
  await page.getByRole("combobox", { name: "Attack type", exact: true }).selectOption("PromptSendingAttack");
  await page.getByRole("button", { name: "Add technique" }).click();
  await createStarted;
  replacementGeneration = "registry-replacement-test";
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(page.getByRole("button", { name: "New technique" })).toBeFocused();
  await expect(page.getByRole("table", { name: "Registered techniques" })).toBeVisible();
  const staleResponse = page.waitForResponse((response) => response.url().endsWith("/api/techniques") && response.request().method() === "POST");
  finishCreate?.();
  await staleResponse;
  await expect(page.getByRole("cell", { name: "old_runtime_response", exact: true })).toHaveCount(0);

  const current = await request.get("/api/scenarios/catalog/airt.rapid_response", { headers });
  expect(current.ok(), await current.text()).toBe(true);
  const after = await current.json();
  expect(after.all_techniques).toContain(name);
  expect(after.default_techniques).toEqual(before.default_techniques);
  await page.goto("/scanner/airt.rapid_response");
  const checkbox = page.getByRole("checkbox", { name, exact: true });
  await expect(checkbox).toBeVisible();
  await checkbox.check();
  await expect(checkbox).toBeChecked();
  expect(promptRequests).toEqual([]);
});

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
      body: JSON.stringify(mockVersion()),
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

async function createCaesarConverter(page: Page, name: string): Promise<void> {
  await page.getByRole("combobox", { name: "Converter type" }).click();
  await page.getByTestId("converter-type-option-CaesarConverter").click();
  await page.getByLabel("Registry name").fill(name);
  await page.getByLabel("caesar_offset *").fill("5");
  await page.getByRole("button", { name: "Add Converter" }).click();
}

async function submitSlowCreate(page: Page, name: string): Promise<() => void> {
  let releaseCreate: (() => void) | undefined;
  await page.route(/\/api\/converters$/, async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    await new Promise<void>((resolve) => {
      releaseCreate = resolve;
    });
    // Hand the request to the registry mock so the refresh the response
    // triggers has the new converter to show.
    await route.fallback();
  });

  await page.getByRole("button", { name: "New Converter" }).click();
  await createCaesarConverter(page, name);
  await expect(page.getByRole("button", { name: "Adding..." })).toBeVisible();
  // Chromium runs the unfocusing steps for the disabled primary action a tick
  // after it is disabled, so give it time to land before pressing Escape.
  await page.waitForTimeout(250);

  return () => releaseCreate?.();
}

test.describe("Converter Registry", () => {
  test.beforeEach(async ({ page }) => {
    await installRegistryMocks(page);
    await page.goto("/registry/converters");
  });

  test("adds and removes a named converter without an active action", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Converter Registry" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Target defaults" })).toHaveCount(0);

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

  test("leaves the removal dialog in place when a dismissed creation succeeds", async ({ page }) => {
    await page.getByRole("button", { name: "Create First Converter" }).click();
    await createCaesarConverter(page, "caesar-first");
    await expect(page.getByText("caesar-first")).toBeVisible();

    const release = await submitSlowCreate(page, "caesar-late");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();

    const removeTrigger = page.getByRole("button", { name: "Remove caesar-first" });
    await removeTrigger.click();
    const removeDialog = page.getByRole("dialog");
    await expect(removeDialog).toBeVisible();

    release();
    await expect(page.getByText("caesar-late")).toBeVisible();

    // Moving focus out makes Tabster mark the still-visible dialog aria-hidden,
    // and Escape then stops dismissing it, so the late response has to leave
    // both the removal dialog's state and its focus alone.
    await expect(removeDialog.locator(":focus")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(removeDialog).toBeHidden();
    // The row that opened the dialog was replaced by the refresh, so focus
    // falls back to the always-mounted header action.
    await expect(page.getByRole("button", { name: "New Converter" })).toBeFocused();
  });

  test("keeps a stale submission failure out of a reopened add dialog", async ({ page }) => {
    const release = await submitDuplicateName(page);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();

    await page.getByRole("button", { name: "New Converter" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("combobox", { name: "Converter type" })).toBeVisible();

    release();

    // The request settles either way; only its failure is dropped, so the
    // reopened dialog keeps the empty form the user opened.
    await expect(dialog.getByRole("button", { name: "Add Converter" })).toBeEnabled();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(dialog.getByLabel("Registry name")).toHaveValue("");
  });

  test("uses the available viewport height for the converter type list", async ({ page }) => {
    await page.getByRole("button", { name: "New Converter" }).click();
    await page.getByRole("combobox", { name: "Converter type" }).click();

    const listbox = page.getByRole("listbox");
    await expect(listbox).toBeVisible();
    const bounds = await listbox.boundingBox();
    expect(bounds?.height).toBeGreaterThan(300);
  });

  for (const { name, viewport, deviceScaleFactor } of [
    { name: "wide short screen", viewport: { width: 1920, height: 600 }, deviceScaleFactor: 1 },
    { name: "scaled laptop", viewport: { width: 1280, height: 640 }, deviceScaleFactor: 1.5 },
    { name: "narrow screen", viewport: { width: 390, height: 640 }, deviceScaleFactor: 2 },
  ]) {
    test.describe(name, () => {
      test.use({ viewport, deviceScaleFactor });

      test("keeps a long converter list vertical, stable, and scrollable", async ({ page }) => {
        await page.getByRole("button", { name: "New Converter" }).click();
        const trigger = page.getByRole("combobox", { name: "Converter type" });
        await trigger.click();

        const listbox = page.getByRole("listbox");
        await expect(listbox).toBeVisible();
        const anchor = await trigger.evaluate((button) => {
          const { x, y, width, height } = button.parentElement!.getBoundingClientRect();
          return { x, y, width, height };
        });
        await expect.poll(async () => {
          const box = (await listbox.boundingBox())!;
          return Math.abs(box.x - anchor.x);
        }).toBeLessThanOrEqual(1);

        // Sample successive frames: a single bounding box can miss position oscillation.
        const samples = await listbox.evaluate(async (element) => {
          const bounds = [];
          for (let frame = 0; frame < 60; frame++) {
            await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
            const { x, y, width, height } = element.getBoundingClientRect();
            bounds.push({ x, y, width, height });
          }
          return bounds;
        });

        for (const box of samples) {
          expect(Math.abs(box.x - anchor.x)).toBeLessThanOrEqual(1);
          expect(Math.abs(box.width - anchor.width)).toBeLessThanOrEqual(1);
          expect(box.height).toBeGreaterThan(0);
          expect(box.x).toBeGreaterThanOrEqual(-1);
          expect(box.y).toBeGreaterThanOrEqual(-1);
          expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
          expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
          expect(
            box.y >= anchor.y + anchor.height - 1
              || box.y + box.height <= anchor.y + 1,
          ).toBe(true);
          expect(Math.abs(box.y - samples[0].y)).toBeLessThanOrEqual(1);
          expect(Math.abs(box.height - samples[0].height)).toBeLessThanOrEqual(1);
        }

        const scroll = await listbox.evaluate((element) => ({
          height: element.clientHeight,
          contentHeight: element.scrollHeight,
        }));
        expect(scroll.contentHeight).toBeGreaterThan(scroll.height);
        const lastOption = listbox.getByRole("option").last();
        await lastOption.scrollIntoViewIfNeeded();
        await expect(lastOption).toBeInViewport();
        await lastOption.click();
        await expect(listbox).toBeHidden();
        await expect(trigger).toHaveText("ViewportConverter9");
      });
    });
  }
});

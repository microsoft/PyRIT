import { expect, test, type Page } from "./_fixtures";
import { mockVersion } from "./_compatibility";
import { READY_RUNTIME } from "./_runtime";

const LONG_HARM_CATEGORY = "cybersecurity_credentials_and_privileged_information_disclosure";

const DATASET = {
  name: "harmbench",
  selection_key: "dataset:named:harmbench",
  loaded: true,
  provider_available: true,
  logical_examples: 12,
  seed_pieces: 12,
  objectives: 1,
  modalities: ["text"],
  harm_categories: [LONG_HARM_CATEGORY],
  has_unlabeled_harm_categories: false,
};

const NEIGHBOR_DATASET = {
  ...DATASET,
  name: "neighbor",
  selection_key: "dataset:named:neighbor",
};

async function mockDatasetCatalog(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const apiPath = new URL(route.request().url()).pathname.replace(/^\/api/, "");
    const body = (payload: unknown) => ({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });

    if (apiPath === "/datasets") {
      await route.fulfill(body({ items: [DATASET, NEIGHBOR_DATASET] }));
      return;
    }
    if (apiPath === "/runtime") {
      await route.fulfill(body(READY_RUNTIME));
      return;
    }
    if (apiPath === "/version") {
      await route.fulfill(body(mockVersion()));
      return;
    }
    if (apiPath === "/auth/config") {
      await route.fulfill(body({ clientId: "", tenantId: "", allowedGroupIds: "" }));
      return;
    }
    if (apiPath === "/auth/access") {
      await route.fulfill(body({ isAdmin: false }));
      return;
    }
    if (apiPath === "/health") {
      await route.fulfill(body({ status: "healthy" }));
      return;
    }
    if (apiPath === "/targets" || apiPath === "/converters" || apiPath === "/converters/types") {
      await route.fulfill(body({ items: [] }));
      return;
    }
    if (apiPath === "/targets/types") {
      await route.fulfill(body({ items: [] }));
      return;
    }
    if (apiPath === "/labels") {
      await route.fulfill(body({ source: "attacks", labels: {} }));
      return;
    }

    throw new Error(`Unhandled dataset catalog request: ${route.request().method()} ${apiPath}`);
  });
}

async function expectLongLabelStaysInsideCard(page: Page): Promise<void> {
  const card = page.getByRole("article", { name: "harmbench" });
  await expect(card).toBeVisible();
  await expect(card.getByText(LONG_HARM_CATEGORY)).toBeVisible();

  const metrics = await card.evaluate((article, label) => {
    const badge = Array.from(article.querySelectorAll("*")).find((element) => element.textContent === label);
    const textHost = badge ?? article;
    const range = document.createRange();
    range.selectNodeContents(textHost);
    const text = range.getBoundingClientRect();
    const cardBox = article.getBoundingClientRect();
    const badgeBox = textHost.getBoundingClientRect();
    return {
      cardWidth: cardBox.width,
      cardRight: cardBox.right,
      cardLeft: cardBox.left,
      cardTop: cardBox.top,
      cardBottom: cardBox.bottom,
      textLeft: text.left,
      textRight: text.right,
      textTop: text.top,
      textBottom: text.bottom,
      badgeHeight: badgeBox.height,
      scrollWidth: textHost.scrollWidth,
      clientWidth: textHost.clientWidth,
    };
  }, LONG_HARM_CATEGORY);

  expect(metrics.cardWidth).toBeLessThanOrEqual(360);
  expect(metrics.textLeft).toBeGreaterThanOrEqual(metrics.cardLeft - 1);
  expect(metrics.textRight).toBeLessThanOrEqual(metrics.cardRight + 1);
  expect(metrics.textTop).toBeGreaterThanOrEqual(metrics.cardTop - 1);
  expect(metrics.textBottom).toBeLessThanOrEqual(metrics.cardBottom + 1);
  expect(metrics.badgeHeight).toBeGreaterThan(20);
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

test.describe("dataset catalog layout", () => {
  test.beforeEach(async ({ page }) => {
    await mockDatasetCatalog(page);
  });

  test("wraps a long harm category inside a multi-column desktop catalog", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1050 });
    await page.goto("/datasets");
    await expectLongLabelStaysInsideCard(page);

    const cards = page.getByRole("article");
    await expect(cards).toHaveCount(2);
    const [first, second] = await cards.evaluateAll((articles: Element[]) =>
      articles.map((article: Element) => {
        const { left, right, top } = article.getBoundingClientRect();
        return { left, right, top };
      }),
    );
    expect(first.top).toBe(second.top);
    expect(second.left).toBeGreaterThan(first.right);
  });

  test("wraps a long harm category inside the card on a 390px mobile viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/datasets");
    await expectLongLabelStaysInsideCard(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});

const FIRST_EXAMPLE = {
  example_id: "example-1",
  name: "alpha",
  preview: "short preview",
  preview_truncated: false,
  modalities: ["text", "image_path"],
  seed_types: ["prompt", "objective"],
  piece_count: 2,
  objective_count: 1,
  harm_categories: [LONG_HARM_CATEGORY],
  has_unlabeled_harm: true,
};

const SECOND_EXAMPLE = {
  example_id: "example-2",
  name: "beta",
  preview: "later preview",
  preview_truncated: false,
  modalities: ["text"],
  seed_types: ["prompt"],
  piece_count: 1,
  objective_count: 0,
  harm_categories: ["hate"],
  has_unlabeled_harm: false,
};

const THIRD_EXAMPLE = {
  example_id: "example-3",
  name: "gamma",
  preview: "third preview",
  preview_truncated: false,
  modalities: ["text"],
  seed_types: ["prompt"],
  piece_count: 1,
  objective_count: 0,
  harm_categories: ["hate"],
  has_unlabeled_harm: false,
};

const EXAMPLE_DETAIL = {
  ...FIRST_EXAMPLE,
  members: [
    {
      id: "f347b3d7-b99a-444d-8277-511e96d68f04",
      seed_type: "prompt",
      value: "prompt body kept",
      value_sha256: "9edfa5336786e1dad5ba3585dcf1d53e58cab5b56d5aa1527464f19f7507d923",
      data_type: "text",
      name: "alpha prompt",
      dataset_name: "harmbench",
      origin: "local",
      harm_categories: [LONG_HARM_CATEGORY],
      description: null,
      authors: null,
      groups: null,
      source: "https://example.org/dataset-provenance",
      date_added: "2026-01-01T00:00:00Z",
      added_by: "tester",
      metadata: null,
      prompt_group_id: "group-1",
      sequence: 0,
      role: "user",
      parameters: null,
      conditions: null,
      response_json_schema: null,
    },
    {
      id: "image-1",
      seed_type: "prompt",
      value: "photo.png",
      value_sha256: "def",
      data_type: "image_path",
      name: null,
      dataset_name: "harmbench",
      origin: "local",
      harm_categories: [],
      description: null,
      authors: null,
      groups: null,
      source: null,
      date_added: "2026-01-01T00:00:00Z",
      added_by: "tester",
      metadata: null,
      prompt_group_id: "group-1",
      sequence: 1,
      role: "user",
      parameters: null,
      conditions: null,
      response_json_schema: null,
    },
  ],
};

function seedPage(items: unknown[], nextCursor: string | null, requestCursor: string | null) {
  return {
    items,
    pagination: {
      limit: 20,
      has_more: nextCursor != null,
      next_cursor: nextCursor,
      prev_cursor: requestCursor,
    },
    total: 3,
  };
}

async function mockDatasetExamples(page: Page): Promise<string[]> {
  const seedRequests: string[] = [];
  await page.route("**/api/**", async (route) => {
    const requestUrl = new URL(route.request().url());
    const apiPath = requestUrl.pathname.replace(/^\/api/, "");
    const body = (payload: unknown, status = 200) => ({
      status,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });

    if (apiPath === "/datasets") {
      await route.fulfill(body({ items: [DATASET, NEIGHBOR_DATASET] }));
      return;
    }
    if (apiPath === "/datasets/seeds") {
      const selection = requestUrl.searchParams.getAll("selection_key");
      if (selection.length !== 1 || selection[0] === "") {
        throw new Error("Seed list was requested without one selection key");
      }
      for (const forbidden of ["q", "sort", "unlabeled", "loaded_only"]) {
        if (requestUrl.searchParams.has(forbidden)) {
          throw new Error(`Seed list included catalog parameter ${forbidden}`);
        }
      }
      seedRequests.push(`${requestUrl.pathname}${requestUrl.search}`);
      const cursors = requestUrl.searchParams.getAll("cursor");
      if (cursors.length > 1) {
        throw new Error("Seed list was requested with a repeated cursor");
      }
      const cursor = cursors[0] ?? null;
      if (cursor === "cursor-2") {
        await route.fulfill(body(seedPage([THIRD_EXAMPLE], null, cursor)));
        return;
      }
      await route.fulfill(body(seedPage([FIRST_EXAMPLE, SECOND_EXAMPLE], "cursor-2", cursor)));
      return;
    }
    if (apiPath.startsWith("/datasets/seeds/")) {
      const selection = requestUrl.searchParams.getAll("selection_key");
      if (selection.length !== 1 || selection[0] !== DATASET.selection_key) {
        throw new Error("Seed detail was requested without this dataset's selection key");
      }
      seedRequests.push(`${requestUrl.pathname}${requestUrl.search}`);
      await route.fulfill(body(EXAMPLE_DETAIL));
      return;
    }
    if (apiPath === "/runtime") {
      await route.fulfill(body(READY_RUNTIME));
      return;
    }
    if (apiPath === "/version") {
      await route.fulfill(body(mockVersion()));
      return;
    }
    if (apiPath === "/auth/config") {
      await route.fulfill(body({ clientId: "", tenantId: "", allowedGroupIds: "" }));
      return;
    }
    if (apiPath === "/auth/access") {
      await route.fulfill(body({ isAdmin: false }));
      return;
    }
    if (apiPath === "/health") {
      await route.fulfill(body({ status: "healthy" }));
      return;
    }
    if (apiPath === "/targets" || apiPath === "/converters" || apiPath === "/converters/types") {
      await route.fulfill(body({ items: [] }));
      return;
    }
    if (apiPath === "/targets/types") {
      await route.fulfill(body({ items: [] }));
      return;
    }
    if (apiPath === "/labels") {
      await route.fulfill(body({ source: "attacks", labels: {} }));
      return;
    }

    throw new Error(`Unhandled dataset example request: ${route.request().method()} ${apiPath}`);
  });
  return seedRequests;
}

async function openHarmbench(page: Page): Promise<void> {
  const category = encodeURIComponent(LONG_HARM_CATEGORY);
  await page.goto(`/datasets?q=harm&modality=text&harm_category=${category}&sort=name`);
  await page.getByRole("link", { name: "harmbench" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "harmbench" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Examples" })).toBeVisible();
}

test.describe("dataset example table", () => {
  test("lists grouped examples, pages, and expands one row", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1050 });
    const seedRequests = await mockDatasetExamples(page);
    await openHarmbench(page);

    const table = page.getByRole("table", { name: "Examples" });
    const rows = table.getByRole("row");
    await expect(rows).toHaveCount(3);
    const alpha = rows.filter({ hasText: "alpha" });
    await expect(alpha.getByText("text")).toBeVisible();
    await expect(alpha.getByText("image_path")).toBeVisible();
    await expect(alpha.getByText("prompt")).toBeVisible();
    await expect(alpha.getByText("objective")).toBeVisible();
    await expect(page.getByRole("status")).toHaveText("3 matching examples, page 1");
    const nextBox = await page.getByRole("button", { name: "Next page" }).boundingBox();
    const showBox = await page.getByRole("button", { name: "Show alpha" }).boundingBox();
    expect(nextBox?.height).toBeLessThan(44);
    expect(showBox?.height).toBeLessThan(44);
    expect(seedRequests.some((request) => request.includes("selection_key="))).toBe(true);
    expect(seedRequests.some((request) => /[?&](?:q|sort|unlabeled)=/.test(request))).toBe(false);

    await page.getByRole("button", { name: "Next page" }).click();
    await expect(page.getByText("third preview")).toBeVisible();
    await expect(page).toHaveURL(/cursor=cursor-2/);
    await expect(page.getByRole("status")).toHaveText("3 matching examples, page 2");
    expect(seedRequests.some((request) => request.includes("cursor=cursor-2"))).toBe(true);
    expect(seedRequests.some((request) => request.includes("prev_cursor="))).toBe(false);

    await page.getByRole("button", { name: "Previous page" }).click();
    await expect(page.getByText("short preview")).toBeVisible();
    await expect(page).not.toHaveURL(/cursor=/);

    await page.getByRole("button", { name: "Show alpha" }).click();
    const detail = page.getByRole("region", { name: "Example detail" });
    await expect(detail.getByText("prompt body kept")).toBeVisible();
    await expect(detail.getByText("photo.png")).toBeVisible();
    await expect(detail.getByText("Media is not played in this view.")).toBeVisible();
    await expect(detail.locator("img, audio, video")).toHaveCount(0);
    await expect(page).toHaveURL(/example=example-1/);
    expect(seedRequests.some((request) => {
      const params = new URL(request, "http://127.0.0.1").searchParams;
      return request.includes("/datasets/seeds/example-1")
        && params.get("selection_key") === DATASET.selection_key;
    })).toBe(true);

    await page.getByRole("link", { name: "Back to datasets" }).click();
    await expect(page).toHaveURL(/\/datasets\?/);
    await expect(page).toHaveURL(/q=harm(?:&|$)/);
    await expect(page).toHaveURL(/modality=text/);
    await expect(page).toHaveURL(new RegExp(`harm_category=${encodeURIComponent(LONG_HARM_CATEGORY)}`));
    await expect(page).toHaveURL(/sort=name/);
    await expect(page.getByRole("table", { name: "Examples" })).toHaveCount(0);

    await page.goBack();
    await expect(page.getByRole("region", { name: "Example detail" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("region", { name: "Example detail" })).toBeVisible();
    await expect(page).toHaveURL(/example=example-1/);
    await expect(page).toHaveURL(new RegExp(`selection_key=${encodeURIComponent(DATASET.selection_key).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
  });

  test("shows a way back when the example id does not match a row", async ({ page }) => {
    await mockDatasetExamples(page);
    const exampleId = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE";
    await page.goto(
      `/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=${exampleId}`,
    );
    await expect(page.getByRole("table", { name: "Examples" })).toBeVisible();
    await expect(page.getByText("This example is not in the dataset.")).toBeVisible();
    await expect(page.getByRole("region", { name: "Example detail" })).toHaveCount(0);
    await page.getByRole("button", { name: "Back to the examples" }).click();
    await expect(page).not.toHaveURL(/[?&]example=/);
    await expect(page.getByRole("table", { name: "Examples" })).toBeVisible();
  });

  test("does not request seeds when the selection key is missing", async ({ page }) => {
    const seedRequests = await mockDatasetExamples(page);
    await page.goto("/datasets/detail");
    await expect(page.getByRole("heading", { name: "Dataset not found" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Examples" })).toHaveCount(0);
    expect(seedRequests).toEqual([]);
  });

  test("wraps a long harm category inside the example table at 390px", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockDatasetExamples(page);
    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}`);
    const table = page.getByRole("table", { name: "Examples" });
    await expect(table).toBeVisible();
    const cell = table.getByRole("cell").filter({ hasText: LONG_HARM_CATEGORY }).first();
    await expect(cell).toBeVisible();

    const metrics = await cell.evaluate((host, label) => {
      const badge = Array.from(host.querySelectorAll("*")).find((element) => element.textContent === label);
      const textHost = badge ?? host;
      const range = document.createRange();
      range.selectNodeContents(textHost);
      const text = range.getBoundingClientRect();
      const cellBox = host.getBoundingClientRect();
      const badgeBox = textHost.getBoundingClientRect();
      return {
        textLeft: text.left,
        textRight: text.right,
        textTop: text.top,
        textBottom: text.bottom,
        cellLeft: cellBox.left,
        cellRight: cellBox.right,
        cellTop: cellBox.top,
        cellBottom: cellBox.bottom,
        badgeHeight: badgeBox.height,
        scrollWidth: textHost.scrollWidth,
        clientWidth: textHost.clientWidth,
      };
    }, LONG_HARM_CATEGORY);

    expect(metrics.textLeft).toBeGreaterThanOrEqual(metrics.cellLeft - 1);
    expect(metrics.textRight).toBeLessThanOrEqual(metrics.cellRight + 1);
    expect(metrics.textTop).toBeGreaterThanOrEqual(metrics.cellTop - 1);
    expect(metrics.textBottom).toBeLessThanOrEqual(metrics.cellBottom + 1);
    expect(metrics.badgeHeight).toBeGreaterThan(20);
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
    const tableLayout = await table.evaluate((host) => ({
      columnWidth: host.querySelector("th")?.getBoundingClientRect().width ?? 0,
      wrapperWidth: host.parentElement?.clientWidth ?? 0,
      scrollWidth: host.parentElement?.scrollWidth ?? 0,
    }));
    expect(tableLayout.columnWidth).toBeGreaterThanOrEqual(140);
    expect(tableLayout.scrollWidth).toBeGreaterThan(tableLayout.wrapperWidth);
    await table.getByRole("button", { name: "Show alpha" }).click();
    await expect(page.getByRole("region", { name: "Example detail" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });

  test("stacks detail labels above readable stored values at 390px", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockDatasetExamples(page);
    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`);
    const member = page.getByRole("region", { name: "Example detail" }).getByRole("article", { name: "Member 1" });
    await expect(member).toBeVisible();
    for (const label of ["ID", "Value hash", "Seed type", "Source"]) {
      const dimensions = await member.getByRole("term").filter({ hasText: new RegExp(`^${label}$`) }).evaluate((term) => {
        const value = term.nextElementSibling;
        if (!value) throw new Error("Stored field has no value");
        const termBox = term.getBoundingClientRect();
        const valueBox = value.getBoundingClientRect();
        return {
          termLeft: termBox.left,
          termBottom: termBox.bottom,
          valueLeft: valueBox.left,
          valueTop: valueBox.top,
          valueWidth: valueBox.width,
          clientWidth: value.clientWidth,
          scrollWidth: value.scrollWidth,
        };
      });
      expect(dimensions.valueTop).toBeGreaterThanOrEqual(dimensions.termBottom);
      expect(dimensions.valueLeft).toBeCloseTo(dimensions.termLeft, 0);
      expect(dimensions.valueWidth).toBeGreaterThanOrEqual(250);
      expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});

test.describe("dataset example touch targets", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

  test("keeps example controls at least 44px", async ({ page }) => {
    await mockDatasetExamples(page);
    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}`);
    await expect(page.getByRole("table", { name: "Examples" })).toBeVisible();

    const controls = [
      page.getByRole("textbox", { name: "Search examples" }),
      page.getByRole("combobox", { name: "Example modalities" }),
      page.getByRole("combobox", { name: "Example seed types" }),
      page.getByRole("combobox", { name: "Example harm categories" }),
      page.getByRole("combobox", { name: "Examples per page" }),
      page.getByRole("button", { name: "Next page" }),
      page.getByRole("button", { name: "Show alpha" }),
    ];
    for (const control of controls) {
      await expect(control).toBeVisible();
      const box = await control.boundingBox();
      if (!box) {
        throw new Error("Expected a visible example touch target");
      }
      expect(Math.round(box.width)).toBeGreaterThanOrEqual(44);
      expect(Math.round(box.height)).toBeGreaterThanOrEqual(44);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});

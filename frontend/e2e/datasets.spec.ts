import { readFileSync } from "node:fs";

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
    if (apiPath === "/scorers") {
      await route.fulfill(body({ items: [], pagination: { has_more: false } }));
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
    return {
      cardRight: cardBox.right,
      cardLeft: cardBox.left,
      cardTop: cardBox.top,
      cardBottom: cardBox.bottom,
      textLeft: text.left,
      textRight: text.right,
      textTop: text.top,
      textBottom: text.bottom,
      scrollWidth: textHost.scrollWidth,
      clientWidth: textHost.clientWidth,
    };
  }, LONG_HARM_CATEGORY);

  expect(metrics.textLeft).toBeGreaterThanOrEqual(metrics.cardLeft - 1);
  expect(metrics.textRight).toBeLessThanOrEqual(metrics.cardRight + 1);
  expect(metrics.textTop).toBeGreaterThanOrEqual(metrics.cardTop - 1);
  expect(metrics.textBottom).toBeLessThanOrEqual(metrics.cardBottom + 1);
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
        const { left, right } = article.getBoundingClientRect();
        return { left, right };
      }),
    );
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

async function mockDatasetExamples(
  page: Page,
  detail: typeof EXAMPLE_DETAIL = EXAMPLE_DETAIL,
): Promise<string[]> {
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
      await route.fulfill(body(detail));
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

    if (apiPath === "/scorers") {
      await route.fulfill(body({ items: [], pagination: { has_more: false } }));
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

    await expect(table.locator("img, audio, video")).toHaveCount(0);
    await page.getByRole("button", { name: "Show alpha" }).click();
    const detail = page.getByRole("region", { name: "Example detail" });
    await expect(detail.getByText("prompt body kept")).toBeVisible();
    await expect(detail.getByText("photo.png")).toBeVisible();
    await expect(detail.getByText("File is missing.")).toBeVisible();
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
      return {
        textLeft: text.left,
        textRight: text.right,
        textTop: text.top,
        textBottom: text.bottom,
        cellLeft: cellBox.left,
        cellRight: cellBox.right,
        cellTop: cellBox.top,
        cellBottom: cellBox.bottom,
        scrollWidth: textHost.scrollWidth,
        clientWidth: textHost.clientWidth,
      };
    }, LONG_HARM_CATEGORY);

    expect(metrics.textLeft).toBeGreaterThanOrEqual(metrics.cellLeft - 1);
    expect(metrics.textRight).toBeLessThanOrEqual(metrics.cellRight + 1);
    expect(metrics.textTop).toBeGreaterThanOrEqual(metrics.cellTop - 1);
    expect(metrics.textBottom).toBeLessThanOrEqual(metrics.cellBottom + 1);
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
    const tableLayout = await table.evaluate((host) => ({
      wrapperWidth: host.parentElement?.clientWidth ?? 0,
      scrollWidth: host.parentElement?.scrollWidth ?? 0,
    }));
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
          termBottom: termBox.bottom,
          valueTop: valueBox.top,
          clientWidth: value.clientWidth,
          scrollWidth: value.scrollWidth,
        };
      });
      expect(dimensions.valueTop).toBeGreaterThanOrEqual(dimensions.termBottom);
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

const MEDIA_URLS = {
  image: "/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fpic.png",
  audio: "/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Ftalk.wav",
  video: "/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fclip.mp4",
  broken: "/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fbroken.mp4",
  svg: "/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fmark.svg",
};

function mediaMember(id: string, dataType: string, value: string, valueUrl: string) {
  return {
    id,
    seed_type: "prompt",
    value,
    value_url: valueUrl,
    value_sha256: "def",
    data_type: dataType,
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
  };
}

test.describe("dataset media previews", () => {
  test("loads image, audio, and video only from the open example and never autoplays", async ({ page }) => {
    const mediaRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/media") mediaRequests.push(request.url());
    });
    await mockDatasetExamples(page, {
      ...EXAMPLE_DETAIL,
      modalities: ["text", "image_path", "audio_path", "video_path"],
      members: [
        EXAMPLE_DETAIL.members[0],
        mediaMember("image-1", "image_path", "/dbdata/seed-prompt-entries/pic.png", MEDIA_URLS.image),
        mediaMember("audio-1", "audio_path", "/dbdata/seed-prompt-entries/talk.wav", MEDIA_URLS.audio),
        mediaMember("video-1", "video_path", "/dbdata/seed-prompt-entries/clip.mp4", MEDIA_URLS.video),
      ],
    });
    await page.route("**/api/media**", async (route) => {
      const path = new URL(route.request().url()).searchParams.get("path") ?? "";
      if (path.endsWith(".png")) {
        await route.fulfill({
          status: 200,
          contentType: "image/png",
          body: Buffer.from(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            "base64",
          ),
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/octet-stream", body: "media" });
    });

    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`);
    const table = page.getByRole("table", { name: "Examples" });
    const detail = page.getByRole("region", { name: "Example detail" });
    await expect(detail.getByText("prompt body kept")).toBeVisible();
    await expect(detail.getByText("/dbdata/seed-prompt-entries/pic.png")).toBeVisible();
    await expect(table.locator("img, audio, video")).toHaveCount(0);
    await expect(detail.locator("img, audio, video")).toHaveCount(0);
    expect(mediaRequests).toEqual([]);

    await detail.getByRole("button", { name: "Show image preview" }).click();
    const image = detail.getByRole("img", { name: "image seed" });
    await expect(image).toHaveAttribute("src", MEDIA_URLS.image);
    await expect(image).toHaveAttribute("loading", "lazy");
    await expect.poll(() => mediaRequests.length).toBe(1);

    await detail.getByRole("button", { name: "Show audio preview" }).click();
    const audio = detail.locator("audio");
    await expect(audio).toHaveAttribute("src", MEDIA_URLS.audio);
    await expect(audio).toHaveAttribute("preload", "none");
    await expect(audio).toHaveJSProperty("autoplay", false);
    await expect(audio).toHaveJSProperty("paused", true);

    await detail.getByRole("button", { name: "Show video preview" }).click();
    const video = detail.locator("video");
    await expect(video).toHaveAttribute("src", MEDIA_URLS.video);
    await expect(video).toHaveAttribute("preload", "none");
    await expect(video).toHaveJSProperty("autoplay", false);
    await expect(video).toHaveJSProperty("paused", true);
    expect(mediaRequests.filter((url) => url.includes("talk.wav") || url.includes("clip.mp4"))).toEqual([]);

    await detail.getByRole("button", { name: "Hide video preview" }).click();
    await expect(detail.locator("video")).toHaveCount(0);
    await expect(table).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(3);
  });

  test("shows broken and unsupported media without breaking the example table", async ({ page }) => {
    await mockDatasetExamples(page, {
      ...EXAMPLE_DETAIL,
      members: [
        EXAMPLE_DETAIL.members[0],
        mediaMember("video-1", "video_path", "/dbdata/seed-prompt-entries/broken.mp4", MEDIA_URLS.broken),
        mediaMember("mark-1", "image_path", "/dbdata/seed-prompt-entries/mark.svg", MEDIA_URLS.svg),
      ],
    });
    await page.route("**/api/media**", async (route) => {
      await route.fulfill({ status: 404, contentType: "text/plain", body: "missing" });
    });

    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`);
    const table = page.getByRole("table", { name: "Examples" });
    const detail = page.getByRole("region", { name: "Example detail" });
    await expect(table).toBeVisible();
    await expect(detail.getByText("This format cannot be previewed.")).toBeVisible();
    await expect(detail.getByRole("link", { name: "Download" })).toHaveAttribute("href", MEDIA_URLS.svg);
    await expect(detail.locator("img, audio, video")).toHaveCount(0);

    await detail.getByRole("button", { name: "Show video preview" }).click();
    const video = detail.locator("video");
    await expect(video).toHaveJSProperty("paused", true);
    await video.evaluate((element: HTMLVideoElement) => element.play().catch(() => undefined));
    await expect(detail.getByText(/Video failed to load/)).toBeVisible();
    await expect(detail.getByRole("button", { name: "Retry video" })).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(3);
    await expect(detail.getByText("prompt body kept")).toBeVisible();
  });

  test("opens a cross-origin download in a new tab and stays on the example", async ({ page }) => {
    const signed = "https://account.blob.core.windows.net/container/clip.mp4?sig=expired";
    await mockDatasetExamples(page, {
      ...EXAMPLE_DETAIL,
      members: [
        EXAMPLE_DETAIL.members[0],
        mediaMember("video-1", "video_path", "https://account.blob.core.windows.net/container/clip.mp4", signed),
      ],
    });
    await page.context().route("https://account.blob.core.windows.net/**", async (route) => {
      await route.fulfill({ status: 403, contentType: "text/plain", body: "expired" });
    });

    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`);
    const detail = page.getByRole("region", { name: "Example detail" });
    const stayed = page.url();
    await detail.getByRole("button", { name: "Show video preview" }).click();
    const video = detail.locator("video");
    await video.evaluate((element: HTMLVideoElement) => element.play().catch(() => undefined));
    await expect(detail.getByText(/Video failed to load/)).toBeVisible();

    const popupPromise = page.waitForEvent("popup");
    await detail.getByRole("link", { name: "Open in new tab" }).click();
    const popup = await popupPromise;
    await expect.poll(() => popup.url()).toContain("blob.core.windows.net");
    await expect(page).toHaveURL(stayed);
    await expect(detail.getByText("prompt body kept")).toBeVisible();
    await expect(page.getByRole("table", { name: "Examples" })).toBeVisible();
    await popup.close();
  });

  test("retries an expired signature with a newly signed url", async ({ page }) => {
    const stored = "https://account.blob.core.windows.net/container/pic.png";
    const expired = `${stored}?sig=expired`;
    const fresh = `${stored}?sig=fresh`;
    let signature: string = expired;
    await mockDatasetExamples(page, {
      ...EXAMPLE_DETAIL,
      members: [
        EXAMPLE_DETAIL.members[0],
        mediaMember("image-1", "image_path", stored, expired),
      ],
    });
    await page.route("**/api/datasets/seeds/example-1**", async (route) => {
      const detail = {
        ...EXAMPLE_DETAIL,
        members: [
          EXAMPLE_DETAIL.members[0],
          mediaMember("image-1", "image_path", stored, signature),
        ],
      };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(detail) });
    });
    await page.context().route("https://account.blob.core.windows.net/**", async (route) => {
      const expiredRequest = route.request().url().includes("sig=expired");
      await route.fulfill(expiredRequest
        ? { status: 403, contentType: "text/plain", body: "expired" }
        : {
          status: 200,
          contentType: "image/png",
          body: Buffer.from(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            "base64",
          ),
        });
    });

    const mediaRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).hostname === "account.blob.core.windows.net") mediaRequests.push(request.url());
    });
    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`);
    const detail = page.getByRole("region", { name: "Example detail" });
    await expect(detail.getByTestId("seed-value-image-1")).toHaveText(stored);
    await detail.getByRole("button", { name: "Show image preview" }).click();
    await expect(detail.getByText(/Image failed to load/)).toBeVisible();
    expect(mediaRequests).toEqual([expired]);

    signature = fresh;
    await detail.getByRole("button", { name: "Retry image" }).click();
    const image = detail.locator("img");
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    await expect(image).toHaveAttribute("src", fresh);
    expect(mediaRequests).toContain(fresh);
    await expect(detail.getByText(/Image failed to load/)).toHaveCount(0);
    await expect(detail.getByTestId("seed-value-image-1")).toHaveText(stored);
    await expect(page.getByRole("region", { name: "Example detail" })).toBeVisible();
  });

  test("keeps media hidden while a repeated Show waits for its refreshed url", async ({ page }) => {
    const stored = "https://account.blob.core.windows.net/container/pic.png";
    const opened = `${stored}?sig=opened`;
    const fresh = `${stored}?sig=fresh`;
    const portrait = readFileSync(new URL("./fixtures/portrait-600x1200.png", import.meta.url));
    let reopening = false;
    let refreshRequests = 0;
    let finishRefresh: () => void = () => {};
    const refreshed = new Promise<void>((resolve) => { finishRefresh = resolve; });
    const detailFor = (signature: string) => ({
      ...EXAMPLE_DETAIL,
      members: [mediaMember("image-1", "image_path", stored, signature)],
    });
    await mockDatasetExamples(page, detailFor(opened));
    await page.route("**/api/datasets/seeds/example-1**", async (route) => {
      const signature = reopening ? fresh : opened;
      if (reopening) {
        refreshRequests += 1;
        await refreshed;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(detailFor(signature)),
      });
    });
    await page.context().route("https://account.blob.core.windows.net/**", async (route) => {
      if (reopening && route.request().url() === opened) {
        await route.fulfill({ status: 403, contentType: "text/plain", body: "expired" });
        return;
      }
      await route.fulfill({ status: 200, contentType: "image/png", body: portrait });
    });
    const mediaRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).hostname === "account.blob.core.windows.net") mediaRequests.push(request.url());
    });

    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`);
    const detail = page.getByRole("region", { name: "Example detail" });
    await detail.getByRole("button", { name: "Show image preview" }).click();
    const image = detail.locator("img");
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(600);
    await detail.getByRole("button", { name: "Hide image preview" }).click();

    reopening = true;
    const show = detail.getByRole("button", { name: "Show image preview" });
    const refreshStarted = page.waitForRequest((request) => request.url().includes("/api/datasets/seeds/example-1"));
    try {
      await show.dblclick();
      await refreshStarted;
      await expect(show).toBeDisabled();
      await expect(show).toHaveAttribute("aria-busy", "true");
      expect(refreshRequests).toBe(1);
      await expect(image).toHaveCount(0);
      expect(mediaRequests).toEqual([opened]);
    } finally {
      finishRefresh();
    }

    await expect(image).toHaveAttribute("src", fresh);
    await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(600);
    expect(mediaRequests).toEqual([opened, fresh]);
    await expect(detail.getByText(/Image failed to load/)).toHaveCount(0);
    await expect(detail.getByTestId("seed-value-image-1")).toHaveText(stored);
  });

  test("ignores a media refresh that finishes after the example is closed", async ({ page }) => {
    const stored = "https://account.blob.core.windows.net/container/pic.png";
    const opened = `${stored}?sig=opened`;
    const reopened = `${stored}?sig=newly-opened`;
    const olderRefresh = `${stored}?sig=older-refresh`;
    let mode: "opened" | "hold" | "reopened" = "opened";
    let releaseHeld: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      releaseHeld = resolve;
    });
    const detailFor = (signature: string) => ({
      ...EXAMPLE_DETAIL,
      members: [
        EXAMPLE_DETAIL.members[0],
        mediaMember("image-1", "image_path", stored, signature),
      ],
    });

    await mockDatasetExamples(page, detailFor(opened));
    await page.route("**/api/datasets/seeds/example-1**", async (route) => {
      if (mode === "hold") {
        await held;
        try {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(detailFor(olderRefresh)),
          });
        } catch {
          // The example was closed, so the browser aborted this refresh.
        }
        return;
      }
      const signature = mode === "reopened" ? reopened : opened;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(detailFor(signature)),
      });
    });
    await page.context().route("https://account.blob.core.windows.net/**", async (route) => {
      if (route.request().url().includes("sig=newly-opened")) {
        await route.fulfill({
          status: 200,
          contentType: "image/png",
          body: Buffer.from(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            "base64",
          ),
        });
        return;
      }
      await route.fulfill({ status: 403, contentType: "text/plain", body: "expired" });
    });

    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`);
    const detail = page.getByRole("region", { name: "Example detail" });
    await detail.getByRole("button", { name: "Show image preview" }).click();
    await expect(detail.getByText(/Image failed to load/)).toBeVisible();

    mode = "hold";
    const refreshStarted = page.waitForRequest((request) => request.url().includes("/api/datasets/seeds/example-1"));
    await detail.getByRole("button", { name: "Retry image" }).click();
    await refreshStarted;

    mode = "reopened";
    await page.getByRole("button", { name: "Hide alpha" }).click();
    await expect(detail).toHaveCount(0);
    await page.getByRole("button", { name: "Show alpha" }).click();
    await expect(detail.getByText("prompt body kept")).toBeVisible();
    await detail.getByRole("button", { name: "Show image preview" }).click();
    const image = detail.locator("img");
    await expect(image).toHaveAttribute("src", reopened);

    releaseHeld();
    await page.waitForLoadState("networkidle");
    await expect(image).toHaveAttribute("src", reopened);
    await expect(page.getByText("Couldn't refresh the link.")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry example" })).toHaveCount(0);
  });

  test("keeps a portrait image above its stored value at desktop and mobile widths", async ({ page }) => {
    const portrait = readFileSync(new URL("./fixtures/portrait-600x1200.png", import.meta.url));
    await mockDatasetExamples(page, {
      ...EXAMPLE_DETAIL,
      members: [
        EXAMPLE_DETAIL.members[0],
        mediaMember("image-1", "image_path", "/dbdata/seed-prompt-entries/portrait.png", "/api/media?path=%2Fdbdata%2Fseed-prompt-entries%2Fportrait.png"),
      ],
    });
    await page.route("**/api/media**", async (route) => {
      await route.fulfill({ status: 200, contentType: "image/png", body: portrait });
    });

    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`);
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const detail = page.getByRole("region", { name: "Example detail" });
      await detail.getByRole("button", { name: "Show image preview" }).click();
      const image = detail.getByRole("img", { name: "image seed" });
      const value = detail.getByTestId("seed-value-image-1");
      await image.scrollIntoViewIfNeeded();
      await expect.poll(() => image.evaluate((element: HTMLImageElement) => (
        element.naturalWidth === 600
        && element.naturalHeight === 1200
        && element.getBoundingClientRect().height > 100
      ))).toBe(true);
      const layout = await image.evaluate((element) => {
        const stored = element.ownerDocument.querySelector("[data-testid='seed-value-image-1']");
        if (!stored) throw new Error("Stored value is missing");
        const imageBox = element.getBoundingClientRect();
        const valueBox = stored.getBoundingClientRect();
        return {
          imageHeight: imageBox.height,
          imageBottom: imageBox.bottom,
          valueTop: valueBox.top,
        };
      });
      expect(layout.imageHeight).toBeGreaterThan(100);
      expect(layout.imageHeight).toBeLessThanOrEqual(404);
      expect(layout.valueTop).toBeGreaterThanOrEqual(layout.imageBottom - 1);
      await expect(value).toBeVisible();
      await detail.getByRole("button", { name: "Hide image preview" }).click();
    }
  });
});

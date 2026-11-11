import { expect as baseExpect, test } from "./_fixtures";
import { compatibilityHeaders } from "./_compatibility";
import type { DatasetInfo } from "../src/types";

const expect = baseExpect.configure({ timeout: 30_000 });
const SELECTION_KEY = "dataset:named:jailbreak_templates";

interface SeedExampleSummary {
  example_id: string;
  name: string | null;
  preview: string;
}

interface SeedMember {
  data_type: string;
  value: string;
  value_url?: string | null;
}

interface SeedExampleDetail extends SeedExampleSummary {
  members: SeedMember[];
}

interface SeedExamplePage {
  items: SeedExampleSummary[];
  total: number;
  pagination: {
    has_more: boolean;
    next_cursor: string | null;
  };
}

async function waitForBackend(request: { get: (url: string, options?: { timeout?: number }) => Promise<{ ok: () => boolean }> }): Promise<void> {
  const maxWait = 60_000;
  const interval = 1_000;
  const start = Date.now();
  while (Date.now() - start < maxWait) {
    try {
      const response = await request.get("/api/health", { timeout: 2_000 });
      if (response.ok()) return;
    } catch {
      // The backend is still loading the local jailbreak templates.
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw new Error("Backend did not become healthy within 60 seconds");
}

test.describe("dataset examples against the real backend @seeded", () => {
  test.beforeAll(async ({ request }) => {
    await waitForBackend(request);
  });

  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    test(`describes an unloaded local provider without changing memory at ${viewport.width}px @seeded`, async ({ page, request }, testInfo) => {
      test.setTimeout(90_000);
      await page.setViewportSize(viewport);
      const headers = compatibilityHeaders();
      const before = await request.get("/api/datasets?loaded_only=true", { headers });
      expect(before.ok(), await before.text()).toBe(true);
      const beforeCatalog: { items: DatasetInfo[] } = await before.json();
      expect(beforeCatalog.items.some((item: DatasetInfo) => item.name === "adv_bench")).toBe(false);

      const unexpectedRequests: string[] = [];
      page.on("request", (outgoing) => {
        const path = new URL(outgoing.url()).pathname;
        if (path.startsWith("/api/datasets") && (
          outgoing.method() !== "GET" || path.startsWith("/api/datasets/seeds")
        )) {
          unexpectedRequests.push(`${outgoing.method()} ${path}`);
        }
      });

      await page.goto("/datasets?view=available&q=adv_bench");
      await page.getByRole("link", { name: "adv_bench", exact: true }).click();
      await expect(page.getByRole("heading", { name: "adv_bench", exact: true })).toBeVisible();
      await expect(page.getByRole("link", { name: "Source", exact: true }))
        .toHaveAttribute("href", "https://github.com/llm-attacks/llm-attacks");
      await expect(page.getByText("This provider's dataset has not been loaded into memory.")).toBeVisible();
      await expect(page.getByRole("table", { name: "Examples" })).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
      const metadata = await page.getByRole("region", { name: "Provider metadata" }).evaluate((region) => ({
        scrollWidth: region.scrollWidth,
        clientWidth: region.clientWidth,
      }));
      expect(metadata.scrollWidth).toBeLessThanOrEqual(metadata.clientWidth + 1);
      await page.screenshot({ path: testInfo.outputPath("provider-description.png"), fullPage: true });

      await page.getByRole("link", { name: "Back to datasets", exact: true }).click();
      await expect(page.getByRole("link", { name: "adv_bench", exact: true })).toBeVisible();
      await expect(page).toHaveURL((url: URL) => url.searchParams.get("view") === "available"
        && url.searchParams.get("q") === "adv_bench");
      expect(unexpectedRequests).toEqual([]);
      const after = await request.get("/api/datasets?loaded_only=true", { headers });
      expect(after.ok(), await after.text()).toBe(true);
      expect(await after.json()).toEqual(beforeCatalog);
    });
  }

  for (const entry of ["chat", "dataset"]) {
    test(`imports a template from ${entry} into the visible prompt box without sending @seeded`, async ({ page, request }) => {
      test.setTimeout(90_000);
      const headers = compatibilityHeaders();
      const list = await request.get(
        `/api/datasets/seeds?selection_key=${encodeURIComponent(SELECTION_KEY)}&limit=20`,
        { headers },
      );
      expect(list.ok(), await list.text()).toBe(true);
      const examples = await list.json() as SeedExamplePage;
      const selected = examples.items.find((example: SeedExampleSummary) => example.name === "wikipedia with title");
      if (!selected) throw new Error("Expected the bundled wikipedia with title template");
      const response = await request.get(
        `/api/datasets/seeds/${selected.example_id}?selection_key=${encodeURIComponent(SELECTION_KEY)}`,
        { headers },
      );
      expect(response.ok(), await response.text()).toBe(true);
      const detail = await response.json() as SeedExampleDetail;
      const prompt = detail.members.find((member: SeedMember) => member.data_type === "text");
      if (!prompt) throw new Error("Expected template text");
      const mutations: string[] = [];
      page.on("request", (outgoing) => {
        if (outgoing.method() === "POST") mutations.push(new URL(outgoing.url()).pathname);
      });

      if (entry === "chat") {
        await page.goto("/attack/chat");
        await expect(page.getByPlaceholder("Type prompt here")).toBeEnabled();
        await page.getByRole("button", { name: "Datasets", exact: true }).click();
        await page.getByRole("link", { name: "jailbreak_templates", exact: true }).click();
      } else {
        await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(SELECTION_KEY)}`);
      }
      await page.getByRole("button", { name: "Show wikipedia with title", exact: true }).click();
      await page.getByTestId("import-into-chat-button").click();
      await page.getByTestId("import-new-attack-item").click();
      await expect(page).toHaveURL(/\/attack\/chat$/);
      const composer = page.getByPlaceholder("Type prompt here");
      await expect(composer).toBeVisible();
      await expect(composer).toHaveValue(prompt.value);
      await composer.fill("Edited imported template");
      await expect(composer).toHaveValue("Edited imported template");
      expect(mutations.filter((path: string) => path.startsWith("/api/attacks"))).toEqual([]);
    });
  }

  test("lists stored examples and opens one member @seeded", async ({ page, request }) => {
    test.setTimeout(90_000);
    const headers = compatibilityHeaders();
    const datasets = await request.get("/api/datasets?loaded_only=true", { headers });
    expect(datasets.ok(), await datasets.text()).toBe(true);
    const catalog = await datasets.json() as { items: Array<{ name: string; selection_key: string; logical_examples: number | null }> };
    const dataset = catalog.items.find((item) => item.selection_key === SELECTION_KEY);
    expect(dataset?.name).toBe("jailbreak_templates");
    expect(dataset?.logical_examples ?? 0).toBeGreaterThan(0);

    const list = await request.get(
      `/api/datasets/seeds?selection_key=${encodeURIComponent(SELECTION_KEY)}&limit=20`,
      { headers },
    );
    expect(list.ok(), await list.text()).toBe(true);
    const pageData = await list.json() as { items: SeedExampleSummary[]; total: number };
    const first = pageData.items[0];
    if (!first?.example_id) {
      throw new Error("Expected the local dataset to return at least one example");
    }
    expect(first.example_id).toMatch(/[a-f]/);

    const detailResponse = await request.get(
      `/api/datasets/seeds/${first.example_id}?selection_key=${encodeURIComponent(SELECTION_KEY)}`,
      { headers },
    );
    expect(detailResponse.ok(), await detailResponse.text()).toBe(true);
    const detail = await detailResponse.json() as SeedExampleDetail;
    expect(detail.example_id).toBe(first.example_id);
    const textMember = detail.members.find((member) => member.data_type === "text" && member.value.trim() !== "");
    if (!textMember) {
      throw new Error("Expected the stored example to include a text member");
    }
    for (const member of detail.members) {
      if (member.data_type === "text") {
        expect(member.value_url ?? null).toBeNull();
      }
    }

    const mediaRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/media") mediaRequests.push(request.url());
    });

    await page.goto(`/datasets/detail?selection_key=${encodeURIComponent(SELECTION_KEY)}`);
    await expect(page.getByRole("heading", { level: 1, name: "jailbreak_templates" })).toBeVisible();
    const table = page.getByRole("table", { name: "Examples" });
    await expect(table).toBeVisible();
    await expect(page.getByTestId("example-status")).toContainText("matching example");
    const firstRow = table.getByRole("row").nth(1);
    await expect(firstRow).toContainText(first.preview);
    const label = first.name || first.preview || first.example_id;
    await firstRow.getByRole("button", { name: `Show ${label}` }).click();

    const region = page.getByRole("region", { name: "Example detail" });
    const snippet = textMember.value.replace(/\s+/g, " ").trim().slice(0, 80);
    await expect(region.getByText(snippet, { exact: false })).toBeVisible();
    await expect(page.getByRole("table", { name: "Examples" }).locator("img, audio, video")).toHaveCount(0);
    await expect(region.locator("img, audio, video")).toHaveCount(0);
    expect(mediaRequests).toEqual([]);
    await expect(page).toHaveURL(new RegExp(`[?&]example=${first.example_id}`));

    await page.reload();
    await expect(page.getByRole("region", { name: "Example detail" })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`[?&]example=${first.example_id}`));
    await expect(page).toHaveURL(new RegExp(`selection_key=${encodeURIComponent(SELECTION_KEY)}`));

    const mismatched = first.example_id.toUpperCase();
    await page.goto(
      `/datasets/detail?selection_key=${encodeURIComponent(SELECTION_KEY)}&example=${mismatched}`,
    );
    await expect(page.getByText("This example is not in the dataset.")).toBeVisible();
    await expect(page.getByRole("region", { name: "Example detail" })).toHaveCount(0);
    await page.getByRole("button", { name: "Back to the examples" }).click();
    await expect(page).not.toHaveURL(/[?&]example=/);
    await expect(page.getByRole("table", { name: "Examples" })).toBeVisible();
  });

  test("paginates stored examples and keeps the second-page selection on refresh @seeded", async ({ page, request }) => {
    test.setTimeout(90_000);
    const headers = compatibilityHeaders();
    // One example per page guarantees pagination with the bundled local dataset,
    // without adding records or depending on its current number of templates.
    const params = new URLSearchParams({ selection_key: SELECTION_KEY, limit: "1" });
    const firstResponse = await request.get(`/api/datasets/seeds?${params}`, { headers });
    expect(firstResponse.ok(), await firstResponse.text()).toBe(true);
    const firstPage = await firstResponse.json() as SeedExamplePage;
    expect(firstPage.total).toBeGreaterThan(1);
    expect(firstPage.items).toHaveLength(1);
    expect(firstPage.pagination.has_more).toBe(true);
    const cursor = firstPage.pagination.next_cursor;
    if (!cursor) throw new Error("Expected a second page of stored examples");

    const secondParams = new URLSearchParams(params);
    secondParams.set("cursor", cursor);
    const secondResponse = await request.get(`/api/datasets/seeds?${secondParams}`, { headers });
    expect(secondResponse.ok(), await secondResponse.text()).toBe(true);
    const secondPage = await secondResponse.json() as SeedExamplePage;
    expect(secondPage.items).toHaveLength(1);
    const first = firstPage.items[0];
    const second = secondPage.items[0];
    expect(second.example_id).not.toBe(first.example_id);

    const detailResponse = await request.get(
      `/api/datasets/seeds/${second.example_id}?selection_key=${encodeURIComponent(SELECTION_KEY)}`,
      { headers },
    );
    expect(detailResponse.ok(), await detailResponse.text()).toBe(true);
    const detail = await detailResponse.json() as SeedExampleDetail;
    expect(detail.example_id).toBe(second.example_id);
    const textMember = detail.members.find((member) => member.data_type === "text" && member.value.trim() !== "");
    if (!textMember) throw new Error("Expected the second-page example to include a text member");

    await page.goto(`/datasets/detail?${params}`);
    const table = page.getByRole("table", { name: "Examples" });
    await expect(table).toBeVisible();
    await expect(table.getByRole("row")).toHaveCount(2);
    await expect(table.getByRole("row").nth(1)).toContainText(first.preview);
    await expect(page.getByRole("button", { name: "Previous page" })).toBeDisabled();

    await page.getByRole("button", { name: "Next page" }).click();
    await expect(page).toHaveURL((url) => url.searchParams.get("cursor") === cursor);
    await expect(table.getByRole("row").nth(1)).toContainText(second.preview);
    await expect(page.getByTestId("example-status")).toHaveText(`${firstPage.total.toLocaleString("en-US")} matching examples, page 2`);

    await page.getByRole("button", { name: "Previous page" }).click();
    await expect(page).toHaveURL((url) => !url.searchParams.has("cursor"));
    await expect(table.getByRole("row").nth(1)).toContainText(first.preview);
    await page.getByRole("button", { name: "Next page" }).click();
    await expect(table.getByRole("row").nth(1)).toContainText(second.preview);

    const label = second.name || second.preview || second.example_id;
    await table.getByRole("button", { name: `Show ${label}`, exact: true }).click();
    const region = page.getByRole("region", { name: "Example detail" });
    const snippet = textMember.value.replace(/\s+/g, " ").trim().slice(0, 80);
    await expect(region.getByText(snippet, { exact: false })).toBeVisible();
    const selectedUrl = new URL(page.url());
    expect(selectedUrl.searchParams.get("example")).toBe(second.example_id);
    expect(selectedUrl.searchParams.get("cursor")).toBe(cursor);

    await page.reload();
    await expect(region.getByText(snippet, { exact: false })).toBeVisible();
    await expect(page).toHaveURL(selectedUrl.toString());
    await expect(table.getByRole("row", { selected: true })).toContainText(second.preview);
    await expect(table.getByRole("button", { name: `Hide ${label}`, exact: true })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByTestId("example-status")).toHaveText(`${firstPage.total.toLocaleString("en-US")} matching examples, page 2`);
  });
});

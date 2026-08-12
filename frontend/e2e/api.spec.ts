import { test, expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";

// API tests go through the Vite dev server proxy (/api -> configured backend)
// rather than hitting the backend directly, so they work as soon as
// Playwright's webServer is ready.

async function waitForBackend(request: APIRequestContext): Promise<void> {
  const maxWait = 30_000;
  const interval = 1_000;
  const start = Date.now();
  while (Date.now() - start < maxWait) {
    try {
      const response = await request.get("/api/health", { timeout: 2_000 });
      if (response.ok()) {
        return;
      }
    } catch {
      // Backend not ready yet
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
  throw new Error("Backend did not become healthy within 30 seconds");
}

test.describe("API Health Check", () => {
  test.beforeAll(async ({ request }) => {
    await waitForBackend(request);
  });

  test("should have healthy backend API @seeded", async ({ request }) => {
    const response = await request.get("/api/health", { timeout: 10_000 });

    expect(response.ok()).toBe(true);
    const data = await response.json();
    expect(data).toBeDefined();
  });

  test("should get version from API @seeded", async ({ request }) => {
    const response = await request.get("/api/version");

    expect(response.ok()).toBe(true);
    const data = await response.json();
    expect(data).toBeDefined();
  });

});

test.describe("Targets API", () => {
  test.beforeAll(async ({ request }) => {
    await waitForBackend(request);
  });

  test("should list targets @seeded", async ({ request }) => {
    const response = await request.get("/api/targets?limit=50");

    expect(response.ok()).toBe(true);
    const data = await response.json();
    expect(data).toHaveProperty("items");
    expect(Array.isArray(data.items)).toBe(true);
  });

  test("should create and retrieve a target @seeded", async ({ request }) => {
    test.setTimeout(90_000);
    const createPayload = {
      type: "OpenAIChatTarget",
      auth_mode: "api_key",
      params: {
        endpoint: "https://e2e-test.openai.azure.com",
        model_name: "gpt-4o-e2e-test",
        api_key: "e2e-test-key",
      },
    };

    const createResp = await request.post("/api/targets", {
      data: createPayload,
      timeout: 60_000,
    });
    expect(createResp.ok()).toBe(true);

    const created = await createResp.json();
    expect(created).toHaveProperty("target_registry_name");
    expect(created.identifier.class_name).toBe("OpenAIChatTarget");

    // Retrieve via list and check it's there
    const listResp = await request.get("/api/targets?limit=200");
    expect(listResp.ok()).toBe(true);
    const list = await listResp.json();
    const found = list.items.find(
      (t: { target_registry_name: string }) =>
        t.target_registry_name === created.target_registry_name,
    );
    expect(found).toBeDefined();
  });
});

test.describe("Attacks API", () => {
  test.beforeAll(async ({ request }) => {
    await waitForBackend(request);
  });

  test("should list attacks @seeded", async ({ request }) => {
    const response = await request.get("/api/attacks");
    expect(response.ok()).toBe(true);
  });
});

test.describe("Scenarios API", () => {
  test.beforeAll(async ({ request }) => {
    await waitForBackend(request);
  });

  test("should expose scenario catalog details and queue state @seeded", async ({ request }) => {
    test.setTimeout(90_000);
    const catalogResponse = await request.get("/api/scenarios/catalog?limit=200");
    expect(catalogResponse.ok()).toBe(true);
    const catalog = await catalogResponse.json();
    expect(catalog.items.length).toBeGreaterThan(0);

    const scenarioName = catalog.items[0].scenario_name as string;
    const detailResponse = await request.get(`/api/scenarios/catalog/${encodeURIComponent(scenarioName)}`);
    expect(detailResponse.ok()).toBe(true);
    const detail = await detailResponse.json();
    expect(detail.scenario_name).toBe(scenarioName);
    expect(detail.dataset_size_limit).toEqual(expect.objectContaining({
      default_scope: expect.any(String),
      override_scope: expect.any(String),
    }));

    const queueResponse = await request.get("/api/scenarios/runs/queue");
    expect(queueResponse.ok()).toBe(true);
    await expect(queueResponse.json()).resolves.toEqual(expect.objectContaining({
      revision: expect.any(Number),
      queued: expect.any(Array),
    }));
  });
});

test.describe("Error Handling", () => {
  test("should display UI when backend is slow", async ({ page }) => {
    // Intercept and delay API calls
    await page.route("**/api/**", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      await route.continue();
    });

    await page.goto("/");

    // UI should be responsive even while APIs are delayed
    await expect(page.getByTitle("Chat")).toBeVisible({ timeout: 10000 });
  });
});

import { test, expect, type Page, type Request } from "./_fixtures";
import { getCompatibilityId, isValidCompatibilityId, mockVersion } from "./_compatibility";

function otherCompatibilityId(): string {
  const identity = getCompatibilityId();
  return identity.slice(0, -40) + (identity.endsWith("a".repeat(40)) ? "b" : "a").repeat(40);
}

test("validates strict version suffixes and rejects whitespace in stamps", () => {
  const identity = "1.2.3rc1.post2.dev3+g" + "a".repeat(40);
  expect(isValidCompatibilityId(identity)).toBe(true);
  for (const invalid of [
    `${identity}\n`, `${identity}\r\n`, ` ${identity}`, `${identity} `,
    identity.replace("rc1.post2.dev3", "custom"), identity.toUpperCase(),
    identity.slice(0, -1), identity.replace("rc1.post2.dev3", ".dev3.post2"), null,
  ]) {
    expect(isValidCompatibilityId(invalid)).toBe(false);
  }
});

async function mockBusinessRequests(page: Page): Promise<Request[]> {
  const requests: Request[] = [];
  await page.route(/\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (["/api/version", "/api/health", "/api/auth/config"].includes(path)) {
      return route.fallback();
    }
    requests.push(route.request());
    expect(route.request().headers()["pyrit-compatibility-id"]).toBe(getCompatibilityId());
    await route.fulfill({ json: { items: [], labels: {} } });
  });
  return requests;
}

test("waits for a matching packaged stamp before sending business requests", async ({ page }) => {
  const requests = await mockBusinessRequests(page);
  let releaseVersion!: () => void;
  const versionReady = new Promise<void>((resolve) => { releaseVersion = resolve; });
  await page.route(/\/api\/version(?:\?|$)/, async (route) => {
    await versionReady;
    await route.fulfill({ json: mockVersion() });
  });
  await page.goto("/");
  await expect(page.getByRole("status")).toContainText("Checking frontend and backend compatibility");
  await expect(page.getByTitle("Chat", { exact: true })).not.toBeVisible();
  expect(requests).toHaveLength(0);
  releaseVersion();
  await expect(page.getByTitle("Chat", { exact: true })).toBeVisible();
  await expect.poll(() => requests.length).toBeGreaterThan(0);
});

for (const [name, payload] of [
  ["same version with a different commit", () => ({ ...mockVersion(), compatibility_id: otherCompatibilityId() })],
  ["missing identity", () => ({ version: mockVersion().version })],
  ["malformed identity", () => ({ ...mockVersion(), compatibility_id: "not-a-build" })],
] as const) {
  test(`blocks startup with ${name}`, async ({ page }) => {
    const requests = await mockBusinessRequests(page);
    await page.route(/\/api\/version(?:\?|$)/, async (route) => {
      await route.fulfill({ json: payload() });
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "PyRIT compatibility blocked" })).toBeVisible();
    await expect(page.getByTitle("Chat", { exact: true })).not.toBeVisible();
    expect(requests).toHaveLength(0);
  });
}

for (const [status, type] of [
  [400, "urn:pyrit:compatibility:invalid"],
  [409, "urn:pyrit:compatibility:mismatch"],
] as const) {
  test(`latches a later ${status} rejection without replaying a mutation`, async ({ page }) => {
    await mockBusinessRequests(page);
    let mutations = 0;
    await page.route(/\/api\/attacks$/, async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      mutations += 1;
      expect(route.request().headers()["pyrit-compatibility-id"]).toBe(getCompatibilityId());
      await route.fulfill({ status, json: { type, expected: otherCompatibilityId(), actual: getCompatibilityId() } });
    });
    await page.goto("/");
    await expect(page.getByTitle("Chat", { exact: true })).toBeVisible();
    await page.evaluate(async () => {
      const modulePath = "/src/services/api.ts";
      const { apiClient } = await import(modulePath);
      for (const attempt of [1, 2]) {
        await apiClient.post("/attacks", { attempt }).catch(() => undefined);
      }
    });
    await expect(page.getByRole("heading", { name: "PyRIT compatibility blocked" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText(getCompatibilityId());
    await expect(page.getByRole("alert")).toContainText(otherCompatibilityId());
    await expect(page.getByTitle("Chat", { exact: true })).not.toBeVisible();
    expect(mutations).toBe(1);
  });
}

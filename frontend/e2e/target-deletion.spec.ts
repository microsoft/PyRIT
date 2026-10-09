import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  test as base, expect, request as playwrightRequest,
  type APIRequestContext, type BrowserContext, type Page,
} from "./_fixtures";
import { compatibilityHeaders } from "./_compatibility";
import type { TargetInstance } from "@/types";

// Forward browser traffic unchanged to the isolated real backend, not API mocks.
async function proxyBackend(context: BrowserContext, backendUrl: string): Promise<void> {
  await context.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: `${backendUrl}${url.pathname}${url.search}` });
    await route.fulfill({ response });
  });
}

const test = base.extend<{
  backendUrl: string;
  api: APIRequestContext;
  protectedTargets: boolean;
  backendProxy: void;
}>({
  protectedTargets: [false, { option: true }],
  backendUrl: async ({ protectedTargets }, runTest) => {
    const root = fileURLToPath(new URL("../../", import.meta.url));
    const virtualenvPython = `${root}.venv/bin/python`;
    const python = process.env.PYRIT_PYTHON
      ?? (existsSync(virtualenvPython) ? virtualenvPython : "python");
    const backend = spawn(python, [
      "-m", "uvicorn", "frontend.e2e.fixtures.target_deletion_backend:app",
      "--host", "127.0.0.1", "--port", "0",
    ], {
      cwd: root,
      env: {
        ...process.env, PYRIT_DEV_MODE: "true",
        PYRIT_E2E_PROTECTED_TARGETS: String(protectedTargets),
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stopped = new Promise<void>((resolve) => backend.once("close", () => resolve()));
    let logs = "";
    let startupTimeout: ReturnType<typeof setTimeout> | undefined;
    const started = new Promise<string>((resolve, reject) => {
      startupTimeout = setTimeout(() => reject(new Error(`Backend startup timed out:\n${logs}`)), 30_000);
      backend.once("error", reject);
      backend.once("exit", (code: number | null) => {
        reject(new Error(`Backend exited (${code}):\n${logs}`));
      });
      const collect = (chunk: Buffer): void => {
        logs = (logs + chunk.toString()).slice(-20_000);
        const address = logs.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/);
        if (address) resolve(address[1]);
      };
      backend.stdout.on("data", collect);
      backend.stderr.on("data", collect);
    });
    try {
      const url = await started;
      clearTimeout(startupTimeout);
      await runTest(url);
    } finally {
      clearTimeout(startupTimeout);
      backend.kill("SIGTERM");
      const forcedShutdown = setTimeout(() => backend.kill("SIGKILL"), 5_000);
      await stopped;
      clearTimeout(forcedShutdown);
    }
  },
  backendProxy: [async ({ context, backendUrl }, runTest) => {
    await proxyBackend(context, backendUrl);
    await runTest();
    await context.unrouteAll({ behavior: "wait" });
  }, { auto: true }],
  api: async ({ backendUrl }, runTest) => {
    const api = await playwrightRequest.newContext({
      baseURL: backendUrl, extraHTTPHeaders: compatibilityHeaders(),
    });
    await runTest(api);
    await api.dispose();
  },
});

async function createTarget(api: APIRequestContext, name: string): Promise<void> {
  const response = await api.post("/api/targets", {
    data: {
      name, type: "OpenAIChatTarget",
      params: { endpoint: `https://${name}.example/v1`, model_name: "gpt-4o", api_key: "offline-placeholder" },
    },
  });
  expect(response.status()).toBe(201);
  expect((await response.json()).target_registry_name).toBe(name);
}

async function openDelete(page: Page, name: string): Promise<void> {
  const actions = page.getByRole("button", { name: `Actions for ${name}`, exact: true });
  await actions.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("End");
  await expect(page.getByRole("menuitem", { name: `Delete ${name}`, exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Delete target?" })).toBeVisible();
}

test.describe("Target deletion against an isolated backend @seeded", () => {
  test.setTimeout(60_000);

  test("creates a named target, confirms global deletion, and restores the empty state and focus", async ({
    page, api, browser, backendUrl, baseURL,
  }) => {
    await page.goto("/registry/targets");
    await expect(page.getByText("No Targets Configured", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "New Target", exact: true }).click();
    const create = page.getByRole("dialog", { name: "Create New Target" });
    await create.getByRole("textbox", { name: "Target name" }).fill("team-image-model");
    await create.getByRole("combobox", { name: "Target Type" }).click();
    await page.getByRole("option", { name: /Implementation: OpenAIImageTarget/ }).click();
    await create.getByPlaceholder("https://your-resource.openai.azure.com/").fill("https://image.example/v1");
    await create.getByPlaceholder("e.g. gpt-4o, my-deployment").fill("gpt-image-1");
    await create.getByPlaceholder("API key (stored in memory only)").fill("offline-placeholder");
    await create.getByRole("button", { name: "Create Target", exact: true }).click();
    await expect(create).not.toBeVisible();

    const observerContext = await browser.newContext({ baseURL });
    try {
      await proxyBackend(observerContext, backendUrl);
      await observerContext.addInitScript(() => localStorage.setItem("pyrit-tour-completed", "true"));
      const observer = await observerContext.newPage();
      await observer.goto("/registry/targets");
      await expect(observer.getByRole("button", { name: "Actions for team-image-model" })).toBeVisible();
      await openDelete(page, "team-image-model");
      const dialog = page.getByRole("dialog", { name: "Delete target?" });
      await expect(dialog).toContainText("team-image-model");
      await expect(dialog).toContainText("all users");
      await expect(dialog).toContainText("cannot be undone");
      await page.keyboard.press("Escape");
      await expect(page.getByRole("button", { name: "Actions for team-image-model" })).toBeFocused();
      expect((await api.get("/api/targets/team-image-model")).status()).toBe(200);

      await openDelete(page, "team-image-model");
      await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(dialog.getByRole("button", { name: "Delete target", exact: true })).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(page.getByText("No Targets Configured", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "New Target", exact: true })).toBeFocused();
      expect((await api.get("/api/targets/team-image-model")).status()).toBe(404);
      await observer.bringToFront();
      await observer.getByRole("button", { name: "Refresh", exact: true }).click();
      await expect(observer.getByText("No Targets Configured", { exact: true })).toBeVisible();
      await observer.reload();
      await expect(observer.getByText("No Targets Configured", { exact: true })).toBeVisible();
    } finally {
      await observerContext.close();
    }
  });

  test("prompts for new defaults when another user reopens the registry after deletion", async ({ page, api }) => {
    await createTarget(api, "shared-default");
    await createTarget(api, "replacement");
    await page.goto("/registry/targets");
    await page.getByLabel("Default objective target", { exact: true }).selectOption("shared-default");
    await page.getByLabel("Default adversarial target", { exact: true }).selectOption("shared-default");
    // The independent API client represents the user deleting a shared target.
    expect((await api.delete("/api/targets/shared-default")).status()).toBe(204);
    await page.goto("/");
    await page.getByRole("button", { name: "Registry", exact: true }).click();
    for (const role of ["objective", "adversarial"]) {
      await expect(page.getByText(`The saved default ${role} target is unavailable or has changed. Select a new default in the registry.`))
        .toBeVisible({ timeout: 15_000 });
      await page.getByLabel(`Default ${role} target`, { exact: true }).selectOption("replacement");
      await expect(page.getByRole("button", { name: `Clear ${role} default` })).not.toBeVisible();
    }
    await page.reload();
    await expect(page.getByRole("combobox", { name: "Default objective target", exact: true })).toHaveValue("replacement");
    await expect(page.getByRole("combobox", { name: "Default adversarial target", exact: true })).toHaveValue("replacement");
  });

  test("refreshes cleanly if a second client deletes the target while confirmation is open", async ({ page, api }) => {
    await createTarget(api, "already-deleted");
    await page.goto("/registry/targets");
    await openDelete(page, "already-deleted");
    expect((await api.delete("/api/targets/already-deleted")).status()).toBe(204);
    await page.getByRole("dialog").getByRole("button", { name: "Delete target", exact: true }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(page.getByText("No Targets Configured", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "New Target", exact: true })).toBeFocused();
    await expect(page.getByText(/Target .* not found/)).not.toBeVisible();
  });

  test("refuses to delete a round-robin member and names its group", async ({ page, api }) => {
    await createTarget(api, "member-a");
    await createTarget(api, "member-b");
    const group = await api.post("/api/targets", {
      data: { name: "team-round-robin", type: "RoundRobinTarget", params: { targets: ["member-a", "member-b"] } },
    });
    expect(group.status()).toBe(201);
    await page.goto("/registry/targets");
    await openDelete(page, "member-a");
    await page.getByRole("dialog").getByRole("button", { name: "Delete target", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("used by target 'team-round-robin'");
    expect((await api.delete("/api/targets/member-a")).status()).toBe(409);
    expect((await api.get("/api/targets/member-a")).status()).toBe(200);
    await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
    await openDelete(page, "team-round-robin");
    await page.getByRole("dialog").getByRole("button", { name: "Delete target", exact: true }).click();
    await expect(page.getByRole("button", { name: "Actions for team-round-robin" })).not.toBeVisible();
    expect((await api.delete("/api/targets/member-a")).status()).toBe(204);
  });

  test.describe("protected registrations", () => {
    test.use({ protectedTargets: true });

    test("explains disabled controls for every managed row and refuses direct API deletion", async ({ page, api }) => {
      await page.goto("/registry/targets");
      const response = await api.get("/api/targets");
      const { items }: { items: TargetInstance[] } = await response.json();
      expect(items.some((target: TargetInstance) => target.target_registry_name === "adversarial_chat")).toBe(true);
      expect(items.some((target: TargetInstance) => target.identifier.class_name === "RoundRobinTarget")).toBe(true);
      for (const target of items) {
        const name = target.target_registry_name;
        const actions = page.getByRole("button", { name: `Actions for ${name}`, exact: true });
        await actions.focus();
        await page.keyboard.press("Enter");
        await page.keyboard.press("End");
        const deletion = page.getByRole("menuitem", { name: `Delete ${name}`, exact: true });
        await expect(deletion).toBeFocused();
        await expect(deletion).toHaveAttribute("aria-disabled", "true");
        await expect(page.getByRole("tooltip")).toContainText(target.deletion_blocked_reason ?? "");
        await expect(page.getByRole("tooltip")).toContainText(/\.env.*\.pyrit_conf.*reinitialize/);
        await page.keyboard.press("Enter");
        await expect(page.getByRole("dialog")).not.toBeVisible();
        await page.keyboard.press("Escape");
        const deleted = await api.delete(`/api/targets/${encodeURIComponent(name)}`);
        expect(deleted.status()).toBe(403);
        expect((await deleted.json()).detail).toBe(target.deletion_blocked_reason);
      }
    });
  });
});

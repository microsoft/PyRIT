import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

import type { UserPreferences } from "./src/types";

const E2E_FRONTEND_PORT = Number.parseInt(
  process.env.E2E_FRONTEND_PORT ?? "3000",
  10,
);
if (
  !Number.isInteger(E2E_FRONTEND_PORT) ||
  E2E_FRONTEND_PORT < 1 ||
  E2E_FRONTEND_PORT > 65535
) {
  throw new Error("E2E_FRONTEND_PORT must be a valid TCP port.");
}

const E2E_FRONTEND_URL = `http://127.0.0.1:${E2E_FRONTEND_PORT}`;
const USE_DEDICATED_VITE =
  Boolean(process.env.CI) || process.env.E2E_FRONTEND_PORT !== undefined;
const SEEDED_MODE = process.env.E2E_SEEDED_MODE === "true";
const PRODUCTION_FRONTEND = process.env.E2E_PRODUCTION_FRONTEND === "true";
const VITE_SERVER_OPTIONS =
  `--host 127.0.0.1 --port ${E2E_FRONTEND_PORT} --strictPort`;
const E2E_BACKEND_PORT = process.env.PYRIT_E2E_BACKEND_PORT ?? "18000";
const E2E_BACKEND_URL = `http://127.0.0.1:${E2E_BACKEND_PORT}`;

/**
 * Copies the seeded backend's configuration into a fresh folder that also holds
 * its saved targets, converters, scorers, and techniques, so a run never reads
 * or writes the ones saved on the machine running it.
 */
function createSeededBackendConfigFile(): string {
  const folder = mkdtempSync(path.join(tmpdir(), "pyrit-seeded-backend-"));
  // The runner stops the backend before it exits, so nothing still uses the folder by then.
  process.once("exit", () => rmSync(folder, { recursive: true, force: true }));
  const configFile = path.join(folder, "browser_config.yaml");
  const config = readFileSync(new URL("../tests/end_to_end/browser_config.yaml", import.meta.url), "utf-8");
  const recipesSource = JSON.stringify(path.join(folder, "registry_recipes"));
  writeFileSync(configFile, `${config}\nregistry_recipes_source: ${recipesSource}\n`);
  return configFile;
}

// Workers load this file too, but only the main process starts the backend.
const SEEDED_BACKEND_CONFIG_FILE =
  SEEDED_MODE && process.env.TEST_WORKER_INDEX === undefined
    ? createSeededBackendConfigFile()
    : "";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: ["**/analytics.spec.ts", "**/paired-outcomes.spec.ts", "**/scenario-hierarchy.spec.ts", "**/scenario-datasets.spec.ts", "**/scenario-techniques.spec.ts"],
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [
    process.env.CI ? ["blob"] : ["html", { open: "never" }],
    ["list"],
    ["./e2e/noSkippedTestsReporter.ts"],
  ],
  timeout: 30000,

  use: {
    baseURL: E2E_FRONTEND_URL,
    trace: process.env.CI ? "retain-on-failure" : "on-first-retry",
    screenshot: "only-on-failure",
    // Pre-set localStorage so the onboarding tour doesn't auto-start and
    // block UI interactions in E2E tests.
    storageState: {
      cookies: [],
      origins: [
        {
          origin: E2E_FRONTEND_URL,
          localStorage: [
            { name: "pyrit-tour-completed", value: "true" },
          ],
        },
      ],
    },
  },

  projects: [
    {
      name: "mock",
      use: { ...devices["Desktop Chrome"] },
      grepInvert: /@seeded|@live/,
    },
    {
      name: "seeded",
      use: {
        ...devices["Desktop Chrome"],
        storageState: {
          cookies: [],
          origins: [{
            origin: E2E_FRONTEND_URL,
            localStorage: [
              { name: "pyrit-tour-completed", value: "true" },
              {
                name: "pyrit.userPreferences.v1.local",
                value: JSON.stringify({
                  targets: { adversarial: null },
                  hiddenTargetRegistryNames: [],
                  labels: {},
                  theme: "system",
                  chatMarkdown: false,
                  operation: null,
                } satisfies UserPreferences),
              },
            ],
          }],
        },
      },
      grep: /@seeded/,
      fullyParallel: false,
      workers: 1,
    },
    {
      name: "live",
      use: { ...devices["Desktop Chrome"] },
      grep: /@live/,
      fullyParallel: false,
      workers: 1,
    },
    // Firefox can be enabled by installing: npx playwright install firefox
    // {
    //   name: "firefox",
    //   use: { ...devices["Desktop Firefox"] },
    // },
  ],

  /* Automatically start servers before running tests */
  webServer: SEEDED_MODE
    ? [
        {
          command:
            `cd .. && uv run --no-sync python -m pyrit.backend.pyrit_backend ` +
            `--auth-mode local --host 127.0.0.1 --port ${E2E_BACKEND_PORT} --log-level warning ` +
            `--config-file "${SEEDED_BACKEND_CONFIG_FILE}"`,
          env: { PYRIT_DEV_MODE: "true", PYRIT_E2E_LISTED_KEY: "e2e-listed-key" },
          url: `${E2E_BACKEND_URL}/api/health`,
          reuseExistingServer: false,
          timeout: 120_000,
        },
        {
          // Seeded specs reload the app often, and a production bundle loads
          // about 10x faster than Vite's unbundled dev graph. Mock specs need
          // the dev server because some import /src modules in the browser.
          command: PRODUCTION_FRONTEND
            ? `npx vite build && npx vite preview ${VITE_SERVER_OPTIONS}`
            : `npx vite ${VITE_SERVER_OPTIONS}`,
          env: { PYRIT_BACKEND_URL: E2E_BACKEND_URL },
          // Use 127.0.0.1 to avoid Node.js 17+ resolving localhost to IPv6 ::1
          url: E2E_FRONTEND_URL,
          reuseExistingServer: false,
          timeout: 120_000,
        },
      ]
    : {
        // Mock CI needs only Vite. Local live runs use dev.py.
        command: USE_DEDICATED_VITE
          ? `npx vite ${VITE_SERVER_OPTIONS}`
          : "python dev.py",
        url: E2E_FRONTEND_URL,
        reuseExistingServer: !USE_DEDICATED_VITE,
        timeout: 120_000,
      },
});

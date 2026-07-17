import { defineConfig, devices } from "@playwright/test";

const CI_SEEDED_MODE =
  !!process.env.CI && process.env.E2E_SEEDED_MODE === "true";
const E2E_BACKEND_PORT = process.env.PYRIT_E2E_BACKEND_PORT ?? "18000";
const E2E_BACKEND_URL = `http://127.0.0.1:${E2E_BACKEND_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [
    ["html", { open: "never" }],
    ["list"],
    ["./e2e/noSkippedTestsReporter.ts"],
  ],
  timeout: 30000,

  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // Pre-set localStorage so the onboarding tour doesn't auto-start and
    // block UI interactions in E2E tests.
    storageState: {
      cookies: [],
      origins: [
        {
          origin: "http://localhost:3000",
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
      use: { ...devices["Desktop Chrome"] },
      grep: /@seeded/,
    },
    {
      name: "live",
      use: { ...devices["Desktop Chrome"] },
      grep: /@live/,
    },
    // Firefox can be enabled by installing: npx playwright install firefox
    // {
    //   name: "firefox",
    //   use: { ...devices["Desktop Firefox"] },
    // },
  ],

  /* Automatically start servers before running tests */
  webServer: CI_SEEDED_MODE
    ? [
        {
          command:
            `cd .. && uv run python -m pyrit.backend.pyrit_backend ` +
            `--host 127.0.0.1 --port ${E2E_BACKEND_PORT} --log-level warning ` +
            "--config-file tests/end_to_end/test_config.yaml",
          env: { PYRIT_DEV_MODE: "true" },
          url: `${E2E_BACKEND_URL}/api/health`,
          reuseExistingServer: false,
          timeout: 120_000,
        },
        {
          command: "npx vite --host 127.0.0.1 --port 3000 --strictPort",
          env: { PYRIT_BACKEND_URL: E2E_BACKEND_URL },
          // Use 127.0.0.1 to avoid Node.js 17+ resolving localhost to IPv6 ::1
          url: "http://127.0.0.1:3000",
          reuseExistingServer: false,
          timeout: 120_000,
        },
      ]
    : {
        // Mock CI needs only Vite. Local seeded/live runs use dev.py.
        command: process.env.CI
          ? "npx vite --port 3000"
          : "python dev.py",
        url: "http://127.0.0.1:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});

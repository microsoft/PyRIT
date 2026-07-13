import { defineConfig, devices } from "@playwright/test";

const frontendPort = Number.parseInt(process.env.E2E_FRONTEND_PORT ?? "3000", 10);
if (!Number.isInteger(frontendPort) || frontendPort < 1 || frontendPort > 65535) {
  throw new Error("E2E_FRONTEND_PORT must be a valid TCP port.");
}

const frontendOrigin = `http://localhost:${frontendPort}`;
const useDedicatedVite = Boolean(process.env.CI) || process.env.E2E_FRONTEND_PORT !== undefined;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["html", { open: "never" }], ["list"]],
  timeout: 30000,

  use: {
    baseURL: frontendOrigin,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    // Pre-set localStorage so the onboarding tour doesn't auto-start and
    // block UI interactions in E2E tests.
    storageState: {
      cookies: [],
      origins: [
        {
          origin: frontendOrigin,
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
  webServer: {
    // CI runs only the mock project (no backend needed) — start Vite directly.
    // Locally, dev.py starts both backend + frontend for seeded/live tests.
    command: useDedicatedVite
      ? `npx vite --host 127.0.0.1 --port ${frontendPort} --strictPort`
      : "python dev.py",
    // Use 127.0.0.1 to avoid Node.js 17+ resolving localhost to IPv6 ::1
    url: `http://127.0.0.1:${frontendPort}`,
    reuseExistingServer: !useDedicatedVite,
    // CI needs extra time for uv sync + backend startup
    timeout: 120_000,
  },
});

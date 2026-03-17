import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["html", { open: "never" }], ["list"]],
  timeout: 30000,

  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
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
  webServer: {
    // CI runs only the mock project (no backend needed) — start Vite directly.
    // Locally, dev.py starts both backend + frontend for seeded/live tests.
    command: process.env.CI
      ? "npx vite --port 3000"
      : "python dev.py",
    // Use 127.0.0.1 to avoid Node.js 17+ resolving localhost to IPv6 ::1
    url: "http://127.0.0.1:3000",
    reuseExistingServer: !process.env.CI,
    // CI needs extra time for uv sync + backend startup
    timeout: 120_000,
  },
});

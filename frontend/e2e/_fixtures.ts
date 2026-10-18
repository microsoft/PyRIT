import { test as base } from "@playwright/test";
import { mockVersion } from "./_compatibility";

export * from "@playwright/test";

export const test = base.extend<{ mockStartup: void }>({
  mockStartup: [async ({ context }, use, testInfo) => {
    if (testInfo.project.name === "mock") {
      await context.route(/\/api\/auth\/config(?:\?|$)/, async (route) => {
        await route.fulfill({ json: { enabled: false, tenantId: "", clientId: "", scopes: [] } });
      });
      await context.route(/\/api\/health(?:\?|$)/, async (route) => {
        await route.fulfill({ json: { status: "healthy", service: "pyrit-backend" } });
      });
      await context.route(/\/api\/version(?:\?|$)/, async (route) => {
        await route.fulfill({ json: mockVersion() });
      });
    }
    await use();
  }, { auto: true }],
});

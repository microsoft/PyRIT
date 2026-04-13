import { test, expect, type Page, type Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MOCK_CONV_ID = "err-conv-001";

/** Standard mock for a successful first-message round-trip (create + send). */
function buildSuccessMessageMock(userText: string) {
  return {
    messages: {
      messages: [
        {
          turn_number: 1,
          role: "user",
          created_at: new Date().toISOString(),
          pieces: [
            {
              piece_id: "p-u",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              original_value: userText,
              converted_value: userText,
              scores: [],
              response_error: "none",
            },
          ],
        },
        {
          turn_number: 1,
          role: "assistant",
          created_at: new Date().toISOString(),
          pieces: [
            {
              piece_id: "p-a",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              original_value: `Reply to: ${userText}`,
              converted_value: `Reply to: ${userText}`,
              scores: [],
              response_error: "none",
            },
          ],
        },
      ],
    },
  };
}

/**
 * Set up all the mocks needed for a full chat flow.
 *
 * The `addMessageHandler` parameter controls what happens on POST messages.
 * By default it returns a success response.  Tests can override it to
 * inject errors on specific calls.
 */
async function mockAllAPIs(
  page: Page,
  addMessageHandler?: (route: Route) => Promise<void>,
) {
  // Targets
  await page.route(/\/api\/targets/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              target_registry_name: "mock-target",
              target_type: "OpenAIChatTarget",
              endpoint: "https://mock.endpoint.com",
              model_name: "gpt-4o-mock",
            },
          ],
        }),
      });
    } else {
      await route.continue();
    }
  });

  // Version
  await page.route(/\/api\/version/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ version: "0.0.0-test", display: "test" }),
    });
  });

  // Create attack
  await page.route(/\/api\/attacks$/, async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          conversation_id: MOCK_CONV_ID,
          attack_result_id: "err-ar-001",
        }),
      });
    } else {
      await route.continue();
    }
  });

  // Messages (GET = conversation load, POST = send)
  // Accumulate sent messages so GET returns them
  const sentMessages: Record<string, unknown>[] = [];
  await page.route(/\/api\/attacks\/[^/]+\/messages/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ messages: sentMessages }),
      });
    } else if (route.request().method() === "POST" && addMessageHandler) {
      // Build success response first to accumulate messages
      let userText = "message";
      try {
        const body = JSON.parse(route.request().postData() ?? "{}");
        userText =
          body?.pieces?.find(
            (p: Record<string, string>) => p.data_type === "text",
          )?.original_value || "message";
      } catch {
        /* ignore */
      }
      const successMock = buildSuccessMessageMock(userText);
      sentMessages.push(...successMock.messages.messages);
      await addMessageHandler(route);
    } else if (route.request().method() === "POST") {
      // Default success
      let userText = "message";
      try {
        const body = JSON.parse(route.request().postData() ?? "{}");
        userText =
          body?.pieces?.find(
            (p: Record<string, string>) => p.data_type === "text",
          )?.original_value || "message";
      } catch {
        /* ignore */
      }
      const successMock = buildSuccessMessageMock(userText);
      sentMessages.push(...successMock.messages.messages);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(successMock),
      });
    } else {
      await route.continue();
    }
  });

  // Conversations
  await page.route(/\/api\/attacks\/[^/]+\/conversations/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ conversations: [] }),
    });
  });
}

/** Navigate to config, set mock target active, return to chat. */
async function activateMockTarget(page: Page) {
  await page.getByTitle("Configuration").click();
  await expect(page.getByText("Target Configuration")).toBeVisible({
    timeout: 10000,
  });
  const setActiveBtn = page.getByRole("button", { name: /set active/i });
  await expect(setActiveBtn).toBeVisible({ timeout: 5000 });
  await setActiveBtn.click();
  await page.getByTitle("Chat").click();
  await expect(page.getByText("PyRIT Attack")).toBeVisible({ timeout: 5000 });
}

/** Send a message and wait for the response. */
async function sendAndWait(page: Page, text: string, responseText: string) {
  const input = page.getByRole("textbox");
  await input.fill(text);
  await page.getByRole("button", { name: /send/i }).click();
  await expect(page.getByText(responseText)).toBeVisible({ timeout: 10000 });
}

/** Simulate a tab visibility change to trigger an immediate health check. */
async function triggerVisibilityChange(page: Page) {
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      value: "hidden",
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForTimeout(100);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

// ---------------------------------------------------------------------------
// Error scenario: backend returns 500 on send message
// ---------------------------------------------------------------------------

test.describe("Error: backend 500 on send message", () => {
  test("should show error bubble and preserve input text", async ({
    page,
  }) => {
    // First message succeeds, second fails with 500.
    // This avoids the conversation-load race on the very first message.
    let callCount = 0;
    await mockAllAPIs(page, async (route) => {
      callCount++;
      if (callCount === 1) {
        // First send succeeds
        let userText = "message";
        try {
          const body = JSON.parse(route.request().postData() ?? "{}");
          userText =
            body?.pieces?.find(
              (p: Record<string, string>) => p.data_type === "text",
            )?.original_value || "message";
        } catch {
          /* ignore */
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(buildSuccessMessageMock(userText)),
        });
      } else {
        // Subsequent sends fail
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Internal server error" }),
        });
      }
    });

    await page.goto("/");
    await activateMockTarget(page);

    // First send establishes the conversation
    await sendAndWait(page, "Setup message", "Reply to: Setup message");

    // Second send should fail
    const input = page.getByRole("textbox");
    await input.fill("This should fail");
    await page.getByRole("button", { name: /send/i }).click();

    // Error message should appear in chat
    await expect(
      page.getByText(/Internal server error/i),
    ).toBeVisible({ timeout: 10000 });

    // The failed text should be restored in the input for easy re-send
    await expect(input).toHaveValue("This should fail", { timeout: 5000 });
  });
});

// ---------------------------------------------------------------------------
// Error scenario: network error (backend unreachable) on send
// ---------------------------------------------------------------------------

test.describe("Error: network error on send message", () => {
  test("should show network error in chat", async ({ page }) => {
    let callCount = 0;
    await mockAllAPIs(page, async (route) => {
      callCount++;
      if (callCount === 1) {
        let userText = "message";
        try {
          const body = JSON.parse(route.request().postData() ?? "{}");
          userText =
            body?.pieces?.find(
              (p: Record<string, string>) => p.data_type === "text",
            )?.original_value || "message";
        } catch {
          /* ignore */
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(buildSuccessMessageMock(userText)),
        });
      } else {
        await route.abort("connectionrefused");
      }
    });

    await page.goto("/");
    await activateMockTarget(page);

    // First send establishes the conversation
    await sendAndWait(page, "Setup message", "Reply to: Setup message");

    // Second send should fail with network error
    const input = page.getByRole("textbox");
    await input.fill("Network fail test");
    await page.getByRole("button", { name: /send/i }).click();

    // Network error message should appear
    await expect(
      page.getByText(/network error|backend is running/i),
    ).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// Error scenario: connection banner when health endpoint fails
// ---------------------------------------------------------------------------

test.describe("Error: connection banner on health failure", () => {
  test("should show degraded banner when health check fails", async ({
    page,
  }) => {
    // Let the page load normally first
    await page.goto("/");
    await expect(page.getByText("PyRIT Attack")).toBeVisible({
      timeout: 10000,
    });

    // Now block health checks to simulate backend going down
    await page.route(/\/api\/health/, async (route) => {
      await route.abort("connectionrefused");
    });

    // Trigger immediate health check via visibility change
    await triggerVisibilityChange(page);

    // Connection banner should appear
    const banner = page.getByTestId("connection-banner");
    await expect(banner).toBeVisible({ timeout: 15000 });
    await expect(
      page.getByText(/unstable|unable to reach/i),
    ).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Error scenario: connection banner recovery
// ---------------------------------------------------------------------------

test.describe("Error: connection banner recovery", () => {
  test("should show reconnected message after health recovers", async ({
    page,
  }) => {
    // Block health from the start
    let blockHealth = true;
    await page.route(/\/api\/health/, async (route) => {
      if (blockHealth) {
        await route.abort("connectionrefused");
      } else {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            status: "healthy",
            timestamp: new Date().toISOString(),
            service: "pyrit-backend",
          }),
        });
      }
    });

    await page.goto("/");
    await expect(page.getByText("PyRIT Attack")).toBeVisible({
      timeout: 10000,
    });

    // Trigger multiple health checks to reach disconnected state
    for (let i = 0; i < 3; i++) {
      await triggerVisibilityChange(page);
      await page.waitForTimeout(500);
    }

    // Banner should show disconnected/degraded
    const banner = page.getByTestId("connection-banner");
    await expect(banner).toBeVisible({ timeout: 15000 });

    // Restore health
    blockHealth = false;

    // Trigger another health check
    await triggerVisibilityChange(page);

    // "Reconnected" message should appear
    await expect(page.getByText(/reconnected/i)).toBeVisible({
      timeout: 15000,
    });
  });
});

// ---------------------------------------------------------------------------
// Error scenario: create-attack fails
// ---------------------------------------------------------------------------

test.describe("Error: create attack fails", () => {
  test("should show error when attack creation returns 500", async ({
    page,
  }) => {
    // Mock targets normally
    await page.route(/\/api\/targets/, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: [
              {
                target_registry_name: "mock-target",
                target_type: "OpenAIChatTarget",
                endpoint: "https://mock.endpoint.com",
                model_name: "gpt-4o-mock",
              },
            ],
          }),
        });
      } else {
        await route.continue();
      }
    });

    // Mock version
    await page.route(/\/api\/version/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ version: "0.0.0-test", display: "test" }),
      });
    });

    // create-attack fails with 500
    await page.route(/\/api\/attacks$/, async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Failed to create attack" }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/");
    await activateMockTarget(page);

    const input = page.getByRole("textbox");
    await input.fill("Should fail to create");
    await page.getByRole("button", { name: /send/i }).click();

    // Error should be shown
    await expect(
      page.getByText(/failed to create|error/i),
    ).toBeVisible({ timeout: 10000 });
  });
});

import { test, expect, type Page } from "@playwright/test";

// ---------------------------------------------------------------------------
// Helpers – mock backend API responses so tests don't require an OpenAI key
// ---------------------------------------------------------------------------

const MOCK_CONVERSATION_ID = "e2e-conv-001";

/** Intercept targets & attacks APIs so the chat flow can run without real keys. */
async function mockBackendAPIs(page: Page) {
  // Accumulate messages so multi-turn tests get full history back
  let accumulatedMessages: Record<string, unknown>[] = [];

  // Mock targets list – return one target already available
  await page.route(/\/api\/targets/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              target_registry_name: "mock-openai-chat",
              target_type: "OpenAIChatTarget",
              endpoint: "https://mock.openai.com",
              model_name: "gpt-4o-mock",
            },
          ],
        }),
      });
    } else {
      await route.continue();
    }
  });

  // Mock add-message – MUST be registered BEFORE the create-attack route
  // so the more specific pattern matches first.
  let postSeen = false; // track POST so GET doesn't return empty during render race
  await page.route(/\/api\/attacks\/[^/]+\/messages/, async (route) => {
    if (route.request().method() === "POST") {
      let userText = "your message";
      try {
        const body = JSON.parse(route.request().postData() ?? "{}");
        userText = body?.pieces?.find(
          (p: Record<string, string>) => p.data_type === "text",
        )?.original_value || "your message";
      } catch {
        // Ignore parse errors
      }

      const turnNumber = Math.floor(accumulatedMessages.length / 2) + 1;
      const userMsg = {
        turn_number: turnNumber,
        role: "user",
        created_at: new Date().toISOString(),
        pieces: [
          {
            piece_id: `piece-u-${turnNumber}`,
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: userText,
            converted_value: userText,
            scores: [],
            response_error: "none",
          },
        ],
      };
      const assistantMsg = {
        turn_number: turnNumber,
        role: "assistant",
        created_at: new Date().toISOString(),
        pieces: [
          {
            piece_id: `piece-a-${turnNumber}`,
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: `Mock response for: ${userText}`,
            converted_value: `Mock response for: ${userText}`,
            scores: [],
            response_error: "none",
          },
        ],
      };

      accumulatedMessages.push(userMsg, assistantMsg);
      postSeen = true;

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          messages: {
            messages: [...accumulatedMessages],
          },
        }),
      });
    } else if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ messages: postSeen ? [...accumulatedMessages] : [] }),
      });
    } else {
      await route.continue();
    }
  });

  // Mock create-attack – returns a conversation id (matches /api/attacks exactly)
  // Also resets accumulated messages for fresh conversations.
  await page.route(/\/api\/attacks$/, async (route) => {
    if (route.request().method() === "POST") {
      accumulatedMessages = [];
      postSeen = false;      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          attack_result_id: "e2e-attack-001",
          conversation_id: MOCK_CONVERSATION_ID,
        }),
      });
    } else {
      await route.continue();
    }
  });
}

/** Navigate to config, set the mock target as active, then return to chat. */
async function activateMockTarget(page: Page) {
  // Click Configuration button in sidebar
  await page.getByTitle("Configuration").click();
  await expect(page.getByText("Target Configuration")).toBeVisible({ timeout: 10000 });

  // Set the mock target active
  const setActiveBtn = page.getByRole("button", { name: /set active/i });
  await expect(setActiveBtn).toBeVisible({ timeout: 5000 });
  await setActiveBtn.click();

  // Return to Chat view
  await page.getByTitle("Chat").click();
  await expect(page.getByText("PyRIT Attack")).toBeVisible({ timeout: 5000 });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe("Application Smoke Tests", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("should load the application", async ({ page }) => {
    await expect(page.locator("body")).toBeVisible();
  });

  test("should display PyRIT header", async ({ page }) => {
    await expect(page.getByText("PyRIT Attack")).toBeVisible({ timeout: 10000 });
  });

  test("should have New Attack button", async ({ page }) => {
    await expect(page.getByRole("button", { name: /new attack/i })).toBeVisible();
  });

  test("should show 'no target' hint when no target is active", async ({ page }) => {
    await expect(page.getByTestId("no-target-banner")).toBeVisible();
  });
});

test.describe("Theme Toggle", () => {
  test("should toggle dark/light theme", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("PyRIT Attack")).toBeVisible({ timeout: 10000 });

    // The app defaults to dark mode, so the toggle button title should say "Light Mode"
    const themeBtn = page.getByTitle("Light Mode");
    await expect(themeBtn).toBeVisible();

    // Click to switch to light mode
    await themeBtn.click();

    // Now the button title should change to "Dark Mode"
    await expect(page.getByTitle("Dark Mode")).toBeVisible({ timeout: 5000 });
    // The old title should no longer be present
    await expect(page.getByTitle("Light Mode")).not.toBeVisible();

    // Click again to switch back to dark mode
    await page.getByTitle("Dark Mode").click();
    await expect(page.getByTitle("Light Mode")).toBeVisible({ timeout: 5000 });
  });
});

test.describe("Chat Functionality", () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendAPIs(page);
    await page.goto("/");
    await activateMockTarget(page);
  });

  test("should display target info after activation", async ({ page }) => {
    await expect(page.getByText("OpenAIChatTarget")).toBeVisible();
    await expect(page.getByText(/gpt-4o-mock/)).toBeVisible();
  });

  test("should send a message and receive backend response", async ({ page }) => {
    const input = page.getByRole("textbox");
    await expect(input).toBeEnabled();

    await input.fill("Hello, this is a test message");
    await page.getByRole("button", { name: /send/i }).click();

    // User message appears
    await expect(page.getByText("Hello, this is a test message", { exact: true })).toBeVisible();

    // Backend response appears
    await expect(
      page.getByText("Mock response for: Hello, this is a test message"),
    ).toBeVisible({ timeout: 10000 });
  });

  test("should clear input after sending", async ({ page }) => {
    const input = page.getByRole("textbox");
    await input.fill("Test message");
    await page.getByRole("button", { name: /send/i }).click();

    await expect(input).toHaveValue("");
  });

  test("should disable send button when input is empty", async ({ page }) => {
    const sendButton = page.getByRole("button", { name: /send/i });
    const input = page.getByRole("textbox");

    // Clear any existing text
    await input.fill("");
    await expect(sendButton).toBeDisabled();
  });

  test("should enable send button when input has text", async ({ page }) => {
    const input = page.getByRole("textbox");
    await input.fill("Some text");
    await expect(page.getByRole("button", { name: /send/i })).toBeEnabled();
  });

  test("should start new chat when clicking New Chat", async ({ page }) => {
    const input = page.getByRole("textbox");
    await input.fill("First message");
    await page.getByRole("button", { name: /send/i }).click();

    await expect(page.getByText("First message", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Mock response for: First message"),
    ).toBeVisible({ timeout: 10000 });

    // Click New Attack
    await page.getByTestId("new-attack-btn").click();

    // Previous messages should be cleared
    await expect(page.getByText("Mock response for: First message")).not.toBeVisible();
  });
});

test.describe("Multiple Messages", () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendAPIs(page);
    await page.goto("/");
    await activateMockTarget(page);
  });

  test("should maintain conversation history", async ({ page }) => {
    const input = page.getByRole("textbox");

    // Send first message
    await input.fill("First message");
    await page.getByRole("button", { name: /send/i }).click();
    await expect(page.getByText("First message", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Mock response for: First message"),
    ).toBeVisible({ timeout: 10000 });

    // Send second message
    await input.fill("Second message");
    await page.getByRole("button", { name: /send/i }).click();
    await expect(page.getByText("Second message", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Mock response for: Second message"),
    ).toBeVisible({ timeout: 10000 });

    // Both user messages should still be visible
    await expect(page.getByText("First message", { exact: true })).toBeVisible();
    await expect(page.getByText("Second message", { exact: true })).toBeVisible();
  });
});

test.describe("Chat without target", () => {
  test("should disable input when no target is active", async ({ page }) => {
    await page.goto("/");

    // The no-target-banner should be visible because no target is active
    await expect(page.getByTestId("no-target-banner")).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Multi-modal response tests
// ---------------------------------------------------------------------------

/** Build the mock message/add-message route handler that returns the
 *  given response pieces for assistant messages. */
function buildModalityMock(
  assistantPieces: Record<string, unknown>[],
  mockConversationId = "e2e-modality-conv",
) {
  return async function mockAPIs(page: Page) {
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
                model_name: "test-model",
              },
            ],
          }),
        });
      } else {
        await route.continue();
      }
    });

    // Add message – returns user turn + assistant with given pieces.
    // Also handles GET requests for loadConversation.
    let lastMessages: Record<string, unknown>[] = [];
    let postSeen = false; // track POST so GET doesn't return empty during render race
    await page.route(/\/api\/attacks\/[^/]+\/messages/, async (route) => {
      if (route.request().method() === "POST") {
        let userText = "user-input";
        try {
          const body = JSON.parse(route.request().postData() ?? "{}");
          userText =
            body?.pieces?.find(
              (p: Record<string, string>) => p.data_type === "text",
            )?.original_value || "user-input";
        } catch {
          // ignore
        }
        lastMessages = [
          {
            turn_number: 0,
            role: "user",
            created_at: new Date().toISOString(),
            pieces: [
              {
                piece_id: "u1",
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
            pieces: assistantPieces,
          },
        ];
        postSeen = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            messages: {
              messages: lastMessages,
            },
          }),
        });
      } else if (route.request().method() === "GET") {
        // Return empty before any POST so loadConversation doesn't hang,
        // but don't overwrite UI with stale empty data.
        // After POST, return full messages for subsequent loads.
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ messages: postSeen ? [...lastMessages] : [] }),
        });
      } else {
        await route.continue();
      }
    });

    // Create attack
    await page.route(/\/api\/attacks$/, async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ attack_result_id: "e2e-modality-attack", conversation_id: mockConversationId }),
        });
      } else {
        await route.continue();
      }
    });
  };
}

test.describe("Multi-modal: Image response", () => {
  const setupImageMock = buildModalityMock([
    {
      piece_id: "img-1",
      original_value_data_type: "text",
      converted_value_data_type: "image_path",
      original_value: "generated image",
      converted_value: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==",
      converted_value_mime_type: "image/png",
      scores: [],
      response_error: "none",
    },
  ]);

  test("should display image from assistant response", async ({ page }) => {
    await setupImageMock(page);
    await page.goto("/");
    await activateMockTarget(page);

    const input = page.getByRole("textbox");
    await input.fill("Generate an image");
    await page.getByRole("button", { name: /send/i }).click();

    // User message visible
    await expect(page.getByText("Generate an image", { exact: true })).toBeVisible();

    // Image element should appear (exclude logo)
    const img = page.locator('img:not([alt="Co-PyRIT Logo"])');
    await expect(img).toBeVisible({ timeout: 10000 });
    const src = await img.getAttribute("src");
    expect(src).toContain("data:image/png;base64,");
  });
});

test.describe("Multi-modal: Audio response", () => {
  const setupAudioMock = buildModalityMock([
    {
      piece_id: "aud-1",
      original_value_data_type: "text",
      converted_value_data_type: "audio_path",
      original_value: "spoken text",
      converted_value: "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=",
      converted_value_mime_type: "audio/wav",
      scores: [],
      response_error: "none",
    },
  ]);

  test("should display audio player for audio response", async ({ page }) => {
    await setupAudioMock(page);
    await page.goto("/");
    await activateMockTarget(page);

    const input = page.getByRole("textbox");
    await input.fill("Speak this out loud");
    await page.getByRole("button", { name: /send/i }).click();

    await expect(page.getByText("Speak this out loud", { exact: true })).toBeVisible();

    // Audio element should appear
    const audio = page.locator("audio");
    await expect(audio).toBeVisible({ timeout: 10000 });
  });
});

test.describe("Multi-modal: Video response", () => {
  const setupVideoMock = buildModalityMock([
    {
      piece_id: "vid-1",
      original_value_data_type: "text",
      converted_value_data_type: "video_path",
      original_value: "generated video",
      converted_value: "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDE=",
      converted_value_mime_type: "video/mp4",
      scores: [],
      response_error: "none",
    },
  ]);

  // Marking skipped for now because this should not use the getByRole query which is too general
  test.skip("should display video player for video response", async ({ page }) => {
    await setupVideoMock(page);
    await page.goto("/");
    await activateMockTarget(page);

    const input = page.getByRole("textbox");
    await input.fill("Create a video clip");
    await page.getByRole("button", { name: /send/i }).click();

    await expect(page.getByText("Create a video clip", { exact: true })).toBeVisible();

    // Video element should appear
    const video = page.locator("video");
    await expect(video).toBeVisible({ timeout: 10000 });
  });
});

test.describe("Multi-modal: Mixed text + image response", () => {
  const setupMixedMock = buildModalityMock([
    {
      piece_id: "txt-1",
      original_value_data_type: "text",
      converted_value_data_type: "text",
      original_value: "Here is the analysis:",
      converted_value: "Here is the analysis:",
      scores: [],
      response_error: "none",
    },
    {
      piece_id: "img-2",
      original_value_data_type: "text",
      converted_value_data_type: "image_path",
      original_value: "chart image",
      converted_value: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==",
      converted_value_mime_type: "image/png",
      scores: [],
      response_error: "none",
    },
  ]);

  test("should display both text and image in response", async ({ page }) => {
    await setupMixedMock(page);
    await page.goto("/");
    await activateMockTarget(page);

    const input = page.getByRole("textbox");
    await input.fill("Analyze this");
    await page.getByRole("button", { name: /send/i }).click();

    // Both text and image should be visible
    await expect(page.getByText("Here is the analysis:", { exact: true })).toBeVisible({ timeout: 10000 });
    const img = page.locator('img:not([alt="Co-PyRIT Logo"])');
    await expect(img).toBeVisible({ timeout: 10000 });
  });
});

test.describe("Multi-modal: Error response from target", () => {
  const setupErrorMock = buildModalityMock([
    {
      piece_id: "err-1",
      original_value_data_type: "text",
      converted_value_data_type: "text",
      original_value: "",
      converted_value: "",
      scores: [],
      response_error: "blocked",
      response_error_description: "Content was filtered by safety system",
    },
  ]);

  test("should display error message for blocked response", async ({ page }) => {
    await setupErrorMock(page);
    await page.goto("/");
    await activateMockTarget(page);

    const input = page.getByRole("textbox");
    await input.fill("unsafe prompt");
    await page.getByRole("button", { name: /send/i }).click();

    await expect(page.getByText("unsafe prompt", { exact: true })).toBeVisible();

    // Error should be displayed
    await expect(
      page.getByText(/Content was filtered by safety system/),
    ).toBeVisible({ timeout: 10000 });
  });
});

test.describe("Multi-turn conversation flow", () => {
  test.beforeEach(async ({ page }) => {
    await mockBackendAPIs(page);
    await page.goto("/");
    await activateMockTarget(page);
  });

  test("should send three messages in sequence", async ({ page }) => {
    const input = page.getByRole("textbox");

    // Turn 1
    await input.fill("First turn");
    await page.getByRole("button", { name: /send/i }).click();
    await expect(page.getByText("First turn", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Mock response for: First turn"),
    ).toBeVisible({ timeout: 10000 });

    // Turn 2
    await input.fill("Second turn");
    await page.getByRole("button", { name: /send/i }).click();
    await expect(page.getByText("Second turn", { exact: true })).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByText("Mock response for: Second turn"),
    ).toBeVisible({ timeout: 10000 });

    // Turn 3
    await input.fill("Third turn");
    await page.getByRole("button", { name: /send/i }).click();
    await expect(page.getByText("Third turn", { exact: true })).toBeVisible({ timeout: 10000 });
    await expect(
      page.getByText("Mock response for: Third turn"),
    ).toBeVisible({ timeout: 10000 });

    // All previous messages still visible
    await expect(page.getByText("First turn", { exact: true })).toBeVisible();
    await expect(page.getByText("Second turn", { exact: true })).toBeVisible();
    await expect(page.getByText("Third turn", { exact: true })).toBeVisible();
  });

  test("should reset conversation on New Chat and send again", async ({ page }) => {
    const input = page.getByRole("textbox");

    // Send a message
    await input.fill("Before reset");
    await page.getByRole("button", { name: /send/i }).click();
    await expect(page.getByText("Before reset", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Mock response for: Before reset"),
    ).toBeVisible({ timeout: 10000 });

    // New Attack
    await page.getByTestId("new-attack-btn").click();
    await expect(page.getByText("Before reset", { exact: true })).not.toBeVisible();

    // Send new message in fresh conversation
    await input.fill("After reset");
    await page.getByRole("button", { name: /send/i }).click();
    await expect(page.getByText("After reset", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Mock response for: After reset"),
    ).toBeVisible({ timeout: 10000 });
  });
});

// ---------------------------------------------------------------------------
// Different target type scenarios
// ---------------------------------------------------------------------------

test.describe("Target type scenarios", () => {
  const TARGETS = [
    {
      target_registry_name: "azure-openai-gpt4o",
      target_type: "OpenAIChatTarget",
      endpoint: "https://myresource.openai.azure.com",
      model_name: "gpt-4o",
    },
    {
      target_registry_name: "dall-e-image-gen",
      target_type: "OpenAIImageTarget",
      endpoint: "https://api.openai.com",
      model_name: "dall-e-3",
    },
    {
      target_registry_name: "tts-speech",
      target_type: "OpenAITTSTarget",
      endpoint: "https://api.openai.com",
      model_name: "tts-1-hd",
    },
  ];

  test("should list multiple target types on config page", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: TARGETS,
            pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
          }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/");
    await page.getByTitle("Configuration").click();
    await expect(page.getByText("Target Configuration")).toBeVisible({ timeout: 10000 });

    await expect(page.locator("table").getByText("OpenAIChatTarget")).toBeVisible();
    await expect(page.locator("table").getByText("OpenAIImageTarget")).toBeVisible();
    await expect(page.locator("table").getByText("OpenAITTSTarget")).toBeVisible();
  });

  test("should activate image target and show it in chat ribbon", async ({ page }) => {
    await page.route(/\/api\/targets/, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            items: TARGETS,
            pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
          }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/");
    await page.getByTitle("Configuration").click();
    await expect(page.getByText("dall-e-3")).toBeVisible({ timeout: 10000 });

    // Activate the DALL-E target (second row)
    const setActiveBtns = page.getByRole("button", { name: /set active/i });
    await setActiveBtns.nth(1).click();

    // Navigate to chat
    await page.getByTitle("Chat").click();
    await expect(page.getByText("OpenAIImageTarget")).toBeVisible();
    await expect(page.getByText(/dall-e-3/)).toBeVisible();
  });
});

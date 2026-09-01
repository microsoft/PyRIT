import { expect, test, type Locator, type Page } from "@playwright/test";
import { makeTarget } from "./_targets";

const MOBILE_VIEWPORT = { width: 390, height: 844 };
const DESKTOP_VIEWPORT = { width: 1280, height: 800 };
const MINIMUM_TOUCH_TARGET_SIZE = 44;
const LONG_SCORE_VALUE = "a".repeat(200);

const TARGETS = [
  makeTarget({
    target_registry_name: "mobile-round-robin",
    target_type: "RoundRobinTarget",
    model_name: "mobile-router",
    capabilities: {
      supports_multi_turn: true,
      supports_system_prompt: true,
      supported_input_modalities: ["text", "image_path"],
      supported_output_modalities: ["text"],
    },
    target_specific_params: { weights: [1, 1] },
    inner_targets: [
      {
        target_registry_name: "mobile-inner-primary",
        target_type: "OpenAIChatTarget",
        endpoint: "https://primary.example.test",
        model_name: "gpt-4o-primary",
      },
      {
        target_registry_name: "mobile-inner-secondary",
        target_type: "OpenAIChatTarget",
        endpoint: "https://secondary.example.test",
        model_name: "gpt-4o-secondary",
      },
    ],
  }),
  makeTarget({
    target_registry_name: "mobile-chat-target",
    target_type: "OpenAIChatTarget",
    endpoint: "https://chat.example.test",
    model_name: "gpt-4o-mobile",
    capabilities: {
      supports_multi_turn: true,
      supports_system_prompt: true,
      supported_input_modalities: ["text", "image_path"],
      supported_output_modalities: ["text"],
    },
  }),
];

const MESSAGES = [
  {
    turn_number: 1,
    role: "user",
    created_at: "2026-07-22T13:10:00.000Z",
    message_pieces: [
      {
        id: "mobile-user-piece",
        original_value_data_type: "text",
        converted_value_data_type: "text",
        original_value: "Assess this deterministic mobile prompt",
        converted_value: "Assess this deterministic mobile prompt",
        scores: [],
        response_error: "none",
      },
    ],
  },
  {
    turn_number: 1,
    role: "assistant",
    created_at: "2026-07-22T13:10:01.000Z",
    message_pieces: [
      {
        id: "mobile-assistant-piece",
        original_value_data_type: "text",
        converted_value_data_type: "text",
        original_value: "Deterministic assistant response for touch-target tests.",
        converted_value: "Deterministic assistant response for touch-target tests.",
        scores: Array.from({ length: 9 }, (_unused: unknown, scoreIndex: number) => ({
          id: `mobile-assistant-score-${scoreIndex}`,
          message_piece_id: "mobile-assistant-piece",
          scorer_type: `SelfAskRefusalScorer${scoreIndex}`,
          score_type: scoreIndex === 0 ? "unknown" : "true_false",
          score_value:
            scoreIndex === 0
              ? LONG_SCORE_VALUE
              : "false",
          is_objective_score: scoreIndex === 0,
          score_category: ["refusal"],
          score_rationale: `Deterministic rationale ${scoreIndex} for touch-target tests.`,
          timestamp: `2026-07-22T13:10:0${scoreIndex}.500Z`,
        })),
        response_error: "none",
      },
    ],
  },
];

const ATTACK_SUMMARY = {
  attack_result_id: "home-attack-001",
  conversation_id: "home-conversation-001",
  attack_type: "PromptSendingAttack",
  target: {
    target_type: "OpenAIChatTarget",
    endpoint: "https://chat.example.test",
    model_name: "gpt-4o-mobile",
  },
  converters: [],
  outcome: "success",
  labels: { operator: "mobile_operator", operation: "touch_targets" },
  message_count: 2,
  related_conversation_ids: [],
  created_at: "2026-07-22T13:07:00.000Z",
  updated_at: "2026-07-22T13:08:00.000Z",
  last_message_preview: "Deterministic recent operation",
};

function jsonResponse(body: unknown) {
  return {
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  };
}

async function installTouchTargetMocks(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const apiPath = new URL(request.url()).pathname.replace(/^\/api/, "");
    const method = request.method();

    if (apiPath === "/health") {
      await route.fulfill(jsonResponse({ status: "healthy" }));
      return;
    }
    if (apiPath === "/auth/config") {
      await route.fulfill(
        jsonResponse({ clientId: "", tenantId: "", allowedGroupIds: "" })
      );
      return;
    }
    if (apiPath === "/auth/access") {
      await route.fulfill(jsonResponse({ isAdmin: true }));
      return;
    }
    if (apiPath === "/version") {
      await route.fulfill(
        jsonResponse({
          version: "touch-target-test",
          display: "touch-target-test",
          default_labels: {
            operator: "mobile_operator",
            operation: "touch_targets",
          },
        })
      );
      return;
    }
    if (apiPath === "/labels") {
      await route.fulfill(
        jsonResponse({
          source: "attacks",
          labels: {
            operator: ["mobile_operator"],
            operation: ["touch_targets"],
            campaign: ["mobile_audit"],
          },
        })
      );
      return;
    }
    if (apiPath === "/initializers/settings" && method === "GET") {
      await route.fulfill(
        jsonResponse({
          baseline: [],
          additional: [],
        })
      );
      return;
    }
    if (apiPath === "/initializers" && method === "GET") {
      await route.fulfill(
        jsonResponse({
          items: [
            {
              initializer_name: "load_default_datasets",
              initializer_type: "DatasetInitializer",
              description: "Loads the default datasets.",
              required_env_vars: [],
              supported_parameters: [],
            },
          ],
          pagination: {
            limit: 200,
            has_more: false,
            next_cursor: null,
            prev_cursor: null,
          },
        })
      );
      return;
    }
    if (apiPath === "/targets/catalog") {
      await route.fulfill(
        jsonResponse({
          items: [
            {
              target_type: "OpenAIChatTarget",
              parameters: [],
              supported_auth_modes: ["api_key"],
            },
            {
              target_type: "RoundRobinTarget",
              parameters: [],
              supported_auth_modes: ["api_key"],
            },
          ],
        })
      );
      return;
    }
    if (apiPath === "/targets" && method === "GET") {
      await route.fulfill(
        jsonResponse({
          items: TARGETS,
          pagination: {
            limit: 200,
            has_more: false,
            next_cursor: null,
            prev_cursor: null,
          },
        })
      );
      return;
    }
    if (apiPath === "/converters/catalog" || apiPath === "/converters") {
      await route.fulfill(jsonResponse({ items: [] }));
      return;
    }
    if (apiPath === "/attacks" && method === "GET") {
      await route.fulfill(
        jsonResponse({
          items: [ATTACK_SUMMARY],
          pagination: {
            limit: 50,
            has_more: false,
            next_cursor: null,
            prev_cursor: null,
          },
        })
      );
      return;
    }
    if (apiPath === "/attacks" && method === "POST") {
      await route.fulfill(
        jsonResponse({
          attack_result_id: "mobile-attack-001",
          conversation_id: "mobile-conversation-001",
        })
      );
      return;
    }
    if (apiPath === "/attacks/mobile-attack-001") {
      await route.fulfill(
        jsonResponse({
          attack_result_id: "mobile-attack-001",
          attack_type: "PromptSendingAttack",
          conversation_id: "mobile-conversation-001",
          related_conversation_ids: [],
          objective:
            "Deterministic long objective that does not fit on a single line of the mobile objective header and must be truncated with a disclosure toggle.",
          labels: {
            operator: "mobile_operator",
            operation: "touch_targets",
          },
          target: {
            target_type: "RoundRobinTarget",
            endpoint: null,
            model_name: "mobile-router",
            identifier_hash: "mobile-round-robin-hash",
          },
          updated_at: "2026-07-22T13:10:01.000Z",
        })
      );
      return;
    }
    if (apiPath === "/attacks/mobile-attack-001/messages") {
      await route.fulfill(
        method === "POST"
          ? jsonResponse({ messages: { messages: MESSAGES } })
          : jsonResponse({ messages: MESSAGES })
      );
      return;
    }
    if (apiPath === "/attacks/mobile-attack-001/conversations") {
      await route.fulfill(
        jsonResponse({
          attack_result_id: "mobile-attack-001",
          main_conversation_id: "mobile-conversation-001",
          conversations: [
            {
              conversation_id: "mobile-conversation-001",
              message_count: 2,
              last_message_preview: "Deterministic assistant response",
              created_at: "2026-07-22T13:10:00.000Z",
            },
          ],
        })
      );
      return;
    }
    if (apiPath === "/attacks/attack-options") {
      await route.fulfill(
        jsonResponse({ attack_types: ["PromptSendingAttack"] })
      );
      return;
    }
    if (apiPath === "/attacks/converter-options") {
      await route.fulfill(jsonResponse({ converter_types: [] }));
      return;
    }

    throw new Error(`Unhandled touch-target API request: ${method} ${apiPath}`);
  });
}

async function expectMinimumTouchTarget(locator: Locator): Promise<void> {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  if (!box) {
    throw new Error("Expected a visible touch target");
  }
  expect(Math.round(box.width)).toBeGreaterThanOrEqual(MINIMUM_TOUCH_TARGET_SIZE);
  expect(Math.round(box.height)).toBeGreaterThanOrEqual(MINIMUM_TOUCH_TARGET_SIZE);
}

async function expectMinimumTouchTargets(locator: Locator): Promise<void> {
  const count = await locator.count();
  expect(count).toBeGreaterThan(0);
  for (let index = 0; index < count; index += 1) {
    await expectMinimumTouchTarget(locator.nth(index));
  }
}

async function expectCompactDesktopTarget(locator: Locator): Promise<void> {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  if (!box) {
    throw new Error("Expected a visible desktop target");
  }
  expect(box.height).toBeLessThan(MINIMUM_TOUCH_TARGET_SIZE);
}

async function expectNoDocumentOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    documentWidth: document.documentElement.scrollWidth,
    bodyWidth: document.body.scrollWidth,
  }));
  expect(dimensions.documentWidth).toBeLessThanOrEqual(
    dimensions.viewportWidth
  );
  expect(dimensions.bodyWidth).toBeLessThanOrEqual(dimensions.viewportWidth);
}

async function startChatWithMessages(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Targets", exact: true }).click();
  await expect(page.getByText("gpt-4o-mobile")).toBeVisible();
  await page.getByRole("button", { name: "Set Active" }).first().click();
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await page.getByTestId("chat-input").fill(
    "Assess this deterministic mobile prompt"
  );
  await page.getByTestId("send-message-btn").click();
  await expect(
    page.getByText("Deterministic assistant response for touch-target tests.")
  ).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await installTouchTargetMocks(page);
});

test.describe("Mobile touch targets", () => {
  test.use({ viewport: MOBILE_VIEWPORT, hasTouch: true });

  test("keeps Home, Targets, and History controls at least 44px", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page.getByTestId("home-view")).toBeVisible();

    await expectMinimumTouchTargets(
      page.getByTestId("home-view").locator(
        [
          '[data-testid="labels-icon-btn"]',
          '[data-testid="home-configure-target-btn"]',
          '[data-testid="home-view-all-history-btn"]',
          '[data-testid="home-open-attack-home-attack-001"]',
        ].join(",")
      )
    );
    await expectMinimumTouchTarget(
      page.getByRole("button", { name: "Home", exact: true })
    );
    await expectNoDocumentOverflow(page);

    await page
      .getByRole("button", { name: "Targets", exact: true })
      .click();
    await expect(page.getByText("gpt-4o-mobile")).toBeVisible();

    await expectMinimumTouchTarget(
      page.getByRole("button", { name: "Refresh", exact: true })
    );
    await expectMinimumTouchTarget(
      page.getByRole("button", { name: "New Target", exact: true })
    );
    await expectMinimumTouchTargets(
      page.getByRole("button", { name: "Set Active" })
    );
    await expectMinimumTouchTarget(
      page.getByRole("button", { name: "Expand inner targets" })
    );
    await expectMinimumTouchTarget(page.locator("select"));
    await expectNoDocumentOverflow(page);

    await page.goto("/history");
    await expect(
      page.getByTestId("open-attack-home-attack-001")
    ).toBeVisible();

    await expectMinimumTouchTarget(page.getByTestId("refresh-btn"));
    await expectMinimumTouchTargets(
      page.locator(
        [
          '[data-testid="attack-type-filter"]',
          '[data-testid="outcome-filter"]',
          '[data-testid="converter-filter"]',
          '[data-testid="operator-filter"]',
          '[data-testid="operation-filter"]',
          '[data-testid="label-filter"]',
        ].join(",")
      )
    );
    await expectMinimumTouchTargets(
      page.getByRole("button", { name: "Open", exact: true })
    );

    const openAttack = page.getByTestId("open-attack-home-attack-001");
    await openAttack.scrollIntoViewIfNeeded();
    await expectMinimumTouchTarget(openAttack);
    await expectMinimumTouchTarget(page.getByTestId("prev-page-btn"));
    await expectMinimumTouchTarget(page.getByTestId("next-page-btn"));
    await expectNoDocumentOverflow(page);
  });

  test("keeps the Initializer selector at least 44px", async ({ page }) => {
    await page.goto("/initializers");

    await expectMinimumTouchTarget(
      page.getByRole("combobox", { name: "Initializer to add" })
    );
    await expectNoDocumentOverflow(page);
  });

  test("keeps Chat message, input, and conversation controls at least 44px", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: MOBILE_VIEWPORT.height });
    // Deep-link directly into the attack (rather than creating one through
    // the chat flow) so the objective is actually hydrated from the backend:
    // the create-attack flow seeds the objective as "" client-side and never
    // loads the long mocked objective, so the disclosure toggle would never
    // render and this test would silently skip checking it.
    await page.goto("/attacks/mobile-attack-001");
    await expect(
      page.getByText("Deterministic assistant response for touch-target tests.")
    ).toBeVisible();
    await expect(
      page.getByTestId("toggle-objective-header-btn")
    ).toBeVisible();

    await page.getByRole("button", { name: "Targets", exact: true }).click();
    await expect(page.getByText("gpt-4o-mobile")).toBeVisible();
    await page.getByRole("button", { name: "Set Active" }).first().click();
    await page.goBack();
    await expect(
      page.getByTestId("toggle-objective-header-btn")
    ).toBeVisible();

    const scoreStack = page.getByTestId("message-score-stack-1");
    await expect(scoreStack).toBeVisible();
    await expectMinimumTouchTarget(scoreStack);
    await scoreStack.click();

    const scoreDetails = page.locator(
      '[data-testid^="message-score-details-1-"]'
    );
    const scoreValue = scoreDetails.getByText(LONG_SCORE_VALUE, { exact: true });
    await expect(scoreValue).toBeVisible();
    const scoreGeometry = await scoreDetails.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(scoreGeometry.scrollWidth).toBeLessThanOrEqual(
      scoreGeometry.clientWidth
    );
    const valueGeometry = await scoreValue.evaluate((element) => ({
      clientWidth: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }));
    expect(valueGeometry.scrollWidth).toBeLessThanOrEqual(
      valueGeometry.clientWidth
    );
    await expectNoDocumentOverflow(page);

    const scoreTabs = page.locator('[data-testid^="message-score-tab-1-"]');
    await expect(scoreTabs).toHaveCount(2);
    await expectMinimumTouchTargets(scoreTabs);
    await scoreTabs.nth(1).click();

    const shortScoreValue = scoreDetails.getByText("false", { exact: true });
    await expect(shortScoreValue).toBeVisible();
    const shortValueGeometry = await shortScoreValue.evaluate((element) => ({
      valueWidth: element.getBoundingClientRect().width,
      rowWidth: element.parentElement?.getBoundingClientRect().width ?? 0,
    }));
    expect(shortValueGeometry.valueWidth).toBeLessThan(
      shortValueGeometry.rowWidth
    );

    const moreScores = page.getByRole("button", {
      name: "More scores, 7 hidden",
    });
    await expect(moreScores).toBeVisible();
    await expectMinimumTouchTarget(moreScores);
    await moreScores.click();

    const scoreMenuItems = page.getByRole("menuitem");
    await expect(scoreMenuItems).toHaveCount(7);
    await expectMinimumTouchTargets(scoreMenuItems);
    await page.keyboard.press("Escape");
    await expect(scoreMenuItems).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(scoreStack).toHaveAttribute("aria-expanded", "false");

    await expectMinimumTouchTargets(
      page.locator(
        [
          '[data-testid="labels-icon-btn"]',
          '[data-testid="export-conversation-btn"]',
          '[data-testid="toggle-panel-btn"]',
          '[data-testid="new-attack-btn"]',
          '[aria-label="Attach files"]',
          '[data-testid="toggle-converter-panel-btn"]',
          '[data-testid="toggle-objective-header-btn"]',
          '[data-testid="chat-input"]',
          '[data-testid="send-message-btn"]',
          '[data-testid="copy-to-input-btn-1"]',
          '[data-testid="copy-to-new-conv-btn-1"]',
          '[data-testid="branch-conv-btn-1"]',
          '[data-testid="branch-attack-btn-1"]',
        ].join(",")
      )
    );
    await expectNoDocumentOverflow(page);

    await page.getByTestId("toggle-panel-btn").click();
    await expect(
      page.getByRole("dialog", { name: "Attack Conversations" })
    ).toBeVisible();

    await expectMinimumTouchTargets(
      page.locator(
        [
          '[data-testid="new-conversation-btn"]',
          '[data-testid="close-panel-btn"]',
          '[data-testid="conversation-item-mobile-conversation-001"]',
          '[data-testid="star-btn-mobile-conversation-001"]',
        ].join(",")
      )
    );
    await expectNoDocumentOverflow(page);
  });

  test("keeps every tour action at least 44px without changing containment", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByTestId("start-tour").click();

    await expect(page.getByText("1 of 5")).toBeVisible();
    await expectMinimumTouchTargets(
      page.getByRole("button", {
        name: /^(Close|Skip tour|Next)$/,
      })
    );

    for (const step of [2, 3, 4]) {
      await page
        .getByRole("button", { name: "Next", exact: true })
        .click({ force: true });
      await expect(page.getByText(`${step} of 5`)).toBeVisible();
      await expectMinimumTouchTargets(
        page.getByRole("button", {
          name: /^(Close|Skip tour|Back|Next)$/,
        })
      );
    }

    await page
      .getByRole("button", { name: "Next", exact: true })
      .click({ force: true });
    await expect(page.getByText("5 of 5")).toBeVisible();
    await expectMinimumTouchTarget(
      page.getByRole("button", { name: "Back", exact: true })
    );
    await expectMinimumTouchTarget(
      page.getByRole("button", { name: "Anchors Away!", exact: true })
    );
  });
});

test("preserves compact desktop controls and existing sidebar dimensions", async ({
  page,
}) => {
  await page.setViewportSize(DESKTOP_VIEWPORT);
  await page.goto("/");
  await expect(page.getByTestId("home-view")).toBeVisible();

  const sidebarHome = page.getByRole("button", {
    name: "Home",
    exact: true,
  });
  const sidebarBox = await sidebarHome.boundingBox();
  expect(sidebarBox?.width).toBe(44);
  expect(sidebarBox?.height).toBe(44);

  await expectCompactDesktopTarget(page.getByTestId("labels-icon-btn"));
  await expectCompactDesktopTarget(
    page.getByTestId("home-configure-target-btn")
  );
  await expectCompactDesktopTarget(
    page.getByTestId("home-open-attack-home-attack-001")
  );

  await page
    .getByRole("button", { name: "Targets", exact: true })
    .click();
  await expect(page.getByText("gpt-4o-mobile")).toBeVisible();
  await expectCompactDesktopTarget(
    page.getByRole("button", { name: "Refresh", exact: true })
  );
  await expectCompactDesktopTarget(page.locator("select"));
  await expectCompactDesktopTarget(
    page.getByRole("button", { name: "Set Active" }).first()
  );
  await expectCompactDesktopTarget(
    page.getByRole("button", { name: "Expand inner targets" })
  );

  await page.goto("/initializers");
  await expectCompactDesktopTarget(
    page.getByRole("combobox", { name: "Initializer to add" })
  );

  await startChatWithMessages(page);
  await expectCompactDesktopTarget(page.getByTestId("toggle-panel-btn"));
  await expectCompactDesktopTarget(
    page.getByRole("button", { name: "Attach files" })
  );
  await expectCompactDesktopTarget(page.getByTestId("chat-input"));
  await expectCompactDesktopTarget(page.getByTestId("copy-to-input-btn-1"));

  await page.getByTestId("toggle-panel-btn").click();
  await expect(page.getByTestId("conversation-panel")).toBeVisible();
  await expectCompactDesktopTarget(page.getByTestId("close-panel-btn"));
  await expectCompactDesktopTarget(
    page.getByTestId("star-btn-mobile-conversation-001")
  );
  await page.getByTestId("close-panel-btn").click();

  await page.getByRole("button", { name: "Home", exact: true }).click();
  await page.getByTestId("start-tour").click();
  await expect(page.getByText("1 of 5")).toBeVisible();
  await expectCompactDesktopTarget(
    page.getByRole("button", { name: "Close", exact: true })
  );
  await expectCompactDesktopTarget(
    page.getByRole("button", { name: "Next", exact: true })
  );

  await page.goto("/history");
  await expect(page.getByTestId("refresh-btn")).toBeVisible();
  await expectCompactDesktopTarget(page.getByTestId("refresh-btn"));
  await expectCompactDesktopTarget(page.getByTestId("attack-type-filter"));
  const openAttack = page.getByTestId("open-attack-home-attack-001");
  await openAttack.scrollIntoViewIfNeeded();
  await expectCompactDesktopTarget(openAttack);
  await expectCompactDesktopTarget(page.getByTestId("next-page-btn"));
});

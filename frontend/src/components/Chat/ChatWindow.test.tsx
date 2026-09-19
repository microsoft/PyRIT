import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { MemoryRouter, Route, Routes } from "react-router";
import ChatWindow from "./ChatWindow";
import { makeTarget } from "@/test-utils/targetFixtures";
import {
  BackendMessage,
  Message,
  MessageAttachment,
  PromptResponseError,
  TargetCapabilities,
  TargetInfo,
  TargetInstance,
  TargetResponseStatus,
} from "../../types";
import { attacksApi, convertersApi, scoresApi } from "../../services/api";
import * as messageMapper from "../../utils/messageMapper";

const buildCapabilities = (
  overrides: Partial<TargetCapabilities> = {}
): TargetCapabilities => ({
  supports_multi_turn: true,
  supports_multi_message_pieces: false,
  supports_json_schema: false,
  supports_json_output: false,
  supports_editable_history: false,
  supports_system_prompt: false,
  supported_input_modalities: [],
  supported_output_modalities: [],
  ...overrides,
});

// Fluent UI Combobox portal interactions are slow in JSDOM under full test load
jest.setTimeout(60000);

jest.mock("../../services/api", () => ({
  attacksApi: {
    createAttack: jest.fn(),
    updateAttack: jest.fn(),
    removeHumanScore: jest.fn(),
    addMessage: jest.fn(),
    getMessages: jest.fn(),
    getRelatedConversations: jest.fn(),
    getConversations: jest.fn(),
    createConversation: jest.fn(),
    changeMainConversation: jest.fn(),
  },
  scoresApi: {
    createManualScore: jest.fn(),
  },
  convertersApi: {
    listConverterCatalog: jest.fn(),
    listConverters: jest.fn(),
    getConverter: jest.fn(),
    createConverter: jest.fn(),
    previewConversion: jest.fn(),
  },
  labelsApi: {
    getLabels: jest.fn().mockImplementation(() => new Promise(() => {})),
  },
}));

jest.mock("../../utils/messageMapper", () => ({
  buildMessagePieces: jest.fn(),
  backendMessageToFrontend: jest.fn(),
  backendMessageToOriginalDraft: jest.fn(),
  backendMessagesToFrontend: jest.fn(),
  fileToBase64: jest.fn(),
}));

const mockedAttacksApi = attacksApi as jest.Mocked<typeof attacksApi>;
const mockedConvertersApi = convertersApi as jest.Mocked<typeof convertersApi>;
const mockedScoresApi = scoresApi as jest.Mocked<typeof scoresApi>;
const mockedMapper = messageMapper as jest.Mocked<typeof messageMapper>;
const actualMessageMapper = jest.requireActual<typeof import("../../utils/messageMapper")>(
  "../../utils/messageMapper"
);
const MARKDOWN_PREFERENCE_STORAGE_KEY = "pyrit.chatMarkdownMode";

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => (
  <FluentProvider theme={webLightTheme}>
    <MemoryRouter>{children}</MemoryRouter>
  </FluentProvider>
);

function mockMatchMedia(matchesNarrowScreen: boolean): void {
  (window.matchMedia as jest.Mock).mockImplementation((query: string) => ({
    matches: matchesNarrowScreen && query === "(max-width: 600px)",
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  }));
}

const mockTarget: TargetInstance = makeTarget({
  target_registry_name: "openai_chat_1",
  target_type: "OpenAIChatTarget",
  endpoint: "https://api.openai.com",
  model_name: "gpt-4",
});

// ---------------------------------------------------------------------------
// Helpers to build mock backend responses
// ---------------------------------------------------------------------------

function makeTextResponse(text: string) {
  return {
    messages: {
      target_response_status: {
        response_error: "none",
        request_turn_number: 0,
        response_turn_number: 1,
      },
      messages: [
        {
          turn_number: 1,
          role: "assistant",
          message_pieces: [
            {
              id: "p-resp",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              original_value: text,
              converted_value: text,
              scores: [],
              response_error: "none",
            },
          ],
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    },
  };
}

function makeImageResponse() {
  return {
    messages: {
      target_response_status: {
        response_error: "none",
        request_turn_number: 0,
        response_turn_number: 1,
      },
      messages: [
        {
          turn_number: 1,
          role: "assistant",
          message_pieces: [
            {
              id: "p-img",
              original_value_data_type: "text",
              converted_value_data_type: "image_path",
              original_value: "generated image",
              converted_value: "iVBORw0KGgo=",
              converted_value_mime_type: "image/png",
              scores: [],
              response_error: "none",
            },
          ],
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    },
  };
}

function makeAudioResponse() {
  return {
    messages: {
      target_response_status: {
        response_error: "none",
        request_turn_number: 0,
        response_turn_number: 1,
      },
      messages: [
        {
          turn_number: 1,
          role: "assistant",
          message_pieces: [
            {
              id: "p-aud",
              original_value_data_type: "text",
              converted_value_data_type: "audio_path",
              original_value: "spoken text",
              converted_value: "UklGRg==",
              converted_value_mime_type: "audio/wav",
              scores: [],
              response_error: "none",
            },
          ],
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    },
  };
}

function makeVideoResponse() {
  return {
    messages: {
      target_response_status: {
        response_error: "none",
        request_turn_number: 0,
        response_turn_number: 1,
      },
      messages: [
        {
          turn_number: 1,
          role: "assistant",
          message_pieces: [
            {
              id: "p-vid",
              original_value_data_type: "text",
              converted_value_data_type: "video_path",
              original_value: "generated video",
              converted_value: "dmlkZW8=",
              converted_value_mime_type: "video/mp4",
              scores: [],
              response_error: "none",
            },
          ],
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    },
  };
}

function makeMultiModalResponse() {
  return {
    messages: {
      target_response_status: {
        response_error: "none",
        request_turn_number: 0,
        response_turn_number: 1,
      },
      messages: [
        {
          turn_number: 1,
          role: "assistant",
          message_pieces: [
            {
              id: "p-text",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              original_value: "Here is the result:",
              converted_value: "Here is the result:",
              scores: [],
              response_error: "none",
            },
            {
              id: "p-img2",
              original_value_data_type: "text",
              converted_value_data_type: "image_path",
              original_value: "image content",
              converted_value: "aW1hZ2U=",
              converted_value_mime_type: "image/jpeg",
              scores: [],
              response_error: "none",
            },
          ],
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    },
  };
}

function makeErrorResponse(
  errorType: PromptResponseError,
  description: string,
  failedRequestTurnNumber = 0,
  hasConverters = false
): { messages: { target_response_status: TargetResponseStatus; messages: BackendMessage[] } } {
  return {
    messages: {
      target_response_status: {
        response_error: errorType,
        request_turn_number: failedRequestTurnNumber,
        response_turn_number: failedRequestTurnNumber + 1,
      },
      messages: [
        {
          turn_number: failedRequestTurnNumber,
          role: "user",
          message_pieces: [
            {
              id: "p-failed-request",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              original_value: "failed request",
              converted_value: "failed request",
              converter_identifiers: hasConverters
                ? [{ type: "MockConverter" }]
                : [],
              scores: [],
              response_error: "none",
            },
          ],
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          turn_number: failedRequestTurnNumber + 1,
          role: "assistant",
          message_pieces: [
            {
              id: "p-err",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              original_value: "",
              converted_value: "",
              scores: [],
              response_error: errorType,
              response_error_description: description,
            },
          ],
          created_at: "2026-01-01T00:00:01Z",
        },
      ],
    },
  };
}

describe("ChatWindow Integration", () => {
  const mockMessages: Message[] = [
    {
      role: "user",
      content: "Hello",
      timestamp: new Date().toISOString(),
    },
    {
      role: "assistant",
      content: "Hi there!",
      timestamp: new Date().toISOString(),
    },
  ];

  const defaultProps = {
    onNewAttack: jest.fn(),
    activeTarget: mockTarget,
    attackResultId: null as string | null,
    conversationId: null as string | null,
    activeConversationId: null as string | null,
    onConversationCreated: jest.fn(),
    onSelectConversation: jest.fn(),
    labels: { operator: 'testuser', operation: 'test_op' },
  };

  beforeEach(() => {
    jest.clearAllMocks();
    mockedAttacksApi.getMessages.mockReset();
    mockedAttacksApi.addMessage.mockReset();
    mockedMapper.backendMessageToFrontend.mockReset();
    mockedMapper.backendMessageToOriginalDraft.mockReset();
    mockedMapper.backendMessageToOriginalDraft.mockImplementation(
      actualMessageMapper.backendMessageToOriginalDraft
    );
    window.localStorage.clear();
    mockMatchMedia(false);
    // Default: panel API returns empty conversations
    mockedAttacksApi.getConversations.mockResolvedValue({
      conversations: [],
      main_conversation_id: null,
    });
    // Default: getMessages never resolves so loadConversation won't trigger
    // state updates outside act(). Tests that need it override this mock.
    mockedAttacksApi.getMessages.mockImplementation(() => new Promise(() => {}));
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [],
    });
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [],
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  // -----------------------------------------------------------------------
  // Basic rendering
  // -----------------------------------------------------------------------

  it("should render chat window with all components", () => {
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );

    // The ribbon no longer shows the "PyRIT Attack" prefix; the target
    // badge stands on its own as the leftmost element.
    expect(screen.getByRole("heading", { level: 1, name: "Chat" })).toBeInTheDocument();
    expect(screen.queryByText("PyRIT Attack")).not.toBeInTheDocument();
    expect(screen.getByTestId("target-badge")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new attack/i })).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("should attach an updated human score to the latest response", async () => {
    const user = userEvent.setup();
    const onHumanScoreChange = jest.fn();
    mockedAttacksApi.getMessages.mockResolvedValue(makeTextResponse("Forked response") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockedScoresApi.createManualScore.mockResolvedValue({
      id: "manual-score-id",
      message_piece_id: "forked-piece",
      scorer_type: "ManualScorer",
      score_type: "true_false",
      score_value: "True",
      timestamp: "2026-01-01T00:00:02Z",
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="attack-result-id"
          conversationId="primary-conversation-id"
          activeConversationId="forked-conversation-id"
          objective="Evaluate the response"
          outcome="undetermined"
          lastResponseMessagePieceId="latest-response-piece"
          automatedScore={{
            id: "automated-score-id",
            message_piece_id: "older-automated-piece",
            scorer_type: "AutomatedScorer",
            score_type: "true_false",
            score_value: "False",
            timestamp: "2026-01-01T00:00:01Z",
          }}
          humanScore={{
            id: "human-score-id",
            message_piece_id: "older-human-piece",
            scorer_type: "ManualScorer",
            score_type: "true_false",
            score_value: "False",
            timestamp: "2026-01-01T00:00:01Z",
          }}
          onHumanScoreChange={onHumanScoreChange}
        />
      </TestWrapper>
    );

    await user.click(await screen.findByRole("button", { name: /objective achieved outcome: undetermined/i }));
    await user.click(screen.getByRole("radio", { name: "Success" }));
    await user.click(screen.getByRole("button", { name: "Update" }));

    await waitFor(() => {
      expect(mockedScoresApi.createManualScore).toHaveBeenCalledWith({
        attack_result_id: "attack-result-id",
        message_id: "latest-response-piece",
        value: true,
        rationale: "",
        update_attack: true,
      });
      expect(onHumanScoreChange).toHaveBeenCalledWith(
        expect.objectContaining({ id: "manual-score-id" }),
        "success",
      );
    });
  });

  it("should remove the attack human-score override", async () => {
    const user = userEvent.setup();
    const onHumanScoreChange = jest.fn();
    mockedAttacksApi.removeHumanScore.mockResolvedValue({
      attack_result_id: "attack-result-id",
      conversation_id: "primary-conversation-id",
      attack_type: "ManualAttack",
      objective: "Evaluate the response",
      converters: [],
      outcome: "failure",
      message_count: 1,
      related_conversation_ids: [],
      labels: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:02Z",
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="attack-result-id"
          conversationId="primary-conversation-id"
          activeConversationId="primary-conversation-id"
          objective="Evaluate the response"
          outcome="success"
          humanScore={{
            id: "manual-score-id",
            message_piece_id: "response-piece",
            scorer_type: "ManualScorer",
            score_type: "true_false",
            score_value: "True",
            timestamp: "2026-01-01T00:00:01Z",
          }}
          onHumanScoreChange={onHumanScoreChange}
        />
      </TestWrapper>
    );

    await user.click(screen.getByRole("button", { name: /objective achieved outcome: success/i }));
    await user.click(screen.getByRole("button", { name: "Remove human score" }));

    await waitFor(() => {
      expect(mockedAttacksApi.removeHumanScore).toHaveBeenCalledWith("attack-result-id");
      expect(onHumanScoreChange).toHaveBeenCalledWith(null, "failure");
    });
  });

  it("shows a safe scenario-run breadcrumb only when provenance is present", () => {
    const scenarioResultId = "123e4567-e89b-12d3-a456-426614174000";
    const { rerender } = render(
      <TestWrapper>
        <ChatWindow {...defaultProps} scenarioResultId={scenarioResultId} />
      </TestWrapper>
    );

    expect(screen.getByRole("navigation", { name: "Attack provenance" })).toBeInTheDocument();
    expect(screen.getByRole("link", {
      name: `Return to scenario run ${scenarioResultId}`,
    })).toHaveAttribute("href", `/scanner-history/${scenarioResultId}`);

    rerender(
      <TestWrapper>
        <ChatWindow {...defaultProps} scenarioResultId={null} />
      </TestWrapper>
    );
    expect(screen.queryByRole("navigation", { name: "Attack provenance" })).not.toBeInTheDocument();
  });

  it("returns to the originating scenario run from the breadcrumb", async () => {
    const user = userEvent.setup();
    const scenarioResultId = "123e4567-e89b-12d3-a456-426614174000";
    render(
      <FluentProvider theme={webLightTheme}>
        <MemoryRouter initialEntries={["/attacks/attack-1"]}>
          <Routes>
            <Route
              path="/attacks/:attackResultId"
              element={<ChatWindow {...defaultProps} scenarioResultId={scenarioResultId} />}
            />
            <Route
              path="/scanner-history/:scenarioResultId"
              element={<h1>Originating scenario run</h1>}
            />
          </Routes>
        </MemoryRouter>
      </FluentProvider>
    );

    await user.click(screen.getByRole("link", {
      name: `Return to scenario run ${scenarioResultId}`,
    }));

    expect(screen.getByRole("heading", {
      level: 1,
      name: "Originating scenario run",
    })).toBeInTheDocument();
  });

  it("defaults to raw mode when no Markdown preference is stored", () => {
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByRole("switch", { name: /markdown/i })).not.toBeChecked();
    expect(window.localStorage.getItem(MARKDOWN_PREFERENCE_STORAGE_KEY)).toBeNull();
  });

  it("persists explicit Markdown and raw choices across remounts", async () => {
    const user = userEvent.setup();
    const firstRender = render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );

    const toggle = screen.getByRole("switch", { name: /markdown/i });
    expect(toggle).not.toBeChecked();

    await user.click(toggle);
    expect(toggle).toBeChecked();
    expect(window.localStorage.getItem(MARKDOWN_PREFERENCE_STORAGE_KEY)).toBe("markdown");

    firstRender.unmount();
    const secondRender = render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );
    const remountedToggle = screen.getByRole("switch", { name: /markdown/i });
    expect(remountedToggle).toBeChecked();

    await user.click(remountedToggle);
    expect(remountedToggle).not.toBeChecked();
    expect(window.localStorage.getItem(MARKDOWN_PREFERENCE_STORAGE_KEY)).toBe("raw");

    secondRender.unmount();
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );
    expect(screen.getByRole("switch", { name: /markdown/i })).not.toBeChecked();
  });

  it("initializes Markdown mode from stored preference", () => {
    window.localStorage.setItem(MARKDOWN_PREFERENCE_STORAGE_KEY, "markdown");

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByRole("switch", { name: /markdown/i })).toBeChecked();
  });

  it("falls back to raw mode for an invalid stored preference", () => {
    window.localStorage.setItem(MARKDOWN_PREFERENCE_STORAGE_KEY, "invalid");

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByRole("switch", { name: /markdown/i })).not.toBeChecked();
  });

  it("falls back to raw mode when localStorage is unavailable during initialization", () => {
    jest.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Access denied", "SecurityError");
    });

    expect(() => {
      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} />
        </TestWrapper>
      );
    }).not.toThrow();
    expect(screen.getByRole("switch", { name: /markdown/i })).not.toBeChecked();
  });

  it("keeps the in-memory choice when localStorage is unavailable during persistence", async () => {
    const user = userEvent.setup();
    jest.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    });

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );

    const toggle = screen.getByRole("switch", { name: /markdown/i });
    await user.click(toggle);
    expect(toggle).toBeChecked();
  });

  it("should display existing messages", async () => {
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-test"
          conversationId="conv-test"
          activeConversationId="conv-test"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("Hello")).toBeInTheDocument();
      expect(screen.getByText("Hi there!")).toBeInTheDocument();
    });
  });

  it("should show target info when target is active", () => {
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );

    // The target badge is the leftmost element. Its visible label
    // includes the type and model. The same strings also appear in the
    // tooltip body, so we query the badge specifically.
    const badge = screen.getByTestId("target-badge");
    expect(badge).toHaveTextContent(/OpenAIChatTarget/);
    expect(badge).toHaveTextContent(/gpt-4/);
    expect(badge).toHaveAttribute("aria-label", expect.stringContaining(mockTarget.target_registry_name));
  });

  it("should show no-target message when target is null", () => {
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={null} />
      </TestWrapper>
    );

    // Banner in ChatInputArea area
    expect(screen.getByTestId("no-target-banner")).toBeInTheDocument();
    expect(screen.getByTestId("configure-target-input-btn")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add objective/i })).not.toBeInTheDocument();
  });

  it("should call onNewAttack when New Attack button is clicked", async () => {
    const user = userEvent.setup();
    const onNewAttack = jest.fn();

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          onNewAttack={onNewAttack}
          attackResultId="ar-conv-123"
          conversationId="conv-123"
          activeConversationId="conv-123"
        />
      </TestWrapper>
    );

    await user.click(screen.getByText("New Attack"));

    expect(onNewAttack).toHaveBeenCalled();
  });

  it("should show no-target banner when no target is selected", () => {
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={null} />
      </TestWrapper>
    );

    // ChatInputArea shows a red warning banner instead of the text input
    expect(screen.getByTestId("no-target-banner")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("should disable sending while routed attack metadata is loading", () => {
    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={mockTarget}
          isLoadingAttack
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("chat-input")).toBeDisabled();
  });

  it("should keep an unverifiable historical target read-only and retryable", async () => {
    const user = userEvent.setup();
    const onRetryTargetResolution = jest.fn();
    const messages: Message[] = [
      {
        role: "assistant",
        content: "Historical response",
        timestamp: "2026-01-01T00:00:00Z",
      },
    ];
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-unverifiable",
      main_conversation_id: "conv-unverifiable",
      conversations: [
        {
          conversation_id: "conv-unverifiable",
          message_count: 1,
        },
        {
          conversation_id: "conv-related",
          message_count: 1,
        },
      ],
    });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(messages);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={null}
          attackResultId="ar-unverifiable"
          conversationId="conv-unverifiable"
          activeConversationId="conv-unverifiable"
          attackTarget={{
            target_type: "TextTarget",
            identifier_hash: "unverifiable-hash",
          }}
          targetResolutionStatus="error"
          onRetryTargetResolution={onRetryTargetResolution}
          relatedConversationCount={1}
        />
      </TestWrapper>
    );

    expect(await screen.findByTestId("target-resolution-error-banner")).toBeInTheDocument();
    expect(await screen.findByText("Historical response")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByTestId("copy-to-input-btn-0")).toBeDisabled();
    expect(screen.getByTestId("branch-conv-btn-0")).toBeDisabled();
    expect(screen.getByTestId("branch-attack-btn-0")).toBeDisabled();
    expect(await screen.findByTestId("star-btn-conv-related")).toBeDisabled();
    expect(mockedAttacksApi.changeMainConversation).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetryTargetResolution).toHaveBeenCalledTimes(1);
  });

  // -----------------------------------------------------------------------
  // Target info display for various target types
  // -----------------------------------------------------------------------

  it("should display target without model name", () => {
    const targetNoModel: TargetInstance = {
      ...mockTarget,
      identifier: { ...mockTarget.identifier, model_name: null },
    };

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={targetNoModel} />
      </TestWrapper>
    );

    const badge = screen.getByTestId("target-badge");
    expect(badge).toHaveTextContent(/OpenAIChatTarget/);
    expect(badge).not.toHaveTextContent(/gpt/);
  });

  // -----------------------------------------------------------------------
  // First message → create attack + send
  // -----------------------------------------------------------------------

  it("should create attack and send text message on first message", async () => {
    const user = userEvent.setup();
    const onConversationCreated = jest.fn();
    const onAttackChange = jest.fn();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Hello" },
    ]);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-conv-1",
      conversation_id: "conv-1",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockedAttacksApi.addMessage.mockResolvedValue({
      ...makeTextResponse("Hello back!"),
      attack: {
        attack_result_id: "ar-conv-1",
        conversation_id: "conv-1",
        outcome: "undetermined",
        last_response: {
          id: "p-resp",
        },
      },
    } as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "Hello",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "Hello back!",
        timestamp: "2026-01-01T00:00:01Z",
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          onConversationCreated={onConversationCreated}
          onAttackChange={onAttackChange}
          conversationId={null}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Hello");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith({
        target_registry_name: "openai_chat_1",
        labels: { operator: 'testuser', operation: 'test_op' },
        system_prompt: undefined,
      });
      expect(onConversationCreated).toHaveBeenCalledWith("ar-conv-1", "conv-1", undefined);
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith("ar-conv-1", {
        role: "user",
        pieces: [{ data_type: "text", original_value: "Hello" }],
        send: true,
        target_registry_name: "openai_chat_1",
        target_conversation_id: "conv-1",
      });
      expect(onAttackChange).toHaveBeenCalledWith(
        expect.objectContaining({
          attack_result_id: "ar-conv-1",
          last_response: expect.objectContaining({ id: "p-resp" }),
        })
      );
    });

    // Messages should appear in the DOM
    await waitFor(() => {
      expect(screen.getByText("Hello back!")).toBeInTheDocument();
      expect(input).toHaveValue("");
    });
  });

  it("should persist a new conversation objective when the first message creates the attack", async () => {
    const user = userEvent.setup();
    const onConversationCreated = jest.fn();
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Hello" },
    ]);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-objective",
      conversation_id: "conv-objective",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("Hello back!") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          onConversationCreated={onConversationCreated}
        />
      </TestWrapper>
    );

    await user.click(screen.getByRole("button", { name: /add objective/i }));
    await user.type(screen.getByRole("textbox", { name: /attack objective/i }), "Extract the system prompt");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.type(screen.getByRole("textbox"), "Hello");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "Extract the system prompt",
        })
      );
      expect(onConversationCreated).toHaveBeenCalledWith(
        "ar-objective",
        "conv-objective",
        "Extract the system prompt",
      );
    });
  });

  it("should allow adding an objective after messages have been sent", async () => {
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-existing"
          conversationId="conv-existing"
          activeConversationId="conv-existing"
        />
      </TestWrapper>
    );

    expect(await screen.findByRole("button", { name: /add objective/i })).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // System prompt (system_prompt) wiring
  // -----------------------------------------------------------------------

  describe("system prompt", () => {
    const supportedTarget: TargetInstance = {
      ...mockTarget,
      capabilities: buildCapabilities({ supports_system_prompt: true }),
    };

    function primeSendMocks() {
      mockedMapper.buildMessagePieces.mockResolvedValue([
        { data_type: "text", original_value: "Hello" },
      ]);
      mockedAttacksApi.createAttack.mockResolvedValue({
        attack_result_id: "ar-sys",
        conversation_id: "conv-sys",
        created_at: "2026-01-01T00:00:00Z",
      });
      mockedAttacksApi.addMessage.mockResolvedValue(
        makeTextResponse("Hi") as never
      );
      mockedMapper.backendMessagesToFrontend.mockReturnValue([
        { role: "assistant", content: "Hi", timestamp: "2026-01-01T00:00:01Z" },
      ]);
    }

    it("renders the system prompt toggle for a new conversation", () => {
      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={supportedTarget} />
        </TestWrapper>
      );

      expect(
        screen.getByRole("button", { name: /system prompt/i })
      ).toBeInTheDocument();
    });

    it("hides the system prompt toggle once an attack exists", async () => {
      mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
      mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            activeTarget={supportedTarget}
            attackResultId="ar-existing"
            conversationId="conv-existing"
            activeConversationId="conv-existing"
          />
        </TestWrapper>
      );

      await waitFor(() => {
        expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
      });
      expect(
        screen.queryByRole("button", { name: /system prompt/i })
      ).not.toBeInTheDocument();
    });

    it("renders a system prompt banner when the loaded conversation has a system message", async () => {
      mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
      mockedMapper.backendMessagesToFrontend.mockReturnValue([
        { role: "system", content: "You are a pirate.", timestamp: "2026-01-01T00:00:00Z" },
        { role: "user", content: "Ahoy", timestamp: "2026-01-01T00:00:01Z" },
      ]);

      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            activeTarget={supportedTarget}
            attackResultId="ar-existing"
            conversationId="conv-existing"
            activeConversationId="conv-existing"
          />
        </TestWrapper>
      );

      expect(await screen.findByTestId("system-prompt-banner")).toBeInTheDocument();
      expect(screen.getByText("You are a pirate.")).toBeInTheDocument();
    });

    it("forwards the typed system prompt when the target supports it", async () => {
      const user = userEvent.setup();
      primeSendMocks();

      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={supportedTarget} />
        </TestWrapper>
      );

      await user.click(screen.getByRole("button", { name: /system prompt/i }));
      await user.type(
        screen.getByRole("textbox", { name: /system prompt/i }),
        "You are helpful"
      );
      await user.type(screen.getByPlaceholderText("Type prompt here"), "Hello");
      await user.click(screen.getByRole("button", { name: /send/i }));

      await waitFor(() => {
        expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(
          expect.objectContaining({ system_prompt: "You are helpful" })
        );
      });
    });

    it("omits the system prompt when the target does not support it", async () => {
      const user = userEvent.setup();
      primeSendMocks();

      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={mockTarget} />
        </TestWrapper>
      );

      await user.type(screen.getByPlaceholderText("Type prompt here"), "Hello");
      await user.click(screen.getByRole("button", { name: /send/i }));

      await waitFor(() => {
        expect(mockedAttacksApi.createAttack).toHaveBeenCalled();
      });
      const createArgs = mockedAttacksApi.createAttack.mock.calls[0][0];
      expect(createArgs.system_prompt).toBeUndefined();
    });

    it("disables the toggle and drops the prompt for an explicitly unsupported target", async () => {
      const user = userEvent.setup();
      primeSendMocks();

      const unsupportedTarget: TargetInstance = {
        ...mockTarget,
        capabilities: buildCapabilities({ supports_system_prompt: false }),
      };

      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={unsupportedTarget} />
        </TestWrapper>
      );

      expect(
        screen.getByRole("button", { name: /system prompt/i })
      ).toBeDisabled();

      await user.type(screen.getByPlaceholderText("Type prompt here"), "Hello");
      await user.click(screen.getByRole("button", { name: /send/i }));

      await waitFor(() => {
        expect(mockedAttacksApi.createAttack).toHaveBeenCalled();
      });
      const createArgs = mockedAttacksApi.createAttack.mock.calls[0][0];
      expect(createArgs.system_prompt).toBeUndefined();
    });

    it("omits the system prompt when left blank on a supporting target", async () => {
      const user = userEvent.setup();
      primeSendMocks();

      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={supportedTarget} />
        </TestWrapper>
      );

      await user.type(screen.getByPlaceholderText("Type prompt here"), "Hello");
      await user.click(screen.getByRole("button", { name: /send/i }));

      await waitFor(() => {
        expect(mockedAttacksApi.createAttack).toHaveBeenCalled();
      });
      const createArgs = mockedAttacksApi.createAttack.mock.calls[0][0];
      expect(createArgs.system_prompt).toBeUndefined();
    });

    it("clears a retained system prompt when switching to an unsupported target", async () => {
      const user = userEvent.setup();
      primeSendMocks();

      const supportedA: TargetInstance = {
        ...mockTarget,
        target_registry_name: "supports_a",
        capabilities: buildCapabilities({ supports_system_prompt: true }),
      };
      const unsupportedB: TargetInstance = {
        ...mockTarget,
        target_registry_name: "no_support_b",
        capabilities: buildCapabilities({ supports_system_prompt: false }),
      };
      const supportedC: TargetInstance = {
        ...mockTarget,
        target_registry_name: "supports_c",
        capabilities: buildCapabilities({ supports_system_prompt: true }),
      };

      const { rerender } = render(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={supportedA} />
        </TestWrapper>
      );

      await user.click(screen.getByRole("button", { name: /system prompt/i }));
      await user.type(
        screen.getByRole("textbox", { name: /system prompt/i }),
        "You are helpful"
      );

      // Switch to an unsupported target (should clear), then to another
      // supporting one so the cleared value is observable on send.
      rerender(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={unsupportedB} />
        </TestWrapper>
      );
      rerender(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={supportedC} />
        </TestWrapper>
      );

      await user.type(screen.getByPlaceholderText("Type prompt here"), "Hello");
      await user.click(screen.getByRole("button", { name: /send/i }));

      await waitFor(() => {
        expect(mockedAttacksApi.createAttack).toHaveBeenCalled();
      });
      const createArgs = mockedAttacksApi.createAttack.mock.calls[0][0];
      expect(createArgs.system_prompt).toBeUndefined();
    });

    it("preserves the system prompt across supporting targets", async () => {
      const user = userEvent.setup();
      primeSendMocks();

      const supportedA: TargetInstance = {
        ...mockTarget,
        target_registry_name: "supports_a",
        capabilities: buildCapabilities({ supports_system_prompt: true }),
      };
      const supportedB: TargetInstance = {
        ...mockTarget,
        target_registry_name: "supports_b",
        capabilities: buildCapabilities({ supports_system_prompt: true }),
      };

      const { rerender } = render(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={supportedA} />
        </TestWrapper>
      );

      await user.click(screen.getByRole("button", { name: /system prompt/i }));
      await user.type(
        screen.getByRole("textbox", { name: /system prompt/i }),
        "You are helpful"
      );

      rerender(
        <TestWrapper>
          <ChatWindow {...defaultProps} activeTarget={supportedB} />
        </TestWrapper>
      );

      await user.type(screen.getByPlaceholderText("Type prompt here"), "Hello");
      await user.click(screen.getByRole("button", { name: /send/i }));

      await waitFor(() => {
        expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(
          expect.objectContaining({ system_prompt: "You are helpful" })
        );
      });
    });
  });

  // -----------------------------------------------------------------------
  // Subsequent messages → reuse conversation ID
  // -----------------------------------------------------------------------

  it("should reuse conversationId on subsequent messages", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Second" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("Response") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "assistant",
        content: "Response",
        timestamp: "2026-01-01T00:00:01Z",
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} attackResultId="ar-existing-conv" conversationId="existing-conv" />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Second");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).not.toHaveBeenCalled();
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith(
        "ar-existing-conv",
        expect.any(Object)
      );
    });
  });

  // -----------------------------------------------------------------------
  // Error handling
  // -----------------------------------------------------------------------

  it("should show error message when API call fails", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "test" },
    ]);
    mockedAttacksApi.createAttack.mockRejectedValue(
      new Error("Network error")
    );

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          conversationId={null}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "test");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument();
    });
  });

  it("should show error message when addMessage fails", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "test" },
    ]);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-conv-err",
      conversation_id: "conv-err",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockedAttacksApi.addMessage.mockRejectedValue(
      new Error("Request failed with status code 404")
    );

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          conversationId={null}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "test");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Request failed with status code 404/)).toBeInTheDocument();
      expect(input).toHaveValue("test");
    });
  });

  it("should extract detail from axios-style error response", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "test" },
    ]);

    // Simulate an axios error with response.data.detail (what FastAPI returns)
    const axiosError = new Error("Request failed with status code 500") as Error & { isAxiosError: boolean; response: { status: number; data: { detail: string } } };
    axiosError.isAxiosError = true;
    axiosError.response = {
      status: 500,
      data: { detail: "Failed to add message: Image URLs are only allowed for messages with role 'user'" },
    };
    mockedAttacksApi.addMessage.mockRejectedValue(axiosError);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          conversationId="conv-x"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "test");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to add message/)).toBeInTheDocument();
    });
  });

  it("should extract plain string from axios-style error response", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "test" },
    ]);

    // Simulate a response where data is a plain string (not JSON)
    const axiosError = new Error("Request failed with status code 500") as Error & { isAxiosError: boolean; response: { status: number; data: string } };
    axiosError.isAxiosError = true;
    axiosError.response = {
      status: 500,
      data: "Internal Server Error",
    };
    mockedAttacksApi.addMessage.mockRejectedValue(axiosError);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          conversationId="conv-x"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "test");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Internal Server Error/)).toBeInTheDocument();
    });
  });

  it("should show generic error for non-Error thrown values", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "test" },
    ]);
    mockedAttacksApi.addMessage.mockRejectedValue("string error");

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          conversationId="conv-x"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "test");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/string error/)).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Loading indicator flow
  // -----------------------------------------------------------------------

  it("should show loading then replace with response", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Hello" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("Hi!") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "Hello",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "Hi!",
        timestamp: "2026-01-01T00:00:01Z",
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-2"
          conversationId="conv-2"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Hello");
    await user.click(screen.getByRole("button", { name: /send/i }));

    // Response should appear in the DOM
    await waitFor(() => {
      expect(screen.getByText("Hi!")).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Multi-modal: image response
  // -----------------------------------------------------------------------

  it("should handle image response from backend", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Generate an image" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeImageResponse() as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        attachments: [
          {
            type: "image" as const,
            name: "image_path_p-img",
            url: "data:image/png;base64,iVBORw0KGgo=",
            mimeType: "image/png",
            size: 12,
          },
        ],
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-img"
          conversationId="conv-img"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Generate an image");
    await user.click(screen.getByRole("button", { name: /send/i }));

    // The response should include the image attachment rendered in the DOM
    await waitFor(() => {
      expect(screen.getByRole("img")).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Multi-modal: audio response
  // -----------------------------------------------------------------------

  it("should handle audio response from backend", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Read this aloud" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeAudioResponse() as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        attachments: [
          {
            type: "audio" as const,
            name: "audio_path_p-aud",
            url: "data:audio/wav;base64,UklGRg==",
            mimeType: "audio/wav",
            size: 8,
          },
        ],
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-audio"
          conversationId="conv-audio"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Read this aloud");
    await user.click(screen.getByRole("button", { name: /send/i }));

    // Audio element should appear in the DOM
    await waitFor(() => {
      const audioEl = document.querySelector("audio");
      expect(audioEl).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Multi-modal: video response
  // -----------------------------------------------------------------------

  it("should handle video response from backend", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Create a video" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeVideoResponse() as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        attachments: [
          {
            type: "video" as const,
            name: "video_path_p-vid",
            url: "data:video/mp4;base64,dmlkZW8=",
            mimeType: "video/mp4",
            size: 8,
          },
        ],
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-video"
          conversationId="conv-video"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Create a video");
    await user.click(screen.getByRole("button", { name: /send/i }));

    // Video element should appear in the DOM
    await waitFor(() => {
      const videoEl = document.querySelector("video");
      expect(videoEl).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Multi-modal: mixed text + image response
  // -----------------------------------------------------------------------

  it("should handle mixed text + image response", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Describe and show" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeMultiModalResponse() as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "assistant",
        content: "Here is the result:",
        timestamp: "2026-01-01T00:00:01Z",
        attachments: [
          {
            type: "image" as const,
            name: "image_path_p-img2",
            url: "data:image/jpeg;base64,aW1hZ2U=",
            mimeType: "image/jpeg",
            size: 8,
          },
        ],
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-multi"
          conversationId="conv-multi"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Describe and show");
    await user.click(screen.getByRole("button", { name: /send/i }));

    // Both text and image should appear in the DOM
    await waitFor(() => {
      expect(screen.getByText("Here is the result:")).toBeInTheDocument();
      expect(screen.getByRole("img")).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Sending image attachment
  // -----------------------------------------------------------------------

  it("should send image attachment alongside text", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "What is this?" },
      {
        data_type: "image_path",
        original_value: "iVBORw0KGgo=",
        mime_type: "image/png",
      },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("It's a cat.") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "assistant",
        content: "It's a cat.",
        timestamp: "2026-01-01T00:00:01Z",
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-attach"
          conversationId="conv-attach"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "What is this?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith(
        "ar-conv-attach",
        expect.objectContaining({
          pieces: [
            { data_type: "text", original_value: "What is this?" },
            {
              data_type: "image_path",
              original_value: "iVBORw0KGgo=",
              mime_type: "image/png",
            },
          ],
          send: true,
          target_conversation_id: "conv-attach",
        })
      );
    });
  });

  // -----------------------------------------------------------------------
  // Sending audio attachment
  // -----------------------------------------------------------------------

  it("should send audio attachment", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      {
        data_type: "audio_path",
        original_value: "UklGRg==",
        mime_type: "audio/wav",
      },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(
      makeTextResponse("Transcribed: hello") as never
    );
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "assistant",
        content: "Transcribed: hello",
        timestamp: "2026-01-01T00:00:01Z",
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} attackResultId="ar-conv-aud-send" conversationId="conv-aud-send" />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Listen");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith(
        "ar-conv-aud-send",
        expect.objectContaining({
          pieces: [
            {
              data_type: "audio_path",
              original_value: "UklGRg==",
              mime_type: "audio/wav",
            },
          ],
          target_conversation_id: "conv-aud-send",
        })
      );
    });
  });

  // -----------------------------------------------------------------------
  // Backend error in response piece (blocked, processing, etc.)
  // -----------------------------------------------------------------------

  it.each([
    { source: "live", prefixLength: 0 },
    { source: "persisted", prefixLength: 0 },
    { source: "live", prefixLength: 2 },
    { source: "persisted", prefixLength: 2 },
  ])(
    "should exclude earlier processing failures from $source recovery with $prefixLength safe prefix turns",
    async ({ source, prefixLength }: { source: string; prefixLength: number }) => {
      const user = userEvent.setup();
      const onSelectConversation = jest.fn();
      const safePrefix = prefixLength
        ? makeErrorResponse("none", "").messages.messages
        : [];
      const earlierFailure = makeErrorResponse("processing", "Earlier failure", prefixLength);
      const latestFailure = makeErrorResponse("processing", "Latest failure", prefixLength + 2);
      const latestRequestPiece = latestFailure.messages.messages[0].message_pieces[0];
      latestRequestPiece.original_value = "Latest failed draft";
      latestRequestPiece.converted_value = "Latest failed draft";
      const failedMessages = {
        conversation_id: "conv-two-failures",
        messages: [
          ...safePrefix,
          ...earlierFailure.messages.messages,
          ...latestFailure.messages.messages,
        ],
        target_response_status: latestFailure.messages.target_response_status,
      };
      const props = {
        ...defaultProps,
        attackResultId: "ar-two-failures",
        conversationId: "conv-two-failures",
        activeConversationId: "conv-two-failures",
        onSelectConversation,
      };
      mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
      mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
      mockedAttacksApi.getMessages.mockResolvedValue(failedMessages);
      mockedAttacksApi.addMessage.mockResolvedValue({ messages: failedMessages } as never);
      mockedAttacksApi.createConversation.mockResolvedValue({
        conversation_id: "conv-error-free",
        created_at: "2026-01-01T00:00:10Z",
      });
      if (source === "live") {
        mockedAttacksApi.getMessages.mockResolvedValueOnce({ messages: [] });
      }

      const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
      if (source === "live") {
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        await user.type(screen.getByRole("textbox"), "Latest failed draft");
        await user.click(screen.getByRole("button", { name: /send message/i }));
      }
      const recover = await screen.findByRole("button", { name: /edit in clean conversation/i });
      await user.click(recover);
      await waitFor(() => {
        expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(
          props.attackResultId,
          prefixLength
            ? { source_conversation_id: props.conversationId, cutoff_index: prefixLength - 1 }
            : {},
        );
      });
      expect(onSelectConversation).toHaveBeenCalledWith("conv-error-free");
      expect(screen.getByText(/history from the first failed prompt onward will be left out/i)).toBeInTheDocument();

      mockedAttacksApi.getMessages.mockResolvedValue({
        conversation_id: "conv-error-free",
        messages: safePrefix,
        target_response_status: prefixLength
          ? { response_error: "none", request_turn_number: 0, response_turn_number: 1 }
          : null,
      });
      rendered.rerender(
        <TestWrapper><ChatWindow {...props} activeConversationId="conv-error-free" /></TestWrapper>
      );
      await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
      expect(screen.getByRole("textbox")).toHaveValue("Latest failed draft");
      expect(screen.queryByRole("button", { name: /edit in clean conversation/i })).not.toBeInTheDocument();
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledTimes(source === "live" ? 1 : 0);
    },
  );

  it("should wait for the selected transcript before sending and exporting after a rejection", async () => {
    const user = userEvent.setup();
    const firstHistory = makeErrorResponse("none", "").messages;
    firstHistory.messages[1].message_pieces[0].converted_value = "Only conversation A history";
    const secondHistory = makeErrorResponse("none", "").messages;
    secondHistory.messages[1].message_pieces[0].converted_value = "Only conversation B history";
    let resolveSecondHistory: (value: typeof secondHistory) => void = () => {};
    const pendingHistory = new Promise<typeof secondHistory>((resolve) => {
      resolveSecondHistory = resolve;
    });
    mockedAttacksApi.getMessages
      .mockResolvedValueOnce(firstHistory)
      .mockReturnValueOnce(pendingHistory);
    mockedAttacksApi.addMessage.mockRejectedValue(new Error("Request rejected"));
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    const props = {
      ...defaultProps,
      attackResultId: "ar-transcript-race",
      conversationId: "conv-a",
      activeConversationId: "conv-a",
    };
    const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    expect(await screen.findByText("Only conversation A history")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox"), "Unsent draft");

    rendered.rerender(<TestWrapper><ChatWindow {...props} activeConversationId="conv-b" /></TestWrapper>);
    await waitFor(() => {
      expect(mockedAttacksApi.getMessages).toHaveBeenCalledWith(props.attackResultId, "conv-b");
    });
    const sendButton = screen.getByRole("button", { name: /send message/i });
    expect(sendButton).toBeDisabled();
    expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled();
    await user.click(sendButton);
    await user.keyboard("{Enter}");
    expect(mockedAttacksApi.addMessage).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("Unsent draft");

    await act(async () => {
      resolveSecondHistory(secondHistory);
    });
    expect(await screen.findByText("Only conversation B history")).toBeInTheDocument();
    expect(screen.queryByText("Only conversation A history")).not.toBeInTheDocument();
    await user.click(sendButton);
    await waitFor(() => {
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith(
        props.attackResultId,
        expect.objectContaining({ target_conversation_id: "conv-b" }),
      );
      expect(screen.getByRole("button", { name: /export conversation/i })).toBeEnabled();
    });
    expect(screen.getByRole("textbox")).toHaveValue("Unsent draft");
    expect(screen.queryByText("Only conversation A history")).not.toBeInTheDocument();

    jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await user.click(screen.getByRole("button", { name: /export conversation/i }));
    await user.click(screen.getByRole("menuitem", { name: /export as json/i }));
    const exportedBlob = jest.mocked(URL.createObjectURL).mock.calls.at(-1)?.[0];
    if (!(exportedBlob instanceof Blob)) {
      throw new Error("Expected a downloaded conversation Blob");
    }
    const exportedText = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        if (typeof reader.result !== "string") {
          reject(new Error("Expected a text export"));
          return;
        }
        resolve(reader.result);
      };
      reader.onerror = () => reject(reader.error);
      reader.readAsText(exportedBlob);
    });
    expect(JSON.parse(exportedText).conversation_id).toBe("conv-b");
    expect(exportedText).toContain("Only conversation B history");
    expect(exportedText).not.toContain("Only conversation A history");
  });

  it.each(["original", "new"])(
    "should keep rejected-send history scoped when returning to the %s conversation view",
    async (destination: string) => {
      const user = userEvent.setup();
      const firstHistory = makeErrorResponse("none", "").messages;
      firstHistory.messages[1].message_pieces[0].converted_value = "Original conversation history";
      const otherHistory = makeErrorResponse("none", "").messages;
      otherHistory.messages[1].message_pieces[0].converted_value = "Other conversation history";
      let rejectSend: (reason: Error) => void = () => {};
      let resolveReload: (value: typeof firstHistory) => void = () => {};
      mockedAttacksApi.getMessages
        .mockResolvedValueOnce(firstHistory)
        .mockResolvedValueOnce(otherHistory)
        .mockImplementationOnce(() => new Promise<typeof firstHistory>((resolve) => {
          resolveReload = resolve;
        }));
      mockedAttacksApi.addMessage.mockImplementation(() => new Promise((_resolve, reject) => {
        rejectSend = reject;
      }));
      mockedAttacksApi.getConversations.mockResolvedValue({
        main_conversation_id: "conv-original",
        conversations: [
          { conversation_id: "conv-original", message_count: 2 },
          { conversation_id: "conv-other", message_count: 2 },
        ],
      });
      mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
      mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
      const props = {
        ...defaultProps,
        attackResultId: "ar-return-race",
        conversationId: "conv-original",
        activeConversationId: "conv-original",
        relatedConversationCount: 1,
      };
      const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
      expect(await screen.findByText("Original conversation history")).toBeInTheDocument();
      await user.type(screen.getByRole("textbox"), "Pending original request");
      await user.click(screen.getByRole("button", { name: /send message/i }));
      await waitFor(() => expect(mockedAttacksApi.addMessage).toHaveBeenCalledTimes(1));
      await user.click(await screen.findByRole("button", { name: "Select conversation conv-other" }));
      rendered.rerender(
        <TestWrapper><ChatWindow {...props} activeConversationId="conv-other" /></TestWrapper>
      );
      expect(await screen.findByText("Other conversation history")).toBeInTheDocument();
      if (destination === "original") {
        await user.click(screen.getByRole("button", { name: "Select conversation conv-original" }));
      }
      rendered.rerender(
        <TestWrapper>
          <ChatWindow
            {...props}
            attackResultId={destination === "original" ? props.attackResultId : null}
            conversationId={destination === "original" ? props.conversationId : null}
            activeConversationId={destination === "original" ? props.activeConversationId : null}
          />
        </TestWrapper>
      );
      await act(async () => {
        rejectSend(new Error("Send failed after navigation"));
      });

      expect(screen.queryByText("Other conversation history")).not.toBeInTheDocument();
      if (destination === "original") {
        expect(await screen.findByText("Original conversation history")).toBeInTheDocument();
        expect(within(screen.getByTestId("message-list")).getByText("Pending original request")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /export conversation/i })).toBeEnabled();
        await act(async () => {
          resolveReload(otherHistory);
        });
        expect(screen.getByText("Original conversation history")).toBeInTheDocument();
        expect(screen.queryByText("Other conversation history")).not.toBeInTheDocument();
      } else {
        expect(screen.queryByText("Original conversation history")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled();
      }
    },
  );

  it("should retain a blocked turn when recovering a later processing failure", async () => {
    const user = userEvent.setup();
    const blocked = makeErrorResponse("blocked", "Blocked by the target", 0);
    const failed = makeErrorResponse("processing", "Processing failed", 2);
    mockedAttacksApi.getMessages.mockResolvedValue({
      messages: [...blocked.messages.messages, ...failed.messages.messages],
      target_response_status: failed.messages.target_response_status,
    });
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "conv-after-blocked",
      created_at: "2026-01-01T00:00:10Z",
    });
    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-blocked-prefix"
          conversationId="conv-blocked-prefix"
          activeConversationId="conv-blocked-prefix"
        />
      </TestWrapper>
    );
    await user.click(await screen.findByRole("button", { name: /edit in clean conversation/i }));
    expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(
      "ar-blocked-prefix",
      { source_conversation_id: "conv-blocked-prefix", cutoff_index: 1 },
    );
    expect(screen.queryByText(/history from the first failed prompt onward/i)).not.toBeInTheDocument();
    expect(mockedAttacksApi.addMessage).not.toHaveBeenCalled();
  });

  it("should preserve a recovered media converter before its file read completes", async () => {
    const user = userEvent.setup();
    const file = new File(["image contents"], "evidence.png", { type: "image/png" });
    const NativeFileReader = FileReader;
    let deferFileReads = false;
    const deferredReads: Array<() => Promise<void>> = [];
    class DeferredFileReader extends NativeFileReader {
      override readAsDataURL(blob: Blob): void {
        if (deferFileReads) {
          deferredReads.push(() => new Promise<void>((resolve) => {
            this.addEventListener("loadend", () => resolve(), { once: true });
            super.readAsDataURL(blob);
          }));
          return;
        }
        super.readAsDataURL(blob);
      }
    }
    jest.spyOn(window, "FileReader").mockImplementation(() => new DeferredFileReader());
    const failedResponse = makeErrorResponse("processing", "Target failed", 0, true);
    Object.assign(failedResponse.messages.messages[0].message_pieces[0], {
      original_value_data_type: "image_path",
      converted_value_data_type: "image_path",
      original_value: "/original/evidence.png",
      converted_value: "/converted/evidence.png",
      original_filename: "evidence.png",
      converted_filename: "converted.png",
    });
    const props = {
      ...defaultProps,
      activeTarget: makeTarget({
        target_registry_name: "media-recovery-target",
        target_type: "OpenAIChatTarget",
        capabilities: buildCapabilities({ supported_input_modalities: ["text", "image_path"] }),
      }),
      attackResultId: "ar-media-readiness",
      conversationId: "conv-media-failure",
      activeConversationId: "conv-media-failure",
    };
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedAttacksApi.getMessages
      .mockImplementation(async (_attackId: string, conversationId: string) => (
        conversationId === props.conversationId ? failedResponse.messages : { messages: [] }
      ))
      .mockResolvedValueOnce({ messages: [] });
    mockedAttacksApi.addMessage
      .mockResolvedValueOnce(failedResponse as never)
      .mockResolvedValueOnce(makeTextResponse("Recovered response") as never);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "conv-media-recovery",
      created_at: "2026-01-01T00:00:02Z",
    });
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [{
        converter_type: "ImageRotationConverter",
        supported_input_types: ["image_path"],
        supported_output_types: ["image_path"],
        parameters: [],
      }],
    });
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: "preserved-image-converter",
      converter_type: "ImageRotationConverter",
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      converted_value: "/converted/evidence.png",
      converted_value_data_type: "image_path",
    });

    const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    await user.upload(screen.getByTestId("file-input"), file);
    await user.click(screen.getByRole("button", { name: /convert/i }));
    await user.click(await screen.findByRole("tab", { name: "Image" }));
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /ImageRotationConverter/ }));
    await user.click(screen.getByRole("button", { name: /^preview$/i }));
    await user.click(await screen.findByRole("button", { name: /use converted/i }));
    await user.click(screen.getByTestId("close-converter-panel-btn"));
    await user.click(screen.getByRole("button", { name: /send message/i }));
    expect(await screen.findByRole("button", { name: /edit in clean conversation/i })).toBeEnabled();
    expect(mockedAttacksApi.addMessage).toHaveBeenLastCalledWith(
      props.attackResultId,
      expect.objectContaining({ converter_ids: ["preserved-image-converter"] }),
    );

    rendered.rerender(<TestWrapper><ChatWindow {...props} activeConversationId="conv-other" /></TestWrapper>);
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    await user.click(screen.getByTestId("remove-attachment-0"));
    await waitFor(() => {
      expect(screen.queryByTestId("clear-media-conversion-image")).not.toBeInTheDocument();
    });
    rendered.rerender(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    const recover = await screen.findByRole("button", { name: /edit in clean conversation/i });
    deferFileReads = true;
    await user.click(recover);
    await waitFor(() => {
      expect(props.onSelectConversation).toHaveBeenCalledWith("conv-media-recovery");
    });
    rendered.rerender(
      <TestWrapper><ChatWindow {...props} activeConversationId="conv-media-recovery" /></TestWrapper>
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /send message/i })).toBeEnabled());
    expect(deferredReads).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => expect(deferredReads).toHaveLength(2));
    await act(async () => {
      await deferredReads[1]();
    });
    expect(mockedAttacksApi.addMessage).toHaveBeenLastCalledWith(
      props.attackResultId,
      expect.objectContaining({
        target_conversation_id: "conv-media-recovery",
        converter_ids: ["preserved-image-converter"],
        pieces: [expect.objectContaining({ data_type: "image_path" })],
      }),
    );
    await act(async () => {
      await deferredReads[0]();
    });
  });

  it("should preserve the draft and expose recovery for an HTTP 200 processing error", async () => {
    const user = userEvent.setup();
    const onSelectConversation = jest.fn();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "retry this prompt" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(
      makeErrorResponse("processing", "The target could not process this message.", 2) as never
    );
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "retry this prompt",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        error: {
          type: "processing",
          description: "The target could not process this message.",
        },
      },
    ]);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "conv-processing-recovery",
    } as never);

    const rendered = render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-processing"
          conversationId="conv-processing"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "retry this prompt");
    await user.click(screen.getByRole("button", { name: /send/i }));

    const recoveryButton = await screen.findByRole(
      "button",
      { name: /edit in clean conversation/i }
    );
    expect(input).toHaveValue("retry this prompt");
    expect(input).toBeDisabled();
    expect(screen.queryByTestId("message-actions-1")).not.toBeInTheDocument();

    await user.click(recoveryButton);
    await waitFor(() => {
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(
        "ar-conv-processing",
        {
          source_conversation_id: "conv-processing",
          cutoff_index: 1,
        }
      );
      expect(onSelectConversation).toHaveBeenCalledWith("conv-processing-recovery");
    });

    rendered.rerender(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-processing"
          conversationId="conv-processing"
          activeConversationId="conv-processing-recovery"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(input).toHaveFocus();
      expect(input).toHaveValue("retry this prompt");
    });
  });

  it("should ignore an older same-conversation load after a processing failure", async () => {
    const user = userEvent.setup();
    const processingResponse = makeErrorResponse(
      "processing",
      "The target could not process this message.",
      2
    );
    const staleLoadResponse = {
      conversation_id: "conv-processing-load-race",
      messages: [],
      target_response_status: null,
    };
    let resolveLoad: ((value: typeof staleLoadResponse) => void) | undefined;
    let resolveSend: (value: typeof processingResponse) => void = () => {};

    mockedAttacksApi.getMessages.mockImplementation(
      () => new Promise<typeof staleLoadResponse>((resolve) => {
        resolveLoad = resolve;
      }) as never
    );
    mockedAttacksApi.getMessages.mockResolvedValueOnce({ messages: [] });
    mockedAttacksApi.getConversations.mockResolvedValue({
      main_conversation_id: "conv-processing-load-race",
      conversations: [{ conversation_id: "conv-processing-load-race", message_count: 0 }],
    });
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "keep this draft" },
    ]);
    mockedAttacksApi.addMessage.mockImplementation(
      () => new Promise<typeof processingResponse>((resolve) => {
        resolveSend = resolve;
      }) as never
    );
    mockedMapper.backendMessagesToFrontend.mockImplementation((messages) =>
      messages.length === 0
        ? []
        : [
            {
              role: "user",
              content: "keep this draft",
              timestamp: "2026-01-01T00:00:00Z",
            },
            {
              role: "assistant",
              content: "",
              timestamp: "2026-01-01T00:00:01Z",
              error: {
                type: "processing",
                description: "The target could not process this message.",
              },
            },
          ]
    );

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-processing-load-race"
          conversationId="conv-processing-load-race"
          activeConversationId="conv-processing-load-race"
          relatedConversationCount={1}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(1);
    });
    const input = screen.getByRole("textbox");
    await user.type(input, "keep this draft");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(mockedAttacksApi.addMessage).toHaveBeenCalledTimes(1));
    await user.click(
      await screen.findByRole("button", { name: "Select conversation conv-processing-load-race" })
    );
    await waitFor(() => expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(2));
    await act(async () => {
      resolveSend(processingResponse);
    });
    await waitFor(() => {
      expect(mockedMapper.backendMessagesToFrontend).toHaveBeenCalledWith(
        processingResponse.messages.messages
      );
    });

    await act(async () => {
      resolveLoad?.(staleLoadResponse);
      await Promise.resolve();
    });

    expect(
      await screen.findByRole("button", { name: /edit in clean conversation/i })
    ).toBeInTheDocument();
    expect(screen.getByText(/The target could not process this message\./)).toBeInTheDocument();
    expect(input).toHaveValue("keep this draft");
    expect(input).toBeDisabled();
  });

  it("should ignore an older same-conversation load failure after a processing failure", async () => {
    const user = userEvent.setup();
    const processingResponse = makeErrorResponse(
      "processing",
      "The target could not process this message.",
      2
    );
    let rejectLoad: ((reason?: unknown) => void) | undefined;
    let resolveSend: (value: typeof processingResponse) => void = () => {};

    mockedAttacksApi.getMessages.mockImplementation(
      () => new Promise((_resolve, reject) => {
        rejectLoad = reject;
      }) as never
    );
    mockedAttacksApi.getMessages.mockResolvedValueOnce({ messages: [] });
    mockedAttacksApi.getConversations.mockResolvedValue({
      main_conversation_id: "conv-processing-load-failure",
      conversations: [{ conversation_id: "conv-processing-load-failure", message_count: 0 }],
    });
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "keep failed draft" },
    ]);
    mockedAttacksApi.addMessage.mockImplementation(
      () => new Promise<typeof processingResponse>((resolve) => {
        resolveSend = resolve;
      }) as never
    );
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-processing-load-failure"
          conversationId="conv-processing-load-failure"
          activeConversationId="conv-processing-load-failure"
          relatedConversationCount={1}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(1);
    });
    const input = screen.getByRole("textbox");
    await user.type(input, "keep failed draft");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(mockedAttacksApi.addMessage).toHaveBeenCalledTimes(1));
    await user.click(
      await screen.findByRole("button", { name: "Select conversation conv-processing-load-failure" })
    );
    await waitFor(() => expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(2));
    await act(async () => {
      resolveSend(processingResponse);
    });
    await waitFor(() => {
      expect(mockedMapper.backendMessagesToFrontend).toHaveBeenCalledWith(
        processingResponse.messages.messages
      );
    });

    await act(async () => {
      rejectLoad?.(new Error("stale load failed"));
      await Promise.resolve();
    });

    expect(
      await screen.findByRole("button", { name: /edit in clean conversation/i })
    ).toBeInTheDocument();
    expect(screen.getByText(/The target could not process this message\./)).toBeInTheDocument();
    expect(input).toHaveValue("keep failed draft");
    expect(input).toBeDisabled();
  });

  it("should let the latest same-conversation load control the transcript and loading state", async () => {
    const user = userEvent.setup();
    const olderResponse = makeTextResponse("older response").messages;
    const newerResponse = makeTextResponse("newer response").messages;
    let resolveOlderLoad: ((value: typeof olderResponse) => void) | undefined;
    let resolveNewerLoad: ((value: typeof newerResponse) => void) | undefined;

    mockedAttacksApi.getConversations.mockResolvedValue({
      main_conversation_id: "conv-latest-load",
      conversations: [
        {
          conversation_id: "conv-latest-load",
          message_count: 1,
        },
      ],
    } as never);
    mockedAttacksApi.getMessages
      .mockImplementationOnce(
        () => new Promise<typeof olderResponse>((resolve) => {
          resolveOlderLoad = resolve;
        }) as never
      )
      .mockImplementationOnce(
        () => new Promise<typeof newerResponse>((resolve) => {
          resolveNewerLoad = resolve;
        }) as never
      );
    mockedMapper.backendMessagesToFrontend.mockImplementation(
      actualMessageMapper.backendMessagesToFrontend
    );

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-latest-load"
          conversationId="conv-latest-load"
          activeConversationId="conv-latest-load"
          relatedConversationCount={1}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(1);
    });
    await user.click(
      await screen.findByRole("button", { name: "Select conversation conv-latest-load" })
    );
    await waitFor(() => {
      expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(2);
    });

    await act(async () => {
      resolveOlderLoad?.(olderResponse);
      await Promise.resolve();
    });

    expect(screen.queryByText("older response")).not.toBeInTheDocument();
    expect(screen.getByTestId("loading-state")).toBeInTheDocument();

    await act(async () => {
      resolveNewerLoad?.(newerResponse);
      await Promise.resolve();
    });

    expect(await screen.findByText("newer response")).toBeInTheDocument();
    expect(screen.queryByText("older response")).not.toBeInTheDocument();
    expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
  });

  it("should preserve an already-loaded transcript when a same-conversation refresh fails", async () => {
    const user = userEvent.setup();
    const loadedResponse = makeTextResponse("keep the loaded response").messages;

    mockedAttacksApi.getConversations.mockResolvedValue({
      main_conversation_id: "conv-refresh-failure",
      conversations: [
        {
          conversation_id: "conv-refresh-failure",
          message_count: 1,
        },
      ],
    } as never);
    mockedAttacksApi.getMessages
      .mockResolvedValueOnce(loadedResponse as never)
      .mockRejectedValueOnce(new Error("refresh failed"));
    mockedMapper.backendMessagesToFrontend.mockImplementation(
      actualMessageMapper.backendMessagesToFrontend
    );

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-refresh-failure"
          conversationId="conv-refresh-failure"
          activeConversationId="conv-refresh-failure"
          relatedConversationCount={1}
        />
      </TestWrapper>
    );

    expect(await screen.findByText("keep the loaded response")).toBeInTheDocument();
    await user.click(
      await screen.findByRole("button", { name: "Select conversation conv-refresh-failure" })
    );
    await waitFor(() => {
      expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(2);
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    expect(screen.getByText("keep the loaded response")).toBeInTheDocument();
  });

  it.each<PromptResponseError | null>(["none", "blocked", "empty", "unknown", null])(
    "should clear live recovery when a refresh reports response status %s",
    async (latestError) => {
      const user = userEvent.setup();
      const failedResponse = makeErrorResponse("processing", "The target could not process this message.", 2);
      const latestResponse = makeErrorResponse(latestError ?? "none", "", 4).messages;
      mockedAttacksApi.getConversations.mockResolvedValue({
        main_conversation_id: "conv-status-refresh",
        conversations: [{ conversation_id: "conv-status-refresh", message_count: 2 }],
      });
      mockedAttacksApi.getMessages.mockResolvedValueOnce({ messages: [] } as never);
      mockedAttacksApi.addMessage.mockResolvedValue(failedResponse as never);
      mockedMapper.buildMessagePieces.mockResolvedValue([
        { data_type: "text", original_value: "live draft" },
      ]);
      mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);

      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            attackResultId="ar-status-refresh"
            conversationId="conv-status-refresh"
            activeConversationId="conv-status-refresh"
            relatedConversationCount={1}
          />
        </TestWrapper>
      );

      const input = screen.getByRole("textbox");
      await user.type(input, "live draft");
      await user.click(screen.getByRole("button", { name: /send/i }));
      expect(await screen.findByRole("button", { name: /edit in clean conversation/i })).toBeEnabled();

      mockedAttacksApi.getMessages.mockResolvedValue({
        conversation_id: "conv-status-refresh",
        messages: [
          ...failedResponse.messages.messages,
          ...latestResponse.messages.map((message) => (
            latestError === null && message.role === "assistant"
              ? { ...message, role: "simulated_assistant" }
              : message
          )),
        ],
        target_response_status: latestError === null ? null : latestResponse.target_response_status,
      });
      await user.click(
        await screen.findByRole("button", { name: "Select conversation conv-status-refresh" })
      );

      await waitFor(() => {
        expect(screen.queryByRole("button", { name: /edit in clean conversation/i })).not.toBeInTheDocument();
        expect(input).toBeEnabled();
      });
      expect(input).toHaveValue("live draft");
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledTimes(1);
      expect(mockedAttacksApi.createConversation).not.toHaveBeenCalled();
    }
  );

  it.each([
    { requestTurn: 4, responseTurn: 5 },
    { requestTurn: 2, responseTurn: 4 },
  ])(
    "should replace live recovery for a different failed turn pair $requestTurn/$responseTurn",
    async ({ requestTurn, responseTurn }) => {
      const user = userEvent.setup();
      const onSelectConversation = jest.fn();
      const failedResponse = makeErrorResponse("processing", "The target could not process this message.", 2);
      const newerResponse = makeErrorResponse("processing", "A later response failed.", requestTurn).messages;
      newerResponse.messages[1].turn_number = responseTurn;
      newerResponse.target_response_status.response_turn_number = responseTurn;
      mockedAttacksApi.getConversations.mockResolvedValue({
        main_conversation_id: "conv-newer-failure",
        conversations: [{ conversation_id: "conv-newer-failure", message_count: 2 }],
      });
      mockedAttacksApi.getMessages.mockResolvedValueOnce({ messages: [] } as never);
      mockedAttacksApi.addMessage.mockResolvedValue(failedResponse as never);
      mockedAttacksApi.createConversation.mockResolvedValue({
        conversation_id: "conv-newer-recovery",
        created_at: "2026-01-01T00:00:04Z",
      });
      mockedMapper.buildMessagePieces.mockResolvedValue([
        { data_type: "text", original_value: "failed request" },
      ]);
      mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);

      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            attackResultId="ar-newer-failure"
            conversationId="conv-newer-failure"
            activeConversationId="conv-newer-failure"
            relatedConversationCount={1}
            onSelectConversation={onSelectConversation}
          />
        </TestWrapper>
      );

      await user.type(screen.getByRole("textbox"), "failed request");
      await user.click(screen.getByRole("button", { name: /send/i }));
      expect(await screen.findByRole("button", { name: /edit in clean conversation/i })).toBeEnabled();

      mockedAttacksApi.getMessages.mockResolvedValue({
        conversation_id: "conv-newer-failure",
        messages: [
          ...failedResponse.messages.messages,
          ...newerResponse.messages.filter((message) => (
            requestTurn !== 2 || message.role === "assistant"
          )),
        ],
        target_response_status: newerResponse.target_response_status,
      });
      await user.click(
        await screen.findByRole("button", { name: "Select conversation conv-newer-failure" })
      );

      expect(await screen.findByText(/restored from conversation history/i)).toBeInTheDocument();
      const recoveryButton = screen.getByRole("button", { name: /edit in clean conversation/i });
      expect(recoveryButton).toHaveAttribute(
        "data-testid",
        `recover-processing-error-btn-${requestTurn === 2 ? 2 : 3}`
      );
      await user.click(recoveryButton);
      await waitFor(() => {
        expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(
          "ar-newer-failure",
          { source_conversation_id: "conv-newer-failure", cutoff_index: 1 }
        );
        expect(onSelectConversation).toHaveBeenCalledWith("conv-newer-recovery");
      });
    }
  );

  it("should retain the matching live draft and converters while updating its recovery position", async () => {
    const user = userEvent.setup();
    const onSelectConversation = jest.fn();
    const failedResponse = makeErrorResponse("processing", "The target could not process this message.", 2, true);
    const file = new File(["image content"], "live.png", { type: "image/png" });
    Object.assign(failedResponse.messages.messages[0].message_pieces[0], {
      original_value: "live draft",
      converted_value: "/converted/live.pdf",
      converted_value_data_type: "binary_path",
    });
    failedResponse.messages.messages[0].message_pieces.push({
      id: "p-live-image",
      original_value_data_type: "image_path",
      converted_value_data_type: "image_path",
      original_value: "/original/live.png",
      converted_value: "/original/live.png",
      original_filename: "live.png",
      converted_filename: "live.png",
      scores: [],
      response_error: "none",
    });
    const props = {
      ...defaultProps,
      activeTarget: makeTarget({
        capabilities: buildCapabilities({
          supports_multi_message_pieces: true,
          supported_input_modalities: ["text", "image_path", "binary_path"],
        }),
      }),
      attackResultId: "ar-matching-failure",
      conversationId: "conv-matching-failure",
      activeConversationId: "conv-matching-failure",
      relatedConversationCount: 1,
      onSelectConversation,
    };
    mockedAttacksApi.getConversations.mockResolvedValue({
      main_conversation_id: props.conversationId,
      conversations: [{ conversation_id: props.conversationId, message_count: 2 }],
    });
    mockedAttacksApi.getMessages.mockResolvedValueOnce({ messages: [] } as never);
    mockedAttacksApi.addMessage.mockResolvedValue(failedResponse as never);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "conv-matching-recovery",
      created_at: "2026-01-01T00:00:04Z",
    });
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [{
        converter_type: "PDFConverter",
        supported_input_types: ["text"],
        supported_output_types: ["binary_path"],
        parameters: [],
      }],
    });
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: "live-pdf-converter",
      converter_type: "PDFConverter",
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      converted_value: "/converted/live.pdf",
      converted_value_data_type: "binary_path",
    });

    const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    await user.type(screen.getByRole("textbox"), "live draft");
    await user.upload(screen.getByTestId("file-input"), file);
    await user.click(screen.getByRole("button", { name: /convert/i }));
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /PDFConverter/ }));
    await user.click(screen.getByRole("button", { name: /^preview$/i }));
    await user.click(await screen.findByRole("button", { name: /use converted/i }));
    await user.click(screen.getByRole("button", { name: /send message/i }));
    expect(await screen.findByRole("button", { name: /edit in clean conversation/i })).toBeEnabled();

    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: props.conversationId,
      messages: [
        ...makeErrorResponse("processing", "Earlier target failure", 0).messages.messages,
        ...failedResponse.messages.messages,
      ],
      target_response_status: failedResponse.messages.target_response_status,
    });
    await user.click(
      await screen.findByRole("button", { name: "Select conversation conv-matching-failure" })
    );
    const recoveryButton = await screen.findByTestId("recover-processing-error-btn-3");
    expect(screen.getByText(/converter choices are preserved/i)).toBeInTheDocument();
    await user.click(recoveryButton);
    await waitFor(() => {
      expect(onSelectConversation).toHaveBeenCalledWith("conv-matching-recovery");
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(props.attackResultId, {});
    });
    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "conv-matching-recovery",
      messages: [],
      target_response_status: null,
    });
    rendered.rerender(
      <TestWrapper><ChatWindow {...props} activeConversationId="conv-matching-recovery" /></TestWrapper>
    );

    expect(await screen.findByText(/live\.png/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("live draft");
    expect(await screen.findByTestId("converted-file-chip")).toHaveTextContent("live.pdf");
    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => {
      expect(mockedAttacksApi.addMessage).toHaveBeenLastCalledWith(
        props.attackResultId,
        expect.objectContaining({
          target_conversation_id: "conv-matching-recovery",
          converter_ids: ["live-pdf-converter"],
        })
      );
    });
    expect(mockedMapper.buildMessagePieces).toHaveBeenLastCalledWith(
      "live draft",
      [expect.objectContaining({ file, name: "live.png" })]
    );
  });

  it.each(["image_path", "binary_path"])("should preview a new converter after restoring persisted %s media", async (originalDataType) => {
    const user = userEvent.setup();
    const onSelectConversation = jest.fn();
    const recoveryTarget = makeTarget({
      capabilities: buildCapabilities({
        supports_multi_message_pieces: true,
        supported_input_modalities: ["text", "image_path", "binary_path"],
      }),
    });
    const persistedMessages: BackendMessage[] = [
      {
        turn_number: 2,
        role: "user",
        message_pieces: [
          {
            id: "p-text-to-pdf",
            original_value_data_type: "text",
            converted_value_data_type: "binary_path",
            original_value: "original persisted prompt",
            converted_value: "/converted/report.pdf",
            converted_value_mime_type: "application/pdf",
            converted_filename: "converted.pdf",
            converter_identifiers: [{ type: "PDFConverter" }],
            scores: [],
            response_error: "none",
          },
          {
            id: "p-image-to-text",
            original_value_data_type: originalDataType,
            converted_value_data_type: "text",
            original_value: "/original/evidence.png",
            original_value_url: "/api/media?path=%2Foriginal%2Fevidence.png",
            original_value_mime_type: "image/png",
            original_filename: "evidence.png",
            converted_value: "converted image description",
            converter_identifiers: [{ type: "ImageToTextConverter" }],
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        turn_number: 3,
        role: "assistant",
        message_pieces: [
          {
            id: "p-processing-error",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: "",
            converted_value: "",
            scores: [],
            response_error: "processing",
            response_error_description: "The target could not process this message.",
          },
        ],
        created_at: "2026-01-01T00:00:01Z",
      },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "conv-persisted-processing",
      messages: persistedMessages,
      target_response_status: {
        response_error: "processing",
        request_turn_number: 2,
        response_turn_number: 3,
      },
    } as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "",
        attachments: [
          {
            type: "file",
            name: "converted.pdf",
            url: "/converted/report.pdf",
            mimeType: "application/pdf",
          },
        ],
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        error: {
          type: "processing",
          description: "The target could not process this message.",
        },
      },
    ]);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "conv-persisted-recovery",
    } as never);

    const rendered = render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={recoveryTarget}
          attackResultId="ar-persisted-processing"
          conversationId="conv-persisted-processing"
          activeConversationId="conv-persisted-processing"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    const recoveryButton = await screen.findByRole(
      "button",
      { name: /edit in clean conversation/i }
    );
    expect(screen.getByText(/converter choices could not be restored/i)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeDisabled();

    await user.click(recoveryButton);
    await waitFor(() => {
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(
        "ar-persisted-processing",
        {
          source_conversation_id: "conv-persisted-processing",
          cutoff_index: 1,
        }
      );
      expect(onSelectConversation).toHaveBeenCalledWith("conv-persisted-recovery");
    });

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] } as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    rendered.rerender(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={recoveryTarget}
          attackResultId="ar-persisted-processing"
          conversationId="conv-persisted-processing"
          activeConversationId="conv-persisted-recovery"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    const restoredInput = await screen.findByRole("textbox");
    expect(restoredInput).toHaveValue("original persisted prompt");
    expect(screen.getAllByText("evidence.png", { exact: false })).toHaveLength(1);
    expect(screen.queryByText(/converted\.pdf/i)).not.toBeInTheDocument();

    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [{
        converter_type: "RecoveredMediaConverter",
        supported_input_types: [originalDataType],
        supported_output_types: [originalDataType],
        parameters: [],
      }],
    });
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: "recovered-media-converter",
      converter_type: "RecoveredMediaConverter",
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      converted_value: "/converted/evidence.png",
      converted_value_data_type: originalDataType,
    });
    await user.click(screen.getByRole("button", { name: /convert/i }));
    await user.click(screen.getByRole("tab", { name: originalDataType === "image_path" ? "Image" : "file" }));
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /RecoveredMediaConverter/ }));
    const previewButton = screen.getByRole("button", { name: /^preview$/i });
    expect(previewButton).toBeEnabled();
    await user.click(previewButton);
    await waitFor(() => {
      expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith({
        original_value: "/original/evidence.png",
        original_value_data_type: originalDataType,
        converter_ids: ["recovered-media-converter"],
      });
    });
    await user.click(await screen.findByRole("button", { name: /use converted/i }));

    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("recovered") as never);
    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => {
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith(
        "ar-persisted-processing",
        expect.objectContaining({
          target_conversation_id: "conv-persisted-recovery",
          converter_ids: ["recovered-media-converter"],
          pieces: expect.arrayContaining([
            expect.objectContaining({
              data_type: originalDataType,
              original_value: "/original/evidence.png",
            }),
          ]),
        })
      );
    });
  });

  it("should not recover a historical processing error after a later successful response", async () => {
    const historicalFailure = makeErrorResponse(
      "processing",
      "The target could not process this message."
    );
    const laterUser: BackendMessage = {
      turn_number: 2,
      role: "user",
      message_pieces: [
        {
          id: "p-later-user",
          original_value_data_type: "text",
          converted_value_data_type: "text",
          original_value: "later request",
          converted_value: "later request",
          scores: [],
          response_error: "none",
        },
      ],
      created_at: "2026-01-01T00:00:02Z",
    };
    const laterAssistant: BackendMessage = {
      turn_number: 3,
      role: "assistant",
      message_pieces: [
        {
          id: "p-later-assistant",
          original_value_data_type: "text",
          converted_value_data_type: "text",
          original_value: "latest success",
          converted_value: "latest success",
          scores: [],
          response_error: "none",
        },
      ],
      created_at: "2026-01-01T00:00:03Z",
    };

    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "conv-stale-processing",
      messages: [
        ...historicalFailure.messages.messages,
        laterUser,
        laterAssistant,
      ],
      target_response_status: {
        response_error: "none",
        request_turn_number: 2,
        response_turn_number: 3,
      },
    } as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "failed request",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        error: {
          type: "processing",
          description: "The target could not process this message.",
        },
      },
      {
        role: "user",
        content: "later request",
        timestamp: "2026-01-01T00:00:02Z",
      },
      {
        role: "assistant",
        content: "latest success",
        timestamp: "2026-01-01T00:00:03Z",
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-stale-processing"
          conversationId="conv-stale-processing"
          activeConversationId="conv-stale-processing"
        />
      </TestWrapper>
    );

    expect(await screen.findByText("latest success")).toBeInTheDocument();
    expect(screen.queryByTestId(/^recover-processing-error-btn-/)).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeEnabled();
  });

  it("should not treat simulated assistant history as a target processing failure", async () => {
    const processingResponse = makeErrorResponse(
      "processing",
      "The target could not process this message."
    );
    const simulatedMessages = processingResponse.messages.messages.map(
      (message, index) => index === 1
        ? { ...message, role: "simulated_assistant" }
        : message
    );

    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "conv-simulated-processing",
      messages: simulatedMessages,
      target_response_status: null,
    } as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "failed request",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "simulated_assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        error: {
          type: "processing",
          description: "The target could not process this message.",
        },
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-simulated-processing"
          conversationId="conv-simulated-processing"
          activeConversationId="conv-simulated-processing"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(mockedMapper.backendMessagesToFrontend).toHaveBeenCalled();
    });
    expect(screen.queryByTestId(/^recover-processing-error-btn-/)).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeEnabled();
  });

  it("should clear an unchanged submitted draft after switching conversations", async () => {
    const user = userEvent.setup();
    const response = makeTextResponse("Reply from conversation A");
    let resolveMessage: ((value: typeof response) => void) | undefined;

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] } as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "conversation A draft" },
    ]);
    mockedAttacksApi.addMessage.mockImplementation(
      () => new Promise<typeof response>((resolve) => {
        resolveMessage = resolve;
      }) as never
    );

    const rendered = render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conversation-switch"
          conversationId="conv-a"
          activeConversationId="conv-a"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(mockedAttacksApi.getMessages).toHaveBeenCalledWith(
        "ar-conversation-switch",
        "conv-a"
      );
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "conversation A draft");
    await user.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(mockedAttacksApi.addMessage).toHaveBeenCalledTimes(1));

    rendered.rerender(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conversation-switch"
          conversationId="conv-a"
          activeConversationId="conv-b"
        />
      </TestWrapper>
    );
    await waitFor(() => {
      expect(mockedAttacksApi.getMessages).toHaveBeenCalledWith(
        "ar-conversation-switch",
        "conv-b"
      );
    });

    await act(async () => {
      resolveMessage?.(response);
      await Promise.resolve();
    });

    expect(input).toHaveValue("");
  });

  it("should not overwrite another conversation when recovery completes after navigation", async () => {
    const user = userEvent.setup();
    const onSelectConversation = jest.fn();
    let resolveConversation: ((value: { conversation_id: string }) => void) | undefined;

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "failed draft" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(
      makeErrorResponse(
        "processing",
        "The target could not process this message.",
        2
      ) as never
    );
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "failed draft",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        error: {
          type: "processing",
          description: "The target could not process this message.",
        },
      },
    ]);
    mockedAttacksApi.createConversation.mockImplementation(
      () => new Promise<{ conversation_id: string }>((resolve) => {
        resolveConversation = resolve;
      }) as never
    );

    const rendered = render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-recovery-navigation"
          conversationId="conv-failed"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "failed draft");
    await user.click(screen.getByRole("button", { name: /send/i }));
    const recoveryButton = await screen.findByRole(
      "button",
      { name: /edit in clean conversation/i }
    );

    await user.click(recoveryButton);
    await waitFor(() => {
      expect(recoveryButton).toBeDisabled();
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledTimes(1);
    });
    await user.click(recoveryButton);
    expect(mockedAttacksApi.createConversation).toHaveBeenCalledTimes(1);

    rendered.rerender(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-recovery-navigation"
          conversationId="conv-failed"
          activeConversationId="conv-other"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );
    await waitFor(() => expect(input).toBeEnabled());
    await user.clear(input);
    await user.type(input, "newer conversation draft");

    await act(async () => {
      resolveConversation?.({ conversation_id: "conv-unused-recovery" });
      await Promise.resolve();
    });

    expect(onSelectConversation).not.toHaveBeenCalled();
    expect(input).toHaveValue("newer conversation draft");
  });

  it("should handle blocked response from target", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "bad prompt" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(
      makeErrorResponse("blocked", "Content was filtered by safety system") as never
    );
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        error: {
          type: "blocked",
          description: "Content was filtered by safety system",
        },
      },
    ]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-block"
          conversationId="conv-block"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "bad prompt");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Content was filtered by safety system/)).toBeInTheDocument();
      expect(input).toHaveValue("");
    });
    expect(screen.queryByTestId(/^recover-processing-error-btn-/)).not.toBeInTheDocument();
  });

  it("should restore a single-turn draft in a new conversation after a processing error", async () => {
    const user = userEvent.setup();
    const onSelectConversation = jest.fn();
    const singleTurnTarget: TargetInstance = makeTarget({
      target_registry_name: "single-turn-target",
      target_type: "OpenAIImageTarget",
      capabilities: buildCapabilities({ supports_multi_turn: false }),
    });

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "generate this image" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(
      makeErrorResponse("processing", "The target could not process this message.") as never
    );
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "generate this image",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "",
        timestamp: "2026-01-01T00:00:01Z",
        error: {
          type: "processing",
          description: "The target could not process this message.",
        },
      },
    ]);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "conv-single-recovery",
    } as never);

    const rendered = render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={singleTurnTarget}
          attackResultId="ar-single-processing"
          conversationId="conv-single-original"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "generate this image");
    await user.click(screen.getByRole("button", { name: /send/i }));

    const recoveryButton = await screen.findByRole(
      "button",
      { name: /edit in new conversation/i }
    );
    expect(screen.getByTestId("single-turn-banner")).toBeInTheDocument();

    await user.click(recoveryButton);
    await waitFor(() => {
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(
        "ar-single-processing",
        {}
      );
      expect(onSelectConversation).toHaveBeenCalledWith("conv-single-recovery");
    });

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] } as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    rendered.rerender(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={singleTurnTarget}
          attackResultId="ar-single-processing"
          conversationId="conv-single-original"
          activeConversationId="conv-single-recovery"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    const restoredInput = await screen.findByRole("textbox");
    expect(restoredInput).toHaveValue("generate this image");
  });

  // -----------------------------------------------------------------------
  // Multi-turn conversation
  // -----------------------------------------------------------------------

  it("should support multi-turn: create on first, reuse on second", async () => {
    const user = userEvent.setup();
    const onConversationCreated = jest.fn();

    // First message
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Turn 1" },
    ]);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-conv-multi-turn",
      conversation_id: "conv-multi-turn",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("Reply 1") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "Turn 1",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "Reply 1",
        timestamp: "2026-01-01T00:00:01Z",
      },
    ]);

    const { rerender } = render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          conversationId={null}
          onConversationCreated={onConversationCreated}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Turn 1");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).toHaveBeenCalledTimes(1);
      expect(onConversationCreated).toHaveBeenCalledWith("ar-conv-multi-turn", "conv-multi-turn", undefined);
    });

    // Now rerender with the conversation ID set (simulating parent state update)
    jest.clearAllMocks();
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Turn 2" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("Reply 2") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      {
        role: "user",
        content: "Turn 1",
        timestamp: "2026-01-01T00:00:00Z",
      },
      {
        role: "assistant",
        content: "Reply 1",
        timestamp: "2026-01-01T00:00:01Z",
      },
      {
        role: "user",
        content: "Turn 2",
        timestamp: "2026-01-01T00:00:02Z",
      },
      {
        role: "assistant",
        content: "Reply 2",
        timestamp: "2026-01-01T00:00:03Z",
      },
    ]);

    rerender(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-multi-turn"
          conversationId="conv-multi-turn"
          onConversationCreated={onConversationCreated}
        />
      </TestWrapper>
    );

    await user.type(screen.getByRole("textbox"), "Turn 2");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).not.toHaveBeenCalled();
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith(
        "ar-conv-multi-turn",
        expect.objectContaining({
          pieces: [{ data_type: "text", original_value: "Turn 2" }],
          target_conversation_id: "conv-multi-turn",
        })
      );
    });
  });

  // -----------------------------------------------------------------------
  // Multi-turn with mixed modalities
  // -----------------------------------------------------------------------

  it("should support sending text first then image in second turn", async () => {
    const user = userEvent.setup();

    // Turn 1: text
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Hello" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("Hi!") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      { role: "assistant", content: "Hi!", timestamp: "2026-01-01T00:00:01Z" },
    ]);

    const { rerender } = render(
      <TestWrapper>
        <ChatWindow {...defaultProps} attackResultId="ar-conv-mixed-turns" conversationId="conv-mixed-turns" />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Hello");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledTimes(1);
    });

    // Turn 2: text + image
    jest.clearAllMocks();
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "What is this?" },
      { data_type: "image_path", original_value: "base64data", mime_type: "image/png" },
    ]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("A cat") as never);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([
      { role: "assistant", content: "A cat", timestamp: "2026-01-01T00:00:02Z" },
    ]);

    rerender(
      <TestWrapper>
        <ChatWindow {...defaultProps} attackResultId="ar-conv-mixed-turns" conversationId="conv-mixed-turns" />
      </TestWrapper>
    );

    await user.type(screen.getByRole("textbox"), "What is this?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith(
        "ar-conv-mixed-turns",
        expect.objectContaining({
          pieces: [
            { data_type: "text", original_value: "What is this?" },
            { data_type: "image_path", original_value: "base64data", mime_type: "image/png" },
          ],
          target_conversation_id: "conv-mixed-turns",
        })
      );
    });
  });

  // -----------------------------------------------------------------------
  // No message sent when target is null (guard)
  // -----------------------------------------------------------------------

  it("should show no-target banner when active target is null", () => {
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={null} />
      </TestWrapper>
    );

    // ChatInputArea shows banner instead of textbox
    expect(screen.getByTestId("no-target-banner")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Single-turn target UX
  // -----------------------------------------------------------------------

  it("should show single-turn banner for single-turn target with existing user messages", async () => {
    const singleTurnTarget: TargetInstance = makeTarget({
      target_registry_name: "openai_image_1",
      target_type: "OpenAIImageTarget",
      capabilities: buildCapabilities({ supports_multi_turn: false }),
    });

    const messagesWithUser: Message[] = [
      { role: "user", content: "Generate an image", timestamp: "2026-01-01T00:00:00Z" },
      { role: "assistant", content: "Here is the image", timestamp: "2026-01-01T00:00:01Z" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(messagesWithUser);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={singleTurnTarget}
          attackResultId="ar-conv-single"
          conversationId="conv-single"
          activeConversationId="conv-single"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("single-turn-banner")).toBeInTheDocument();
      expect(screen.getByText(/only supports single-turn/)).toBeInTheDocument();
    });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("should not show single-turn banner for single-turn target with no messages", () => {
    const singleTurnTarget: TargetInstance = makeTarget({
      target_registry_name: "openai_image_1",
      target_type: "OpenAIImageTarget",
      capabilities: buildCapabilities({ supports_multi_turn: false }),
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={singleTurnTarget}
          conversationId="conv-single"
          activeConversationId="conv-single"
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("single-turn-banner")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("should not show single-turn banner for multiturn target with messages", async () => {
    const messagesWithUser: Message[] = [
      { role: "user", content: "Hello", timestamp: "2026-01-01T00:00:00Z" },
      { role: "assistant", content: "Hi there", timestamp: "2026-01-01T00:00:01Z" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(messagesWithUser);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-multi"
          conversationId="conv-multi"
          activeConversationId="conv-multi"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("Hello")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("single-turn-banner")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  it("should show New Conversation button in single-turn banner when conversation exists", async () => {
    const singleTurnTarget: TargetInstance = makeTarget({
      target_registry_name: "openai_tts_1",
      target_type: "OpenAITTSTarget",
      capabilities: buildCapabilities({ supports_multi_turn: false }),
    });

    const messagesWithUser: Message[] = [
      { role: "user", content: "Say hello", timestamp: "2026-01-01T00:00:00Z" },
      { role: "assistant", content: "Audio output", timestamp: "2026-01-01T00:00:01Z" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(messagesWithUser);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={singleTurnTarget}
          attackResultId="ar-conv-tts"
          conversationId="conv-tts"
          activeConversationId="conv-tts"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("new-conversation-btn")).toBeInTheDocument();
    });
  });

  it("should show cross-target banner when attackTarget differs from activeTarget", () => {
    const differentTarget: TargetInfo = {
      target_type: "AzureOpenAIChatTarget",
      endpoint: "https://azure.openai.com",
      model_name: "gpt-4o",
      identifier_hash: "different-target-hash",
    };

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-cross"
          conversationId="conv-cross"
          attackTarget={differentTarget}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("cross-target-banner")).toBeInTheDocument();
  });

  it("should not show cross-target banner when attackTarget matches activeTarget", () => {
    const sameTarget: TargetInfo = {
      target_type: mockTarget.identifier.class_name,
      endpoint: mockTarget.identifier.endpoint,
      model_name: mockTarget.identifier.model_name,
      identifier_hash: mockTarget.identifier.hash,
    };

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-same"
          conversationId="conv-same"
          attackTarget={sameTarget}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("cross-target-banner")).not.toBeInTheDocument();
  });

  it("should keep a historical Round Robin attack writable when the identifier hash matches", () => {
    const roundRobinTarget = makeTarget({
      target_registry_name: "round-robin",
      target_type: "RoundRobinTarget",
      endpoint: null,
      model_name: null,
      identifier_hash: "round-robin-hash",
      inner_targets: [
        { target_registry_name: "inner-a", model_name: "e2e-dummy-model" },
        { target_registry_name: "inner-b", model_name: "e2e-dummy-model" },
      ],
    });
    const historicalTarget: TargetInfo = {
      target_type: "RoundRobinTarget",
      endpoint: null,
      model_name: null,
      identifier_hash: "round-robin-hash",
    };

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={roundRobinTarget}
          attackResultId="ar-round-robin"
          conversationId="conv-round-robin"
          attackTarget={historicalTarget}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("cross-target-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-input")).toBeEnabled();
  });

  it("should lock a historical Round Robin attack when the composite identifier hash differs", () => {
    const roundRobinTarget = makeTarget({
      target_registry_name: "round-robin",
      target_type: "RoundRobinTarget",
      endpoint: null,
      model_name: null,
      identifier_hash: "active-round-robin-hash",
      inner_targets: [
        { target_registry_name: "inner-a", model_name: "e2e-dummy-model" },
        { target_registry_name: "inner-b", model_name: "e2e-dummy-model" },
      ],
    });
    const historicalTarget: TargetInfo = {
      target_type: "RoundRobinTarget",
      endpoint: null,
      model_name: null,
      identifier_hash: "different-round-robin-hash",
    };

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={roundRobinTarget}
          attackResultId="ar-round-robin"
          conversationId="conv-round-robin"
          attackTarget={historicalTarget}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("cross-target-banner")).toBeInTheDocument();
    expect(screen.queryByTestId("chat-input")).not.toBeInTheDocument();
  });

  it("should auto-open conversation panel when relatedConversationCount > 0", async () => {
    mockedAttacksApi.getRelatedConversations.mockResolvedValue({
      conversations: [
        { conversation_id: "conv-main", is_main: true },
        { conversation_id: "conv-related", is_main: false },
      ],
    });
    mockedAttacksApi.getMessages.mockResolvedValue({
      messages: [],
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-multi"
          conversationId="conv-main"
          activeConversationId="conv-main"
          relatedConversationCount={2}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("conversation-panel")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("complementary", { name: "Attack Conversations" })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("dialog", { name: "Attack Conversations" })
    ).not.toBeInTheDocument();
  });

  it("should not auto-open conversation panel when relatedConversationCount is 0", () => {
    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-single"
          conversationId="conv-only"
          activeConversationId="conv-only"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("conversation-panel")).not.toBeInTheDocument();
  });

  it("should open conversation panel when branching a conversation", async () => {
    const mockMessages: Message[] = [
      { role: "user", content: "hello", data_type: "text" },
      { role: "assistant", content: "hi there", data_type: "text" },
    ];

    // Mock getMessages so loadConversation resolves and clears loading state
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "new-conv-branched",
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-branch"
          conversationId="conv-main"
          activeConversationId="conv-main"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    // Wait for loading to complete (loadConversation resolves)
    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    // Panel should NOT be open initially
    expect(screen.queryByTestId("conversation-panel")).not.toBeInTheDocument();

    // Click the branch-conversation button on the assistant message (index 1)
    const branchBtn = screen.getByTestId("branch-conv-btn-1");
    await userEvent.click(branchBtn);

    // Panel should now be open
    await waitFor(() => {
      expect(screen.getByTestId("conversation-panel")).toBeInTheDocument();
    });
  });

  it("should keep the mobile drawer closed until requested and restore focus after Escape", async () => {
    const user = userEvent.setup();
    mockMatchMedia(true);
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedAttacksApi.getConversations.mockResolvedValue({
      main_conversation_id: "conv-mobile",
      conversations: [
        {
          conversation_id: "conv-mobile",
          is_main: true,
          message_count: 1,
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-mobile"
          conversationId="conv-mobile"
          activeConversationId="conv-mobile"
          relatedConversationCount={1}
        />
      </TestWrapper>
    );

    const toggleButton = screen.getByRole("button", {
      name: "Toggle conversations panel",
    });
    expect(
      screen.queryByRole("dialog", { name: "Attack Conversations" })
    ).not.toBeInTheDocument();
    expect(toggleButton).toHaveAttribute("aria-expanded", "false");

    await user.click(toggleButton);

    expect(
      await screen.findByRole("dialog", { name: "Attack Conversations" })
    ).toBeInTheDocument();
    expect(toggleButton).toHaveAttribute("aria-expanded", "true");

    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", { name: "Attack Conversations" })
      ).not.toBeInTheDocument();
    });
    expect(toggleButton).toHaveAttribute("aria-expanded", "false");
    expect(toggleButton).toHaveFocus();
  });

  it("should open conversation panel when copying to new conversation", async () => {
    const mockMessages: Message[] = [
      { role: "user", content: "hello", data_type: "text" },
      { role: "assistant", content: "hi there", data_type: "text" },
    ];

    // Mock getMessages so loadConversation resolves and clears loading state
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "new-conv-copied",
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-copy"
          conversationId="conv-main"
          activeConversationId="conv-main"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    // Wait for loading to complete
    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    // Panel should NOT be open initially
    expect(screen.queryByTestId("conversation-panel")).not.toBeInTheDocument();

    // Click the copy-to-new-conversation button on the assistant message (index 1)
    const copyBtn = screen.getByTestId("copy-to-new-conv-btn-1");
    await userEvent.click(copyBtn);

    // Panel should now be open
    await waitFor(() => {
      expect(screen.getByTestId("conversation-panel")).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // handleNewConversation
  // -----------------------------------------------------------------------

  it("should create a new conversation and select it via handleNewConversation", async () => {
    const onSelectConversation = jest.fn();
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "new-conv-from-new",
    });

    const singleTurnTarget: TargetInstance = makeTarget({
      target_registry_name: "openai_image_1",
      target_type: "OpenAIImageTarget",
      capabilities: buildCapabilities({ supports_multi_turn: false }),
    });

    const messagesWithUser: Message[] = [
      { role: "user", content: "Generate an image", timestamp: "2026-01-01T00:00:00Z" },
      { role: "assistant", content: "Here is the image", timestamp: "2026-01-01T00:00:01Z" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(messagesWithUser);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={singleTurnTarget}
          attackResultId="ar-new-conv"
          conversationId="conv-existing"
          activeConversationId="conv-existing"
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    // For single-turn targets with existing messages, there's a New Conversation button
    await waitFor(() => {
      expect(screen.getByTestId("new-conversation-btn")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("new-conversation-btn"));

    await waitFor(() => {
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith("ar-new-conv", {});
      expect(onSelectConversation).toHaveBeenCalledWith("new-conv-from-new");
    });
  });

  it("should not create conversation when attackResultId is null", async () => {
    const onSelectConversation = jest.fn();

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId={null}
          conversationId={null}
          activeConversationId={null}
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    // No new-conversation button should be available without an attackResultId
    expect(screen.queryByTestId("new-conversation-btn")).not.toBeInTheDocument();
    expect(mockedAttacksApi.createConversation).not.toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // handleCopyToInput
  // -----------------------------------------------------------------------

  it("should copy message content to input box via copy-to-input button", async () => {
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "This is the response text" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-copy-input"
          conversationId="conv-copy-input"
          activeConversationId="conv-copy-input"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    // Click copy-to-input on assistant message (index 1)
    const copyBtn = screen.getByTestId("copy-to-input-btn-1");
    await userEvent.click(copyBtn);

    // The text should appear in the input area
    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toBe("This is the response text");
    });
  });

  // -----------------------------------------------------------------------
  // handleCopyToNewConversation
  // -----------------------------------------------------------------------

  it("should create a new conversation and copy message when copy-to-new-conv is clicked", async () => {
    const user = userEvent.setup();
    const onSelectConversation = jest.fn();
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      {
        role: "assistant",
        content: "reply text to copy",
        attachments: [
          {
            type: "image",
            name: "first.png",
            url: "data:image/png;base64,aW1hZ2U=",
            mimeType: "image/png",
            pieceId: "piece-image",
            metadata: { source: "generated" },
          },
          {
            type: "file",
            name: "excluded.pdf",
            url: "data:application/pdf;base64,cGRm",
            mimeType: "application/pdf",
          },
          {
            type: "audio",
            name: "second.wav",
            url: "data:audio/wav;base64,YXVkaW8=",
            mimeType: "audio/wav",
            pieceId: "piece-audio",
            metadata: { voice: "alloy" },
          },
        ],
      },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "new-conv-copy",
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-copy-new"
          conversationId="conv-copy-new"
          activeConversationId="conv-copy-new"
          onSelectConversation={onSelectConversation}
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    const copyBtn = screen.getByTestId("copy-to-new-conv-btn-1");
    await user.click(copyBtn);

    await waitFor(() => {
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith("ar-copy-new", {});
      expect(onSelectConversation).toHaveBeenCalledWith("new-conv-copy");
    });
    expect(await screen.findByText(/first\.png/)).toBeInTheDocument();
    expect(screen.getByText(/second\.wav/)).toBeInTheDocument();
    expect(screen.getAllByTestId(/^remove-attachment-/)).toHaveLength(2);
  });

  it("should fall back when createConversation fails in copy-to-new-conversation", async () => {
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "fallback text" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);
    mockedAttacksApi.createConversation.mockRejectedValue(new Error("Failed"));

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-fail-copy"
          conversationId="conv-fail-copy"
          activeConversationId="conv-fail-copy"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    const copyBtn = screen.getByTestId("copy-to-new-conv-btn-1");
    await userEvent.click(copyBtn);

    // Should fall back to setting text in current input
    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toBe("fallback text");
    });
  });

  // -----------------------------------------------------------------------
  // handleBranchConversation
  // -----------------------------------------------------------------------

  it("should branch conversation and load cloned messages", async () => {
    const onSelectConversation = jest.fn();
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "response" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "branched-conv",
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-branch-test"
          conversationId="conv-branch-test"
          activeConversationId="conv-branch-test"
          onSelectConversation={onSelectConversation}
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    const branchBtn = screen.getByTestId("branch-conv-btn-1");
    await userEvent.click(branchBtn);

    await waitFor(() => {
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith("ar-branch-test", {
        source_conversation_id: "conv-branch-test",
        cutoff_index: 1,
      });
      expect(onSelectConversation).toHaveBeenCalledWith("branched-conv");
    });
  });

  // -----------------------------------------------------------------------
  // handleBranchAttack
  // -----------------------------------------------------------------------

  it("should branch into a new attack and load cloned messages", async () => {
    const onConversationCreated = jest.fn();
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "response" },
    ];
    const clonedMessages: Message[] = [
      { role: "user", content: "hello", timestamp: "2026-01-01T00:00:00Z" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-branch-attack"
          conversationId="conv-branch-attack"
          activeConversationId="conv-branch-attack"
          onConversationCreated={onConversationCreated}
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    // Set up mocks for the branch attack flow
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-new-branch",
      conversation_id: "conv-new-branch",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(clonedMessages);

    const branchBtn = screen.getByTestId("branch-attack-btn-1");
    await userEvent.click(branchBtn);

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(
        expect.objectContaining({
          target_registry_name: "openai_chat_1",
          source_conversation_id: "conv-branch-attack",
          cutoff_index: 1,
        })
      );
      expect(onConversationCreated).toHaveBeenCalledWith("ar-new-branch", "conv-new-branch");
    });
  });

  // -----------------------------------------------------------------------
  // handleChangeMainConversation
  // -----------------------------------------------------------------------

  it("should call changeMainConversation API via conversation panel", async () => {
    mockedAttacksApi.getConversations.mockResolvedValue({
      conversations: [
        { conversation_id: "conv-main", is_main: true, message_count: 2, created_at: "2026-01-01T00:00:00Z" },
        { conversation_id: "conv-alt", is_main: false, message_count: 1, created_at: "2026-01-01T00:01:00Z" },
      ],
      main_conversation_id: "conv-main",
    });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockedAttacksApi.changeMainConversation.mockResolvedValue({});

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-main-change"
          conversationId="conv-main"
          activeConversationId="conv-main"
          relatedConversationCount={2}
        />
      </TestWrapper>
    );

    // Panel should auto-open due to relatedConversationCount > 0
    await waitFor(() => {
      expect(screen.getByTestId("conversation-panel")).toBeInTheDocument();
    });

    // Wait for conversations to load in panel
    await waitFor(() => {
      expect(screen.getByTestId("star-btn-conv-alt")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("star-btn-conv-alt"));

    await waitFor(() => {
      expect(mockedAttacksApi.changeMainConversation).toHaveBeenCalledWith(
        "ar-main-change",
        "conv-alt"
      );
    });
  });

  // -----------------------------------------------------------------------
  // handleUseAsTemplate
  // -----------------------------------------------------------------------

  it("should create new attack from template when use-as-template button is clicked", async () => {
    const onConversationCreated = jest.fn();
    const existingMessages: Message[] = [
      { role: "user", content: "hello", timestamp: "2026-01-01T00:00:00Z" },
      {
        role: "assistant",
        content: "response",
        timestamp: "2026-01-01T00:00:01Z",
        displayPieces: [
          {
            type: "text",
            pieceId: "locked-response",
            pieceIndex: 0,
            content: "response",
            scores: [],
          },
        ],
      },
    ];

    const differentTarget: TargetInfo = {
      target_type: "AzureOpenAIChatTarget",
      endpoint: "https://azure.openai.com",
      model_name: "gpt-4o",
    };

    const templateMessages: Message[] = [
      { role: "user", content: "hello", timestamp: "2026-01-01T00:00:00Z" },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(existingMessages);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-template",
      conversation_id: "conv-template",
      created_at: "2026-01-01T00:00:00Z",
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-cross-template"
          conversationId="conv-cross-template"
          activeConversationId="conv-cross-template"
          attackTarget={differentTarget}
          onConversationCreated={onConversationCreated}
        />
      </TestWrapper>
    );

    // Cross-target banner should appear
    await waitFor(() => {
      expect(screen.getByTestId("cross-target-banner")).toBeInTheDocument();
    });

    // Reconfigure mocks for the template creation
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(templateMessages);

    const useTemplateBtn = screen.getByTestId("use-as-template-btn");
    await userEvent.click(useTemplateBtn);

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(
        expect.objectContaining({
          target_registry_name: "openai_chat_1",
          source_conversation_id: "conv-cross-template",
          cutoff_index: 1,
        })
      );
      expect(onConversationCreated).toHaveBeenCalledWith("ar-template", "conv-template");
    });
  });

  it("should show operator locked banner and use-as-template when operator differs", async () => {
    const existingMessages: Message[] = [
      { role: "user", content: "hello", timestamp: "2026-01-01T00:00:00Z" },
      {
        role: "assistant",
        content: "response",
        timestamp: "2026-01-01T00:00:01Z",
        displayPieces: [
          {
            type: "text",
            pieceId: "locked-response",
            pieceIndex: 0,
            content: "response",
            scores: [],
          },
        ],
      },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(existingMessages);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-locked"
          conversationId="conv-locked"
          activeConversationId="conv-locked"
          labels={{ operator: "alice", operation: "test_op" }}
          attackOperator="bob"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("operator-locked-banner")).toBeInTheDocument();
    });

    expect(screen.getByTestId("use-as-template-btn")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add manual score" })).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Cross-target locking rendering details
  // -----------------------------------------------------------------------

  it("should render conversation panel as locked when cross-target locked", async () => {
    const differentTarget: TargetInfo = {
      target_type: "AzureOpenAIChatTarget",
      endpoint: "https://azure.openai.com",
      model_name: "gpt-4o",
    };

    mockedAttacksApi.getRelatedConversations.mockResolvedValue({
      conversations: [
        { conversation_id: "conv-cross-panel", is_main: true, message_count: 2, created_at: "2026-01-01T00:00:00Z" },
      ],
    });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-cross-lock"
          conversationId="conv-cross-panel"
          activeConversationId="conv-cross-panel"
          attackTarget={differentTarget}
          relatedConversationCount={1}
        />
      </TestWrapper>
    );

    // Panel should auto-open and the cross-target banner should appear
    await waitFor(() => {
      expect(screen.getByTestId("conversation-panel")).toBeInTheDocument();
      expect(screen.getByTestId("cross-target-banner")).toBeInTheDocument();
    });
  });

  it("should not show cross-target banner when attackTarget is null", () => {
    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-no-cross"
          conversationId="conv-no-cross"
          attackTarget={null}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("cross-target-banner")).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Network error in handleSend
  // -----------------------------------------------------------------------

  it("should show network error when addMessage fails with network error", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "test" },
    ]);

    const networkError = new Error("Network Error") as Error & {
      isAxiosError: boolean;
      response: undefined;
      code: undefined;
    };
    networkError.isAxiosError = true;
    (networkError as Record<string, unknown>).response = undefined;
    mockedAttacksApi.addMessage.mockRejectedValue(networkError);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          conversationId="conv-net-err"
          attackResultId="ar-net-err"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "test");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument();
    });
  });

  it("should show timeout error when addMessage fails with timeout", async () => {
    const user = userEvent.setup();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "test" },
    ]);

    const timeoutError = new Error("timeout") as Error & {
      isAxiosError: boolean;
      code: string;
    };
    timeoutError.isAxiosError = true;
    (timeoutError as Record<string, unknown>).code = "ECONNABORTED";
    mockedAttacksApi.addMessage.mockRejectedValue(timeoutError);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          conversationId="conv-timeout"
          attackResultId="ar-timeout"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "test");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/timed out/)).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Toggle panel button
  // -----------------------------------------------------------------------

  it("should toggle conversation panel when toggle-panel button is clicked", async () => {
    mockedAttacksApi.getRelatedConversations.mockResolvedValue({
      conversations: [
        { conversation_id: "conv-toggle-main", is_main: true, message_count: 1, created_at: "2026-01-01T00:00:00Z" },
      ],
    });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-toggle"
          conversationId="conv-toggle-main"
          activeConversationId="conv-toggle-main"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    // Panel should not be open initially (relatedConversationCount=0)
    expect(screen.queryByTestId("conversation-panel")).not.toBeInTheDocument();

    // Click toggle button to open panel
    const toggleBtn = screen.getByTestId("toggle-panel-btn");
    await userEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.getByTestId("conversation-panel")).toBeInTheDocument();
    });

    // Click toggle button again to close panel
    await userEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.queryByTestId("conversation-panel")).not.toBeInTheDocument();
    });
  });

  it("should expose the conversations panel toggle via aria-label", () => {
    // Regression guard: the toggle button is icon-only and previously relied
    // on aria-label without a visible tooltip; assert both are wired up so
    // the button is reachable by accessible name (catches regression to
    // missing aria-label).
    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-aria-toggle"
          conversationId="conv-aria-toggle"
          activeConversationId="conv-aria-toggle"
        />
      </TestWrapper>
    );

    const toggleBtn = screen.getByRole("button", { name: /toggle conversations panel/i });
    expect(toggleBtn).toBe(screen.getByTestId("toggle-panel-btn"));
  });

  it("should toggle converter panel when convert button is clicked", async () => {
    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-converter-panel"
          conversationId="conv-converter-panel"
          activeConversationId="conv-converter-panel"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("converter-panel")).not.toBeInTheDocument();

    const toggleBtn = screen.getByTestId("toggle-converter-panel-btn");
    await userEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.getByTestId("converter-panel")).toBeInTheDocument();
    });

    await userEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.queryByTestId("converter-panel")).not.toBeInTheDocument();
    });
  });

  it("should load and display converters in the converter panel", async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [
        {
          converter_type: "Base64Converter",
          supported_input_types: ["text"],
          supported_output_types: ["text"],
          parameters: [],
        },
      ],
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-converter-list"
          conversationId="conv-converter-list"
          activeConversationId="conv-converter-list"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("converter-panel-list")).toBeInTheDocument();
      expect(screen.getByTestId("converter-panel-select")).toBeInTheDocument();
      expect(screen.getByTestId("converter-preview-btn")).toBeInTheDocument();
      expect(screen.getByText("Converted output will appear here.")).toBeInTheDocument();
    });

    // Select a converter
    const input = screen.getByRole("combobox");
    await userEvent.click(input);
    const option = await screen.findByRole("option", { name: /Base64Converter/ });
    await userEvent.click(option);

    await waitFor(() => {
      expect(screen.getByTestId("converter-item-Base64Converter")).toBeInTheDocument();
      expect(screen.getByText("In:")).toBeInTheDocument();
      expect(screen.getByText("Out:")).toBeInTheDocument();
      expect(screen.getByTestId("converter-output")).toBeInTheDocument();
      // No params section when parameters is empty
      expect(screen.queryByTestId("converter-params")).not.toBeInTheDocument();
    });
  });

  it("should show parameter form when converter has parameters", async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [
        {
          converter_type: "Base64Converter",
          supported_input_types: ["text"],
          supported_output_types: ["text"],
          parameters: [
            {
              name: "encoding_func",
              type_name: "str",
              required: false,
              default: "b64encode",
              choices: ["b64encode", "urlsafe_b64encode"],
            },
          ],
        },
      ],
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-converter-params"
          conversationId="conv-converter-params"
          activeConversationId="conv-converter-params"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));

    // Select the converter that has parameters
    const input = screen.getByRole("combobox");
    await userEvent.click(input);
    const option = await screen.findByRole("option", { name: /Base64Converter/ });
    await userEvent.click(option);

    await waitFor(() => {
      expect(screen.getByTestId("converter-params")).toBeInTheDocument();
      expect(screen.getByTestId("param-encoding_func")).toBeInTheDocument();
      expect(screen.getByText("Parameters")).toBeInTheDocument();
    });
  });

  it("should preview conversion when Preview button is clicked", async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [
        {
          converter_type: "Base64Converter",
          supported_input_types: ["text"],
          supported_output_types: ["text"],
          parameters: [],
        },
      ],
    });
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: "test-conv-id",
      converter_type: "Base64Converter",
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      converted_value: "aGVsbG8=",
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-converter-preview"
          conversationId="conv-converter-preview"
          activeConversationId="conv-converter-preview"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    // Type in the chat input textarea first
    const chatInput = screen.getByTestId("chat-input");
    await userEvent.type(chatInput, "hello");

    // Open converter panel
    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));

    // Select converter
    const combobox = screen.getByRole("combobox");
    await userEvent.click(combobox);
    const converterOption = await screen.findByRole("option", { name: /Base64Converter/ });
    await userEvent.click(converterOption);

    await waitFor(() => {
      expect(screen.getByTestId("converter-preview-btn")).toBeInTheDocument();
    });

    // Click Preview — should use chat input text
    await userEvent.click(screen.getByTestId("converter-preview-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("converter-preview-result")).toBeInTheDocument();
      expect(screen.getByText("aGVsbG8=")).toBeInTheDocument();
    });

    expect(mockedConvertersApi.createConverter).toHaveBeenCalledWith({
      type: "Base64Converter",
      params: {},
    });
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith({
      original_value: "hello",
      converter_ids: ["test-conv-id"],
      original_value_data_type: "text",
    });
  });

  it("should switch converter details when a different dropdown option is selected", async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [
        {
          converter_type: "Base64Converter",
          supported_input_types: ["text"],
          supported_output_types: ["text"],
          parameters: [],
        },
        {
          converter_type: "CharSwapConverter",
          supported_input_types: ["text"],
          supported_output_types: ["text"],
          parameters: [],
        },
      ],
    });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-converter-select"
          conversationId="conv-converter-select"
          activeConversationId="conv-converter-select"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));

    // Select first converter
    const input = screen.getByRole("combobox");
    await userEvent.click(input);
    const firstOption = await screen.findByRole("option", { name: /Base64Converter/ });
    await userEvent.click(firstOption);

    await waitFor(() => {
      expect(screen.getByTestId("converter-item-Base64Converter")).toBeInTheDocument();
    });

    // Click combobox to open the dropdown listbox
    await userEvent.click(input);

    // Find and click the second converter option
    const option = await screen.findByRole("option", { name: /CharSwapConverter/ });
    await userEvent.click(option);

    await waitFor(() => {
      expect(screen.getByTestId("converter-item-CharSwapConverter")).toBeInTheDocument();
    });
  });

  it("should allow converter and conversation panels to be open at the same time", async () => {
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedAttacksApi.getConversations.mockResolvedValue({
      main_conversation_id: "conv-both-panels",
      conversations: [
        { conversation_id: "conv-both-panels", is_main: true, message_count: 1, created_at: "2026-01-01T00:00:00Z" },
      ],
    });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-both-panels"
          conversationId="conv-both-panels"
          activeConversationId="conv-both-panels"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));
    await userEvent.click(screen.getByTestId("toggle-panel-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("converter-panel")).toBeInTheDocument();
      expect(screen.getByTestId("conversation-panel")).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Copy to input with attachments
  // -----------------------------------------------------------------------

  it("should copy message with attachments to input box", async () => {
    const user = userEvent.setup();
    const copiedAttachments: MessageAttachment[] = [
      {
        type: "image",
        name: "first.png",
        url: "data:image/png;base64,aW1hZ2U=",
        mimeType: "image/png",
        size: 12,
        pieceId: "piece-image",
        metadata: { source: "generated" },
      },
      {
        type: "file",
        name: "excluded.pdf",
        url: "data:application/pdf;base64,cGRm",
        mimeType: "application/pdf",
        pieceId: "piece-file",
        metadata: { source: "document" },
      },
      {
        type: "audio",
        name: "second.wav",
        url: "data:audio/wav;base64,YXVkaW8=",
        mimeType: "audio/wav",
        pieceId: "piece-audio",
        metadata: { voice: "alloy" },
      },
    ];
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      {
        role: "assistant",
        content: "Here is an image",
        attachments: copiedAttachments,
      },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);
    mockedMapper.buildMessagePieces.mockResolvedValue([]);
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("done") as never);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={{
            ...mockTarget,
            capabilities: buildCapabilities({
              supported_input_modalities: ["image_path", "audio_path"],
            }),
          }}
          attackResultId="ar-copy-att"
          conversationId="conv-copy-att"
          activeConversationId="conv-copy-att"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    const copyBtn = screen.getByTestId("copy-to-input-btn-1");
    await user.click(copyBtn);

    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toBe("Here is an image");
    });
    expect(screen.getByText(/first\.png/)).toBeInTheDocument();
    expect(screen.getByText(/second\.wav/)).toBeInTheDocument();
    expect(screen.getAllByTestId(/^remove-attachment-/)).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(mockedMapper.buildMessagePieces).toHaveBeenCalledWith(
        "Here is an image",
        [copiedAttachments[0], copiedAttachments[2]]
      );
    });
  });

  it("should not copy a score-only media piece into the input box", async () => {
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      {
        role: "assistant",
        content: "Blocked media response",
        displayPieces: [
          {
            type: "media",
            pieceId: "piece-blocked",
            pieceIndex: 0,
            scores: [
              {
                id: "score-blocked",
                message_piece_id: "piece-blocked",
                scorer_type: "ImageScorer",
                score_type: "true_false",
                score_value: "True",
                pieceIndex: 0,
                pieceType: "image_path",
                sourceLabel: "Piece 1 · image_path",
                timestamp: "2026-02-15T00:00:00Z",
              },
            ],
          },
        ],
      },
    ];

    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-copy-score-only"
          conversationId="conv-copy-score-only"
          activeConversationId="conv-copy-score-only"
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("copy-to-input-btn-1"));

    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toBe("Blocked media response");
    });
    expect(screen.queryByTestId("remove-attachment-0")).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Converter panel integration
  // ---------------------------------------------------------------------------

  it("should open converter panel when toggle button is clicked and pass props", async () => {
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({ items: [] });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-panel"
          conversationId="conv-panel"
          activeConversationId="conv-panel"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    // Panel should not be open initially
    expect(screen.queryByTestId("converter-panel")).not.toBeInTheDocument();

    // Click the converter toggle
    const toggleBtn = screen.getByTestId("toggle-converter-panel-btn");
    await userEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.getByTestId("converter-panel")).toBeInTheDocument();
    });

    // Close the panel
    const closeBtn = screen.getByTestId("close-converter-panel-btn");
    await userEvent.click(closeBtn);

    await waitFor(() => {
      expect(screen.queryByTestId("converter-panel")).not.toBeInTheDocument();
    });
  });

  it("should pass input text and attachments to converter panel", async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({ items: [] });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-input"
          conversationId="conv-input"
          activeConversationId="conv-input"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    // Type into chat input
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "test text");

    // Open converter panel
    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));

    await waitFor(() => {
      expect(screen.getByTestId("converter-panel")).toBeInTheDocument();
    });
  });

  it("should handle onClearConversion and onConvertedValueChange from ChatInputArea", async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({ items: [] });

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-conv-flow"
          conversationId="conv-flow"
          activeConversationId="conv-flow"
          relatedConversationCount={0}
        />
      </TestWrapper>
    );

    // Open converter panel — this exercises onToggleConverterPanel (L584),
    // ConverterPanel onClose (L508), and onUseConvertedValue (L509)
    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("converter-panel")).toBeInTheDocument();
    });

    // Close it via the panel close button — exercises the onClose callback
    await userEvent.click(screen.getByTestId("close-converter-panel-btn"));
    await waitFor(() => {
      expect(screen.queryByTestId("converter-panel")).not.toBeInTheDocument();
    });

    // Toggle it again to verify state toggles correctly
    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("converter-panel")).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Text → File converter flow (e.g. PDFConverter)
  // -----------------------------------------------------------------------

  it("should render converted-file chip and synthesize file attachment when a text→file converter is used", async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [
        {
          converter_type: "PDFConverter",
          supported_input_types: ["text"],
          supported_output_types: ["binary_path"],
          parameters: [],
        },
      ],
    });
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: "conv-pdf",
      converter_type: "PDFConverter",
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      converted_value: "/tmp/results/report.pdf",
      converted_value_data_type: "binary_path",
    });
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-pdf",
      conversation_id: "conv-pdf-flow",
      created_at: "2026-01-01T00:00:00Z",
    } as never);
    // Keep addMessage pending so the optimistic user message (with the
    // synthesized file attachment) remains in the DOM for assertion.
    mockedAttacksApi.addMessage.mockImplementation(
      () => new Promise(() => {}) as never
    );
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { piece_type: "text", original_value: "make a pdf" } as never,
    ]);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} conversationId={null} />
      </TestWrapper>
    );

    // 1. Type input
    const chatInput = screen.getByTestId("chat-input");
    await userEvent.type(chatInput, "make a pdf");

    // 2. Open converter panel and select PDFConverter
    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));
    const combobox = screen.getByRole("combobox");
    await userEvent.click(combobox);
    const option = await screen.findByRole("option", { name: /PDFConverter/ });
    await userEvent.click(option);

    // 3. text→file converters do not auto-preview; click Preview explicitly
    await waitFor(() => {
      expect(screen.getByTestId("converter-preview-btn")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("converter-preview-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("converter-preview-result")).toBeInTheDocument();
    });

    // 4. Use the converted value — populates pieceConversions['text'] with binary_path output
    await userEvent.click(screen.getByTestId("use-converted-btn"));

    // 5. The file chip should appear in the input area (covers convertedFileChip IIFE)
    const chip = await screen.findByTestId("converted-file-chip");
    expect(chip).toHaveTextContent("report.pdf");
    const openLink = screen.getByTestId("converted-file-open");
    expect(openLink).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent("/tmp/results/report.pdf"))
    );

    // 6. Send — covers handleSend's text→file branch (buildMediaUrl /
    //    dataTypeToAttachmentKind / basenameFromValue) which synthesizes a
    //    file attachment on the optimistic user message.
    const sendBtn = screen.getByTestId("send-message-btn");
    await waitFor(() => expect(sendBtn).toBeEnabled());
    await userEvent.click(sendBtn);

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).toHaveBeenCalled();
    });

    // The optimistic user bubble carries the synthesized file attachment.
    // MessageList renders the file attachment with a unique testid we can target.
    const attachmentOpen = await screen.findByTestId("attachment-open-0-0");
    expect(attachmentOpen).toHaveAttribute(
      "href",
      expect.stringContaining(encodeURIComponent("/tmp/results/report.pdf"))
    );
  });

  it("should auto-clear a stale text→text conversion when the typed text diverges from the original", async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValue({
      items: [
        {
          converter_type: "Base64Converter",
          supported_input_types: ["text"],
          supported_output_types: ["text"],
          parameters: [],
        },
      ],
    });
    mockedConvertersApi.createConverter.mockResolvedValue({
      converter_id: "conv-b64-stale",
      converter_type: "Base64Converter",
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      converted_value: "aGVsbG8=",
      converted_value_data_type: "text",
    });

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} conversationId={null} />
      </TestWrapper>
    );

    const chatInput = screen.getByTestId("chat-input");
    await userEvent.type(chatInput, "hello");

    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));
    const combobox = screen.getByRole("combobox");
    await userEvent.click(combobox);
    const option = await screen.findByRole("option", { name: /Base64Converter/ });
    await userEvent.click(option);

    await waitFor(() => {
      expect(screen.getByTestId("converter-preview-btn")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("converter-preview-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("converter-preview-result")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("use-converted-btn"));

    // The converted text row now exists (originalValue captured = "hello")
    await waitFor(() => {
      expect(screen.getByTestId("converted-value-input")).toBeInTheDocument();
    });

    // Type more — originalValue no longer matches chatInputText, so the
    // auto-clear effect must drop pieceConversions['text'].
    await userEvent.type(chatInput, " world");

    await waitFor(() => {
      expect(screen.queryByTestId("converted-value-input")).not.toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Conversation export
  // -----------------------------------------------------------------------

  describe("conversation export", () => {
    function spyOnDownloadAnchor(): { clickSpy: jest.Mock; getDownloadAnchor: () => HTMLAnchorElement } {
      const anchors: HTMLAnchorElement[] = [];
      const clickSpy = jest.fn();
      const origCreateElement = document.createElement.bind(document);
      jest.spyOn(document, "createElement").mockImplementation((tag: string) => {
        const el = origCreateElement(tag);
        if (tag === "a") {
          anchors.push(el as HTMLAnchorElement);
          jest.spyOn(el as HTMLAnchorElement, "click").mockImplementation(clickSpy);
        }
        return el;
      });
      return { clickSpy, getDownloadAnchor: () => anchors.find((a) => a.download) as HTMLAnchorElement };
    }

    async function renderWithLoadedConversation(
      props: Record<string, unknown> = {}
    ): Promise<void> {
      mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
      mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);
      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            attackResultId="ar-1"
            conversationId="conv-1"
            activeConversationId="conv-1"
            {...props}
          />
        </TestWrapper>
      );
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /export conversation/i })).toBeEnabled()
      );
    }

    afterEach(() => {
      jest.restoreAllMocks();
    });

    it("shows an export button in the ribbon", () => {
      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} />
        </TestWrapper>
      );
      expect(screen.getByRole("button", { name: /export conversation/i })).toBeInTheDocument();
    });

    it("disables export when the conversation is empty", () => {
      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} />
        </TestWrapper>
      );
      expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled();
    });

    it("enables export once a conversation with messages loads", async () => {
      await renderWithLoadedConversation();
      expect(screen.getByRole("button", { name: /export conversation/i })).toBeEnabled();
    });

    it("keeps export disabled when every loaded message is a loading placeholder", async () => {
      mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
      mockedMapper.backendMessagesToFrontend.mockReturnValue([
        { role: "assistant", content: "", timestamp: "2026-07-22T02:30:07.000Z", isLoading: true },
      ]);
      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            attackResultId="ar-1"
            conversationId="conv-1"
            activeConversationId="conv-1"
          />
        </TestWrapper>
      );
      await waitFor(() => {
        expect(mockedMapper.backendMessagesToFrontend).toHaveBeenCalled();
      });
      // length > 0 but no non-loading message => export must stay disabled.
      expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled();
    });

    it("keeps export disabled when the only loaded message is a system prompt", async () => {
      mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
      mockedMapper.backendMessagesToFrontend.mockReturnValue([
        { role: "system", content: "You are a pirate.", timestamp: "2026-07-22T02:30:07.000Z" },
      ]);
      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            attackResultId="ar-1"
            conversationId="conv-1"
            activeConversationId="conv-1"
          />
        </TestWrapper>
      );
      await waitFor(() => {
        expect(mockedMapper.backendMessagesToFrontend).toHaveBeenCalled();
      });
      // A lone system prompt renders only in the banner, so export stays disabled.
      expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled();
    });

    it("opens a menu with Markdown and JSON options", async () => {
      const user = userEvent.setup();
      await renderWithLoadedConversation();

      await user.click(screen.getByRole("button", { name: /export conversation/i }));

      expect(screen.getByRole("menuitem", { name: /export as markdown/i })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: /export as json/i })).toBeInTheDocument();
    });

    it("downloads Markdown when the Markdown option is clicked", async () => {
      const user = userEvent.setup();
      await renderWithLoadedConversation();
      const { clickSpy, getDownloadAnchor } = spyOnDownloadAnchor();

      await user.click(screen.getByRole("button", { name: /export conversation/i }));
      await user.click(screen.getByRole("menuitem", { name: /export as markdown/i }));

      const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
      expect(blob.type).toBe("text/markdown;charset=utf-8");
      expect(getDownloadAnchor().download).toMatch(/^copyrit-conversation-conv-1-.*\.md$/);
      expect(clickSpy).toHaveBeenCalled();
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    });

    it("downloads JSON without re-fetching the conversation", async () => {
      const user = userEvent.setup();
      await renderWithLoadedConversation();
      const callsBefore = mockedAttacksApi.getMessages.mock.calls.length;
      const { getDownloadAnchor } = spyOnDownloadAnchor();

      await user.click(screen.getByRole("button", { name: /export conversation/i }));
      await user.click(screen.getByRole("menuitem", { name: /export as json/i }));

      const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
      expect(blob.type).toBe("application/json;charset=utf-8");
      expect(getDownloadAnchor().download).toMatch(/^copyrit-conversation-conv-1-.*\.json$/);
      // WYSIWYG: export serializes in-state messages and makes no extra API call.
      expect(mockedAttacksApi.getMessages.mock.calls.length).toBe(callsBefore);
    });

    it("exports the displayed conversation as a self-contained HTML transcript", async () => {
      const user = userEvent.setup();
      await renderWithLoadedConversation();
      const callsBefore = mockedAttacksApi.getMessages.mock.calls.length;
      const { getDownloadAnchor } = spyOnDownloadAnchor();

      await user.click(screen.getByRole("button", { name: /export conversation/i }));
      await user.click(screen.getByRole("menuitem", { name: /export as html/i }));

      await waitFor(() => expect(URL.createObjectURL as jest.Mock).toHaveBeenCalled());
      const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
      expect(blob.type).toBe("text/html;charset=utf-8");
      expect(getDownloadAnchor().download).toMatch(/^copyrit-conversation-conv-1-.*\.html$/);
      // WYSIWYG: export serializes in-state messages and makes no extra API call.
      expect(mockedAttacksApi.getMessages.mock.calls.length).toBe(callsBefore);
    });

    it("shows progress and ignores a second request while an export is in flight", async () => {
      const user = userEvent.setup();
      const messagesWithMedia: Message[] = [
        ...mockMessages,
        {
          role: "assistant",
          content: "",
          timestamp: new Date().toISOString(),
          attachments: [
            {
              type: "image",
              name: "r.png",
              url: "blob:http://localhost/pending",
              mimeType: "image/png",
              file: new File(["x"], "r.png", { type: "image/png" }),
            },
          ],
        },
      ];
      mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
      mockedMapper.backendMessagesToFrontend.mockReturnValue(messagesWithMedia);
      // Hold the media read open so the export stays in flight across clicks.
      let releaseMedia: (value: string) => void = () => {};
      mockedMapper.fileToBase64.mockImplementation(
        () => new Promise<string>((resolve) => { releaseMedia = resolve; })
      );
      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            attackResultId="ar-1"
            conversationId="conv-1"
            activeConversationId="conv-1"
          />
        </TestWrapper>
      );
      await waitFor(() =>
        expect(screen.getByRole("button", { name: /export conversation/i })).toBeEnabled()
      );
      const { clickSpy } = spyOnDownloadAnchor();

      await user.click(screen.getByRole("button", { name: /export conversation/i }));
      await user.click(screen.getByTestId("export-html-item"));
      const exportButton = screen.getByRole("button", { name: /export conversation/i });
      await waitFor(() => expect(within(exportButton).getByRole("progressbar")).toBeInTheDocument());

      await user.click(screen.getByRole("button", { name: /export conversation/i }));
      await user.click(screen.getByTestId("export-html-item"));

      // The menu shows the export is already running, and the guard stops a
      // second one from starting even if the click lands anyway.
      expect(screen.getByTestId("export-html-item")).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByTestId("export-markdown-item")).toHaveAttribute("aria-disabled", "true");
      expect(mockedMapper.fileToBase64).toHaveBeenCalledTimes(1);

      releaseMedia("eA==");
      await waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(within(exportButton).queryByRole("progressbar")).not.toBeInTheDocument());
    });

    it("exports the displayed conversation id when it differs from the attack's main conversation", async () => {
      const user = userEvent.setup();
      // Viewing a branch: activeConversationId (displayed) differs from the
      // attack's main conversationId. handleExport uses activeConversationId.
      await renderWithLoadedConversation({
        conversationId: "conv-main",
        activeConversationId: "conv-branch",
      });
      const { getDownloadAnchor } = spyOnDownloadAnchor();

      await user.click(screen.getByRole("button", { name: /export conversation/i }));
      await user.click(screen.getByRole("menuitem", { name: /export as markdown/i }));

      expect(getDownloadAnchor().download).toMatch(/^copyrit-conversation-conv-branch-.*\.md$/);
    });

    it("allows exporting a read-only historical conversation", async () => {
      const user = userEvent.setup();
      // Operator lock: the loaded attack belongs to a different operator.
      await renderWithLoadedConversation({ attackOperator: "someone-else" });
      const { clickSpy } = spyOnDownloadAnchor();

      const exportButton = screen.getByRole("button", { name: /export conversation/i });
      expect(exportButton).toBeEnabled();

      await user.click(exportButton);
      await user.click(screen.getByRole("menuitem", { name: /export as markdown/i }));

      expect(clickSpy).toHaveBeenCalled();
    });

    it("disables export while a message is being sent", async () => {
      const user = userEvent.setup();
      mockedMapper.buildMessagePieces.mockResolvedValue([
        { data_type: "text", original_value: "hi" },
      ]);
      // addMessage never resolves, so the conversation stays in the sending state.
      mockedAttacksApi.addMessage.mockImplementation(() => new Promise(() => {}));
      await renderWithLoadedConversation();

      await user.type(screen.getByRole("textbox"), "hi");
      await user.click(screen.getByRole("button", { name: /send/i }));

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled()
      );
    });
  });
});

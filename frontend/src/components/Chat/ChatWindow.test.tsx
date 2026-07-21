import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import ChatWindow from "./ChatWindow";
import { makeTarget } from "@/test-utils/targetFixtures";
import { Message, TargetCapabilities, TargetInfo, TargetInstance } from "../../types";
import { attacksApi, convertersApi } from "../../services/api";
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
    addMessage: jest.fn(),
    getMessages: jest.fn(),
    getRelatedConversations: jest.fn(),
    getConversations: jest.fn(),
    createConversation: jest.fn(),
    changeMainConversation: jest.fn(),
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
  backendMessagesToFrontend: jest.fn(),
}));

const mockedAttacksApi = attacksApi as jest.Mocked<typeof attacksApi>;
const mockedConvertersApi = convertersApi as jest.Mocked<typeof convertersApi>;
const mockedMapper = messageMapper as jest.Mocked<typeof messageMapper>;

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <FluentProvider theme={webLightTheme}>{children}</FluentProvider>;

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

function makeErrorResponse(errorType: string, description: string) {
  return {
    messages: {
      messages: [
        {
          turn_number: 1,
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
    onLabelsChange: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
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

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Hello" },
    ]);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-conv-1",
      conversation_id: "conv-1",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockedAttacksApi.addMessage.mockResolvedValue(makeTextResponse("Hello back!") as never);
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
      });
      expect(onConversationCreated).toHaveBeenCalledWith("ar-conv-1", "conv-1");
      expect(mockedAttacksApi.addMessage).toHaveBeenCalledWith("ar-conv-1", {
        role: "user",
        pieces: [{ data_type: "text", original_value: "Hello" }],
        send: true,
        target_registry_name: "openai_chat_1",
        target_conversation_id: "conv-1",
        labels: { operator: "testuser", operation: "test_op" },
      });
    });

    // Messages should appear in the DOM
    await waitFor(() => {
      expect(screen.getByText("Hello back!")).toBeInTheDocument();
    });
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
    });
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
      expect(onConversationCreated).toHaveBeenCalledWith("ar-conv-multi-turn", "conv-multi-turn");
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
    const onSelectConversation = jest.fn();
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      { role: "assistant", content: "reply text to copy" },
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
    await userEvent.click(copyBtn);

    await waitFor(() => {
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith("ar-copy-new", {});
      expect(onSelectConversation).toHaveBeenCalledWith("new-conv-copy");
    });
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
      { role: "assistant", content: "response", timestamp: "2026-01-01T00:00:01Z" },
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
      { role: "assistant", content: "response", timestamp: "2026-01-01T00:00:01Z" },
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
          attackLabels={{ operator: "bob", operation: "test_op" }}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("operator-locked-banner")).toBeInTheDocument();
    });

    expect(screen.getByTestId("use-as-template-btn")).toBeInTheDocument();
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
    const mockMessages: Message[] = [
      { role: "user", content: "hello" },
      {
        role: "assistant",
        content: "Here is an image",
        attachments: [
          {
            type: "image" as const,
            name: "test.png",
            url: "data:image/png;base64,iVBORw0KGgo=",
            mimeType: "image/png",
            size: 12,
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
    await userEvent.click(copyBtn);

    // The text should appear in the input area
    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toBe("Here is an image");
    });
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
});

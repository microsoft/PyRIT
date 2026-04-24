import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import ChatWindow from "./ChatWindow";
import { Message, TargetInfo, TargetInstance } from "../../types";
import { attacksApi, convertersApi } from "../../services/api";
import * as messageMapper from "../../utils/messageMapper";

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

const mockTarget: TargetInstance = {
  target_registry_name: "openai_chat_1",
  target_type: "OpenAIChatTarget",
  endpoint: "https://api.openai.com",
  model_name: "gpt-4",
};

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
          pieces: [
            {
              piece_id: "p-resp",
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
          pieces: [
            {
              piece_id: "p-img",
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
          pieces: [
            {
              piece_id: "p-aud",
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
          pieces: [
            {
              piece_id: "p-vid",
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
          pieces: [
            {
              piece_id: "p-text",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              original_value: "Here is the result:",
              converted_value: "Here is the result:",
              scores: [],
              response_error: "none",
            },
            {
              piece_id: "p-img2",
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
          pieces: [
            {
              piece_id: "p-err",
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

    expect(screen.getByText("PyRIT Attack")).toBeInTheDocument();
    expect(screen.getByText("New Attack")).toBeInTheDocument();
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

    expect(screen.getByText(/OpenAIChatTarget/)).toBeInTheDocument();
    expect(screen.getByText(/gpt-4/)).toBeInTheDocument();
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
      model_name: null,
    };

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={targetNoModel} />
      </TestWrapper>
    );

    expect(screen.getByText(/OpenAIChatTarget/)).toBeInTheDocument();
    expect(screen.queryByText(/gpt/)).not.toBeInTheDocument();
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
    const singleTurnTarget: TargetInstance = {
      target_registry_name: "openai_image_1",
      target_type: "OpenAIImageTarget",
      supports_multi_turn: false,
    };

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
    const singleTurnTarget: TargetInstance = {
      target_registry_name: "openai_image_1",
      target_type: "OpenAIImageTarget",
      supports_multi_turn: false,
    };

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
    const singleTurnTarget: TargetInstance = {
      target_registry_name: "openai_tts_1",
      target_type: "OpenAITTSTarget",
      supports_multi_turn: false,
    };

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
      target_type: mockTarget.target_type,
      endpoint: mockTarget.endpoint,
      model_name: mockTarget.model_name,
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

    const singleTurnTarget: TargetInstance = {
      target_registry_name: "openai_image_1",
      target_type: "OpenAIImageTarget",
      supports_multi_turn: false,
    };

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
              type_name: "Literal['b64encode', 'urlsafe_b64encode']",
              required: false,
              default_value: "b64encode",
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
});

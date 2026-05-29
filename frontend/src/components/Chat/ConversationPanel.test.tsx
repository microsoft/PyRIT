import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import ConversationPanel from "./ConversationPanel";
import { attacksApi } from "../../services/api";

jest.mock("../../services/api", () => ({
  attacksApi: {
    getConversations: jest.fn(),
    createConversation: jest.fn(),
  },
}));

const mockedAttacksApi = attacksApi as jest.Mocked<typeof attacksApi>;

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <FluentProvider theme={webLightTheme}>{children}</FluentProvider>;

const defaultProps = {
  attackResultId: "ar-attack-123",
  activeConversationId: "attack-123",
  onSelectConversation: jest.fn(),
  onNewConversation: jest.fn(),
  onChangeMainConversation: jest.fn(),
  onClose: jest.fn(),
};

describe("ConversationPanel", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  // -----------------------------------------------------------------------
  // Basic rendering
  // -----------------------------------------------------------------------

  it("should render the panel header", async () => {
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [],
    });

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Attack Conversations")).toBeInTheDocument();
    await waitFor(() => {
      expect(mockedAttacksApi.getConversations).toHaveBeenCalledTimes(1);
    });
  });

  it("should show empty state when no conversations", async () => {
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [],
    });

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("No related conversations")).toBeInTheDocument();
    });
  });

  it("should show empty state when no attack is active", async () => {
    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} attackResultId={null} />
      </TestWrapper>
    );

    expect(
      screen.getByText("Start an attack to see conversations")
    ).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Conversation list
  // -----------------------------------------------------------------------

  it("should display related conversations", async () => {
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [
        {
          conversation_id: "attack-123",
          message_count: 5,
          last_message_preview: "Hello world",
          created_at: "2026-02-18T10:30:00Z",
        },
        {
          conversation_id: "branch-1",
          message_count: 2,
          last_message_preview: "Branched",
          created_at: "2026-02-18T11:00:00Z",
        },
      ],
    });

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("attack-123")).toBeInTheDocument();
      expect(screen.getByText("branch-1")).toBeInTheDocument();
    });
  });

  it("should show filled star for main conversation and outline star for non-main", async () => {
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [
        {
          conversation_id: "attack-123",
          message_count: 3,
          last_message_preview: null,
          created_at: "2026-02-18T10:30:00Z",
        },
        {
          conversation_id: "conv-2",
          message_count: 1,
          last_message_preview: null,
          created_at: "2026-02-18T11:00:00Z",
        },
      ],
    });

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      const mainStarBtn = screen.getByTestId("star-btn-attack-123");
      expect(mainStarBtn).toBeInTheDocument();
      expect(mainStarBtn).toBeDisabled();

      const otherStarBtn = screen.getByTestId("star-btn-conv-2");
      expect(otherStarBtn).toBeInTheDocument();
      expect(otherStarBtn).not.toBeDisabled();
    });
  });

  it("should show message count badge", async () => {
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [
        {
          conversation_id: "attack-123",
          message_count: 7,
          last_message_preview: null,
          created_at: "2026-02-18T10:30:00Z",
        },
      ],
    });

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("7")).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Selection and actions
  // -----------------------------------------------------------------------

  it("should call onSelectConversation when clicking a conversation", async () => {
    const user = userEvent.setup();
    const onSelectConversation = jest.fn();

    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [
        {
          conversation_id: "branch-1",
          message_count: 2,
          last_message_preview: null,
          created_at: "2026-02-18T11:00:00Z",
        },
      ],
    });

    render(
      <TestWrapper>
        <ConversationPanel
          {...defaultProps}
          onSelectConversation={onSelectConversation}
        />
      </TestWrapper>
    );

    const convItem = await screen.findByTestId("conversation-item-branch-1");
    await user.click(convItem);
    expect(onSelectConversation).toHaveBeenCalledWith("branch-1");
  });

  it("should call onNewConversation when clicking new conversation button", async () => {
    const user = userEvent.setup();
    const onNewConversation = jest.fn();

    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [],
    });

    render(
      <TestWrapper>
        <ConversationPanel
          {...defaultProps}
          onNewConversation={onNewConversation}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(
        screen.getByTestId("new-conversation-btn")
      ).not.toBeDisabled();
    });

    await user.click(screen.getByTestId("new-conversation-btn"));
    expect(onNewConversation).toHaveBeenCalled();
  });

  it("should disable new conversation button when no attack is active", () => {
    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} attackResultId={null} />
      </TestWrapper>
    );

    expect(screen.getByTestId("new-conversation-btn")).toBeDisabled();
  });

  it("should call onClose when clicking close button", async () => {
    const user = userEvent.setup();
    const onClose = jest.fn();

    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [],
    });

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} onClose={onClose} />
      </TestWrapper>
    );

    await user.click(screen.getByTestId("close-panel-btn"));
    expect(onClose).toHaveBeenCalled();
  });

  // -----------------------------------------------------------------------
  // Preview text
  // -----------------------------------------------------------------------

  it("should show last message preview", async () => {
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [
        {
          conversation_id: "attack-123",
          message_count: 3,
          last_message_preview: "This is a preview of the last...",
          created_at: "2026-02-18T10:30:00Z",
        },
      ],
    });

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(
        screen.getByText("This is a preview of the last...")
      ).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Error handling
  // -----------------------------------------------------------------------

  it("should handle API errors gracefully", async () => {
    mockedAttacksApi.getConversations.mockRejectedValue(
      new Error("Network error")
    );

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    // Should show error state on error, not crash
    await waitFor(() => {
      expect(screen.getByTestId("conversation-error")).toBeInTheDocument();
    });
    expect(screen.getByText("Network error")).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Set main conversation via star icon
  // -----------------------------------------------------------------------

  it("should call onChangeMainConversation when clicking star on non-main conversation", async () => {
    const onChangeMainConversation = jest.fn().mockResolvedValue(undefined);
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: "ar-attack-123",
      main_conversation_id: "attack-123",
      conversations: [
        {
          conversation_id: "attack-123",
          message_count: 3,
          last_message_preview: null,
          created_at: "2026-02-18T10:30:00Z",
        },
        {
          conversation_id: "conv-2",
          message_count: 1,
          last_message_preview: null,
          created_at: "2026-02-18T11:00:00Z",
        },
      ],
    });

    const user = userEvent.setup();
    render(
      <TestWrapper>
        <ConversationPanel
          {...defaultProps}
          onChangeMainConversation={onChangeMainConversation}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("star-btn-conv-2")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("star-btn-conv-2"));
    expect(onChangeMainConversation).toHaveBeenCalledWith("conv-2");
  });

  it("should show error state on fetch failure", async () => {
    const axiosError = {
      isAxiosError: true,
      response: { status: 500, data: { detail: "Server exploded" } },
    };
    mockedAttacksApi.getConversations.mockRejectedValue(axiosError);

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("conversation-error")).toBeInTheDocument();
    });
    expect(screen.getByText("Server exploded")).toBeInTheDocument();
    expect(screen.getByTestId("conversation-retry-btn")).toBeInTheDocument();
  });

  it("should retry on clicking retry button", async () => {
    const axiosError = {
      isAxiosError: true,
      response: { status: 500, data: { detail: "Server error" } },
    };
    mockedAttacksApi.getConversations.mockRejectedValueOnce(axiosError);

    render(
      <TestWrapper>
        <ConversationPanel {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByTestId("conversation-error")).toBeInTheDocument();
    });

    // Now succeed on retry
    mockedAttacksApi.getConversations.mockResolvedValue({
      conversations: [
        { conversation_id: "conv-1", message_count: 3, last_message_preview: "hello" },
      ],
      main_conversation_id: "conv-1",
    });

    const user = userEvent.setup();
    await user.click(screen.getByTestId("conversation-retry-btn"));

    await waitFor(() => {
      expect(
        screen.getByTestId("conversation-item-conv-1")
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("conversation-error")).not.toBeInTheDocument();
  });
});

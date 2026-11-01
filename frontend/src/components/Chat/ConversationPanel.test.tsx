import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import ConversationPanel from "./ConversationPanel";
import { attacksApi } from "../../services/api";
import type { AttackConversationsResponse, ConversationSummary } from "@/types";

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

  describe("bounded saved references", () => {
    const props = { ...defaultProps, referencePageSize: 50 };
    const references: ConversationSummary[] = Array.from({ length: 55 }, (_value: unknown, index: number) => ({
      conversation_id: index === 0 ? "attack-123" : `saved-ref-${index}`,
      conversation_type: index === 0 ? "main" : "pruned", message_count: index + 1,
      last_message_preview: `Saved preview ${index}`,
    }));
    function page(
      conversations: ConversationSummary[], cursor: string | null, attackId = defaultProps.attackResultId,
    ): AttackConversationsResponse {
      return {
        attack_result_id: attackId, conversations, total_count: 55,
        main_conversation_id: attackId === defaultProps.attackResultId ? "attack-123" : `${attackId}-main`,
        pagination: { limit: 50, has_more: cursor !== null, next_cursor: cursor },
      };
    }
    const first = page(references.slice(0, 50), "reference-cursor");
    const final = page(references.slice(49), null);
    const unavailable = { isAxiosError: true, response: { status: 503, data: { detail: "Saved references temporarily unavailable" } } };
    beforeEach(() => { mockedAttacksApi.getConversations.mockReset(); });

    it("should preserve off-page selection, follow actual totals, deduplicate references and continue through keyboard selection changes", async () => {
      const user = userEvent.setup();
      const onSelectConversation = jest.fn();
      let finishPage: (response: AttackConversationsResponse) => void = () => { throw new Error("No pending reference page"); };
      mockedAttacksApi.getConversations.mockResolvedValueOnce(first).mockImplementationOnce(
        () => new Promise<AttackConversationsResponse>((resolve) => { finishPage = resolve; }),
      );
      const rendered = render(<TestWrapper><ConversationPanel {...props} activeConversationId="saved-ref-54"
        onSelectConversation={onSelectConversation} /></TestWrapper>);
      expect(await screen.findByText(/50 of 55 saved conversations loaded/)).toBeVisible();
      expect(screen.getByText(/Selected conversation saved-ref-54 is outside the loaded references/)).toBeVisible();
      expect(onSelectConversation).not.toHaveBeenCalled();
      expect(mockedAttacksApi.getConversations.mock.calls).toEqual([["ar-attack-123", { limit: 50 }]]);
      screen.getByRole("button", { name: "Load more conversations" }).focus();
      await user.keyboard("{Enter}");
      const loadingButton = screen.getByRole("button", { name: "Loading more conversations..." });
      expect(loadingButton).toBeDisabled();
      await user.click(loadingButton);
      const loadedRow = screen.getByRole("button", { name: "Select conversation saved-ref-7" });
      loadedRow.focus();
      await user.keyboard(" ");
      expect(onSelectConversation).toHaveBeenCalledWith("saved-ref-7");
      rendered.rerender(<TestWrapper><ConversationPanel {...props} activeConversationId="saved-ref-7"
        onSelectConversation={onSelectConversation} /></TestWrapper>);
      expect(mockedAttacksApi.getConversations).toHaveBeenCalledTimes(2);
      await act(async () => { finishPage(final); });
      expect(screen.getByRole("region", { name: "Saved conversation references" })).toHaveTextContent("55 of 55 saved conversations loaded. End of saved conversation references.");
      expect(screen.getAllByRole("button", { name: /^Select conversation / })).toHaveLength(55);
      expect(screen.getByRole("button", { name: "Select conversation saved-ref-7" })).toBe(loadedRow);
      expect(screen.queryByRole("button", { name: "Load more conversations" })).not.toBeInTheDocument();
      expect(mockedAttacksApi.getConversations.mock.calls[1]).toEqual(["ar-attack-123", { limit: 50, cursor: "reference-cursor" }]);
      screen.getByRole("button", { name: "Select conversation saved-ref-54" }).focus();
      await user.keyboard("{Enter}");
      expect(onSelectConversation).toHaveBeenLastCalledWith("saved-ref-54");
    });

    it("should keep references on continuation failure and retry the same bounded cursor", async () => {
      const user = userEvent.setup();
      mockedAttacksApi.getConversations.mockResolvedValueOnce(first).mockRejectedValueOnce(unavailable).mockResolvedValueOnce(final);
      render(<TestWrapper><ConversationPanel {...props} /></TestWrapper>);
      await user.click(await screen.findByRole("button", { name: "Load more conversations" }));
      expect(await screen.findByText(/Could not load more conversations: Saved references temporarily unavailable/)).toBeVisible();
      expect(screen.getByRole("button", { name: "Select conversation saved-ref-49" })).toBeVisible();
      await user.click(screen.getByRole("button", { name: "Retry more conversations" }));
      expect(await screen.findByText(/55 of 55 saved conversations loaded/)).toBeVisible();
      expect(mockedAttacksApi.getConversations.mock.calls).toEqual([
        ["ar-attack-123", { limit: 50 }],
        ["ar-attack-123", { limit: 50, cursor: "reference-cursor" }],
        ["ar-attack-123", { limit: 50, cursor: "reference-cursor" }],
      ]);
    });

    it("should restart rejected references without a cursor and retain the loaded rows on restart failure", async () => {
      const user = userEvent.setup();
      mockedAttacksApi.getConversations.mockResolvedValueOnce(first).mockRejectedValueOnce({
        isAxiosError: true, response: { status: 400, data: { detail: "Saved reference order changed" } },
      }).mockRejectedValueOnce(unavailable).mockResolvedValueOnce(page(references.slice(0, 50), "restarted-reference-cursor"));
      render(<TestWrapper><ConversationPanel {...props} /></TestWrapper>);
      await user.click(await screen.findByRole("button", { name: "Load more conversations" }));
      await user.click(await screen.findByRole("button", { name: "Restart conversation references" }));
      expect(await screen.findByText("Saved references temporarily unavailable", { exact: true })).toBeVisible();
      expect(screen.getByRole("button", { name: "Select conversation saved-ref-49" })).toBeVisible();
      expect(screen.queryByRole("button", { name: "Retry more conversations" })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Restart conversation references" }));
      expect(await screen.findByRole("button", { name: "Load more conversations" })).toBeEnabled();
      expect(mockedAttacksApi.getConversations.mock.calls.slice(2)).toEqual([
        ["ar-attack-123", { limit: 50 }], ["ar-attack-123", { limit: 50 }],
      ]);
      expect(screen.queryByText("Saved references temporarily unavailable", { exact: true })).not.toBeInTheDocument();
    });

    it("should reject a late page after switching attacks and returning to the original selection", async () => {
      const user = userEvent.setup();
      let finishOld: (response: AttackConversationsResponse) => void = () => { throw new Error("No old reference read"); };
      mockedAttacksApi.getConversations.mockResolvedValueOnce(first).mockImplementationOnce(
        () => new Promise<AttackConversationsResponse>((resolve) => { finishOld = resolve; }),
      ).mockResolvedValueOnce(page([{ conversation_id: "other-main", message_count: 1 }], null, "other"))
        .mockResolvedValueOnce(page(references.slice(0, 50), "fresh-reference-cursor"));
      const rendered = render(<TestWrapper><ConversationPanel {...props} /></TestWrapper>);
      await user.click(await screen.findByRole("button", { name: "Load more conversations" }));
      rendered.rerender(<TestWrapper><ConversationPanel {...props} attackResultId="other" activeConversationId="other-main" /></TestWrapper>);
      expect(await screen.findByRole("button", { name: "Select conversation other-main" })).toBeVisible();
      rendered.rerender(<TestWrapper><ConversationPanel {...props} /></TestWrapper>);
      expect(await screen.findByText(/50 of 55 saved conversations loaded/)).toBeVisible();
      await act(async () => { finishOld(final); });
      expect(screen.queryByRole("button", { name: "Select conversation saved-ref-54" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Select conversation other-main" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Load more conversations" })).toBeEnabled();
      expect(mockedAttacksApi.getConversations.mock.calls[2]).toEqual(["other", { limit: 50 }]);
      expect(mockedAttacksApi.getConversations.mock.calls[3]).toEqual(["ar-attack-123", { limit: 50 }]);
    });

    it("should invalidate a pending continuation when the reference refresh key changes", async () => {
      const user = userEvent.setup();
      let failOld: (cause: unknown) => void = () => { throw new Error("No old reference request"); };
      mockedAttacksApi.getConversations.mockResolvedValueOnce(first).mockImplementationOnce(
        () => new Promise<AttackConversationsResponse>((_resolve, reject) => { failOld = reject; }),
      ).mockResolvedValueOnce(page(references.slice(0, 50), "fresh-reference-cursor"));
      const rendered = render(<TestWrapper><ConversationPanel {...props} refreshKey={0} /></TestWrapper>);
      await user.click(await screen.findByRole("button", { name: "Load more conversations" }));
      rendered.rerender(<TestWrapper><ConversationPanel {...props} refreshKey={1} /></TestWrapper>);
      await screen.findByRole("button", { name: "Load more conversations" });
      await act(async () => { failOld(unavailable); });
      expect(screen.queryByText(/Could not load more conversations/)).not.toBeInTheDocument();
      expect(mockedAttacksApi.getConversations.mock.calls[2]).toEqual(["ar-attack-123", { limit: 50 }]);
    });

    it.each(["attack_result_id", "pagination", "total_count", "has_more", "limit"])("should fail closed on missing saved %s metadata without an unpaged fallback", async (field: string) => {
      const malformed = { ...first, pagination: first.pagination && { ...first.pagination } };
      if ((field === "has_more" || field === "limit") && malformed.pagination) Reflect.deleteProperty(malformed.pagination, field);
      else Reflect.deleteProperty(malformed, field);
      mockedAttacksApi.getConversations.mockResolvedValueOnce(malformed);
      render(<TestWrapper><ConversationPanel {...props} /></TestWrapper>);
      expect(await screen.findByText(/changed or invalid conversation references/)).toBeVisible();
      expect(screen.queryByRole("region", { name: "Saved conversation references" })).not.toBeInTheDocument();
      expect(screen.queryByText("No related conversations")).not.toBeInTheDocument();
      expect(mockedAttacksApi.getConversations.mock.calls).toEqual([["ar-attack-123", { limit: 50 }]]);
    });

    it.each(["repeat", "cycle", "changed totals"])("should retain references and require restart for a cursor %s", async (problem: string) => {
      const user = userEvent.setup();
      mockedAttacksApi.getConversations.mockResolvedValueOnce(first);
      if (problem === "cycle") {
        mockedAttacksApi.getConversations.mockResolvedValueOnce(page([references[50]], "second-cursor"))
          .mockResolvedValueOnce(page([references[51]], "reference-cursor"));
      } else {
        mockedAttacksApi.getConversations.mockResolvedValueOnce(problem === "repeat"
          ? page([references[50]], "reference-cursor") : { ...final, total_count: 56 });
      }
      render(<TestWrapper><ConversationPanel {...props} /></TestWrapper>);
      await user.click(await screen.findByRole("button", { name: "Load more conversations" }));
      if (problem === "cycle") {
        await user.click(await screen.findByRole("button", { name: "Load more conversations" }));
      }
      expect(await screen.findByRole("button", { name: "Restart conversation references" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Select conversation saved-ref-49" })).toBeVisible();
      expect(screen.queryByRole("button", { name: "Select conversation saved-ref-51" })).not.toBeInTheDocument();
      expect(screen.queryByText(/End of saved conversation references/)).not.toBeInTheDocument();
    });

    it.each(["initial", "continuation"])("should ignore a saved %s response after unmount", async (phase: string) => {
      const user = userEvent.setup();
      let finish: (response: AttackConversationsResponse) => void = () => { throw new Error("No pending unmount read"); };
      if (phase === "continuation") mockedAttacksApi.getConversations.mockResolvedValueOnce(first);
      mockedAttacksApi.getConversations.mockImplementationOnce(
        () => new Promise<AttackConversationsResponse>((resolve) => { finish = resolve; }),
      );
      const rendered = render(<TestWrapper><ConversationPanel {...props} /></TestWrapper>);
      if (phase === "continuation") await user.click(await screen.findByRole("button", { name: "Load more conversations" }));
      rendered.unmount();
      await act(async () => { finish(final); });
      expect(screen.queryByRole("region", { name: "Saved conversation references" })).not.toBeInTheDocument();
      expect(mockedAttacksApi.getConversations).toHaveBeenCalledTimes(phase === "initial" ? 1 : 2);
    });
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

  it("should be keyboard accessible for conversation selection", async () => {
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
    expect(convItem).toHaveAttribute("role", "button");
    expect(convItem).toHaveAttribute("tabindex", "0");

    convItem.focus();
    await user.keyboard("{Enter}");
    expect(onSelectConversation).toHaveBeenCalledWith("branch-1");

    onSelectConversation.mockClear();
    convItem.focus();
    await user.keyboard(" ");
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

  it.each(["{Enter}", " "])("should promote from the manual star with %s without activating its parent row", async (key: string) => {
    const user = userEvent.setup();
    const onChangeMainConversation = jest.fn();
    const onSelectConversation = jest.fn();
    mockedAttacksApi.getConversations.mockResolvedValue({
      attack_result_id: defaultProps.attackResultId, main_conversation_id: "attack-123",
      conversations: [{ conversation_id: "branch-1", message_count: 2 }],
    });
    render(<TestWrapper><ConversationPanel {...defaultProps} onChangeMainConversation={onChangeMainConversation}
      onSelectConversation={onSelectConversation} /></TestWrapper>);
    const star = await screen.findByRole("button", { name: "Promote conversation branch-1 to main" });
    star.focus();
    await user.keyboard(key);
    expect(onChangeMainConversation).toHaveBeenCalledTimes(1);
    expect(onChangeMainConversation).toHaveBeenCalledWith("branch-1");
    expect(onSelectConversation).not.toHaveBeenCalled();
    expect(star).toHaveFocus();
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

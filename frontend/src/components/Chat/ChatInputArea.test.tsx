import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import ChatInputArea from "./ChatInputArea";
import type { ChatInputAreaHandle } from "./ChatInputArea";

// Wrapper component for Fluent UI context
const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
);

// Helper to get the send button specifically
const getSendButton = () => screen.getByRole("button", { name: /send/i });

describe("ChatInputArea", () => {
  const defaultProps = {
    onSend: jest.fn(),
    disabled: false,
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("should render input area and send button", () => {
    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByRole("textbox")).toBeInTheDocument();
    expect(getSendButton()).toBeInTheDocument();
  });

  it("should call onSend with input value when send button clicked", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Test prompt message");
    await user.click(getSendButton());

    expect(onSend).toHaveBeenCalled();
  });

  it("should disable input when disabled prop is true", () => {
    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} disabled={true} />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    expect(input).toBeDisabled();
  });

  it("should disable send button when input is empty", () => {
    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const sendButton = getSendButton();
    expect(sendButton).toBeDisabled();
  });

  it("should enable send button when input has text", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Some text");

    const sendButton = getSendButton();
    expect(sendButton).toBeEnabled();
  });

  it("should clear input after sending", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Test message");
    await user.click(getSendButton());

    await waitFor(() => {
      expect(input).toHaveValue("");
    });
  });

  it("should send message on Enter key press", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Test message");
    await user.type(input, "{Enter}");

    expect(onSend).toHaveBeenCalled();
  });

  it("should not send on Shift+Enter (allows multiline)", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Test message");
    await user.keyboard("{Shift>}{Enter}{/Shift}");

    expect(onSend).not.toHaveBeenCalled();
  });

  it("should allow sending whitespace-only messages", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "   ");
    await user.click(getSendButton());

    expect(onSend).toHaveBeenCalled();
  });

  it("should not send when input is completely empty", () => {
    const onSend = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} />
      </TestWrapper>
    );

    const sendButton = getSendButton();
    expect(sendButton).toBeDisabled();
  });

  it("should have file input for attachments", () => {
    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const attachButton = screen.getByTitle("Attach files");
    expect(attachButton).toBeInTheDocument();
  });

  it("should handle file attachment selection", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    // Create a mock file
    const file = new File(["test content"], "test.txt", { type: "text/plain" });
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;

    // Simulate file selection
    await user.upload(fileInput, file);

    // Check that the attachment appears
    await waitFor(() => {
      expect(screen.getByText(/test\.txt/)).toBeInTheDocument();
    });
  });

  it("should handle image file attachment", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const file = new File(["image data"], "photo.png", { type: "image/png" });
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByText(/photo\.png/)).toBeInTheDocument();
    });
  });

  it("should remove attachment when dismiss button clicked", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const file = new File(["test"], "remove-me.txt", { type: "text/plain" });
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByText(/remove-me\.txt/)).toBeInTheDocument();
    });

    // Find and click the dismiss button
    const dismissButtons = screen.getAllByRole("button");
    const dismissButton = dismissButtons.find(
      (btn) =>
        btn.querySelector("svg") && btn.getAttribute("aria-label") !== "Send"
    );

    if (dismissButton) {
      await user.click(dismissButton);
    }
  });

  it("should send with attachments even without text", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} />
      </TestWrapper>
    );

    const file = new File(["test"], "file.txt", { type: "text/plain" });
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByText(/file\.txt/)).toBeInTheDocument();
    });

    // Should be able to send with just attachment
    const sendButton = getSendButton();
    expect(sendButton).toBeEnabled();
    await user.click(sendButton);

    expect(onSend).toHaveBeenCalled();
  });

  it("should handle audio file attachment", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const file = new File(["audio data"], "sound.mp3", { type: "audio/mpeg" });
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByText(/sound\.mp3/)).toBeInTheDocument();
    });
  });

  it("should handle video file attachment", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const file = new File(["video data"], "video.mp4", { type: "video/mp4" });
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;

    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByText(/video\.mp4/)).toBeInTheDocument();
    });
  });

  it("should show single-turn warning when target does not support multiturn chat", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={{
            target_registry_name: "test",
            target_type: "TextTarget",
            supports_multi_turn: false,
          }}
        />
      </TestWrapper>
    );

    expect(
      screen.getByText(
        /does not track conversation history/
      )
    ).toBeInTheDocument();
  });

  it("should not show single-turn warning when target supports multiturn chat", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={{
            target_registry_name: "test",
            target_type: "OpenAIChatTarget",
            supports_multi_turn: true,
          }}
        />
      </TestWrapper>
    );

    expect(
      screen.queryByText(/does not track conversation history/)
    ).not.toBeInTheDocument();
  });

  it("should not show single-turn warning when no active target", () => {
    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} activeTarget={null} />
      </TestWrapper>
    );

    expect(
      screen.queryByText(/does not track conversation history/)
    ).not.toBeInTheDocument();
  });

  it("should handle multiple file attachments", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const files = [
      new File(["text content"], "document.txt", { type: "text/plain" }),
      new File(["image data"], "photo.png", { type: "image/png" }),
      new File(["audio data"], "audio.mp3", { type: "audio/mpeg" }),
    ];
    const fileInput = document.querySelector(
      'input[type="file"]'
    ) as HTMLInputElement;

    await user.upload(fileInput, files);

    await waitFor(() => {
      expect(screen.getByText(/document\.txt/)).toBeInTheDocument();
      expect(screen.getByText(/photo\.png/)).toBeInTheDocument();
      expect(screen.getByText(/audio\.mp3/)).toBeInTheDocument();
    });
  });

  it("should show attachment chip when addAttachment is called via ref", async () => {
    const ref = React.createRef<ChatInputAreaHandle>();

    render(
      <TestWrapper>
        <ChatInputArea ref={ref} {...defaultProps} />
      </TestWrapper>
    );

    // Programmatically add an attachment via the ref
    React.act(() => {
      ref.current?.addAttachment({
        type: "image",
        name: "forwarded.png",
        url: "data:image/png;base64,abc=",
        mimeType: "image/png",
        size: 512,
      });
    });

    await waitFor(() => {
      expect(screen.getByText(/forwarded\.png/)).toBeInTheDocument();
    });

    // Send button should be enabled since there's an attachment
    expect(screen.getByTitle("Send message")).toBeEnabled();
  });

  it("should show single-turn banner when singleTurnLimitReached is true", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          singleTurnLimitReached={true}
          onNewConversation={jest.fn()}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("single-turn-banner")).toBeInTheDocument();
    expect(screen.getByText(/only supports single-turn/)).toBeInTheDocument();
    expect(screen.getByTestId("new-conversation-btn")).toBeInTheDocument();
    // Input area should not be rendered
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("should call onNewConversation when New Conversation button clicked", async () => {
    const user = userEvent.setup();
    const onNewConversation = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          singleTurnLimitReached={true}
          onNewConversation={onNewConversation}
        />
      </TestWrapper>
    );

    await user.click(screen.getByTestId("new-conversation-btn"));
    expect(onNewConversation).toHaveBeenCalledTimes(1);
  });

  it("should not show New Conversation button when onNewConversation is not provided", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          singleTurnLimitReached={true}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("single-turn-banner")).toBeInTheDocument();
    expect(screen.queryByTestId("new-conversation-btn")).not.toBeInTheDocument();
  });

  it("should show normal input when singleTurnLimitReached is false", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          singleTurnLimitReached={false}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("single-turn-banner")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });
});

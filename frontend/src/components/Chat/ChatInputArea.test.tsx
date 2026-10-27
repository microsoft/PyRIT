import React from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import ChatInputArea from "./ChatInputArea";
import type { ChatInputAreaHandle } from "./ChatInputArea";
import { makeTarget } from "@/test-utils/targetFixtures";
import type { MessageAttachment, TargetCapabilities } from "@/types";

// Wrapper component for Fluent UI context
const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
);

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

// Helper to get the send button specifically
const getSendButton = () => screen.getByRole("button", { name: "Send message", exact: true });

describe("ChatInputArea", () => {
  const sentOutcome = { status: "sent", clearDraft: true } as const;
  const retryableOutcome = {
    status: "retryable_failure",
    clearDraft: false,
  } as const;
  const defaultProps = {
    onSend: jest.fn().mockResolvedValue(sentOutcome),
    disabled: false,
    onUseAsTemplate: jest.fn(),
    onConfigureTarget: jest.fn(),
    onToggleConverterPanel: jest.fn(),
    isConverterPanelOpen: false,
    onInputChange: jest.fn(),
    onAttachmentsChange: jest.fn(),
    onClearConversion: jest.fn(),
    onClearAllConversions: jest.fn(),
    onConvertedValueChange: jest.fn(),
    onClearMediaConversion: jest.fn(),
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
    expect(screen.getByRole("button", { name: /convert/i })).toHaveAttribute(
      "data-tour",
      "converter-toggle"
    );
  });

  it("should call converter panel toggle handler when convert button is clicked", async () => {
    const user = userEvent.setup();
    const onToggleConverterPanel = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onToggleConverterPanel={onToggleConverterPanel}
        />
      </TestWrapper>
    );

    await user.click(screen.getByRole("button", { name: /convert/i }));

    expect(onToggleConverterPanel).toHaveBeenCalledTimes(1);
  });

  it("should place Undo directly after the converter button inside the prompt box without opening the panel", async () => {
    const user = userEvent.setup();
    const onUndo = jest.fn();
    render(<TestWrapper><ChatInputArea {...defaultProps}
      undoLastSend={{ isUndoing: false, onUndo }} /></TestWrapper>);
    const prompt = screen.getByTestId("prompt-editor");
    const converter = within(prompt).getByRole("button", { name: "Toggle converter panel" });
    const undo = within(prompt).getByRole("button", { name: "Undo last send" });
    expect(converter.compareDocumentPosition(undo) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(undo);
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(defaultProps.onToggleConverterPanel).not.toHaveBeenCalled();
  });

  it("should keep Undo reachable for an eligible first-turn undo when a single-turn prompt is disabled", async () => {
    const user = userEvent.setup();
    const onUndo = jest.fn();
    render(<TestWrapper><ChatInputArea {...defaultProps} disabled
      disabledReasons={["This target only supports single-turn conversations."]}
      undoLastSend={{ isUndoing: false, onUndo }} /></TestWrapper>);
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Toggle converter panel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Undo last send" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Undo last send" }));
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("single-turn-banner")).not.toBeInTheDocument();
  });

  it("should retain the disabled Undo explanation in the prompt box", async () => {
    const user = userEvent.setup();
    const onUndo = jest.fn();
    const reason = "The converter state is no longer available.";
    render(<TestWrapper><ChatInputArea {...defaultProps}
      undoLastSend={{ isUndoing: false, disabledReason: reason, onUndo }} /></TestWrapper>);
    expect(screen.getByRole("button", { name: "Undo last send" })).toBeDisabled();
    await user.hover(screen.getByLabelText(reason));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(reason);
    expect(onUndo).not.toHaveBeenCalled();
  });

  it("should allow drafting without a target and keep Send disabled without a warning box", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} activeTarget={null} sendDisabled />
      </TestWrapper>
    );

    expect(screen.getByRole("textbox")).toBeEnabled();
    expect(getSendButton()).toHaveAttribute("aria-disabled", "true");
    await user.type(screen.getByRole("textbox"), "Unsent draft");
    expect(screen.queryByText(/No target is selected/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Why the prompt box is disabled")).not.toBeVisible();
    await user.click(getSendButton());
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  it("should show all disabled reasons on hover and keyboard activation", async () => {
    jest.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    const rendered = render(<TestWrapper><ChatInputArea {...defaultProps} disabled
      disabledReasons={["This conversation contains a target error.", "The attack is loading."]} /></TestWrapper>);
    try {
      const composer = screen.getByLabelText("Why the prompt box is disabled");
      await user.hover(composer);
      act(() => { jest.advanceTimersByTime(700); });
      const explanation = within(screen.getByRole("group", { name: "Prompt input is disabled" }));
      expect(explanation.getAllByRole("listitem")).toHaveLength(2);
      expect(explanation.getByText("This conversation contains a target error.")).toBeVisible();
      expect(explanation.getByText("The attack is loading.")).toBeVisible();
      composer.focus();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("group", { name: "Prompt input is disabled" })).not.toBeInTheDocument();
      composer.focus();
      await user.keyboard("{Enter}");
      expect(screen.getByRole("group", { name: "Prompt input is disabled" })).toBeVisible();
    } finally {
      rendered.unmount();
      jest.useRealTimers();
    }
  });

  it("should wait 700 ms before opening the disabled-input explanation on hover", async () => {
    jest.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    const rendered = render(<TestWrapper><ChatInputArea {...defaultProps} disabled /></TestWrapper>);
    try {
      await user.hover(screen.getByLabelText("Why the prompt box is disabled"));
      act(() => { jest.advanceTimersByTime(699); });
      expect(screen.queryByText("Prompt input is disabled")).not.toBeInTheDocument();
      act(() => { jest.advanceTimersByTime(1); });
      expect(screen.getByText("Prompt input is disabled")).toBeInTheDocument();
    } finally {
      rendered.unmount();
      jest.useRealTimers();
    }
  });

  it("should cancel a brief hover and still open immediately on click or Enter", async () => {
    jest.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    const rendered = render(<TestWrapper><ChatInputArea {...defaultProps} disabled /></TestWrapper>);
    try {
      const overlay = screen.getByLabelText("Why the prompt box is disabled");
      await user.hover(overlay);
      act(() => { jest.advanceTimersByTime(300); });
      await user.unhover(overlay);
      act(() => { jest.advanceTimersByTime(700); });
      expect(screen.queryByText("Prompt input is disabled")).not.toBeInTheDocument();
      await user.click(overlay);
      expect(screen.getByText("Prompt input is disabled")).toBeInTheDocument();
      await user.keyboard("{Escape}");
      overlay.focus();
      await user.keyboard("{Enter}");
      expect(screen.getByText("Prompt input is disabled")).toBeInTheDocument();
    } finally {
      rendered.unmount();
      jest.useRealTimers();
    }
  });

  it("should explain a temporary disabled state even when no reason was supplied", async () => {
    const user = userEvent.setup();
    render(<TestWrapper><ChatInputArea {...defaultProps} activeTarget={makeTarget({})} disabled /></TestWrapper>);
    await user.click(screen.getByLabelText("Why the prompt box is disabled"));
    expect(screen.getByRole("listitem")).toHaveTextContent("Wait for the current operation to finish.");
  });

  it("should explain an empty draft in the Send tooltip and restore the label when ready", async () => {
    const user = userEvent.setup();
    render(<TestWrapper><ChatInputArea {...defaultProps} /></TestWrapper>);
    await user.hover(getSendButton());
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Send message: Enter a prompt or attach a file.");
    await user.unhover(getSendButton());
    await user.type(screen.getByRole("textbox"), "Ready to send");
    await user.hover(getSendButton());
    expect(await screen.findByRole("tooltip")).toHaveTextContent(/^Send message$/);
    expect(getSendButton()).not.toHaveAttribute("aria-disabled");
  });

  it("should explain all Send blockers without disabling drafting", async () => {
    const user = userEvent.setup();
    render(<TestWrapper><ChatInputArea {...defaultProps} sendDisabled sendDisabledReasons={[
      "Select a target to send a message.",
      "Choose a saved Operation, register, or clear this one to start new attacks and scans.",
    ]} /></TestWrapper>);
    await user.type(screen.getByRole("textbox"), "Keep this draft");
    await user.hover(getSendButton());
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Send message: Select a target to send a message.");
    expect(tooltip).toHaveTextContent("Choose a saved Operation, register, or clear this one");
    expect(screen.getByRole("textbox")).toBeEnabled();
    expect(getSendButton()).toHaveAttribute("aria-disabled", "true");
    await user.click(getSendButton());
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  it("should explain disabled input in the Send tooltip on keyboard focus", async () => {
    const user = userEvent.setup();
    render(<TestWrapper><ChatInputArea {...defaultProps} disabled
      disabledReasons={["A message is being sent. Wait for the response."]} /></TestWrapper>);
    await user.tab();
    expect(getSendButton()).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Send message: A message is being sent.");
    await user.keyboard("{Enter} ");
    await user.click(getSendButton());
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  it("should explain a temporary Send blocker when no reason was supplied", async () => {
    const user = userEvent.setup();
    render(<TestWrapper><ChatInputArea {...defaultProps} sendDisabled /></TestWrapper>);
    await user.hover(getSendButton());
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Send message: Sending is temporarily unavailable.");
  });

  it("should explain unsupported converter output in the Send tooltip", async () => {
    const user = userEvent.setup();
    render(<TestWrapper><ChatInputArea {...defaultProps} converterOutputDataTypes={["audio_path"]}
      activeTarget={makeTarget({ capabilities: buildCapabilities({ supported_input_modalities: ["text"] }) })} /></TestWrapper>);
    await user.type(screen.getByRole("textbox"), "Convert this");
    await user.hover(getSendButton());
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Send message: The selected converter produces audio output, which this target does not support."
    );
    expect(getSendButton()).toHaveAttribute("aria-disabled", "true");
  });

  it("should show a retry action when target verification fails", async () => {
    const user = userEvent.setup();
    const onRetryTargetResolution = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          targetResolutionStatus="error"
          onRetryTargetResolution={onRetryTargetResolution}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("target-resolution-error-banner")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetryTargetResolution).toHaveBeenCalledTimes(1);
  });

  it("should direct ambiguous target identities to remove duplicates and retry", async () => {
    const user = userEvent.setup();
    const onRetryTargetResolution = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          targetResolutionStatus="ambiguous"
          onRetryTargetResolution={onRetryTargetResolution}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("target-resolution-ambiguous-banner")).toBeInTheDocument();
    expect(screen.getByText(/remove duplicate registrations/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetryTargetResolution).toHaveBeenCalledTimes(1);
  });

  it("should keep legacy attacks read-only while allowing a new-attack template", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "explicit-target" })}
          targetResolutionStatus="legacy"
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("target-resolution-legacy-banner")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /continue with your target/i }));
    expect(defaultProps.onUseAsTemplate).toHaveBeenCalledTimes(1);
  });

  it("should call onSend with input value when send button clicked", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);

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

  it("should bound repetitions from one to ten and default to shared conversion", async () => {
    const user = userEvent.setup();
    render(<TestWrapper><ChatInputArea {...defaultProps} /></TestWrapper>);
    await user.click(screen.getByRole("button", { name: "Repetitions: 1" }));
    const decrease = screen.getByRole("button", { name: "Decrease repetitions" });
    const increase = screen.getByRole("button", { name: "Increase repetitions" });
    expect(decrease).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Convert once, reuse for all" })).toBeChecked();
    const converterHelp = screen.getByText(/completed conversions are added to the prompt automatically/i);
    expect(converterHelp).toHaveTextContent("Shared repeats use that prompt.");
    expect(converterHelp).toHaveTextContent("Independent repeats run unapplied pipelines on original inputs.");
    expect(converterHelp).toHaveTextContent("Repeated sends are unavailable for edited or partially converted prompts.");
    for (let count = 1; count < 10; count++) await user.click(increase);
    expect(increase).toBeDisabled();
    expect(screen.getByRole("button", { name: "Repetitions: 10" })).toHaveTextContent("n=10");
    for (let count = 10; count > 1; count--) await user.click(decrease);
    expect(decrease).toBeDisabled();
  });

  it("should disable repeated sends with a hover explanation and submit edited text only once", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);
    const reason = "Repeated sends are unavailable for edited or partially converted prompts.";
    const { rerender } = render(<TestWrapper><ChatInputArea {...defaultProps} onSend={onSend} /></TestWrapper>);
    await user.type(screen.getByRole("textbox"), "Source prompt");
    await user.click(screen.getByRole("button", { name: "Repetitions: 1" }));
    await user.click(screen.getByRole("button", { name: "Increase repetitions" }));
    await user.keyboard("{Escape}");
    rerender(<TestWrapper><ChatInputArea {...defaultProps} onSend={onSend}
      convertedValue="Edited output" repeatDisabledReason={reason} /></TestWrapper>);
    expect(screen.getByRole("button", { name: "Repetitions: 1" })).toBeDisabled();
    await user.hover(screen.getByLabelText(reason));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(reason);
    await user.click(getSendButton());
    expect(onSend).toHaveBeenCalledWith("Source prompt", "Edited output", []);
  });

  it.each(["Enter", "Send"])("should submit the same repeat options with %s and reset before completion", async (action: string) => {
    const user = userEvent.setup();
    let settle: (value: typeof retryableOutcome) => void = () => {};
    const onSend = jest.fn(() => new Promise<typeof retryableOutcome>((resolve) => { settle = resolve; }));
    render(<TestWrapper><ChatInputArea {...defaultProps} onSend={onSend} /></TestWrapper>);
    await user.type(screen.getByRole("textbox"), "Repeat this");
    await user.click(screen.getByRole("button", { name: "Repetitions: 1" }));
    await user.click(screen.getByRole("button", { name: "Increase repetitions" }));
    await user.click(screen.getByRole("radio", { name: "Convert independently for each" }));
    await user.keyboard("{Escape}");
    if (action === "Enter") {
      await user.click(screen.getByRole("textbox"));
      await user.keyboard("{Enter}");
    } else {
      await user.click(getSendButton());
    }
    expect(onSend).toHaveBeenCalledWith("Repeat this", undefined, [], { count: 2, requestConverterMode: "per_branch" });
    const settings = screen.getByRole("button", { name: "Repetitions: 1" });
    expect(settings).toHaveTextContent("n=1");
    await user.click(settings);
    await user.click(screen.getByRole("button", { name: "Increase repetitions" }));
    settle(retryableOutcome);
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Repeat this"));
    expect(screen.getByRole("button", { name: "Repetitions: 2" })).toHaveTextContent("n=2");
  });

  it("should keep ordinary sends on the existing callback contract", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);
    render(<TestWrapper><ChatInputArea {...defaultProps} onSend={onSend} /></TestWrapper>);
    await user.type(screen.getByRole("textbox"), "One send{Enter}");
    expect(onSend).toHaveBeenCalledWith("One send", undefined, []);
    expect(screen.getByRole("button", { name: "Repetitions: 1" })).toBeInTheDocument();
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
    expect(sendButton).toHaveAttribute("aria-disabled", "true");
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
    expect(sendButton).not.toHaveAttribute("aria-disabled");
  });

  it("should allow draft edits while sending is disabled", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);
    const rendered = render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} sendDisabled />
      </TestWrapper>
    );
    const input = screen.getByRole("textbox");
    await user.type(input, "Draft while history loads");
    expect(input).toBeEnabled();
    expect(getSendButton()).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Enter}");
    await user.click(getSendButton());
    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("Draft while history loads");

    rendered.rerender(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} sendDisabled={false} />
      </TestWrapper>
    );
    await user.click(getSendButton());
    expect(onSend).toHaveBeenCalledWith("Draft while history loads", undefined, []);
    expect(input).toHaveValue("");
  });

  it("should clear input after a successful send", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);

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

  it("should preserve the draft when the send finishes in another conversation", async () => {
    const user = userEvent.setup();
    const onClearAllConversions = jest.fn();
    const onSend = jest.fn().mockResolvedValue({
      status: "sent",
      clearDraft: false,
    });

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onSend={onSend}
          onClearAllConversions={onClearAllConversions}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "keep this draft");
    await user.click(getSendButton());

    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
    expect(input).toHaveValue("keep this draft");
    expect(onClearAllConversions).not.toHaveBeenCalled();
  });

  it("should not clear a newer draft when an earlier send completes", async () => {
    const user = userEvent.setup();
    const onClearAllConversions = jest.fn();
    let resolveSend: ((outcome: typeof sentOutcome) => void) | undefined;
    const onSend = jest.fn(
      () => new Promise<typeof sentOutcome>((resolve) => {
        resolveSend = resolve;
      })
    );

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onSend={onSend}
          onClearAllConversions={onClearAllConversions}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "conversation A draft");
    await user.click(getSendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));

    await user.clear(input);
    await user.type(input, "conversation B draft");
    resolveSend?.(sentOutcome);

    await waitFor(() => {
      expect(input).toHaveValue("conversation B draft");
    });
    expect(onClearAllConversions).not.toHaveBeenCalled();
  });

  it("should not clear a draft whose converter selection changed during a send", async () => {
    const user = userEvent.setup();
    const onClearAllConversions = jest.fn();
    let resolveSend: ((outcome: typeof sentOutcome) => void) | undefined;
    const onSend = jest.fn(
      () => new Promise<typeof sentOutcome>((resolve) => {
        resolveSend = resolve;
      })
    );

    const rendered = render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onSend={onSend}
          conversionRevisionKey="converter-a"
          onClearAllConversions={onClearAllConversions}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "draft with converter");
    await user.click(getSendButton());
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));

    rendered.rerender(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onSend={onSend}
          conversionRevisionKey="converter-b"
          onClearAllConversions={onClearAllConversions}
        />
      </TestWrapper>
    );
    resolveSend?.(sentOutcome);

    await waitFor(() => {
      expect(input).toHaveValue("draft with converter");
    });
    expect(onClearAllConversions).not.toHaveBeenCalled();
  });

  it("should preserve the complete draft after a retryable send failure", async () => {
    const user = userEvent.setup();
    const ref = React.createRef<ChatInputAreaHandle>();
    const onSend = jest.fn().mockResolvedValue(retryableOutcome);
    const onClearConversion = jest.fn();
    const onClearAllConversions = jest.fn();
    const onClearMediaConversion = jest.fn();
    const file = new File(["image"], "photo.png", { type: "image/png" });

    render(
      <TestWrapper>
        <ChatInputArea
          ref={ref}
          {...defaultProps}
          onSend={onSend}
          onClearConversion={onClearConversion}
          onClearAllConversions={onClearAllConversions}
          onClearMediaConversion={onClearMediaConversion}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "T",
            endpoint: "e",
            model_name: "m",
            capabilities: buildCapabilities({
              supported_input_modalities: ["text", "image_path"],
            }),
          })}
          convertedValue="converted prompt"
          originalValue="original prompt"
          converterOutputDataTypes={["text", "image_path"]}
          mediaConversions={[
            {
              pieceId: "restored-photo",
              convertedValue: "/media/converted.png",
              convertedDataType: "image_path",
            },
          ]}
        />
      </TestWrapper>
    );

    const input = screen.getByPlaceholderText("Type prompt here");
    React.act(() => {
      ref.current?.restoreDraft("original prompt", [{
        draftId: "restored-photo",
        type: "image",
        name: file.name,
        url: "blob:photo",
        file,
        mimeType: file.type,
      }]);
    });

    await user.click(getSendButton());

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledTimes(1);
      expect(input).toHaveValue("original prompt");
    });
    expect(screen.getByText("photo.png", { exact: false })).toBeInTheDocument();
    expect(screen.getByTestId("converted-value-input")).toHaveValue("converted prompt");
    expect(screen.getByText("converted.png")).toBeInTheDocument();
    expect(onClearConversion).not.toHaveBeenCalled();
    expect(onClearAllConversions).not.toHaveBeenCalled();
    expect(onClearMediaConversion).not.toHaveBeenCalled();
  });

  it("should send message on Enter key press", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);

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
    const onSend = jest.fn().mockResolvedValue(sentOutcome);

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
    const onSend = jest.fn().mockResolvedValue(sentOutcome);

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
    const onSend = jest.fn().mockResolvedValue(sentOutcome);

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} onSend={onSend} />
      </TestWrapper>
    );

    const sendButton = getSendButton();
    expect(sendButton).toHaveAttribute("aria-disabled", "true");
  });

  it("should have file input for attachments", () => {
    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    const attachButton = screen.getByRole("button", { name: /attach files/i });
    expect(attachButton).toBeInTheDocument();
  });

  it("should expose all icon-only buttons via aria-label, not only title", () => {
    // Regression guard against using only `title=` for icon buttons.
    // `title` is not a reliable accessible name for screen readers; aria-label
    // is. All icon-only buttons in the input area must be reachable by role
    // + name (i.e., they have an aria-label that matches the visible tooltip).
    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByRole("button", { name: /attach files/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /toggle converter panel/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send message/i })).toBeInTheDocument();
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
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
        />
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

    // Click the dismiss button for the first attachment
    await user.click(screen.getByTestId("remove-attachment-0"));

    await waitFor(() => {
      expect(screen.queryByText(/remove-me\.txt/)).not.toBeInTheDocument();
    });
  });

  it("should send with attachments even without text", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);

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
    expect(sendButton).not.toHaveAttribute("aria-disabled");
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
          activeTarget={makeTarget({
            target_registry_name: "test",
            target_type: "TextTarget",
            capabilities: buildCapabilities({ supports_multi_turn: false }),
          })}
        />
      </TestWrapper>
    );

    expect(
      screen.getByText(
        /only supports single-turn conversations/
      )
    ).toBeInTheDocument();
  });

  it("should not show single-turn warning when target supports multiturn chat", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "test",
            target_type: "OpenAIChatTarget",
            capabilities: buildCapabilities({ supports_multi_turn: true }),
          })}
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
    expect(screen.getByRole("button", { name: /send message/i })).not.toHaveAttribute("aria-disabled");
  });

  it("should synchronously report each recovered and uploaded attachment with a distinct draft identity", () => {
    const ref = React.createRef<ChatInputAreaHandle>();
    const onAttachmentsChange = jest.fn();
    const file = new File(["new image"], "uploaded.png", { type: "image/png" });
    render(
      <TestWrapper>
        <ChatInputArea ref={ref} {...defaultProps} onAttachmentsChange={onAttachmentsChange} />
      </TestWrapper>
    );

    React.act(() => {
      ref.current?.addAttachment({
        type: "image",
        name: "restored.png",
        url: "/api/media?path=display-only.png",
        sourceValue: "/original/restored.png",
        sourceDataType: "image_path",
        mimeType: "image/png",
      });
      ref.current?.addAttachment({
        type: "image",
        name: "uploaded.png",
        url: "blob:uploaded-image",
        file,
        mimeType: "image/png",
      });
    });

    expect(onAttachmentsChange).toHaveBeenLastCalledWith([
      expect.objectContaining({
        draftId: expect.any(String),
        type: "image",
        sourceValue: "/original/restored.png",
        sourceDataType: "image_path",
        url: "/api/media?path=display-only.png",
      }),
      expect.objectContaining({
        draftId: expect.any(String),
        type: "image",
        url: "blob:uploaded-image",
        file,
      }),
    ]);
    const [attachments]: [MessageAttachment[]] = onAttachmentsChange.mock.lastCall;
    expect(attachments[0].draftId).not.toBe(attachments[1].draftId);
    expect(screen.getByText(/restored\.png/)).toBeInTheDocument();
    expect(screen.getByText(/uploaded\.png/)).toBeInTheDocument();
  });

  it("should preserve restored draft identities and generate missing identities", () => {
    const ref = React.createRef<ChatInputAreaHandle>();
    const onAttachmentsChange = jest.fn();
    const attachments: MessageAttachment[] = [
      { draftId: "saved-image", type: "image", name: "photo.png", url: "blob:first" },
      { type: "image", name: "photo.png", url: "blob:second" },
    ];
    render(
      <TestWrapper>
        <ChatInputArea ref={ref} {...defaultProps} onAttachmentsChange={onAttachmentsChange} />
      </TestWrapper>
    );

    React.act(() => {
      ref.current?.restoreDraft("Recovered prompt", attachments);
    });

    expect(screen.getByRole("textbox")).toHaveValue("Recovered prompt");
    expect(onAttachmentsChange).toHaveBeenLastCalledWith([
      attachments[0],
      { ...attachments[1], draftId: expect.any(String) },
    ]);
    const [restored]: [MessageAttachment[]] = onAttachmentsChange.mock.lastCall;
    expect(restored[1].draftId).not.toBe("saved-image");
    expect(attachments[1].draftId).toBeUndefined();

    React.act(() => {
      ref.current?.restoreDraft("Restored again", restored);
    });
    expect(onAttachmentsChange).toHaveBeenLastCalledWith(restored);

    React.act(() => {
      ref.current?.addAttachment(restored[0]);
    });
    const [withNewCopy]: [MessageAttachment[]] = onAttachmentsChange.mock.lastCall;
    expect(withNewCopy.slice(0, 2)).toEqual(restored);
    expect(withNewCopy[2]).toEqual({ ...restored[0], draftId: expect.any(String) });
    expect(new Set(withNewCopy.map((attachment: MessageAttachment) => attachment.draftId)).size).toBe(3);
  });

  it("should retain restored text metadata on edits and retries and clear it with the draft", async () => {
    const user = userEvent.setup();
    const ref = React.createRef<ChatInputAreaHandle>();
    const metadata = { video_id: "existing-video" };
    const onSend = jest.fn().mockResolvedValueOnce(retryableOutcome).mockResolvedValue(sentOutcome);
    render(<TestWrapper><ChatInputArea ref={ref} {...defaultProps} onSend={onSend} /></TestWrapper>);
    React.act(() => { ref.current?.restoreDraft("Change the background", [], metadata); });
    await user.type(screen.getByRole("textbox"), " to blue");
    expect(ref.current?.getTextMetadata()).toEqual(metadata);
    await user.click(getSendButton());
    expect(ref.current?.getTextMetadata()).toEqual(metadata);
    await user.click(getSendButton());
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
    expect(ref.current?.getTextMetadata()).toBeUndefined();
    React.act(() => { ref.current?.restoreDraft("Another remix", [], metadata); });
    React.act(() => { ref.current?.setText("An unrelated copied prompt"); });
    expect(ref.current?.getTextMetadata()).toBeUndefined();
    React.act(() => { ref.current?.restoreDraft("Another remix", [], metadata); });
    await user.clear(screen.getByRole("textbox"));
    expect(ref.current?.getTextMetadata()).toBeUndefined();
  });

  it("should keep uploaded duplicate filenames distinct and preserve remaining identity on removal and send", async () => {
    const user = userEvent.setup();
    const onAttachmentsChange = jest.fn();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onAttachmentsChange={onAttachmentsChange}
          onSend={onSend}
        />
      </TestWrapper>
    );
    const files = [
      new File(["first image"], "photo.png", { type: "image/png" }),
      new File(["second image"], "photo.png", { type: "image/png" }),
    ];
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, files);

    const [uploaded]: [MessageAttachment[]] = onAttachmentsChange.mock.lastCall;
    expect(uploaded).toHaveLength(2);
    expect(uploaded[0]).toEqual(expect.objectContaining({ draftId: expect.any(String), file: files[0] }));
    expect(uploaded[1]).toEqual(expect.objectContaining({ draftId: expect.any(String), file: files[1] }));
    expect(uploaded[0].draftId).not.toBe(uploaded[1].draftId);

    await user.click(screen.getByTestId("remove-attachment-0"));
    expect(onAttachmentsChange).toHaveBeenLastCalledWith([uploaded[1]]);
    await user.click(getSendButton());
    expect(onSend).toHaveBeenCalledWith("", undefined, [uploaded[1]]);
    expect(onAttachmentsChange).toHaveBeenLastCalledWith([]);
  });

  it("should render attachment chip without size label when size is undefined", async () => {
    // Regression guard for the media-chip bug: when an attachment forwarded
    // via "Copy to input box in a new conversation" has no known size (e.g.
    // its url is a /api/media?path=... reference), the chip must not print a
    // bogus "(... B)" derived from the URL string length.
    const ref = React.createRef<ChatInputAreaHandle>();

    render(
      <TestWrapper>
        <ChatInputArea ref={ref} {...defaultProps} />
      </TestWrapper>
    );

    React.act(() => {
      ref.current?.addAttachment({
        type: "image",
        name: "forwarded.png",
        url: "/api/media?path=%2Fdbdata%2Fprompt-memory-entries%2Fimages%2Fimg.png",
        mimeType: "image/png",
        // no size
      });
    });

    await waitFor(() => {
      expect(screen.getByText(/forwarded\.png/)).toBeInTheDocument();
    });

    // No "(NNN B)" / "(N.N KB)" / "(N.N MB)" label should be rendered.
    expect(screen.queryByText(/\(\s*\d+(?:\.\d+)?\s*[KM]?B\s*\)/)).toBeNull();
  });

  it.each([false, true])("keeps the disabled single-turn composer visible with Undo present=%s", async (withUndo: boolean) => {
    const user = userEvent.setup();
    render(<TestWrapper><ChatInputArea {...defaultProps} disabled
      disabledReasons={["This target only supports single-turn conversations."]}
      undoLastSend={withUndo ? { onUndo: jest.fn(), isUndoing: false, disabledReason: "No saved converter state." } : undefined}
    /></TestWrapper>);
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.queryByTestId("single-turn-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-conversation-btn")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Why the prompt box is disabled"));
    expect(screen.getByRole("listitem")).toHaveTextContent("This target only supports single-turn conversations.");
    expect(screen.queryByText(/Wait for the current operation/)).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.hover(getSendButton());
    expect(await screen.findByRole("tooltip")).toHaveTextContent("This target only supports single-turn conversations.");
    await user.click(getSendButton());
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  it("should show normal input before a single-turn send", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("single-turn-banner")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Converter integration: attachment with media conversions
  // ---------------------------------------------------------------------------

  it("should show a media conversion only on its matching draft piece even with duplicate filenames", async () => {
    const ref = React.createRef<ChatInputAreaHandle>();
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          ref={ref}
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          mediaConversions={[{ pieceId: "second-image", convertedValue: "/media/converted.png", convertedDataType: "image_path" }]}
        />
      </TestWrapper>
    );

    React.act(() => {
      ref.current?.restoreDraft("", [
        { draftId: "first-image", type: "image", name: "photo.png", url: "blob:first" },
        { draftId: "second-image", type: "image", name: "photo.png", url: "blob:second" },
      ]);
    });

    expect(screen.getAllByText(/photo\.png/)).toHaveLength(2);
    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(screen.getByText("Converted")).toBeInTheDocument();
    expect(screen.getByText("converted.png")).toBeInTheDocument();

    await user.click(screen.getByTestId("remove-attachment-0"));
    expect(screen.getByText("converted.png")).toBeInTheDocument();
    await user.click(screen.getByTestId("remove-attachment-0"));
    expect(screen.queryByText("converted.png")).not.toBeInTheDocument();
    expect(screen.queryByText("Converted")).not.toBeInTheDocument();
  });

  it("should clear only the selected media piece when attachments have the same type and filename", async () => {
    const ref = React.createRef<ChatInputAreaHandle>();
    const user = userEvent.setup();
    const onClearMediaConversion = jest.fn();
    const conversions = [
      { pieceId: "second-image", convertedValue: "/media/second-converted.png", convertedDataType: "image_path" },
      { pieceId: "first-image", convertedValue: "/media/first-converted.png", convertedDataType: "image_path" },
    ];
    const props = {
      ...defaultProps,
      onClearMediaConversion,
    };

    const rendered = render(
      <TestWrapper>
        <ChatInputArea
          ref={ref}
          {...props}
          mediaConversions={conversions}
        />
      </TestWrapper>
    );

    React.act(() => {
      ref.current?.restoreDraft("", [
        { draftId: "first-image", type: "image", name: "photo.png", url: "blob:first" },
        { draftId: "second-image", type: "image", name: "photo.png", url: "blob:second" },
      ]);
    });

    expect(screen.getByText("first-converted.png")).toBeInTheDocument();
    expect(screen.getByText("second-converted.png")).toBeInTheDocument();
    await user.click(screen.getAllByTestId("clear-media-conversion-image")[1]);
    expect(onClearMediaConversion).toHaveBeenCalledTimes(1);
    expect(onClearMediaConversion).toHaveBeenLastCalledWith("second-image");

    rendered.rerender(
      <TestWrapper>
        <ChatInputArea ref={ref} {...props} mediaConversions={[conversions[1]]} />
      </TestWrapper>
    );
    expect(screen.queryByText("second-converted.png")).not.toBeInTheDocument();
    expect(screen.getByText("first-converted.png")).toBeInTheDocument();
    expect(screen.getAllByText(/photo\.png/)).toHaveLength(2);
    await user.click(screen.getByTestId("clear-media-conversion-image"));
    expect(onClearMediaConversion).toHaveBeenLastCalledWith("first-image");
  });

  it("should show converted value textarea and call onConvertedValueChange", async () => {
    const onConvertedValueChange = jest.fn();
    const onClearConversion = jest.fn();
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedValue="aGVsbG8="
          originalValue="hello"
          onConvertedValueChange={onConvertedValueChange}
          onClearConversion={onClearConversion}
        />
      </TestWrapper>
    );

    // Should show original banner and converted indicator
    expect(screen.getByTestId("original-banner")).toBeInTheDocument();
    expect(screen.getByTestId("converted-indicator")).toBeInTheDocument();

    // Edit the converted value
    const convertedInput = screen.getByRole("textbox", { name: /converted prompt/i });
    expect(convertedInput).toHaveValue("aGVsbG8=");
    await user.clear(convertedInput);
    await user.type(convertedInput, "new");
    expect(onConvertedValueChange).toHaveBeenCalled();

    // Clear the conversion
    await user.click(screen.getByTestId("clear-conversion-btn"));
    expect(onClearConversion).toHaveBeenCalled();
  });

  it("should pass convertedValue to onSend when sending with conversion", async () => {
    const onSend = jest.fn().mockResolvedValue(sentOutcome);
    const onClearAllConversions = jest.fn();
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onSend={onSend}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedValue="convertedHello"
          originalValue="hello"
          onClearAllConversions={onClearAllConversions}
        />
      </TestWrapper>
    );

    const input = screen.getByTestId("chat-input");
    await user.type(input, "hello");
    await user.click(getSendButton());

    expect(onSend).toHaveBeenCalledWith("hello", "convertedHello", []);
    expect(onClearAllConversions).toHaveBeenCalled();
  });

  it("should keep an explicitly empty converted value visible and send it unchanged", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn().mockResolvedValue(sentOutcome);
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onSend={onSend}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedValue=""
          originalValue="original"
        />
      </TestWrapper>
    );
    expect(screen.getByRole("textbox", { name: /converted prompt/i })).toHaveValue("");
    expect(screen.getByTestId("clear-conversion-btn")).toBeInTheDocument();
    await user.type(screen.getByTestId("chat-input"), "original");
    await user.click(getSendButton());
    expect(onSend).toHaveBeenCalledWith("original", "", []);
  });

  it("should render converted file chip with Open link for text→file conversion", async () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedFileChip={{
            name: "result.pdf",
            url: "/api/media?path=%2Ftmp%2Fresult.pdf",
            iconKind: "file",
          }}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("original-banner")).toBeInTheDocument();
    const chip = screen.getByTestId("converted-file-chip");
    expect(chip).toHaveTextContent("result.pdf");
    const openLink = screen.getByTestId("converted-file-open");
    expect(openLink).toHaveAttribute("href", "/api/media?path=%2Ftmp%2Fresult.pdf");
    expect(openLink).toHaveAttribute("target", "_blank");
  });

  it("should call onClearConvertedFileChip when chip dismiss is clicked", async () => {
    const onClearConvertedFileChip = jest.fn();
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedFileChip={{
            name: "result.pdf",
            url: "/api/media?path=%2Ftmp%2Fresult.pdf",
            iconKind: "file",
          }}
          onClearConvertedFileChip={onClearConvertedFileChip}
        />
      </TestWrapper>
    );

    await user.click(screen.getByTestId("clear-converted-file-chip"));
    expect(onClearConvertedFileChip).toHaveBeenCalledTimes(1);
  });

  it("should render an inline image preview for an image conversion", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedFileChip={{
            name: "result.png",
            url: "/api/media?path=%2Ftmp%2Fresult.png",
            iconKind: "image",
          }}
        />
      </TestWrapper>
    );

    const preview = screen.getByTestId("converted-file-preview-image");
    expect(preview.tagName).toBe("IMG");
    expect(preview).toHaveAttribute("src", "/api/media?path=%2Ftmp%2Fresult.png");
    expect(preview).toHaveAttribute("alt", "result.png");
    // Header info (filename + Open link) is still rendered alongside the preview.
    expect(screen.getByTestId("converted-file-chip")).toHaveTextContent("result.png");
    expect(screen.getByTestId("converted-file-open")).toHaveAttribute("href", "/api/media?path=%2Ftmp%2Fresult.png");
  });

  it("should render an inline audio preview for an audio conversion", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedFileChip={{
            name: "speech.wav",
            url: "/api/media?path=%2Ftmp%2Fspeech.wav",
            iconKind: "audio",
          }}
        />
      </TestWrapper>
    );

    const preview = screen.getByTestId("converted-file-preview-audio");
    expect(preview.tagName).toBe("AUDIO");
    expect(preview).toHaveAttribute("src", "/api/media?path=%2Ftmp%2Fspeech.wav");
    expect(preview).toHaveAttribute("controls");
    expect(screen.queryByTestId("converted-file-preview-image")).not.toBeInTheDocument();
    expect(screen.queryByTestId("converted-file-preview-video")).not.toBeInTheDocument();
  });

  it("should render an inline video preview for a video conversion", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedFileChip={{
            name: "clip.mp4",
            url: "/api/media?path=%2Ftmp%2Fclip.mp4",
            iconKind: "video",
          }}
        />
      </TestWrapper>
    );

    const preview = screen.getByTestId("converted-file-preview-video");
    expect(preview.tagName).toBe("VIDEO");
    expect(preview).toHaveAttribute("src", "/api/media?path=%2Ftmp%2Fclip.mp4");
    expect(preview).toHaveAttribute("controls");
  });

  it("should not render a media preview for a generic file conversion", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({ target_registry_name: "t", target_type: "T", endpoint: "e", model_name: "m" })}
          convertedFileChip={{
            name: "result.pdf",
            url: "/api/media?path=%2Ftmp%2Fresult.pdf",
            iconKind: "file",
          }}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("converted-file-preview-image")).not.toBeInTheDocument();
    expect(screen.queryByTestId("converted-file-preview-audio")).not.toBeInTheDocument();
    expect(screen.queryByTestId("converted-file-preview-video")).not.toBeInTheDocument();
  });

  // ---------------------------------------------------------------------------
  // Unsupported modality warnings
  // ---------------------------------------------------------------------------

  it("validates converted attachment types while retaining their unsupported originals", async () => {
    const user = userEvent.setup();
    const ref = { current: null as ChatInputAreaHandle | null };
    const props = {
      ...defaultProps,
      activeTarget: makeTarget({
        target_registry_name: "text", capabilities: buildCapabilities({ supported_input_modalities: ["text"] }),
      }),
    };
    const rendered = render(<TestWrapper><ChatInputArea ref={ref} {...props}
      mediaConversions={[{ pieceId: "image", convertedValue: "Image transcript", convertedDataType: "text" }]} />
    </TestWrapper>);
    act(() => { ref.current?.restoreDraft("", [{
      draftId: "image", type: "image", name: "original.png", url: "/original.png", mimeType: "image/png",
    }]); });
    expect(screen.queryByTestId("unsupported-modality-warning")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send message" })).not.toHaveAttribute("aria-disabled");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(defaultProps.onSend).toHaveBeenCalled();
    rendered.rerender(<TestWrapper><ChatInputArea ref={ref} {...props} mediaConversions={[]} /></TestWrapper>);
    act(() => { ref.current?.restoreDraft("", [{
      draftId: "image", type: "image", name: "original.png", url: "/original.png", mimeType: "image/png",
    }]); });
    expect(screen.getByTestId("unsupported-modality-warning")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send message" })).toHaveAttribute("aria-disabled", "true");
  });

  it("should show warning when attaching image to text-only target", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "TextTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text"] }),
          })}
        />
      </TestWrapper>
    );

    const file = new File(["img"], "photo.png", { type: "image/png" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByTestId("unsupported-modality-warning")).toHaveTextContent(/does not support image attachments/);
    });
    await user.hover(getSendButton());
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "Send message: This target does not support image attachments. Remove them to send."
    );
  });

  it("should not show warning when attaching image to image-capable target", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "OpenAIChatTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text", "image_path"] }),
          })}
        />
      </TestWrapper>
    );

    const file = new File(["img"], "photo.png", { type: "image/png" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByText(/photo\.png/)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("unsupported-modality-warning")).not.toBeInTheDocument();
  });

  it("should not show warning when no target is selected", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea {...defaultProps} activeTarget={null} />
      </TestWrapper>
    );

    const file = new File(["img"], "photo.png", { type: "image/png" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByText(/photo\.png/)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("unsupported-modality-warning")).not.toBeInTheDocument();
  });

  it("should show warning for audio attachment on text+image target", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "OpenAIChatTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text", "image_path"] }),
          })}
        />
      </TestWrapper>
    );

    const file = new File(["audio"], "sound.mp3", { type: "audio/mpeg" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByTestId("unsupported-modality-warning")).toHaveTextContent(/does not support audio attachments/);
    });
  });

  it("should show warning listing multiple unsupported types", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "TextTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text"] }),
          })}
        />
      </TestWrapper>
    );

    const files = [
      new File(["img"], "photo.png", { type: "image/png" }),
      new File(["audio"], "sound.mp3", { type: "audio/mpeg" }),
    ];
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, files);

    await waitFor(() => {
      expect(screen.getByTestId("unsupported-modality-warning")).toHaveTextContent(/does not support image, audio attachments/);
    });
  });

  it("should disable send button with unsupported attachment type", async () => {
    const user = userEvent.setup();
    const onSend = jest.fn();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          onSend={onSend}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "TextTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text"] }),
          })}
        />
      </TestWrapper>
    );

    const file = new File(["img"], "photo.png", { type: "image/png" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByTestId("unsupported-modality-warning")).toBeInTheDocument();
    });

    // Send should be disabled (warning is blocking)
    expect(getSendButton()).toHaveAttribute("aria-disabled", "true");
  });

  it("should show warning for file attachment to text-only target", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "TextTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text"] }),
          })}
        />
      </TestWrapper>
    );

    const file = new File(["pdf content"], "document.pdf", { type: "application/pdf" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByTestId("unsupported-modality-warning")).toHaveTextContent(/does not support file attachments/);
    });
  });

  it("should block sending when converter output type is unsupported by target", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "TextTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text"] }),
          })}
          converterOutputDataTypes={["image_path"]}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "convert this to image");

    expect(screen.getByTestId("unsupported-modality-warning")).toHaveTextContent(
      /selected converter produces image output, which this target does not support/
    );
    expect(getSendButton()).toHaveAttribute("aria-disabled", "true");
  });

  it("should not block when converter output type is supported by target", () => {
    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "OpenAIChatTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text", "image_path"] }),
          })}
          converterOutputDataTypes={["image_path"]}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("unsupported-modality-warning")).not.toBeInTheDocument();
  });

  it("should show both attachment and converter messages when both are unsupported", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <ChatInputArea
          {...defaultProps}
          activeTarget={makeTarget({
            target_registry_name: "t",
            target_type: "TextTarget",
            capabilities: buildCapabilities({ supported_input_modalities: ["text"] }),
          })}
          converterOutputDataTypes={["audio_path"]}
        />
      </TestWrapper>
    );

    const file = new File(["img"], "photo.png", { type: "image/png" });
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, file);

    await waitFor(() => {
      expect(screen.getByTestId("unsupported-modality-warning")).toBeInTheDocument();
    });
    // Attachment message — uses UI piece type
    expect(screen.getByTestId("unsupported-modality-warning")).toHaveTextContent(/does not support image attachments\. Remove them to send\./);
    // Converter message — uses formatted modality label (audio, not audio_path)
    expect(
      screen.getByTestId("unsupported-modality-warning")
    ).toHaveTextContent(/selected converter produces audio output, which this target does not support/);
    expect(getSendButton()).toHaveAttribute("aria-disabled", "true");
  });

  describe("attachment blob URL lifecycle and metadata", () => {
    let revokeSpy: jest.SpyInstance;

    beforeEach(() => {
      revokeSpy = jest.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    });

    afterEach(() => {
      revokeSpy.mockRestore();
    });

    it("preserves initialAttachments blob URLs without calling URL.revokeObjectURL on mount", () => {
      const attachment: MessageAttachment = {
        name: "photo.png",
        type: "image/png",
        url: "blob:http://localhost/active-photo",
        size: 2048,
        draftId: "draft-photo-1",
      };

      render(
        <TestWrapper>
          <ChatInputArea
            {...defaultProps}
            initialAttachments={[attachment]}
            initialText="restored prompt text"
          />
        </TestWrapper>
      );

      expect(screen.getByText(/photo\.png/)).toBeInTheDocument();
      expect(revokeSpy).not.toHaveBeenCalled();
    });

    it("does not revoke blob URL when restoreDraft updates attachments with identical URLs", () => {
      const handleRef = React.createRef<ChatInputAreaHandle>();
      const initialAttachment: MessageAttachment = {
        name: "photo.png",
        type: "image/png",
        url: "blob:http://localhost/active-photo",
        size: 2048,
        draftId: "draft-photo-1",
      };

      render(
        <TestWrapper>
          <ChatInputArea
            ref={handleRef}
            {...defaultProps}
            initialAttachments={[initialAttachment]}
            initialText="prompt text"
          />
        </TestWrapper>
      );

      expect(revokeSpy).not.toHaveBeenCalled();

      // Calling restoreDraft with the same attachment or cloned copy should NOT revoke its URL
      act(() => {
        handleRef.current?.restoreDraft("updated prompt", [{ ...initialAttachment }]);
      });

      expect(revokeSpy).not.toHaveBeenCalled();
    });

    it("revokes blob URL when attachment is explicitly removed", async () => {
      const user = userEvent.setup();
      const attachment: MessageAttachment = {
        name: "photo.png",
        type: "image/png",
        url: "blob:http://localhost/remove-me",
        size: 2048,
        draftId: "draft-photo-2",
      };

      render(
        <TestWrapper>
          <ChatInputArea
            {...defaultProps}
            initialAttachments={[attachment]}
            initialText="prompt text"
          />
        </TestWrapper>
      );

      expect(screen.getByText(/photo\.png/)).toBeInTheDocument();
      expect(revokeSpy).not.toHaveBeenCalled();

      const removeBtn = screen.getByRole("button", { name: "Remove photo.png" });
      await user.click(removeBtn);

      expect(revokeSpy).toHaveBeenCalledTimes(1);
      expect(revokeSpy).toHaveBeenCalledWith("blob:http://localhost/remove-me");
    });

    it("retains initialTextMetadata and exposes via getTextMetadata", () => {
      const handleRef = React.createRef<ChatInputAreaHandle>();
      const metadata = { datasetId: "ds-123", seedIndex: 42 };

      render(
        <TestWrapper>
          <ChatInputArea
            ref={handleRef}
            {...defaultProps}
            initialText="seed prompt"
            initialTextMetadata={metadata}
          />
        </TestWrapper>
      );

      expect(handleRef.current?.getTextMetadata()).toEqual(metadata);
    });
  });
});

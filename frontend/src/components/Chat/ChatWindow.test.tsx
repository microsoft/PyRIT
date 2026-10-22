import { useState } from "react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { createMemoryRouter, MemoryRouter, RouterProvider, Route, Routes, useLocation } from "react-router";
import ChatWindow from "./ChatWindow";
import { makeTarget } from "@/test-utils/targetFixtures";
import { UserPreferencesProvider } from "@/hooks/useUserPreferences";
import * as runtimeHooks from "@/hooks/useRuntime";
import { readUserPreferences } from "@/utils/userPreferences";
import {
  AddMessageResponse,
  AddMessageRequest,
  AttackSummary,
  BackendMessage,
  AttackTargetResolutionStatus,
  ConverterInstance,
  Message,
  MessageAttachment,
  MessageSendConversation,
  MessageSendStatus,
  PromptResponseError,
  RepeatConversionMode,
  RuntimeReadiness,
  TargetCapabilities,
  TargetInfo,
  TargetInstance,
  TargetResponseStatus,
} from "@/types";
import { attacksApi, convertersApi, scoresApi, targetsApi } from "@/services/api";
import * as messageMapper from "@/utils/messageMapper";

jest.mock("./FindingEvidenceDialog", () => ({
  __esModule: true,
  default: ({ attackResultId, disabled, open }: {
    attackResultId: string; disabled: boolean; open: boolean;
  }) => <div data-testid="finding-evidence-dialog" data-attack={attackResultId}
    data-disabled={String(disabled)} data-open={String(open)} />,
}));

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

jest.mock("@/services/api", () => ({
  attacksApi: {
    createAttack: jest.fn(),
    updateAttack: jest.fn(),
    removeHumanScore: jest.fn(),
    submitMessageSend: jest.fn(),
    getMessageSend: jest.fn(),
    getAttack: jest.fn(),
    getMessages: jest.fn(),
    getRelatedConversations: jest.fn(),
    getConversations: jest.fn(),
    createConversation: jest.fn(),
    changeMainConversation: jest.fn(),
    saveConversation: jest.fn(),
  },
  scoresApi: {
    createManualScore: jest.fn(),
  },
  convertersApi: {
    listConverters: jest.fn(),
    listConverterTypes: jest.fn(),
    getConverter: jest.fn(),
    createConverter: jest.fn(),
    deleteConverter: jest.fn(),
    previewConversion: jest.fn(),
  },
  targetsApi: { buildTarget: jest.fn() },
  labelsApi: {
    getLabels: jest.fn().mockImplementation(() => new Promise(() => {})),
  },
}));

jest.mock("@/utils/messageMapper", () => ({
  ...jest.requireActual<typeof import("@/utils/messageMapper")>("@/utils/messageMapper"),
  buildMessagePieces: jest.fn(),
  backendMessageToFrontend: jest.fn(),
  backendMessageToOriginalDraft: jest.fn(),
  backendMessagesToFrontend: jest.fn(),
  fileToBase64: jest.fn(),
}));

const mockedAttacksApi = attacksApi as jest.Mocked<typeof attacksApi>;
// Existing transcript fixtures are reused across the submission, status and read endpoints.
const mockSendResult = jest.fn<Promise<AddMessageResponse>, [string, AddMessageRequest]>();
const mockedConvertersApi = convertersApi as jest.Mocked<typeof convertersApi>;
const mockedScoresApi = scoresApi as jest.Mocked<typeof scoresApi>;
const mockedMapper = messageMapper as jest.Mocked<typeof messageMapper>;
const actualMessageMapper = jest.requireActual<typeof import("@/utils/messageMapper")>(
  "@/utils/messageMapper"
);

function RouterLocation() {
  const location = useLocation();
  return <output aria-label="Current URL">{location.pathname}{location.search}</output>;
}

async function findRecoveryAction(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  const buttons = await screen.findAllByRole("button", { name: "Copy or Branch" });
  const lastButton = buttons.at(-1);
  if (!lastButton) throw new Error("The failed prompt has no copy menu.");
  await user.click(lastButton);
  return screen.findByRole("menuitem", { name: "Branch conversation" });
}

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => (
  <FluentProvider theme={webLightTheme}>
    <UserPreferencesProvider accountKey="local">
      <MemoryRouter>{children}</MemoryRouter>
    </UserPreferencesProvider>
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
  capabilities: buildCapabilities({ supports_editable_history: true, supported_input_modalities: ["text"] }),
});

function makeConverterInstance(
  converterId: string,
  className: string,
  supportedInputTypes = ["text"],
  supportedOutputTypes = ["text"],
): ConverterInstance {
  return {
    converter_id: converterId,
    identifier: {
      class_name: className,
      class_module: `pyrit.converter.${className}`,
      hash: `${converterId}-hash`,
      pyrit_version: "0.0.0",
      supported_input_types: supportedInputTypes,
      supported_output_types: supportedOutputTypes,
    },
    is_llm_based: false,
  };
}

// ---------------------------------------------------------------------------
// Helpers to build mock backend responses
// ---------------------------------------------------------------------------

function makeTextResponse(text: string): Pick<AddMessageResponse, "messages"> {
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

function makeMultiModalResponse(): Pick<AddMessageResponse, "messages"> {
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
              converted_value_url: "/api/media?path=generated.jpeg",
              converted_filename: "generated.jpeg",
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
    defaultsReady: true,
    activeTarget: mockTarget,
    availableTargets: [mockTarget],
    targetsLoading: false,
    targetsError: null,
    onRefreshTargets: jest.fn(),
    onSelectTarget: jest.fn(),
    defaultBranchTarget: null,
    attackResultId: null as string | null,
    conversationId: null as string | null,
    activeConversationId: null as string | null,
    onConversationCreated: jest.fn(),
    onSelectConversation: jest.fn(),
    labels: { operator: 'testuser', operation: 'test_op' },
  };

  async function chooseCount(
    user: ReturnType<typeof userEvent.setup>,
    count: number,
    mode?: RepeatConversionMode,
  ): Promise<void> {
    await user.click(screen.getByRole("button", { name: "Repetitions: 1" }));
    for (let index = 1; index < count; index++) {
      await user.click(screen.getByRole("button", { name: "Increase repetitions" }));
    }
    if (mode) {
      await user.click(screen.getByRole("radio", {
        name: mode === "shared" ? "Convert once, reuse for all" : "Convert independently for each",
      }));
    }
    await user.keyboard("{Escape}");
  }

  it("opens the finding dialog for the viewed attack from Attack Result Details", async () => {
    const user = userEvent.setup();
    mockedAttacksApi.getMessages.mockResolvedValue(makeTextResponse("Saved").messages);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([{ role: "user", content: "Saved", timestamp: "" }]);
    render(<TestWrapper><ChatWindow {...defaultProps} attackResultId="owner" conversationId="main"
      activeConversationId="related" outcome="undetermined" labels={{ operation: "Different toolbar Operation" }} /></TestWrapper>);
    const dialog = screen.getByTestId("finding-evidence-dialog");
    await waitFor(() => { expect(dialog).toHaveAttribute("data-disabled", "false"); });
    expect(dialog).toHaveAttribute("data-attack", "owner");
    expect(dialog).toHaveAttribute("data-open", "false");
    expect(screen.queryByRole("button", { name: "Link to finding" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Objective achieved outcome: undetermined" }));
    await user.click(await screen.findByRole("button", { name: "Add as finding" }));
    expect(dialog).toHaveAttribute("data-open", "true");
    await waitFor(() => { expect(screen.queryByText("Attack Result Details")).not.toBeInTheDocument(); });
  });

  it("keeps the attachment workflow mounted across viewed conversation changes", async () => {
    mockedAttacksApi.getMessages.mockResolvedValue(makeTextResponse("Saved").messages);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([{ role: "user", content: "Saved", timestamp: "" }]);
    const rendered = render(<TestWrapper><ChatWindow {...defaultProps} attackResultId="owner" conversationId="main"
      activeConversationId="related" /></TestWrapper>);
    const dialog = screen.getByTestId("finding-evidence-dialog");
    rendered.rerender(<TestWrapper><ChatWindow {...defaultProps} attackResultId="owner" conversationId="main"
      activeConversationId="another" /></TestWrapper>);
    await waitFor(() => { expect(screen.getByTestId("finding-evidence-dialog")).toBe(dialog); });
    expect(dialog).toHaveAttribute("data-attack", "owner");
  });

  it.each([404, 503])("distinguishes evidence-origin message load status %s", async (status: number) => {
    mockedAttacksApi.getMessages.mockRejectedValue({ isAxiosError: true, response: { status, data: { detail: "source load failed" } } });
    render(<TestWrapper><ChatWindow {...defaultProps} attackResultId="owner" conversationId="main"
      activeConversationId="main" findingEvidenceId="123e4567-e89b-12d3-a456-426614174000" /></TestWrapper>);
    if (status === 404) {
      expect(await screen.findByText("Evidence unavailable")).toBeInTheDocument();
    } else {
      expect(await screen.findByText(/Could not load conversation/)).toBeInTheDocument();
      expect(screen.queryByText("Evidence unavailable")).not.toBeInTheDocument();
    }
  });

  it("reports an evidence-origin successfully empty transcript as unavailable", async () => {
    mockedAttacksApi.getMessages.mockResolvedValue({ conversation_id: "main", messages: [], target_response_status: null });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    render(<TestWrapper><ChatWindow {...defaultProps} attackResultId="owner" conversationId="main"
      activeConversationId="main" findingEvidenceId="123e4567-e89b-12d3-a456-426614174000" /></TestWrapper>);
    expect(await screen.findByText("Evidence unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("finding-evidence-dialog")).toHaveAttribute("data-attack", "");
    expect(screen.getByTestId("finding-evidence-dialog")).toHaveAttribute("data-disabled", "true");
    expect(screen.getByRole("textbox")).toBeDisabled();
  });

  beforeEach(() => {
    jest.clearAllMocks();
    for (const api of [mockedAttacksApi, mockedConvertersApi, mockedScoresApi, mockedMapper]) {
      for (const mock of Object.values(api)) {
        if (jest.isMockFunction(mock)) mock.mockReset();
      }
    }
    mockSendResult.mockReset();
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "default-created-attack",
      conversation_id: "default-created-conversation",
      created_at: "2026-01-01T00:00:00Z",
    });
    const accepted = new Map<string, MessageSendStatus>();
    mockedAttacksApi.submitMessageSend.mockImplementation(async (attackId, request) => {
      const { submission_id: submissionId, ...message } = request;
      const response = await mockSendResult(attackId, message);
      const progress: MessageSendStatus = {
        send_id: submissionId,
        attack_result_id: attackId,
        conversation_id: request.target_conversation_id,
        request_turn_number: response.messages.target_response_status?.request_turn_number ?? null,
        state: "completed",
        error: null,
        failure_stage: null,
      };
      accepted.set(submissionId, progress);
      mockedAttacksApi.getMessages.mockResolvedValueOnce(response.messages);
      mockedAttacksApi.getAttack.mockResolvedValueOnce(response.attack);
      return { ...progress, state: "queued" };
    });
    mockedAttacksApi.getMessageSend.mockImplementation(async (_attackId, sendId) => {
      const progress = accepted.get(sendId);
      if (!progress) { throw new Error("Unexpected send handle"); }
      return progress;
    });
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedMapper.backendMessageToFrontend.mockImplementation(actualMessageMapper.backendMessageToFrontend);
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedMapper.backendMessageToOriginalDraft.mockImplementation(
      actualMessageMapper.backendMessageToOriginalDraft
    );
    mockedMapper.fileToBase64.mockImplementation(actualMessageMapper.fileToBase64);
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
    mockedConvertersApi.listConverterTypes.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  describe("asynchronous sending", () => {
    const props = {
      ...defaultProps,
      attackResultId: "async-attack",
      conversationId: "async-conversation",
      activeConversationId: "async-conversation",
    };
    const queued: MessageSendStatus = {
      send_id: "async-send",
      attack_result_id: props.attackResultId,
      conversation_id: props.conversationId,
      request_turn_number: 0,
      state: "queued",
      error: null,
      failure_stage: null,
    };
    const complete: MessageSendStatus = { ...queued, state: "completed" };
    const empty = { conversation_id: props.conversationId, messages: [], target_response_status: null };
    const reply = { ...makeTextResponse("Async reply").messages, conversation_id: props.conversationId };
    const readError = {
      isAxiosError: true,
      response: { status: 503, data: { detail: "Read temporarily unavailable" } },
    };
    const attack: AttackSummary = {
      attack_result_id: props.attackResultId,
      conversation_id: props.conversationId,
      attack_type: "ManualAttack",
      objective: "",
      outcome: "undetermined",
      converters: [],
      message_count: 2,
      related_conversation_ids: [],
      labels: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:01Z",
    };

    beforeEach(() => {
      mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
      mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
      mockedAttacksApi.getMessages.mockResolvedValue(empty);
      mockedAttacksApi.submitMessageSend.mockResolvedValue(queued);
      mockedAttacksApi.getMessageSend.mockResolvedValue(complete);
      mockedAttacksApi.getAttack.mockResolvedValue(attack);
    });

    async function sendDraft(user: ReturnType<typeof userEvent.setup>): Promise<void> {
      await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
      await user.click(screen.getByRole("textbox"));
      await user.paste("Draft to send");
      await waitFor(() => expect(screen.getByRole("button", { name: /send message/i })).not.toHaveAttribute("aria-disabled"));
      await user.click(screen.getByRole("button", { name: /send message/i }));
    }

    it("keeps a newer conversation refresh when an older completion read returns late", async () => {
      const user = userEvent.setup();
      const onAttackChange = jest.fn();
      let finishMetadata: (value: AttackSummary) => void = () => {};
      mockedAttacksApi.getAttack.mockImplementationOnce(() => new Promise((resolve) => {
        finishMetadata = resolve;
      }));
      mockedAttacksApi.getConversations.mockResolvedValue({
        attack_result_id: props.attackResultId, main_conversation_id: props.conversationId,
        conversations: [{ conversation_id: props.conversationId, message_count: 0 }],
      });
      mockedAttacksApi.getMessages.mockResolvedValueOnce(empty).mockResolvedValueOnce(reply);
      render(<TestWrapper><ChatWindow {...props} relatedConversationCount={1} onAttackChange={onAttackChange} /></TestWrapper>);
      await sendDraft(user);
      await waitFor(() => expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(2));
      const newer = { ...makeTextResponse("Newer conversation reply").messages, conversation_id: props.conversationId };
      mockedAttacksApi.getMessages.mockResolvedValue(newer);
      await user.click(screen.getByRole("button", { name: `Select conversation ${props.conversationId}` }));
      expect(await screen.findByText("Newer conversation reply")).toBeInTheDocument();
      await act(async () => { finishMetadata(attack); });
      expect(screen.getByText("Newer conversation reply")).toBeInTheDocument();
      expect(screen.queryByText("Async reply")).not.toBeInTheDocument();
      expect(screen.queryByText("...")).not.toBeInTheDocument();
      expect(onAttackChange).not.toHaveBeenCalled();
    });

    it("restores a newer failed request from history instead of the earlier successful draft", async () => {
      const user = userEvent.setup();
      const laterFailure = {
        ...makeErrorResponse("processing", "Later request failed", 2).messages, conversation_id: props.conversationId,
      };
      laterFailure.messages[0].message_pieces[0].original_value = "Request from another client";
      laterFailure.messages[0].message_pieces[0].converted_value = "Request from another client";
      mockedAttacksApi.getMessages.mockResolvedValueOnce(empty).mockResolvedValue(laterFailure);
      mockedAttacksApi.createConversation.mockResolvedValue({
        conversation_id: "clean-conversation", created_at: "2026-01-01T00:00:03Z",
      });
      render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
      await sendDraft(user);
      await user.click(await findRecoveryAction(user));
      expect(screen.getByRole("textbox")).toHaveValue("Request from another client");
    });

    it("finishes immediately without advancing a polling timer", async () => {
      jest.useFakeTimers({ doNotFake: ["queueMicrotask"] });
      try {
        const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
        mockedAttacksApi.getMessages.mockResolvedValueOnce(empty).mockResolvedValue(reply);
        render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
        await sendDraft(user);
        await act(async () => { await jest.advanceTimersByTimeAsync(0); });
        expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
        expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(2);
        expect(mockedMapper.backendMessagesToFrontend).toHaveBeenLastCalledWith(reply.messages);
        expect(screen.getByTestId("message-list").textContent).toContain("Async reply");
        expect(screen.getByRole("textbox")).toHaveValue("");
        expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(1);
        expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledWith(props.attackResultId, expect.objectContaining({
          submission_id: expect.any(String), target_conversation_id: props.conversationId, send: true,
        }));
        expect(mockSendResult).not.toHaveBeenCalled();
      } finally {
        jest.useRealTimers();
      }
    });

    it.each([false, true])("refreshes a lost status without resending, edited draft %s", async (edit: boolean) => {
      const user = userEvent.setup();
      mockedAttacksApi.getMessageSend.mockRejectedValueOnce(readError).mockResolvedValue(complete);
      render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
      await sendDraft(user);
      const status = await screen.findByRole("group", { name: "Message send status" });
      expect(status).toHaveTextContent(/Send status is unavailable/);
      expect(status).toBeVisible();
      expect(screen.getByRole("button", { name: /send message/i })).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByRole("button", { name: "Send message" })).toHaveAccessibleDescription(
        /Send message:.*Send status is unavailable/
      );
      expect(screen.getByRole("textbox")).toHaveValue("Draft to send");
      if (edit) {
        await user.clear(screen.getByRole("textbox"));
        await user.type(screen.getByRole("textbox"), "Newer draft");
      }
      mockedAttacksApi.getMessages.mockResolvedValue(reply);
      await user.click(screen.getByRole("button", { name: /refresh saved messages/i }));
      expect(await screen.findByText("Async reply")).toBeInTheDocument();
      expect(screen.getByRole("textbox")).toHaveValue(edit ? "Newer draft" : "");
      expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
      expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(2);
    });

    it.each(["getAttack", "getMessages"] as const)(
      "retains completed work when %s fails and retries only reads",
      async (read) => {
        const user = userEvent.setup();
        render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        mockedAttacksApi[read].mockRejectedValueOnce(readError);
        await sendDraft(user);
        expect(await screen.findByRole("group", { name: "Message send status" })).toHaveTextContent(
          /Saved messages or attack details could not be loaded/
        );
        mockedAttacksApi.getMessages.mockResolvedValue(reply);
        await user.click(screen.getByRole("button", { name: /refresh saved messages/i }));
        expect(await screen.findByText("Async reply")).toBeInTheDocument();
        expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
        expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("textbox")).toHaveValue("");
      },
    );

    it.each(["lost handle", "lost submission"])("keeps %s uncertain even after evidence refresh", async (failure: string) => {
      const user = userEvent.setup();
      if (failure === "lost handle") {
        mockedAttacksApi.getMessageSend.mockRejectedValue({
          isAxiosError: true, response: { status: 404, data: { detail: "Handle expired" } },
        });
      } else {
        mockedAttacksApi.submitMessageSend.mockRejectedValue({ isAxiosError: true, code: "ECONNABORTED" });
      }
      render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
      await sendDraft(user);
      expect(await screen.findByRole("group", { name: "Message send status" })).toHaveTextContent(/Send acceptance is unknown/);
      mockedAttacksApi.getMessages.mockResolvedValue(reply);
      await user.click(screen.getByRole("button", { name: /refresh saved messages/i }));
      expect(await screen.findByText("Async reply")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /send message/i })).toHaveAttribute("aria-disabled", "true");
      expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
      expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(failure === "lost handle" ? 1 : 0);
    });

    it.each(["preparation", "interrupted", "finalization"] as const)(
      "uses the explicit %s stage rather than inferring it from stored errors",
      async (stage) => {
        const user = userEvent.setup();
        mockedAttacksApi.getMessageSend.mockResolvedValue({
          ...queued, state: stage === "interrupted" ? "interrupted" : "failed",
          failure_stage: stage, error: `Controlled ${stage} failure`,
        });
        const stored = { ...makeErrorResponse("processing", "Stored error").messages, conversation_id: props.conversationId };
        mockedAttacksApi.getMessages.mockResolvedValueOnce(empty).mockResolvedValue(stored);
        render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
        await sendDraft(user);
        expect(await screen.findByRole("group", { name: "Message send status" })).toHaveTextContent(`Controlled ${stage} failure`);
        expect(screen.getByRole("textbox")).toHaveValue("Draft to send");
        if (stage === "preparation") {
          expect(await findRecoveryAction(user)).toBeInTheDocument();
        } else {
          expect(screen.queryByRole("button", { name: /edit in clean conversation/i })).not.toBeInTheDocument();
        }
        expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
      },
    );

    it("aborts the single status read on unmount, not the accepted submission", async () => {
      const user = userEvent.setup();
      mockedAttacksApi.getMessageSend.mockImplementation(() => new Promise(() => {}));
      const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
      await sendDraft(user);
      expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(1);
      const signal = mockedAttacksApi.getMessageSend.mock.calls[0][2];
      expect(signal?.aborted).toBe(false);
      rendered.unmount();
      expect(signal?.aborted).toBe(true);
      expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
    });

    it("does not duplicate a saved optimistic message when refreshing an active conversation", async () => {
      const user = userEvent.setup();
      mockedAttacksApi.getMessageSend.mockImplementation(() => new Promise(() => {}));
      mockedAttacksApi.getConversations.mockResolvedValue({
        attack_result_id: props.attackResultId, main_conversation_id: props.conversationId,
        conversations: [{ conversation_id: props.conversationId, message_count: 0 }],
      });
      render(<TestWrapper><ChatWindow {...props} relatedConversationCount={1} /></TestWrapper>);
      await sendDraft(user);
      const requestMessage = makeErrorResponse("none", "").messages.messages[0];
      requestMessage.message_pieces[0].original_value = "Draft to send";
      requestMessage.message_pieces[0].converted_value = "Draft to send";
      mockedAttacksApi.getMessages.mockResolvedValue({ ...empty, messages: [requestMessage] });
      await user.click(screen.getByRole("button", { name: `Select conversation ${props.conversationId}` }));
      await waitFor(() => {
        expect(within(screen.getByTestId("message-list")).getAllByText("Draft to send")).toHaveLength(1);
      });
      expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(1);
    });

    it("waits for accepted work to settle before allowing processing-error recovery", async () => {
      const user = userEvent.setup();
      mockedAttacksApi.getMessageSend.mockImplementation(() => new Promise(() => {}));
      mockedAttacksApi.getConversations.mockResolvedValue({
        attack_result_id: props.attackResultId, main_conversation_id: props.conversationId,
        conversations: [{ conversation_id: props.conversationId, message_count: 0 }],
      });
      render(<TestWrapper><ChatWindow {...props} relatedConversationCount={1} /></TestWrapper>);
      await sendDraft(user);
      mockedAttacksApi.getMessages.mockResolvedValue({
        ...makeErrorResponse("processing", "Stored processing error").messages, conversation_id: props.conversationId,
      });
      await user.click(screen.getByRole("button", { name: `Select conversation ${props.conversationId}` }));
      const recovery = await findRecoveryAction(user);
      expect(recovery).toHaveAttribute("aria-disabled", "true");
      await user.click(recovery);
      expect(mockedAttacksApi.createConversation).not.toHaveBeenCalled();
    });

    it("does not let an old attack completion update or unlock a newer send", async () => {
      const user = userEvent.setup();
      const onAttackChange = jest.fn();
      let finishOld: (value: MessageSendStatus) => void = () => {};
      mockedAttacksApi.getMessageSend.mockImplementationOnce(() => new Promise((resolve) => {
        finishOld = resolve;
      })).mockImplementation(() => new Promise(() => {}));
      const rendered = render(<TestWrapper><ChatWindow {...props} onAttackChange={onAttackChange} /></TestWrapper>);
      await sendDraft(user);
      rendered.rerender(<TestWrapper><ChatWindow {...props}
        attackResultId="newer-attack" conversationId="newer-conversation" activeConversationId="newer-conversation"
        onAttackChange={onAttackChange}
      /></TestWrapper>);
      await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
      await user.click(screen.getByRole("button", { name: /send message/i }));
      await waitFor(() => expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(2));
      mockedAttacksApi.getMessages.mockResolvedValue(reply);
      await act(async () => { finishOld(complete); });
      expect(onAttackChange).not.toHaveBeenCalled();
      expect(screen.getByRole("textbox")).toHaveValue("Draft to send");
      expect(screen.getByRole("textbox")).toBeDisabled();
      expect(screen.queryByText("Async reply")).not.toBeInTheDocument();
    });

    describe("repeat sends", () => {
      function repeatedProgress(states: MessageSendConversation["state"][]): MessageSendStatus {
        return {
          ...queued,
          count: states.length,
          state: states.every((state) => state === "completed") ? "completed" : "sending",
          conversations: states.map((state, index): MessageSendConversation => ({
            conversation_id: index === 0 ? props.conversationId : `copy-${index}`,
            request_turn_number: 0, state, error: null, failure_stage: null,
          })),
        };
      }

      it("settles completed conversations independently and cannot unlock a newer send on a finished copy", async () => {
        const user = userEvent.setup();
        const onSelectConversation = jest.fn();
        const onAttackChange = jest.fn();
        let finishOlder: (value: MessageSendStatus) => void = () => {};
        mockedAttacksApi.submitMessageSend.mockResolvedValueOnce({ ...queued, count: 3 })
          .mockResolvedValue({ ...queued, send_id: "newer-send", conversation_id: "copy-1" });
        mockedAttacksApi.getMessageSend.mockResolvedValueOnce(repeatedProgress(["completed", "completed", "sending"]))
          .mockImplementation((_attackId: string, sendId: string) => new Promise((resolve) => {
            if (sendId === queued.send_id) finishOlder = resolve;
          }));
        mockedAttacksApi.getMessages.mockResolvedValueOnce(empty).mockImplementation(async (_attackId, conversationId) => ({
          ...reply, conversation_id: conversationId ?? props.conversationId,
        }));
        const rendered = render(<TestWrapper>
          <ChatWindow {...props} onSelectConversation={onSelectConversation} onAttackChange={onAttackChange} />
        </TestWrapper>);
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        await chooseCount(user, 3);
        await sendDraft(user);
        expect(mockedAttacksApi.submitMessageSend.mock.calls[0][1].count).toBe(3);
        expect(mockedAttacksApi.submitMessageSend.mock.calls[0][1].request_converter_configurations).toBeUndefined();
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        expect(screen.getByRole("textbox")).toHaveValue("");
        expect(screen.getByRole("button", { name: "Open conversation copy-1" })).toHaveTextContent("Completed");
        expect(screen.getByRole("button", { name: "Open conversation copy-2" })).toHaveTextContent("Sending");
        expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(2);
        await user.click(screen.getByRole("button", { name: "Open conversation copy-1" }));
        expect(onSelectConversation).toHaveBeenCalledWith("copy-1");
        rendered.rerender(<TestWrapper>
          <ChatWindow {...props} activeConversationId="copy-1"
            onSelectConversation={onSelectConversation} onAttackChange={onAttackChange} />
        </TestWrapper>);
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        await user.type(screen.getByRole("textbox"), "Newer copy draft");
        await user.click(screen.getByRole("button", { name: "Send message", exact: true }));
        await waitFor(() => expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(2));
        expect(mockedAttacksApi.submitMessageSend.mock.calls[1][1]).toEqual(expect.objectContaining({
          target_conversation_id: "copy-1",
          pieces: [expect.objectContaining({ original_value: "Newer copy draft" })],
        }));
        onAttackChange.mockClear();
        await act(async () => { finishOlder(repeatedProgress(["completed", "completed", "completed"])); });
        expect(await screen.findByRole("button", { name: "Dismiss repeat progress" })).toBeInTheDocument();
        expect(screen.getByRole("textbox")).toBeDisabled();
        expect(screen.getByRole("textbox")).toHaveValue("Newer copy draft");
        expect(onAttackChange).not.toHaveBeenCalled();
      });

      it("refreshes one shared progress GET after failure without resubmitting any conversation", async () => {
        const user = userEvent.setup();
        mockedAttacksApi.submitMessageSend.mockResolvedValue({ ...queued, count: 3 });
        mockedAttacksApi.getMessageSend
          .mockResolvedValueOnce(repeatedProgress(["completed", "sending", "queued"]))
          .mockRejectedValueOnce(readError)
          .mockResolvedValue(repeatedProgress(["completed", "completed", "completed"]));
        mockedAttacksApi.getMessages.mockResolvedValueOnce(empty).mockResolvedValue(reply);
        render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        await chooseCount(user, 3);
        await sendDraft(user);
        await user.click(await screen.findByRole("button", { name: "Refresh progress" }));
        await waitFor(() => {
          expect(screen.getByRole("button", { name: "Open conversation copy-1" })).toHaveTextContent("Completed");
          expect(screen.queryByRole("button", { name: "Refresh progress" })).not.toBeInTheDocument();
        });
        expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(3);
        expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("textbox")).toHaveValue("");
        await user.click(screen.getByRole("button", { name: "Dismiss repeat progress" }));
        expect(screen.queryByRole("region", { name: "Repeated send progress" })).not.toBeInTheDocument();
      });

      it.each([false, true])("reads the final summary when conversations finish first, read failure %s", async (readFails) => {
        const user = userEvent.setup();
        const complete = repeatedProgress(["completed", "completed"]);
        mockedAttacksApi.submitMessageSend.mockResolvedValue({ ...queued, count: 2 });
        mockedAttacksApi.getMessageSend.mockResolvedValueOnce({ ...complete, state: "sending" });
        if (readFails) mockedAttacksApi.getMessageSend.mockRejectedValueOnce(readError);
        mockedAttacksApi.getMessageSend.mockResolvedValue(complete);
        mockedAttacksApi.getMessages.mockResolvedValueOnce(empty).mockResolvedValue(reply);
        render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        await chooseCount(user, 2);
        await sendDraft(user);
        if (readFails) await user.click(await screen.findByRole("button", { name: "Refresh progress" }));
        expect(await screen.findByRole("button", { name: "Dismiss repeat progress" })).toBeInTheDocument();
        expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(readFails ? 3 : 2);
        expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
      });

      it.each(["preparation", "sending"] as const)("preserves a %s failure draft and attachment on a copied conversation", async (stage) => {
        const user = userEvent.setup();
        const target = makeTarget({
          ...mockTarget,
          capabilities: {
            ...mockTarget.capabilities,
            supports_multi_message_pieces: true,
            supported_input_modalities: ["text", "image_path"],
          },
        });
        const attachmentProps = { ...props, activeTarget: target, availableTargets: [target] };
        const failed = repeatedProgress(["completed", "failed"]);
        failed.state = "failed";
        failed.failure_stage = "sending";
        failed.error = "Some conversations failed";
        const copy = failed.conversations?.[1];
        if (!copy) throw new Error("Expected a copy");
        copy.failure_stage = stage;
        copy.error = `Controlled ${stage} failure`;
        mockedAttacksApi.submitMessageSend.mockResolvedValue({ ...queued, count: 2 });
        mockedAttacksApi.getMessageSend.mockResolvedValue(failed);
        mockedAttacksApi.getMessages.mockResolvedValueOnce(empty).mockImplementation(async (_attackId, conversationId) => (
          conversationId === "copy-1"
            ? { ...(stage === "sending" ? makeErrorResponse("processing", "Copy failed").messages : empty), conversation_id: "copy-1" }
            : reply
        ));
        mockedAttacksApi.createConversation.mockResolvedValue({
          conversation_id: "clean-copy", created_at: "2026-01-01T00:00:03Z",
        });
        const rendered = render(<TestWrapper><ChatWindow {...attachmentProps} /></TestWrapper>);
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        await user.upload(screen.getByTestId("file-input"), new File(["image"], "retained.png", { type: "image/png" }));
        await chooseCount(user, 2);
        await sendDraft(user);
        await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
        await user.click(screen.getByRole("button", { name: "Open conversation copy-1" }));
        rendered.rerender(<TestWrapper><ChatWindow {...attachmentProps} activeConversationId="copy-1" /></TestWrapper>);
        const restore = stage === "preparation"
          ? await screen.findByRole("button", { name: /restore prompt/i })
          : await findRecoveryAction(user);
        await user.click(restore);
        expect(screen.getByRole("textbox")).toHaveValue("Draft to send");
        expect(screen.getByText(/retained.png/)).toBeInTheDocument();
        expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("button", { name: "Repetitions: 1" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Refresh progress" })).not.toBeInTheDocument();
        expect(screen.queryByText(/Progress or saved messages could not be loaded/)).not.toBeInTheDocument();
      });

      it("keeps a failed preparation editable when no copies were committed", async () => {
        const user = userEvent.setup();
        mockedAttacksApi.submitMessageSend.mockResolvedValue({ ...queued, count: 3 });
        mockedAttacksApi.getMessageSend.mockResolvedValue({
          ...queued, count: 3, conversations: [], state: "failed",
          failure_stage: "preparation", error: "Copies could not be prepared",
        });
        render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        await chooseCount(user, 3);
        await sendDraft(user);
        await waitFor(() => expect(screen.getByRole("button", { name: "Send message", exact: true })).not.toHaveAttribute("aria-disabled"));
        expect(screen.getByRole("textbox")).toHaveValue("Draft to send");
        expect(screen.queryByRole("button", { name: /open conversation/ })).not.toBeInTheDocument();
        expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
      });

      it("does not start copy trackers after unmount while preparation was being read", async () => {
        const user = userEvent.setup();
        let finish: (value: MessageSendStatus) => void = () => {};
        mockedAttacksApi.submitMessageSend.mockResolvedValue({ ...queued, count: 3 });
        mockedAttacksApi.getMessageSend.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
        const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
        await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
        await chooseCount(user, 3);
        await sendDraft(user);
        const signal = mockedAttacksApi.getMessageSend.mock.calls[0][2];
        rendered.unmount();
        expect(signal?.aborted).toBe(true);
        await act(async () => { finish(repeatedProgress(["sending", "sending", "queued"])); });
        expect(mockedAttacksApi.getMessageSend).toHaveBeenCalledTimes(1);
        expect(mockedAttacksApi.getMessages).toHaveBeenCalledTimes(1);
      });
    });
  });

  // -----------------------------------------------------------------------
  // Basic rendering
  // -----------------------------------------------------------------------

  it("should fade informational messages after five seconds and retain save errors", async () => {
    jest.useFakeTimers();
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "notice-attack", conversation_id: "notice-conversation", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 0, related_conversation_ids: [],
        labels: {}, created_at: "", updated_at: "",
      },
      messages: { conversation_id: "notice-conversation", messages: [], target_response_status: null },
    });
    const router = createMemoryRouter([{ path: "/", element: <ChatWindow {...defaultProps} /> }]);
    const rendered = render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    const saveDraft = async (): Promise<void> => {
      await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
      await screen.findByTestId("conversation-editor");
      await user.click(screen.getByRole("button", { name: "Save", exact: true }));
    };
    try {
      await user.type(screen.getByPlaceholderText("Type prompt here"), "Notice check");
      await saveDraft();
      expect(await screen.findByText("Conversation saved.")).toBeInTheDocument();
      act(() => { jest.advanceTimersByTime(4_999); });
      expect(screen.getByText("Conversation saved.")).toBeInTheDocument();
      act(() => { jest.advanceTimersByTime(201); });
      expect(screen.queryByText("Conversation saved.")).not.toBeInTheDocument();
      await saveDraft();
      expect(await screen.findByText("Conversation saved.")).toBeInTheDocument();
      act(() => { jest.advanceTimersByTime(4_000); });
      await saveDraft();
      expect(await screen.findByText("Conversation saved.")).toBeInTheDocument();
      act(() => { jest.advanceTimersByTime(1_500); });
      expect(screen.getByText("Conversation saved.")).toBeInTheDocument();
      act(() => { jest.advanceTimersByTime(3_700); });
      expect(screen.queryByText("Conversation saved.")).not.toBeInTheDocument();
      mockedAttacksApi.saveConversation.mockRejectedValue(new Error("Keep this save error"));
      await saveDraft();
      expect(await screen.findByText("Keep this save error")).toBeInTheDocument();
      act(() => { jest.advanceTimersByTime(10_000); });
      expect(screen.getByText("Keep this save error")).toBeInTheDocument();
    } finally {
      rendered.unmount();
      jest.useRealTimers();
    }
  });

  describe("capability-safe chat actions", () => {
    it("filters first-send target binding for an unbound attack with simulated assistant history", async () => {
      const user = userEvent.setup();
      const immutable = makeTarget({
        target_registry_name: "immutable",
        capabilities: buildCapabilities({ supports_editable_history: false, supported_input_modalities: ["text"] }),
      });
      const editable = makeTarget({
        target_registry_name: "editable",
        capabilities: buildCapabilities({ supports_editable_history: true, supported_input_modalities: ["text"] }),
      });
      mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
      mockedAttacksApi.getMessages.mockResolvedValue({
        conversation_id: "unbound-history", target_response_status: null,
        messages: [{ role: "simulated_assistant", turn_number: 0, created_at: "", message_pieces: [{
          id: "saved-assistant", original_value_data_type: "text", original_value: "Saved assistant context",
          converted_value_data_type: "text", converted_value: "Saved assistant context", scores: [], response_error: "none",
        }] }],
      });
      function UnboundHarness() {
        const [target, setTarget] = useState<TargetInstance | null>(immutable);
        return <ChatWindow {...defaultProps} activeTarget={target} onSelectTarget={setTarget}
          availableTargets={[immutable, editable]} attackResultId="unbound-attack" conversationId="unbound-history"
          activeConversationId="unbound-history" targetResolutionStatus="unbound" />;
      }
      render(<TestWrapper><UnboundHarness /></TestWrapper>);
      await screen.findByText("Saved assistant context");
      await user.type(screen.getByPlaceholderText("Type prompt here"), "Next request");
      expect(screen.getByRole("option", { name: /immutable/ })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Send message", exact: true })).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByRole("button", { name: "Send message", exact: true })).toHaveAccessibleDescription(
        /This target does not support editable history/
      );
      await user.click(screen.getByRole("button", { name: "Copy or Branch" }));
      expect(screen.getByRole("menuitem", { name: "Branch conversation", exact: true })).not.toHaveAttribute("aria-disabled", "true");
      await user.keyboard("{Escape}");
      await user.selectOptions(screen.getByRole("combobox", { name: "Chat target" }), "editable");
      expect(screen.getByRole("button", { name: "Send message", exact: true })).not.toHaveAttribute("aria-disabled");
      expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
    });

    it("allows inspection but blocks same-attack save, branching and repeats for immutable assistant history", async () => {
      const user = userEvent.setup();
      const immutable = makeTarget({
        target_registry_name: "immutable-history", target_type: "OpenAIChatTarget", identifier_hash: "immutable-hash",
        capabilities: buildCapabilities({ supports_editable_history: false, supported_input_modalities: ["text"] }),
      });
      const editable = makeTarget({
        target_registry_name: "editable-history", target_type: "OpenAIChatTarget", identifier_hash: "editable-hash",
        capabilities: buildCapabilities({ supports_editable_history: true, supported_input_modalities: ["text"] }),
      });
      const source: BackendMessage[] = [
        { role: "user", turn_number: 0, created_at: "", message_pieces: [{
          id: "cap-user", original_value_data_type: "text", original_value: "Existing user",
          converted_value_data_type: "text", converted_value: "Existing user", scores: [], response_error: "none",
        }] },
        { role: "assistant", turn_number: 1, created_at: "", message_pieces: [{
          id: "cap-assistant", original_value_data_type: "text", original_value: "Existing assistant",
          converted_value_data_type: "text", converted_value: "Existing assistant", scores: [], response_error: "none",
        }] },
      ];
      mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
      mockedAttacksApi.getMessages.mockResolvedValue({ conversation_id: "cap-source", messages: source, target_response_status: null });
      mockedAttacksApi.saveConversation.mockResolvedValue({
        attack: {
          attack_result_id: "new-cap", conversation_id: "new-conversation", attack_type: "ManualAttack",
          objective: "", converters: [], message_count: 2, related_conversation_ids: [],
          labels: {}, created_at: "", updated_at: "",
        },
        messages: { conversation_id: "new-conversation", messages: source, target_response_status: null },
      });
      const onConversationCreated = jest.fn();
      const router = createMemoryRouter([{
        path: "/", element: <ChatWindow {...defaultProps} activeTarget={immutable}
          availableTargets={[immutable, editable]} attackResultId="cap-existing"
          conversationId="cap-source" activeConversationId="cap-source" targetResolutionStatus="resolved"
          attackTarget={{ target_type: "OpenAIChatTarget", identifier_hash: "immutable-hash" }}
          onConversationCreated={onConversationCreated} />,
      }]);
      render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
        <RouterProvider router={router} />
      </UserPreferencesProvider></FluentProvider>);
      await screen.findByText("Existing assistant");
      expect(screen.getByRole("button", { name: "Repetitions: 1" })).toBeDisabled();
      await user.click(screen.getAllByRole("button", { name: "Copy or Branch" })[1]);
      expect(screen.getByRole("menuitem", { name: "Branch conversation", exact: true })).toHaveAttribute("aria-disabled", "true");
      expect(screen.getByRole("menuitem", { name: "Branch attack", exact: true })).not.toHaveAttribute("aria-disabled", "true");
      await user.keyboard("{Escape}");
      await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
      expect(screen.queryByRole("button", { name: "Undo last send" })).not.toBeInTheDocument();
      await user.dblClick(screen.getByTestId("message-list"));
      await screen.findByTestId("conversation-editor");
      expect(screen.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Save to new attack", exact: true })).toBeDisabled();
      const field = within(screen.getByRole("region", { name: "Message 1" })).getByPlaceholderText("Type prompt here");
      await user.type(field, " edited");
      await user.click(screen.getByTestId("conversation-editor"));
      expect(mockedAttacksApi.saveConversation).not.toHaveBeenCalled();
      expect(screen.getByTestId("conversation-editor")).toBeInTheDocument();
      await user.selectOptions(screen.getByRole("combobox", { name: "Chat target" }), "editable-history");
      expect(screen.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Save to new attack", exact: true })).toBeEnabled();
      await user.click(screen.getByRole("button", { name: "Save to new attack", exact: true }));
      await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledTimes(1));
      expect(mockedAttacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
        destination: "new_attack", target_registry_name: "editable-history",
        messages: [expect.objectContaining({ role: "user" }), expect.objectContaining({ role: "simulated_assistant" })],
      }));
      expect(onConversationCreated).toHaveBeenCalledWith("new-cap", "new-conversation", "", editable);
    });

    it("allows a pending-user-only editor draft to start a fresh single-turn attack", async () => {
      const user = userEvent.setup();
      const target = makeTarget({
        target_registry_name: "single",
        capabilities: buildCapabilities({
          supports_multi_turn: false, supports_editable_history: false, supported_input_modalities: ["text"],
        }),
      });
      mockedAttacksApi.saveConversation.mockResolvedValue({
        attack: {
          attack_result_id: "fresh-single", conversation_id: "fresh-conversation", attack_type: "ManualAttack",
          objective: "", converters: [], message_count: 0, related_conversation_ids: [],
          labels: {}, created_at: "", updated_at: "",
        },
        messages: { conversation_id: "fresh-conversation", messages: [], target_response_status: null },
      });
      const router = createMemoryRouter([{
        path: "/", element: <ChatWindow {...defaultProps} activeTarget={target} availableTargets={[target]} />,
      }]);
      render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
        <RouterProvider router={router} />
      </UserPreferencesProvider></FluentProvider>);
      await user.type(screen.getByPlaceholderText("Type prompt here"), "Fresh request");
      await user.dblClick(screen.getByTestId("message-list"));
      await screen.findByTestId("conversation-editor");
      expect(screen.getByRole("option", { name: /single/ })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
      await user.click(screen.getByRole("button", { name: "Save", exact: true }));
      await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
        destination: "new_attack", target_registry_name: "single", messages: [],
      })));
      expect(await screen.findByPlaceholderText("Type prompt here")).toHaveValue("Fresh request");
    });
  });

  describe("undo last send", () => {
    it.each(["save", "leave"])("keeps hand-edited history and converter stages through editor %s, send, and Undo", async (action: string) => {
      const user = userEvent.setup();
      const stored = new Map<string, BackendMessage[]>([["source", [{
        role: "system", turn_number: 0, created_at: "",
        message_pieces: [{
          id: "context", original_value_data_type: "text", original_value: "Original context",
          converted_value_data_type: "text", converted_value: "Original context", scores: [], response_error: "none",
        }],
      }]]]);
      const attack: AttackSummary = {
        attack_result_id: "editing-attack", conversation_id: "source", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 1, related_conversation_ids: ["edited", "undo"],
        labels: {}, created_at: "", updated_at: "",
      };
      mockedAttacksApi.getAttack.mockResolvedValue(attack);
      mockedAttacksApi.getMessages.mockImplementation(async (_attackId, id) => ({
        conversation_id: id, messages: stored.get(id) ?? [], target_response_status: null,
      }));
      mockedAttacksApi.saveConversation.mockImplementation(async (request) => {
        const messages: BackendMessage[] = request.messages.map((message, index) => ({
          role: message.role, turn_number: index, created_at: "",
          message_pieces: message.pieces.map((piece, pieceIndex) => ({
            id: `edited-${index}-${pieceIndex}`, original_value_data_type: piece.data_type,
            original_value: piece.original_value, converted_value_data_type: piece.converted_value_data_type ?? piece.data_type,
            converted_value: piece.converted_value ?? piece.original_value, scores: [], response_error: "none",
          })),
        }));
        stored.set("edited", messages);
        return { attack, messages: { conversation_id: "edited", messages, target_response_status: null } };
      });
      mockedAttacksApi.createConversation.mockImplementation(async (_attackId, request) => {
        stored.set("undo", (stored.get("edited") ?? []).filter((message) => message.turn_number <= (request.cutoff_index ?? -1)));
        return { conversation_id: "undo", created_at: "" };
      });
      mockedConvertersApi.listConverters.mockResolvedValue({
        items: [makeConverterInstance("first", "Base64Converter"), makeConverterInstance("second", "Base64Converter")],
      });
      mockedConvertersApi.previewConversion.mockImplementation(async (request) => {
        let value = request.original_value;
        const steps = request.converter_ids.map((converterId) => {
          const input = value;
          value = `${input}|${converterId}`;
          return {
            converter_id: converterId, converter_type: "TestConverter", input_value: input,
            input_data_type: "text", output_value: value, output_data_type: "text",
          };
        });
        return { original_value: request.original_value, original_value_data_type: "text",
          converted_value: value, converted_value_data_type: "text", steps };
      });
      mockSendResult.mockImplementation(async (_attackId, request) => {
        const history = stored.get("edited") ?? [];
        const sent: BackendMessage[] = [...history, {
          role: "user", turn_number: 1, created_at: "",
          message_pieces: [{
            id: "sent-user", original_value_data_type: "text", original_value: request.pieces[0].original_value,
            converted_value_data_type: "text", converted_value: request.pieces[0].converted_value ?? "",
            scores: [], response_error: "none",
          }],
        }, {
          role: "assistant", turn_number: 2, created_at: "",
          message_pieces: [{
            id: "sent-reply", original_value_data_type: "text", original_value: "Saved response",
            converted_value_data_type: "text", converted_value: "Saved response", scores: [], response_error: "none",
          }],
        }];
        stored.set("edited", sent);
        return { attack, messages: { conversation_id: "edited", messages: sent,
          target_response_status: { response_error: "none", request_turn_number: 1, response_turn_number: 2 } } };
      });
      function EditorUndoHarness() {
        const [id, setId] = useState("source");
        return <ChatWindow {...defaultProps} attackResultId="editing-attack"
          conversationId={id} activeConversationId={id} onSelectConversation={setId} />;
      }
      const router = createMemoryRouter([{ path: "/", element: <EditorUndoHarness /> }]);
      render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
        <RouterProvider router={router} />
      </UserPreferencesProvider></FluentProvider>);
      await screen.findByText("Original context");
      await user.type(screen.getByPlaceholderText("Type prompt here"), "Original prompt");
      await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
      const context = within(await screen.findByRole("region", { name: "Message 1" }));
      await user.clear(context.getByPlaceholderText("Type prompt here"));
      await user.type(context.getByPlaceholderText("Type prompt here"), "Hand-edited context");
      const pending = within(screen.getByRole("region", { name: "Message 2" }));
      await user.click(pending.getByRole("button", { name: "Toggle converter panel" }));
      for (const name of ["first", "second"]) {
        await user.click(await screen.findByRole("combobox", { name: "Add converter" }));
        await user.click(await screen.findByRole("option", { name: new RegExp(`^${name}`) }));
      }
      await user.click(screen.getByRole("button", { name: "Convert 1 message", exact: true }));
      const middle = await screen.findByRole("textbox", { name: "Stage 1 output - Message 2: text" });
      await user.clear(middle);
      await user.type(middle, "Manual middle");
      await user.click(screen.getByRole("button", { name: "Convert Message 2: text from stage 2 to end" }));
      expect(await screen.findByRole("textbox", { name: "Stage 2 output - Message 2: text" })).toHaveValue("Manual middle|second");
      await user.click(context.getByRole("button", { name: "Toggle converter panel" }));
      await user.click(pending.getByRole("button", { name: "Toggle converter panel" }));
      expect(screen.getByRole("textbox", { name: "Stage 1 output - Message 2: text" })).toHaveValue("Manual middle");
      await user.click(screen.getByRole("button", { name: "Close converters" }));
      await user.click(action === "save"
        ? screen.getByRole("button", { name: "Save", exact: true })
        : screen.getByRole("heading", { name: "Chat" }));
      await waitFor(() => expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument());
      expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Original prompt");
      expect(screen.getByRole("textbox", { name: "Converted prompt" })).toHaveValue("Manual middle|second");
      expect(screen.getByText("Hand-edited context")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
      expect(screen.getByRole("textbox", { name: "Stage 1 output - Text" })).toHaveValue("Manual middle");
      await user.click(screen.getByRole("button", { name: "Close converters" }));
      await user.click(screen.getByRole("button", { name: "Send message", exact: true }));
      await screen.findByText("Saved response");
      expect(mockSendResult).toHaveBeenCalledWith("editing-attack", expect.objectContaining({
        pieces: [expect.objectContaining({
          original_value: "Original prompt", converted_value: "Manual middle|second",
          applied_converter_ids: ["first", "second"],
        })],
      }));
      await user.click(await screen.findByRole("button", { name: "Undo last send" }));
      await waitFor(() => expect(screen.queryByText("Saved response")).not.toBeInTheDocument());
      expect(screen.getByText("Hand-edited context")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Original prompt");
      expect(screen.getByRole("textbox", { name: "Converted prompt" })).toHaveValue("Manual middle|second");
      await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
      expect(screen.getByRole("textbox", { name: "Stage 1 output - Text" })).toHaveValue("Manual middle");
      expect(screen.getByRole("textbox", { name: "Stage 2 output - Text" })).toHaveValue("Manual middle|second");
      expect(stored.get("edited")?.at(-1)?.message_pieces[0].converted_value).toBe("Saved response");
      expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(2);
      await user.click(screen.getByRole("button", { name: "Close converters" }));
      await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
      const editedContext = within(await screen.findByRole("region", { name: "Message 1" }));
      await user.type(editedContext.getByPlaceholderText("Type prompt here"), " again");
      await user.click(screen.getByRole("button", { name: "Save", exact: true }));
      await waitFor(() => expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument());
      expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Original prompt");
      expect(screen.getByRole("textbox", { name: "Converted prompt" })).toHaveValue("Manual middle|second");
      await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
      expect(screen.getByRole("textbox", { name: "Stage 1 output - Text" })).toHaveValue("Manual middle");
      expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(2);
    });

    async function prepareUndoChat(options: {
      immutable?: boolean; emptyHistory?: boolean; assistantHistory?: boolean; restorable?: boolean;
    } = {}) {
      const user = userEvent.setup();
      const prefix: BackendMessage[] = [{
        role: "system", turn_number: 0, created_at: "",
        message_pieces: [{
          id: "context", original_value_data_type: "text", original_value: "Context before send",
          converted_value_data_type: "text", converted_value: "Context before send", scores: [], response_error: "none",
        }],
      }];
      if (options.emptyHistory) prefix.length = 0;
      if (options.assistantHistory) {
        prefix.push({
          role: "user", turn_number: prefix.length, created_at: "",
          message_pieces: [{
            id: "prior-user", original_value_data_type: "text", original_value: "Previous request",
            converted_value_data_type: "text", converted_value: "Previous request", scores: [], response_error: "none",
          }],
        }, {
          role: "assistant", turn_number: prefix.length + 1, created_at: "",
          message_pieces: [{
            id: "prior-assistant", original_value_data_type: "text", original_value: "Previous reply",
            converted_value_data_type: "text", converted_value: "Previous reply", scores: [], response_error: "none",
          }],
        });
      }
      const target = options.immutable ? makeTarget({
        target_registry_name: "immutable",
        ...(options.restorable ? { supports_history_replay: true } : {}),
        capabilities: buildCapabilities({ supports_editable_history: false, supported_input_modalities: ["text"] }),
      }) : mockTarget;
      let source = { conversation_id: "undo-source", messages: prefix, target_response_status: null as TargetResponseStatus | null };
      const attack: AttackSummary = {
        attack_result_id: "undo-attack", conversation_id: "undo-source", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 3, related_conversation_ids: ["undo-branch"],
        labels: {}, created_at: "", updated_at: "",
      };
      mockedAttacksApi.getMessages.mockImplementation(async (_attackId, conversationId) => (
        conversationId === "undo-branch"
          ? { conversation_id: "undo-branch", messages: prefix, target_response_status: null }
          : source
      ));
      mockedAttacksApi.getAttack.mockResolvedValue(attack);
      mockedAttacksApi.createConversation.mockResolvedValue({ conversation_id: "undo-branch" });
      mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
      mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
      mockedConvertersApi.listConverters.mockResolvedValue({
        items: [makeConverterInstance("first", "Base64Converter"), makeConverterInstance("second", "Base64Converter")],
      });
      mockedConvertersApi.previewConversion.mockImplementation(async (request) => {
        let value = request.original_value;
        const steps = request.converter_ids.map((converterId) => {
          const input = value;
          value = `${input}|${converterId}`;
          return {
            converter_id: converterId, converter_type: "TestConverter", input_value: input,
            input_data_type: "text", output_value: value, output_data_type: "text",
          };
        });
        return {
          original_value: request.original_value, original_value_data_type: "text",
          converted_value: value, converted_value_data_type: "text", steps,
        };
      });
      mockSendResult.mockImplementation(async (_attackId, request) => {
        source = {
          conversation_id: "undo-source",
          target_response_status: {
            response_error: "none", request_turn_number: prefix.length, response_turn_number: prefix.length + 1,
          },
          messages: [...prefix, {
            role: "user", turn_number: prefix.length, created_at: "",
            message_pieces: [{
              id: "last-user", original_value_data_type: "text", original_value: request.pieces[0].original_value,
              converted_value_data_type: "text", converted_value: request.pieces[0].converted_value ?? "",
              scores: [], response_error: "none",
            }],
          }, {
            role: "assistant", turn_number: prefix.length + 1, created_at: "",
            message_pieces: [{
              id: "last-assistant", original_value_data_type: "text", original_value: "Accepted result",
              converted_value_data_type: "text", converted_value: "Accepted result", scores: [], response_error: "none",
            }],
          }],
        };
        return { attack, messages: source };
      });
      const selected = jest.fn();
      function UndoHarness() {
        const [conversation, setConversation] = useState("undo-source");
        return <ChatWindow {...defaultProps} attackResultId="undo-attack"
          activeTarget={target} availableTargets={[target]}
          conversationId={conversation} activeConversationId={conversation}
          onSelectConversation={(id: string) => { selected(id); setConversation(id); }} />;
      }
      render(<TestWrapper><UndoHarness /></TestWrapper>);
      await user.type(await screen.findByPlaceholderText("Type prompt here"), "Original prompt");
      await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
      await user.click(await screen.findByRole("combobox", { name: "Add converter" }));
      await user.click(await screen.findByRole("option", { name: /^first/ }));
      await user.click(screen.getByRole("combobox", { name: "Add converter" }));
      await user.click(await screen.findByRole("option", { name: /^second/ }));
      await user.click(screen.getByRole("button", { name: "Convert", exact: true }));
      const middle = await screen.findByRole("textbox", { name: "Stage 1 output - Text" });
      await user.clear(middle);
      await user.type(middle, "Custom middle text");
      await user.click(screen.getByRole("button", { name: "Convert Text from stage 2 to end" }));
      await user.click(screen.getByRole("button", { name: "Send message" }));
      await screen.findByText("Accepted result");
      if (options.immutable && !options.emptyHistory && !options.restorable) {
        await waitFor(() => expect(screen.queryByRole("button", { name: "Undo last send" })).not.toBeInTheDocument());
      } else {
        await waitFor(() => expect(screen.getByRole("button", { name: "Undo last send" })).toBeEnabled());
      }
      return {
        user, selected,
        changeSource: () => {
          source = { ...source, messages: [...source.messages, {
            role: "user", turn_number: 3, created_at: "",
            message_pieces: [{
              id: "newer-user", original_value_data_type: "text", original_value: "Another client",
              converted_value_data_type: "text", converted_value: "Another client", scores: [], response_error: "none",
            }],
          }] };
        },
      };
    }

    it("restores the last prompt and exact middle-stage converter edits with a single click", async () => {
      const { user, selected } = await prepareUndoChat();
      expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("");
      await user.click(screen.getByRole("button", { name: "Undo last send" }));
      await waitFor(() => expect(selected).toHaveBeenCalledWith("undo-branch"));
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith("undo-attack", {
        source_conversation_id: "undo-source", cutoff_index: 0,
      });
      expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Original prompt");
      expect(screen.getByRole("textbox", { name: "Stage 1 output - Text" })).toHaveValue("Custom middle text");
      expect(screen.getByRole("textbox", { name: "Stage 2 output - Text" })).toHaveValue("Custom middle text|second");
      expect(screen.getByRole("textbox", { name: "Converted prompt" })).toHaveValue("Custom middle text|second");
      expect(screen.getByText("Context before send")).toBeInTheDocument();
      expect(screen.queryByText("Accepted result")).not.toBeInTheDocument();
      expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(2);
      expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
      expect(mockedAttacksApi.saveConversation).not.toHaveBeenCalled();
      const middle = screen.getByRole("textbox", { name: "Stage 1 output - Text" });
      await user.clear(middle);
      await user.type(middle, "Changed after undo");
      await user.click(screen.getByRole("button", { name: "Convert Text from stage 2 to end" }));
      expect(mockedConvertersApi.previewConversion).toHaveBeenLastCalledWith({
        original_value: "Changed after undo", original_value_data_type: "text", converter_ids: ["second"],
      });
    });

    it("hides undo when retained assistant history cannot be replayed", async () => {
      await prepareUndoChat({ immutable: true, assistantHistory: true });
      expect(screen.queryByRole("button", { name: "Undo last send" })).not.toBeInTheDocument();
      expect(mockedAttacksApi.createConversation).not.toHaveBeenCalled();
    });

    it("allows repeats, branching, and Undo when provider restoration supports immutable history", async () => {
      const { user } = await prepareUndoChat({ immutable: true, assistantHistory: true, restorable: true });
      expect(screen.getByRole("button", { name: "Repetitions: 1" })).toBeEnabled();
      await user.click(screen.getAllByRole("button", { name: "Copy or Branch" })[1]);
      expect(screen.getByRole("menuitem", { name: "Branch conversation", exact: true })).toBeEnabled();
      await user.keyboard("{Escape}");
      await user.click(screen.getByRole("button", { name: "Undo last send" }));
      await waitFor(() => expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith("undo-attack", {
        source_conversation_id: "undo-source", cutoff_index: 2,
      }));
      expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Original prompt");
    });

    it("allows first-turn undo for an immutable target when no history needs replay", async () => {
      const { user } = await prepareUndoChat({ immutable: true, emptyHistory: true });
      await user.click(screen.getByRole("button", { name: "Undo last send" }));
      await waitFor(() => expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith("undo-attack", {}));
      expect(await screen.findByPlaceholderText("Type prompt here")).toHaveValue("Original prompt");
    });

    it("disables undo rather than overwriting a new prompt", async () => {
      const { user } = await prepareUndoChat();
      await user.type(screen.getByPlaceholderText("Type prompt here"), "Keep my new draft");
      expect(screen.getByRole("button", { name: "Undo last send" })).toBeDisabled();
      await user.click(screen.getByRole("button", { name: "Undo last send" }));
      expect(mockedAttacksApi.createConversation).not.toHaveBeenCalled();
      expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Keep my new draft");
    });

    it("detects newer source messages before creating an undo branch", async () => {
      const { user, changeSource } = await prepareUndoChat();
      changeSource();
      await user.click(screen.getByRole("button", { name: "Undo last send" }));
      expect(await screen.findByText(/conversation changed after this send/)).toBeInTheDocument();
      expect(mockedAttacksApi.createConversation).not.toHaveBeenCalled();
      expect(screen.getByText("Accepted result")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Undo last send" })).toBeDisabled();
    });

    it("disables undo if the converter pipeline has changed", async () => {
      const { user } = await prepareUndoChat();
      await user.click(screen.getByRole("button", { name: "Remove converter second" }));
      expect(screen.getByRole("button", { name: "Undo last send" })).toBeDisabled();
      expect(mockedAttacksApi.createConversation).not.toHaveBeenCalled();
    });

    it("keeps the sent turn and disables undo when branch creation has an uncertain outcome", async () => {
      const { user } = await prepareUndoChat();
      mockedAttacksApi.createConversation.mockRejectedValue(new Error("Connection lost"));
      await user.click(screen.getByRole("button", { name: "Undo last send" }));
      expect(await screen.findByText(/Check the conversation list before trying again.*Connection lost/)).toBeInTheDocument();
      expect(screen.getByText("Accepted result")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("");
      expect(screen.getByRole("button", { name: "Undo last send" })).toBeDisabled();
      expect(mockedAttacksApi.createConversation).toHaveBeenCalledTimes(1);
    });
  });

  it("should enter edit mode on double-click and retain an unsent prompt without saving on an unchanged exit", async () => {
    const user = userEvent.setup();
    const router = createMemoryRouter([{ path: "/", element: <ChatWindow {...defaultProps} /> }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await user.type(screen.getByPlaceholderText("Type prompt here"), "Unsent prompt");
    await user.dblClick(screen.getByTestId("message-list"));
    const editor = await screen.findByTestId("conversation-editor");
    expect(within(editor).getByPlaceholderText("Type prompt here")).toHaveValue("Unsent prompt");
    await user.click(screen.getByRole("heading", { name: "Chat" }));
    await waitFor(() => expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument());
    expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Unsent prompt");
    expect(mockedAttacksApi.saveConversation).not.toHaveBeenCalled();
  });

  it("should reuse the same unsent conversation on repeated edit exits with a fresh piece snapshot", async () => {
    const user = userEvent.setup();
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    let source: BackendMessage[] = [{
      role: "system", turn_number: 0, created_at: "",
      message_pieces: [{
        id: "initial-piece", original_value_data_type: "text", original_value: "Initial context",
        converted_value_data_type: "text", converted_value: "Initial context", scores: [], response_error: "none",
      }],
    }];
    mockedAttacksApi.getMessages.mockImplementation(async () => ({
      conversation_id: "source", messages: source, target_response_status: null,
    }));
    mockedAttacksApi.saveConversation.mockImplementation(async (request) => {
      source = request.messages.map((message, index) => ({
        role: message.role, turn_number: index, created_at: "",
        message_pieces: message.pieces.map((piece) => ({
          id: `saved-${piece.original_value}`, original_value_data_type: piece.data_type ?? "text",
          original_value: piece.original_value, converted_value_data_type: piece.converted_value_data_type ?? piece.data_type ?? "text",
          converted_value: piece.converted_value ?? piece.original_value, scores: [], response_error: "none",
        })),
      }));
      return {
        attack: {
          attack_result_id: "existing", conversation_id: "source", attack_type: "ManualAttack",
          objective: "", converters: [], message_count: source.length, related_conversation_ids: [],
          labels: {}, created_at: "", updated_at: "",
        },
        messages: { conversation_id: "source", messages: source, target_response_status: null },
      };
    });
    const onSelectConversation = jest.fn();
    const router = createMemoryRouter([{
      path: "/", element: <ChatWindow {...defaultProps} attackResultId="existing"
        conversationId="source" activeConversationId="source" onSelectConversation={onSelectConversation} />,
    }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await screen.findByText("Initial context");
    for (const [index, text] of ["First change", "Second change"].entries()) {
      await user.dblClick(screen.getByTestId("message-list"));
      const field = within(await screen.findByTestId("conversation-editor")).getByPlaceholderText("Type prompt here");
      await user.clear(field);
      await user.type(field, text);
      await user.click(screen.getByTestId("conversation-editor"));
      await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledTimes(index + 1));
      await waitFor(() => expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument());
      expect(mockedAttacksApi.saveConversation.mock.calls[index][0]).toEqual(expect.objectContaining({
        destination: "same_attack", source_conversation_id: "source",
        reuse_unsent_conversation: true,
        expected_source_piece_ids: [index === 0 ? "initial-piece" : "saved-First change"],
      }));
      expect(onSelectConversation).toHaveBeenLastCalledWith("source");
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
  });

  it("keeps an unrun editor pipeline on an unchanged exit without saving history", async () => {
    const user = userEvent.setup();
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [makeConverterInstance("base64", "Base64Converter")] });
    const router = createMemoryRouter([{ path: "/", element: <ChatWindow {...defaultProps} /> }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await user.type(screen.getByPlaceholderText("Type prompt here"), "Unconverted prompt");
    await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
    const pending = within(await screen.findByRole("region", { name: "Message 1" }));
    await user.click(pending.getByRole("button", { name: "Toggle converter panel" }));
    await user.click(await screen.findByRole("combobox", { name: "Add converter" }));
    await user.click(await screen.findByRole("option", { name: /^base64/ }));
    await user.click(screen.getByRole("button", { name: "Close converters" }));
    await user.click(screen.getByRole("heading", { name: "Chat" }));
    await waitFor(() => expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument());
    expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Unconverted prompt");
    await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
    expect(screen.getByRole("button", { name: "Remove converter base64" })).toBeInTheDocument();
    expect(mockedAttacksApi.saveConversation).not.toHaveBeenCalled();
    expect(mockedConvertersApi.previewConversion).not.toHaveBeenCalled();
  });

  it("keeps an empty original prompt with a manually authored converted value across editor Save", async () => {
    const user = userEvent.setup();
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "saved", conversation_id: "empty", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 0, related_conversation_ids: [],
        labels: {}, created_at: "", updated_at: "",
      },
      messages: { conversation_id: "empty", messages: [], target_response_status: null },
    });
    const router = createMemoryRouter([{ path: "/", element: <ChatWindow {...defaultProps} /> }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
    await user.type(await screen.findByRole("textbox", { name: "Working input - Text" }), "Manual converted value");
    await user.click(screen.getByRole("button", { name: "Close converters" }));
    await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
    const pending = within(await screen.findByRole("region", { name: "Message 1" }));
    expect(pending.getByPlaceholderText("Type prompt here")).toHaveValue("");
    expect(pending.getByRole("textbox", { name: "Converted prompt" })).toHaveValue("Manual converted value");
    await user.click(screen.getByRole("button", { name: "Save", exact: true }));
    await waitFor(() => expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument());
    expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "Converted prompt" })).toHaveValue("Manual converted value");
    expect(screen.getByRole("button", { name: "Send message", exact: true })).not.toHaveAttribute("aria-disabled");
  });

  it("should keep edits visible when automatically saving on exit fails", async () => {
    const user = userEvent.setup();
    mockedAttacksApi.saveConversation.mockRejectedValue(new Error("The save failed"));
    const router = createMemoryRouter([{ path: "/", element: <ChatWindow {...defaultProps} /> }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await user.dblClick(screen.getByTestId("message-list"));
    await user.click(await screen.findByRole("button", { name: "Insert message at start" }));
    await user.type(within(screen.getByTestId("conversation-editor")).getByPlaceholderText("Type prompt here"), "Keep these edits");
    await user.click(screen.getByRole("heading", { name: "Chat" }));
    expect(await screen.findByText("The save failed")).toBeInTheDocument();
    expect(screen.getByTestId("conversation-editor")).toBeInTheDocument();
    expect(within(screen.getByTestId("conversation-editor")).getByPlaceholderText("Type prompt here")).toHaveValue("Keep these edits");
  });

  it.each(["delete", "system"])("clears the old composer draft after pending-message change %s", async (change: string) => {
    const user = userEvent.setup();
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "saved", conversation_id: "context", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 1, related_conversation_ids: [],
        labels: {}, created_at: "", updated_at: "",
      },
      messages: { conversation_id: "context", messages: [], target_response_status: null },
    });
    const router = createMemoryRouter([{ path: "/", element: <ChatWindow {...defaultProps} /> }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await user.type(screen.getByPlaceholderText("Type prompt here"), "Delete this draft");
    await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
    await user.click(await screen.findByRole("button", { name: "Insert message at start" }));
    const context = within(screen.getByRole("region", { name: "Message 1" }));
    await user.selectOptions(context.getByRole("combobox"), "system");
    await user.type(context.getByPlaceholderText("Type prompt here"), "Keep history");
    if (change === "delete") {
      await user.click(screen.getByRole("button", { name: "Delete message 2" }));
    } else {
      await user.selectOptions(within(screen.getByRole("region", { name: "Message 2" })).getByRole("combobox"), "system");
    }
    await user.click(screen.getByRole("button", { name: "Save to new attack", exact: true }));
    await waitFor(() => expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument());
    expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Send message", exact: true })).toHaveAttribute("aria-disabled", "true");
  });

  it.each([true, false])("should move the final fake user turn to the composer with a target: %s", async (hasTarget: boolean) => {
    const user = userEvent.setup();
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    const onConversationCreated = jest.fn();
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "new", conversation_id: "saved", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 1, related_conversation_ids: [],
        labels: {}, created_at: "", updated_at: "", target_unbound: !hasTarget,
      },
      messages: {
        conversation_id: "saved", target_response_status: null,
        messages: [{
          role: "system", turn_number: 0, created_at: "",
          message_pieces: [{ id: "system", original_value_data_type: "text", original_value: "System context",
            converted_value_data_type: "text", converted_value: "System context", scores: [], response_error: "none" }],
        }],
      },
    });
    const router = createMemoryRouter([{
      path: "/", element: <ChatWindow {...defaultProps} activeTarget={hasTarget ? mockTarget : null}
        onConversationCreated={onConversationCreated} />,
    }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
    await user.click(await screen.findByRole("button", { name: "Insert message at start" }));
    const system = screen.getByRole("region", { name: "Message 1" });
    await user.selectOptions(within(system).getByRole("combobox"), "system");
    await user.type(within(system).getByRole("textbox"), "System context");
    await user.click(screen.getByRole("button", { name: "Insert message after message 1" }));
    await user.type(within(screen.getByRole("region", { name: "Message 2" })).getByRole("textbox"), "Ready to send");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("button", { name: "Send message" });
    expect(await screen.findByPlaceholderText("Type prompt here")).toHaveValue("Ready to send");
    const request = mockedAttacksApi.saveConversation.mock.calls[0][0];
    expect(request.messages).toEqual([expect.objectContaining({
      role: "system", pieces: [expect.objectContaining({ original_value: "System context" })],
    })]);
    expect(screen.getByText("System context")).toBeInTheDocument();
    expect(screen.queryAllByText("Ready to send").every((element: HTMLElement) => element.tagName === "TEXTAREA")).toBe(true);
    if (!hasTarget) {
      expect(screen.queryByTestId("no-target-banner")).not.toBeInTheDocument();
      expect(screen.getByPlaceholderText("Type prompt here")).toBeEnabled();
      expect(screen.getByRole("button", { name: "Send message" })).toHaveAttribute("aria-disabled", "true");
    }
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
  });

  it("should send a text-only video remix with its metadata after saving the editor", async () => {
    const user = userEvent.setup();
    const metadata = { video_id: "existing-video", custom: "preserved" };
    const source: BackendMessage[] = [{
      role: "user", turn_number: 0, created_at: "",
      message_pieces: [{
        id: "text-remix", original_value_data_type: "text", original_value: "Change the background",
        converted_value_data_type: "text", converted_value: "Change the background",
        prompt_metadata: metadata, scores: [], response_error: "none",
      }],
    }];
    const empty = { conversation_id: "saved", messages: [], target_response_status: null };
    mockedAttacksApi.getMessages.mockImplementation(async (_attackId, conversationId) => (
      conversationId === "source" ? { ...empty, conversation_id: "source", messages: source } : empty
    ));
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "existing", conversation_id: "saved", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 0, related_conversation_ids: [],
        labels: {}, created_at: "", updated_at: "",
      },
      messages: empty,
    });
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedAttacksApi.submitMessageSend.mockImplementation(() => new Promise(() => {}));
    function SavedRemixHarness() {
      const [conversationId, setConversationId] = useState("source");
      return <ChatWindow {...defaultProps} attackResultId="existing" conversationId={conversationId}
        activeConversationId={conversationId} onSelectConversation={setConversationId} />;
    }
    const router = createMemoryRouter([{ path: "/", element: <SavedRemixHarness /> }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await user.click(await screen.findByRole("button", { name: "Edit Conversation", exact: true }));
    await screen.findByRole("region", { name: "Message 1" });
    await user.click(screen.getByRole("button", { name: "Save" }));
    const input = await screen.findByPlaceholderText("Type prompt here");
    await waitFor(() => expect(input).toHaveValue("Change the background"));
    expect(mockedAttacksApi.saveConversation.mock.calls[0][0].messages).toEqual([]);
    await user.type(input, " to blue");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledWith(
      "existing", expect.objectContaining({
        pieces: [{ data_type: "text", original_value: "Change the background to blue", prompt_metadata: metadata }],
      }),
    ));
  });

  it("should keep a target-free attack unbound when copying a system turn", async () => {
    const user = userEvent.setup();
    const source: BackendMessage[] = [{
      role: "system", turn_number: 0, created_at: "",
      message_pieces: [{ id: "system", original_value_data_type: "text", original_value: "Context",
        converted_value_data_type: "text", converted_value: "Context", scores: [], response_error: "none" }],
    }];
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedAttacksApi.getMessages.mockResolvedValue({ conversation_id: "source", messages: source, target_response_status: null });
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "existing", conversation_id: "source", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 1, related_conversation_ids: ["copy"],
        labels: {}, created_at: "", updated_at: "", target_unbound: true,
      },
      messages: { conversation_id: "copy", messages: source, target_response_status: null },
    });
    render(<TestWrapper><ChatWindow {...defaultProps} attackResultId="existing" conversationId="source"
      activeConversationId="source" targetResolutionStatus="unbound" /></TestWrapper>);
    await user.click(await screen.findByRole("button", { name: "Copy or Branch" }));
    await user.click(screen.getByRole("menuitem", { name: "Branch conversation", exact: true }));
    await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledTimes(1));
    expect(mockedAttacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
      destination: "same_attack", target_registry_name: undefined,
      messages: [expect.objectContaining({ role: "system" })],
    }));
    expect(defaultProps.onSelectConversation).toHaveBeenCalledWith("copy");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each(["Same attack", "New attack"])("edits locally, restores the composer on Cancel, and saves to %s", async (destination: string) => {
    const user = userEvent.setup();
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue(mockMessages);
    const onConversationCreated = jest.fn();
    const onSelectConversation = jest.fn();
    const attackId = destination === "Same attack" ? "existing" : "new";
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: attackId, conversation_id: "saved", attack_type: "ManualAttack",
        objective: "Draft goal", converters: [], message_count: 0, related_conversation_ids: [],
        labels: {}, created_at: "", updated_at: "",
      },
      messages: { conversation_id: "saved", messages: [], target_response_status: null },
    });
    const router = createMemoryRouter([{
      path: "/",
      element: <ChatWindow {...defaultProps} attackResultId="existing" conversationId="source"
        activeConversationId="source" onConversationCreated={onConversationCreated}
        onSelectConversation={onSelectConversation} />,
    }]);
    render(
      <FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
        <RouterProvider router={router} />
      </UserPreferencesProvider></FluentProvider>
    );
    await user.type(await screen.findByPlaceholderText("Type prompt here"), "Keep this unsent prompt");
    await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
    expect(await screen.findByRole("button", { name: "Convert Conversation" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Draft objective" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send message" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel", exact: true }));
    expect(await screen.findByPlaceholderText("Type prompt here")).toHaveValue("Keep this unsent prompt");
    await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
    await screen.findByRole("button", { name: "Convert Conversation" });
    await user.click(screen.getByRole("button", { name: "Add objective" }));
    await user.type(screen.getByRole("textbox", { name: "Attack objective" }), "Draft goal");
    await user.click(within(screen.getByTestId("objective-header")).getByRole("button", { name: "Save", exact: true }));
    expect(mockedAttacksApi.updateAttack).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: destination === "Same attack" ? "Save" : "Save to new attack", exact: true }));
    await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledTimes(1));
    expect(await screen.findByPlaceholderText("Type prompt here")).toBeInTheDocument();
    if (destination === "Same attack") expect(onSelectConversation).toHaveBeenCalledWith("saved");
    else expect(onConversationCreated).toHaveBeenCalledWith("new", "saved", "Draft goal", mockTarget);
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
  });

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

  it("limits editor targets to editable history and updates tool requirements after deletion", async () => {
    const user = userEvent.setup();
    const lockedTarget = makeTarget({ target_registry_name: "locked", capabilities: buildCapabilities() });
    const toolTarget = makeTarget({
      target_registry_name: "tools",
      capabilities: buildCapabilities({
        supports_editable_history: true,
        supported_input_modalities: ["text", "function_call", "function_call_output"],
      }),
    });
    const router = createMemoryRouter([{
      path: "/", element: <ChatWindow {...defaultProps} activeTarget={lockedTarget}
        availableTargets={[lockedTarget, mockTarget, toolTarget]} />,
    }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    expect(screen.getByRole("option", { name: "locked" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Edit Conversation" }));
    await screen.findByRole("button", { name: "Convert Conversation" });
    expect(screen.getByRole("option", { name: "locked" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Insert message at start" }));
    const context = screen.getByRole("region", { name: "Message 1" });
    await user.selectOptions(within(context).getByRole("combobox"), "simulated_assistant");
    await user.type(within(context).getByPlaceholderText("Type prompt here"), "Assistant history");
    expect(screen.getByRole("option", { name: /locked/ })).toBeDisabled();
    expect(screen.getByText(/Select a compatible target/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save to new attack" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockedAttacksApi.saveConversation).not.toHaveBeenCalled();
    const picker = await screen.findByRole("combobox", { name: "Chat target" });
    await user.selectOptions(picker, "");
    await user.click(screen.getByRole("button", { name: "Insert message at start" }));
    const message = screen.getByRole("region", { name: "Message 1" });
    await user.click(within(message).getByRole("button", { name: "Add content" }));
    expect(screen.getByRole("menuitem", { name: "Add tool call" })).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Escape}");
    await user.selectOptions(within(message).getByRole("combobox"), "simulated_assistant");
    await user.click(within(message).getByRole("button", { name: "Add content" }));
    await user.click(screen.getByRole("menuitem", { name: "Add tool call" }));
    expect(screen.getByRole("option", { name: /openai_chat_1/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: "tools" })).toBeEnabled();
    await user.selectOptions(picker, "tools");
    await user.click(screen.getByRole("button", { name: "Remove piece 2 from message 1" }));
    expect(screen.getByRole("option", { name: /openai_chat_1/ })).toBeEnabled();
    await user.selectOptions(picker, "openai_chat_1");
    await user.click(within(message).getByRole("button", { name: "Add content" }));
    expect(screen.getByRole("menuitem", { name: "Add tool call" })).toHaveAttribute("aria-disabled", "true");
  });

  it("saves an unsaved batch-converted draft to a new attack and clears the notice on send", async () => {
    const user = userEvent.setup();
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [makeConverterInstance("base64", "Base64Converter")] });
    mockedConvertersApi.previewConversion.mockImplementation(async (request) => ({
      original_value: request.original_value, original_value_data_type: "text",
      converted_value: btoa(request.original_value), converted_value_data_type: "text",
      steps: [{
        converter_id: "base64", converter_type: "Base64Converter",
        input_value: request.original_value, input_data_type: "text",
        output_value: btoa(request.original_value), output_data_type: "text",
      }],
    }));
    const savedMessages: BackendMessage[] = ["First prompt", "Second prompt"].map((value: string, index: number) => ({
      role: "user", turn_number: index, created_at: "2026-01-01T00:00:00Z",
      message_pieces: [{
        id: `saved-${index}`, original_value_data_type: "text", original_value: value,
        converted_value_data_type: "text", converted_value: btoa(value), scores: [], response_error: "none",
      }],
    }));
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "new", conversation_id: "saved", attack_type: "ManualAttack",
        objective: "", converters: [], message_count: 1, related_conversation_ids: [],
        labels: {}, created_at: "", updated_at: "",
      },
      messages: { conversation_id: "saved", messages: savedMessages.slice(0, 1), target_response_status: null },
    });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: savedMessages.slice(0, 1) });
    mockedAttacksApi.submitMessageSend.mockImplementation(() => new Promise(() => {}));
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    function SavedChatHarness() {
      const [location, setLocation] = useState<{ attackId: string; conversationId: string } | null>(null);
      return <ChatWindow {...defaultProps} attackResultId={location?.attackId ?? null}
        conversationId={location?.conversationId ?? null} activeConversationId={location?.conversationId ?? null}
        onConversationCreated={(attackId: string, conversationId: string) => setLocation({ attackId, conversationId })} />;
    }
    const router = createMemoryRouter([{ path: "/", element: <SavedChatHarness /> }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    const targetPicker = screen.getByRole("combobox", { name: "Chat target" });
    const editButton = screen.getByRole("button", { name: "Edit Conversation" });
    expect(targetPicker.compareDocumentPosition(editButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(editButton);
    await user.click(await screen.findByRole("button", { name: "Insert message at start" }));
    await user.type(within(screen.getByRole("region", { name: "Message 1" })).getByPlaceholderText("Type prompt here"), "First prompt");
    await user.click(screen.getByRole("button", { name: "Insert message after message 1" }));
    await user.type(within(screen.getByRole("region", { name: "Message 2" })).getByPlaceholderText("Type prompt here"), "Second prompt");
    await user.click(screen.getByRole("button", { name: "Convert Conversation" }));
    await user.click(await screen.findByRole("combobox", { name: "Add converter" }));
    await user.click(await screen.findByRole("option", { name: /base64/ }));
    await user.click(screen.getByRole("button", { name: "Convert 2 messages" }));
    const outputs = await screen.findByRole("region", { name: "Converted messages" });
    for (const [index, value] of ["First prompt", "Second prompt"].entries()) {
      const pair = within(outputs).getByRole("region", { name: `Message ${index + 1}: text` });
      expect(within(pair).getByText("Original message")).toBeInTheDocument();
      expect(within(pair).getByText(value)).toBeInTheDocument();
      expect(within(pair).getByText("Converted message")).toBeInTheDocument();
      expect(within(pair).getByText(btoa(value))).toBeInTheDocument();
    }
    expect(screen.queryByText(/^\d+ selected$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Final output -/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add converted value" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save to new attack" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledTimes(1));
    const request = mockedAttacksApi.saveConversation.mock.calls[0][0];
    expect(request.destination).toBe("new_attack");
    expect(request.messages.map((message) => message.pieces[0].converted_value)).toEqual([btoa("First prompt")]);
    expect(request.messages[0].pieces[0].applied_converter_ids).toEqual(["base64"]);
    expect(defaultProps.onNewAttack).not.toHaveBeenCalled();
    expect(await screen.findByText("Conversation saved.")).toBeInTheDocument();
    await screen.findByRole("button", { name: "Send message" });
    expect(screen.getByPlaceholderText("Type prompt here")).toHaveValue("Second prompt");
    expect(screen.getByRole("textbox", { name: "Converted prompt" })).toHaveValue(btoa("Second prompt"));
    await user.clear(screen.getByPlaceholderText("Type prompt here"));
    await user.type(await screen.findByPlaceholderText("Type prompt here"), "Next prompt");
    expect(screen.getByText("Conversation saved.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("Conversation saved.")).not.toBeInTheDocument();
  });

  it.each([
    ["Branch conversation", "same_attack"],
    ["Branch attack", "new_attack"],
    ["Use in scan", "new_attack"],
  ])("copies through the selected message to %s without sending", async (
    label: string, destination: string,
  ) => {
    const user = userEvent.setup();
    const clipboard = jest.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("Clipboard denied"));
    const useInTechnique = label === "Use in scan";
    const source: BackendMessage[] = ["system", "user", "assistant"].map((role: string, index: number) => ({
      turn_number: index, role, created_at: "2026-01-01T00:00:00Z",
      message_pieces: [{
        id: `source-${index}`, original_value_data_type: "text", original_value: `Message ${index}`,
        converted_value_data_type: "text", converted_value: `Message ${index}`, scores: [], response_error: "none",
      }],
    }));
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: source });
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: destination === "same_attack" ? "existing" : "copied",
        conversation_id: "copied-conversation", objective: "Source objective", attack_type: "ManualAttack",
        converters: [], message_count: 2, related_conversation_ids: [], labels: {}, created_at: "", updated_at: "",
      },
      messages: { conversation_id: "copied-conversation", messages: source.slice(0, 2), target_response_status: null },
    });
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <MemoryRouter><Routes>
        <Route path="/" element={<ChatWindow {...defaultProps} attackResultId="existing" conversationId="source"
          activeConversationId="source" objective="Source objective"
          defaultBranchTarget={makeTarget({ target_registry_name: "unrelated-default", capabilities: buildCapabilities() })} />} />
        <Route path="/scanner/technique.execute" element={<><h1>Technique configuration</h1><RouterLocation /></>} />
      </Routes></MemoryRouter>
    </UserPreferencesProvider></FluentProvider>);
    await user.click(await screen.findByTestId("copy-to-input-btn-1"));
    await user.click(screen.getByRole("menuitem", { name: label, exact: true }));
    await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledTimes(1));
    const request = mockedAttacksApi.saveConversation.mock.calls[0][0];
    expect(request.destination).toBe(destination);
    expect(request.objective).toBe(destination === "new_attack" ? "Source objective" : undefined);
    expect(request.expected_objective).toBeUndefined();
    expect(request.target_registry_name).toBe(useInTechnique ? undefined : mockTarget.target_registry_name);
    expect(request.messages.map((message) => message.pieces[0].source_piece_id)).toEqual(["source-0", "source-1"]);
    expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    if (useInTechnique) {
      expect(await screen.findByRole("heading", { name: "Technique configuration" })).toBeInTheDocument();
      expect(screen.getByLabelText("Current URL")).toHaveTextContent(
        "/scanner/technique.execute?prepended_conversation_id=copied-conversation",
      );
      expect(defaultProps.onConversationCreated).not.toHaveBeenCalled();
      expect(defaultProps.onSelectConversation).not.toHaveBeenCalled();
      expect(screen.queryByText(/Conversation ID copied/)).not.toBeInTheDocument();
    } else if (destination === "same_attack") {
      await waitFor(() => expect(defaultProps.onSelectConversation).toHaveBeenCalledWith("copied-conversation"));
    } else {
      await waitFor(() => expect(defaultProps.onConversationCreated).toHaveBeenCalledWith(
        "copied", "copied-conversation", "Source objective", mockTarget,
      ));
    }
    if (!useInTechnique) {
      expect(await screen.findByRole("complementary", { name: "Attack Conversations" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Toggle conversations panel" })).toHaveAttribute("aria-expanded", "true");
    }
    expect(clipboard).not.toHaveBeenCalled();
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
  });

  it("should copy a failed prompt to a new attack without stored errors or dependent history", async () => {
    const user = userEvent.setup();
    const safeHistory = makeErrorResponse("none", "", 0).messages.messages;
    safeHistory[0].message_pieces[0].original_value = "Safe context";
    safeHistory[1].message_pieces[0].original_value = "Safe response";
    const earlierFailure = makeErrorResponse("processing", "Earlier target error", 2);
    const latestFailure = makeErrorResponse("processing", "Latest target error", 4);
    latestFailure.messages.messages[0].message_pieces[0].original_value = "Latest failed draft";
    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "failed-source",
      messages: [...safeHistory, ...earlierFailure.messages.messages, ...latestFailure.messages.messages],
      target_response_status: latestFailure.messages.target_response_status,
    });
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "copied", conversation_id: "copied-conversation", objective: "", attack_type: "ManualAttack",
        converters: [], message_count: 3, related_conversation_ids: [], labels: {}, created_at: "", updated_at: "",
      },
      messages: { conversation_id: "copied-conversation", messages: [], target_response_status: null },
    });
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    const rendered = render(<TestWrapper><ChatWindow {...defaultProps} attackResultId="existing"
      conversationId="failed-source" activeConversationId="failed-source" /></TestWrapper>);
    await findRecoveryAction(user);
    await user.click(screen.getByRole("menuitem", { name: "Branch attack", exact: true }));
    await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledTimes(1));
    const request = mockedAttacksApi.saveConversation.mock.calls[0][0];
    expect(request.destination).toBe("new_attack");
    expect(request.messages.map((message) => message.pieces[0].original_value)).toEqual([
      "Safe context", "Safe response",
    ]);
    expect(request.target_registry_name).toBe(mockTarget.target_registry_name);
    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "copied-conversation", messages: safeHistory, target_response_status: null,
    });
    rendered.rerender(<TestWrapper><ChatWindow {...defaultProps} attackResultId="copied"
      conversationId="copied-conversation" activeConversationId="copied-conversation" /></TestWrapper>);
    expect(await screen.findByRole("textbox")).toHaveValue("Latest failed draft");
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    expect(screen.queryByText("Latest target error")).not.toBeInTheDocument();
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
  });

  it.each([
    { name: "non-editable", supportsMultiTurn: true, supportsEditableHistory: false },
    { name: "single-turn", supportsMultiTurn: false, supportsEditableHistory: true },
  ])("should restore a saved failure in a fresh targeted attack for a $name target", async ({
    supportsMultiTurn, supportsEditableHistory,
  }: { supportsMultiTurn: boolean; supportsEditableHistory: boolean }) => {
    const user = userEvent.setup();
    const target = makeTarget({
      target_registry_name: "saved-retry-target",
      capabilities: buildCapabilities({
        supports_multi_turn: supportsMultiTurn,
        supports_editable_history: supportsEditableHistory,
        supports_multi_message_pieces: true,
        supported_input_modalities: ["text", "image_path"],
      }),
    });
    const failed = makeErrorResponse("processing", "Saved error", 2, true).messages;
    failed.messages[0].message_pieces[0].original_value = "Retry this prompt";
    failed.messages[0].message_pieces.push({
      id: "original-image", original_value_data_type: "image_path", original_value: "/original/evidence.png",
      converted_value_data_type: "image_path", converted_value: "/original/evidence.png",
      original_filename: "evidence.png", scores: [], response_error: "none",
    });
    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "failed-source", messages: [...makeErrorResponse("none", "").messages.messages, ...failed.messages],
      target_response_status: failed.target_response_status,
    });
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "retry-attack", conversation_id: "retry-conversation", created_at: "",
    });
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    const rendered = render(<TestWrapper><ChatWindow {...defaultProps} activeTarget={target}
      attackResultId="source-attack" conversationId="failed-source" activeConversationId="failed-source" /></TestWrapper>);
    await findRecoveryAction(user);
    await user.click(screen.getByRole("menuitem", { name: "Branch attack", exact: true }));
    await waitFor(() => expect(defaultProps.onConversationCreated).toHaveBeenCalledWith(
      "retry-attack", "retry-conversation", "", target,
    ));
    expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(expect.objectContaining({
      target_registry_name: target.target_registry_name,
    }));
    expect(mockedAttacksApi.saveConversation).not.toHaveBeenCalled();
    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "retry-conversation", messages: [], target_response_status: null,
    });
    rendered.rerender(<TestWrapper><ChatWindow {...defaultProps} activeTarget={target}
      attackResultId="retry-attack" conversationId="retry-conversation" activeConversationId="retry-conversation" /></TestWrapper>);
    expect(await screen.findByRole("textbox")).toHaveValue("Retry this prompt");
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    expect(screen.getByRole("button", { name: "Remove evidence.png" })).toBeInTheDocument();
    expect(screen.getByText(/Converter choices could not be restored/)).toBeInTheDocument();
    expect(screen.queryByText("Saved error")).not.toBeInTheDocument();
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
  });

  it.each<{ name: string; target: TargetInstance | null }>([
    { name: "unavailable", target: null },
    { name: "non-editable", target: makeTarget({
      capabilities: buildCapabilities({ supported_input_modalities: ["text", "function_call"] }),
    }) },
    { name: "single-turn", target: makeTarget({
      capabilities: buildCapabilities({
        supports_editable_history: true, supports_multi_turn: false,
        supported_input_modalities: ["text", "function_call"],
      }),
    }) },
    { name: "missing tool support", target: mockTarget },
  ])("copies to a targetless new attack when the source target is $name", async ({ target }: { target: TargetInstance | null }) => {
    const user = userEvent.setup();
    const source: BackendMessage[] = [{
      turn_number: 0, role: "assistant", created_at: "2026-01-01T00:00:00Z",
      message_pieces: [{
        id: "source-call", original_value_data_type: "text", original_value: "Original text",
        converted_value_data_type: "function_call",
        converted_value: JSON.stringify({ type: "function_call", call_id: "call-1", name: "lookup", arguments: "{}" }),
        scores: [], response_error: "none",
      }],
    }];
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: source });
    mockedAttacksApi.saveConversation.mockResolvedValue({
      attack: {
        attack_result_id: "copied", conversation_id: "copied-conversation", objective: "", attack_type: "ManualAttack",
        converters: [], message_count: 1, related_conversation_ids: [], labels: {}, created_at: "", updated_at: "",
        target_unbound: true,
      },
      messages: { conversation_id: "copied-conversation", messages: source, target_response_status: null },
    });
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    const router = createMemoryRouter([{
      path: "/", element: <ChatWindow {...defaultProps} attackResultId="existing" conversationId="source"
        activeConversationId="source" activeTarget={target} defaultBranchTarget={mockTarget} />,
    }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);
    await user.click(await screen.findByRole("button", { name: "Copy or Branch" }));
    await user.click(screen.getByRole("menuitem", { name: "Branch attack", exact: true }));
    await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledTimes(1));
    const request = mockedAttacksApi.saveConversation.mock.calls[0][0];
    expect(request.destination).toBe("new_attack");
    expect(request.target_registry_name).toBeUndefined();
    expect(request.messages[0].pieces[0].converted_value_data_type).toBe("function_call");
    await waitFor(() => expect(defaultProps.onConversationCreated).toHaveBeenCalledWith(
      "copied", "copied-conversation", "", null,
    ));
    expect(screen.queryByTestId("conversation-editor")).not.toBeInTheDocument();
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
  });

  it.each<AttackTargetResolutionStatus>(["resolved", "unavailable", "ambiguous", "legacy", "error"])(
    "should update the human score with target status %s",
    async (targetResolutionStatus) => {
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
          activeTarget={targetResolutionStatus === "resolved" ? mockTarget : null}
          targetResolutionStatus={targetResolutionStatus}
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

  it.each<AttackTargetResolutionStatus>(["resolved", "unavailable", "ambiguous", "legacy", "error"])(
    "should remove the human score with target status %s",
    async (targetResolutionStatus) => {
    const user = userEvent.setup();
    const onHumanScoreChange = jest.fn();
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
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
          activeTarget={targetResolutionStatus === "resolved" ? mockTarget : null}
          targetResolutionStatus={targetResolutionStatus}
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
      <UserPreferencesProvider accountKey="local">
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
      </UserPreferencesProvider>
    );

    await user.click(screen.getByRole("link", {
      name: `Return to scenario run ${scenarioResultId}`,
    }));

    expect(screen.getByRole("heading", {
      level: 1,
      name: "Originating scenario run",
    })).toBeInTheDocument();
  });

  it("should apply and persist the Markdown choice across remounts", async () => {
    const user = userEvent.setup();
    mockedAttacksApi.getMessages.mockResolvedValue(makeTextResponse("**Hello**").messages);
    const chat = (
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-markdown"
          conversationId="conv-markdown"
          activeConversationId="conv-markdown"
        />
      </TestWrapper>
    );
    const firstRender = render(chat);

    const toggle = screen.getByRole("switch", { name: /markdown/i });
    expect(toggle).not.toBeChecked();
    expect(await screen.findByText("**Hello**")).toBeInTheDocument();

    await user.click(toggle);
    expect(toggle).toBeChecked();
    expect(readUserPreferences('local').chatMarkdown).toBe(true);
    expect(screen.getByText("Hello", { selector: "strong" })).toBeInTheDocument();

    firstRender.unmount();
    const secondRender = render(chat);
    const remountedToggle = screen.getByRole("switch", { name: /markdown/i });
    expect(remountedToggle).toBeChecked();
    expect(await screen.findByText("Hello", { selector: "strong" })).toBeInTheDocument();

    await user.click(remountedToggle);
    expect(remountedToggle).not.toBeChecked();
    expect(readUserPreferences('local').chatMarkdown).toBe(false);
    expect(screen.getByText("**Hello**")).toBeInTheDocument();

    secondRender.unmount();
    render(chat);
    expect(screen.getByRole("switch", { name: /markdown/i })).not.toBeChecked();
    expect(await screen.findByText("**Hello**")).toBeInTheDocument();
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

  it("should offer target selection in the ribbon without a bottom warning", () => {
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={null} />
      </TestWrapper>
    );

    expect(screen.getByRole("combobox", { name: "Chat target" })).toHaveValue("");
    expect(screen.queryByTestId("no-target-banner")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Configure Target" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add objective/i })).toBeEnabled();
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

    await user.click(screen.getByRole("button", { name: "New Attack", exact: true }));

    expect(onNewAttack).toHaveBeenCalled();
  });

  it("should allow drafting but disable sending when no target is selected", () => {
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={null} />
      </TestWrapper>
    );

    expect(screen.getByRole("textbox")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Send message" })).toHaveAttribute("aria-disabled", "true");
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
    expect(screen.getByTestId("copy-to-input-btn-0")).toBeEnabled();
    expect(screen.queryByTestId("branch-conv-btn-0")).not.toBeInTheDocument();
    expect(screen.queryByTestId("branch-attack-btn-0")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Conversation", exact: true })).toBeEnabled();
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
        <ChatWindow {...defaultProps} activeTarget={targetNoModel} availableTargets={[targetNoModel]} />
      </TestWrapper>
    );

    const badge = screen.getByTestId("target-badge");
    expect(badge).toHaveTextContent(/OpenAIChatTarget/);
    expect(badge).not.toHaveTextContent(/gpt/);
  });

  // -----------------------------------------------------------------------
  // First message → create attack + send
  // -----------------------------------------------------------------------

  it("builds a private temperature setting before creating the attack", async () => {
    const user = userEvent.setup();
    const source = { ...mockTarget, supports_temperature_override: true };
    const onConversationCreated = jest.fn();
    jest.mocked(targetsApi.buildTarget).mockResolvedValue({
      identifier: { ...source.identifier, hash: "private-hash", temperature: 0.8 },
      capabilities: source.capabilities,
    });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockedMapper.buildMessagePieces.mockResolvedValue([{ data_type: "text", original_value: "Hello" }]);
    mockSendResult.mockResolvedValue({
      ...makeTextResponse("Reply"),
      attack: { attack_result_id: "default-created-attack", conversation_id: "default-created-conversation" },
    } as never);
    render(<TestWrapper><ChatWindow {...defaultProps} activeTarget={source}
      onConversationCreated={onConversationCreated} /></TestWrapper>);
    expect(screen.getByText("Temperature:")).toBeInTheDocument();
    expect(screen.queryByText("Empty keeps the source setting.")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Temperature"), "0.8");
    await user.type(screen.getByRole("textbox"), "Hello");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockedAttacksApi.createAttack).toHaveBeenCalledTimes(1));
    const binding = {
      version: 1, source_name: source.target_registry_name, source_hash: source.identifier.hash,
      temperature: 0.8, effective_hash: "private-hash",
    };
    expect(targetsApi.buildTarget).toHaveBeenCalledWith("OpenAIChatTarget", {
      source_name: source.target_registry_name, source_hash: source.identifier.hash,
      params: { temperature: 0.8 },
    });
    expect(mockedAttacksApi.createAttack.mock.calls[0][0].target_binding).toEqual(binding);
    expect(onConversationCreated.mock.calls[0][3].binding).toEqual(binding);
    expect(source.identifier.hash).not.toBe("private-hash");
  });

  it.each([
    { ready: true, generation: "gen-1", defaultsReady: false },
    { ready: true, generation: "gen-2", defaultsReady: true },
    { ready: false, generation: "gen-1", defaultsReady: true },
  ])("preserves the first-send draft when launch state becomes %j during temperature construction", async (
    next: { ready: boolean; generation: string; defaultsReady: boolean },
  ) => {
    const user = userEvent.setup();
    const runtime = jest.spyOn(runtimeHooks, "useRuntime").mockReturnValue({
      ready: true, state: "ready", generation: "gen-1",
    });
    const source = { ...mockTarget, supports_temperature_override: true };
    const built = {
      identifier: { ...source.identifier, hash: "private-hash", temperature: 0.8 },
      capabilities: source.capabilities,
    };
    let finish: (value: typeof built) => void = () => {};
    jest.mocked(targetsApi.buildTarget).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    mockedMapper.buildMessagePieces.mockResolvedValue([{ data_type: "text", original_value: "Prepared prompt" }]);
    const rendered = render(<TestWrapper><ChatWindow {...defaultProps} activeTarget={source} /></TestWrapper>);
    await user.type(screen.getByLabelText("Temperature"), "0.8");
    const input = screen.getByRole("textbox");
    await user.type(input, "Prepared prompt");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(targetsApi.buildTarget).toHaveBeenCalledTimes(1));
    runtime.mockReturnValue({
      ready: next.ready, state: next.ready ? "ready" : "unavailable", generation: next.generation,
    });
    rendered.rerender(<TestWrapper><ChatWindow {...defaultProps} activeTarget={source}
      defaultsReady={next.defaultsReady} labels={{ operation: "replacement" }} /></TestWrapper>);
    await act(async () => { finish(built); });
    expect(await screen.findByText(/Runtime or default labels changed while preparing this message/)).toBeInTheDocument();
    expect(input).toHaveValue("Prepared prompt");
    expect(screen.getByLabelText("Temperature")).toHaveValue(0.8);
    expect(mockedAttacksApi.createAttack).not.toHaveBeenCalled();
    expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled();
  });

  it("shows saved temperature as read-only", () => {
    const target = {
      ...mockTarget, supports_temperature_override: true,
      identifier: { ...mockTarget.identifier, temperature: 0.8 },
    };
    render(<TestWrapper><ChatWindow {...defaultProps} activeTarget={target}
      attackResultId="existing" conversationId="conversation" /></TestWrapper>);
    expect(screen.getByLabelText("Temperature")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Temperature")).toHaveValue(0.8);
    expect(screen.queryByText("Read-only for this attack.")).not.toBeInTheDocument();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it.each([
    { interaction: "hover", reconstructionError: "The selected target cannot be recreated with its current settings." },
    { interaction: "keyboard", reconstructionError: "The selected target cannot be recreated with its current settings." },
    { interaction: "hover", reconstructionError: undefined },
    { interaction: "keyboard", reconstructionError: undefined },
  ])("explains unavailable temperature only on $interaction: $reconstructionError", async ({
    interaction, reconstructionError,
  }: { interaction: string; reconstructionError: string | undefined }) => {
    const user = userEvent.setup();
    const target = {
      ...mockTarget,
      supports_temperature_override: false,
      reconstruction_error: reconstructionError,
    };
    const explanation = reconstructionError ?? "This target does not support a separate temperature setting.";
    render(<TestWrapper><ChatWindow {...defaultProps} activeTarget={target} /></TestWrapper>);
    const input = screen.getByRole("spinbutton", { name: "Temperature" });
    expect(input).toBeDisabled();
    expect(screen.getByText(explanation)).not.toBeVisible();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    if (interaction === "hover") await user.hover(input);
    else {
      await user.click(screen.getByRole("combobox", { name: "Chat target" }));
      await user.tab();
    }
    expect(await screen.findByRole("tooltip")).toHaveTextContent(explanation);
    expect(targetsApi.buildTarget).not.toHaveBeenCalled();
  });

  it.each([
    { interaction: "hover", supportsOverride: true },
    { interaction: "click", supportsOverride: true },
    { interaction: "hover", supportsOverride: false },
    { interaction: "click", supportsOverride: false },
  ])("explains read-only temperature on $interaction with override support $supportsOverride", async ({
    interaction, supportsOverride,
  }: { interaction: string; supportsOverride: boolean }) => {
    const user = userEvent.setup();
    const target = {
      ...mockTarget, supports_temperature_override: supportsOverride,
      identifier: { ...mockTarget.identifier, temperature: 0.8 },
    };
    render(<TestWrapper><ChatWindow {...defaultProps} activeTarget={target}
      attackResultId="existing" conversationId="conversation" /></TestWrapper>);
    const input = screen.getByLabelText("Temperature");
    expect(screen.queryByText("Read-only for this attack.")).not.toBeInTheDocument();
    if (interaction === "hover") await user.hover(input);
    else await user.click(input);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      supportsOverride
        ? "Temperature can only be modified in a new attack"
        : "This target does not support a separate temperature setting."
    );
    expect(input).toHaveAttribute("readonly");
    expect(input).toHaveValue(0.8);
    expect(targetsApi.buildTarget).not.toHaveBeenCalled();
  });

  it("should preserve operation attribution when the first message creates the attack", async () => {
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
    mockSendResult.mockResolvedValue({
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
          labels={{ operator: 'testuser' }}
          operationId="operation-1"
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Hello");
    await user.click(screen.getByRole("button", { name: "Send message", exact: true }));

    await waitFor(() => {
      expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith({
        target_registry_name: "openai_chat_1",
        labels: { operator: 'testuser' },
        operation_id: "operation-1",
        system_prompt: undefined,
      });
      expect(onConversationCreated).toHaveBeenCalledWith("ar-conv-1", "conv-1", undefined);
      expect(mockSendResult).toHaveBeenCalledWith("ar-conv-1", {
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
    mockSendResult.mockResolvedValue(makeTextResponse("Hello back!") as never);
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
    await user.click(screen.getByRole("button", { name: "Send message" }));

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

  it("keeps a cleared objective empty after the first send and when opening the editor", async () => {
    const user = userEvent.setup();
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Hello" },
    ]);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-objective",
      conversation_id: "conv-objective",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockSendResult.mockResolvedValue(makeTextResponse("Hello back!") as never);
    mockedAttacksApi.updateAttack.mockResolvedValue({
      attack_result_id: "ar-objective", objective: "",
    } as Awaited<ReturnType<typeof attacksApi.updateAttack>>);
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    function ObjectiveHarness() {
      const [attackId, setAttackId] = useState<string | null>(null);
      const [savedObjective, setSavedObjective] = useState("");
      return <ChatWindow {...defaultProps} attackResultId={attackId}
        conversationId={attackId ? "conv-objective" : null}
        activeConversationId={attackId ? "conv-objective" : null}
        objective={savedObjective} onObjectiveChange={setSavedObjective}
        onConversationCreated={(id, _conversationId, value) => {
          setAttackId(id);
          setSavedObjective(value ?? "");
        }} />;
    }
    const router = createMemoryRouter([{ path: "/", element: <ObjectiveHarness /> }]);
    render(<FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <RouterProvider router={router} />
    </UserPreferencesProvider></FluentProvider>);

    await user.click(screen.getByRole("button", { name: /add objective/i }));
    await user.type(screen.getByRole("textbox", { name: /attack objective/i }), "Old draft objective");
    await user.click(screen.getByRole("button", { name: "Save", exact: true }));
    await user.type(screen.getByPlaceholderText("Type prompt here"), "Hello");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalled());
    await user.click(await screen.findByRole("button", { name: /edit objective/i }));
    await user.clear(screen.getByRole("textbox", { name: /attack objective/i }));
    await user.click(screen.getByRole("button", { name: "Save", exact: true }));
    expect(await screen.findByRole("button", { name: /add objective/i })).toBeInTheDocument();
    expect(screen.queryByText("Old draft objective")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit Conversation", exact: true }));
    await screen.findByRole("button", { name: "Convert Conversation" });
    await user.click(screen.getByRole("button", { name: /add objective/i }));
    expect(screen.getByRole("textbox", { name: /attack objective/i })).toHaveValue("");
  });

  it("keeps the objective baseline through a refresh and retains the draft on conflict", async () => {
    const user = userEvent.setup();
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockedAttacksApi.updateAttack.mockRejectedValue({
      response: { status: 409, data: { detail: "The objective changed." } },
    });
    const onObjectiveChange = jest.fn();
    const onAttackChange = jest.fn();
    const props = {
      ...defaultProps, attackResultId: "ar-existing", conversationId: "conv-existing",
      activeConversationId: "conv-existing", onObjectiveChange, onAttackChange,
    };
    const { rerender } = render(
      <TestWrapper><ChatWindow {...props} objective="A" /></TestWrapper>
    );
    await user.click(await screen.findByRole("button", { name: "Edit objective" }));
    await user.clear(screen.getByRole("textbox", { name: "Attack objective" }));
    await user.type(screen.getByRole("textbox", { name: "Attack objective" }), "Edited A");
    rerender(<TestWrapper><ChatWindow {...props} objective="B" /></TestWrapper>);
    expect(screen.getByRole("textbox", { name: "Attack objective" })).toHaveValue("Edited A");
    await user.click(screen.getByRole("button", { name: "Save", exact: true }));
    await waitFor(() => expect(mockedAttacksApi.updateAttack).toHaveBeenCalledWith(
      "ar-existing", { objective: "Edited A", expected_objective: "A" },
    ));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unable to save the objective.");
    expect(screen.getByRole("textbox", { name: "Attack objective" })).toHaveValue("Edited A");
    expect(onObjectiveChange).not.toHaveBeenCalled();
    expect(onAttackChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cancel", exact: true }));
    expect(screen.getByRole("button", { name: "Edit objective" })).toHaveTextContent("B");
    await user.click(screen.getByRole("button", { name: "Edit objective" }));
    await user.clear(screen.getByRole("textbox", { name: "Attack objective" }));
    await user.type(screen.getByRole("textbox", { name: "Attack objective" }), "Edited B");
    await user.click(screen.getByRole("button", { name: "Save", exact: true }));
    await waitFor(() => expect(mockedAttacksApi.updateAttack).toHaveBeenLastCalledWith(
      "ar-existing", { objective: "Edited B", expected_objective: "B" },
    ));
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
      mockSendResult.mockResolvedValue(
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

    it("renders a system message in transcript order", async () => {
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

      expect(await screen.findByTestId("message-list")).toHaveTextContent("You are a pirate.");
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
      await user.click(screen.getByRole("button", { name: "Send message" }));

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
      await user.click(screen.getByRole("button", { name: "Send message" }));

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
      await user.click(screen.getByRole("button", { name: "Send message" }));

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
      await user.click(screen.getByRole("button", { name: "Send message" }));

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
      await user.click(screen.getByRole("button", { name: "Send message" }));

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
      await user.click(screen.getByRole("button", { name: "Send message" }));

      await waitFor(() => {
        expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(
          expect.objectContaining({ system_prompt: "You are helpful" })
        );
      });

    });

    it("preserves the draft prompt while its target is unavailable during refresh", async () => {
      const user = userEvent.setup();
      primeSendMocks();
      const supported: TargetInstance = {
        ...mockTarget,
        capabilities: buildCapabilities({ supports_system_prompt: true }),
      };
      const { rerender } = render(
        <TestWrapper><ChatWindow {...defaultProps} activeTarget={supported} /></TestWrapper>,
      );
      await user.click(screen.getByRole("button", { name: /system prompt/i }));
      await user.type(screen.getByRole("textbox", { name: /system prompt/i }), "Keep this instruction");
      await user.type(screen.getByPlaceholderText("Type prompt here"), "Hello");
      rerender(<TestWrapper><ChatWindow {...defaultProps} activeTarget={null} /></TestWrapper>);
      expect(screen.getByRole("button", { name: "Send message" })).toHaveAttribute("aria-disabled", "true");
      expect(mockedAttacksApi.createAttack).not.toHaveBeenCalled();
      rerender(<TestWrapper><ChatWindow {...defaultProps} activeTarget={supported} /></TestWrapper>);
      await user.click(screen.getByRole("button", { name: "Send message" }));
      await waitFor(() => expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(
        expect.objectContaining({ system_prompt: "Keep this instruction" }),
      ));
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
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => {
      expect(screen.getByText(/Network error/)).toBeInTheDocument();
    });
  });

  it("should preserve a first-send error when the created attack route commits later", async () => {
    const user = userEvent.setup();
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Keep failed draft" },
    ]);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-created",
      conversation_id: "conv-created",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockSendResult.mockRejectedValue({
      isAxiosError: true,
      response: { status: 400, data: { detail: "First send failed" } },
    });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    const { rerender } = render(
      <TestWrapper>
        <ChatWindow {...defaultProps} />
      </TestWrapper>
    );
    await user.type(screen.getByRole("textbox"), "Keep failed draft");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText(/First send failed/)).toBeInTheDocument();

    rerender(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          attackResultId="ar-created"
          conversationId="conv-created"
          activeConversationId="conv-created"
        />
      </TestWrapper>
    );

    expect(mockedAttacksApi.getMessages).not.toHaveBeenCalled();
    expect(screen.getByText(/First send failed/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("Keep failed draft");
    expect(screen.getByRole("button", { name: "Send message" })).not.toHaveAttribute("aria-disabled");
  });

  it("should reload when returning while another conversation load is still pending", async () => {
    const history = makeTextResponse("Original history").messages;
    let finishOtherLoad: (value: typeof history) => void = () => {};
    mockedAttacksApi.getMessages
      .mockResolvedValueOnce(history)
      .mockImplementationOnce(() => new Promise((resolve) => { finishOtherLoad = resolve; }))
      .mockResolvedValueOnce(history);
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    const props = {
      ...defaultProps,
      attackResultId: "ar-return",
      conversationId: "conv-original",
      activeConversationId: "conv-original",
    };
    const { rerender } = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    expect(await screen.findByText("Original history")).toBeInTheDocument();

    rerender(<TestWrapper><ChatWindow {...props} activeConversationId="conv-other" /></TestWrapper>);
    expect(mockedAttacksApi.getMessages).toHaveBeenLastCalledWith("ar-return", "conv-other");
    rerender(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    expect(mockedAttacksApi.getMessages).toHaveBeenLastCalledWith("ar-return", "conv-original");
    expect(await screen.findByText("Original history")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeEnabled();

    await act(async () => { finishOtherLoad(makeTextResponse("Other history").messages); });
    expect(screen.getByText("Original history")).toBeInTheDocument();
    expect(screen.queryByText("Other history")).not.toBeInTheDocument();
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
      mockSendResult.mockResolvedValue({ messages: failedMessages } as never);
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
      const recover = await findRecoveryAction(user);
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
      expect(screen.queryByText(/Continue in a clean conversation/i)).not.toBeInTheDocument();

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
      expect(mockSendResult).toHaveBeenCalledTimes(source === "live" ? 1 : 0);
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
    mockSendResult.mockRejectedValue(new Error("Request rejected"));
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
    expect(sendButton).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled();
    await user.click(sendButton);
    await user.keyboard("{Enter}");
    expect(mockSendResult).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("Unsent draft");

    await act(async () => {
      resolveSecondHistory(secondHistory);
    });
    expect(await screen.findByText("Only conversation B history")).toBeInTheDocument();
    expect(screen.queryByText("Only conversation A history")).not.toBeInTheDocument();
    await user.click(sendButton);
    await waitFor(() => {
      expect(mockSendResult).toHaveBeenCalledWith(
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
      mockSendResult.mockImplementation(() => new Promise((_resolve, reject) => {
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
      await waitFor(() => expect(mockSendResult).toHaveBeenCalledTimes(1));
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
    await user.click(await findRecoveryAction(user));
    expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(
      "ar-blocked-prefix",
      { source_conversation_id: "conv-blocked-prefix", cutoff_index: 1 },
    );
    expect(screen.queryByText(/history from the first failed prompt onward/i)).not.toBeInTheDocument();
    expect(mockSendResult).not.toHaveBeenCalled();
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
    mockSendResult
      .mockResolvedValueOnce(failedResponse as never)
      .mockResolvedValueOnce(makeTextResponse("Recovered response") as never);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "conv-media-recovery",
      created_at: "2026-01-01T00:00:02Z",
    });
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [
        makeConverterInstance(
          "preserved-image-converter",
          "ImageRotationConverter",
          ["image_path"],
          ["image_path"]
        ),
      ],
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      original_value: "/original/evidence.png",
      original_value_data_type: "image_path",
      converted_value: "/converted/evidence.png",
      converted_value_data_type: "image_path",
      steps: [{
        converter_id: "preserved-image-converter",
        converter_type: "ImageRotationConverter",
        input_value: "/original/evidence.png",
        input_data_type: "image_path",
        output_value: "/converted/evidence.png",
        output_data_type: "image_path",
      }],
    });

    const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    await user.upload(screen.getByTestId("file-input"), file);
    await user.click(screen.getByRole("button", { name: /convert/i }));
    await user.click(await screen.findByRole("tab", { name: "Image" }));
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /ImageRotationConverter/ }));
    await user.click(screen.getByRole("button", { name: /^convert$/i }));
    expect(screen.queryByRole("button", { name: /add converted value/i })).not.toBeInTheDocument();
    await user.click(screen.getByTestId("close-converter-panel-btn"));
    await user.click(screen.getByRole("button", { name: /send message/i }));
    expect(await findRecoveryAction(user)).toBeEnabled();
    expect(mockSendResult).toHaveBeenLastCalledWith(
      props.attackResultId,
      expect.objectContaining({
        pieces: [expect.objectContaining({ applied_converter_ids: ["preserved-image-converter"] })],
      }),
    );

    rendered.rerender(<TestWrapper><ChatWindow {...props} activeConversationId="conv-other" /></TestWrapper>);
    await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled());
    await user.click(screen.getByTestId("remove-attachment-0"));
    await waitFor(() => {
      expect(screen.queryByTestId("clear-media-conversion-image")).not.toBeInTheDocument();
    });
    rendered.rerender(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    const recover = await findRecoveryAction(user);
    deferFileReads = true;
    await user.click(recover);
    await waitFor(() => {
      expect(props.onSelectConversation).toHaveBeenCalledWith("conv-media-recovery");
    });
    rendered.rerender(
      <TestWrapper><ChatWindow {...props} activeConversationId="conv-media-recovery" /></TestWrapper>
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /send message/i })).not.toHaveAttribute("aria-disabled"));
    expect(deferredReads).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => expect(deferredReads).toHaveLength(1));
    await act(async () => {
      await deferredReads[0]();
    });
    expect(mockSendResult).toHaveBeenLastCalledWith(
      props.attackResultId,
      expect.objectContaining({
        target_conversation_id: "conv-media-recovery",
        pieces: [expect.objectContaining({
          data_type: "image_path", applied_converter_ids: ["preserved-image-converter"],
        })],
      }),
    );
  });

  it("should preserve the draft and expose recovery for an HTTP 200 processing error", async () => {
    const user = userEvent.setup();
    const onSelectConversation = jest.fn();

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "retry this prompt" },
    ]);
    mockSendResult.mockResolvedValue(
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
    await user.click(screen.getByRole("button", { name: "Send message" }));

    const recoveryButton = await findRecoveryAction(user);
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
    mockSendResult.mockImplementation(
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
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockSendResult).toHaveBeenCalledTimes(1));
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
      await findRecoveryAction(user)
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
    mockSendResult.mockImplementation(
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
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockSendResult).toHaveBeenCalledTimes(1));
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
      await findRecoveryAction(user)
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
      mockSendResult.mockResolvedValue(failedResponse as never);
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
      await user.click(screen.getByRole("button", { name: "Send message" }));
      expect(await findRecoveryAction(user)).toBeEnabled();

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
      expect(mockSendResult).toHaveBeenCalledTimes(1);
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
      mockSendResult.mockResolvedValue(failedResponse as never);
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
      await user.click(screen.getByRole("button", { name: "Send message" }));
      expect(await findRecoveryAction(user)).toBeEnabled();

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
      const recoveryButton = await findRecoveryAction(user);
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

  it.each([
    { reinitialized: false, destination: "Branch conversation" },
    { reinitialized: true, destination: "Branch conversation" },
    { reinitialized: false, destination: "Branch attack" },
    { reinitialized: true, destination: "Branch attack" },
  ])("should restore the matching live draft to $destination with reinitialization $reinitialized", async ({
    reinitialized, destination,
  }: { reinitialized: boolean; destination: string }) => {
    const user = userEvent.setup();
    const runtime = jest.spyOn(runtimeHooks, "useRuntime").mockReturnValue({
      ready: true, state: "ready", generation: "original",
    });
    const onSelectConversation = jest.fn();
    const onConversationCreated = jest.fn();
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
        target_registry_name: "live-retry-target",
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
      onConversationCreated,
    };
    mockedAttacksApi.getConversations.mockResolvedValue({
      main_conversation_id: props.conversationId,
      conversations: [{ conversation_id: props.conversationId, message_count: 2 }],
    });
    mockedAttacksApi.getMessages.mockResolvedValueOnce({ messages: [] } as never);
    mockSendResult.mockResolvedValue(failedResponse as never);
    mockedAttacksApi.createConversation.mockResolvedValue({
      conversation_id: "conv-matching-recovery",
      created_at: "2026-01-01T00:00:04Z",
    });
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "recovered-attack",
      conversation_id: "conv-matching-recovery",
      created_at: "2026-01-01T00:00:04Z",
    });
    mockedMapper.backendMessagesToFrontend.mockImplementation(actualMessageMapper.backendMessagesToFrontend);
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [
        makeConverterInstance(
          "live-pdf-converter",
          "PDFConverter",
          ["text"],
          ["binary_path"]
        ),
      ],
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      original_value: "live draft",
      original_value_data_type: "text",
      converted_value: "/converted/live.pdf",
      converted_value_data_type: "binary_path",
      steps: [{
        converter_id: "live-pdf-converter",
        converter_type: "PDFConverter",
        input_value: "live draft",
        input_data_type: "text",
        output_value: "/converted/live.pdf",
        output_data_type: "binary_path",
      }],
    });

    const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    await user.type(screen.getByRole("textbox"), "live draft");
    await user.upload(screen.getByTestId("file-input"), file);
    await user.click(screen.getByRole("button", { name: /convert/i }));
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /PDFConverter/ }));
    await user.click(screen.getByRole("button", { name: /^convert$/i }));
    expect(screen.queryByRole("button", { name: /add converted value/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /send message/i }));
    expect(await findRecoveryAction(user)).toBeEnabled();

    if (reinitialized) {
      runtime.mockReturnValue({ ready: true, state: "ready", generation: "replacement" });
      rendered.rerender(<TestWrapper><ChatWindow {...props} /></TestWrapper>);
    }
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
    const recoveryButton = await findRecoveryAction(user);
    await user.click(destination === "Branch conversation" ? recoveryButton
      : screen.getByRole("menuitem", { name: "Branch attack", exact: true }));
    await waitFor(() => {
      if (destination === "Branch conversation") {
        expect(onSelectConversation).toHaveBeenCalledWith("conv-matching-recovery");
        expect(mockedAttacksApi.createConversation).toHaveBeenCalledWith(props.attackResultId, {});
      } else {
        expect(onConversationCreated).toHaveBeenCalledWith("recovered-attack", "conv-matching-recovery", "", props.activeTarget);
      }
    });
    mockedAttacksApi.getMessages.mockResolvedValue({
      conversation_id: "conv-matching-recovery",
      messages: [],
      target_response_status: null,
    });
    rendered.rerender(
      <TestWrapper><ChatWindow {...props} activeConversationId="conv-matching-recovery"
        attackResultId={destination === "Branch attack" ? "recovered-attack" : props.attackResultId}
        conversationId={destination === "Branch attack" ? "conv-matching-recovery" : props.conversationId} /></TestWrapper>
    );

    expect(await screen.findByText(/live\.png/)).toBeInTheDocument();
    expect(screen.getByTestId("chat-input")).toHaveValue("live draft");
    if (reinitialized) {
      expect(screen.queryByTestId("converted-file-chip")).not.toBeInTheDocument();
      expect(screen.getByText(/Converter choices could not be restored/)).toBeInTheDocument();
    } else {
      expect(await screen.findByTestId("converted-file-chip")).toHaveTextContent("live.pdf");
      expect(screen.queryByText(/Converter choices could not be restored/)).not.toBeInTheDocument();
    }
    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => {
      expect(mockSendResult).toHaveBeenLastCalledWith(
        destination === "Branch attack" ? "recovered-attack" : props.attackResultId,
        expect.objectContaining({
          target_conversation_id: "conv-matching-recovery",
          pieces: expect.arrayContaining([reinitialized
            ? expect.objectContaining({ original_value: "live draft" })
            : expect.objectContaining({ applied_converter_ids: ["live-pdf-converter"] })]),
        })
      );
    });
    if (reinitialized) {
      expect(mockSendResult.mock.calls.at(-1)?.[1].pieces.every((piece) => !piece.applied_converter_ids)).toBe(true);
    }
    expect(mockedMapper.buildMessagePieces).toHaveBeenLastCalledWith(
      "live draft",
      [expect.objectContaining({ file, name: "live.png" })],
      undefined,
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

    const recoveryButton = await findRecoveryAction(user);
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
    expect(screen.getByText(/Converter choices could not be restored/)).toBeInTheDocument();
    expect(screen.getAllByText("evidence.png", { exact: false })).toHaveLength(1);
    expect(screen.queryByText(/converted\.pdf/i)).not.toBeInTheDocument();

    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [
        makeConverterInstance(
          "recovered-media-converter",
          "RecoveredMediaConverter",
          [originalDataType],
          [originalDataType]
        ),
      ],
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      original_value: "/original/evidence.png",
      original_value_data_type: originalDataType,
      converted_value: "/converted/evidence.png",
      converted_value_data_type: originalDataType,
      steps: [{
        converter_id: "recovered-media-converter",
        converter_type: "RecoveredMediaConverter",
        input_value: "/original/evidence.png",
        input_data_type: originalDataType,
        output_value: "/converted/evidence.png",
        output_data_type: originalDataType,
      }],
    });
    await user.click(screen.getByRole("button", { name: /convert/i }));
    await user.click(screen.getByRole("tab", {
      name: originalDataType === "image_path" ? "Image" : /file/i,
    }));
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /RecoveredMediaConverter/ }));
    const convertButton = screen.getByRole("button", { name: /^convert$/i });
    expect(convertButton).toBeEnabled();
    await user.click(convertButton);
    await waitFor(() => {
      expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith({
        original_value: "/original/evidence.png",
        original_value_data_type: originalDataType,
        converter_ids: ["recovered-media-converter"],
      });
    });
    expect(screen.queryByRole("button", { name: /add converted value/i })).not.toBeInTheDocument();

    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockSendResult.mockResolvedValue(makeTextResponse("recovered") as never);
    await user.click(screen.getByRole("button", { name: /send message/i }));
    await waitFor(() => {
      expect(mockSendResult).toHaveBeenCalledWith(
        "ar-persisted-processing",
        expect.objectContaining({
          target_conversation_id: "conv-persisted-recovery",
          pieces: expect.arrayContaining([
            expect.objectContaining({
              data_type: originalDataType,
              original_value: "/original/evidence.png",
              applied_converter_ids: ["recovered-media-converter"],
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
    mockSendResult.mockImplementation(
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
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockSendResult).toHaveBeenCalledTimes(1));

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
    mockSendResult.mockResolvedValue(
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
    await user.click(screen.getByRole("button", { name: "Send message" }));
    const recoveryButton = await findRecoveryAction(user);

    await user.click(recoveryButton);
    await waitFor(() => {
      expect(recoveryButton).toHaveAttribute("aria-disabled", "true");
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
    mockSendResult.mockResolvedValue(
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
    await user.click(screen.getByRole("button", { name: "Send message" }));

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
    mockSendResult.mockResolvedValue(
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
    await user.click(screen.getByRole("button", { name: "Send message" }));

    const recoveryButton = await findRecoveryAction(user);
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
    const onAttackChange = jest.fn();
    const attack: AttackSummary = {
      attack_result_id: "ar-conv-multi-turn", conversation_id: "conv-multi-turn",
      attack_type: "ManualAttack", objective: "", converters: [], outcome: "undetermined",
      message_count: 2, related_conversation_ids: [], labels: {},
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:01Z",
      last_response: makeTextResponse("Reply 1").messages.messages[0].message_pieces[0],
    };

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Turn 1" },
    ]);
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-conv-multi-turn",
      conversation_id: "conv-multi-turn",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockSendResult.mockResolvedValue({ attack, ...makeTextResponse("Reply 1") });
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
          onAttackChange={onAttackChange}
        />
      </TestWrapper>
    );

    const input = screen.getByRole("textbox");
    await user.type(input, "Turn 1");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Reply 1")).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(mockedAttacksApi.createAttack).toHaveBeenCalledTimes(1);
    expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith({
      target_registry_name: "openai_chat_1",
      labels: { operator: "testuser", operation: "test_op" },
      system_prompt: undefined,
    });
    expect(onConversationCreated).toHaveBeenCalledWith("ar-conv-multi-turn", "conv-multi-turn", undefined);
    expect(onAttackChange).toHaveBeenCalledWith(attack);
    expect(mockedAttacksApi.submitMessageSend).toHaveBeenNthCalledWith(
      1, "ar-conv-multi-turn", expect.objectContaining({
        role: "user", send: true, target_registry_name: "openai_chat_1",
        pieces: [{ data_type: "text", original_value: "Turn 1" }],
        target_conversation_id: "conv-multi-turn",
        submission_id: expect.any(String),
      }),
    );

    mockedMapper.buildMessagePieces.mockResolvedValue([
      { data_type: "text", original_value: "Turn 2" },
    ]);
    mockSendResult.mockResolvedValue({ attack: { ...attack, message_count: 4 }, ...makeTextResponse("Reply 2") });
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
          onAttackChange={onAttackChange}
        />
      </TestWrapper>
    );

    await user.type(screen.getByRole("textbox"), "Turn 2");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Reply 2")).toBeInTheDocument();
    expect(screen.getByText("Reply 1")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(mockedAttacksApi.createAttack).toHaveBeenCalledTimes(1);
    expect(onConversationCreated).toHaveBeenCalledTimes(1);
    expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(2);
    expect(mockedAttacksApi.submitMessageSend).toHaveBeenNthCalledWith(
      2, "ar-conv-multi-turn", expect.objectContaining({
        pieces: [{ data_type: "text", original_value: "Turn 2" }],
        target_registry_name: "openai_chat_1", target_conversation_id: "conv-multi-turn",
      }),
    );
  });

  // -----------------------------------------------------------------------
  // Media upload and response mapping
  // -----------------------------------------------------------------------

  it("should upload an image with text and display the mapped backend response", async () => {
    const user = userEvent.setup();
    const mediaTarget: TargetInstance = {
      ...mockTarget,
      capabilities: buildCapabilities({
        supports_multi_message_pieces: true,
        supported_input_modalities: ["text", "image_path"],
        supported_output_modalities: ["text", "image_path"],
      }),
    };
    const attack: AttackSummary = {
      attack_result_id: "ar-media", conversation_id: "conv-media",
      attack_type: "ManualAttack", objective: "", converters: [],
      message_count: 2, related_conversation_ids: [], labels: {},
      created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:01Z",
    };
    const progress: MessageSendStatus = {
      send_id: "media-send", attack_result_id: "ar-media", conversation_id: "conv-media",
      request_turn_number: 0, state: "completed", error: null, failure_stage: null,
    };
    mockedAttacksApi.getMessages.mockResolvedValueOnce({ messages: [] })
      .mockResolvedValue(makeMultiModalResponse().messages);
    mockedAttacksApi.getAttack.mockResolvedValue(attack);
    mockedAttacksApi.submitMessageSend.mockResolvedValue({ ...progress, state: "queued" });
    mockedAttacksApi.getMessageSend.mockResolvedValue(progress);
    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={mediaTarget}
          availableTargets={[mediaTarget]}
          attackResultId="ar-media"
          conversationId="conv-media"
          activeConversationId="conv-media"
        />
      </TestWrapper>
    );

    const input = screen.getByPlaceholderText("Type prompt here");
    await user.upload(screen.getByTestId("file-input"), new File(["image bytes"], "photo.png", { type: "image/png" }));
    expect(screen.getByRole("button", { name: "Remove photo.png" })).toBeInTheDocument();
    await user.type(input, "Describe this image");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("Here is the result:")).toBeInTheDocument();
    expect(input).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Remove photo.png" })).not.toBeInTheDocument();
    expect(screen.getByAltText("generated.jpeg")).toHaveAttribute("src", "/api/media?path=generated.jpeg");
    expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1);
    expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledWith(
      "ar-media", expect.objectContaining({
        target_registry_name: mediaTarget.target_registry_name,
        target_conversation_id: "conv-media",
        pieces: [
          { data_type: "text", original_value: "Describe this image" },
          { data_type: "image_path", original_value: "aW1hZ2UgYnl0ZXM=", mime_type: "image/png" },
        ],
      }),
    );
  });

  // -----------------------------------------------------------------------
  // No message sent when target is null (guard)
  // -----------------------------------------------------------------------

  it("should block send when active target is null", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={null} />
      </TestWrapper>
    );

    const send = screen.getByRole("button", { name: "Send message" });
    expect(send).toHaveAttribute("aria-disabled", "true");
    await user.click(send);
    expect(mockSendResult).not.toHaveBeenCalled();
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

  it.each([false, true])("should preserve the operator's panel choice after a late conversation update, narrow %s", async (isNarrowScreen: boolean) => {
    const user = userEvent.setup();
    mockMatchMedia(isNarrowScreen);
    const props = {
      ...defaultProps,
      attackResultId: "ar-late-conversations",
      conversationId: "conv-main",
      activeConversationId: "conv-main",
      relatedConversationCount: 0,
    };
    const rendered = render(
      <TestWrapper><ChatWindow {...props} /></TestWrapper>
    );
    const toggleButton = screen.getByRole("button", { name: "Toggle conversations panel" });
    expect(toggleButton).toHaveAttribute("aria-expanded", "false");

    await user.click(toggleButton);
    await user.click(await screen.findByRole("button", { name: "Close panel" }));
    await waitFor(() => {
      expect(screen.queryByRole(isNarrowScreen ? "dialog" : "complementary", {
        name: "Attack Conversations",
      })).not.toBeInTheDocument();
    });

    rendered.rerender(
      <TestWrapper><ChatWindow {...props} relatedConversationCount={1} /></TestWrapper>
    );

    expect(toggleButton).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole(isNarrowScreen ? "dialog" : "complementary", {
      name: "Attack Conversations",
    })).not.toBeInTheDocument();
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
    await userEvent.click(screen.getByRole("menuitem", { name: "Copy to next message" }));

    // The text should appear in the input area
    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toBe("This is the response text");
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

  it("should show only the registered name and LLM badge in the picker and keep stage settings accessible", async () => {
    const user = userEvent.setup();
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [{
        ...makeConverterInstance("translation_spanish", "TranslationConverter"),
        is_llm_based: true,
        reconstructable: true,
      }],
    });
    render(<TestWrapper><ChatWindow {...defaultProps} /></TestWrapper>);
    await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
    await user.click(await screen.findByRole("combobox", { name: "Add converter" }));
    const option = await screen.findByRole("option", { name: /translation_spanish.*TranslationConverter/ });
    expect(within(option).getByText("translation_spanish")).toBeInTheDocument();
    expect(within(option).getByText("LLM")).toBeInTheDocument();
    expect(within(option).queryByText("TranslationConverter")).not.toBeInTheDocument();
    await user.click(option);
    expect(screen.getByRole("button", { name: "Reorder converter translation_spanish" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Remove converter translation_spanish" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Settings for converter translation_spanish" }));
    expect(screen.getByRole("menuitem", { name: "Settings", exact: true })).not.toHaveAttribute("aria-disabled", "true");
  });

  it("should share draft inputs with the converter panel and retain edits through both close controls", async () => {
    const user = userEvent.setup();
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [makeConverterInstance("base64", "Base64Converter")],
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      original_value: "hello", original_value_data_type: "text",
      converted_value: "aGVsbG8=", converted_value_data_type: "text",
      steps: [{
        converter_id: "base64", converter_type: "Base64Converter",
        input_value: "hello", input_data_type: "text",
        output_value: "aGVsbG8=", output_data_type: "text",
      }],
    });
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
    await user.type(screen.getByPlaceholderText("Type prompt here"), "hello");
    await user.upload(screen.getByTestId("file-input"), new File(["image"], "draft.png", { type: "image/png" }));
    const toggle = screen.getByRole("button", { name: "Toggle converter panel" });
    await user.click(toggle);
    const panel = await screen.findByTestId("converter-panel");
    expect(within(panel).getByRole("textbox", { name: "Working input - Text" })).toHaveValue("hello");
    await user.click(await within(panel).findByRole("tab", { name: /^Image/ }));
    expect(within(panel).getByRole("img")).toBeInTheDocument();
    await user.click(within(panel).getByRole("tab", { name: /^Text/ }));
    await user.click(within(panel).getByRole("combobox", { name: "Add converter" }));
    await user.click(await screen.findByRole("option", { name: /Base64Converter/ }));
    await user.click(within(panel).getByRole("button", { name: "Convert", exact: true }));
    const output = await screen.findByRole("textbox", { name: "Stage 1 output - Text" });
    expect(output).toHaveValue("aGVsbG8=");
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith({
      original_value: "hello", converter_ids: ["base64"], original_value_data_type: "text",
    });
    await user.clear(output);
    await user.type(output, "manual result");
    expect(screen.getByRole("textbox", { name: /converted prompt/i })).toHaveValue("manual result");
    await user.click(screen.getByRole("button", { name: "Close converters" }));
    expect(screen.queryByTestId("converter-panel")).not.toBeInTheDocument();
    await user.click(toggle);
    expect(screen.getByRole("textbox", { name: "Stage 1 output - Text" })).toHaveValue("manual result");
    await user.click(toggle);
    expect(screen.queryByTestId("converter-panel")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /converted prompt/i })).toHaveValue("manual result");
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(1);
    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled();
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
    mockSendResult.mockResolvedValue(makeTextResponse("done") as never);

    render(
      <TestWrapper>
        <ChatWindow
          {...defaultProps}
          activeTarget={{
            ...mockTarget,
            capabilities: buildCapabilities({
              supported_input_modalities: ["image_path", "audio_path", "binary_path"],
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
    await user.click(screen.getByRole("menuitem", { name: "Copy to next message" }));

    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toBe("Here is an image");
    });
    expect(screen.getByText(/first\.png/)).toBeInTheDocument();
    expect(screen.getByText(/second\.wav/)).toBeInTheDocument();
    expect(screen.getAllByTestId(/^remove-attachment-/)).toHaveLength(3);

    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => {
      expect(mockedMapper.buildMessagePieces).toHaveBeenCalledWith(
        "Here is an image",
        [
          { ...copiedAttachments[0], draftId: expect.any(String) },
          { ...copiedAttachments[1], draftId: expect.any(String) },
          { ...copiedAttachments[2], draftId: expect.any(String) },
        ],
        undefined,
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
    await userEvent.click(screen.getByRole("menuitem", { name: "Copy to next message" }));

    await waitFor(() => {
      const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
      expect(textarea.value).toBe("Blocked media response");
    });
    expect(screen.queryByTestId("remove-attachment-0")).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Text → File converter flow (e.g. PDFConverter)
  // -----------------------------------------------------------------------

  it.each([false, true])("sends only the successful image after a partial failure (remove failed input: %s)", async (removeFailed: boolean) => {
    const user = userEvent.setup();
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [makeConverterInstance("compress", "ImageCompressionConverter", ["image_path"], ["image_path"])],
    });
    mockedConvertersApi.previewConversion.mockImplementation(async (request) => {
      if (request.original_value.endsWith("Zmlyc3Q=")) throw new Error("First image failed");
      return {
        original_value: request.original_value,
        original_value_data_type: "image_path",
        converted_value: "/converted/second.png",
        converted_value_data_type: "image_path",
        steps: [{
          converter_id: "compress", converter_type: "ImageCompressionConverter",
          input_value: request.original_value, input_data_type: "image_path",
          output_value: "/converted/second.png", output_data_type: "image_path",
        }],
      };
    });
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockSendResult.mockImplementation(() => new Promise(() => {}));
    render(<TestWrapper><ChatWindow
      {...defaultProps}
      attackResultId="ar-pieces"
      conversationId="conv-pieces"
      activeConversationId="conv-pieces"
      activeTarget={makeTarget({
        capabilities: buildCapabilities({ supported_input_modalities: ["text", "image_path"] }),
      })}
    /></TestWrapper>);
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeEnabled());
    await user.upload(screen.getByTestId("file-input"), [
      new File(["first"], "same.png", { type: "image/png" }),
      new File(["second"], "same.png", { type: "image/png" }),
    ]);
    await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
    await user.click(await screen.findByRole("tab", { name: "Image" }));
    await user.click(screen.getByRole("combobox", { name: "Add converter" }));
    await user.click(await screen.findByRole("option", { name: /ImageCompressionConverter/ }));
    await user.click(screen.getByRole("button", { name: "Convert", exact: true }));
    expect(await screen.findByTestId("converter-preview-error")).toHaveTextContent("First image failed");
    expect(screen.queryByRole("button", { name: "Add converted value" })).not.toBeInTheDocument();
    expect(screen.getAllByTestId("clear-media-conversion-image")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Close converters" }));
    if (removeFailed) await user.click(screen.getByTestId("remove-attachment-0"));
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(mockSendResult).toHaveBeenCalledWith("ar-pieces", expect.objectContaining({
      pieces: expect.arrayContaining([expect.objectContaining({ applied_converter_ids: ["compress"] })]),
    })));
    const request = mockSendResult.mock.calls[0][1];
    expect(request.pieces[removeFailed ? 0 : 1].original_value).toBe("c2Vjb25k");
    expect(request.pieces[removeFailed ? 0 : 1].applied_converter_ids).toEqual(["compress"]);
    expect(request.pieces).toHaveLength(removeFailed ? 1 : 2);
  });

  it.each<[number, "default" | RepeatConversionMode]>([
    [1, "default"], [3, "shared"], [3, "per_branch"],
  ])("keeps a pipeline after sending without reusing applied results (n=%s, mode=%s)", async (
    count: number, mode: "default" | RepeatConversionMode,
  ) => {
    const user = userEvent.setup();
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [
      makeConverterInstance("base64", "Base64Converter"),
      makeConverterInstance("suffix", "SuffixAppendConverter"),
    ] });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      original_value: "hello", original_value_data_type: "text",
      converted_value: "aGVsbG8=", converted_value_data_type: "text",
      steps: [{
        converter_id: "base64", converter_type: "Base64Converter",
        input_value: "hello", input_data_type: "text", output_value: "aGVsbG8=", output_data_type: "text",
      }],
    });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockSendResult.mockResolvedValue({
      attack: {
        attack_result_id: "ar-persist", conversation_id: "conv-persist",
        attack_type: "ManualAttack", objective: "", outcome: "undetermined", converters: [],
        message_count: 2, related_conversation_ids: [], labels: {}, created_at: "", updated_at: "",
      },
      ...makeTextResponse("response"),
    });
    render(<TestWrapper><ChatWindow
      {...defaultProps} attackResultId="ar-persist" conversationId="conv-persist" activeConversationId="conv-persist"
    /></TestWrapper>);
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeEnabled());
    await user.type(screen.getByTestId("chat-input"), "hello");
    await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
    await user.click(await screen.findByRole("combobox", { name: "Add converter" }));
    await user.click(await screen.findByRole("option", { name: /Base64Converter/ }));
    await user.click(screen.getByRole("button", { name: "Convert", exact: true }));
    expect(screen.queryByRole("button", { name: "Add converted value" })).not.toBeInTheDocument();
    await chooseCount(user, count, mode === "default" ? undefined : mode);
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(screen.getByTestId("chat-input")).toHaveValue(""));
    expect(screen.getByTestId("converter-item-base64")).toBeInTheDocument();
    expect(screen.queryByTestId("converted-indicator")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add converted value" })).not.toBeInTheDocument();
    await user.type(screen.getByTestId("chat-input"), "next");
    await user.click(screen.getByRole("combobox", { name: "Add converter" }));
    await user.click(await screen.findByRole("option", { name: /SuffixAppendConverter/ }));
    await user.click(screen.getByRole("combobox", { name: "Add converter" }));
    await user.click(await screen.findByRole("option", { name: /Base64Converter/ }));
    await chooseCount(user, count, mode === "default" ? undefined : mode);
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockSendResult).toHaveBeenCalledTimes(2));
    const firstRequest = mockedAttacksApi.submitMessageSend.mock.calls[0][1];
    const secondRequest = mockedAttacksApi.submitMessageSend.mock.calls[1][1];
    expect(firstRequest.pieces[0]).toEqual({
      data_type: "text", original_value: "hello", converted_value: "aGVsbG8=",
      converted_value_data_type: "text", applied_converter_ids: ["base64"],
    });
    expect(firstRequest).not.toHaveProperty("request_converter_configurations");
    expect(secondRequest.pieces).toEqual([{ data_type: "text", original_value: "next" }]);
    if (count > 1 && mode === "per_branch") {
      expect(secondRequest.request_converter_configurations).toEqual([
        { converter_ids: ["base64", "suffix", "base64"], indexes_to_apply: [0] },
      ]);
    } else {
      expect(secondRequest).not.toHaveProperty("request_converter_configurations");
    }
    if (count === 1) {
      expect(secondRequest).not.toHaveProperty("count");
      expect(secondRequest).not.toHaveProperty("request_converter_mode");
    } else {
      expect(secondRequest.count).toBe(count);
      expect(secondRequest.request_converter_mode).toBe(mode === "default" ? "shared" : mode);
    }
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(1);
  });

  it.each(["Working input - Text", "Stage 1 output - Text"])(
    "keeps unapplied edits to %s made while a send is pending",
    async (editorLabel: string) => {
      const user = userEvent.setup();
      let finishSend: (response: AddMessageResponse) => void = () => { throw new Error("Send not started"); };
      mockedConvertersApi.listConverters.mockResolvedValue({ items: [makeConverterInstance("base64", "Base64Converter")] });
      mockedConvertersApi.previewConversion.mockResolvedValue({
        original_value: "hello", original_value_data_type: "text",
        converted_value: "aGVsbG8=", converted_value_data_type: "text",
        steps: [{
          converter_id: "base64", converter_type: "Base64Converter",
          input_value: "hello", input_data_type: "text", output_value: "aGVsbG8=", output_data_type: "text",
        }],
      });
      mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
      mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
      mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
      mockSendResult.mockImplementation(() => new Promise((resolve) => { finishSend = resolve; }));
      render(<TestWrapper><ChatWindow
        {...defaultProps} attackResultId="ar-editing" conversationId="conv-editing" activeConversationId="conv-editing"
      /></TestWrapper>);
      await waitFor(() => expect(screen.getByTestId("chat-input")).toBeEnabled());
      await user.type(screen.getByTestId("chat-input"), "hello");
      await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
      await user.click(await screen.findByRole("combobox", { name: "Add converter" }));
      await user.click(await screen.findByRole("option", { name: /Base64Converter/ }));
      await user.click(screen.getByRole("button", { name: "Convert", exact: true }));
      await screen.findByRole("textbox", { name: "Stage 1 output - Text" });
      await user.click(screen.getByRole("button", { name: "Send message" }));
      await waitFor(() => expect(mockSendResult).toHaveBeenCalledTimes(1));
      const editor = screen.getByRole("textbox", { name: editorLabel });
      await user.clear(editor);
      await user.type(editor, "next draft");

      await act(async () => { finishSend({
        attack: {
          attack_result_id: "ar-editing", conversation_id: "conv-editing",
          attack_type: "ManualAttack", objective: "", converters: [], message_count: 2,
          related_conversation_ids: [], labels: {}, created_at: "", updated_at: "",
        },
        messages: { messages: [] },
      }); });

      expect(screen.getByTestId("chat-input")).toHaveValue("hello");
      expect(screen.getByRole("textbox", { name: editorLabel })).toHaveValue("next draft");
      expect(screen.queryByRole("button", { name: "Add converted value" })).not.toBeInTheDocument();
    },
  );

  it.each<[string, number, RepeatConversionMode]>([
    ["original chat", 3, "per_branch"], ["", 3, "shared"],
  ])("sends the edited pane result once with original text %j (previous n=%s, mode=%s)", async (
    original: string, count: number, mode: RepeatConversionMode,
  ) => {
    const user = userEvent.setup();
    const appliedResult = "  final manual result\nsecond line  ";
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [makeConverterInstance("base64", "Base64Converter")] });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      original_value: "working draft", original_value_data_type: "text",
      converted_value: "generated", converted_value_data_type: "text",
      steps: [{
        converter_id: "base64", converter_type: "Base64Converter",
        input_value: "working draft", input_data_type: "text", output_value: "generated", output_data_type: "text",
      }],
    });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockSendResult.mockImplementation(() => new Promise(() => {}));
    render(<TestWrapper><ChatWindow
      {...defaultProps} attackResultId="ar-edited" conversationId="conv-edited" activeConversationId="conv-edited"
    /></TestWrapper>);
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeEnabled());
    if (original) await user.type(screen.getByTestId("chat-input"), original);
    await chooseCount(user, count, mode);
    await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
    await user.click(await screen.findByRole("combobox", { name: "Add converter" }));
    await user.click(await screen.findByRole("option", { name: /Base64Converter/ }));
    const working = screen.getByRole("textbox", { name: "Working input - Text" });
    await user.clear(working);
    await user.type(working, "working draft");
    expect(screen.getByTestId("chat-input")).toHaveValue(original);
    await user.click(screen.getByRole("button", { name: "Convert", exact: true }));
    const output = await screen.findByRole("textbox", { name: "Stage 1 output - Text" });
    await user.clear(output);
    await user.type(output, appliedResult);
    expect(screen.queryByRole("button", { name: "Add converted value" })).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-input")).toHaveValue(original);
    expect(screen.getByRole("textbox", { name: /converted prompt/i })).toHaveValue(appliedResult);
    expect(screen.getByRole("button", { name: "Repetitions: 1" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(mockSendResult).toHaveBeenCalledWith("ar-edited", expect.objectContaining({
      pieces: [{
        data_type: "text", original_value: original,
        converted_value: appliedResult, converted_value_data_type: "text",
        applied_converter_ids: ["base64"],
      }],
    })));
    expect(mockedAttacksApi.submitMessageSend.mock.calls[0][1]).not.toHaveProperty("request_converter_configurations");
    expect(mockedAttacksApi.submitMessageSend.mock.calls[0][1]).not.toHaveProperty("count");
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(1);
  });

  it("sends a manual conversion when only the pane working input is edited", async () => {
    const user = userEvent.setup();
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [] });
    mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] });
    mockedMapper.buildMessagePieces.mockImplementation(actualMessageMapper.buildMessagePieces);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);
    mockSendResult.mockImplementation(() => new Promise(() => {}));
    render(<TestWrapper><ChatWindow
      {...defaultProps} attackResultId="ar-manual" conversationId="conv-manual" activeConversationId="conv-manual"
    /></TestWrapper>);
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeEnabled());
    await user.type(screen.getByTestId("chat-input"), "original chat");
    await user.click(screen.getByRole("button", { name: "Toggle converter panel" }));
    const working = await screen.findByRole("textbox", { name: "Working input - Text" });
    await user.clear(working);
    await user.type(working, "manual result");
    expect(screen.queryByRole("button", { name: "Add converted value" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(mockSendResult).toHaveBeenCalledWith("ar-manual", expect.objectContaining({
      pieces: [{
        data_type: "text",
        original_value: "original chat",
        converted_value: "manual result",
        converted_value_data_type: "text",
        applied_converter_ids: [],
      }],
    })));
    expect(mockedConvertersApi.previewConversion).not.toHaveBeenCalled();
  });

  it("should render converted-file chip and synthesize file attachment when a text→file converter is used", async () => {
    const fileTarget = {
      ...mockTarget,
      capabilities: buildCapabilities({ supported_input_modalities: ["text", "binary_path"] }),
    };
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [
        makeConverterInstance("conv-pdf", "PDFConverter", ["text"], ["binary_path"]),
      ],
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      original_value: "make a pdf",
      original_value_data_type: "text",
      converted_value: "/tmp/results/report.pdf",
      converted_value_data_type: "binary_path",
      steps: [{
        converter_id: "conv-pdf",
        converter_type: "PDFConverter",
        input_value: "make a pdf",
        input_data_type: "text",
        output_value: "/tmp/results/report.pdf",
        output_data_type: "binary_path",
      }],
    });
    mockedAttacksApi.createAttack.mockResolvedValue({
      attack_result_id: "ar-pdf",
      conversation_id: "conv-pdf-flow",
      created_at: "2026-01-01T00:00:00Z",
    } as never);
    // Keep submission pending so the optimistic user message (with the
    // synthesized file attachment) remains in the DOM for assertion.
    mockSendResult.mockImplementation(
      () => new Promise(() => {}) as never
    );
    mockedMapper.buildMessagePieces.mockResolvedValue([
      { piece_type: "text", original_value: "make a pdf" } as never,
    ]);
    mockedMapper.backendMessagesToFrontend.mockReturnValue([]);

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} activeTarget={fileTarget} conversationId={null} />
      </TestWrapper>
    );

    // 1. Type input
    const chatInput = screen.getByTestId("chat-input");
    await userEvent.type(chatInput, "make a pdf");

    // 2. Open converter panel and select PDFConverter
    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));
    const combobox = screen.getByRole("combobox", { name: "Add converter" });
    await userEvent.click(combobox);
    const option = await screen.findByRole("option", { name: /PDFConverter/ });
    await userEvent.click(option);

    // 3. Convert the text-to-file converter explicitly.
    await waitFor(() => {
      expect(screen.getByTestId("converter-preview-btn")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("converter-preview-btn"));
    await waitFor(() => {
      expect(screen.getByTestId("converter-preview-result")).toBeInTheDocument();
    });

    // 4. Use the converted value — populates pieceConversions['text'] with binary_path output
    expect(screen.queryByTestId("use-converted-btn")).not.toBeInTheDocument();

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
    await waitFor(() => expect(sendBtn).not.toHaveAttribute("aria-disabled"));
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
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [makeConverterInstance("conv-b64-stale", "Base64Converter")],
    });
    mockedConvertersApi.previewConversion.mockResolvedValue({
      original_value: "hello",
      original_value_data_type: "text",
      converted_value: "aGVsbG8=",
      converted_value_data_type: "text",
      steps: [{
        converter_id: "conv-b64-stale",
        converter_type: "Base64Converter",
        input_value: "hello",
        input_data_type: "text",
        output_value: "aGVsbG8=",
        output_data_type: "text",
      }],
    });

    render(
      <TestWrapper>
        <ChatWindow {...defaultProps} conversationId={null} />
      </TestWrapper>
    );

    const chatInput = screen.getByTestId("chat-input");
    await userEvent.type(chatInput, "hello");

    await userEvent.click(screen.getByTestId("toggle-converter-panel-btn"));
    const combobox = screen.getByRole("combobox", { name: "Add converter" });
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
    expect(screen.queryByTestId("use-converted-btn")).not.toBeInTheDocument();

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

    it("disables export when the conversation is empty", () => {
      render(
        <TestWrapper>
          <ChatWindow {...defaultProps} />
        </TestWrapper>
      );
      expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled();
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
      // Submission never resolves, so the conversation stays in the sending state.
      mockSendResult.mockImplementation(() => new Promise(() => {}));
      await renderWithLoadedConversation();

      await user.type(screen.getByRole("textbox"), "hi");
      await user.click(screen.getByRole("button", { name: "Send message" }));

      await waitFor(() =>
        expect(screen.getByRole("button", { name: /export conversation/i })).toBeDisabled()
      );
    });
  });

  describe('defaults readiness gating scope', () => {
    const savedAttack: AttackSummary = {
      attack_result_id: "existing-attack",
      conversation_id: "conv-1",
      attack_type: "ManualAttack",
      objective: "",
      outcome: "undetermined",
      converters: [],
      message_count: 2,
      related_conversation_ids: [],
      operator: "testuser",
      operation: "original_op",
      labels: { team: "original_team" },
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:01Z",
    }
    const reply: AddMessageResponse = {
      attack: savedAttack,
      messages: { conversation_id: "conv-1", messages: [], target_response_status: null },
    }
    const copiedSource: Awaited<ReturnType<typeof attacksApi.getMessages>> = {
      conversation_id: "conv-1", target_response_status: null,
      messages: [{
        turn_number: 0, role: "assistant", created_at: "2026-01-01T00:00:00Z",
        message_pieces: [{
          id: "saved-piece", original_value_data_type: "text", converted_value_data_type: "text",
          original_value: "Saved reply", converted_value: "Saved reply", scores: [], response_error: "none",
        }],
      }],
    }

    it('allows sending a reply in an existing attack while defaults are loading', async () => {
      const user = userEvent.setup()
      const onAttackChange = jest.fn()
      mockedAttacksApi.getMessages.mockResolvedValue({ messages: [] })
      mockedMapper.backendMessagesToFrontend.mockReturnValue([])
      mockedMapper.buildMessagePieces.mockResolvedValue([
        { data_type: "text", original_value: "replying in an existing attack" },
      ])
      mockSendResult.mockResolvedValue(reply)
      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            defaultsReady={false}
            labels={{ ...defaultProps.labels, operation: "config_op_v2" }}
            onAttackChange={onAttackChange}
            attackResultId="existing-attack"
            conversationId="conv-1"
            activeConversationId="conv-1"
          />
        </TestWrapper>
      )
      const input = await screen.findByRole('textbox')
      await user.type(input, 'replying in an existing attack')
      await user.keyboard('{Enter}')

      await waitFor(() => {
        expect(onAttackChange).toHaveBeenCalledWith(expect.objectContaining({
          operation: "original_op", labels: { team: "original_team" },
        }))
      })
      expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledWith(
        "existing-attack", expect.objectContaining({ target_conversation_id: "conv-1" }),
      )
      expect(mockedAttacksApi.createAttack).not.toHaveBeenCalled()
      expect(mockedAttacksApi.updateAttack).not.toHaveBeenCalled()
    })

    it('blocks the first message of a new attack while defaults are loading', async () => {
      const user = userEvent.setup()
      render(<TestWrapper><ChatWindow {...defaultProps} defaultsReady={false} attackResultId={null} /></TestWrapper>)
      const input = await screen.findByRole('textbox')
      await user.type(input, 'starting a brand new attack')
      await user.keyboard('{Enter}')

      expect(screen.getByRole('button', { name: 'Send message' })).toHaveAttribute('aria-disabled', 'true')
      expect(input).toHaveValue('starting a brand new attack')
      expect(mockedAttacksApi.createAttack).not.toHaveBeenCalled()
      expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled()
    })

    it.each([
      { name: 'a target is missing', activeTarget: null, defaultsReady: true, newActivityBlockedReason: undefined,
        explanation: 'Select a target to send a message.' },
      { name: 'default labels are loading', activeTarget: mockTarget, defaultsReady: false, newActivityBlockedReason: undefined,
        explanation: 'Default labels are not ready. Wait for them to load.' },
      { name: 'an operation is unresolved', activeTarget: mockTarget, defaultsReady: false,
        newActivityBlockedReason: 'Choose a saved Operation, register, or clear this one to start new attacks and scans.',
        explanation: 'Choose a saved Operation, register, or clear this one to start new attacks and scans.' },
    ])('explains disabled Send when $name', async ({ activeTarget, defaultsReady, newActivityBlockedReason, explanation }: {
      activeTarget: TargetInstance | null
      defaultsReady: boolean
      newActivityBlockedReason: string | undefined
      explanation: string
    }) => {
      const user = userEvent.setup()
      render(<TestWrapper><ChatWindow {...defaultProps} activeTarget={activeTarget} defaultsReady={defaultsReady}
        newActivityBlockedReason={newActivityBlockedReason} attackResultId={null} /></TestWrapper>)
      await user.type(await screen.findByRole('textbox'), 'Keep this draft')
      await user.hover(screen.getByRole('button', { name: 'Send message' }))
      expect(await screen.findByRole('tooltip')).toHaveTextContent(`Send message: ${explanation}`)
      expect(screen.getByRole('button', { name: 'Send message' })).toHaveAttribute('aria-disabled', 'true')
      expect(mockedAttacksApi.createAttack).not.toHaveBeenCalled()
    })

    it.each([
      { name: "defaults start loading", ready: true, generation: "gen-1", defaultsReady: false },
      { name: "a new generation is already ready", ready: true, generation: "gen-2", defaultsReady: true },
      { name: "the runtime becomes unavailable", ready: false, generation: "gen-1", defaultsReady: true },
    ])('preserves a prepared draft when $name before attack creation', async (
      change: { name: string; ready: boolean; generation: string; defaultsReady: boolean },
    ) => {
      const user = userEvent.setup()
      const runtime = jest.spyOn(runtimeHooks, "useRuntime").mockReturnValue({
        ready: true, state: "ready", generation: "gen-1",
      })
      const file = new File(["image content"], "draft.png", { type: "image/png" })
      const props = {
        ...defaultProps,
        activeTarget: makeTarget({
          capabilities: buildCapabilities({
            supports_multi_message_pieces: true,
            supported_input_modalities: ["text", "image_path"],
          }),
        }),
      }
      const pieces: Awaited<ReturnType<typeof messageMapper.buildMessagePieces>> = [
        { data_type: "text", original_value: "prepared draft" },
        { data_type: "image_path", original_value: "aW1hZ2U=" },
      ]
      let resolvePieces: (value: typeof pieces) => void = () => {}
      mockedMapper.buildMessagePieces.mockReturnValueOnce(new Promise((resolve) => { resolvePieces = resolve }))
      mockedMapper.buildMessagePieces.mockResolvedValue(pieces)
      mockedMapper.backendMessagesToFrontend.mockReturnValue([])
      mockSendResult.mockResolvedValue({
        ...reply,
        attack: {
          ...savedAttack,
          attack_result_id: "default-created-attack",
          conversation_id: "default-created-conversation",
          operation: "config_op_v2",
        },
      })
      const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>)
      const input = screen.getByRole('textbox')
      await user.type(input, 'prepared draft')
      await user.upload(screen.getByTestId('file-input'), file)
      await user.click(screen.getByRole('button', { name: 'Send message' }))
      expect(mockedMapper.buildMessagePieces).toHaveBeenCalledTimes(1)

      const nextRuntime: RuntimeReadiness = {
        ready: change.ready, state: change.ready ? "ready" : "unavailable", generation: change.generation,
      }
      runtime.mockReturnValue(nextRuntime)
      rendered.rerender(
        <TestWrapper>
          <ChatWindow {...props} defaultsReady={change.defaultsReady} labels={{ operation: "config_op_v2" }} />
        </TestWrapper>,
      )
      await act(async () => { resolvePieces(pieces) })

      expect(await screen.findByText(/Runtime or default labels changed while preparing this message/)).toBeInTheDocument()
      expect(input).toHaveValue('prepared draft')
      expect(mockedAttacksApi.createAttack).not.toHaveBeenCalled()
      expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled()

      runtime.mockReturnValue({ ready: true, state: "ready", generation: "gen-2" })
      rendered.rerender(
        <TestWrapper>
          <ChatWindow {...props} defaultsReady={true} labels={{ operation: "config_op_v2" }} />
        </TestWrapper>,
      )
      await user.click(screen.getByRole('button', { name: 'Send message' }))
      await waitFor(() => expect(mockedAttacksApi.submitMessageSend).toHaveBeenCalledTimes(1))
      expect(mockedAttacksApi.createAttack).toHaveBeenCalledWith(expect.objectContaining({
        labels: { operation: "config_op_v2" },
      }))
      expect(mockedMapper.buildMessagePieces).toHaveBeenLastCalledWith(
        'prepared draft', expect.arrayContaining([expect.objectContaining({ file })]), undefined,
      )
    })

    it('blocks copying to a new attack while defaults load and uses refreshed labels when creation resumes', async () => {
      const user = userEvent.setup()
      mockedAttacksApi.getMessages.mockResolvedValue(copiedSource)
      mockedAttacksApi.saveConversation.mockResolvedValue(reply)
      mockedAttacksApi.saveConversation.mockClear()
      mockedMapper.backendMessagesToFrontend.mockReturnValue([{ role: "assistant", content: "Saved reply" }])
      const props = {
        ...defaultProps,
        attackResultId: "existing-attack",
        conversationId: "conv-1",
        activeConversationId: "conv-1",
      }
      const rendered = render(<TestWrapper><ChatWindow {...props} defaultsReady={false} /></TestWrapper>)
      await user.click(await screen.findByRole('button', { name: 'Copy or Branch' }))
      const create = screen.getByRole('menuitem', { name: 'Branch attack', exact: true })
      expect(create).toHaveAttribute('aria-disabled', 'true')
      expect(mockedAttacksApi.saveConversation).not.toHaveBeenCalled()

      rendered.rerender(
        <TestWrapper>
          <ChatWindow {...props} defaultsReady={true} labels={{ ...defaultProps.labels, operation: "config_op_v2" }} />
        </TestWrapper>,
      )
      expect(create).not.toHaveAttribute('aria-disabled', 'true')
      await user.click(create)
      await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
        destination: 'new_attack',
        source_conversation_id: "conv-1",
        labels: { ...defaultProps.labels, operation: "config_op_v2" },
      })))
      expect(mockedAttacksApi.submitMessageSend).not.toHaveBeenCalled()
    })

    it('rejects a new-attack copy if the runtime changes while source messages are loading', async () => {
      const user = userEvent.setup()
      const runtime = jest.spyOn(runtimeHooks, "useRuntime").mockReturnValue({
        ready: true, state: "ready", generation: "gen-1",
      })
      mockedAttacksApi.getMessages.mockResolvedValue(copiedSource)
      mockedAttacksApi.saveConversation.mockResolvedValue(reply)
      mockedAttacksApi.saveConversation.mockClear()
      mockedMapper.backendMessagesToFrontend.mockReturnValue([{ role: "assistant", content: "Saved reply" }])
      const props = {
        ...defaultProps,
        attackResultId: "existing-attack", conversationId: "conv-1", activeConversationId: "conv-1",
      }
      const rendered = render(<TestWrapper><ChatWindow {...props} /></TestWrapper>)
      await user.click(await screen.findByRole('button', { name: 'Copy or Branch' }))
      let finish: (value: typeof copiedSource) => void = () => {}
      mockedAttacksApi.getMessages.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
      await user.click(screen.getByRole('menuitem', { name: 'Branch attack', exact: true }))

      runtime.mockReturnValue({ ready: true, state: "ready", generation: "gen-2" })
      rendered.rerender(
        <TestWrapper>
          <ChatWindow {...props} labels={{ ...defaultProps.labels, operation: "config_op_v2" }} />
        </TestWrapper>,
      )
      await act(async () => { finish(copiedSource) })
      expect(await screen.findByText(/Runtime or default labels changed while loading this conversation/)).toBeInTheDocument()
      expect(mockedAttacksApi.saveConversation).not.toHaveBeenCalled()

      await user.click(screen.getByRole('button', { name: 'Copy or Branch' }))
      await user.click(screen.getByRole('menuitem', { name: 'Branch attack', exact: true }))
      await waitFor(() => expect(mockedAttacksApi.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
        destination: 'new_attack', labels: { ...defaultProps.labels, operation: "config_op_v2" },
      })))
    })
  })

  describe('converter restoration warning', () => {
    it('does not show converter restoration warning on fresh empty chat with no conversation', async () => {
      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            activeTarget={mockTarget}
            attackResultId={null}
            conversationId={null}
            activeConversationId={null}
            defaultsReady={true}
          />
        </TestWrapper>,
      )
      expect(screen.queryByText(/Converter choices could not be restored/)).not.toBeInTheDocument()
      expect(screen.getByText('There are no messages in this conversation yet.')).toBeInTheDocument()
    })

    it('does not show converter restoration warning while fresh chat is loading without active target', async () => {
      render(
        <TestWrapper>
          <ChatWindow
            {...defaultProps}
            activeTarget={null}
            attackResultId={null}
            conversationId={null}
            activeConversationId={null}
            defaultsReady={false}
          />
        </TestWrapper>,
      )
      expect(screen.queryByText(/Converter choices could not be restored/)).not.toBeInTheDocument()
    })
  })
})

import { act, render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { makeTarget } from "@/test-utils/targetFixtures";
import type { Parameter, TargetTypeEntry, TargetTypeListResponse } from "@/types";
import CreateTargetDialog from "./CreateTargetDialog";
import { parseWeight, MAX_WEIGHT } from "./weightValidation";
import { targetsApi } from "@/services/api";

jest.mock("@/services/api", () => ({
  targetsApi: {
    createTarget: jest.fn(),
    listTargetTypes: jest.fn(),
    listTargets: jest.fn(),
  },
}));

const mockedTargetsApi = targetsApi as jest.Mocked<typeof targetsApi>;

const OPENAI_COMMON_PARAMETERS: Parameter[] = [
  {
    name: "model_name",
    type_name: "str",
    required: false,
    default: null,
    description: "The model or deployment name.",
  },
  {
    name: "endpoint",
    type_name: "str",
    required: true,
    default: null,
    description: "The target URL for the OpenAI service.",
  },
  {
    name: "api_key",
    type_name: "str | Callable",
    required: false,
    default: null,
    description: "The API key for accessing the OpenAI service.",
  },
  {
    name: "underlying_model",
    type_name: "str",
    required: false,
    default: null,
    description: "The underlying model name used for identification.",
  },
];

const AZURE_ML_COMMON_PARAMETERS: Parameter[] = [
  {
    name: "endpoint",
    type_name: "str",
    required: true,
    default: null,
    description: "The endpoint URL for the deployed Azure ML model.",
  },
  {
    name: "api_key",
    type_name: "str | Callable",
    required: false,
    default: null,
    description: "The API key for accessing the Azure ML endpoint.",
  },
  {
    name: "model_name",
    type_name: "str",
    required: false,
    default: "",
    description: "The name of the deployed model.",
  },
];

const TARGET_TYPES: TargetTypeListResponse = {
  items: [
    {
      target_type: "GitHubCopilotTarget",
      parameters: [
        {
          name: "model_name",
          type_name: "str",
          required: true,
          default: null,
          description: "Explicit Copilot model ID.",
        },
        {
          name: "github_token",
          type_name: "str",
          required: false,
          default: null,
          sensitive: true,
          description: "Required unless GITHUB_TOKEN is configured for this CoPyRIT instance through its environment or loaded .env/.env.local files.",
        },
        {
          name: "working_directory",
          type_name: "str | Path",
          required: false,
          default: null,
          description: "Existing local directory on the server.",
        },
        {
          name: "retain_session",
          type_name: "bool",
          required: false,
          default: "False",
          description: "Keep Copilot sessions on disk.",
        },
        {
          name: "response_timeout_seconds",
          type_name: "float",
          required: false,
          default: "60.0",
          description: "Response time budget in seconds.",
        },
        {
          name: "max_requests_per_minute",
          type_name: "int",
          required: false,
          default: null,
          description: "Per-send pacing.",
        },
      ],
      supported_auth_modes: ["api_key"],
      description: "Send text requests through the GitHub Copilot SDK.",
    },
    {
      target_type: "AzureMLChatTarget",
      parameters: [
        ...AZURE_ML_COMMON_PARAMETERS,
        {
          name: "max_new_tokens",
          type_name: "int",
          required: false,
          default: "400",
          description: "The maximum number of tokens to generate in the response.",
        },
        {
          name: "temperature",
          type_name: "float",
          required: false,
          default: "1.0",
          description: "The temperature for generating diverse responses.",
        },
        {
          name: "top_p",
          type_name: "float",
          required: false,
          default: "1.0",
          description: "The top-p value for generating diverse responses.",
        },
        {
          name: "repetition_penalty",
          type_name: "float",
          required: false,
          default: "1.0",
          description: "The repetition penalty for generated responses.",
        },
      ],
      supported_auth_modes: ["api_key", "identity"],
      description: "A prompt target for Azure Machine Learning chat endpoints.",
    },
    {
      target_type: "OpenAIChatTarget",
      parameters: [
        {
          name: "temperature",
          type_name: "float",
          required: false,
          default: null,
          description: "Controls the randomness of the response.",
        },
        {
          name: "top_p",
          type_name: "float",
          required: false,
          default: null,
          description: "Controls the diversity of the response.",
        },
        {
          name: "seed",
          type_name: "int",
          required: false,
          default: null,
          description: "Makes a best effort to sample deterministically.",
        },
        {
          name: "extra_body_parameters",
          type_name: "dict[str, typing.Any]",
          required: false,
          default: null,
          description: "Additional parameters to include in the request body.",
        },
        {
          name: "httpx_client_kwargs",
          type_name: "dict[str, typing.Any]",
          required: false,
          default: null,
          description: "Additional parameters for the HTTP client.",
        },
        ...OPENAI_COMMON_PARAMETERS,
      ],
      supported_auth_modes: ["api_key", "identity", "none"],
      description: "Facilitates multimodal (image and text) input and text output generation.",
    },
    {
      target_type: "OpenAICompletionTarget",
      parameters: [
        {
          name: "stop",
          type_name: "list[str]",
          required: false,
          default: null,
          is_list: true,
          description: "Sequences where generation should stop.",
        },
        ...OPENAI_COMMON_PARAMETERS,
      ],
      supported_auth_modes: ["api_key", "identity"],
      description: "A prompt target for OpenAI completion endpoints.",
    },
    {
      target_type: "OpenAIImageTarget",
      parameters: OPENAI_COMMON_PARAMETERS,
      supported_auth_modes: ["api_key", "identity"],
      description: "A target for image generation or editing using OpenAI's image models.",
    },
    {
      target_type: "OpenAIResponseTarget",
      parameters: [
        {
          name: "reasoning_effort",
          type_name: "str",
          required: false,
          default: null,
          choices: ["none", "minimal", "low", "medium", "high", "xhigh"],
          description: "Controls how much reasoning the model performs.",
        },
        {
          name: "fail_on_missing_function",
          type_name: "bool",
          required: false,
          default: "False",
          description: "Raise when the response calls an unknown function.",
        },
        {
          name: "custom_functions",
          type_name: "dict[str, collections.abc.Callable]",
          required: false,
          default: null,
          description: "Mapping of user-defined function names.",
        },
        ...OPENAI_COMMON_PARAMETERS,
      ],
      supported_auth_modes: ["api_key", "identity"],
      description: "Enables communication with endpoints that support the OpenAI Response API.",
    },
    {
      target_type: "OpenAITTSTarget",
      parameters: OPENAI_COMMON_PARAMETERS,
      supported_auth_modes: ["api_key", "identity"],
      description: "A prompt target for OpenAI Text-to-Speech (TTS) endpoints.",
    },
    {
      target_type: "OpenAIVideoTarget",
      parameters: OPENAI_COMMON_PARAMETERS,
      supported_auth_modes: ["api_key", "identity"],
      description: "OpenAI Video Target using the OpenAI SDK for video generation.",
    },
    {
      target_type: "RoundRobinTarget",
      parameters: [],
      supported_auth_modes: ["api_key"],
      description: "A prompt target that distributes requests across multiple inner targets using weighted round-robin selection.",
    },
    {
      target_type: "TextTarget",
      parameters: [],
      supported_auth_modes: [],
      description: "Writes prompts to a local text stream.",
    },
    {
      target_type: "HTTPTarget",
      parameters: [
        {
          name: "http_request",
          type_name: "str",
          required: true,
          default: null,
          multiline: true,
          description: "The HTTP request template containing the prompt placeholder.",
        },
        {
          name: "use_tls",
          type_name: "bool",
          required: false,
          default: "True",
          description: "Whether to use TLS.",
        },
      ],
      supported_auth_modes: ["api_key"],
      description: "Sends prompts through a raw HTTP request template.",
    },
    {
      target_type: "AzureBlobStorageTarget",
      parameters: [
        {
          name: "storage_url",
          type_name: "str",
          required: false,
          default: null,
          description: "The Azure Blob Storage service URL.",
        },
        {
          name: "sas_token",
          type_name: "str",
          required: false,
          default: null,
          sensitive: true,
          identity_conflicting: true,
          description: "The optional shared access signature.",
        },
      ],
      supported_auth_modes: ["api_key", "identity"],
      description: "Stores prompts and responses in Azure Blob Storage.",
    },
    {
      target_type: "PlaywrightTarget",
      parameters: [
        {
          name: "interaction_func",
          type_name: "InteractionFunction",
          required: true,
          default: null,
          description: "The Python function used to interact with the page.",
        },
      ],
      supported_auth_modes: ["api_key"],
      description: "Uses Playwright to interact with a web UI.",
    },
  ],
};

const TARGET_DISPLAY_NAMES: Record<string, string> = {
  AzureMLChatTarget: "Azure Machine Learning chat",
  GitHubCopilotTarget: "GitHub Copilot",
  OpenAIChatTarget: "OpenAI chat",
  OpenAICompletionTarget: "OpenAI text completion",
  OpenAIImageTarget: "OpenAI image",
  OpenAIResponseTarget: "OpenAI Responses API",
  OpenAITTSTarget: "OpenAI text to speech",
  OpenAIVideoTarget: "OpenAI video",
  RoundRobinTarget: "Weighted round robin",
  HTTPTarget: "HTTPTarget",
  TextTarget: "TextTarget",
  AzureBlobStorageTarget: "AzureBlobStorageTarget",
  A2ATarget: "A2ATarget",
};

// The primary action reads Save Target, or Create as Temporary when the target can't be saved.
const SUBMIT_BUTTON_NAME = /^(Save Target|Create as Temporary|Creating\.\.\.)$/;

function getSubmitButton(): HTMLElement {
  return screen.getByRole("button", { name: SUBMIT_BUTTON_NAME });
}

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <FluentProvider theme={webLightTheme}>{children}</FluentProvider>;

// Fluent's Dropdown renders its listbox in a portal guarded by a focus
// modalizer. Under jsdom the popover only toggles via a direct click event,
// and the `aria-hidden` the modalizer puts on the dialog while the listbox is
// open is not reliably restored once it closes, which would hide the rest of
// the form from role-based queries. Both behaviors are jsdom artifacts — real
// pointer and keyboard interaction is covered by e2e/config.spec.ts.
async function openTargetTypePicker(): Promise<HTMLElement> {
  const picker = screen.getByRole("combobox", { name: /target type/i });
  await waitFor(() => {
    expect(picker).toBeEnabled();
  });
  fireEvent.click(picker);
  await screen.findByRole("listbox");
  return picker;
}

// Drops the leftover `aria-hidden` from the dialog and everything above it.
// It is a no-op while a listbox is open, so the modalizer keeps its real
// behavior and only the missing restore is compensated for.
function restoreDialogAccessibility(): void {
  const dialog = document.querySelector('[role="dialog"]');
  if (!dialog) return;
  if (document.querySelector('[role="listbox"]')) return;
  const hiddenAncestors = document.querySelectorAll('[aria-hidden="true"]');
  for (const element of Array.from(hiddenAncestors)) {
    if (element === dialog || element.contains(dialog)) {
      element.removeAttribute("aria-hidden");
    }
  }
}

// The modalizer can re-apply `aria-hidden` well after the listbox closed —
// on an unrelated focus change, for instance — so a one-shot cleanup leaves
// every later `*ByRole` query racing against it. Re-run the cleanup whenever
// the attribute reappears instead.
function watchDialogAccessibility(): MutationObserver {
  const observer = new MutationObserver(restoreDialogAccessibility);
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["aria-hidden"],
  });
  return observer;
}

async function selectTargetType(value: string): Promise<void> {
  await openTargetTypePicker();
  fireEvent.click(
    screen.getByRole("option", {
      name: new RegExp(`Implementation: ${value}`),
    }),
  );
  await waitFor(() => {
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
  await waitFor(() => {
    restoreDialogAccessibility();
    expect(screen.getByRole("combobox", { name: /target type/i })).toHaveTextContent(
      TARGET_DISPLAY_NAMES[value] ?? value
    );
  });
}

// The target type fetch mock (see beforeEach) resolves on mount, and its
// setTargetTypeEntries/setTypeMetadataStatus updates land on the next microtask
// tick. Tests that follow up with an `await` (selectTargetType,
// openTargetTypePicker, userEvent, ...) give React a chance to settle that
// update inside their own act()-wrapped waiting. Tests that only make
// synchronous assertions after `render` never yield, so the update fires
// after the test body returns and React reports it as outside act(...).
// Call this right after `render` in those synchronous tests to flush it
// deterministically.
async function flushTargetTypesFetch(): Promise<void> {
  await act(async () => {});
}

describe("parseWeight", () => {
  it("rejects empty input", () => {
    expect(parseWeight("")).toEqual({ ok: false, error: "Weight is required" });
  });

  it("rejects decimals like '2.5' (parseInt would silently truncate to 2)", () => {
    expect(parseWeight("2.5")).toEqual({
      ok: false,
      error: "Weight must be a whole number",
    });
  });

  it("rejects scientific notation like '1e10' (parseInt would silently return 1)", () => {
    expect(parseWeight("1e10")).toEqual({
      ok: false,
      error: "Weight must be a whole number",
    });
  });

  it("rejects negatives", () => {
    expect(parseWeight("-3")).toEqual({
      ok: false,
      error: "Weight must be a whole number",
    });
  });

  it("rejects whitespace and trailing characters", () => {
    expect(parseWeight(" 5")).toEqual({
      ok: false,
      error: "Weight must be a whole number",
    });
    expect(parseWeight("5x")).toEqual({
      ok: false,
      error: "Weight must be a whole number",
    });
  });

  it("rejects 0 with a 'must be at least 1' error (no silent revert)", () => {
    expect(parseWeight("0")).toEqual({
      ok: false,
      error: "Weight must be at least 1",
    });
  });

  it(`rejects values above MAX_WEIGHT (${MAX_WEIGHT})`, () => {
    expect(parseWeight(String(MAX_WEIGHT + 1))).toEqual({
      ok: false,
      error: `Weight must be at most ${MAX_WEIGHT}`,
    });
    expect(parseWeight("99999999999")).toEqual({
      ok: false,
      error: `Weight must be at most ${MAX_WEIGHT}`,
    });
  });

  it("accepts boundary values 1 and MAX_WEIGHT", () => {
    expect(parseWeight("1")).toEqual({ ok: true, value: 1 });
    expect(parseWeight(String(MAX_WEIGHT))).toEqual({ ok: true, value: MAX_WEIGHT });
  });

  it("accepts typical integer weights", () => {
    expect(parseWeight("7")).toEqual({ ok: true, value: 7 });
    expect(parseWeight("42")).toEqual({ ok: true, value: 42 });
  });
});

describe("CreateTargetDialog", () => {
  it("explains explicit manual model entry instead of promising environment fallback", async () => {
    mockedTargetsApi.listTargetTypes.mockResolvedValue({
      items: [{ target_type: "OpenAIChatTarget", supported_auth_modes: ["api_key"],
        parameters: OPENAI_COMMON_PARAMETERS.map((parameter) =>
        parameter.name === "model_name" ? {
          ...parameter, required: true, description: "If None, uses OPENAI_CHAT_MODEL from the environment.",
        } : parameter) }],
    });
    render(<TestWrapper><CreateTargetDialog {...defaultProps} /></TestWrapper>);
    await selectTargetType("OpenAIChatTarget");
    expect(await screen.findByText(/Enter the model or deployment name explicitly/)).toBeVisible();
    expect(screen.queryByText(/If None, uses OPENAI_CHAT_MODEL/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: SUBMIT_BUTTON_NAME })).toBeDisabled();
    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  });

  const defaultProps = {
    open: true,
    onClose: jest.fn(),
    onCreated: jest.fn(),
  };

  let dialogAccessibilityObserver: MutationObserver;

  beforeEach(() => {
    dialogAccessibilityObserver = watchDialogAccessibility();
    jest.clearAllMocks();
    mockedTargetsApi.listTargetTypes.mockResolvedValue(TARGET_TYPES);
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [],
      pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
    } as unknown as Awaited<ReturnType<typeof mockedTargetsApi.listTargets>>);
  });

  afterEach(() => {
    dialogAccessibilityObserver.disconnect();
  });

  it("should render dialog when open", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );
    await flushTargetTypesFetch();

    expect(screen.getByText("Create New Target")).toBeInTheDocument();
    expect(getSubmitButton()).toBeInTheDocument();
    expect(screen.getByText("Cancel")).toBeInTheDocument();
  });

  it("should show friendly names, registry descriptions, implementation identifiers, and auth for all target types", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await openTargetTypePicker();

    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(11);
    for (const entry of TARGET_TYPES.items.filter((item) => !["PlaywrightTarget", "TextTarget"].includes(item.target_type))) {
      const option = screen.getByRole("option", {
        name: new RegExp(`Implementation: ${entry.target_type}`),
      });
      expect(
        within(option).getAllByText(TARGET_DISPLAY_NAMES[entry.target_type]).length,
      ).toBeGreaterThan(0);
      expect(within(option).getByText(entry.target_type, { selector: "code" })).toBeInTheDocument();
      expect(within(option).getByText(entry.description ?? "")).toBeInTheDocument();
    }

    expect(within(screen.getByRole("option", {
      name: /Implementation: OpenAIChatTarget/,
    })).getByText(/API key or Microsoft Entra ID/)).toBeInTheDocument();
    expect(within(screen.getByRole("option", {
      name: /Implementation: RoundRobinTarget/,
    })).getByText("Supported authentication: API key")).toBeInTheDocument();
    expect(screen.queryByRole("option", {
      name: /Implementation: PlaywrightTarget/,
    })).not.toBeInTheDocument();
  });

  it("should keep guidance for the selected target visible after the list closes", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");

    expect(screen.getByRole("combobox", { name: /target type/i })).toHaveTextContent(
      "Weighted round robin",
    );
    const selectedDetails = screen.getByRole("region", { name: /selected target details/i });
    expect(selectedDetails).toHaveTextContent("RoundRobinTarget");
    expect(selectedDetails).toHaveTextContent("weighted round-robin selection");
    expect(selectedDetails).toHaveTextContent("Supported authentication: API key");
  });

  it("should not offer the SDK-only TextTarget", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await openTargetTypePicker();

    expect(screen.queryByRole("option", { name: /Implementation: TextTarget/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Writes prompts to a local text stream.")).not.toBeInTheDocument();
  });

  it("should still describe other credential-free target types", async () => {
    mockedTargetsApi.listTargetTypes.mockResolvedValue({
      items: [...TARGET_TYPES.items, {
        target_type: "CredentialFreeTarget",
        parameters: [],
        supported_auth_modes: [],
        description: "A credential-free endpoint.",
      }],
    });
    render(<TestWrapper><CreateTargetDialog {...defaultProps} /></TestWrapper>);
    await openTargetTypePicker();
    const option = screen.getByRole("option", { name: /Implementation: CredentialFreeTarget/ });
    expect(option).toHaveTextContent("No authentication required");
    expect(option).not.toHaveTextContent("API key");
  });

  it("should not offer SDK-only inner targets in a round-robin target", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} existingTargets={[
          makeTarget({ target_registry_name: "demo", target_type: "TextTarget" }),
          makeTarget({ target_registry_name: "endpoint", target_type: "OpenAIChatTarget" }),
        ]} />
      </TestWrapper>
    );
    await selectTargetType("RoundRobinTarget");
    expect(screen.queryByRole("option", { name: /demo/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: /endpoint/ })).toBeInTheDocument();
  });

  it("should keep the selected target displayed after focus moves to another field", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    await user.click(
      screen.getByPlaceholderText("https://your-resource.openai.azure.com/"),
    );
    await user.keyboard("https://api.openai.com");
    restoreDialogAccessibility();

    const picker = screen.getByRole("combobox", { name: /target type/i });
    expect(picker).toHaveTextContent("OpenAI chat");
    expect(picker).not.toHaveTextContent("Select a target type");
  });

  it("should expose fallback target choices while type metadata is loading", async () => {
    mockedTargetsApi.listTargetTypes.mockReturnValue(
      new Promise<TargetTypeListResponse>(() => {}),
    );

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Loading target details...")).toBeInTheDocument();
    await openTargetTypePicker();
    expect(screen.getAllByRole("option")).toHaveLength(9);
  });

  it("should preserve a fallback selection when type metadata arrives", async () => {
    let resolveTargetTypes: ((targetTypes: TargetTypeListResponse) => void) | null = null;
    mockedTargetsApi.listTargetTypes.mockReturnValue(
      new Promise<TargetTypeListResponse>((resolve) => {
        resolveTargetTypes = resolve;
      }),
    );

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    expect(screen.getByRole("combobox", { name: /target type/i })).toHaveTextContent("OpenAI chat");

    if (resolveTargetTypes === null) {
      throw new Error("Target type resolver was not initialized");
    }
    await act(async () => {
      resolveTargetTypes(TARGET_TYPES);
    });

    await waitFor(() => {
      expect(screen.queryByText("Loading target details...")).not.toBeInTheDocument();
    });
    expect(screen.getByRole("combobox", { name: /target type/i })).toHaveTextContent("OpenAI chat");
  });

  it("should keep all target types selectable and explain when type metadata fails to load", async () => {
    mockedTargetsApi.listTargetTypes.mockRejectedValueOnce(new Error("registry unavailable"));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    expect(await screen.findByText(/Target details could not be loaded/)).toBeInTheDocument();
    await openTargetTypePicker();
    expect(screen.getAllByRole("option")).toHaveLength(9);
    expect(screen.getByRole("option", {
      name: /Azure Machine Learning chat.*Implementation: AzureMLChatTarget/,
    })).toBeInTheDocument();
  });

  it("should not render when closed", () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} open={false} />
      </TestWrapper>
    );

    expect(screen.queryByText("Create New Target")).not.toBeInTheDocument();
  });

  it("should have Create button disabled until type and endpoint filled", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );
    await flushTargetTypesFetch();

    const createButton = getSubmitButton();
    expect(createButton.closest("button")).toBeDisabled();
  });

  it("should hide the Authentication field until a target type is selected", async () => {

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    // No type is chosen yet, so neither authentication nor API-key controls are visible.
    expect(
      screen.queryByRole("radio", { name: /Identity-based/ })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText("API key (stored in memory only)")
    ).not.toBeInTheDocument();

    // Selecting an identity-capable type should reveal the Authentication field.
    await selectTargetType("OpenAIChatTarget");
    expect(
      screen.getByRole("radio", { name: /Identity-based/ })
    ).toBeInTheDocument();
  });

  it("should call onClose when Cancel is clicked", async () => {
    const onClose = jest.fn();
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onClose={onClose} />
      </TestWrapper>
    );

    await user.click(screen.getByText("Cancel"));

    expect(onClose).toHaveBeenCalled();
  });

  it("should create target and call onCreated on successful submit", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_chat_new",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
      </TestWrapper>
    );

    // Select target type
    await selectTargetType("OpenAIChatTarget");

    // Fill the endpoint & model names
    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.openai.com" } });

    const modelInput = screen.getByPlaceholderText("e.g. gpt-4o, my-deployment");
    fireEvent.change(modelInput, { target: { value: "gpt-4" } });

    // Submit
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_chat_gpt_4",
        persist: true,
        type: "OpenAIChatTarget",
        params: {
          endpoint: "https://api.openai.com",
          model_name: "gpt-4",
        },
      });
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("should send underlying_model when toggle is enabled", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "azure_deployment",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
      </TestWrapper>
    );

    // Select target type
    await selectTargetType("OpenAIChatTarget");

    // Fill endpoint & model names
    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.azure.com" } });

    const modelInput = screen.getByPlaceholderText("e.g. gpt-4o, my-deployment");
    fireEvent.change(modelInput, { target: { value: "my-gpt4o-deployment" } });

    // Toggle underlying model switch
    await user.click(screen.getByRole("switch"));

    // Fill underlying model
    const underlyingInput = screen.getByPlaceholderText("e.g. gpt-4o-2024-08-06");
    fireEvent.change(underlyingInput, { target: { value: "gpt-4o" } });

    // Submit
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_chat_my_gpt4o_deployment",
        persist: true,
        type: "OpenAIChatTarget",
        params: {
          endpoint: "https://api.azure.com",
          model_name: "my-gpt4o-deployment",
          underlying_model: "gpt-4o",
        },
      });
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("should not send underlying_model when toggle is off", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "simple_target",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.openai.com" } });

    const modelInput = screen.getByPlaceholderText("e.g. gpt-4o, my-deployment");
    fireEvent.change(modelInput, { target: { value: "gpt-4o" } });

    // Do NOT toggle the underlying model switch

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_chat_gpt_4o",
        persist: true,
        type: "OpenAIChatTarget",
        params: {
          endpoint: "https://api.openai.com",
          model_name: "gpt-4o",
        },
      });
    });
  });

  it("should show error when createTarget fails", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockRejectedValue(
      new Error("Invalid API key")
    );

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    // Select target type
    await selectTargetType("OpenAIChatTarget");

    // Fill endpoint
    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://example.com" } });

    // Submit
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(screen.getByText("Invalid API key")).toBeInTheDocument();
    });
  });

  it("should include API key in params when provided", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_chat_keyed",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    // Select target type
    await selectTargetType("OpenAIChatTarget");

    // Fill endpoint — use fireEvent.change because userEvent.type truncates
    // URLs containing periods in FluentUI Input under jsdom.
    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.openai.com" } });

    // Fill API key — use fireEvent.change for the same reason as endpoint input.
    fireEvent.change(screen.getByPlaceholderText("API key (stored in memory only)"), {
      target: { value: "sk-test-key-123" },
    });

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith(
        expect.objectContaining({
          params: expect.objectContaining({
            api_key: "sk-test-key-123",
          }),
        })
      );
    });
  });

  it("should display supported target initializer guidance", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );
    await flushTargetTypesFetch();

    expect(screen.getByText("target", { selector: "code" })).toBeInTheDocument();
    expect(
      screen.getByText(/registers available prompt targets from endpoints/i)
    ).toBeInTheDocument();
    expect(
      screen.queryByText("airt", { selector: "code" })
    ).not.toBeInTheDocument();
  });

  it("should render .pyrit_conf_example as an accessible link", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );
    await flushTargetTypesFetch();

    const link = screen.getByRole("link", { name: ".pyrit_conf_example" });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/microsoft/PyRIT/blob/main/.pyrit_conf_example"
    );
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("preserves an optional endpoint default for a locally implemented target", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.listTargetTypes.mockResolvedValue({
      items: [{
        target_type: "LocalTarget",
        parameters: [{ name: "endpoint", type_name: "str", required: false, default: "" }],
        supported_auth_modes: ["api_key"],
      }],
    });
    render(<TestWrapper><CreateTargetDialog {...defaultProps} /></TestWrapper>);
    await selectTargetType("LocalTarget");
    const create = screen.getByRole("button", { name: SUBMIT_BUTTON_NAME });
    expect(create).toBeEnabled();
    await user.click(create);
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith(
      expect.objectContaining({ type: "LocalTarget", params: {} }),
    );
  });

  it("should require an endpoint when identity authentication is selected", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByRole("radio", { name: /Identity-based/ }));

    // Submit via form (bypass disabled button by submitting the form directly)
    const form = screen.getByText("Create New Target").closest("form") ??
      document.querySelector("form");
    if (form) {
      fireEvent.submit(form);
    }

    await waitFor(() => {
      expect(screen.getByText("Please provide an endpoint URL")).toBeInTheDocument();
    });
  });

  it("should surface string throws verbatim via toApiError", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockRejectedValue("string error");

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://example.com" } });

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(screen.getByText("string error")).toBeInTheDocument();
    });
  });

  it("should omit untouched AzureML defaults so the constructor owns default behavior", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "azure_ml_llama",
      target_type: "AzureMLChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
      </TestWrapper>
    );

    // Select AzureMLChatTarget type
    await selectTargetType("AzureMLChatTarget");

    // Fill endpoint
    const endpointInput = screen.getByPlaceholderText(
      "https://your-model.region.inference.ml.azure.com/score"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://my-llama.eastus.inference.ml.azure.com/score" },
    });

    // Fill model name
    const modelInput = screen.getByPlaceholderText("e.g. Llama-3.2-3B-Instruct");
    fireEvent.change(modelInput, { target: { value: "Llama-3.2-3B-Instruct" } });

    // Submit without overriding the displayed constructor defaults.
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "azure_machine_learning_chat_llama_3_2_3b_instruct",
        persist: true,
        type: "AzureMLChatTarget",
        params: {
          endpoint: "https://my-llama.eastus.inference.ml.azure.com/score",
          model_name: "Llama-3.2-3B-Instruct",
        },
      });
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("should show AzureML metadata fields without unrelated OpenAI fields", async () => {

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("AzureMLChatTarget");

    // AzureML-specific fields should be visible
    expect(screen.getByText("Max New Tokens")).toBeInTheDocument();
    expect(screen.getByText("Temperature")).toBeInTheDocument();
    expect(screen.getByText("Top P")).toBeInTheDocument();
    expect(screen.getByText("Repetition Penalty")).toBeInTheDocument();

    // Azure ML does not declare an underlying-model constructor parameter.
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("should show constructor defaults as editable guidance without setting the values", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("AzureMLChatTarget");
    await user.click(screen.getByText("Advanced settings"));

    const maxNewTokens = screen.getByRole("spinbutton", { name: "Max New Tokens" });
    expect(maxNewTokens).toHaveValue(null);
    expect(maxNewTokens).toHaveAttribute("placeholder", "Defaults to 400");
    expect(maxNewTokens).toHaveAccessibleDescription(/maximum number of tokens.*Defaults to 400\./i);
    expect(maxNewTokens).toBeVisible();

    const repetitionPenalty = screen.getByRole("spinbutton", { name: "Repetition Penalty" });
    expect(repetitionPenalty).toHaveValue(null);
    expect(repetitionPenalty).toHaveAttribute("placeholder", "Defaults to 1.0");
  });

  it("should send custom AzureML params when fields are modified", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "azure_ml_custom",
      target_type: "AzureMLChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
      </TestWrapper>
    );

    await selectTargetType("AzureMLChatTarget");

    // Fill endpoint — use fireEvent.change because userEvent.type truncates
    // URLs containing periods in FluentUI Input under jsdom.
    const endpointInput = screen.getByPlaceholderText(
      "https://your-model.region.inference.ml.azure.com/score"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://my-model.eastus.inference.ml.azure.com/score" },
    });

    // Modify AzureML-specific fields to non-default values via input roles.
    // Use fireEvent.change for the same jsdom/FluentUI reason as endpoint input.
    fireEvent.change(screen.getByRole("spinbutton", { name: "Max New Tokens" }), {
      target: { value: "512" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Temperature" }), {
      target: { value: "0.7" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Top P" }), {
      target: { value: "0.9" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Repetition Penalty" }), {
      target: { value: "1.2" },
    });

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "azure_machine_learning_chat",
        persist: true,
        type: "AzureMLChatTarget",
        params: {
          endpoint: "https://my-model.eastus.inference.ml.azure.com/score",
          max_new_tokens: 512,
          temperature: 0.7,
          top_p: 0.9,
          repetition_penalty: 1.2,
        },
      });
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("should render and submit OpenAI numeric parameters from target metadata", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_chat_custom",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByText("Advanced settings"));

    fireEvent.change(screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), {
      target: { value: "https://api.openai.com" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Temperature" }), {
      target: { value: "0" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Seed" }), {
      target: { value: "42" },
    });

    expect(screen.getByRole("textbox", { name: "Extra Body Parameters" })).toBeVisible();

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_chat",
        persist: true,
        type: "OpenAIChatTarget",
        params: {
          endpoint: "https://api.openai.com",
          temperature: 0,
          seed: 42,
        },
      });
    });
  });

  it("should submit the catalog video duration as a number", async () => {
    const user = userEvent.setup();
    const videoParameters: Parameter[] = [
      {
        name: "resolution_dimensions",
        type_name: "str",
        is_list: false,
        choices: ["720x1280", "1280x720", "1024x1792", "1792x1024"],
        default: "1280x720",
        required: false,
      },
      { name: "n_seconds", type_name: "int", is_list: false, choices: null, default: "4", required: false },
      { name: "model_name", type_name: "str", is_list: false, choices: null, default: null, required: true },
      { name: "endpoint", type_name: "str", is_list: false, choices: null, default: null, required: true },
      { name: "api_key", type_name: "str", is_list: false, choices: null, default: null, required: false },
      { name: "underlying_model", type_name: "str", is_list: false, choices: null, default: null, required: false },
    ];
    mockedTargetsApi.listTargetTypes.mockResolvedValue({
      items: TARGET_TYPES.items.map((item) => (
        item.target_type === "OpenAIVideoTarget" ? { ...item, parameters: videoParameters } : item
      )),
    });
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_video_custom",
      target_type: "OpenAIVideoTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIVideoTarget");
    await user.click(screen.getByText("Advanced settings"));

    fireEvent.change(screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), {
      target: { value: "https://api.openai.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("e.g. gpt-4o, my-deployment"), {
      target: { value: "sora-2" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: "N Seconds" }), {
      target: { value: "8" },
    });

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_video_sora_2",
        persist: true,
        type: "OpenAIVideoTarget",
        params: {
          endpoint: "https://api.openai.com",
          model_name: "sora-2",
          n_seconds: 8,
        },
      });
    });
  });

  it("should render and submit metadata choices and booleans", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_response_custom",
      target_type: "OpenAIResponseTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIResponseTarget");
    await user.click(screen.getByText("Advanced settings"));

    fireEvent.change(screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), {
      target: { value: "https://api.openai.com" },
    });
    await user.selectOptions(screen.getByRole("combobox", { name: "Reasoning Effort" }), "high");
    const failOnMissingFunction = screen.getByRole("combobox", { name: "Fail On Missing Function" });
    expect(within(failOnMissingFunction).getByRole("option", {
      name: "Use default (False)",
    })).toBeInTheDocument();
    await user.selectOptions(failOnMissingFunction, "false");

    expect(screen.queryByRole("textbox", { name: "Custom Functions" })).not.toBeInTheDocument();

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_responses_api",
        persist: true,
        type: "OpenAIResponseTarget",
        params: {
          endpoint: "https://api.openai.com",
          reasoning_effort: "high",
          fail_on_missing_function: false,
        },
      });
    });
  });

  it("should render and submit scalar list parameters from target metadata", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_completion_custom",
      target_type: "OpenAICompletionTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAICompletionTarget");
    await user.click(screen.getByText("Advanced settings"));

    fireEvent.change(screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), {
      target: { value: "https://api.openai.com" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Stop" }), {
      target: { value: "END, DONE" },
    });

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_text_completion",
        persist: true,
        type: "OpenAICompletionTarget",
        params: {
          endpoint: "https://api.openai.com",
          stop: ["END", "DONE"],
        },
      });
    });
  });

  it("should preserve an explicitly selected empty list", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_completion_empty_stop",
      target_type: "OpenAICompletionTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAICompletionTarget");
    fireEvent.change(screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), {
      target: { value: "https://api.openai.com" },
    });
    await user.click(screen.getByText("Advanced settings"));
    await user.click(screen.getByRole("checkbox", { name: "Use empty list for stop" }));
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_text_completion",
        persist: true,
        type: "OpenAICompletionTarget",
        params: {
          endpoint: "https://api.openai.com",
          stop: [],
        },
      });
    });
  });

  it("should parse and submit JSON-object target parameters", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_chat_json",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    fireEvent.change(screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), {
      target: { value: "https://api.openai.com" },
    });
    await user.click(screen.getByText("Advanced settings"));

    fireEvent.change(screen.getByRole("textbox", { name: "Extra Body Parameters" }), {
      target: { value: '{"reasoning":{"effort":"high"},"include":["usage"]}' },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Httpx Client Kwargs" }), {
      target: { value: '{"timeout":180}' },
    });

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_chat",
        persist: true,
        type: "OpenAIChatTarget",
        params: {
          endpoint: "https://api.openai.com",
          extra_body_parameters: {
            reasoning: { effort: "high" },
            include: ["usage"],
          },
          httpx_client_kwargs: { timeout: 180 },
        },
      });
    });
  });

  it("should reject malformed or non-object JSON target parameters", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByText("Advanced settings"));

    const extraBody = screen.getByRole("textbox", { name: "Extra Body Parameters" });
    fireEvent.change(extraBody, {
      target: { value: '{"reasoning":' },
    });
    expect(screen.getByText("Extra Body Parameters must contain valid JSON.")).toBeVisible();

    fireEvent.change(extraBody, {
      target: { value: '["reasoning"]' },
    });
    expect(screen.getByText("Extra Body Parameters must be a JSON object.")).toBeVisible();

    await user.click(getSubmitButton());

    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  });

  it("should keep optional parameters in expandable advanced settings", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIResponseTarget");

    const summary = screen.getByText("Advanced settings");
    const advancedSettings = summary.closest("details");
    expect(advancedSettings).not.toHaveAttribute("open");
    expect(screen.getByLabelText("Reasoning Effort", { selector: "select" })).not.toBeVisible();

    await user.click(summary);

    expect(advancedSettings).toHaveAttribute("open");
    expect(screen.getByRole("combobox", { name: "Reasoning Effort" })).toBeVisible();
    expect(screen.getByText(/Python callables cannot be configured in CopyRIT/)).toBeVisible();
  });

  it("should create a metadata-supported target without a hardcoded form shape", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "raw_http_target",
      target_type: "HTTPTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("HTTPTarget");

    expect(screen.queryByLabelText("Endpoint URL")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("API key (stored in memory only)")).not.toBeInTheDocument();
    expect(getSubmitButton().closest("button")).toBeDisabled();

    // A real raw HTTP request template needs actual line breaks between the
    // request line, headers, and body — the backend parser splits on real
    // newlines, not on literal "\r\n" characters. The http_request field
    // must render as a multiline control (Textarea) for this to be entered.
    const httpRequestTemplate = "POST /chat HTTP/1.1\nHost: example.com\nContent-Type: application/json\n\n{\"message\": \"{PROMPT}\"}";
    const httpRequestField = screen.getByRole("textbox", { name: /Http Request/ });
    expect(httpRequestField.tagName).toBe("TEXTAREA");
    fireEvent.change(httpRequestField, {
      target: { value: httpRequestTemplate },
    });

    expect(getSubmitButton().closest("button")).toBeEnabled();
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "http",
        persist: true,
        type: "HTTPTarget",
        params: {
          http_request: httpRequestTemplate,
        },
      });
    });
  });

  it("should allow a blank GitHub token when the instance has GITHUB_TOKEN configured", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "copilot",
      target_type: "GitHubCopilotTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("GitHubCopilotTarget");

    expect(screen.getByRole("region", { name: /selected target details/i }))
      .toHaveTextContent("Supported authentication: GitHub token");
    expect(screen.getByText(/CoPyRIT does not use browser or saved Copilot logins/)).toBeVisible();
    expect(screen.getByText(/Tokens and created targets can be used by anyone with access to this CoPyRIT instance/))
      .toBeVisible();
    expect(screen.getByLabelText("GitHub Token", { selector: "input" })).toHaveAccessibleDescription(
      /Required unless GITHUB_TOKEN is configured for this CoPyRIT instance/,
    );
    expect(screen.getByText(/Model IDs are not validated when creating a target/)).toBeVisible();
    expect(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ })).toHaveAccessibleDescription(
      /Model IDs are not validated.*An unrecognized ID may cause Copilot to use a different model.*Verify the exact model ID is available to the GitHub account associated with this target's token/,
    );
    expect(screen.queryByText(/SDK login discovery/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Endpoint URL/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Underlying model differs/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /Identity-based/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("GitHub Token", { selector: "input" })).toBeVisible();
    expect(screen.getByLabelText("GitHub Token", { selector: "input" })).toHaveAttribute("type", "password");
    expect(getSubmitButton()).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4.1");
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "github_copilot_gpt_4_1",
        persist: true,
        type: "GitHubCopilotTarget",
        params: { model_name: "gpt-4.1" },
      });
    });
    expect(defaultProps.onCreated).toHaveBeenCalledTimes(1);
  });

  it("should show missing instance token guidance and allow retrying with an entered token", async () => {
    const user = userEvent.setup();
    const detail = "GitHub Copilot requires a GitHub token in CoPyRIT. Enter GitHub Token or configure "
      + "GITHUB_TOKEN for this CoPyRIT instance through its environment or loaded .env/.env.local files.";
    mockedTargetsApi.createTarget.mockRejectedValueOnce(Object.assign(new Error("Bad Request"), {
      isAxiosError: true,
      response: { status: 400, data: { detail } },
    }));
    mockedTargetsApi.createTarget.mockResolvedValueOnce(makeTarget({
      target_registry_name: "copilot",
      target_type: "GitHubCopilotTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("GitHubCopilotTarget");
    await user.type(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4.1");
    await user.click(getSubmitButton());

    expect(await screen.findByText(detail)).toBeVisible();
    expect(defaultProps.onCreated).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ })).toHaveValue("gpt-4.1");

    await user.type(screen.getByLabelText("GitHub Token", { selector: "input" }), "test-github-token");
    await user.click(getSubmitButton());

    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenLastCalledWith({
      name: "github_copilot_gpt_4_1",
      persist: false,
      type: "GitHubCopilotTarget",
      params: { model_name: "gpt-4.1", github_token: "test-github-token" },
    });
  });

  it("should submit the GitHub token and typed Copilot advanced settings", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "copilot",
      target_type: "GitHubCopilotTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("GitHubCopilotTarget");
    await user.type(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4.1");
    await user.type(screen.getByLabelText("GitHub Token", { selector: "input" }), "test-github-token");
    await user.click(screen.getByText("Advanced settings"));
    await user.type(screen.getByRole("textbox", { name: "Working Directory" }), "C:\\copilot-workspace");
    await user.selectOptions(screen.getByRole("combobox", { name: "Retain Session" }), "false");
    await user.type(screen.getByRole("spinbutton", { name: "Response Timeout Seconds" }), "120");
    await user.type(screen.getByRole("spinbutton", { name: "Max Requests Per Minute" }), "10");
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "github_copilot_gpt_4_1",
        persist: false,
        type: "GitHubCopilotTarget",
        params: {
          model_name: "gpt-4.1",
          github_token: "test-github-token",
          working_directory: "C:\\copilot-workspace",
          retain_session: false,
          response_timeout_seconds: 120,
          max_requests_per_minute: 10,
        },
      });
    });

    await selectTargetType("GitHubCopilotTarget");
    expect(screen.getByLabelText("GitHub Token", { selector: "input" })).toHaveValue("");
  });

  it.each(["loading", "failed", "empty"] as const)(
    "should create GitHub Copilot without an endpoint when metadata is %s",
    async (metadataStatus) => {
      const user = userEvent.setup();
      if (metadataStatus === "loading") {
        mockedTargetsApi.listTargetTypes.mockReturnValue(new Promise<TargetTypeListResponse>(() => {}));
      } else if (metadataStatus === "failed") {
        mockedTargetsApi.listTargetTypes.mockRejectedValueOnce(new Error("registry unavailable"));
      } else {
        mockedTargetsApi.listTargetTypes.mockResolvedValueOnce({ items: [] });
      }
      mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
        target_registry_name: "copilot",
        target_type: "GitHubCopilotTarget",
      }));

      render(
        <TestWrapper>
          <CreateTargetDialog {...defaultProps} />
        </TestWrapper>
      );

      await selectTargetType("GitHubCopilotTarget");
      expect(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ })).toHaveAccessibleDescription(
        /Model IDs are not validated.*An unrecognized ID may cause Copilot to use a different model/,
      );
      expect(screen.queryByLabelText(/Endpoint URL/)).not.toBeInTheDocument();
      expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();
      expect(screen.queryByRole("radio", { name: /Identity-based/ })).not.toBeInTheDocument();
      expect(getSubmitButton()).toBeDisabled();
      expect(screen.getByLabelText("GitHub Token", { selector: "input" })).toHaveAttribute("type", "password");

      await user.type(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4.1");
      await user.type(screen.getByLabelText("GitHub Token", { selector: "input" }), "test-github-token");
      await user.click(getSubmitButton());

      await waitFor(() => {
        expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
          name: "github_copilot_gpt_4_1",
          persist: false,
          type: "GitHubCopilotTarget",
          params: { model_name: "gpt-4.1", github_token: "test-github-token" },
        });
      });
    },
  );

  it("should clear the GitHub token when switching to another target type", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("GitHubCopilotTarget");
    await user.type(screen.getByLabelText("GitHub Token", { selector: "input" }), "test-github-token");
    await selectTargetType("OpenAIChatTarget");
    expect(screen.queryByText(/Model IDs are not validated when creating a target/)).not.toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: /Endpoint URL/ }), "https://api.openai.com");
    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_chat",
        persist: true,
        type: "OpenAIChatTarget",
        params: { endpoint: "https://api.openai.com" },
      });
    });

    await selectTargetType("GitHubCopilotTarget");
    expect(screen.getByLabelText("GitHub Token", { selector: "input" })).toHaveValue("");
  });

  it("should support identity auth for targets without an endpoint or api_key parameter", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("AzureBlobStorageTarget");

    expect(screen.queryByLabelText("Endpoint URL")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("API key (stored in memory only)")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Identity-based/ })).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /Identity-based/ }));

    expect(getSubmitButton().closest("button")).toBeEnabled();
  });

  it("should mask the sas_token parameter and drop it once identity auth is selected", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "blob_target",
      target_type: "AzureBlobStorageTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("AzureBlobStorageTarget");
    await user.click(screen.getByText("Advanced settings"));

    const sasTokenField = screen.getByLabelText(/Sas Token/, { selector: "input" });
    expect(sasTokenField).toHaveAttribute("type", "password");

    fireEvent.change(sasTokenField, { target: { value: "sv=2024&sig=secret" } });
    expect(sasTokenField).toHaveValue("sv=2024&sig=secret");

    // Selecting identity-based auth must clear and disable sas_token so it
    // can't silently override the chosen auth mode (SAS takes precedence
    // over DefaultAzureCredential on the backend).
    await user.click(screen.getByRole("radio", { name: /Identity-based/ }));

    expect(sasTokenField).toBeDisabled();
    expect(sasTokenField).toHaveValue("");

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "azure_blob_storage",
        persist: true,
        type: "AzureBlobStorageTarget",
        params: {},
        auth_mode: "identity",
      });
    });
  });

  it("should reset form when dialog is closed via onOpenChange", () => {
    const onClose = jest.fn();

    const { rerender } = render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onClose={onClose} />
      </TestWrapper>
    );

    // Simulate Dialog's onOpenChange with open=false
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();

    // Close the dialog by re-rendering with open=false
    rerender(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} open={false} onClose={onClose} />
      </TestWrapper>
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("should hide the API Key field and omit api_key/include auth_mode when identity is selected", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_chat_identity",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://my-resource.openai.azure.com/" },
    });

    // check that API Key field is visible by default.
    expect(
      screen.getByPlaceholderText("API key (stored in memory only)")
    ).toBeInTheDocument();

    // Select identity option.
    await user.click(
      screen.getByRole("radio", {
        name: /Identity-based/,
      })
    );

    // check that API Key field is hidden when identity mode is selected.
    expect(
      screen.queryByPlaceholderText("API key (stored in memory only)")
    ).not.toBeInTheDocument();

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_chat",
        persist: true,
        type: "OpenAIChatTarget",
        params: {
          endpoint: "https://my-resource.openai.azure.com/",
        },
        auth_mode: "identity",
      });
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("should clear a previously-typed API key when switching to identity", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "openai_chat_identity",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://my-resource.openai.azure.com/" },
    });

    // Type a key, then switch to identity option.
    fireEvent.change(
      screen.getByPlaceholderText("API key (stored in memory only)"),
      { target: { value: "sk-typed-before-switch" } }
    );

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    await user.click(getSubmitButton());

    await waitFor(() => {
      const call = mockedTargetsApi.createTarget.mock.calls[0][0];
      expect(call.auth_mode).toBe("identity");
      expect(call.params).not.toHaveProperty("api_key");
    });
  });

  it("should submit no-key auth without an API key", async () => {
    const onCreated = jest.fn();
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({
      target_registry_name: "local_chat",
      target_type: "OpenAIChatTarget",
    }));

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    expect(screen.getByRole("region", { name: /selected target details/i })).toHaveTextContent(
      "Supported authentication: API key or Microsoft Entra ID or No key",
    );
    await user.type(
      screen.getByPlaceholderText("https://your-resource.openai.azure.com/"),
      "http://localhost:11434/v1",
    );
    await user.type(screen.getByPlaceholderText("API key (stored in memory only)"), "sk-typed-before-switch");
    await user.click(screen.getByRole("radio", { name: /No key/ }));

    expect(screen.queryByPlaceholderText("API key (stored in memory only)")).not.toBeInTheDocument();

    await user.click(getSubmitButton());

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        name: "openai_chat",
        persist: true,
        type: "OpenAIChatTarget",
        params: {
          endpoint: "http://localhost:11434/v1",
        },
        auth_mode: "none",
      });
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("should offer no-key auth only for target types that support it", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("AzureMLChatTarget");

    expect(screen.getByRole("radio", { name: /Identity-based/ })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /No key/ })).not.toBeInTheDocument();
  });

  it("should return to API key auth when the next target type has no no-key option", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByRole("radio", { name: /No key/ }));
    await selectTargetType("AzureMLChatTarget");

    expect(screen.getByRole("radio", { name: /API Key/ })).toBeChecked();
    expect(screen.getByPlaceholderText("API key (stored in memory only)")).toBeInTheDocument();
  });

  it("should offer no-key auth for OpenAI targets when type metadata fails to load", async () => {
    mockedTargetsApi.listTargetTypes.mockRejectedValueOnce(new Error("registry unavailable"));
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    expect(await screen.findByText(/Target details could not be loaded/)).toBeInTheDocument();
    await selectTargetType("OpenAIChatTarget");

    expect(screen.getByRole("radio", { name: /No key/ })).toBeInTheDocument();
  });

  it("should warn the user when identity is selected for a non-Azure OpenAI endpoint", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.openai.com/" } });

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    expect(
      screen.getByText(/Identity-based auth only works with Azure OpenAI/)
    ).toBeInTheDocument();
  });

  it("should NOT warn when identity is selected for a recognized Azure endpoint", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://my-resource.openai.azure.com/" },
    });

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    expect(
      screen.queryByText(/Identity-based auth only works with Azure OpenAI/)
    ).not.toBeInTheDocument();
  });

  it("should disable the submit button and skip API call for identity + non-Azure OpenAI endpoint", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.test.com/" } });

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    const createButton = getSubmitButton().closest("button");
    expect(createButton).toBeDisabled();

    await user.click(getSubmitButton());

    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  });

  it("should warn the user when identity is selected for a non-AML endpoint on AzureMLChatTarget", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("AzureMLChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-model.region.inference.ml.azure.com/score"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://example.com/score" },
    });

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    expect(
      screen.getByText(
        /Identity-based auth for AzureMLChatTarget only works with Azure ML managed online endpoints/
      )
    ).toBeInTheDocument();
  });

  it("should NOT warn when identity is selected for a recognized AML endpoint", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("AzureMLChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-model.region.inference.ml.azure.com/score"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://my-llama.eastus.inference.ml.azure.com/score" },
    });

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    expect(
      screen.queryByText(
        /Identity-based auth for AzureMLChatTarget only works with Azure ML managed online endpoints/
      )
    ).not.toBeInTheDocument();
  });

  it("should disable the submit button and skip API call for identity + non-AML endpoint on AzureMLChatTarget", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("AzureMLChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-model.region.inference.ml.azure.com/score"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://api.test.com/score" },
    });

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    const createButton = getSubmitButton().closest("button");
    expect(createButton).toBeDisabled();

    await user.click(getSubmitButton());

    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  });

  it("should show target picker when RoundRobinTarget is selected", async () => {

    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: "openai_a",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              endpoint: "https://a.openai.azure.com",
            }),
            makeTarget({
              target_registry_name: "openai_b",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              endpoint: "https://b.openai.azure.com",
            }),
          ]}
        />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");

    // Endpoint field should NOT be visible for RoundRobin
    expect(
      screen.queryByPlaceholderText("https://your-resource.openai.azure.com/")
    ).not.toBeInTheDocument();

    // Add Target dropdown should be visible
    expect(screen.getByText("Add Target")).toBeInTheDocument();
  });

  it("should disable Create button when fewer than 2 inner targets are selected for RoundRobin", async () => {

    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: "openai_a",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
            }),
          ]}
        />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");

    const createButton = getSubmitButton().closest("button");
    expect(createButton).toBeDisabled();
  });

  it("should keep full long registry names accessible after selecting RoundRobin targets", async () => {
    const user = userEvent.setup();
    const firstRegistryName =
      "openai-production-eastus2-red-team-evaluation-primary-deployment";
    const secondRegistryName =
      "openai-production-eastus2-red-team-evaluation-secondary-deployment";

    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: firstRegistryName,
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "long-hash-a",
            }),
            makeTarget({
              target_registry_name: secondRegistryName,
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "long-hash-b",
            }),
          ]}
        />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");
    const select = screen.getByText("Select a target to add...").closest("select");
    expect(select).not.toBeNull();
    if (!select) {
      throw new Error("Round Robin target selector was not rendered");
    }

    await user.selectOptions(select, firstRegistryName);
    await user.selectOptions(select, secondRegistryName);

    const selectedName = screen.getByLabelText(
      `Selected target: ${firstRegistryName} (gpt-4o)`
    );
    expect(selectedName).toHaveAttribute("tabindex", "0");
    await user.click(selectedName);
    expect(selectedName).toHaveFocus();

    expect(
      screen.getByRole("button", { name: `Remove ${firstRegistryName}` })
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(`Weight for ${secondRegistryName}`)
    ).toBeInTheDocument();
  });

  it("filters duplicate-by-identifier-hash targets out of the picker once one is selected", async () => {
    const user = userEvent.setup();

    // openai_a and openai_a_alias share an identifier_hash — they resolve to the
    // same backend config, so once one is picked the other should disappear from
    // the dropdown. openai_b has a different hash and stays.
    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: "openai_a",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              underlying_model_name: "gpt-4o",
              identifier_hash: "hash-a",
            }),
            makeTarget({
              target_registry_name: "openai_a_alias",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              underlying_model_name: "gpt-4o",
              identifier_hash: "hash-a",
            }),
            makeTarget({
              target_registry_name: "openai_b",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              underlying_model_name: "gpt-4o",
              identifier_hash: "hash-b",
            }),
          ]}
        />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");

    // Before selecting anything: all three are eligible.
    const select = screen.getByText("Select a target to add...").closest("select")!;
    expect(select.querySelector('option[value="openai_a"]')).not.toBeNull();
    expect(select.querySelector('option[value="openai_a_alias"]')).not.toBeNull();
    expect(select.querySelector('option[value="openai_b"]')).not.toBeNull();

    // Pick openai_a.
    await user.selectOptions(select, "openai_a");

    // openai_a_alias (same hash) should now be filtered out, openai_b stays.
    expect(select.querySelector('option[value="openai_a_alias"]')).toBeNull();
    expect(select.querySelector('option[value="openai_b"]')).not.toBeNull();
  });

  it("applies underlying_model_name → model_name fallback when filtering compatible targets", async () => {
    const user = userEvent.setup();

    // foundry_a has no underlying_model_name but model_name='DeepSeek-R1' — the
    // backend treats its effective underlying model as 'DeepSeek-R1' via the
    // TARGET_EVAL_PARAM_FALLBACKS fallback. foundry_b also lacks underlying_model_name
    // but has model_name='Gemini', which is a different effective underlying model.
    // foundry_c is a true match: same effective underlying model as foundry_a.
    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: "foundry_a",
              target_type: "OpenAIChatTarget",
              model_name: "DeepSeek-R1",
              identifier_hash: "hash-a",
            }),
            makeTarget({
              target_registry_name: "foundry_b",
              target_type: "OpenAIChatTarget",
              model_name: "Gemini",
              identifier_hash: "hash-b",
            }),
            makeTarget({
              target_registry_name: "foundry_c",
              target_type: "OpenAIChatTarget",
              model_name: "DeepSeek-R1",
              identifier_hash: "hash-c",
            }),
          ]}
        />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");
    const select = screen.getByText("Select a target to add...").closest("select")!;
    await user.selectOptions(select, "foundry_a");

    // foundry_b's effective underlying model ('Gemini') differs from foundry_a's
    // ('DeepSeek-R1') once the model_name fallback is applied, so it must be
    // filtered out — the backend would reject the pair with HTTP 400.
    expect(select.querySelector('option[value="foundry_b"]')).toBeNull();
    // foundry_c shares the effective underlying model and stays eligible.
    expect(select.querySelector('option[value="foundry_c"]')).not.toBeNull();
  });

  it("surfaces the backend error detail when target creation fails", async () => {
    const user = userEvent.setup();

    // Simulate an axios error with an RFC 7807 detail body — this is what the
    // backend returns when, for example, RoundRobinTarget rejects an incompatible
    // pair the frontend filter missed.
    const axiosError = Object.assign(new Error("Request failed with status code 400"), {
      isAxiosError: true,
      response: {
        status: 400,
        data: {
          detail:
            "Behavioral parameter 'underlying_model_name' differs across targets: target 0 has 'DeepSeek-R1', target 1 has 'gemini-2.0-flash'.",
        },
      },
    });
    mockedTargetsApi.createTarget.mockRejectedValueOnce(axiosError);

    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: "a",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "hash-a",
            }),
            makeTarget({
              target_registry_name: "b",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "hash-b",
            }),
          ]}
        />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");
    const select = screen.getByText("Select a target to add...").closest("select")!;
    await user.selectOptions(select, "a");
    await user.selectOptions(select, "b");

    await user.click(getSubmitButton());

    await waitFor(() => {
      // The backend's detail (the actual validation message) should be shown to
      // the user, not the generic "Request failed with status code 400".
      expect(screen.getByText(/Behavioral parameter/)).toBeInTheDocument();
    });
    expect(screen.queryByText(/Request failed with status code 400/)).not.toBeInTheDocument();
  });

  // ===========================================================================
  // RoundRobinTarget weight input validation
  // ===========================================================================

  /**
   * Helper: render the dialog with two compatible inner targets already
   * pickable, then select both — leaves the form in the state where every
   * remaining concern is the weight inputs.
   */
  async function renderWithTwoRoundRobinTargetsSelected(): Promise<{
    user: ReturnType<typeof userEvent.setup>;
    weightInputs: HTMLInputElement[];
  }> {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: "a",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "hash-a",
            }),
            makeTarget({
              target_registry_name: "b",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "hash-b",
            }),
          ]}
        />
      </TestWrapper>
    );
    await selectTargetType("RoundRobinTarget");
    const select = screen.getByText("Select a target to add...").closest("select")!;
    await user.selectOptions(select, "a");
    await user.selectOptions(select, "b");
    // Wait for both weight inputs to actually render — under load, React
    // updates can lag behind the userEvent returns and queries return 0 or 1
    // result, causing downstream assertions to operate on stale state.
    await waitFor(
      () => {
        expect(screen.getAllByLabelText(/Weight for /)).toHaveLength(2);
      },
      { timeout: 10000 },
    );
    const weightInputs = screen.getAllByLabelText(/Weight for /) as HTMLInputElement[];
    return { user, weightInputs };
  }

  // Note: exhaustive validation of decimal, scientific notation, negative,
  // zero, and out-of-range cases is covered by the parseWeight unit tests
  // above. These integration tests only verify the UI wiring: that invalid
  // input surfaces an alert + disables Create, and that valid input round
  // trips through createTarget as parsed ints.

  it("shows an alert and disables Create when a weight is invalid", async () => {
    const { user, weightInputs } = await renderWithTwoRoundRobinTargetsSelected();

    // Use fireEvent.change to bypass HTML5 step="1" constraint that
    // userEvent.type would respect. We specifically want to verify our JS
    // validation catches values that bypass browser-level checks.
    fireEvent.change(weightInputs[0], { target: { value: "2.5" } });

    // Re-query state under waitFor — under heavy load React commits can lag
    // behind fireEvent's return, and stale references won't reflect updates.
    await waitFor(
      () => {
        const inputs = screen.getAllByLabelText(/Weight for /) as HTMLInputElement[];
        expect(inputs[0].value).toBe("2.5");
        expect(inputs[0].getAttribute("aria-invalid")).toBe("true");
      },
      { timeout: 10000 },
    );

    expect(screen.getByText("Weight must be a whole number")).toBeInTheDocument();
    expect(getSubmitButton().closest("button")).toBeDisabled();

    // Pressing Enter inside the weight input must not bypass the disabled
    // button and submit the form.
    await user.click(getSubmitButton());
    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  }, 30000);

  it("submits parsed integer weights when all inputs are valid", async () => {
    mockedTargetsApi.createTarget.mockResolvedValueOnce({
      target_registry_name: "rr",
    } as unknown as Awaited<ReturnType<typeof mockedTargetsApi.createTarget>>);

    const { user, weightInputs } = await renderWithTwoRoundRobinTargetsSelected();
    fireEvent.change(weightInputs[0], { target: { value: "7" } });
    fireEvent.change(weightInputs[1], { target: { value: "42" } });

    // Wait until both inputs reflect the new values and Create is enabled
    // (which only happens once both weights have flushed to state and parsed
    // successfully).
    await waitFor(
      () => {
        const inputs = screen.getAllByLabelText(/Weight for /) as HTMLInputElement[];
        expect(inputs[0].value).toBe("7");
        expect(inputs[1].value).toBe("42");
        expect(getSubmitButton().closest("button")).not.toBeDisabled();
      },
      { timeout: 10000 },
    );

    await user.click(getSubmitButton());

    await waitFor(
      () => {
        expect(mockedTargetsApi.createTarget).toHaveBeenCalledTimes(1);
      },
      { timeout: 10000 },
    );
    const call = mockedTargetsApi.createTarget.mock.calls[0][0];
    expect(call.type).toBe("RoundRobinTarget");
    expect(call.params?.targets).toEqual(["a", "b"]);
    expect(call.params?.weights).toEqual([7, 42]);
  }, 30000);

  it("removes a selected inner target when its delete button is clicked", async () => {
    const { user } = await renderWithTwoRoundRobinTargetsSelected();

    // Both targets show up as selected rows.
    expect(screen.getAllByLabelText(/Weight for /)).toHaveLength(2);

    await user.click(screen.getByLabelText("Remove a"));

    await waitFor(
      () => {
        expect(screen.getAllByLabelText(/Weight for /)).toHaveLength(1);
      },
      { timeout: 10000 },
    );
    // With only one inner target left, Create is disabled (needs >= 2).
    expect(getSubmitButton().closest("button")).toBeDisabled();
  }, 30000);

  it("submit-time guards reject invalid weights even if the disabled button is bypassed", async () => {
    // Re-validating at submit time defends against pressing Enter inside the
    // weight input, which submits the form regardless of the button's disabled
    // state. We exercise that branch directly by submitting the form element.
    const { weightInputs } = await renderWithTwoRoundRobinTargetsSelected();

    fireEvent.change(weightInputs[0], { target: { value: "2.5" } });
    await waitFor(
      () => {
        expect(getSubmitButton().closest("button")).toBeDisabled();
      },
      { timeout: 10000 },
    );

    // Find the form (the dialog wraps the fields in a <form>) and dispatch
    // submit directly to simulate Enter-key submission bypassing the button.
    const form = weightInputs[0].closest("form")!;
    fireEvent.submit(form);

    // Error surfaces in the dialog's top-level MessageBar, and the API is not
    // called.
    await waitFor(
      () => {
        expect(
          screen.getByText(/Invalid weight for "a": Weight must be a whole number\./),
        ).toBeInTheDocument();
      },
      { timeout: 10000 },
    );
    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  }, 30000);

  it("submit-time guard rejects submission with fewer than 2 selected inner targets", async () => {
    // Same bypass scenario as above, but for the "need at least 2" guard.
    const { user, weightInputs } = await renderWithTwoRoundRobinTargetsSelected();
    await user.click(screen.getByLabelText("Remove b"));
    await waitFor(
      () => {
        expect(screen.getAllByLabelText(/Weight for /)).toHaveLength(1);
      },
      { timeout: 10000 },
    );

    const form = weightInputs[0].closest("form")!;
    fireEvent.submit(form);

    await waitFor(
      () => {
        expect(screen.getByText("Please select at least 2 targets.")).toBeInTheDocument();
      },
      { timeout: 10000 },
    );
    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  }, 30000);
});

const GITHUB_COPILOT_NOT_SAVEABLE_REASON =
  "'GitHubCopilotTarget' needs a credential in 'github_token', which saved entries can't hold or take from this "
  + "server yet.";

// What the backend reports about saving each type, so the dialog can tell before submitting.
const SAVEABILITY: Record<string, Pick<TargetTypeEntry, "saveable_auth_modes" | "not_saveable_reason">> = {
  OpenAIChatTarget: { saveable_auth_modes: ["identity", "none"], not_saveable_reason: null },
  GitHubCopilotTarget: { saveable_auth_modes: [], not_saveable_reason: GITHUB_COPILOT_NOT_SAVEABLE_REASON },
  RoundRobinTarget: { saveable_auth_modes: ["api_key"], not_saveable_reason: null },
};

const SAVEABLE_TARGET_TYPES: TargetTypeListResponse = {
  items: TARGET_TYPES.items.map((entry) => ({ ...entry, ...SAVEABILITY[entry.target_type] })),
};

// A2ATarget saves without credentials, but its optional token and key can't be saved.
const A2A_TARGET_TYPE: TargetTypeEntry = {
  target_type: "A2ATarget",
  parameters: [
    { name: "endpoint", type_name: "str", required: true, default: null, description: "The A2A agent endpoint." },
    { name: "auth_token", type_name: "str", required: false, default: null, sensitive: true },
    { name: "api_key", type_name: "str", required: false, default: null, sensitive: true },
  ],
  supported_auth_modes: ["api_key"],
  saveable_auth_modes: ["api_key"],
  not_saveable_reason: null,
};

const AZURE_ENDPOINT = "https://contoso.openai.azure.com/openai/v1";

function axiosError(status: number, data: Record<string, unknown>): Error {
  return Object.assign(new Error("Request failed"), { isAxiosError: true, response: { status, data } });
}

// Pasting fills a field in one input event, which keeps these tests well inside the timeout on a busy machine.
async function pasteInto(user: ReturnType<typeof userEvent.setup>, field: HTMLElement, text: string): Promise<void> {
  await user.click(field);
  await user.paste(text);
}

describe("CreateTargetDialog saving", () => {
  const defaultProps = {
    open: true,
    onClose: jest.fn(),
    onCreated: jest.fn(),
  };

  let dialogAccessibilityObserver: MutationObserver;

  beforeEach(() => {
    dialogAccessibilityObserver = watchDialogAccessibility();
    jest.clearAllMocks();
    mockedTargetsApi.listTargetTypes.mockResolvedValue(SAVEABLE_TARGET_TYPES);
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [],
      pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
    } as unknown as Awaited<ReturnType<typeof mockedTargetsApi.listTargets>>);
    mockedTargetsApi.createTarget.mockResolvedValue(makeTarget({ target_registry_name: "openai_chat" }));
  });

  afterEach(() => {
    dialogAccessibilityObserver.disconnect();
  });

  async function fillAzureOpenAiChat(user: ReturnType<typeof userEvent.setup>): Promise<void> {
    await selectTargetType("OpenAIChatTarget");
    await pasteInto(user, screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), AZURE_ENDPOINT);
    await pasteInto(user, screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4o");
  }

  it("should suggest a registry name from the target type that no target has", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[makeTarget({ target_registry_name: "openai_chat" })]}
        />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");

    expect(screen.getByRole("textbox", { name: /Registry Name/ })).toHaveValue("openai_chat_2");
  });

  it("should follow the model in the suggested name until the name is edited", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    expect(nameInput).toHaveValue("openai_chat");
    const modelInput = screen.getByRole("textbox", { name: /Model \/ Deployment Name/ });
    await pasteInto(user, modelInput, "gpt-4o");
    // A startup initializer registers `openai_chat`, which would stop a saved target of that name restoring.
    expect(nameInput).toHaveValue("openai_chat_gpt_4o");

    await user.type(nameInput, "_eval");
    await pasteInto(user, modelInput, "-mini");
    expect(nameInput).toHaveValue("openai_chat_gpt_4o_eval");
  });

  it("should keep a name the user typed when the target type changes", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    await user.clear(nameInput);
    await user.type(nameInput, "contoso_gpt4o");
    await selectTargetType("GitHubCopilotTarget");

    expect(screen.getByRole("textbox", { name: /Registry Name/ })).toHaveValue("contoso_gpt4o");
  });

  it("should show the naming rule and keep the button disabled for an invalid name", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    await user.clear(nameInput);
    await user.type(nameInput, "Contoso-GPT");

    expect(
      screen.getByText("Use lowercase letters, digits and underscores, starting with a letter (max 64 characters)."),
    ).toBeInTheDocument();
    expect(getSubmitButton()).toBeDisabled();
  });

  it("should not send an invalid name when the form is submitted", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    await user.clear(nameInput);
    await user.type(nameInput, "Contoso-GPT");
    fireEvent.submit(screen.getByTestId("create-target-form"));

    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
    expect(nameInput).toHaveAccessibleDescription(expect.stringContaining("Use lowercase letters"));
  });

  it("should start with Microsoft Entra ID and save the target", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);

    expect(screen.getByRole("radio", { name: /Identity-based/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save Target" }));

    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
      name: "openai_chat_gpt_4o",
      type: "OpenAIChatTarget",
      params: { endpoint: AZURE_ENDPOINT, model_name: "gpt-4o" },
      auth_mode: "identity",
      persist: true,
    });
  });

  async function renderWithPendingTargetTypes(): Promise<(targetTypes: TargetTypeListResponse) => void> {
    let resolveTargetTypes: ((targetTypes: TargetTypeListResponse) => void) | null = null;
    mockedTargetsApi.listTargetTypes.mockReturnValue(
      new Promise<TargetTypeListResponse>((resolve) => {
        resolveTargetTypes = resolve;
      }),
    );
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );
    await selectTargetType("OpenAIChatTarget");
    if (resolveTargetTypes === null) {
      throw new Error("Target type resolver was not initialized");
    }
    return resolveTargetTypes;
  }

  it("should switch to a saveable default when the target details arrive after the type is chosen", async () => {
    const user = userEvent.setup();
    const resolveTargetTypes = await renderWithPendingTargetTypes();
    expect(screen.getByRole("radio", { name: "API Key" })).toBeChecked();

    await act(async () => {
      resolveTargetTypes(SAVEABLE_TARGET_TYPES);
    });

    expect(screen.getByRole("radio", { name: /Identity-based/ })).toBeChecked();
    await pasteInto(user, screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), AZURE_ENDPOINT);
    await pasteInto(user, screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4o");
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();
  });

  it("should keep a key typed before the target details arrive and explain why it can't be saved", async () => {
    const user = userEvent.setup();
    const resolveTargetTypes = await renderWithPendingTargetTypes();
    await user.type(screen.getByPlaceholderText("API key (stored in memory only)"), "test-key");

    await act(async () => {
      resolveTargetTypes(SAVEABLE_TARGET_TYPES);
    });

    expect(screen.getByRole("radio", { name: "API Key" })).toBeChecked();
    expect(screen.getByPlaceholderText("API key (stored in memory only)")).toHaveValue("test-key");
    expect(screen.getByText(/Saved targets can't use an API key yet\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create as Temporary" })).toBeInTheDocument();
  });

  it("should go back to the saveable default when the target type changes", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByRole("radio", { name: "API Key" }));
    await selectTargetType("GitHubCopilotTarget");
    await selectTargetType("OpenAIChatTarget");

    expect(screen.getByRole("radio", { name: /Identity-based/ })).toBeChecked();
  });

  it("should keep the key and sign-in when the selected type is picked again", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByRole("radio", { name: "API Key" }));
    await user.type(screen.getByPlaceholderText("API key (stored in memory only)"), "test-key");
    await selectTargetType("OpenAIChatTarget");

    expect(screen.getByRole("radio", { name: "API Key" })).toBeChecked();
    expect(screen.getByPlaceholderText("API key (stored in memory only)")).toHaveValue("test-key");
  });

  it("should point a local endpoint that fails the Entra ID check to No key once the field is left", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await pasteInto(user, screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4o");
    await pasteInto(user, screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), "http://localhost:11434/v1");

    // No error while the address is still being typed, but saving already waits for one that works.
    expect(screen.queryByText(/Identity-based auth only works/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Target" })).toBeDisabled();
    await user.tab();
    expect(screen.getAllByRole("status").some((region) => (
      region.textContent?.includes("For a server that needs no key, such as a local one, choose No key.")
    ))).toBe(true);
    await user.click(screen.getByRole("radio", { name: /^No key/ }));
    expect(screen.queryByText(/Identity-based auth only works/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();
  });

  it("should ask for a model, and suggest a name with it, when an OpenAI type's details fail to load", async () => {
    mockedTargetsApi.listTargetTypes.mockRejectedValueOnce(new Error("registry unavailable"));
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    expect(await screen.findByText(/Target details could not be loaded/)).toBeInTheDocument();
    await selectTargetType("OpenAIChatTarget");
    await pasteInto(user, screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), AZURE_ENDPOINT);
    expect(screen.getByRole("button", { name: "Save Target" })).toBeDisabled();
    await pasteInto(user, screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4o");

    expect(screen.getByRole("textbox", { name: /Registry Name/ })).toHaveValue("openai_chat_gpt_4o");
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();
  });

  it("should not carry a typed key over to another target type", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.listTargetTypes.mockResolvedValue({ items: [...SAVEABLE_TARGET_TYPES.items, A2A_TARGET_TYPE] });
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByRole("radio", { name: "API Key" }));
    await user.type(screen.getByPlaceholderText("API key (stored in memory only)"), "test-key");
    await selectTargetType("A2ATarget");

    expect(screen.getByPlaceholderText("API key (stored in memory only)")).toHaveValue("");
    await pasteInto(user, screen.getByPlaceholderText("https://example.com/"), "https://agent.example.com");
    await user.click(screen.getByRole("button", { name: "Save Target" }));

    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
      name: "a2a",
      type: "A2ATarget",
      params: { endpoint: "https://agent.example.com" },
      persist: true,
    });
  });

  const SERVER_CREDENTIAL_TYPES: TargetTypeListResponse = {
    items: SAVEABLE_TARGET_TYPES.items.map((entry) => (
      entry.target_type === "OpenAIChatTarget"
        ? {
          ...entry,
          server_credentials: [
            { name: "contoso", endpoint: "https://api.contoso.com/v1" },
            { name: "fabrikam", endpoint: "https://fabrikam.openai.azure.com/openai/v1" },
          ],
        }
        : entry
    )),
  };

  it("should save a target that uses a server credential by sending its name and no key", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.listTargetTypes.mockResolvedValue(SERVER_CREDENTIAL_TYPES);
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByRole("radio", { name: /^Server credential/ }));
    const endpointInput = screen.getByPlaceholderText("https://your-resource.openai.azure.com/");
    expect(endpointInput).toHaveValue("https://api.contoso.com/v1");
    expect(endpointInput).toHaveAttribute("readonly");
    expect(screen.queryByPlaceholderText("API key (stored in memory only)")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Server credential" }), "fabrikam");
    expect(endpointInput).toHaveValue("https://fabrikam.openai.azure.com/openai/v1");
    await pasteInto(user, screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4o");
    await user.click(screen.getByRole("button", { name: "Save Target" }));

    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
      name: "openai_chat_gpt_4o",
      type: "OpenAIChatTarget",
      params: { endpoint: "https://fabrikam.openai.azure.com/openai/v1", model_name: "gpt-4o" },
      auth_mode: "api_key",
      server_credential: "fabrikam",
      persist: true,
    });
  });

  it("should keep the server credential's endpoint, and drop its name, when a key is typed instead", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.listTargetTypes.mockResolvedValue(SERVER_CREDENTIAL_TYPES);
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await user.click(screen.getByRole("radio", { name: /^Server credential/ }));
    await user.click(screen.getByRole("radio", { name: "API Key" }));
    const endpointInput = screen.getByPlaceholderText("https://your-resource.openai.azure.com/");
    expect(endpointInput).toHaveValue("https://api.contoso.com/v1");
    expect(endpointInput).not.toHaveAttribute("readonly");
    await pasteInto(user, screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "gpt-4o");
    await user.type(screen.getByPlaceholderText("API key (stored in memory only)"), "test-key");

    expect(screen.getByRole("status", { name: "This target can't be saved" })).toHaveTextContent(
      /Choose Microsoft Entra ID or No key or Server credential to save this one\./,
    );
    await user.click(screen.getByRole("button", { name: "Create as Temporary" }));
    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
      name: "openai_chat_gpt_4o",
      type: "OpenAIChatTarget",
      params: { endpoint: "https://api.contoso.com/v1", model_name: "gpt-4o", api_key: "test-key" },
      persist: false,
    });
  });

  it("should not offer a server credential for a type the server lists none for", async () => {
    mockedTargetsApi.listTargetTypes.mockResolvedValue(SERVER_CREDENTIAL_TYPES);
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    expect(screen.getByRole("radio", { name: /^Server credential/ })).toBeInTheDocument();
    await selectTargetType("GitHubCopilotTarget");

    expect(screen.queryByRole("radio", { name: /^Server credential/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Server credential" })).not.toBeInTheDocument();
  });

  it("should offer Temporary with the reason when the target uses an API key", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("radio", { name: "API Key" }));
    await user.type(screen.getByPlaceholderText("API key (stored in memory only)"), "test-key");

    // A live region, so screen readers hear why the button changed.
    const warning = screen.getByRole("status", { name: "This target can't be saved" });
    expect(warning).toHaveTextContent(
      /Saved targets can't use an API key yet\. Choose Microsoft Entra ID or No key to save this one\./,
    );
    expect(warning).toHaveTextContent("A Temporary target is gone after the backend restarts or is reinitialized.");
    // Once when it appeared, not again for each typed character.
    expect(jest.mocked(Element.prototype.scrollIntoView).mock.contexts.filter((context) => context === warning))
      .toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Create as Temporary" }));

    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
      name: "openai_chat_gpt_4o",
      type: "OpenAIChatTarget",
      params: { endpoint: AZURE_ENDPOINT, model_name: "gpt-4o", api_key: "test-key" },
      persist: false,
    });
  });

  it("should explain before submitting why a target type can't be saved", async () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("GitHubCopilotTarget");

    expect(screen.getByText(new RegExp(GITHUB_COPILOT_NOT_SAVEABLE_REASON.replace(/[.']/g, ".")))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create as Temporary" })).toBeInTheDocument();
  });

  it("should offer Temporary only while a credential is typed for a target that saves without one", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.listTargetTypes.mockResolvedValue({ items: [...SAVEABLE_TARGET_TYPES.items, A2A_TARGET_TYPE] });
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("A2ATarget");
    await pasteInto(user, screen.getByPlaceholderText("https://example.com/"), "https://agent.example.com");
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();

    await user.click(screen.getByText("Advanced settings"));
    const tokenField = screen.getByLabelText(/Auth Token/, { selector: "input" });
    await pasteInto(user, tokenField, "secret-token");

    expect(screen.getByText(/Saved targets can't hold credentials \('auth_token'\) yet\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create as Temporary" })).toBeInTheDocument();

    await user.clear(tokenField);

    expect(screen.queryByText("This target can't be saved")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save Target" }));
    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
      name: "a2a",
      type: "A2ATarget",
      params: { endpoint: "https://agent.example.com" },
      persist: true,
    });
  });

  it("should offer Temporary after the server refuses to save, only for the values it refused", async () => {
    const user = userEvent.setup();
    const detail = "'endpoint' can carry a credential in a URL, which saved entries can't hold. "
      + "It can still be created without saving it.";
    mockedTargetsApi.createTarget.mockRejectedValueOnce(axiosError(400, { type: "/errors/not-saveable", detail }));
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("button", { name: "Save Target" }));

    expect(await screen.findByText(new RegExp("can carry a credential in a URL"))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create as Temporary" }));

    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget.mock.calls.map(([request]) => request.persist)).toEqual([true, false]);
    expect(mockedTargetsApi.createTarget.mock.calls[1][0]).toEqual({
      ...mockedTargetsApi.createTarget.mock.calls[0][0],
      persist: false,
    });
  });

  it("should go back to saving once the refused values change", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockRejectedValueOnce(axiosError(400, {
      type: "/errors/not-saveable",
      detail: "Refused. It can still be created without saving it.",
    }));
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("button", { name: "Save Target" }));
    expect(await screen.findByRole("button", { name: "Create as Temporary" })).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "-mini");

    expect(screen.getByRole("button", { name: "Save Target" })).toBeInTheDocument();
    expect(screen.queryByText("This target can't be saved")).not.toBeInTheDocument();
  });

  it("should go back to saving once an advanced value the server refused changes", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockRejectedValueOnce(axiosError(400, {
      type: "/errors/not-saveable",
      detail: "Refused. It can still be created without saving it.",
    }));
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("button", { name: "Save Target" }));
    expect(await screen.findByRole("button", { name: "Create as Temporary" })).toBeInTheDocument();

    await user.click(screen.getByText("Advanced settings"));
    await pasteInto(user, screen.getByLabelText(/Temperature/, { selector: "input" }), "0.5");

    expect(screen.getByRole("button", { name: "Save Target" })).toBeInTheDocument();
  });

  it.each([
    ["an ordinary bad request", 400, { detail: "Endpoint is not reachable." }],
    ["a validation error", 422, { detail: "Registry name is invalid." }],
    ["a forbidden request", 403, { detail: "You can't create targets." }],
    ["unavailable storage", 503, { detail: "Saved targets can't be written right now." }],
  ])("should not offer Temporary for %s", async (_, status, data) => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockRejectedValueOnce(axiosError(status, data));
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("button", { name: "Save Target" }));

    expect(await screen.findByText(data.detail)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Target" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create as Temporary" })).not.toBeInTheDocument();
  });

  it("should start over with saving after the dialog is closed and reopened", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.createTarget.mockRejectedValueOnce(axiosError(400, {
      type: "/errors/not-saveable",
      detail: "Refused. It can still be created without saving it.",
    }));
    const { rerender } = render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.type(screen.getByRole("textbox", { name: /Registry Name/ }), "_custom");
    await user.click(screen.getByRole("button", { name: "Save Target" }));
    expect(await screen.findByRole("button", { name: "Create as Temporary" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    rerender(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} open={false} />
      </TestWrapper>
    );
    rerender(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByRole("textbox", { name: /Registry Name/ })).toHaveValue("");
    await fillAzureOpenAiChat(user);
    expect(screen.getByRole("textbox", { name: /Registry Name/ })).toHaveValue("openai_chat_gpt_4o");
    expect(screen.getByRole("button", { name: "Save Target" })).toBeInTheDocument();
    expect(screen.queryByText("This target can't be saved")).not.toBeInTheDocument();
  });

  it("should ask for a target type when Enter is pressed in a filled-in name first, until one is chosen", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    // Until a type is chosen the name is the form's only input, so Enter submits once it holds a name.
    await user.type(screen.getByRole("textbox", { name: /Registry Name/ }), "my_chat{Enter}");

    expect(await screen.findByText("Please select a target type")).toBeInTheDocument();
    await fillAzureOpenAiChat(user);
    expect(screen.queryByText("Please select a target type")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Registry Name/ })).toHaveValue("my_chat");
    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  });

  it("should show the Entra ID endpoint error after the dialog was closed from the endpoint field", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    await pasteInto(user, screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), "http://localhost:11434/v1");
    await user.keyboard("{Escape}");
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1);
    rerender(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} open={false} />
      </TestWrapper>
    );
    rerender(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType("OpenAIChatTarget");
    fireEvent.change(screen.getByPlaceholderText("https://your-resource.openai.azure.com/"), {
      target: { value: "http://localhost:11434/v1" },
    });
    expect(screen.getByText(/Identity-based auth only works with Azure OpenAI/)).toBeInTheDocument();
  });

  it("should say a name is taken before submitting when the dialog already lists it", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[makeTarget({ target_registry_name: "contoso_chat" })]}
        />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    await user.clear(nameInput);
    await user.type(nameInput, "contoso_chat");

    expect(nameInput).toHaveAccessibleDescription(
      expect.stringContaining("A target named 'contoso_chat' already exists."),
    );
    expect(screen.getByRole("button", { name: "Save Target" })).toBeDisabled();
    await user.type(nameInput, "_2");
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();
  });

  it("should not hold a name that the refreshed target list no longer has", async () => {
    const user = userEvent.setup();
    const temporaryChat = makeTarget({
      target_registry_name: "temp_chat",
      target_type: "OpenAIChatTarget",
      model_name: "gpt-4o",
      origin: "temporary",
    });
    const { rerender } = render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} existingTargets={[temporaryChat]} />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");
    await fillAzureOpenAiChat(user);
    rerender(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} existingTargets={[]} />
      </TestWrapper>
    );
    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    await user.clear(nameInput);
    await pasteInto(user, nameInput, "temp_chat");

    expect(nameInput).not.toHaveAccessibleDescription(expect.stringContaining("already exists"));
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();
  });

  it("should keep a conflict that arrives late on the name it was reported for", async () => {
    const user = userEvent.setup();
    const detail = "Instance 'openai_chat_gpt_4o' already exists";
    let rejectCreate: ((error: Error) => void) | null = null;
    mockedTargetsApi.createTarget.mockImplementationOnce(() => new Promise((_, reject) => {
      rejectCreate = reject;
    }));
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("button", { name: "Save Target" }));
    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    await user.type(nameInput, "_2");
    const modelInput = screen.getByRole("textbox", { name: /Model \/ Deployment Name/ });
    await user.click(modelInput);
    if (rejectCreate === null) {
      throw new Error("The create request was not sent");
    }
    await act(async () => {
      rejectCreate(axiosError(409, { detail }));
    });

    expect(modelInput).toHaveFocus();
    expect(nameInput).not.toHaveAccessibleDescription(expect.stringContaining(detail));
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();
    await user.clear(nameInput);
    await pasteInto(user, nameInput, "openai_chat_gpt_4o");
    expect(nameInput).toHaveAccessibleDescription(expect.stringContaining(detail));
    expect(screen.getByRole("button", { name: "Save Target" })).toBeDisabled();
  });

  it("should let the user save again when a refusal arrives after the form was edited", async () => {
    const user = userEvent.setup();
    let rejectCreate: ((error: Error) => void) | null = null;
    mockedTargetsApi.createTarget.mockImplementationOnce(() => new Promise((_, reject) => {
      rejectCreate = reject;
    }));
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("button", { name: "Save Target" }));
    await user.type(screen.getByRole("textbox", { name: /Model \/ Deployment Name/ }), "-mini");
    if (rejectCreate === null) {
      throw new Error("The create request was not sent");
    }
    await act(async () => {
      rejectCreate(axiosError(400, {
        type: "/errors/not-saveable",
        detail: "Refused. It can still be created without saving it.",
      }));
    });

    expect(screen.queryByText("This target can't be saved")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();
  });

  it("should show a taken name on the name field and hold saving until it is edited", async () => {
    const user = userEvent.setup();
    const detail = "A saved target named 'openai_chat_gpt_4o' already exists.";
    mockedTargetsApi.createTarget.mockRejectedValueOnce(axiosError(409, { detail }));
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("button", { name: "Save Target" }));

    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    await waitFor(() => expect(nameInput).toHaveAccessibleDescription(expect.stringContaining(detail)));
    expect(nameInput).toHaveFocus();
    expect(screen.getByRole("button", { name: "Save Target" })).toBeDisabled();
    fireEvent.submit(screen.getByTestId("create-target-form"));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledTimes(1);
    await user.type(nameInput, "_2");

    expect(screen.queryByText(detail)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Target" })).toBeEnabled();
  });

  it("should keep a typed conflict such as a backend update out of the name field", async () => {
    const user = userEvent.setup();
    const detail = "The backend was updated. Reload the page to continue.";
    mockedTargetsApi.createTarget.mockRejectedValueOnce(
      axiosError(409, { type: "urn:pyrit:compatibility:mismatch", detail }),
    );
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await fillAzureOpenAiChat(user);
    await user.click(screen.getByRole("button", { name: "Save Target" }));

    expect(await screen.findByText(detail)).toBeInTheDocument();
    const nameInput = screen.getByRole("textbox", { name: /Registry Name/ });
    expect(nameInput).not.toHaveAccessibleDescription(expect.stringContaining(detail));
    expect(nameInput).not.toHaveFocus();
  });

  it("should save a round robin over targets that come back after a restart", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: "saved_chat",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "hash-saved",
              origin: "saved",
            }),
            makeTarget({
              target_registry_name: "startup_chat",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "hash-startup",
              origin: "configuration",
            }),
          ]}
        />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");
    const select = screen.getByText("Select a target to add...").closest("select")!;
    await user.selectOptions(select, "saved_chat");
    await user.selectOptions(select, "startup_chat");
    await user.click(screen.getByRole("button", { name: "Save Target" }));

    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
      name: "weighted_round_robin",
      type: "RoundRobinTarget",
      params: { targets: ["saved_chat", "startup_chat"], weights: [1, 1] },
      persist: true,
    });
  });

  it("should mark Temporary round-robin members and offer Temporary for the round robin", async () => {
    const user = userEvent.setup();
    render(
      <TestWrapper>
        <CreateTargetDialog
          {...defaultProps}
          existingTargets={[
            makeTarget({
              target_registry_name: "saved_chat",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "hash-saved",
              origin: "saved",
            }),
            makeTarget({
              target_registry_name: "temp_chat",
              target_type: "OpenAIChatTarget",
              model_name: "gpt-4o",
              identifier_hash: "hash-temp",
              origin: "temporary",
            }),
          ]}
        />
      </TestWrapper>
    );

    await selectTargetType("RoundRobinTarget");
    const select = screen.getByText("Select a target to add...").closest("select")!;
    expect(screen.getByRole("option", { name: "temp_chat — OpenAIChatTarget (gpt-4o) · Temporary" })).toBeInTheDocument();
    await user.selectOptions(select, "saved_chat");
    await user.selectOptions(select, "temp_chat");

    expect(screen.getByLabelText("Selected target: saved_chat (gpt-4o)")).toBeInTheDocument();
    expect(screen.getByLabelText("Selected target: temp_chat (gpt-4o) · Temporary")).toBeInTheDocument();
    expect(
      await screen.findByText(/Uses temporary target 'temp_chat', which won't exist after a restart\./),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create as Temporary" }));

    await waitFor(() => expect(defaultProps.onCreated).toHaveBeenCalledTimes(1));
    expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
      name: "weighted_round_robin",
      type: "RoundRobinTarget",
      params: { targets: ["saved_chat", "temp_chat"], weights: [1, 1] },
      persist: false,
    });
  });
});

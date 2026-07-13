import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { makeTarget } from "@/test-utils/targetFixtures";
import CreateTargetDialog from "./CreateTargetDialog";
import { parseWeight, MAX_WEIGHT } from "./weightValidation";
import { targetsApi } from "@/services/api";

jest.mock("@/services/api", () => ({
  targetsApi: {
    createTarget: jest.fn(),
    listTargetCatalog: jest.fn(),
    listTargets: jest.fn(),
  },
}));

const mockedTargetsApi = targetsApi as jest.Mocked<typeof targetsApi>;

// Representative target catalog covering the types the dialog renders. Mirrors
// the shape returned by GET /targets/catalog.
const TARGET_CATALOG = {
  items: [
    { target_type: "OpenAIChatTarget", parameters: [], supported_auth_modes: ["api_key", "identity"] },
    { target_type: "OpenAIResponseTarget", parameters: [], supported_auth_modes: ["api_key", "identity"] },
    { target_type: "AzureMLChatTarget", parameters: [], supported_auth_modes: ["api_key", "identity"] },
    { target_type: "RoundRobinTarget", parameters: [], supported_auth_modes: ["api_key"] },
  ],
};

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <FluentProvider theme={webLightTheme}>{children}</FluentProvider>;

/**
 * Helper to select a target type from the native select element.
 * Uses selectOptions from userEvent which works with native select.
 */
async function selectTargetType(
  user: ReturnType<typeof userEvent.setup>,
  value: string
) {
  const select = screen.getByRole("combobox");
  await user.selectOptions(select, value);
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
  const defaultProps = {
    open: true,
    onClose: jest.fn(),
    onCreated: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
    mockedTargetsApi.listTargetCatalog.mockResolvedValue(
      TARGET_CATALOG as unknown as Awaited<ReturnType<typeof mockedTargetsApi.listTargetCatalog>>,
    );
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [],
      pagination: { limit: 200, has_more: false, next_cursor: null, prev_cursor: null },
    } as unknown as Awaited<ReturnType<typeof mockedTargetsApi.listTargets>>);
  });

  it("should render dialog when open", () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    expect(screen.getByText("Create New Target")).toBeInTheDocument();
    expect(screen.getByText("Create Target")).toBeInTheDocument();
    expect(screen.getByText("Cancel")).toBeInTheDocument();
  });

  it("should not render when closed", () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} open={false} />
      </TestWrapper>
    );

    expect(screen.queryByText("Create New Target")).not.toBeInTheDocument();
  });

  it("should have Create button disabled until type and endpoint filled", () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    const createButton = screen.getByText("Create Target");
    expect(createButton.closest("button")).toBeDisabled();
  });

  it("should hide the Authentication field until a target type is selected", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    // No type is chosen yet, so authentication option is not visible yet, but plain API Key input is.
    expect(
      screen.queryByRole("radio", { name: /Identity-based/ })
    ).not.toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("API key (stored in memory only)")
    ).toBeInTheDocument();

    // Selecting an identity-capable type should reveal the Authentication field.
    await selectTargetType(user, "OpenAIChatTarget");
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
    await selectTargetType(user, "OpenAIChatTarget");

    // Fill the endpoint & model names
    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.openai.com" } });

    const modelInput = screen.getByPlaceholderText("e.g. gpt-4o, my-deployment");
    fireEvent.change(modelInput, { target: { value: "gpt-4" } });

    // Submit
    await user.click(screen.getByText("Create Target"));

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
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
    await selectTargetType(user, "OpenAIChatTarget");

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
    await user.click(screen.getByText("Create Target"));

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
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

    await selectTargetType(user, "OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.openai.com" } });

    const modelInput = screen.getByPlaceholderText("e.g. gpt-4o, my-deployment");
    fireEvent.change(modelInput, { target: { value: "gpt-4o" } });

    // Do NOT toggle the underlying model switch

    await user.click(screen.getByText("Create Target"));

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
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
    await selectTargetType(user, "OpenAIChatTarget");

    // Fill endpoint
    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://example.com" } });

    // Submit
    await user.click(screen.getByText("Create Target"));

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
    await selectTargetType(user, "OpenAIChatTarget");

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

    await user.click(screen.getByText("Create Target"));

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

  it("should display pyrit_conf hint text", () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    expect(
      screen.getByText(/auto-populated by adding an initializer/)
    ).toBeInTheDocument();
  });

  it("should render .pyrit_conf_example as an accessible link", () => {
    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    const link = screen.getByRole("link", { name: ".pyrit_conf_example" });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute(
      "href",
      "https://github.com/microsoft/PyRIT/blob/main/.pyrit_conf_example"
    );
  });

  it("should show field validation errors when submitting form without endpoint", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    // Select target type but leave endpoint empty
    await selectTargetType(user, "OpenAIChatTarget");

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

    await selectTargetType(user, "OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://example.com" } });

    await user.click(screen.getByText("Create Target"));

    await waitFor(() => {
      expect(screen.getByText("string error")).toBeInTheDocument();
    });
  });

  it("should create AzureMLChatTarget with AzureML-specific params", async () => {
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
    await selectTargetType(user, "AzureMLChatTarget");

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

    // Submit (uses defaults for max_new_tokens, temperature, top_p, repetition_penalty)
    await user.click(screen.getByText("Create Target"));

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
        type: "AzureMLChatTarget",
        params: {
          endpoint: "https://my-llama.eastus.inference.ml.azure.com/score",
          model_name: "Llama-3.2-3B-Instruct",
          max_new_tokens: 400,
          temperature: 1.0,
          top_p: 1.0,
          repetition_penalty: 1.0,
        },
      });
      expect(onCreated).toHaveBeenCalled();
    });
  });

  it("should show AzureML fields and hide OpenAI fields when AzureMLChatTarget selected", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType(user, "AzureMLChatTarget");

    // AzureML-specific fields should be visible
    expect(screen.getByText("Max New Tokens")).toBeInTheDocument();
    expect(screen.getByText("Temperature")).toBeInTheDocument();
    expect(screen.getByText("Top P")).toBeInTheDocument();
    expect(screen.getByText("Repetition Penalty")).toBeInTheDocument();

    // OpenAI-specific fields should NOT be visible, but underlying model switch should be
    expect(screen.getByRole("switch")).toBeInTheDocument();
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

    await selectTargetType(user, "AzureMLChatTarget");

    // Fill endpoint — use fireEvent.change because userEvent.type truncates
    // URLs containing periods in FluentUI Input under jsdom.
    const endpointInput = screen.getByPlaceholderText(
      "https://your-model.region.inference.ml.azure.com/score"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://my-model.eastus.inference.ml.azure.com/score" },
    });

    // Modify AzureML-specific fields to non-default values via label queries
    // Use fireEvent.change for the same jsdom/FluentUI reason as endpoint input.
    fireEvent.change(screen.getByLabelText("Max New Tokens"), {
      target: { value: "512" },
    });
    fireEvent.change(screen.getByLabelText("Temperature"), {
      target: { value: "0.7" },
    });
    fireEvent.change(screen.getByLabelText("Top P"), {
      target: { value: "0.9" },
    });
    fireEvent.change(screen.getByLabelText("Repetition Penalty"), {
      target: { value: "1.2" },
    });

    await user.click(screen.getByText("Create Target"));

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
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

    await selectTargetType(user, "OpenAIChatTarget");

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

    await user.click(screen.getByText("Create Target"));

    await waitFor(() => {
      expect(mockedTargetsApi.createTarget).toHaveBeenCalledWith({
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

    await selectTargetType(user, "OpenAIChatTarget");

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

    await user.click(screen.getByText("Create Target"));

    await waitFor(() => {
      const call = mockedTargetsApi.createTarget.mock.calls[0][0];
      expect(call.auth_mode).toBe("identity");
      expect(call.params).not.toHaveProperty("api_key");
    });
  });

  it("should warn the user when identity is selected for a non-Azure OpenAI endpoint", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType(user, "OpenAIChatTarget");

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

    await selectTargetType(user, "OpenAIChatTarget");

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

  it("should disable Create Target and skip API call for identity + non-Azure OpenAI endpoint", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType(user, "OpenAIChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-resource.openai.azure.com/"
    );
    fireEvent.change(endpointInput, { target: { value: "https://api.test.com/" } });

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    const createButton = screen.getByText("Create Target").closest("button");
    expect(createButton).toBeDisabled();

    await user.click(screen.getByText("Create Target"));

    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  });

  it("should warn the user when identity is selected for a non-AML endpoint on AzureMLChatTarget", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType(user, "AzureMLChatTarget");

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

    await selectTargetType(user, "AzureMLChatTarget");

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

  it("should disable Create Target and skip API call for identity + non-AML endpoint on AzureMLChatTarget", async () => {
    const user = userEvent.setup();

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} />
      </TestWrapper>
    );

    await selectTargetType(user, "AzureMLChatTarget");

    const endpointInput = screen.getByPlaceholderText(
      "https://your-model.region.inference.ml.azure.com/score"
    );
    fireEvent.change(endpointInput, {
      target: { value: "https://api.test.com/score" },
    });

    await user.click(
      screen.getByRole("radio", { name: /Identity-based/ })
    );

    const createButton = screen.getByText("Create Target").closest("button");
    expect(createButton).toBeDisabled();

    await user.click(screen.getByText("Create Target"));

    expect(mockedTargetsApi.createTarget).not.toHaveBeenCalled();
  });

  it("should show target picker when RoundRobinTarget is selected", async () => {
    const user = userEvent.setup();

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

    await selectTargetType(user, "RoundRobinTarget");

    // Endpoint field should NOT be visible for RoundRobin
    expect(
      screen.queryByPlaceholderText("https://your-resource.openai.azure.com/")
    ).not.toBeInTheDocument();

    // Add Target dropdown should be visible
    expect(screen.getByText("Add Target")).toBeInTheDocument();
  });

  it("should disable Create button when fewer than 2 inner targets are selected for RoundRobin", async () => {
    const user = userEvent.setup();

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

    await selectTargetType(user, "RoundRobinTarget");

    const createButton = screen.getByText("Create Target").closest("button");
    expect(createButton).toBeDisabled();
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

    await selectTargetType(user, "RoundRobinTarget");

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

    await selectTargetType(user, "RoundRobinTarget");
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

    await selectTargetType(user, "RoundRobinTarget");
    const select = screen.getByText("Select a target to add...").closest("select")!;
    await user.selectOptions(select, "a");
    await user.selectOptions(select, "b");

    await user.click(screen.getByText("Create Target"));

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
    await selectTargetType(user, "RoundRobinTarget");
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
    expect(screen.getByText("Create Target").closest("button")).toBeDisabled();

    // Pressing Enter inside the weight input must not bypass the disabled
    // button and submit the form.
    await user.click(screen.getByText("Create Target"));
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
        expect(screen.getByText("Create Target").closest("button")).not.toBeDisabled();
      },
      { timeout: 10000 },
    );

    await user.click(screen.getByText("Create Target"));

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
    expect(screen.getByText("Create Target").closest("button")).toBeDisabled();
  }, 30000);

  it("submit-time guards reject invalid weights even if the disabled button is bypassed", async () => {
    // Re-validating at submit time defends against pressing Enter inside the
    // weight input, which submits the form regardless of the button's disabled
    // state. We exercise that branch directly by submitting the form element.
    const { weightInputs } = await renderWithTwoRoundRobinTargetsSelected();

    fireEvent.change(weightInputs[0], { target: { value: "2.5" } });
    await waitFor(
      () => {
        expect(screen.getByText("Create Target").closest("button")).toBeDisabled();
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

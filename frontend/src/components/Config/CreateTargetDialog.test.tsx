import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import CreateTargetDialog from "./CreateTargetDialog";
import { targetsApi } from "../../services/api";

jest.mock("../../services/api", () => ({
  targetsApi: {
    createTarget: jest.fn(),
  },
}));

const mockedTargetsApi = targetsApi as jest.Mocked<typeof targetsApi>;

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

describe("CreateTargetDialog", () => {
  const defaultProps = {
    open: true,
    onClose: jest.fn(),
    onCreated: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
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
    mockedTargetsApi.createTarget.mockResolvedValue({
      target_registry_name: "openai_chat_new",
      target_type: "OpenAIChatTarget",
    });

    render(
      <TestWrapper>
        <CreateTargetDialog {...defaultProps} onCreated={onCreated} />
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

    // Fill model name
    const modelInput = screen.getByPlaceholderText("e.g. gpt-4o, dall-e-3");
    await user.type(modelInput, "gpt-4");

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
    mockedTargetsApi.createTarget.mockResolvedValue({
      target_registry_name: "openai_chat_keyed",
      target_type: "OpenAIChatTarget",
    });

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

  it("should show generic error for non-Error exceptions", async () => {
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
      expect(screen.getByText("Failed to create target")).toBeInTheDocument();
    });
  });
});

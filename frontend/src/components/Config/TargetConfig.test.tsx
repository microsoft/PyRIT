import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { UserPreferencesProvider } from "@/hooks/useUserPreferences";
import { makeTarget } from "@/test-utils/targetFixtures";
import TargetConfig from "./TargetConfig";
import { targetsApi } from "../../services/api";
import type { TargetInstance, TargetListResponse } from "../../types";

jest.mock("../../services/api", () => ({
  targetsApi: {
    listTargets: jest.fn(),
    createTarget: jest.fn(),
    deleteTarget: jest.fn(),
  },
}));

let mockRuntimeGeneration = "generation-1";

jest.mock("@/hooks/useRuntime", () => ({
  useRuntime: () => ({ generation: mockRuntimeGeneration, ready: true, state: "ready" }),
}));

jest.mock("./CreateTargetDialog", () => {
  const MockDialog = ({
    open,
    onClose,
    onCreated,
  }: {
    open: boolean;
    onClose: () => void;
    onCreated: () => void;
  }) => {
    if (!open) return null;
    return (
      <div data-testid="create-dialog">
        <button onClick={onClose} data-testid="dialog-close">
          Cancel
        </button>
        <button onClick={onCreated} data-testid="dialog-create">
          Create
        </button>
      </div>
    );
  };
  MockDialog.displayName = "MockCreateTargetDialog";
  return {
    __esModule: true,
    default: MockDialog,
  };
});

const mockedTargetsApi = targetsApi as jest.Mocked<typeof targetsApi>;

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => (
  <UserPreferencesProvider accountKey="local">
    <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
  </UserPreferencesProvider>
);

const sampleTargets: TargetInstance[] = [
  makeTarget({
    target_registry_name: "openai_chat_gpt4",
    target_type: "OpenAIChatTarget",
    endpoint: "https://api.openai.com",
    model_name: "gpt-4",
  }),
  makeTarget({
    target_registry_name: "openai_image_dalle",
    target_type: "OpenAIImageTarget",
    endpoint: "https://api.openai.com",
    model_name: "dall-e-3",
  }),
];

describe("TargetConfig", () => {
  const defaultProps = {
    defaultObjectiveTarget: null as TargetInstance | null,
    defaultAdversarialTarget: null as TargetInstance | null,
    onSetDefaultObjectiveTarget: jest.fn(),
    onSetDefaultAdversarialTarget: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    mockRuntimeGeneration = "generation-1";
  });

  it("should show loading state initially", () => {
    mockedTargetsApi.listTargets.mockReturnValue(new Promise(() => {})); // never resolves

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "Target Registry" })
    ).toBeInTheDocument();
    expect(screen.getByText("Loading targets...")).toBeInTheDocument();
  });

  it("should render target list after loading", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: sampleTargets,
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getAllByText("OpenAIChatTarget").length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText("OpenAIImageTarget").length).toBeGreaterThanOrEqual(1);
    });
  });

  it("should load every registry page before offering defaults", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.listTargets
      .mockResolvedValueOnce({
        items: [sampleTargets[0]],
        pagination: { limit: 200, has_more: true, next_cursor: "second-page" },
      })
      .mockResolvedValueOnce({
        items: [sampleTargets[1]],
        pagination: { limit: 200, has_more: false },
      });
    render(<TestWrapper><TargetConfig {...defaultProps} /></TestWrapper>);
    expect(await screen.findByText("openai_image_dalle")).toBeInTheDocument();
    expect(mockedTargetsApi.listTargets).toHaveBeenCalledWith(200, "second-page");
    await user.selectOptions(screen.getByRole("combobox", { name: "Default objective target" }), sampleTargets[1].target_registry_name);
    expect(defaultProps.onSetDefaultObjectiveTarget).toHaveBeenCalledWith(sampleTargets[1]);
  });

  it("should show empty state when no targets", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [],
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("No Targets Configured")).toBeInTheDocument();
    });
    expect(screen.getByText("target", { selector: "code" })).toBeInTheDocument();
    expect(
      screen.getByText(/register available prompt targets automatically/i)
    ).toBeInTheDocument();
    expect(
      screen.queryByText("airt", { selector: "code" })
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: ".pyrit_conf_example" })
    ).toHaveAttribute("rel", "noopener noreferrer");
  });

  it(
    "should show error state on API failure",
    async () => {
      mockedTargetsApi.listTargets.mockRejectedValue(
        new Error("Connection refused")
      );

      render(
        <TestWrapper>
          <TargetConfig {...defaultProps} />
        </TestWrapper>
      );

      await waitFor(
        () => {
          expect(screen.getByText(/Connection refused/)).toBeInTheDocument();
        },
        { timeout: 15000 }
      );
    },
    20000
  );

  it("should set the objective default", async () => {
    const user = userEvent.setup();
    const onSetDefaultObjectiveTarget = jest.fn();
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: sampleTargets,
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig
          {...defaultProps}
          onSetDefaultObjectiveTarget={onSetDefaultObjectiveTarget}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getAllByText("OpenAIChatTarget").length).toBeGreaterThanOrEqual(1);
    });

    await user.selectOptions(screen.getByRole("combobox", { name: "Default objective target" }), sampleTargets[0].target_registry_name);

    expect(onSetDefaultObjectiveTarget).toHaveBeenCalledWith(sampleTargets[0]);
  });

  it("should show the objective default badge", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: sampleTargets,
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig
          {...defaultProps}
          defaultObjectiveTarget={sampleTargets[0]}
        />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Default objective target" })).toHaveValue(sampleTargets[0].target_registry_name);
    });
  });

  it("should refresh targets when Refresh button is clicked", async () => {
    const user = userEvent.setup();
    const onTargetsLoaded = jest.fn();
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: sampleTargets,
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} onTargetsLoaded={onTargetsLoaded} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getAllByText("OpenAIChatTarget").length).toBeGreaterThanOrEqual(1);
    });

    expect(mockedTargetsApi.listTargets).toHaveBeenCalledTimes(1);
    expect(onTargetsLoaded).toHaveBeenCalledWith(sampleTargets);

    await user.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => {
      expect(mockedTargetsApi.listTargets).toHaveBeenCalledTimes(2);
    });
    expect(onTargetsLoaded).toHaveBeenCalledTimes(2);
  });

  it("should keep table filters, expanded rows, and defaults across a runtime reload", async () => {
    const user = userEvent.setup();
    const imageCapabilities = {
      supports_multi_turn: true,
      supports_json_schema: false,
      supports_json_output: false,
      supports_system_prompt: true,
      supported_input_modalities: ["text", "image_path"],
      supported_output_modalities: ["text"],
    };
    const roundRobin = makeTarget({
      target_registry_name: "rr_image",
      target_type: "RoundRobinTarget",
      capabilities: imageCapabilities,
      target_specific_params: { weights: [1, 1] },
      inner_targets: [
        { target_registry_name: "inner_a", target_type: "OpenAIChatTarget" },
        { target_registry_name: "inner_b", target_type: "OpenAIChatTarget" },
      ],
    });
    const textTarget = makeTarget({
      target_registry_name: "text_only",
      target_type: "OpenAIChatTarget",
      capabilities: { ...imageCapabilities, supported_input_modalities: ["text"] },
    });
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [roundRobin, textTarget],
      pagination: { limit: 200, has_more: false },
    });
    const onTargetsLoaded = jest.fn();
    const { rerender } = render(
      <TestWrapper>
        <TargetConfig {...defaultProps} defaultObjectiveTarget={roundRobin} onTargetsLoaded={onTargetsLoaded} />
      </TestWrapper>
    );
    await user.click(await screen.findByRole("combobox", { name: "Filter by input:" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Image" }));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Expand inner targets" }));
    const scrollRegion = screen.getByTestId("target-table-scroll-region");

    mockRuntimeGeneration = "generation-2";
    rerender(
      <TestWrapper>
        <TargetConfig {...defaultProps} defaultObjectiveTarget={roundRobin} onTargetsLoaded={onTargetsLoaded} />
      </TestWrapper>
    );
    await waitFor(() => {
      expect(onTargetsLoaded).toHaveBeenCalledTimes(2);
    });

    expect(screen.getByTestId("target-table-scroll-region")).toBe(scrollRegion);
    expect(screen.getByRole("combobox", { name: "Filter by input:" })).toHaveValue("Inputs: Image");
    expect(screen.queryByRole("row", { name: /text_only/ })).not.toBeInTheDocument();
    expect(screen.getByText("#1 inner_a")).toBeInTheDocument();
    const defaults = within(screen.getByRole("region", { name: "Target defaults" }));
    expect(defaults.getByRole("combobox", { name: "Default objective target" })).toHaveValue("rr_image");
  });

  it("should drop a filter chosen while a runtime reload is still loading", async () => {
    const user = userEvent.setup();
    const textCapabilities = {
      supports_multi_turn: true,
      supports_json_schema: false,
      supports_json_output: false,
      supports_system_prompt: true,
      supported_input_modalities: ["text"],
      supported_output_modalities: ["text"],
    };
    const imageTarget = makeTarget({
      target_registry_name: "image_chat",
      target_type: "OpenAIChatTarget",
      capabilities: { ...textCapabilities, supported_input_modalities: ["text", "image_path"] },
    });
    const textTarget = makeTarget({
      target_registry_name: "text_chat",
      target_type: "OpenAIChatTarget",
      capabilities: textCapabilities,
    });
    mockedTargetsApi.listTargets.mockResolvedValueOnce({
      items: [imageTarget, textTarget],
      pagination: { limit: 200, has_more: false },
    });
    const { rerender } = render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );
    await screen.findByRole("combobox", { name: "Filter by input:" });

    let resolveReload: (response: TargetListResponse) => void = () => {};
    mockedTargetsApi.listTargets.mockReturnValueOnce(
      new Promise<TargetListResponse>((resolve) => {
        resolveReload = resolve;
      })
    );
    mockRuntimeGeneration = "generation-2";
    rerender(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );
    await waitFor(() => {
      expect(mockedTargetsApi.listTargets).toHaveBeenCalledTimes(2);
    });
    await user.click(screen.getByRole("combobox", { name: "Filter by input:" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Image" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("row", { name: /text_chat/ })).not.toBeInTheDocument();

    await act(async () => {
      resolveReload({ items: [textTarget], pagination: { limit: 200, has_more: false } });
    });

    expect(screen.getByRole("row", { name: /text_chat/ })).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Filter by input:" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("should open create dialog when New Target is clicked", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [],
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("No Targets Configured")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByText("New Target"));

    expect(screen.getByTestId("create-dialog")).toBeInTheDocument();
  });

  it("should refresh list after target creation", async () => {
    const user = userEvent.setup();
    const onTargetsLoaded = jest.fn();
    mockedTargetsApi.listTargets
      .mockResolvedValueOnce({ items: [], pagination: { limit: 200, has_more: false } })
      .mockResolvedValueOnce({ items: sampleTargets, pagination: { limit: 200, has_more: false } });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} onTargetsLoaded={onTargetsLoaded} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("No Targets Configured")).toBeInTheDocument();
    });

    expect(onTargetsLoaded).toHaveBeenCalledWith([]);
    await user.click(screen.getByRole("button", { name: "New Target" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onTargetsLoaded).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "New Target" }));
    await user.click(screen.getByRole("button", { name: "Create", exact: true }));

    await waitFor(() => {
      expect(screen.getAllByText("OpenAIChatTarget").length).toBeGreaterThanOrEqual(1);
    });
    expect(onTargetsLoaded).toHaveBeenCalledTimes(2);
    expect(onTargetsLoaded).toHaveBeenLastCalledWith(sampleTargets);
  });

  it("should display target type, endpoint, and model", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: sampleTargets,
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getAllByText("OpenAIChatTarget").length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText("gpt-4")).toBeInTheDocument();
      expect(
        screen.getAllByText("https://api.openai.com").length
      ).toBeGreaterThanOrEqual(1);
    });
  });

  it("should display target_specific_params like reasoning_effort", async () => {
    const targetsWithParams: TargetInstance[] = [
      makeTarget({
        target_registry_name: "azure_responses",
        target_type: "OpenAIResponseTarget",
        endpoint: "https://api.openai.com",
        model_name: "o3",
        target_specific_params: {
          reasoning_effort: "high",
          reasoning_summary: "auto",
          max_output_tokens: 4096,
        },
      }),
    ];

    mockedTargetsApi.listTargets.mockResolvedValue({
      items: targetsWithParams,
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("OpenAIResponseTarget")).toBeInTheDocument();
      // formatParams renders as "key: value, key: value"
      expect(screen.getByText(/reasoning_effort: high/)).toBeInTheDocument();
      expect(screen.getByText(/reasoning_summary: auto/)).toBeInTheDocument();
      expect(screen.getByText(/max_output_tokens: 4096/)).toBeInTheDocument();
    });
  });

  it("should show dash when no target_specific_params", async () => {
    const targetsNoParams: TargetInstance[] = [
      makeTarget({
        target_registry_name: "simple_target",
        target_type: "TextTarget",
        endpoint: "http://localhost",
        model_name: "text",
      }),
    ];

    mockedTargetsApi.listTargets.mockResolvedValue({
      items: targetsNoParams,
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("TextTarget")).toBeInTheDocument();
    });

    // No reasoning or other special params should be displayed
    expect(screen.queryByText(/reasoning_effort:/)).not.toBeInTheDocument();
  });

  it("should open dialog when Create First Target button is clicked in empty state", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [],
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("No Targets Configured")).toBeInTheDocument();
    });

    // Click the "Create First Target" button (in the empty state)
    await userEvent.click(screen.getByText("Create First Target"));

    expect(screen.getByTestId("create-dialog")).toBeInTheDocument();
  });

  it("should close the create dialog when Cancel is clicked", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: sampleTargets,
      pagination: { limit: 200, has_more: false },
    });

    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} />
      </TestWrapper>
    );

    await waitFor(() => {
      expect(screen.getByText("New Target")).toBeInTheDocument();
    });

    // Open the dialog
    await userEvent.click(screen.getByText("New Target"));
    expect(screen.getByTestId("create-dialog")).toBeInTheDocument();

    // Close via Cancel
    await userEvent.click(screen.getByTestId("dialog-close"));
    expect(screen.queryByTestId("create-dialog")).not.toBeInTheDocument();
  });

  it("should delete a saved target with its version and return focus to New Target", async () => {
    const user = userEvent.setup();
    const savedTarget = { ...sampleTargets[0], version: "v1" };
    mockedTargetsApi.listTargets
      .mockResolvedValueOnce({ items: [savedTarget, sampleTargets[1]], pagination: { limit: 200, has_more: false } })
      .mockResolvedValueOnce({ items: [sampleTargets[1]], pagination: { limit: 200, has_more: false } });
    mockedTargetsApi.deleteTarget.mockResolvedValue();
    render(<TestWrapper><TargetConfig {...defaultProps} /></TestWrapper>);

    await user.click(await screen.findByRole("button", { name: "Delete openai_chat_gpt4" }));
    expect(screen.queryByRole("button", { name: "Delete openai_image_dalle" })).not.toBeInTheDocument();
    const dialog = screen.getByRole("dialog", { name: "Delete saved target?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(mockedTargetsApi.deleteTarget).toHaveBeenCalledWith("openai_chat_gpt4", "v1");
    await waitFor(() => {
      expect(screen.queryByText("openai_chat_gpt4")).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "New Target" })).toHaveFocus();
    });
  });

  it("should keep the confirmation open and show why a delete failed", async () => {
    const user = userEvent.setup();
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [{ ...sampleTargets[0], version: "v1" }],
      pagination: { limit: 200, has_more: false },
    });
    mockedTargetsApi.deleteTarget.mockRejectedValue(
      new Error("Target 'openai_chat_gpt4' cannot be deleted because saved instances reference it"),
    );
    render(<TestWrapper><TargetConfig {...defaultProps} /></TestWrapper>);

    await user.click(await screen.findByRole("button", { name: "Delete openai_chat_gpt4" }));
    const dialog = screen.getByRole("dialog", { name: "Delete saved target?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(await within(dialog).findByText(/saved instances reference it/)).toBeInTheDocument();
    expect(screen.getByText("openai_chat_gpt4")).toBeInTheDocument();
  });

  it("should explain saved targets that were not restored and let them be deleted", async () => {
    const user = userEvent.setup();
    const unrestorable: TargetListResponse["unrestorable"] = [{
      kind: "target",
      name: "team-chat",
      type: "OpenAIChatTarget",
      reason: "Environment variable 'TEAM_KEY' (for 'api_key') is not set.",
      version: "v9",
    }];
    mockedTargetsApi.listTargets
      .mockResolvedValueOnce({ items: sampleTargets, pagination: { limit: 200, has_more: false }, unrestorable })
      .mockResolvedValueOnce({ items: sampleTargets, pagination: { limit: 200, has_more: false } });
    mockedTargetsApi.deleteTarget.mockResolvedValue();
    render(<TestWrapper><TargetConfig {...defaultProps} /></TestWrapper>);

    const list = await screen.findByRole("list", { name: "Saved targets that were not restored" });
    expect(within(list).getByText("team-chat")).toBeInTheDocument();
    expect(within(list).getByText(/'TEAM_KEY' \(for 'api_key'\) is not set/)).toBeInTheDocument();

    await user.click(within(list).getByRole("button", { name: "Delete saved target team-chat" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));

    expect(mockedTargetsApi.deleteTarget).toHaveBeenCalledWith("team-chat", "v9");
    await waitFor(() => {
      expect(screen.queryByRole("list", { name: "Saved targets that were not restored" })).not.toBeInTheDocument();
    });
  });

  it("should report a saved target store that could not be read and still list targets", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: sampleTargets,
      pagination: { limit: 200, has_more: false },
      restore_error: "The saved instance store is unavailable: access denied",
    });
    render(<TestWrapper><TargetConfig {...defaultProps} /></TestWrapper>);

    expect(await screen.findByText("Saved targets could not be loaded")).toBeInTheDocument();
    expect(screen.getByText(/store is unavailable: access denied/)).toBeInTheDocument();
    expect(screen.getByText("openai_chat_gpt4")).toBeInTheDocument();
  });

});

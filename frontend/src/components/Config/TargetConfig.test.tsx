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

async function openDeleteDialog(
  user: ReturnType<typeof userEvent.setup>,
  registryName: string,
): Promise<void> {
  await user.click(await screen.findByRole("button", { name: `Actions for ${registryName}` }));
  await user.click(await screen.findByRole("menuitem", { name: `Delete ${registryName}` }));
}

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

  it("should enable deletion only for manually added targets and explain protected targets", async () => {
    const user = userEvent.setup();
    const manual = { ...sampleTargets[1], can_delete: true };
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [sampleTargets[0], manual],
      pagination: { limit: 200, has_more: false },
    });
    render(<TestWrapper><TargetConfig {...defaultProps} /></TestWrapper>);
    await user.click(await screen.findByRole("button", { name: `Actions for ${sampleTargets[0].target_registry_name}` }));
    expect(await screen.findByRole("menuitem", { name: `Hide ${sampleTargets[0].target_registry_name}` })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /^Delete / })).toHaveAttribute("aria-disabled", "true");
    expect(within(screen.getByRole("menuitem", { name: /^Delete / })).getByText(/edit .env \/ .pyrit_conf and reinitialize/)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: `Actions for ${manual.target_registry_name}` }));
    expect(await screen.findByRole("menuitem", { name: `Delete ${manual.target_registry_name}` })).not.toHaveAttribute("aria-disabled", "true");
  });

  it("should confirm deletion and remove the target without selecting a replacement", async () => {
    const user = userEvent.setup();
    const manual = { ...sampleTargets[1], can_delete: true };
    const onTargetsLoaded = jest.fn();
    mockedTargetsApi.listTargets.mockResolvedValueOnce({
      items: [sampleTargets[0], manual],
      pagination: { limit: 200, has_more: false },
    }).mockResolvedValue({
      items: [sampleTargets[0]],
      pagination: { limit: 200, has_more: false },
    });
    mockedTargetsApi.deleteTarget.mockResolvedValueOnce(undefined);
    render(
      <TestWrapper>
        <TargetConfig {...defaultProps} defaultObjectiveTarget={manual} onTargetsLoaded={onTargetsLoaded} />
      </TestWrapper>
    );
    await openDeleteDialog(user, manual.target_registry_name);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Saved conversations and results are kept/)).toBeInTheDocument();
    expect(within(dialog).getByText(/immediately removes.*shared registry for all users and cannot be undone/)).toBeInTheDocument();
    expect(mockedTargetsApi.deleteTarget).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Delete target" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText(manual.target_registry_name)).not.toBeInTheDocument());
    expect(mockedTargetsApi.deleteTarget).toHaveBeenCalledWith(manual.target_registry_name);
    expect(onTargetsLoaded).toHaveBeenLastCalledWith([sampleTargets[0]]);
    expect(defaultProps.onSetDefaultObjectiveTarget).not.toHaveBeenCalled();
    expect(defaultProps.onSetDefaultAdversarialTarget).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "New Target" })).toHaveFocus());
  });

  it("should leave the registry unchanged when deletion is cancelled", async () => {
    const user = userEvent.setup();
    const manual = { ...sampleTargets[1], can_delete: true };
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [manual], pagination: { limit: 200, has_more: false },
    });
    render(<TestWrapper><TargetConfig {...defaultProps} /></TestWrapper>);
    await openDeleteDialog(user, manual.target_registry_name);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(mockedTargetsApi.deleteTarget).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: `Actions for ${manual.target_registry_name}` })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: `Actions for ${manual.target_registry_name}` })).toHaveFocus());
  });

  it.each([204, 404])("should restore the empty state and focus after deleting the last target (%s)", async (status: number) => {
    const user = userEvent.setup();
    const manual = { ...sampleTargets[0], can_delete: true };
    mockedTargetsApi.listTargets.mockResolvedValueOnce({
      items: [manual], pagination: { limit: 200, has_more: false },
    }).mockResolvedValue({ items: [], pagination: { limit: 200, has_more: false } });
    if (status === 404) {
      mockedTargetsApi.deleteTarget.mockRejectedValueOnce({
        isAxiosError: true, response: { status: 404, data: { detail: "Target not found" } },
      });
    } else {
      mockedTargetsApi.deleteTarget.mockResolvedValueOnce(undefined);
    }
    render(<TestWrapper><TargetConfig {...defaultProps} /></TestWrapper>);
    const trigger = await screen.findByRole("button", { name: `Actions for ${manual.target_registry_name}` });
    trigger.focus();
    await user.keyboard("{Enter}{End}{Enter}");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(new RegExp(manual.target_registry_name))).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus());
    await user.tab();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("No Targets Configured")).toBeInTheDocument();
    expect(screen.queryByText("Target not found")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "New Target" })).toHaveFocus());
  });

  it("should use shared registry updates when another client removes a target", async () => {
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: sampleTargets, pagination: { limit: 200, has_more: false },
    });
    const { rerender } = render(
      <TestWrapper><TargetConfig {...defaultProps} registeredTargets={sampleTargets} /></TestWrapper>
    );
    await screen.findByRole("table");
    rerender(<TestWrapper><TargetConfig {...defaultProps} registeredTargets={[]} /></TestWrapper>);
    expect(screen.getByText("No Targets Configured")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("should show the in-use error and retain the target", async () => {
    const user = userEvent.setup();
    const manual = { ...sampleTargets[1], can_delete: true };
    mockedTargetsApi.listTargets.mockResolvedValue({
      items: [manual], pagination: { limit: 200, has_more: false },
    });
    mockedTargetsApi.deleteTarget.mockRejectedValueOnce(new Error("Target is in use by queued work."));
    const onTargetsLoaded = jest.fn();
    render(<TestWrapper><TargetConfig {...defaultProps} onTargetsLoaded={onTargetsLoaded} /></TestWrapper>);
    await openDeleteDialog(user, manual.target_registry_name);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete target" }));
    expect(await screen.findByText("Target is in use by queued work.")).toBeInTheDocument();
    expect(onTargetsLoaded).toHaveBeenCalledTimes(1);
    expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete target" })).toBeEnabled();
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

});

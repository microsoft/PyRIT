/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import App from "./App";
import { ThemeProvider } from "./hooks/useTheme";

import { attacksApi, targetsApi } from "./services/api";
import { makeTarget } from "./test-utils/targetFixtures";

const mockGetActiveAccount = jest.fn();

// Mock react-joyride to prevent the guided tour from interfering with App tests.
// The Joyride component is rendered as a no-op div, avoiding uncontrolled state
// updates from the tour's auto-start logic.
jest.mock("react-joyride", () => ({
  __esModule: true,
  default: () => <div data-testid="joyride-mock" />,
  Joyride: () => <div data-testid="joyride-mock" />,
  ACTIONS: { NEXT: "next", PREV: "prev", CLOSE: "close" },
  LIFECYCLE: { COMPLETE: "complete", READY: "ready" },
  STATUS: { RUNNING: "running", FINISHED: "finished", SKIPPED: "skipped" },
}));

// Mock useTour to prevent the auto-start tour from triggering state updates
// that race with async label initialization.
jest.mock("./hooks/useTour", () => ({
  useTour: () => ({
    startTour: jest.fn(),
    tourProps: {
      steps: [],
      run: false,
      stepIndex: 0,
      onEvent: jest.fn(),
      continuous: true,
      showSkipButton: true,
      tooltipComponent: () => null,
      floatingOptions: { hideArrow: true },
      options: { blockTargetInteraction: false, closeButtonAction: "skip", overlayClickAction: false },
      locale: { back: "Back", close: "Close", last: "Let's go!", next: "Next", skip: "Skip tour" },
    },
  }),
}));

// Mock MSAL — App uses useMsal() to wire the instance into the API client
jest.mock("@azure/msal-react", () => ({
  useMsal: () => ({ instance: { getActiveAccount: mockGetActiveAccount, getAllAccounts: () => [] } }),
}));

jest.mock("./services/api", () => ({
  attacksApi: {
    getAttack: jest.fn(),
    listAttacks: jest.fn(),
    createAttack: jest.fn(),
    deleteAttack: jest.fn(),
  },
  targetsApi: {
    listTargets: jest.fn(),
    getTarget: jest.fn(),
  },
  versionApi: {
    getVersion: jest.fn().mockResolvedValue({ version: "1.0.0" }),
  },
  authApi: {
    getAccess: jest.fn().mockResolvedValue({ isAdmin: true }),
  },
  setMsalInstance: jest.fn(),
}));

const mockedVersionApi = jest.requireMock("./services/api").versionApi;

const mockGetAttack = attacksApi.getAttack as jest.Mock;
const mockListTargets = targetsApi.listTargets as jest.Mock;
const mockGetTarget = targetsApi.getTarget as jest.Mock;

// Mock the child components to isolate App logic
jest.mock("./components/Labels/LabelsBar", () => {
  const MockLabelsBar = () => <div data-testid="labels-bar" />;
  MockLabelsBar.displayName = "MockLabelsBar";
  return {
    __esModule: true,
    default: MockLabelsBar,
    DEFAULT_GLOBAL_LABELS: { operator: 'roakey', operation: 'op_trash_panda' },
  };
});

jest.mock("./components/Layout/MainLayout", () => {
  const MockMainLayout = ({
    children,
    currentView,
    onNavigate,
  }: {
    children: React.ReactNode;
    currentView: string;
    onNavigate: (view: string) => void;
  }) => {
    return (
      <div data-testid="main-layout" data-current-view={currentView}>
        <button onClick={() => onNavigate("home")} data-testid="nav-home">
          Home
        </button>
        <button onClick={() => onNavigate("targets")} data-testid="nav-config">
          Config
        </button>
        <button onClick={() => onNavigate("chat")} data-testid="nav-chat">
          Chat
        </button>
        <button onClick={() => onNavigate("history")} data-testid="nav-history">
          History
        </button>
        {children}
      </div>
    );
  };
  MockMainLayout.displayName = "MockMainLayout";
  return {
    __esModule: true,
    default: MockMainLayout,
  };
});

jest.mock("./components/Chat/ChatWindow", () => {
  const MockChatWindow = ({
    onNewAttack,
    activeTarget,
    attackResultId,
    conversationId,
    activeConversationId,
    attackTarget,
    objective,
    targetResolutionStatus,
    onRetryTargetResolution,
    onConversationCreated,
    onSelectConversation,
    labels,
  }: {
    onNewAttack: () => void;
    activeTarget: unknown;
    attackResultId: string | null;
    conversationId: string | null;
    activeConversationId: string | null;
    attackTarget?: { identifier_hash?: string | null } | null;
    objective?: string;
    targetResolutionStatus?: string;
    onRetryTargetResolution?: () => void;
    onConversationCreated: (attackResultId: string, conversationId: string) => void;
    onSelectConversation: (convId: string) => void;
    labels: Record<string, string>;
  }) => {
    return (
      <div data-testid="chat-window">
        <span data-testid="attack-result-id">{attackResultId ?? "none"}</span>
        <span data-testid="conversation-id">{conversationId ?? "none"}</span>
        <span data-testid="active-conversation-id">{activeConversationId ?? "none"}</span>
        <span data-testid="has-target">{activeTarget ? "yes" : "no"}</span>
        <span data-testid="active-target-name">
          {(activeTarget as { target_registry_name?: string } | null)?.target_registry_name ?? "none"}
        </span>
        <span data-testid="attack-target-hash">{attackTarget?.identifier_hash ?? "none"}</span>
        <span data-testid="objective">{objective ?? ""}</span>
        <span data-testid="target-resolution-status">{targetResolutionStatus ?? "none"}</span>
        <span data-testid="labels-operator">{labels.operator ?? ""}</span>
        <span data-testid="labels-json">{JSON.stringify(labels)}</span>
        <button onClick={onNewAttack} data-testid="new-attack">
          New Attack
        </button>
        <button
          onClick={() => onConversationCreated("ar-123", "conv-123")}
          data-testid="set-conversation"
        >
          Set Conv
        </button>
        <button
          onClick={() => onSelectConversation("conv-456")}
          data-testid="select-conversation"
        >
          Select Conv
        </button>
        {onRetryTargetResolution && (
          <button onClick={onRetryTargetResolution} data-testid="retry-target-resolution">
            Retry target resolution
          </button>
        )}
      </div>
    );
  };
  MockChatWindow.displayName = "MockChatWindow";
  return {
    __esModule: true,
    default: MockChatWindow,
  };
});

jest.mock("./components/Config/TargetConfig", () => {
  const { makeTarget } = jest.requireActual("@/test-utils/targetFixtures") as typeof import("@/test-utils/targetFixtures");
  const MockTargetConfig = ({
    activeTarget,
    onSetActiveTarget,
  }: {
    activeTarget: unknown;
    onSetActiveTarget: (t: unknown) => void;
  }) => {
    return (
      <div data-testid="target-config">
        <span data-testid="active-target-name">
          {(activeTarget as { target_registry_name?: string })?.target_registry_name ?? "none"}
        </span>
        <button
          onClick={() =>
            onSetActiveTarget(makeTarget({
              target_registry_name: "test_target",
              target_type: "OpenAIChatTarget",
              identifier_hash: "test-target-hash",
            }))
          }
          data-testid="set-target"
        >
          Set Target
        </button>
      </div>
    );
  };
  MockTargetConfig.displayName = "MockTargetConfig";
  return {
    __esModule: true,
    default: MockTargetConfig,
  };
});

jest.mock("./components/Configuration/Configuration", () => ({
  __esModule: true,
  default: () => <div data-testid="configuration">Configuration</div>,
}));

jest.mock("./components/History/AttackHistory", () => {
  const MockAttackHistory = ({
    onOpenAttack,
    filters,
    onFiltersChange,
    activeTarget,
    onNavigate,
  }: {
    onOpenAttack: (attackResultId: string) => void;
    filters: Record<string, unknown>;
    onFiltersChange: (filters: Record<string, unknown>) => void;
    activeTarget: unknown;
    onNavigate: (view: string) => void;
  }) => {
    return (
      <div data-testid="attack-history">
        <span data-testid="history-filters">{JSON.stringify(filters)}</span>
        <span data-testid="history-has-target">{activeTarget ? "yes" : "no"}</span>
        {activeTarget ? (
          <button onClick={() => onNavigate("chat")} data-testid="history-start-attack">
            Start attack
          </button>
        ) : (
          <button onClick={() => onNavigate("targets")} data-testid="history-configure-target">
            Configure target
          </button>
        )}
        <button
          onClick={() => onOpenAttack("ar-attack-1")}
          data-testid="open-attack"
        >
          Open Attack
        </button>
        <button
          onClick={() => onOpenAttack("ar-attack-2")}
          data-testid="open-attack-2"
        >
          Open Attack 2
        </button>
        <button
          onClick={() => onFiltersChange({ ...filters, outcome: "success" })}
          data-testid="set-outcome-filter"
        >
          Filter Outcome
        </button>
      </div>
    );
  };
  MockAttackHistory.displayName = "MockAttackHistory";
  return {
    __esModule: true,
    default: MockAttackHistory,
  };
});

jest.mock("./components/Home/Home", () => {
  const MockHome = ({
    activeTarget,
    onNavigate,
    onOpenAttack,
    labels,
  }: {
    activeTarget: unknown;
    onNavigate: (view: string) => void;
    onOpenAttack: (attackResultId: string) => void;
    labels: Record<string, string>;
  }) => {
    return (
      <div data-testid="home-view">
        <span data-testid="home-has-target">{activeTarget ? "yes" : "no"}</span>
        <span data-testid="home-labels-json">{JSON.stringify(labels)}</span>
        <button onClick={() => onNavigate("targets")} data-testid="home-go-config">
          Go to config
        </button>
        <button
          onClick={() => onOpenAttack("ar-home-attack")}
          data-testid="home-open-attack"
        >
          Open Home Attack
        </button>
      </div>
    );
  };
  MockHome.displayName = "MockHome";
  return {
    __esModule: true,
    default: MockHome,
  };
});

describe("App", () => {
  // App reads the active view from the URL, so every render needs a router.
  // initialPath lets a test deep-link straight to a view (e.g. "/targets").
  function renderApp(initialPath = "/") {
    return render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[initialPath]}>
          <App />
        </MemoryRouter>
      </ThemeProvider>
    );
  }

  beforeEach(() => {
    jest.clearAllMocks();
    mockGetActiveAccount.mockReturnValue(null);
    mockListTargets.mockResolvedValue({
      items: [],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });
    mockGetTarget.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });
    window.localStorage.clear();
  });

  it("renders with FluentProvider and MainLayout", () => {
    renderApp();
    expect(screen.getByTestId("main-layout")).toBeInTheDocument();
    expect(screen.getByTestId("home-view")).toBeInTheDocument();
  });

  it("starts in home view", () => {
    renderApp();

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "home"
    );
    expect(screen.getByTestId("home-view")).toBeInTheDocument();
  });

  it("renders the view named by the initial URL", () => {
    renderApp("/targets");

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "targets"
    );
    expect(screen.getByTestId("target-config")).toBeInTheDocument();
  });

  it("renders configuration at /config", () => {
    renderApp("/config");

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "configuration"
    );
    expect(screen.getByTestId("configuration")).toBeInTheDocument();
  });

  it("renders the history view when deep-linked to /history", () => {
    renderApp("/history");

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "history"
    );
    expect(screen.getByTestId("attack-history")).toBeInTheDocument();
  });

  it("redirects an unknown path back to home", () => {
    renderApp("/does-not-exist");

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "home"
    );
    expect(screen.getByTestId("home-view")).toBeInTheDocument();
  });

  it("switches to chat view", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-chat"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "chat"
    );
    expect(screen.getByTestId("chat-window")).toBeInTheDocument();
  });

  it("switches to targets view", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-config"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "targets"
    );
    expect(screen.getByTestId("target-config")).toBeInTheDocument();
  });

  it("switches back to chat from targets", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-config"));
    expect(screen.getByTestId("target-config")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("nav-chat"));
    expect(screen.getByTestId("chat-window")).toBeInTheDocument();
  });

  it("sets conversationId from chat window", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-chat"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("none");

    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");
  });

  it("retains and trusts the active target when creating an attack", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-config"));
    fireEvent.click(screen.getByTestId("set-target"));
    fireEvent.click(screen.getByTestId("nav-chat"));
    fireEvent.click(screen.getByTestId("set-conversation"));

    expect(screen.getByTestId("attack-target-hash")).toHaveTextContent("test-target-hash");
    expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("resolved");
    expect(mockListTargets).not.toHaveBeenCalled();
  });

  it("retains a route-resolved target when branching to a new attack", async () => {
    const resolvedTarget = makeTarget({
      target_registry_name: "branch-target",
      identifier_hash: "branch-target-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-source",
      conversation_id: "conv-source",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        identifier_hash: "branch-target-hash",
      },
    });
    mockListTargets.mockResolvedValue({
      items: [resolvedTarget],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });

    renderApp("/attacks/ar-source");
    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("branch-target")
    );

    fireEvent.click(screen.getByTestId("set-conversation"));

    expect(screen.getByTestId("active-target-name")).toHaveTextContent("branch-target");
    expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("resolved");
    expect(mockListTargets).toHaveBeenCalledTimes(1);
  });

  it("clears conversationId on new attack", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-chat"));
    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");

    fireEvent.click(screen.getByTestId("new-attack"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("none");
  });

  it("sets active target from targets page and passes to chat", () => {
    renderApp();

    // Switch to chat and confirm no target initially
    fireEvent.click(screen.getByTestId("nav-chat"));
    expect(screen.getByTestId("has-target")).toHaveTextContent("no");

    // Switch to targets and set target
    fireEvent.click(screen.getByTestId("nav-config"));
    fireEvent.click(screen.getByTestId("set-target"));

    // Switch back to chat — target should be present
    fireEvent.click(screen.getByTestId("nav-chat"));
    expect(screen.getByTestId("has-target")).toHaveTextContent("yes");
  });

  it("switches to history view", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-history"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "history"
    );
    expect(screen.getByTestId("attack-history")).toBeInTheDocument();
  });

  it("navigates from empty history to targets when no target is active", () => {
    renderApp("/history");

    expect(screen.getByTestId("history-has-target")).toHaveTextContent("no");
    fireEvent.click(screen.getByTestId("history-configure-target"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "targets");
    expect(screen.getByTestId("target-config")).toBeInTheDocument();
  });

  it("navigates from empty history to chat when a target is active", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-config"));
    fireEvent.click(screen.getByTestId("set-target"));
    fireEvent.click(screen.getByTestId("nav-history"));

    expect(screen.getByTestId("history-has-target")).toHaveTextContent("yes");
    fireEvent.click(screen.getByTestId("history-start-attack"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "chat");
    expect(screen.getByTestId("chat-window")).toBeInTheDocument();
  });

  it("opens attack from history and switches to chat", async () => {
    mockGetAttack.mockResolvedValue({ attack_result_id: "ar-attack-1", conversation_id: "attack-conv-1", labels: { operator: "roakey" } });
    renderApp();

    fireEvent.click(screen.getByTestId("nav-history"));
    fireEvent.click(screen.getByTestId("open-attack"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "chat"
    );
    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledWith("ar-attack-1"));
    await waitFor(() => expect(screen.getByTestId("conversation-id")).toHaveTextContent("attack-conv-1"));
  });

  it("opens attack from home and switches to chat", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-home-attack",
      conversation_id: "home-conv-1",
      labels: { operator: "roakey" },
    });
    renderApp();

    fireEvent.click(screen.getByTestId("home-open-attack"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "chat"
    );
    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledWith("ar-home-attack"));
    await waitFor(() => expect(screen.getByTestId("conversation-id")).toHaveTextContent("home-conv-1"));
  });

  it("navigates to targets from the home view", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("home-go-config"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "targets"
    );
    expect(screen.getByTestId("target-config")).toBeInTheDocument();
  });

  it("shows the not-found UX when an attack returns 404", async () => {
    mockGetAttack.mockRejectedValue({ isAxiosError: true, response: { status: 404, data: {} } });
    renderApp();

    fireEvent.click(screen.getByTestId("nav-history"));
    fireEvent.click(screen.getByTestId("open-attack"));

    // Should switch to chat view even on error
    expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "chat");
    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledWith("ar-attack-1"));
    // The chat window is replaced by an inline "attack not found" message
    await waitFor(() => expect(screen.getByTestId("attack-not-found")).toBeInTheDocument());
    expect(screen.queryByTestId("chat-window")).not.toBeInTheDocument();
  });

  it("shows the error UX (not not-found) when an attack load fails with a non-404", async () => {
    // A 500 / network / timeout is transient and must not claim the attack was deleted.
    mockGetAttack.mockRejectedValue({ isAxiosError: true, response: { status: 500, data: {} } });
    renderApp();

    fireEvent.click(screen.getByTestId("nav-history"));
    fireEvent.click(screen.getByTestId("open-attack"));

    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledWith("ar-attack-1"));
    await waitFor(() => expect(screen.getByTestId("attack-load-error")).toBeInTheDocument());
    expect(screen.queryByTestId("attack-not-found")).not.toBeInTheDocument();
    expect(screen.queryByTestId("chat-window")).not.toBeInTheDocument();
  });

  it("clears activeConversationId synchronously before fetching a new attack", async () => {
    // Repro: in attack A the user branched into a related conversation, so
    // activeConversationId points to a conv that does NOT belong to attack B.
    // When the user clicks Open Attack on B, App.tsx must clear the stale
    // conv id *before* flipping attackResultId — otherwise ChatWindow renders
    // with (attackResultId=B, activeConversationId=A_conv) during the in-flight
    // getAttack and issues GET /messages?conversation_id=A_conv → 400.

    // Defer getAttack so we can inspect the intermediate render before it resolves.
    let resolveGetAttack: (value: unknown) => void = () => {};
    mockGetAttack.mockImplementation(
      () => new Promise((resolve) => { resolveGetAttack = resolve })
    );

    renderApp();

    // Simulate: user is already on attack A with a branched conv selected.
    fireEvent.click(screen.getByTestId("nav-chat"));
    fireEvent.click(screen.getByTestId("set-conversation"));      // attack A, main conv-123
    // Resolve the (unrelated) getAttack triggered earlier to keep state quiet
    // — actually nothing called it yet because set-conversation routes through
    // onConversationCreated, not handleOpenAttack. Proceed.
    fireEvent.click(screen.getByTestId("select-conversation"));   // branched conv-456 in attack A
    expect(screen.getByTestId("attack-result-id")).toHaveTextContent("ar-123");
    expect(screen.getByTestId("active-conversation-id")).toHaveTextContent("conv-456");

    // User clicks Open Attack on attack B in history.
    fireEvent.click(screen.getByTestId("nav-history"));
    fireEvent.click(screen.getByTestId("open-attack-2"));        // ar-attack-2

    // BEFORE getAttack resolves: ChatWindow must NOT see the stale conv id
    // alongside the new attack id. While attack B loads, its data is not yet
    // ready, so both the attack id and conversation id are withheld — which
    // gates ChatWindow's /messages fetch and prevents the cross-attack 400.
    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "chat"
    );
    expect(screen.getByTestId("attack-result-id")).toHaveTextContent("none");
    expect(screen.getByTestId("active-conversation-id")).toHaveTextContent("none");
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("none");

    // After getAttack resolves: the conv id belonging to attack B is committed.
    resolveGetAttack({
      attack_result_id: "ar-attack-2",
      conversation_id: "attack-conv-2",
      labels: {},
    });
    await waitFor(() =>
      expect(screen.getByTestId("active-conversation-id")).toHaveTextContent("attack-conv-2")
    );
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("attack-conv-2");
  });

  it("merges default labels from backend version API", async () => {
    mockedVersionApi.getVersion.mockResolvedValueOnce({
      version: "2.0.0",
      default_labels: { operator: "default_user", custom: "value" },
    });

    renderApp();

    // The version API is called on mount and labels get merged
    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });

    // Switch to chat to inspect labels
    fireEvent.click(screen.getByTestId("nav-chat"));

    await waitFor(() => {
      expect(screen.getByTestId("labels-operator")).toHaveTextContent("default_user");
      expect(screen.getByTestId("labels-json")).toHaveTextContent('"custom":"value"');
    });
  });

  it("sets operator label from active account alias when backend has no operator", async () => {
    mockGetActiveAccount.mockReturnValue({ username: "Test.User@contoso.com" });
    mockedVersionApi.getVersion.mockResolvedValueOnce({
      version: "2.0.0",
      default_labels: { custom: "value" },
    });

    renderApp();

    // Home receives the same labels prop — assert there to avoid racing the
    // async initLabels effect against a view-change re-render.
    await waitFor(() => {
      const labels = screen.getByTestId("home-labels-json").textContent ?? "";
      expect(labels).toContain('"operator":"test.user"');
      expect(labels).toContain('"custom":"value"');
    });
  });

  it("prefers active account alias over backend operator when both are provided", async () => {
    mockGetActiveAccount.mockReturnValue({ username: "override_user@contoso.com" });
    mockedVersionApi.getVersion.mockResolvedValueOnce({
      version: "2.0.0",
      default_labels: { operator: "backend_user", custom: "value" },
    });

    renderApp();

    await waitFor(() => {
      const labels = screen.getByTestId("home-labels-json").textContent ?? "";
      expect(labels).toContain('"operator":"override_user"');
      expect(labels).toContain('"custom":"value"');
    });
  });

  it("prefers the labels you last picked over the backend defaults", async () => {
    window.localStorage.setItem(
      "pyrit.globalLabels",
      JSON.stringify({ operator: "roakey", operation: "op_i_picked" }),
    );
    mockedVersionApi.getVersion.mockResolvedValueOnce({
      version: "2.0.0",
      default_labels: { operation: "op_from_backend", custom: "value" },
    });

    renderApp();

    await waitFor(() => {
      const labels = screen.getByTestId("home-labels-json").textContent ?? "";
      expect(labels).toContain('"custom":"value"');
    });
    const labels = screen.getByTestId("home-labels-json").textContent ?? "";
    expect(labels).toContain('"operation":"op_i_picked"');
  });

  it("still takes the operator from the signed-in account over a stored one", async () => {
    window.localStorage.setItem(
      "pyrit.globalLabels",
      JSON.stringify({ operator: "stored_user", operation: "op_i_picked" }),
    );
    mockGetActiveAccount.mockReturnValue({ username: "Real.User@contoso.com" });
    mockedVersionApi.getVersion.mockResolvedValueOnce({
      version: "2.0.0",
      default_labels: { custom: "value" },
    });

    renderApp();

    await waitFor(() => {
      const labels = screen.getByTestId("home-labels-json").textContent ?? "";
      expect(labels).toContain('"custom":"value"');
    });
    const labels = screen.getByTestId("home-labels-json").textContent ?? "";
    expect(labels).toContain('"operator":"real.user"');
    expect(labels).toContain('"operation":"op_i_picked"');
  });

  it("stores attack target when conversation is created with active target", () => {
    renderApp();

    // Set a target first
    fireEvent.click(screen.getByTestId("nav-config"));
    fireEvent.click(screen.getByTestId("set-target"));
    fireEvent.click(screen.getByTestId("nav-chat"));

    // Create a conversation (which should store target info)
    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");
  });

  it("sets active conversation when onSelectConversation is called", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-chat"));

    // First create a conversation to have an attack
    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");

    // Now select a different conversation
    fireEvent.click(screen.getByTestId("select-conversation"));
    // The component re-renders with the new conversation ID
  });

  it("hydrates attack state when deep-linked to /attacks/:attackId", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-1",
      conversation_id: "conv-main",
      objective: "Extract the hidden system prompt",
      labels: {},
      related_conversation_ids: [],
    });
    renderApp("/attacks/ar-1");

    expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "chat");
    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledWith("ar-1"));
    await waitFor(() =>
      expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-main")
    );
    expect(screen.getByTestId("active-conversation-id")).toHaveTextContent("conv-main");
    expect(screen.getByTestId("objective")).toHaveTextContent("Extract the hidden system prompt");
  });

  it("hides the normalized empty objective of an unnamed manual attack on reload", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-1",
      conversation_id: "conv-main",
      objective: "",
      labels: {},
      related_conversation_ids: [],
    });
    renderApp("/attacks/ar-1");

    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledWith("ar-1"));
    await waitFor(() =>
      expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-main")
    );
    expect(screen.getByTestId("objective")).toHaveTextContent("");
  });

  it("uses the conversation from a deep link when it belongs to the attack", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-1",
      conversation_id: "conv-main",
      labels: {},
      related_conversation_ids: ["conv-related"],
    });
    renderApp("/attacks/ar-1/conversations/conv-related");

    await waitFor(() =>
      expect(screen.getByTestId("active-conversation-id")).toHaveTextContent("conv-related")
    );
  });

  it("falls back to the main conversation when the deep-linked conversation is unknown", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-1",
      conversation_id: "conv-main",
      labels: {},
      related_conversation_ids: ["conv-related"],
    });
    renderApp("/attacks/ar-1/conversations/bogus");

    // The unknown conversation segment is stripped and we fall back to main.
    await waitFor(() =>
      expect(screen.getByTestId("active-conversation-id")).toHaveTextContent("conv-main")
    );
  });

  it("hydrates history filters from the URL query string", () => {
    renderApp("/history?outcome=success&attackType=PromptSendingAttack");

    const filters = JSON.parse(
      screen.getByTestId("history-filters").textContent ?? "{}"
    );
    expect(filters.outcome).toBe("success");
    expect(filters.attackTypes).toEqual(["PromptSendingAttack"]);
  });

  it("writes filter changes into the URL", () => {
    renderApp("/history");

    expect(
      JSON.parse(screen.getByTestId("history-filters").textContent ?? "{}").outcome
    ).toBe("");

    fireEvent.click(screen.getByTestId("set-outcome-filter"));

    // The change flows out to the URL and back into the derived filters prop.
    expect(
      JSON.parse(screen.getByTestId("history-filters").textContent ?? "{}").outcome
    ).toBe("success");
  });

  it("restores history filters when returning via the nav button", () => {
    renderApp("/history?outcome=success");

    expect(
      JSON.parse(screen.getByTestId("history-filters").textContent ?? "{}").outcome
    ).toBe("success");

    // Leave history for another view, then come back via the nav button.
    fireEvent.click(screen.getByTestId("nav-config"));
    expect(screen.getByTestId("target-config")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("nav-history"));
    expect(
      JSON.parse(screen.getByTestId("history-filters").textContent ?? "{}").outcome
    ).toBe("success");
  });

  it("restores the exact registered target from a direct attack URL across registry pages", async () => {
    const nearDuplicate = makeTarget({
      target_registry_name: "near-duplicate",
      target_type: "OpenAIChatTarget",
      endpoint: "https://example.test",
      model_name: "gpt-test",
      identifier_hash: "persisted-full-hash-near",
    });
    const exactTarget = makeTarget({
      target_registry_name: "exact-target",
      target_type: "OpenAIChatTarget",
      endpoint: "https://example.test",
      model_name: "gpt-test",
      identifier_hash: "persisted-full-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-direct",
      conversation_id: "conv-direct",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "OpenAIChatTarget",
        endpoint: "https://example.test",
        model_name: "gpt-test",
        identifier_hash: "persisted-full-hash",
      },
    });
    mockListTargets
      .mockResolvedValueOnce({
        items: [nearDuplicate],
        pagination: { limit: 200, has_more: true, next_cursor: "page-2" },
      })
      .mockResolvedValueOnce({
        items: [exactTarget],
        pagination: { limit: 200, has_more: false, next_cursor: null },
      });

    renderApp("/attacks/ar-direct");

    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("exact-target")
    );
    expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("resolved");
    expect(mockListTargets).toHaveBeenNthCalledWith(1, 200, undefined);
    expect(mockListTargets).toHaveBeenNthCalledWith(2, 200, "page-2");
  });

  it("restores a named target directly after validating its full hash", async () => {
    const exactTarget = makeTarget({
      target_registry_name: "persisted-alias",
      identifier_hash: "persisted-alias-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-named",
      conversation_id: "conv-named",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        target_registry_name: "persisted-alias",
        identifier_hash: "persisted-alias-hash",
      },
    });
    mockGetTarget.mockResolvedValue(exactTarget);

    renderApp("/attacks/ar-named");

    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("persisted-alias")
    );
    expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("resolved");
    expect(mockGetTarget).toHaveBeenCalledWith("persisted-alias");
    expect(mockListTargets).not.toHaveBeenCalled();
  });

  it("falls back to full-hash resolution when a persisted alias points to a different target", async () => {
    const staleAliasTarget = makeTarget({
      target_registry_name: "reused-alias",
      identifier_hash: "different-hash",
    });
    const renamedTarget = makeTarget({
      target_registry_name: "renamed-target",
      identifier_hash: "original-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-reused-alias",
      conversation_id: "conv-reused-alias",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        target_registry_name: "reused-alias",
        identifier_hash: "original-hash",
      },
    });
    mockGetTarget.mockResolvedValue(staleAliasTarget);
    mockListTargets.mockResolvedValue({
      items: [staleAliasTarget, renamedTarget],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });

    renderApp("/attacks/ar-reused-alias");

    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("renamed-target")
    );
    expect(mockGetTarget).toHaveBeenCalledWith("reused-alias");
    expect(mockListTargets).toHaveBeenCalledWith(200, undefined);
  });

  it("falls back to full-hash resolution when a persisted alias was removed", async () => {
    const renamedTarget = makeTarget({
      target_registry_name: "renamed-target",
      identifier_hash: "original-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-removed-alias",
      conversation_id: "conv-removed-alias",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        target_registry_name: "removed-alias",
        identifier_hash: "original-hash",
      },
    });
    mockListTargets.mockResolvedValue({
      items: [renamedTarget],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });

    renderApp("/attacks/ar-removed-alias");

    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("renamed-target")
    );
    expect(mockGetTarget).toHaveBeenCalledWith("removed-alias");
    expect(mockListTargets).toHaveBeenCalledWith(200, undefined);
  });

  it("falls back when a reserved alias returns a non-target response", async () => {
    const exactTarget = makeTarget({
      target_registry_name: "catalog",
      identifier_hash: "catalog-target-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-catalog-alias",
      conversation_id: "conv-catalog-alias",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        target_registry_name: "catalog",
        identifier_hash: "catalog-target-hash",
      },
    });
    mockGetTarget.mockResolvedValue({ items: [] });
    mockListTargets.mockResolvedValue({
      items: [exactTarget],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });

    renderApp("/attacks/ar-catalog-alias");

    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("catalog")
    );
    expect(mockGetTarget).toHaveBeenCalledWith("catalog");
    expect(mockListTargets).toHaveBeenCalledWith(200, undefined);
  });

  it("retries named-target registry failures without masking them with a list fallback", async () => {
    const exactTarget = makeTarget({
      target_registry_name: "persisted-alias",
      identifier_hash: "persisted-alias-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-named-error",
      conversation_id: "conv-named-error",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        target_registry_name: "persisted-alias",
        identifier_hash: "persisted-alias-hash",
      },
    });
    mockGetTarget
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 500 } })
      .mockResolvedValueOnce(exactTarget);
    const user = userEvent.setup();

    renderApp("/attacks/ar-named-error");

    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("error")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");
    expect(mockListTargets).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("retry-target-resolution"));

    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("persisted-alias")
    );
    expect(mockGetTarget).toHaveBeenCalledTimes(2);
    expect(mockListTargets).not.toHaveBeenCalled();
  });

  it("fails closed when registry pagination does not advance", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-stalled-pagination",
      conversation_id: "conv-stalled-pagination",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        identifier_hash: "pagination-hash",
      },
    });
    mockListTargets
      .mockResolvedValueOnce({
        items: [],
        pagination: { limit: 200, has_more: true, next_cursor: "same-cursor" },
      })
      .mockResolvedValueOnce({
        items: [],
        pagination: { limit: 200, has_more: true, next_cursor: "same-cursor" },
      });

    renderApp("/attacks/ar-stalled-pagination");

    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("error")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");
  });

  it("restores the target again after an app remount", async () => {
    const exactTarget = makeTarget({
      target_registry_name: "remounted-target",
      identifier_hash: "remount-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-remount",
      conversation_id: "conv-remount",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        identifier_hash: "remount-hash",
      },
    });
    mockListTargets.mockResolvedValue({
      items: [exactTarget],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });

    const firstRender = renderApp("/attacks/ar-remount");
    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("remounted-target")
    );
    firstRender.unmount();

    renderApp("/attacks/ar-remount");
    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("remounted-target")
    );
    expect(mockListTargets).toHaveBeenCalledTimes(2);
  });

  it("revokes a restored target when it is removed before remount", async () => {
    const exactTarget = makeTarget({
      target_registry_name: "removed-target",
      identifier_hash: "removed-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-removed",
      conversation_id: "conv-removed",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        identifier_hash: "removed-hash",
      },
    });
    mockListTargets
      .mockResolvedValueOnce({
        items: [exactTarget],
        pagination: { limit: 200, has_more: false, next_cursor: null },
      })
      .mockResolvedValueOnce({
        items: [],
        pagination: { limit: 200, has_more: false, next_cursor: null },
      });

    const firstRender = renderApp("/attacks/ar-removed");
    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("removed-target")
    );
    firstRender.unmount();

    renderApp("/attacks/ar-removed");
    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("unavailable")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");
  });

  it("revalidates a previously resolved target when revisiting the same attack", async () => {
    const exactTarget = makeTarget({
      target_registry_name: "revisited-target",
      identifier_hash: "revisited-hash",
    });
    let resolveRevisitAttack: (value: unknown) => void = () => {};
    let resolveRevisit: (value: unknown) => void = () => {};
    const attack = {
      attack_result_id: "ar-attack-1",
      conversation_id: "conv-revisited",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        identifier_hash: "revisited-hash",
      },
    };
    mockGetAttack
      .mockResolvedValueOnce(attack)
      .mockImplementationOnce(
        () => new Promise((resolve) => {
          resolveRevisitAttack = resolve;
        })
      );
    mockListTargets
      .mockResolvedValueOnce({
        items: [exactTarget],
        pagination: { limit: 200, has_more: false, next_cursor: null },
      })
      .mockImplementationOnce(
        () => new Promise((resolve) => {
          resolveRevisit = resolve;
        })
      );
    const user = userEvent.setup();
    renderApp("/attacks/ar-attack-1");

    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("revisited-target")
    );
    await user.click(screen.getByTestId("nav-history"));
    await user.click(screen.getByTestId("open-attack"));

    await waitFor(() =>
      expect(screen.getByTestId("attack-result-id")).toHaveTextContent("none")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");

    resolveRevisitAttack(attack);
    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("loading")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");

    resolveRevisit({
      items: [exactTarget],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });
    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("revisited-target")
    );
  });

  it("keeps a near-duplicate target read-only when its full hash differs", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-near",
      conversation_id: "conv-near",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "OpenAIChatTarget",
        endpoint: "https://example.test",
        model_name: "gpt-test",
        identifier_hash: "required-full-hash",
      },
    });
    mockListTargets.mockResolvedValue({
      items: [
        makeTarget({
          target_registry_name: "near-target",
          target_type: "OpenAIChatTarget",
          endpoint: "https://example.test",
          model_name: "gpt-test",
          identifier_hash: "different-full-hash",
        }),
      ],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });

    renderApp("/attacks/ar-near");

    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("unavailable")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");
  });

  it("keeps duplicate exact target identities read-only as ambiguous", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-ambiguous",
      conversation_id: "conv-ambiguous",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        identifier_hash: "duplicate-hash",
      },
    });
    mockListTargets.mockResolvedValue({
      items: [
        makeTarget({
          target_registry_name: "duplicate-a",
          identifier_hash: "duplicate-hash",
        }),
        makeTarget({
          target_registry_name: "duplicate-b",
          identifier_hash: "duplicate-hash",
        }),
      ],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });

    renderApp("/attacks/ar-ambiguous");

    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("ambiguous")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");
  });

  it("preserves an explicitly selected different target and reports a cross-target state", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-other-target",
      conversation_id: "conv-other-target",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        identifier_hash: "other-target-hash",
      },
    });
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByTestId("nav-config"));
    await user.click(screen.getByTestId("set-target"));
    await user.click(screen.getByTestId("nav-history"));
    await user.click(screen.getByTestId("open-attack"));

    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("explicit-mismatch")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("test_target");
    expect(mockListTargets).not.toHaveBeenCalled();
  });

  it("hash-validates an explicitly selected matching target against the registry", async () => {
    const selectedTarget = makeTarget({
      target_registry_name: "test_target",
      target_type: "OpenAIChatTarget",
      identifier_hash: "test-target-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-same-target",
      conversation_id: "conv-same-target",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "OpenAIChatTarget",
        identifier_hash: "test-target-hash",
      },
    });
    mockListTargets.mockResolvedValue({
      items: [selectedTarget],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByTestId("nav-config"));
    await user.click(screen.getByTestId("set-target"));
    await user.click(screen.getByTestId("nav-history"));
    await user.click(screen.getByTestId("open-attack"));

    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("resolved")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("test_target");
    expect(mockListTargets).toHaveBeenCalledWith(200, undefined);
  });

  it("preserves an explicitly selected alias with the same canonical hash", async () => {
    const persistedAliasTarget = makeTarget({
      target_registry_name: "persisted-alias",
      target_type: "OpenAIChatTarget",
      identifier_hash: "test-target-hash",
    });
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-same-identity",
      conversation_id: "conv-same-identity",
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "OpenAIChatTarget",
        target_registry_name: "persisted-alias",
        identifier_hash: "test-target-hash",
      },
    });
    mockGetTarget.mockResolvedValue(persistedAliasTarget);
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByTestId("nav-config"));
    await user.click(screen.getByTestId("set-target"));
    await user.click(screen.getByTestId("nav-history"));
    await user.click(screen.getByTestId("open-attack"));

    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("resolved")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("test_target");
    expect(mockGetTarget).toHaveBeenCalledWith("persisted-alias");
    expect(mockListTargets).not.toHaveBeenCalled();
  });

  it.each([401, 500])(
    "keeps the attack read-only after a registry %s and resolves it on retry",
    async (status: number) => {
      const exactTarget = makeTarget({
        target_registry_name: "retry-target",
        identifier_hash: "retry-hash",
      });
      mockGetAttack.mockResolvedValue({
        attack_result_id: "ar-retry",
        conversation_id: "conv-retry",
        labels: {},
        related_conversation_ids: [],
        target: {
          target_type: "TextTarget",
          identifier_hash: "retry-hash",
        },
      });
      mockListTargets
        .mockRejectedValueOnce({ isAxiosError: true, response: { status } })
        .mockResolvedValueOnce({
          items: [exactTarget],
          pagination: { limit: 200, has_more: false, next_cursor: null },
        });
      const user = userEvent.setup();
      renderApp("/attacks/ar-retry");

      await waitFor(() =>
        expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("error")
      );
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");

      await user.click(screen.getByTestId("retry-target-resolution"));

      await waitFor(() =>
        expect(screen.getByTestId("active-target-name")).toHaveTextContent("retry-target")
      );
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("resolved");
    }
  );

  it("ignores a stale target registry response after navigating to another attack", async () => {
    let resolveFirstRegistryPage: (value: unknown) => void = () => {};
    mockGetAttack.mockImplementation(async (attackId: string) => ({
      attack_result_id: attackId,
      conversation_id: `conv-${attackId}`,
      labels: {},
      related_conversation_ids: [],
      target: {
        target_type: "TextTarget",
        identifier_hash: `${attackId}-hash`,
      },
    }));
    mockListTargets
      .mockImplementationOnce(
        () => new Promise((resolve) => {
          resolveFirstRegistryPage = resolve;
        })
      )
      .mockResolvedValueOnce({
        items: [
          makeTarget({
            target_registry_name: "attack-2-target",
            identifier_hash: "ar-attack-2-hash",
          }),
        ],
        pagination: { limit: 200, has_more: false, next_cursor: null },
      });
    const user = userEvent.setup();
    renderApp("/history");

    await user.click(screen.getByTestId("open-attack"));
    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("loading")
    );
    await user.click(screen.getByTestId("nav-history"));
    await user.click(screen.getByTestId("open-attack-2"));
    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("attack-2-target")
    );

    resolveFirstRegistryPage({
      items: [
        makeTarget({
          target_registry_name: "stale-attack-1-target",
          identifier_hash: "ar-attack-1-hash",
        }),
      ],
      pagination: { limit: 200, has_more: false, next_cursor: null },
    });

    await waitFor(() =>
      expect(screen.getByTestId("active-target-name")).toHaveTextContent("attack-2-target")
    );
  });

  it("keeps legacy attacks without complete target metadata read-only", async () => {
    mockGetAttack.mockResolvedValue({
      attack_result_id: "ar-legacy",
      conversation_id: "conv-legacy",
      labels: {},
      related_conversation_ids: [],
      target: null,
    });

    renderApp("/attacks/ar-legacy");

    await waitFor(() =>
      expect(screen.getByTestId("target-resolution-status")).toHaveTextContent("legacy")
    );
    expect(screen.getByTestId("active-target-name")).toHaveTextContent("none");
    expect(mockListTargets).not.toHaveBeenCalled();
  });
});

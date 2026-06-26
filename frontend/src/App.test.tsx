/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import { ThemeProvider } from "./hooks/useTheme";

import { attacksApi } from "./services/api";

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
      spotlightClicks: false,
      tooltipComponent: () => null,
      floatingOptions: { hideArrow: true },
      options: { closeButtonAction: "skip", overlayClickAction: false },
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
  versionApi: {
    getVersion: jest.fn().mockResolvedValue({ version: "1.0.0" }),
  },
  setMsalInstance: jest.fn(),
}));

const mockedVersionApi = jest.requireMock("./services/api").versionApi;

const mockGetAttack = attacksApi.getAttack as jest.Mock;

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
        <button onClick={() => onNavigate("config")} data-testid="nav-config">
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
    onConversationCreated,
    onSelectConversation,
    labels,
  }: {
    onNewAttack: () => void;
    activeTarget: unknown;
    attackResultId: string | null;
    conversationId: string | null;
    activeConversationId: string | null;
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
            onSetActiveTarget({
              target_id: "t1",
              target_registry_name: "test_target",
              target_type: "OpenAIChatTarget",
              status: "active",
            })
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

jest.mock("./components/History/AttackHistory", () => {
  const MockAttackHistory = ({
    onOpenAttack,
    filters,
    onFiltersChange,
  }: {
    onOpenAttack: (attackResultId: string) => void;
    filters: Record<string, unknown>;
    onFiltersChange: (filters: Record<string, unknown>) => void;
  }) => {
    return (
      <div data-testid="attack-history">
        <span data-testid="history-filters">{JSON.stringify(filters)}</span>
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
        <button onClick={() => onNavigate("config")} data-testid="home-go-config">
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
  // initialPath lets a test deep-link straight to a view (e.g. "/config").
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
    renderApp("/config");

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "config"
    );
    expect(screen.getByTestId("target-config")).toBeInTheDocument();
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

  it("switches to config view", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-config"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "config"
    );
    expect(screen.getByTestId("target-config")).toBeInTheDocument();
  });

  it("switches back to chat from config", () => {
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

  it("clears conversationId on new attack", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("nav-chat"));
    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");

    fireEvent.click(screen.getByTestId("new-attack"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("none");
  });

  it("sets active target from config page and passes to chat", () => {
    renderApp();

    // Switch to chat and confirm no target initially
    fireEvent.click(screen.getByTestId("nav-chat"));
    expect(screen.getByTestId("has-target")).toHaveTextContent("no");

    // Switch to config and set target
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

  it("navigates to config from the home view", () => {
    renderApp();

    fireEvent.click(screen.getByTestId("home-go-config"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "config"
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
});

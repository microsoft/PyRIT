/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import App from "./App";
import { attacksApi } from "./services/api";

const mockGetActiveAccount = jest.fn();

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
    onToggleTheme,
    isDarkMode,
    currentView,
    onNavigate,
  }: {
    children: React.ReactNode;
    onToggleTheme: () => void;
    isDarkMode: boolean;
    currentView: string;
    onNavigate: (view: string) => void;
  }) => {
    return (
      <div data-testid="main-layout" data-dark-mode={isDarkMode} data-current-view={currentView}>
        <button onClick={onToggleTheme} data-testid="toggle-theme">
          Toggle Theme
        </button>
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
        <button onClick={() => onNavigate("tree")} data-testid="nav-tree">
          Tree
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
    onOpenAttackAsTree,
  }: {
    onOpenAttack: (attackResultId: string) => void;
    onOpenAttackAsTree?: (attackResultId: string) => void;
  }) => {
    return (
      <div data-testid="attack-history">
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
        {onOpenAttackAsTree && (
          <button
            onClick={() => onOpenAttackAsTree("ar-tree-1")}
            data-testid="open-attack-as-tree"
          >
            Open Attack As Tree
          </button>
        )}
      </div>
    );
  };
  MockAttackHistory.displayName = "MockAttackHistory";
  return {
    __esModule: true,
    default: MockAttackHistory,
  };
});

jest.mock("./components/Tree/TreeRunnerHost", () => {
  const React = jest.requireActual("react") as typeof import("react");
  const dirtyTree = {
    id: "dirty-tree",
    rootId: "root",
    nodes: [
      { id: "root", kind: "root_prompt", parentId: null, state: "clean", params: {} },
      { id: "send-1", kind: "send", parentId: "root", state: "edited", params: {} },
    ],
    parentConversationTreeId: null,
  };
  const hasDirty = (tree: { nodes?: Array<{ state?: string }> } | null) =>
    tree?.nodes?.some((node) => node.state === "edited" || node.state === "draft") === true;

  const MockTreeRunnerHost = ({
    tree,
    onTreeChange,
    onGuardedSwapReady,
    openFromAttackResultId,
  }: {
    tree: typeof dirtyTree | null;
    onTreeChange?: (tree: typeof dirtyTree) => void;
    onGuardedSwapReady?: (guardedSwap: (tree: typeof dirtyTree | null, swap: () => void) => void) => void;
    openFromAttackResultId?: string | null;
  }) => {
    const [pendingSwap, setPendingSwap] = React.useState<(() => void) | null>(null);
    const lastOpenedArIdRef = React.useRef<string | null>(null);

    const guardedSwap = React.useCallback((candidateTree: typeof dirtyTree | null, swap: () => void) => {
      if (hasDirty(candidateTree)) setPendingSwap(() => swap);
      else swap();
    }, []);

    React.useEffect(() => {
      onGuardedSwapReady?.(guardedSwap);
    }, [guardedSwap, onGuardedSwapReady]);

    React.useEffect(() => {
      if (!openFromAttackResultId) return;
      if (lastOpenedArIdRef.current === openFromAttackResultId) return;
      lastOpenedArIdRef.current = openFromAttackResultId;
      onTreeChange?.({ ...dirtyTree, id: `opened-${openFromAttackResultId}`, nodes: [dirtyTree.nodes[0]] });
    }, [openFromAttackResultId, onTreeChange]);

    return (
      <div data-testid="tree-runner-host" data-tree-id={tree?.id ?? "none"}>
        <button data-testid="make-dirty-tree" onClick={() => onTreeChange?.(dirtyTree)}>
          Make Dirty Tree
        </button>
        <span data-testid="open-from-ar-id">{openFromAttackResultId ?? "none"}</span>
        <span data-testid="tree-is-dirty">{hasDirty(tree) ? "yes" : "no"}</span>
        {pendingSwap && (
          <div role="dialog" aria-label="Discard unsaved edits?">
            <button onClick={() => setPendingSwap(null)}>Cancel</button>
            <button
              onClick={() => {
                const swap = pendingSwap;
                setPendingSwap(null);
                swap();
              }}
            >
              Discard and continue
            </button>
          </div>
        )}
      </div>
    );
  };
  MockTreeRunnerHost.displayName = "MockTreeRunnerHost";
  return { TreeRunnerHost: MockTreeRunnerHost };
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
  const originalTreeFlag = process.env.VITE_ENABLE_TREE_UI;

  beforeEach(() => {
    jest.clearAllMocks();
    mockGetActiveAccount.mockReturnValue(null);
    if (originalTreeFlag === undefined) delete process.env.VITE_ENABLE_TREE_UI;
    else process.env.VITE_ENABLE_TREE_UI = originalTreeFlag;
    window.history.replaceState(window.history.state, "", "/");
  });

  it("renders with FluentProvider and MainLayout", () => {
    render(<App />);
    expect(screen.getByTestId("main-layout")).toBeInTheDocument();
    expect(screen.getByTestId("home-view")).toBeInTheDocument();
  });

  it("starts in dark mode", () => {
    render(<App />);
    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-dark-mode",
      "true"
    );
  });

  it("toggles theme when onToggleTheme is called", () => {
    render(<App />);

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-dark-mode",
      "true"
    );

    fireEvent.click(screen.getByTestId("toggle-theme"));
    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-dark-mode",
      "false"
    );

    fireEvent.click(screen.getByTestId("toggle-theme"));
    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-dark-mode",
      "true"
    );
  });

  it("starts in home view", () => {
    render(<App />);

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "home"
    );
    expect(screen.getByTestId("home-view")).toBeInTheDocument();
  });

  it("switches to chat view", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-chat"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "chat"
    );
    expect(screen.getByTestId("chat-window")).toBeInTheDocument();
  });

  it("switches to config view", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-config"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "config"
    );
    expect(screen.getByTestId("target-config")).toBeInTheDocument();
  });

  it("switches back to chat from config", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-config"));
    expect(screen.getByTestId("target-config")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("nav-chat"));
    expect(screen.getByTestId("chat-window")).toBeInTheDocument();
  });

  it("sets conversationId from chat window", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-chat"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("none");

    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");
  });

  it("clears conversationId on new attack", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-chat"));
    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");

    fireEvent.click(screen.getByTestId("new-attack"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("none");
  });

  it("sets active target from config page and passes to chat", () => {
    render(<App />);

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
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-history"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "history"
    );
    expect(screen.getByTestId("attack-history")).toBeInTheDocument();
  });

  it("guards Open as tree from History when the current tree has unsaved edits", async () => {
    process.env.VITE_ENABLE_TREE_UI = "true";
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-tree"));
    fireEvent.click(screen.getByTestId("make-dirty-tree"));
    expect(screen.getByTestId("tree-is-dirty")).toHaveTextContent("yes");

    fireEvent.click(screen.getByTestId("nav-history"));
    fireEvent.click(screen.getByRole("button", { name: /discard and continue/i }));
    expect(screen.getByTestId("attack-history")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("open-attack-as-tree"));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "history");
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "history");

    fireEvent.click(screen.getByTestId("open-attack-as-tree"));
    fireEvent.click(screen.getByRole("button", { name: /discard and continue/i }));

    await waitFor(() => {
      expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "tree");
    });
    expect(screen.getByTestId("open-from-ar-id")).toHaveTextContent("ar-tree-1");
  });

  it("starts in tree view when the URL carries a conversation_tree_id fragment", () => {
    process.env.VITE_ENABLE_TREE_UI = "true";
    window.history.replaceState(window.history.state, "", "/#conversation_tree_id=tree-from-url");

    render(<App />);

    expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "tree");
    expect(screen.getByTestId("tree-runner-host")).toBeInTheDocument();
  });

  it("opens attack from history and switches to chat", async () => {
    mockGetAttack.mockResolvedValue({ attack_result_id: "ar-attack-1", conversation_id: "attack-conv-1", labels: { operator: "roakey" } });
    render(<App />);

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
    render(<App />);

    fireEvent.click(screen.getByTestId("home-open-attack"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "chat"
    );
    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledWith("ar-home-attack"));
    await waitFor(() => expect(screen.getByTestId("conversation-id")).toHaveTextContent("home-conv-1"));
  });

  it("navigates to config from the home view", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("home-go-config"));

    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "config"
    );
    expect(screen.getByTestId("target-config")).toBeInTheDocument();
  });

  it("handles failed attack open gracefully", async () => {
    mockGetAttack.mockRejectedValue(new Error("Not found"));
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-history"));
    fireEvent.click(screen.getByTestId("open-attack"));

    // Should switch to chat view even on error
    expect(screen.getByTestId("main-layout")).toHaveAttribute("data-current-view", "chat");
    await waitFor(() => expect(mockGetAttack).toHaveBeenCalledWith("ar-attack-1"));
    // Conversation should be cleared on error
    await waitFor(() => expect(screen.getByTestId("conversation-id")).toHaveTextContent("none"));
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

    render(<App />);

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
    // alongside the new attack id. This is the invariant the fix establishes.
    expect(screen.getByTestId("main-layout")).toHaveAttribute(
      "data-current-view",
      "chat"
    );
    expect(screen.getByTestId("attack-result-id")).toHaveTextContent("ar-attack-2");
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

    render(<App />);

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

    render(<App />);

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

    render(<App />);

    await waitFor(() => {
      const labels = screen.getByTestId("home-labels-json").textContent ?? "";
      expect(labels).toContain('"operator":"override_user"');
      expect(labels).toContain('"custom":"value"');
    });
  });

  it("stores attack target when conversation is created with active target", () => {
    render(<App />);

    // Set a target first
    fireEvent.click(screen.getByTestId("nav-config"));
    fireEvent.click(screen.getByTestId("set-target"));
    fireEvent.click(screen.getByTestId("nav-chat"));

    // Create a conversation (which should store target info)
    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");
  });

  it("sets active conversation when onSelectConversation is called", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("nav-chat"));

    // First create a conversation to have an attack
    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");

    // Now select a different conversation
    fireEvent.click(screen.getByTestId("select-conversation"));
    // The component re-renders with the new conversation ID
  });
});

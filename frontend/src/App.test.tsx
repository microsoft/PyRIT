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
    conversationId,
    onConversationCreated,
    onSelectConversation,
    labels,
  }: {
    onNewAttack: () => void;
    activeTarget: unknown;
    conversationId: string | null;
    onConversationCreated: (attackResultId: string, conversationId: string) => void;
    onSelectConversation: (convId: string) => void;
    labels: Record<string, string>;
  }) => {
    return (
      <div data-testid="chat-window">
        <span data-testid="conversation-id">{conversationId ?? "none"}</span>
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
  }: {
    onOpenAttack: (attackResultId: string) => void;
  }) => {
    return (
      <div data-testid="attack-history">
        <button
          onClick={() => onOpenAttack("ar-attack-1")}
          data-testid="open-attack"
        >
          Open Attack
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

describe("App", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetActiveAccount.mockReturnValue(null);
  });

  it("renders with FluentProvider and MainLayout", () => {
    render(<App />);
    expect(screen.getByTestId("main-layout")).toBeInTheDocument();
    expect(screen.getByTestId("chat-window")).toBeInTheDocument();
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

  it("starts in chat view", () => {
    render(<App />);

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

    expect(screen.getByTestId("conversation-id")).toHaveTextContent("none");

    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");
  });

  it("clears conversationId on new attack", () => {
    render(<App />);

    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");

    fireEvent.click(screen.getByTestId("new-attack"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("none");
  });

  it("sets active target from config page and passes to chat", () => {
    render(<App />);

    // No target initially
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

    await waitFor(() => {
      expect(screen.getByTestId("labels-operator")).toHaveTextContent("test.user");
      expect(screen.getByTestId("labels-json")).toHaveTextContent('"custom":"value"');
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
      expect(screen.getByTestId("labels-operator")).toHaveTextContent("override_user");
      expect(screen.getByTestId("labels-json")).toHaveTextContent('"custom":"value"');
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

    // First create a conversation to have an attack
    fireEvent.click(screen.getByTestId("set-conversation"));
    expect(screen.getByTestId("conversation-id")).toHaveTextContent("conv-123");

    // Now select a different conversation
    fireEvent.click(screen.getByTestId("select-conversation"));
    // The component re-renders with the new conversation ID
  });
});

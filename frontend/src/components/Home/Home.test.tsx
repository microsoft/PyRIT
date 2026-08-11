/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { makeTarget } from "@/test-utils/targetFixtures";
import Home from "./Home";
import { attacksApi } from "../../services/api";
import type { AttackSummary, TargetInstance } from "../../types";

jest.mock("../../services/api", () => ({
  attacksApi: {
    listAttacks: jest.fn(),
  },
  labelsApi: {
    getLabels: jest.fn().mockResolvedValue({ source: "attacks", labels: {} }),
  },
}));

const mockListAttacks = attacksApi.listAttacks as jest.Mock;

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
);

function makeAttack(overrides: Partial<AttackSummary> = {}): AttackSummary {
  return {
    attack_result_id: "ar-1",
    conversation_id: "conv-1",
    attack_type: "TestAttack",
    converters: [],
    outcome: "success",
    last_message_preview: "preview",
    message_count: 1,
    related_conversation_ids: [],
    labels: { operator: "alice", operation: "op_alpha" },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

const defaultLabels: Record<string, string> = { operator: "alice", operation: "op_alpha" };

const defaultProps = {
  labels: defaultLabels,
  onLabelsChange: jest.fn(),
  activeTarget: null as TargetInstance | null,
  onNavigate: jest.fn(),
  onOpenAttack: jest.fn(),
};

describe("Home", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockListAttacks.mockResolvedValue({
      items: [],
      pagination: { has_more: false, next_cursor: null },
    });
  });

  it("renders the welcome hero", async () => {
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>);
    expect(
      screen.getByRole("heading", { level: 1, name: /welcome to co-pyrit/i })
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Labels" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Target" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 2, name: "Recent operations" })
    ).toBeInTheDocument();
    await waitFor(() => expect(mockListAttacks).toHaveBeenCalled());
  });

  it("shows an empty target hint when no target is set", async () => {
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>);
    expect(screen.getByTestId("home-target-empty")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /configure a target/i })).toBeInTheDocument();
    await waitFor(() => expect(mockListAttacks).toHaveBeenCalled());
  });

  it("renders active target summary and 'Manage targets' button when a target is set", async () => {
    const target: TargetInstance = makeTarget({
      target_registry_name: "my_target",
      target_type: "OpenAIChatTarget",
      endpoint: "https://example.com",
      model_name: "gpt-test",
    });
    render(<TestWrapper><Home {...defaultProps} activeTarget={target} /></TestWrapper>);
    expect(screen.getByTestId("home-target-active")).toHaveTextContent("gpt-test");
    expect(screen.getByRole("button", { name: /manage targets/i })).toBeInTheDocument();
    await waitFor(() => expect(mockListAttacks).toHaveBeenCalled());
  });

  it("navigates to config when 'Configure a target' is clicked", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    render(<TestWrapper><Home {...defaultProps} onNavigate={onNavigate} /></TestWrapper>);
    await user.click(screen.getByTestId("home-configure-target-btn"));
    expect(onNavigate).toHaveBeenCalledWith("config");
  });

  it("shows the empty state when there are no attacks", async () => {
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>);
    expect(await screen.findByTestId("home-empty")).toBeInTheDocument();
    expect(screen.getByText(/no attacks yet/i)).toBeInTheDocument();
  });

  it("navigates to chat when 'Go to chat' is clicked from the empty state", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    render(<TestWrapper><Home {...defaultProps} onNavigate={onNavigate} /></TestWrapper>);
    const btn = await screen.findByTestId("home-start-attack-btn");
    await user.click(btn);
    expect(onNavigate).toHaveBeenCalledWith("chat");
  });

  it("renders recent operations grouped by operation label", async () => {
    const now = Date.now();
    mockListAttacks.mockResolvedValue({
      items: [
        makeAttack({
          attack_result_id: "ar-1",
          labels: { operator: "alice", operation: "op_alpha" },
          updated_at: new Date(now).toISOString(),
        }),
        makeAttack({
          attack_result_id: "ar-2",
          labels: { operator: "alice", operation: "op_alpha" },
          updated_at: new Date(now - 60_000).toISOString(),
        }),
        makeAttack({
          attack_result_id: "ar-3",
          labels: { operator: "alice", operation: "op_beta" },
          updated_at: new Date(now - 120_000).toISOString(),
        }),
      ],
      pagination: { has_more: false, next_cursor: null },
    });

    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>);
    expect(await screen.findByTestId("home-operation-op_alpha")).toBeInTheDocument();
    expect(screen.getByTestId("home-operation-op_beta")).toBeInTheDocument();
    // op_alpha has 2 attacks
    expect(screen.getByTestId("home-operation-op_alpha")).toHaveTextContent(/2 attacks/i);
    // op_beta has 1 attack
    expect(screen.getByTestId("home-operation-op_beta")).toHaveTextContent(/1 attack/);
  });

  it("updates last-activity for newer attacks and formats hour/day/older timestamps", async () => {
    const now = Date.now();
    const HOUR = 60 * 60 * 1000;
    const DAY = 24 * HOUR;
    mockListAttacks.mockResolvedValue({
      items: [
        // Oldest first, so a later (newer) attack in the same operation updates
        // the group's last-activity — exercising the "newer than current" branch.
        makeAttack({
          attack_result_id: "ar-old",
          labels: { operator: "alice", operation: "op_time" },
          last_message_preview: "older than a week",
          updated_at: new Date(now - 10 * DAY).toISOString(),
        }),
        makeAttack({
          attack_result_id: "ar-hours",
          labels: { operator: "alice", operation: "op_time" },
          last_message_preview: "a few hours ago",
          updated_at: new Date(now - 3 * HOUR).toISOString(),
        }),
        makeAttack({
          attack_result_id: "ar-days",
          labels: { operator: "alice", operation: "op_time" },
          last_message_preview: "a few days ago",
          updated_at: new Date(now - 3 * DAY).toISOString(),
        }),
      ],
      pagination: { has_more: false, next_cursor: null },
    });

    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>);

    const card = await screen.findByTestId("home-operation-op_time");
    // All three attacks render, exercising the hour/day/older formatting paths.
    expect(within(card).getByText("a few hours ago")).toBeInTheDocument();
    expect(within(card).getByText("a few days ago")).toBeInTheDocument();
    expect(within(card).getByText("older than a week")).toBeInTheDocument();
    // The newer (3h-ago) attack wins as the group's most recent activity.
    expect(within(card).getAllByText(/3h ago/).length).toBeGreaterThan(0);
    expect(within(card).getAllByText(/3d ago/).length).toBeGreaterThan(0);
  });

  it("caps visible attacks and falls back for missing preview, outcome, and timestamp", async () => {
    const now = Date.now();
    mockListAttacks.mockResolvedValue({
      items: [
        makeAttack({
          attack_result_id: "f1",
          labels: { operator: "alice", operation: "op_full" },
          outcome: "success",
          last_message_preview: "first preview",
          updated_at: new Date(now - 60_000).toISOString(),
        }),
        makeAttack({
          attack_result_id: "f2",
          labels: { operator: "alice", operation: "op_full" },
          outcome: null, // unknown outcome -> default icon via the ?? 'undetermined' branch
          last_message_preview: null, // missing preview -> falls back to attack_type
          updated_at: new Date(now - 120_000).toISOString(),
        }),
        makeAttack({
          attack_result_id: "f3",
          labels: { operator: "alice", operation: "op_full" },
          // Outcome not present in the icon map -> exercises the icon fallback branch.
          outcome: "mystery" as unknown as AttackSummary["outcome"],
          last_message_preview: "third preview",
          updated_at: "not-a-date", // invalid -> empty relative time (NaN guard)
        }),
        makeAttack({
          attack_result_id: "f4",
          labels: { operator: "alice", operation: "op_full" },
          last_message_preview: "fourth preview",
          updated_at: new Date(now - 240_000).toISOString(),
        }),
      ],
      pagination: { has_more: false, next_cursor: null },
    });

    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>);

    const card = await screen.findByTestId("home-operation-op_full");
    // Only the first three attacks render; the fourth is summarized as overflow.
    expect(within(card).getByText("first preview")).toBeInTheDocument();
    expect(within(card).getByText("third preview")).toBeInTheDocument();
    expect(within(card).queryByText("fourth preview")).not.toBeInTheDocument();
    expect(within(card).getByText(/\+1 more in history/)).toBeInTheDocument();
    // f2 has no preview, so its row shows the attack type instead.
    expect(within(card).getByText("TestAttack")).toBeInTheDocument();
  });

  it("groups attacks with no operation label under '(no operation)'", async () => {
    mockListAttacks.mockResolvedValue({
      items: [
        makeAttack({
          attack_result_id: "ar-x",
          labels: { operator: "alice" },
        }),
      ],
      pagination: { has_more: false, next_cursor: null },
    });

    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>);
    expect(await screen.findByTestId("home-operation-unlabeled")).toHaveTextContent(/no operation/i);
  });

  it("calls onOpenAttack when a recent attack row is clicked", async () => {
    const user = userEvent.setup();
    const onOpenAttack = jest.fn();
    mockListAttacks.mockResolvedValue({
      items: [makeAttack({ attack_result_id: "ar-click" })],
      pagination: { has_more: false, next_cursor: null },
    });

    render(<TestWrapper><Home {...defaultProps} onOpenAttack={onOpenAttack} /></TestWrapper>);
    const row = await screen.findByTestId("home-open-attack-ar-click");
    await user.click(row);
    expect(onOpenAttack).toHaveBeenCalledWith("ar-click");
  });

  it("renders error message when listAttacks fails", async () => {
    mockListAttacks.mockRejectedValueOnce(new Error("boom"));
    render(<TestWrapper><Home {...defaultProps} /></TestWrapper>);
    expect(await screen.findByTestId("home-error")).toBeInTheDocument();
  });

  it("navigates to history when 'View all history' is clicked", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    render(<TestWrapper><Home {...defaultProps} onNavigate={onNavigate} /></TestWrapper>);
    await user.click(screen.getByTestId("home-view-all-history-btn"));
    expect(onNavigate).toHaveBeenCalledWith("history");
  });
});

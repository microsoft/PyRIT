/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, useTheme } from "../../hooks/useTheme";
import { UserPreferencesProvider } from "@/hooks/useUserPreferences";
import { readUserPreferences } from "@/utils/userPreferences";
import { THEME_PRESETS } from "@/themes/themePresets";
import type { ThemePreset } from "@/types";
import Navigation from "./Navigation";

const STORAGE_KEY = "pyrit.themeMode";

const renderWithProvider = (ui: React.ReactElement) =>
  render(<UserPreferencesProvider accountKey="local"><ThemeProvider>{ui}</ThemeProvider></UserPreferencesProvider>);

describe("Navigation", () => {
  const defaultProps = {
    currentView: "chat" as const,
    onNavigate: jest.fn(),
    onOpenFeedback: jest.fn(),
    canManageConfiguration: true,
  };

  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.removeProperty("color-scheme");
  });

  it("renders the home button", () => {
    renderWithProvider(<Navigation {...defaultProps} />);
    expect(screen.getByRole("button", { name: "Home" })).toBeInTheDocument();
  });

  it("exposes one primary navigation landmark and marks the current view", () => {
    renderWithProvider(<Navigation {...defaultProps} />);

    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Chat" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(screen.getByRole("button", { name: "Home" })).not.toHaveAttribute(
      "aria-current"
    );
  });

  it("calls onNavigate with 'home' when home button is clicked", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    await user.click(screen.getByRole("button", { name: "Home" }));
    expect(onNavigate).toHaveBeenCalledWith("home");
  });

  it("renders the chat button", () => {
    renderWithProvider(<Navigation {...defaultProps} />);
    expect(screen.getByRole("button", { name: "Chat" })).toBeInTheDocument();
  });

  it("renders the registry button", () => {
    renderWithProvider(<Navigation {...defaultProps} />);
    expect(
      screen.getByRole("button", { name: "Registry" })
    ).toBeInTheDocument();
  });

  it("calls onNavigate with 'chat' when chat button is clicked", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    await user.click(screen.getByRole("button", { name: "Chat" }));
    expect(onNavigate).toHaveBeenCalledWith("chat");
  });

  it("calls onNavigate with 'registry' when registry button is clicked", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    await user.click(screen.getByRole("button", { name: "Registry" }));
    expect(onNavigate).toHaveBeenCalledWith("registry");
  });

  it("navigates to configuration", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    await user.click(screen.getByRole("button", { name: "Configuration" }));
    expect(onNavigate).toHaveBeenCalledWith("configuration");
  });

  it("hides configuration from users without administrator access", () => {
    renderWithProvider(
      <Navigation {...defaultProps} canManageConfiguration={false} />
    );

    expect(
      screen.queryByRole("button", { name: "Configuration" })
    ).not.toBeInTheDocument();
  });

  it("renders the history button", () => {
    renderWithProvider(<Navigation {...defaultProps} />);
    expect(
      screen.getByRole("button", { name: "History" })
    ).toBeInTheDocument();
  });

  it("renders the Scanner button", () => {
    renderWithProvider(<Navigation {...defaultProps} />);
    expect(
      screen.getByRole("button", { name: "Scanner" })
    ).toBeInTheDocument();
  });

  it("renders the final primary navigation order", () => {
    renderWithProvider(<Navigation {...defaultProps} />);
    const navigation = screen.getByRole("navigation", { name: "Primary" });
    const labels = within(navigation)
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label"));

    expect(labels).toEqual([
      "Home",
      "Chat",
      "History",
      "Scanner",
      "Registry",
      "Configuration",
    ]);
  });

  it("marks History current and navigates to its tabbed view", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation
        {...defaultProps}
        currentView="history"
        onNavigate={onNavigate}
      />,
    );

    const button = screen.getByRole("button", { name: "History" });
    expect(button).toHaveAttribute("aria-current", "page");
    await user.click(button);
    expect(onNavigate).toHaveBeenCalledWith("history");
  });

  it("calls onNavigate with 'scenarios' when the scenarios button is clicked", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    await user.click(screen.getByRole("button", { name: "Scanner" }));
    expect(onNavigate).toHaveBeenCalledWith("scenarios");
  });

  it("marks the scenarios button current when it is the active view", () => {
    renderWithProvider(
      <Navigation {...defaultProps} currentView="scenarios" />
    );
    expect(screen.getByRole("button", { name: "Scanner" })).toHaveAttribute(
      "aria-current",
      "page"
    );
  });

  it("renders one feedback button and forwards clicks to onOpenFeedback", async () => {
    const user = userEvent.setup();
    const onOpenFeedback = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onOpenFeedback={onOpenFeedback} />
    );

    const feedbackButtons = screen.getAllByRole("button", { name: "Feedback" });
    expect(feedbackButtons).toHaveLength(1);
    await user.click(feedbackButtons[0]);
    expect(onOpenFeedback).toHaveBeenCalledTimes(1);
  });

  it("does not render a direct security link", () => {
    renderWithProvider(<Navigation {...defaultProps} />);

    expect(
      screen.queryByRole("link", { name: "Security" })
    ).not.toBeInTheDocument();
  });

  it("calls onNavigate with 'history' when history button is clicked", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    await user.click(screen.getByRole("button", { name: "History" }));
    expect(onNavigate).toHaveBeenCalledWith("history");
  });

  it("renders the theme picker labelled with the current mode", () => {
    renderWithProvider(<Navigation {...defaultProps} />);
    expect(
      screen.getByRole("button", { name: "Theme: System" })
    ).toBeInTheDocument();
  });

  it("opens the theme menu and exposes standard modes and every preset", async () => {
    const user = userEvent.setup();
    renderWithProvider(<Navigation {...defaultProps} />);

    await user.click(screen.getByRole("button", { name: "Theme: System" }));

    expect(
      screen.getByRole("menuitemradio", { name: "System" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: "Light" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitemradio", { name: "Dark" })
    ).toBeInTheDocument();
    expect(screen.getAllByRole("menuitemradio")).toHaveLength(Object.keys(THEME_PRESETS).length + 1);
    for (const preset of Object.values(THEME_PRESETS)) {
      expect(screen.getByRole("menuitemradio", { name: preset.label })).toBeInTheDocument();
    }
  });

  it("changes the theme mode when a menu item is selected", async () => {
    const user = userEvent.setup();

    function Reader() {
      const { mode } = useTheme();
      return <span data-testid="mode">{mode}</span>;
    }

    renderWithProvider(
      <>
        <Navigation {...defaultProps} />
        <Reader />
      </>
    );

    expect(screen.getByTestId("mode")).toHaveTextContent("system");

    await user.click(screen.getByRole("button", { name: "Theme: System" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Dark" }));

    expect(screen.getByTestId("mode")).toHaveTextContent("dark");
    expect(readUserPreferences('local').theme).toBe("dark");
  });

  it("reflects the persisted mode in the trigger label", () => {
    window.localStorage.setItem(STORAGE_KEY, "light");
    renderWithProvider(<Navigation {...defaultProps} />);
    expect(
      screen.getByRole("button", { name: "Theme: Light" })
    ).toBeInTheDocument();
  });

  it.each(Object.entries(THEME_PRESETS))(
    "selects and checks %s without navigating",
    async (id: string, preset: ThemePreset) => {
      const user = userEvent.setup();
      const onNavigate = jest.fn();
      renderWithProvider(
        <Navigation {...defaultProps} canManageConfiguration={false} onNavigate={onNavigate} />
      );

      await user.click(screen.getByRole("button", { name: "Theme: System" }));
      await user.click(screen.getByRole("menuitemradio", { name: preset.label }));
      expect(readUserPreferences('local').theme).toBe(id);
      expect(onNavigate).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: `Theme: ${preset.label}` }));
      expect(screen.getByRole("menuitemradio", { name: preset.label, checked: true })).toBeInTheDocument();
    }
  );

});

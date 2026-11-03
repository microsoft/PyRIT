/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { useState } from "react";

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, useTheme } from "../../hooks/useTheme";
import { UserPreferencesProvider } from "@/hooks/useUserPreferences";
import { readUserPreferences } from "@/utils/userPreferences";
import { THEME_PRESETS } from "@/themes/themePresets";
import type { ThemePreset } from "@/types";
import Navigation, { type ViewName } from "./Navigation";

const STORAGE_KEY = "pyrit.themeMode";

const renderWithProvider = (ui: React.ReactElement) =>
  render(<UserPreferencesProvider accountKey="local"><ThemeProvider>{ui}</ThemeProvider></UserPreferencesProvider>);

describe("Navigation", () => {
  const defaultProps = {
    currentView: "chat" as const,
    onNavigate: jest.fn(),
    onOpenFeedback: jest.fn(),
    canManageConfiguration: true,
    expanded: false,
    onToggleExpanded: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.style.removeProperty("color-scheme");
  });

  it("hides configuration from users without administrator access", () => {
    renderWithProvider(
      <Navigation {...defaultProps} canManageConfiguration={false} />
    );

    expect(
      screen.queryByRole("button", { name: "Configuration" })
    ).not.toBeInTheDocument();
  });

  it.each([false, true])("shows the ordered destinations with expanded=%s", (expanded: boolean) => {
    renderWithProvider(<Navigation {...defaultProps} expanded={expanded} />);
    const navigation = screen.getByRole("navigation", { name: "Primary" });
    const buttons = within(navigation).getAllByRole("button");
    const labels = buttons.map((button: HTMLElement) => button.getAttribute("aria-label"));

    expect(labels).toEqual([
      "Home",
      "Attacks",
      "Scanner",
      "Analytics",
      "Operations",
      "Datasets",
      "Registry",
      "Configuration",
    ]);
    for (const button of buttons) {
      expect(button.textContent).toBe(expanded ? button.getAttribute("aria-label") : "");
    }
    expect(screen.getByRole("button", { name: "Attacks" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(screen.getByRole("button", { name: "Home" })).not.toHaveAttribute("aria-current");
    const toggle = screen.getByRole("button", { name: expanded ? "Collapse sidebar" : "Expand sidebar" });
    expect(toggle).toHaveAttribute("aria-expanded", String(expanded));
    expect(toggle.textContent).toBe("");
    expect(toggle).toHaveAttribute("title", expanded ? "Collapse sidebar" : "Expand sidebar");
  });

  it("navigates to the matching view for each destination", async () => {
    const user = userEvent.setup();
    const onNavigate = jest.fn();
    renderWithProvider(<Navigation {...defaultProps} onNavigate={onNavigate} />);
    const destinations: Array<[string, ViewName]> = [
      ["Home", "home"], ["Attacks", "chat"], ["Scanner", "scenarios"],
      ["Analytics", "analytics"], ["Operations", "operations"],
      ["Datasets", "datasets"], ["Registry", "registry"], ["Configuration", "configuration"],
    ];
    for (const [label, view] of destinations) {
      await user.click(screen.getByRole("button", { name: label }));
      expect(onNavigate).toHaveBeenLastCalledWith(view);
    }
  });

  it("shows a single icon label after expanding and collapsing in place", async () => {
    const user = userEvent.setup();
    function ToggleNavigation() {
      const [expanded, setExpanded] = useState(true);
      return (
        <Navigation
          {...defaultProps}
          expanded={expanded}
          onToggleExpanded={() => { setExpanded((current: boolean) => !current); }}
        />
      );
    }

    renderWithProvider(<ToggleNavigation />);
    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    await user.hover(screen.getByRole("button", { name: "Attacks" }));
    expect(await screen.findByRole("tooltip", { name: "Attacks" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Attacks" })).not.toHaveAttribute("title");

    await user.unhover(screen.getByRole("button", { name: "Attacks" }));
    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
    await user.hover(screen.getByRole("button", { name: "Attacks" }));
    expect(screen.queryByRole("tooltip", { name: "Attacks" })).not.toBeInTheDocument();

    await user.unhover(screen.getByRole("button", { name: "Attacks" }));
    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: "Attacks" })).toHaveFocus();
    expect(await screen.findByRole("tooltip", { name: "Attacks" })).toBeVisible();
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

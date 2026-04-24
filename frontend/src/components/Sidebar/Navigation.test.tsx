/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import Navigation from "./Navigation";

const renderWithProvider = (ui: React.ReactElement) => {
  return render(<FluentProvider theme={webLightTheme}>{ui}</FluentProvider>);
};

describe("Navigation", () => {
  const defaultProps = {
    currentView: "chat" as const,
    onNavigate: jest.fn(),
    onToggleTheme: jest.fn(),
    isDarkMode: false,
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("renders the chat button", () => {
    renderWithProvider(<Navigation {...defaultProps} />);

    const chatButton = screen.getByTitle("Chat");
    expect(chatButton).toBeInTheDocument();
    expect(chatButton).not.toBeDisabled();
  });

  it("renders the configuration button", () => {
    renderWithProvider(<Navigation {...defaultProps} />);

    const configButton = screen.getByTitle("Configuration");
    expect(configButton).toBeInTheDocument();
  });

  it("calls onNavigate with 'chat' when chat button is clicked", () => {
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    fireEvent.click(screen.getByTitle("Chat"));
    expect(onNavigate).toHaveBeenCalledWith("chat");
  });

  it("calls onNavigate with 'config' when config button is clicked", () => {
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    fireEvent.click(screen.getByTitle("Configuration"));
    expect(onNavigate).toHaveBeenCalledWith("config");
  });

  it("renders the attack history button", () => {
    renderWithProvider(<Navigation {...defaultProps} />);

    const historyButton = screen.getByTitle("Attack History");
    expect(historyButton).toBeInTheDocument();
  });

  it("calls onNavigate with 'history' when history button is clicked", () => {
    const onNavigate = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onNavigate={onNavigate} />
    );

    fireEvent.click(screen.getByTitle("Attack History"));
    expect(onNavigate).toHaveBeenCalledWith("history");
  });

  it("renders theme toggle button with light mode title when in dark mode", () => {
    renderWithProvider(
      <Navigation {...defaultProps} isDarkMode={true} />
    );

    const themeButton = screen.getByTitle("Light Mode");
    expect(themeButton).toBeInTheDocument();
  });

  it("renders theme toggle button with dark mode title when in light mode", () => {
    renderWithProvider(
      <Navigation {...defaultProps} isDarkMode={false} />
    );

    const themeButton = screen.getByTitle("Dark Mode");
    expect(themeButton).toBeInTheDocument();
  });

  it("calls onToggleTheme when theme button is clicked", () => {
    const mockToggleTheme = jest.fn();
    renderWithProvider(
      <Navigation {...defaultProps} onToggleTheme={mockToggleTheme} />
    );

    const themeButton = screen.getByTitle("Dark Mode");
    fireEvent.click(themeButton);

    expect(mockToggleTheme).toHaveBeenCalledTimes(1);
  });

  it("theme button is not disabled", () => {
    renderWithProvider(<Navigation {...defaultProps} />);

    const themeButton = screen.getByTitle("Dark Mode");
    expect(themeButton).not.toBeDisabled();
  });
});

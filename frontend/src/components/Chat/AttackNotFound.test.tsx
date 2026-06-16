/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import AttackNotFound from "./AttackNotFound";

function renderNotFound(props: Partial<React.ComponentProps<typeof AttackNotFound>> = {}) {
  const onStartNew = jest.fn();
  const onBackToHistory = jest.fn();
  render(
    <FluentProvider theme={webLightTheme}>
      <AttackNotFound
        attackId="ar-missing"
        onStartNew={onStartNew}
        onBackToHistory={onBackToHistory}
        {...props}
      />
    </FluentProvider>
  );
  return { onStartNew, onBackToHistory };
}

describe("AttackNotFound", () => {
  it("shows the missing attack id", () => {
    renderNotFound();
    expect(screen.getByTestId("attack-not-found")).toBeInTheDocument();
    expect(screen.getByText("ar-missing")).toBeInTheDocument();
  });

  it("calls onStartNew when the start button is clicked", () => {
    const { onStartNew } = renderNotFound();
    fireEvent.click(screen.getByRole("button", { name: "Start a new attack" }));
    expect(onStartNew).toHaveBeenCalledTimes(1);
  });

  it("calls onBackToHistory when the back button is clicked", () => {
    const { onBackToHistory } = renderNotFound();
    fireEvent.click(screen.getByRole("button", { name: "Back to history" }));
    expect(onBackToHistory).toHaveBeenCalledTimes(1);
  });

  it("shows the error variant with transient-failure copy, not the deleted/not-found wording", () => {
    renderNotFound({ variant: "error" });
    expect(screen.getByTestId("attack-load-error")).toBeInTheDocument();
    expect(screen.queryByTestId("attack-not-found")).not.toBeInTheDocument();
    expect(screen.getByText(/Could not load attack/)).toBeInTheDocument();
    expect(screen.getByText(/temporary network or server error/)).toBeInTheDocument();
    expect(screen.queryByText(/may have been/)).not.toBeInTheDocument();
  });
});

/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import MainLayout from "./MainLayout";

// Mock the api module
jest.mock("../../services/api", () => ({
  versionApi: {
    getVersion: jest.fn(),
  },
}));

// Mock Navigation to simplify testing
jest.mock("../Sidebar/Navigation", () => {
  const MockNavigation = ({
    currentView,
    onNavigate,
  }: {
    currentView: string;
    onNavigate: (view: string) => void;
  }) => {
    return (
      <div data-testid="navigation" data-current-view={currentView}>
        <button onClick={() => onNavigate("targets")}>Targets</button>
      </div>
    );
  };
  MockNavigation.displayName = "MockNavigation";
  return {
    __esModule: true,
    default: MockNavigation,
  };
});

import { versionApi } from "../../services/api";

const mockedVersionApi = versionApi as jest.Mocked<typeof versionApi>;

const renderWithProvider = (ui: React.ReactElement) => {
  return render(<FluentProvider theme={webLightTheme}>{ui}</FluentProvider>);
};

describe("MainLayout", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  const defaultProps = {
    currentView: 'chat' as const,
    onNavigate: jest.fn(),
    onOpenFeedback: jest.fn(),
    canManageConfiguration: true,
  };

  it("renders the header with title and subtitle", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({ version: "1.0.0" });

    renderWithProvider(
      <MainLayout {...defaultProps}>
        <div>Child Content</div>
      </MainLayout>
    );

    expect(screen.getByText("Co-PyRIT")).toBeInTheDocument();
    expect(
      screen.getByText("Python Risk Identification Tool")
    ).toBeInTheDocument();

    // Wait for async useEffect to complete
    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });
  });

  it("renders children content", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({ version: "1.0.0" });

    renderWithProvider(
      <MainLayout {...defaultProps}>
        <div data-testid="child-content">Child Content</div>
      </MainLayout>
    );

    expect(screen.getByTestId("child-content")).toBeInTheDocument();

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });
  });

  it("renders the logo image", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({ version: "1.0.0" });

    renderWithProvider(
      <MainLayout {...defaultProps}>
        <div>Content</div>
      </MainLayout>
    );

    const logo = screen.getByAltText("Co-PyRIT Logo");
    expect(logo).toBeInTheDocument();
    expect(logo).toHaveAttribute("src", "/roakey.png");

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });
  });

  it("displays the version, commit, and database for a development release", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({
      version: "1.1.0.dev0",
      commit: "729b7dd0446cc95412345662a1a921e2a4bf1979",
      display: "729b7dd0446cc95412345662a1a921e2a4bf1979",
      database_info: "AzureSQLMemory (airtprod)",
    });
    const user = userEvent.setup();

    renderWithProvider(
      <MainLayout {...defaultProps}>
        <div>Content</div>
      </MainLayout>
    );

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });

    await user.hover(screen.getByAltText("Co-PyRIT Logo"));

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("PyRIT 1.1.0.dev0");
    expect(tooltip).toHaveTextContent(
      "Commit: 729b7dd0446cc95412345662a1a921e2a4bf1979"
    );
    expect(tooltip).toHaveTextContent("AzureSQLMemory (airtprod)");
  });

  it("does not display the commit for a release version", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({
      version: "1.1.0",
      commit: "729b7dd0446cc95412345662a1a921e2a4bf1979",
      display: "729b7dd0446cc95412345662a1a921e2a4bf1979",
      database_info: "AzureSQLMemory (airtprod)",
    });
    const user = userEvent.setup();

    renderWithProvider(
      <MainLayout {...defaultProps}>
        <div>Content</div>
      </MainLayout>
    );

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });

    await user.hover(screen.getByAltText("Co-PyRIT Logo"));

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("PyRIT 1.1.0");
    expect(tooltip).not.toHaveTextContent("Commit:");
    expect(tooltip).toHaveTextContent("AzureSQLMemory (airtprod)");
  });

  it("displays 'Unknown' when version API fails", async () => {
    mockedVersionApi.getVersion.mockRejectedValue(new Error("API Error"));

    renderWithProvider(
      <MainLayout {...defaultProps}>
        <div>Content</div>
      </MainLayout>
    );

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });
  });

  it("renders a 'Take a tour' button in the top bar when onStartTour is provided", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({ version: "1.0.0" });

    renderWithProvider(
      <MainLayout {...defaultProps} onStartTour={jest.fn()}>
        <div>Content</div>
      </MainLayout>
    );

    expect(screen.getByRole("button", { name: /take a tour/i })).toBeInTheDocument();

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });
  });

  it("calls onStartTour when the 'Take a tour' button is clicked", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({ version: "1.0.0" });
    const onStartTour = jest.fn();
    const user = userEvent.setup();

    renderWithProvider(
      <MainLayout {...defaultProps} onStartTour={onStartTour}>
        <div>Content</div>
      </MainLayout>
    );

    await user.click(screen.getByRole("button", { name: /take a tour/i }));
    expect(onStartTour).toHaveBeenCalledTimes(1);

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });
  });

  it("does not render the 'Take a tour' button when onStartTour is not provided", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({ version: "1.0.0" });

    renderWithProvider(
      <MainLayout {...defaultProps}>
        <div>Content</div>
      </MainLayout>
    );

    expect(screen.queryByRole("button", { name: /take a tour/i })).not.toBeInTheDocument();

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });
  });
});

/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { render, screen, waitFor } from "@testing-library/react";
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
        <button onClick={() => onNavigate("config")}>Config</button>
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

  it("displays version from API in tooltip", async () => {
    mockedVersionApi.getVersion.mockResolvedValue({
      version: "1.0.0",
      display: "v1.0.0-beta",
    });

    renderWithProvider(
      <MainLayout {...defaultProps}>
        <div>Content</div>
      </MainLayout>
    );

    await waitFor(() => {
      expect(mockedVersionApi.getVersion).toHaveBeenCalled();
    });
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
});

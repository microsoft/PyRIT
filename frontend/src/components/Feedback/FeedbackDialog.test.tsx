import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import FeedbackDialog from "./FeedbackDialog";

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
);

const defaultProps = {
  open: true,
  onClose: jest.fn(),
  context: {
    app_version: "1.2.3",
    current_view: "chat",
    target_type: "OpenAIChatTarget",
  },
};

function renderDialog(overrides: Partial<typeof defaultProps> = {}) {
  return render(
    <TestWrapper>
      <FeedbackDialog {...defaultProps} {...overrides} />
    </TestWrapper>,
  );
}

async function pickCategory(category: string) {
  const user = userEvent.setup();
  await user.selectOptions(
    screen.getByTestId("feedback-category-select"),
    category,
  );
  return user;
}

describe("FeedbackDialog", () => {
  let openSpy: jest.SpyInstance;

  beforeEach(() => {
    jest.clearAllMocks();
    openSpy = jest.spyOn(window, "open").mockImplementation(() => null);
  });

  afterEach(() => {
    openSpy.mockRestore();
  });

  describe("shell", () => {
    it("renders sensitive-info warning and links to the public repo", () => {
      renderDialog();

      expect(screen.getByText("Send feedback")).toBeInTheDocument();
      const warning = screen.getByTestId("feedback-sensitive-warning");
      expect(warning).toHaveTextContent(/public/i);
      expect(warning).toHaveTextContent(/secrets/i);
      expect(warning).toHaveTextContent(/credentials/i);
      expect(warning).toHaveTextContent(/customer data/i);
      expect(warning).toHaveTextContent(/proprietary/i);
      expect(
        screen.getByRole("link", { name: /github\.com\/microsoft\/PyRIT/i }),
      ).toHaveAttribute("href", "https://github.com/microsoft/PyRIT/issues");
      expect(
        screen.getByRole("link", { name: /microsoft privacy statement/i }),
      ).toHaveAttribute(
        "href",
        "https://privacy.microsoft.com/en-us/privacystatement",
      );
    });

    it("does not render when closed", () => {
      renderDialog({ open: false });
      expect(screen.queryByText("Send feedback")).not.toBeInTheDocument();
    });

    it("offers all five template-backed categories in the dropdown", () => {
      renderDialog();
      const select = screen.getByTestId(
        "feedback-category-select",
      ) as HTMLSelectElement;
      const values = Array.from(select.options).map((o) => o.value);
      expect(values).toEqual(["bug", "feature", "doc", "praise", "other"]);
    });

    it("calls onClose when Cancel is clicked without opening any tab", async () => {
      const onClose = jest.fn();
      renderDialog({ onClose });
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: /cancel/i }));
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(openSpy).not.toHaveBeenCalled();
    });
  });

  describe("category-driven fields", () => {
    it("defaults to the bug category and renders bug-specific fields", () => {
      renderDialog();
      expect(
        screen.getByTestId("feedback-bug-describe-input"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("feedback-bug-repro-input"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("feedback-bug-versions-input"),
      ).toBeInTheDocument();
    });

    it("swaps to feature-request fields when feature is selected", async () => {
      renderDialog();
      await pickCategory("feature");
      expect(
        screen.getByTestId("feedback-feature-solution-input"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("feedback-feature-problem-input"),
      ).toBeInTheDocument();
      expect(
        screen.queryByTestId("feedback-bug-describe-input"),
      ).not.toBeInTheDocument();
    });

    it("swaps to documentation fields when doc is selected", async () => {
      renderDialog();
      await pickCategory("doc");
      expect(
        screen.getByTestId("feedback-doc-issue-input"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("feedback-doc-suggestion-input"),
      ).toBeInTheDocument();
    });

    it("renders a single 'What do you love?' field for praise", async () => {
      renderDialog();
      await pickCategory("praise");
      expect(
        screen.getByTestId("feedback-praise-body-input"),
      ).toBeInTheDocument();
      expect(
        screen.queryByTestId("feedback-bug-describe-input"),
      ).not.toBeInTheDocument();
    });

    it("renders a generic body field for other", async () => {
      renderDialog();
      await pickCategory("other");
      expect(
        screen.getByTestId("feedback-other-body-input"),
      ).toBeInTheDocument();
    });

    it("clears prior fields when switching categories", async () => {
      renderDialog();
      const user = userEvent.setup();
      const describe = screen.getByTestId(
        "feedback-bug-describe-input",
      ) as HTMLTextAreaElement;
      await user.click(describe);
      await user.paste("Original bug description text here");
      expect(describe.value).toBe("Original bug description text here");

      await user.selectOptions(
        screen.getByTestId("feedback-category-select"),
        "praise",
      );
      await user.selectOptions(
        screen.getByTestId("feedback-category-select"),
        "bug",
      );

      // After switching away and back, the bug-describe field should be empty.
      const describeAfter = screen.getByTestId(
        "feedback-bug-describe-input",
      ) as HTMLTextAreaElement;
      expect(describeAfter.value).toBe("");
    });
  });

  describe("submit gate", () => {
    it("disables Continue on GitHub until the primary field reaches the minimum length", async () => {
      renderDialog();
      const submit = screen.getByTestId("feedback-submit-button");
      expect(submit).toBeDisabled();

      const user = userEvent.setup();
      const describe = screen.getByTestId("feedback-bug-describe-input");
      await user.click(describe);
      await user.paste("short");
      expect(submit).toBeDisabled();

      await user.click(describe);
      await user.paste("This is definitely long enough now");
      expect(submit).not.toBeDisabled();
    });

    it("Cancel and missing primary field never opens a tab", async () => {
      renderDialog();
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: /cancel/i }));
      expect(openSpy).not.toHaveBeenCalled();
    });
  });

  describe("URL produced by submission", () => {
    it("submits a bug to the bug_report template with GUI + Bug: triage labels", async () => {
      const onClose = jest.fn();
      renderDialog({ onClose });
      const user = userEvent.setup();
      const describe = screen.getByTestId("feedback-bug-describe-input");
      await user.click(describe);
      await user.paste("Chat window crashes on empty send");

      await user.click(screen.getByTestId("feedback-submit-button"));

      expect(openSpy).toHaveBeenCalledTimes(1);
      const url = openSpy.mock.calls[0][0] as string;
      expect(url).toMatch(
        /^https:\/\/github\.com\/(microsoft|Microsoft)\/PyRIT\/issues\/new\?/,
      );
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      expect(params.get("template")).toBe("bug_report.md");
      expect(params.get("labels")).toBe("GUI,bug");
      expect(params.get("title")).toContain("[Co-PyRIT Bug]");
      expect(params.get("body")).toContain("#### Describe the bug");
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("submits praise to the praise template with GUI + praise labels", async () => {
      renderDialog();
      const user = await pickCategory("praise");
      const body = screen.getByTestId("feedback-praise-body-input");
      await user.click(body);
      await user.paste("Co-PyRIT is fantastic, thanks team!");

      await user.click(screen.getByTestId("feedback-submit-button"));

      expect(openSpy).toHaveBeenCalledTimes(1);
      const url = openSpy.mock.calls[0][0] as string;
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      expect(params.get("template")).toBe("praise.md");
      expect(params.get("labels")).toBe("GUI,praise");
      expect(params.get("title")).toContain("[Co-PyRIT Praise]");
      expect(params.get("body")).toContain("#### What do you love?");
    });

    it("submits feature requests to feature_request with the enhancement label", async () => {
      renderDialog();
      const user = await pickCategory("feature");
      const solution = screen.getByTestId("feedback-feature-solution-input");
      await user.click(solution);
      await user.paste("Persist chat history across restarts.");

      await user.click(screen.getByTestId("feedback-submit-button"));

      expect(openSpy).toHaveBeenCalledTimes(1);
      const url = openSpy.mock.calls[0][0] as string;
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      expect(params.get("template")).toBe("feature_request.md");
      expect(params.get("labels")).toBe("GUI,enhancement");
      expect(params.get("body")).toContain(
        "#### Describe the solution you'd like",
      );
    });

    it("submits doc improvements to doc_improvement with Documentation label", async () => {
      renderDialog();
      const user = await pickCategory("doc");
      const issue = screen.getByTestId("feedback-doc-issue-input");
      await user.click(issue);
      await user.paste("Quickstart references old install command");

      await user.click(screen.getByTestId("feedback-submit-button"));

      expect(openSpy).toHaveBeenCalledTimes(1);
      const url = openSpy.mock.calls[0][0] as string;
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      expect(params.get("template")).toBe("doc_improvement.md");
      expect(params.get("labels")).toBe("GUI,documentation");
    });

    it("submits 'other' to the blank template with only the GUI label", async () => {
      renderDialog();
      const user = await pickCategory("other");
      const body = screen.getByTestId("feedback-other-body-input");
      await user.click(body);
      await user.paste("Random thought — could you support dark mode?");

      await user.click(screen.getByTestId("feedback-submit-button"));

      expect(openSpy).toHaveBeenCalledTimes(1);
      const url = openSpy.mock.calls[0][0] as string;
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      expect(params.get("template")).toBe("blank_template.md");
      expect(params.get("labels")).toBe("GUI");
    });

    it("omits the contact section when the contact field is blank", async () => {
      renderDialog();
      const user = userEvent.setup();
      const describe = screen.getByTestId("feedback-bug-describe-input");
      await user.click(describe);
      await user.paste("Some sufficiently long bug description here");

      await user.click(screen.getByTestId("feedback-submit-button"));

      const url = openSpy.mock.calls[0][0] as string;
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      expect(params.get("body") ?? "").not.toContain("#### Preferred contact");
    });

    it("includes the contact section when one is provided", async () => {
      renderDialog();
      const user = userEvent.setup();
      const describe = screen.getByTestId("feedback-bug-describe-input");
      await user.click(describe);
      await user.paste("Some sufficiently long bug description here");
      const contact = screen.getByTestId("feedback-contact-input");
      await user.click(contact);
      await user.paste("alice@contoso.com");

      await user.click(screen.getByTestId("feedback-submit-button"));

      const url = openSpy.mock.calls[0][0] as string;
      const params = new URLSearchParams(url.split("?")[1] ?? "");
      expect(params.get("body") ?? "").toContain("#### Preferred contact");
      expect(params.get("body") ?? "").toContain("alice@contoso.com");
    });

    it("opens the new tab with noopener,noreferrer security attributes", async () => {
      renderDialog();
      const user = await pickCategory("praise");
      const body = screen.getByTestId("feedback-praise-body-input");
      await user.click(body);
      await user.paste("Awesome experience all around");

      await user.click(screen.getByTestId("feedback-submit-button"));

      const [, target, features] = openSpy.mock.calls[0];
      expect(target).toBe("_blank");
      expect(features).toBe("noopener,noreferrer");
    });
  });

  describe("secret detection", () => {
    it("does not show the secret warning for plain prose", async () => {
      renderDialog();
      const user = userEvent.setup();
      const describe = screen.getByTestId("feedback-bug-describe-input");
      await user.click(describe);
      await user.paste("Plain feedback with no secrets at all.");
      expect(
        screen.queryByTestId("feedback-secret-warning"),
      ).not.toBeInTheDocument();
    });

    it("shows the secret warning when a token-like value appears in any field", async () => {
      renderDialog();
      const user = userEvent.setup();
      const repro = screen.getByTestId("feedback-bug-repro-input");
      await user.click(repro);
      await user.paste(
        "Run with key sk-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345 to reproduce",
      );

      const warning = await screen.findByTestId("feedback-secret-warning");
      expect(warning).toHaveTextContent(/OpenAI API key/i);
    });

    it("opens the confirm dialog instead of GitHub when submitting with a detected secret", async () => {
      renderDialog();
      const user = userEvent.setup();
      const describe = screen.getByTestId("feedback-bug-describe-input");
      await user.click(describe);
      await user.paste(
        "Repro: token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa breaks the run",
      );
      await user.click(screen.getByTestId("feedback-submit-button"));

      expect(
        await screen.findByTestId("feedback-confirm-dialog"),
      ).toBeInTheDocument();
      expect(openSpy).not.toHaveBeenCalled();
    });

    it("cancels submission when the user clicks 'Go back and fix'", async () => {
      const onClose = jest.fn();
      renderDialog({ onClose });
      const user = userEvent.setup();
      const describe = screen.getByTestId("feedback-bug-describe-input");
      await user.click(describe);
      await user.paste(
        "Repro: token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa breaks the run",
      );
      await user.click(screen.getByTestId("feedback-submit-button"));
      await user.click(await screen.findByTestId("feedback-confirm-cancel"));

      expect(openSpy).not.toHaveBeenCalled();
      expect(onClose).not.toHaveBeenCalled();
    });

    it("proceeds to GitHub when the user explicitly clicks 'Submit anyway'", async () => {
      const onClose = jest.fn();
      renderDialog({ onClose });
      const user = userEvent.setup();
      const describe = screen.getByTestId("feedback-bug-describe-input");
      await user.click(describe);
      await user.paste(
        "Repro: token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa breaks the run",
      );
      await user.click(screen.getByTestId("feedback-submit-button"));
      await user.click(await screen.findByTestId("feedback-confirm-submit"));

      expect(openSpy).toHaveBeenCalledTimes(1);
      expect(onClose).toHaveBeenCalledTimes(1);
    });
  });

  it("resets the form when the dialog is reopened", async () => {
    const { unmount } = renderDialog();
    const user = userEvent.setup();
    const describe = screen.getByTestId(
      "feedback-bug-describe-input",
    ) as HTMLTextAreaElement;
    await user.click(describe);
    await user.paste("Some prior content that should be cleared");

    // Unmount and remount to simulate the conditional rendering in App.tsx.
    unmount();
    renderDialog();

    const describeAfter = screen.getByTestId(
      "feedback-bug-describe-input",
    ) as HTMLTextAreaElement;
    expect(describeAfter.value).toBe("");
  });
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import { SecretWarning } from "./SecretWarning";
import type { SecretMatch } from "./detectSecrets";

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
);

interface RenderOverrides {
  matches?: SecretMatch[];
  confirmOpen?: boolean;
  onConfirmOpenChange?: (open: boolean) => void;
  onConfirmSubmit?: () => void;
}

function renderWarning(overrides: RenderOverrides = {}) {
  const props = {
    matches: [],
    confirmOpen: false,
    onConfirmOpenChange: jest.fn(),
    onConfirmSubmit: jest.fn(),
    ...overrides,
  };
  const utils = render(
    <TestWrapper>
      <SecretWarning {...props} />
    </TestWrapper>,
  );
  return { ...utils, props };
}

const anthropicMatch: SecretMatch = {
  ruleId: "anthropic-api-key",
  label: "Anthropic API key",
  count: 1,
};

const openaiMatch: SecretMatch = {
  ruleId: "openai-api-key",
  label: "OpenAI API key",
  count: 2,
};

const ghPatMatch: SecretMatch = {
  ruleId: "github-pat",
  label: "GitHub personal access token",
  count: 1,
};

describe("SecretWarning", () => {
  describe("inline banner", () => {
    it("renders nothing when there are no matches and the confirm modal is closed", () => {
      const { container } = renderWarning();
      // FluentProvider wraps children in a div, so assert the wrapper is empty
      // rather than the whole container.
      expect(container.firstChild).toBeEmptyDOMElement();
      expect(screen.queryByTestId("feedback-secret-warning")).toBeNull();
      expect(screen.queryByTestId("feedback-confirm-dialog")).toBeNull();
    });

    it("renders the banner when there is at least one match", () => {
      renderWarning({ matches: [anthropicMatch] });
      expect(screen.getByTestId("feedback-secret-warning")).toBeInTheDocument();
      expect(screen.getByText("Possible secret detected")).toBeInTheDocument();
    });

    it("includes the matched rule label in the banner message", () => {
      renderWarning({ matches: [openaiMatch] });
      const banner = screen.getByTestId("feedback-secret-warning");
      expect(banner).toHaveTextContent("OpenAI API key");
    });

    it("joins multiple match labels with a comma and a space", () => {
      renderWarning({ matches: [anthropicMatch, openaiMatch, ghPatMatch] });
      const banner = screen.getByTestId("feedback-secret-warning");
      expect(banner).toHaveTextContent(
        "Anthropic API key, OpenAI API key, GitHub personal access token",
      );
    });

    it("warns the user that GitHub issues are public", () => {
      renderWarning({ matches: [anthropicMatch] });
      const banner = screen.getByTestId("feedback-secret-warning");
      expect(banner).toHaveTextContent(/GitHub issues are public/i);
      expect(banner).toHaveTextContent(/remove before continuing/i);
    });

    it("does not leak the matched secret value (only the rule label)", () => {
      // SecretMatch by design carries no raw substring; this test pins that
      // contract at the component layer so a future regression that adds a
      // `value` field would still not appear in the rendered output.
      const sneakyMatch = {
        ...anthropicMatch,
        // @ts-expect-error -- intentionally extra field to prove it isn't rendered
        value: "sk-ant-SUPER_SECRET_VALUE_DO_NOT_RENDER",
      } as SecretMatch;

      renderWarning({ matches: [sneakyMatch] });
      const banner = screen.getByTestId("feedback-secret-warning");
      expect(banner).not.toHaveTextContent("SUPER_SECRET_VALUE_DO_NOT_RENDER");
    });
  });

  describe("confirm modal", () => {
    it("does not render the modal when confirmOpen is false", () => {
      renderWarning({ matches: [anthropicMatch], confirmOpen: false });
      expect(screen.queryByTestId("feedback-confirm-dialog")).toBeNull();
    });

    it("does not render the modal when confirmOpen is true but matches is empty", () => {
      // Defensive: the parent should never open the modal with no matches,
      // but if it does we should not show a meaningless "may contain: ." prompt.
      renderWarning({ matches: [], confirmOpen: true });
      expect(screen.queryByTestId("feedback-confirm-dialog")).toBeNull();
    });

    it("renders the modal when there are matches and confirmOpen is true", () => {
      renderWarning({ matches: [anthropicMatch], confirmOpen: true });
      expect(
        screen.getByTestId("feedback-confirm-dialog"),
      ).toBeInTheDocument();
      expect(
        screen.getByText("Possible secret in your feedback"),
      ).toBeInTheDocument();
    });

    it("lists the matched labels and reminds the user the issue is public", () => {
      renderWarning({
        matches: [anthropicMatch, openaiMatch],
        confirmOpen: true,
      });
      const modal = screen.getByTestId("feedback-confirm-dialog");
      expect(modal).toHaveTextContent("Anthropic API key, OpenAI API key");
      expect(modal).toHaveTextContent(/github\.com\/microsoft\/PyRIT/);
      expect(modal).toHaveTextContent(/is public/i);
    });

    it("makes the safe 'Go back and fix' action the primary button", () => {
      // Primary/secondary appearance is intentionally inverted so the safe
      // option is highlighted. If a future change swaps them, this test
      // fails loudly.
      renderWarning({ matches: [anthropicMatch], confirmOpen: true });
      const cancelBtn = screen.getByTestId("feedback-confirm-cancel");
      const submitBtn = screen.getByTestId("feedback-confirm-submit");
      expect(cancelBtn).toHaveTextContent("Go back and fix");
      expect(submitBtn).toHaveTextContent("Submit anyway");
    });

    it("calls onConfirmOpenChange(false) when 'Go back and fix' is clicked", async () => {
      const user = userEvent.setup();
      const { props } = renderWarning({
        matches: [anthropicMatch],
        confirmOpen: true,
      });

      await user.click(screen.getByTestId("feedback-confirm-cancel"));

      expect(props.onConfirmOpenChange).toHaveBeenCalledWith(false);
      expect(props.onConfirmSubmit).not.toHaveBeenCalled();
    });

    it("calls onConfirmSubmit when 'Submit anyway' is clicked", async () => {
      const user = userEvent.setup();
      const { props } = renderWarning({
        matches: [anthropicMatch],
        confirmOpen: true,
      });

      await user.click(screen.getByTestId("feedback-confirm-submit"));

      expect(props.onConfirmSubmit).toHaveBeenCalledTimes(1);
      expect(props.onConfirmOpenChange).not.toHaveBeenCalled();
    });

    it("propagates dialog dismissal (e.g. Escape / outside click) to onConfirmOpenChange", async () => {
      const user = userEvent.setup();
      const { props } = renderWarning({
        matches: [anthropicMatch],
        confirmOpen: true,
      });

      await user.keyboard("{Escape}");

      expect(props.onConfirmOpenChange).toHaveBeenCalledWith(false);
    });
  });
});

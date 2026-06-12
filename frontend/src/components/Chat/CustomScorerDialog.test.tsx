import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import CustomScorerDialog from "./CustomScorerDialog";
import { scorersApi } from "../../services/api";
import type { ScorerSummary } from "../../types";

jest.mock("../../services/api", () => ({
  scorersApi: {
    createCustomScorer: jest.fn(),
    updateCustomScorer: jest.fn(),
    deleteCustomScorer: jest.fn(),
    listScorers: jest.fn(),
  },
}));

const mockedScorersApi = scorersApi as jest.Mocked<typeof scorersApi>;

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <FluentProvider theme={webLightTheme}>{children}</FluentProvider>;

const FLOAT_SCALE_BUILTIN: ScorerSummary = {
  scorer_registry_name: "harm_float",
  scorer_type: "FloatScaleScorer",
  score_type: "float_scale",
  tags: [],
  description: null,
  uses_objective: false,
  editable: false,
  custom_config: null,
};

const EXISTING_CUSTOM_TF: ScorerSummary = {
  scorer_registry_name: "my_tf",
  scorer_type: "SelfAskGeneralTrueFalseScorer",
  score_type: "true_false",
  tags: [],
  description: null,
  uses_objective: false,
  editable: true,
  custom_config: {
    kind: "general_true_false",
    system_prompt_format_string: "Is it bad?",
    prompt_format_string: null,
    category: null,
    score_aggregator: "OR",
  },
};

describe("CustomScorerDialog", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("does not render content when closed", () => {
    render(
      <TestWrapper>
        <CustomScorerDialog
          open={false}
          editing={null}
          availableScorers={[]}
          onClose={jest.fn()}
          onSaved={jest.fn()}
        />
      </TestWrapper>
    );
    expect(screen.queryByText("Create custom scorer")).not.toBeInTheDocument();
  });

  it("creates a general_float_scale scorer with valid input", async () => {
    mockedScorersApi.createCustomScorer.mockResolvedValue({
      summary: {
        scorer_registry_name: "new_scale",
        scorer_type: "SelfAskGeneralFloatScaleScorer",
        score_type: "float_scale",
        tags: [],
        description: null,
        uses_objective: false,
        editable: true,
        custom_config: null,
      },
    });
    const onSaved = jest.fn();
    const onClose = jest.fn();

    render(
      <TestWrapper>
        <CustomScorerDialog
          open
          editing={null}
          availableScorers={[]}
          onClose={onClose}
          onSaved={onSaved}
        />
      </TestWrapper>
    );

    const nameInput = screen.getByTestId("custom-scorer-name-input") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "new_scale" } });

    const submitBtn = screen.getByTestId("custom-scorer-submit-btn");
    expect(submitBtn).not.toBeDisabled();
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockedScorersApi.createCustomScorer).toHaveBeenCalledTimes(1);
    });
    const call = mockedScorersApi.createCustomScorer.mock.calls[0][0];
    expect(call.name).toBe("new_scale");
    expect(call.config.kind).toBe("general_float_scale");
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("disables submit for an invalid name", () => {
    render(
      <TestWrapper>
        <CustomScorerDialog
          open
          editing={null}
          availableScorers={[]}
          onClose={jest.fn()}
          onSaved={jest.fn()}
        />
      </TestWrapper>
    );

    const nameInput = screen.getByTestId("custom-scorer-name-input") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "has spaces!" } });
    expect(screen.getByTestId("custom-scorer-submit-btn")).toBeDisabled();
  });

  it("disables submit when name is empty", () => {
    render(
      <TestWrapper>
        <CustomScorerDialog
          open
          editing={null}
          availableScorers={[]}
          onClose={jest.fn()}
          onSaved={jest.fn()}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("custom-scorer-submit-btn")).toBeDisabled();
  });

  it("seeds form fields from `editing` and calls updateCustomScorer on save", async () => {
    mockedScorersApi.updateCustomScorer.mockResolvedValue({
      summary: { ...EXISTING_CUSTOM_TF },
    });
    const onSaved = jest.fn();

    render(
      <TestWrapper>
        <CustomScorerDialog
          open
          editing={EXISTING_CUSTOM_TF}
          availableScorers={[EXISTING_CUSTOM_TF]}
          onClose={jest.fn()}
          onSaved={onSaved}
        />
      </TestWrapper>
    );

    // Name input is disabled in edit mode.
    const nameInput = screen.getByTestId("custom-scorer-name-input") as HTMLInputElement;
    expect(nameInput).toBeDisabled();
    expect(nameInput.value).toBe("my_tf");

    // System prompt seeded.
    const systemPrompt = screen.getByTestId("custom-scorer-system-prompt") as HTMLTextAreaElement;
    expect(systemPrompt.value).toBe("Is it bad?");

    fireEvent.change(systemPrompt, { target: { value: "Updated prompt" } });

    const submitBtn = screen.getByTestId("custom-scorer-submit-btn");
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockedScorersApi.updateCustomScorer).toHaveBeenCalledWith(
        "my_tf",
        expect.objectContaining({
          config: expect.objectContaining({
            kind: "general_true_false",
            system_prompt_format_string: "Updated prompt",
          }),
        })
      );
    });
    expect(onSaved).toHaveBeenCalled();
  });

  it("threshold_wrapper subform is disabled when no float-scale candidates exist", async () => {
    render(
      <TestWrapper>
        <CustomScorerDialog
          open
          editing={null}
          availableScorers={[]}
          onClose={jest.fn()}
          onSaved={jest.fn()}
        />
      </TestWrapper>
    );

    const nameInput = screen.getByTestId("custom-scorer-name-input") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "th" } });

    // Switch to threshold_wrapper via the dropdown.
    const kindDropdown = screen.getByTestId("custom-scorer-kind-dropdown");
    fireEvent.click(kindDropdown);
    const thresholdOption = await screen.findByText(/Threshold wrapper/);
    fireEvent.click(thresholdOption);

    await waitFor(() =>
      expect(screen.getByTestId("custom-scorer-wrapped-dropdown")).toBeInTheDocument()
    );
    // Submit disabled because no wrapped scorer is selected.
    expect(screen.getByTestId("custom-scorer-submit-btn")).toBeDisabled();
  });

  it("threshold_wrapper allows submit once a candidate is picked", async () => {
    mockedScorersApi.createCustomScorer.mockResolvedValue({
      summary: {
        scorer_registry_name: "th",
        scorer_type: "FloatScaleThresholdScorer",
        score_type: "true_false",
        tags: [],
        description: null,
        uses_objective: false,
        editable: true,
        custom_config: null,
      },
    });

    render(
      <TestWrapper>
        <CustomScorerDialog
          open
          editing={null}
          availableScorers={[FLOAT_SCALE_BUILTIN]}
          onClose={jest.fn()}
          onSaved={jest.fn()}
        />
      </TestWrapper>
    );

    const nameInput = screen.getByTestId("custom-scorer-name-input") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "th" } });

    const kindDropdown = screen.getByTestId("custom-scorer-kind-dropdown");
    fireEvent.click(kindDropdown);
    const thresholdOption = await screen.findByText(/Threshold wrapper/);
    fireEvent.click(thresholdOption);

    const wrappedDropdown = await screen.findByTestId("custom-scorer-wrapped-dropdown");
    fireEvent.click(wrappedDropdown);
    const candidate = await screen.findByText("harm_float");
    fireEvent.click(candidate);

    await waitFor(() =>
      expect(screen.getByTestId("custom-scorer-submit-btn")).not.toBeDisabled()
    );
    fireEvent.click(screen.getByTestId("custom-scorer-submit-btn"));

    await waitFor(() => expect(mockedScorersApi.createCustomScorer).toHaveBeenCalled());
    const call = mockedScorersApi.createCustomScorer.mock.calls[0][0];
    expect(call.config.kind).toBe("threshold_wrapper");
    if (call.config.kind === "threshold_wrapper") {
      expect(call.config.wrapped_scorer_registry_name).toBe("harm_float");
    }
  });

  it("surfaces API errors as a MessageBar", async () => {
    mockedScorersApi.createCustomScorer.mockRejectedValue(new Error("duplicate name"));

    render(
      <TestWrapper>
        <CustomScorerDialog
          open
          editing={null}
          availableScorers={[]}
          onClose={jest.fn()}
          onSaved={jest.fn()}
        />
      </TestWrapper>
    );

    const nameInput = screen.getByTestId("custom-scorer-name-input") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "dup" } });
    fireEvent.click(screen.getByTestId("custom-scorer-submit-btn"));

    expect(
      await screen.findByTestId("custom-scorer-submit-error")
    ).toHaveTextContent("duplicate name");
  });
});

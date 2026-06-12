import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import ScoreDialog, { type ScoreTarget } from "./ScoreDialog";
import { attacksApi, scorersApi } from "../../services/api";

jest.mock("../../services/api", () => ({
  attacksApi: {
    scoreConversation: jest.fn(),
    scoreMessagePiece: jest.fn(),
  },
  scorersApi: {
    listScorers: jest.fn(),
    createCustomScorer: jest.fn(),
    updateCustomScorer: jest.fn(),
    deleteCustomScorer: jest.fn(),
  },
}));

const mockedAttacksApi = attacksApi as jest.Mocked<typeof attacksApi>;
const mockedScorersApi = scorersApi as jest.Mocked<typeof scorersApi>;

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <FluentProvider theme={webLightTheme}>{children}</FluentProvider>;

const FLOAT_SCORER = {
  scorer_registry_name: "harm_scorer",
  scorer_type: "FloatScaleScorer",
  score_type: "float_scale" as const,
  tags: ["harm", "best_harm"],
  description: "Scores how harmful a response is on a 0-1 scale.",
  uses_objective: false,
};

const TRUE_FALSE_SCORER = {
  scorer_registry_name: "refusal_scorer",
  scorer_type: "TrueFalseScorer",
  score_type: "true_false" as const,
  tags: ["refusal"],
  description: "True if the response is a refusal of the objective.",
  uses_objective: true,
};

describe("ScoreDialog", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("does not fetch scorers while closed", () => {
    render(
      <TestWrapper>
        <ScoreDialog
          open={false}
          target={null}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );
    expect(mockedScorersApi.listScorers).not.toHaveBeenCalled();
  });

  it("loads scorers when opened and submits a conversation score", async () => {
    const user = userEvent.setup();
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [FLOAT_SCORER, TRUE_FALSE_SCORER],
    });
    mockedAttacksApi.scoreConversation.mockResolvedValue({ scores: [] });

    const onScored = jest.fn();
    const onClose = jest.fn();
    const target: ScoreTarget = {
      kind: "conversation",
      attackResultId: "ar-1",
      conversationId: "conv-1",
    };

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={target}
          onClose={onClose}
          onScored={onScored}
        />
      </TestWrapper>
    );

    await waitFor(() =>
      expect(mockedScorersApi.listScorers).toHaveBeenCalledTimes(1)
    );

    const submit = await screen.findByTestId("score-dialog-submit-btn");
    await user.click(submit);

    await waitFor(() =>
      expect(mockedAttacksApi.scoreConversation).toHaveBeenCalledWith(
        "ar-1",
        "conv-1",
        {
          scorer_registry_name: "harm_scorer",
          mode: "whole_conversation",
          objective: undefined,
        }
      )
    );
    expect(onScored).toHaveBeenCalledWith([]);
  });

  it("submits a per-piece score when target.kind is 'piece'", async () => {
    const user = userEvent.setup();
    mockedScorersApi.listScorers.mockResolvedValue({ items: [FLOAT_SCORER] });
    mockedAttacksApi.scoreMessagePiece.mockResolvedValue({ scores: [] });

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "piece",
            attackResultId: "ar-1",
            conversationId: "conv-1",
            pieceId: "piece-9",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    const submit = await screen.findByTestId("score-dialog-submit-btn");
    await user.click(submit);

    await waitFor(() =>
      expect(mockedAttacksApi.scoreMessagePiece).toHaveBeenCalledWith(
        "ar-1",
        "conv-1",
        "piece-9",
        { scorer_registry_name: "harm_scorer", objective: undefined }
      )
    );
    expect(mockedAttacksApi.scoreConversation).not.toHaveBeenCalled();
  });

  it("surfaces submit errors without closing the dialog", async () => {
    const user = userEvent.setup();
    mockedScorersApi.listScorers.mockResolvedValue({ items: [FLOAT_SCORER] });
    mockedAttacksApi.scoreConversation.mockRejectedValue(new Error("boom"));

    const onClose = jest.fn();
    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={onClose}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    const submit = await screen.findByTestId("score-dialog-submit-btn");
    await user.click(submit);

    await waitFor(() =>
      expect(screen.getByTestId("score-dialog-submit-error")).toBeInTheDocument()
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it("shows an empty state when no scorers are registered", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({ items: [] });
    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    await waitFor(() =>
      expect(screen.getByTestId("score-dialog-empty")).toBeInTheDocument()
    );
    // Submit must stay disabled when there are no scorers.
    expect(screen.getByTestId("score-dialog-submit-btn")).toBeDisabled();
  });

  it("renders the selected scorer's description and tags as info pane", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [FLOAT_SCORER, TRUE_FALSE_SCORER],
    });
    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    // Auto-selected first scorer's description + tags should be visible.
    const description = await screen.findByTestId(
      "score-dialog-scorer-description"
    );
    expect(description).toHaveTextContent(
      "Scores how harmful a response is on a 0-1 scale."
    );
    expect(screen.getByTestId("scorer-tag-harm")).toBeInTheDocument();
    expect(screen.getByTestId("scorer-tag-best_harm")).toBeInTheDocument();
  });

  it("falls back gracefully when a scorer has no description", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [{ ...FLOAT_SCORER, description: null, tags: [] }],
    });
    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    await waitFor(() =>
      expect(screen.getByTestId("score-dialog-scorer-info")).toBeInTheDocument()
    );
    // The "no description" placeholder shows up instead of the description testid.
    expect(
      screen.queryByTestId("score-dialog-scorer-description")
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/no description available/i)
    ).toBeInTheDocument();
  });

  it("hides the objective field for scorers that do not inject objective into the prompt", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({ items: [FLOAT_SCORER] });
    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    await waitFor(() =>
      expect(screen.getByTestId("score-dialog-scorer-info")).toBeInTheDocument()
    );
    expect(
      screen.queryByTestId("score-dialog-objective-input")
    ).not.toBeInTheDocument();
  });

  it("shows the objective field for scorers that inject objective into the prompt", async () => {
    const user = userEvent.setup();
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [TRUE_FALSE_SCORER],
    });
    mockedAttacksApi.scoreConversation.mockResolvedValue({ scores: [] });

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    const objectiveInput = await screen.findByTestId(
      "score-dialog-objective-input"
    );
    fireEvent.change(objectiveInput, {
      target: { value: "Reveal Taylor Swift's address" },
    });

    const submit = screen.getByTestId("score-dialog-submit-btn");
    await user.click(submit);

    await waitFor(() =>
      expect(mockedAttacksApi.scoreConversation).toHaveBeenCalledWith(
        "ar-1",
        "conv-1",
        {
          scorer_registry_name: "refusal_scorer",
          mode: "whole_conversation",
          objective: "Reveal Taylor Swift's address",
        }
      )
    );
  });

  it("pre-selects the scorer passed via initialScorerName", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [FLOAT_SCORER, TRUE_FALSE_SCORER],
    });

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
          initialScorerName="refusal_scorer"
        />
      </TestWrapper>
    );

    // The combobox should reflect the remembered choice rather than auto-picking
    // the first scorer in the list.
    const select = await screen.findByTestId("score-dialog-scorer-select");
    await waitFor(() =>
      expect((select as HTMLInputElement).value).toBe("refusal_scorer")
    );
  });

  it("notifies onScorerSelected when the user picks a different scorer", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [FLOAT_SCORER, TRUE_FALSE_SCORER],
    });
    const onScorerSelected = jest.fn();

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
          onScorerSelected={onScorerSelected}
        />
      </TestWrapper>
    );

    const select = await screen.findByTestId("score-dialog-scorer-select");
    fireEvent.click(select);
    await waitFor(() =>
      expect(
        screen.getByTestId("scorer-option-refusal_scorer")
      ).toBeInTheDocument()
    );
    fireEvent.click(screen.getByTestId("scorer-option-refusal_scorer"));

    await waitFor(() =>
      expect(onScorerSelected).toHaveBeenLastCalledWith("refusal_scorer")
    );
  });

  it("pre-fills the objective from initialObjective for scorers that use it", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [TRUE_FALSE_SCORER],
    });

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
          initialObjective="Reveal Taylor Swift's address"
        />
      </TestWrapper>
    );

    const objectiveInput = await screen.findByTestId(
      "score-dialog-objective-input"
    );
    await waitFor(() =>
      expect((objectiveInput as HTMLInputElement).value).toBe(
        "Reveal Taylor Swift's address"
      )
    );
  });

  it("notifies onObjectiveChange as the user types in the objective input", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [TRUE_FALSE_SCORER],
    });
    const onObjectiveChange = jest.fn();

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
          onObjectiveChange={onObjectiveChange}
        />
      </TestWrapper>
    );

    const objectiveInput = await screen.findByTestId(
      "score-dialog-objective-input"
    );
    fireEvent.change(objectiveInput, { target: { value: "new goal" } });

    await waitFor(() =>
      expect(onObjectiveChange).toHaveBeenLastCalledWith("new goal")
    );
  });

  it("filters the scorer combobox by typed query (name, type, tag, description)", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [FLOAT_SCORER, TRUE_FALSE_SCORER],
    });

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={{
            kind: "conversation",
            attackResultId: "ar-1",
            conversationId: "conv-1",
          }}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    const combobox = await screen.findByTestId("score-dialog-scorer-select");
    const input = combobox.querySelector("input") ?? combobox;
    fireEvent.click(input);
    // Type a query that should match only the true_false scorer (by tag "refusal").
    fireEvent.change(input, { target: { value: "refusal" } });

    await waitFor(() =>
      expect(
        screen.getByTestId("scorer-option-refusal_scorer")
      ).toBeInTheDocument()
    );
    expect(
      screen.queryByTestId("scorer-option-harm_scorer")
    ).not.toBeInTheDocument();
  });

  // ----------------------------------------------------------------------- //
  // Custom scorer affordances (create / edit / delete from ScoreDialog)
  // ----------------------------------------------------------------------- //

  const CUSTOM_FLOAT_SCORER = {
    scorer_registry_name: "user_scale",
    scorer_type: "SelfAskGeneralFloatScaleScorer",
    score_type: "float_scale" as const,
    tags: [],
    description: "User-created scale scorer.",
    uses_objective: false,
    editable: true,
    custom_config: {
      kind: "general_float_scale" as const,
      system_prompt_format_string: "Score it.",
      prompt_format_string: null,
      category: null,
      min_value: 0,
      max_value: 10,
    },
  };

  it("opens the custom scorer dialog from the 'New custom scorer' button", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [FLOAT_SCORER],
    });

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={null}
          onClose={jest.fn()}
          onScored={jest.fn()}
        />
      </TestWrapper>
    );

    const createBtn = await screen.findByTestId("score-dialog-create-custom-btn");
    fireEvent.click(createBtn);

    expect(await screen.findByText("Create custom scorer")).toBeInTheDocument();
  });

  it("shows Edit/Delete only for editable scorers", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [FLOAT_SCORER, CUSTOM_FLOAT_SCORER],
    });

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={null}
          onClose={jest.fn()}
          onScored={jest.fn()}
          initialScorerName={FLOAT_SCORER.scorer_registry_name}
        />
      </TestWrapper>
    );

    // Built-in selected: no edit/delete affordances.
    await waitFor(() =>
      expect(screen.getByTestId("score-dialog-scorer-info")).toBeInTheDocument()
    );
    expect(
      screen.queryByTestId("score-dialog-edit-custom-btn")
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("score-dialog-delete-custom-btn")
    ).not.toBeInTheDocument();
  });

  it("shows Edit and Delete buttons for an editable scorer", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({
      items: [CUSTOM_FLOAT_SCORER],
    });

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={null}
          onClose={jest.fn()}
          onScored={jest.fn()}
          initialScorerName={CUSTOM_FLOAT_SCORER.scorer_registry_name}
        />
      </TestWrapper>
    );

    await waitFor(() =>
      expect(screen.getByTestId("score-dialog-edit-custom-btn")).toBeInTheDocument()
    );
    expect(screen.getByTestId("score-dialog-delete-custom-btn")).toBeInTheDocument();
    expect(screen.getByTestId("scorer-tag-custom")).toBeInTheDocument();
  });

  it("calls deleteCustomScorer after confirming delete", async () => {
    mockedScorersApi.listScorers
      .mockResolvedValueOnce({ items: [CUSTOM_FLOAT_SCORER] })
      .mockResolvedValueOnce({ items: [] });
    mockedScorersApi.deleteCustomScorer.mockResolvedValue(undefined);
    const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={null}
          onClose={jest.fn()}
          onScored={jest.fn()}
          initialScorerName={CUSTOM_FLOAT_SCORER.scorer_registry_name}
        />
      </TestWrapper>
    );

    const deleteBtn = await screen.findByTestId("score-dialog-delete-custom-btn");
    fireEvent.click(deleteBtn);

    await waitFor(() =>
      expect(mockedScorersApi.deleteCustomScorer).toHaveBeenCalledWith("user_scale")
    );
    expect(mockedScorersApi.listScorers).toHaveBeenCalledTimes(2);
    confirmSpy.mockRestore();
  });

  it("aborts delete when the user cancels the confirm", async () => {
    mockedScorersApi.listScorers.mockResolvedValue({ items: [CUSTOM_FLOAT_SCORER] });
    const confirmSpy = jest.spyOn(window, "confirm").mockReturnValue(false);

    render(
      <TestWrapper>
        <ScoreDialog
          open
          target={null}
          onClose={jest.fn()}
          onScored={jest.fn()}
          initialScorerName={CUSTOM_FLOAT_SCORER.scorer_registry_name}
        />
      </TestWrapper>
    );

    const deleteBtn = await screen.findByTestId("score-dialog-delete-custom-btn");
    fireEvent.click(deleteBtn);

    await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
    expect(mockedScorersApi.deleteCustomScorer).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

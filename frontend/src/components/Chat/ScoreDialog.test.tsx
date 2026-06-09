import { render, screen, waitFor } from "@testing-library/react";
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
};

const TRUE_FALSE_SCORER = {
  scorer_registry_name: "refusal_scorer",
  scorer_type: "TrueFalseScorer",
  score_type: "true_false" as const,
  tags: ["refusal"],
  description: "True if the response is a refusal of the objective.",
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
          mode: "last_message",
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
});

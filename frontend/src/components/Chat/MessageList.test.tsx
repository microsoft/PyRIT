import { render, screen, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FluentProvider, webLightTheme } from "@fluentui/react-components";
import MessageList from "./MessageList";
import { BackendScore, Message } from "../../types";

const originalClientWidthDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientWidth");

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => <FluentProvider theme={webLightTheme}>{children}</FluentProvider>;

describe("MessageList", () => {
  afterEach(() => {
    if (originalClientWidthDescriptor) {
      Object.defineProperty(HTMLElement.prototype, "clientWidth", originalClientWidthDescriptor);
    }
  });

  const mockMessages: Message[] = [
    {
      role: "user",
      content: "Hello, how are you?",
      timestamp: new Date().toISOString(),
    },
    {
      role: "assistant",
      content: "I am doing well, thank you!",
      timestamp: new Date().toISOString(),
    },
    {
      role: "user",
      content: "Can you help me?",
      timestamp: new Date().toISOString(),
    },
  ];

  it("should render empty state when no messages", () => {
    render(
      <TestWrapper>
        <MessageList messages={[]} />
      </TestWrapper>
    );

    expect(
      screen.getByText("There are no messages in this conversation yet.")
    ).toBeInTheDocument();
  });

  it("should render all messages", () => {
    render(
      <TestWrapper>
        <MessageList messages={mockMessages} />
      </TestWrapper>
    );

    expect(screen.getByText("Hello, how are you?")).toBeInTheDocument();
    expect(
      screen.getByText("I am doing well, thank you!")
    ).toBeInTheDocument();
    expect(screen.getByText("Can you help me?")).toBeInTheDocument();
  });

  it("should not render system messages as transcript bubbles", () => {
    const withSystem: Message[] = [
      {
        role: "system",
        content: "You are a pirate.",
        timestamp: new Date().toISOString(),
      },
      ...mockMessages,
    ];

    render(
      <TestWrapper>
        <MessageList messages={withSystem} />
      </TestWrapper>
    );

    expect(screen.queryByText("You are a pirate.")).not.toBeInTheDocument();
    expect(screen.getByText("Hello, how are you?")).toBeInTheDocument();
  });

  it("should render user messages", () => {
    const userMessages: Message[] = [
      {
        role: "user",
        content: "User message test",
        timestamp: new Date().toISOString(),
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={userMessages} />
      </TestWrapper>
    );

    expect(screen.getByText("User message test")).toBeInTheDocument();
  });

  it("should render assistant messages", () => {
    const assistantMessages: Message[] = [
      {
        role: "assistant",
        content: "Assistant message test",
        timestamp: new Date().toISOString(),
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={assistantMessages} />
      </TestWrapper>
    );

    expect(screen.getByText("Assistant message test")).toBeInTheDocument();
  });

  it("should show the message score and its details when present", async () => {
    const user = userEvent.setup();
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Scored response",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-1",
            message_piece_id: "piece-1",
            scorer_type: "SelfAskScaleScorer",
            score_type: "float_scale",
            score_value: "0.9",
            is_objective_score: true,
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            score_category: ["harmful"],
            score_rationale: "The response contains harmful content.",
            timestamp: "2026-02-15T00:01:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    const scoreButton = screen.getByRole("button", {
      name: /score 0.9 from selfaskscalescorer, objective score/i,
    });
    expect(scoreButton).toBeInTheDocument();
    expect(scoreButton).toHaveTextContent("Score: 0.9");

    await user.click(scoreButton);

    expect(screen.getByText("float_scale")).toBeInTheDocument();
    expect(screen.getByText("SelfAskScaleScorer")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("Piece 1 · text")).toBeInTheDocument();
    expect(screen.getByText("harmful")).toBeInTheDocument();
    expect(screen.getByText("The response contains harmful content.")).toBeInTheDocument();
  });

  it("should show an undetermined score in the chip, tooltip, label, and details", async () => {
    const user = userEvent.setup();
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Response without a verdict",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-undetermined",
            message_piece_id: "piece-1",
            scorer_type: "SelfAskScaleScorer",
            score_type: "float_scale",
            score_value: null,
            status: "undetermined",
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:01:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    const scoreButton = screen.getByRole("button", {
      name: /score undetermined from selfaskscalescorer, piece 1 · text/i,
    });
    expect(scoreButton).toHaveTextContent("Score: undetermined");

    await user.hover(scoreButton);
    expect(await screen.findByRole("tooltip")).toHaveTextContent("Score: undetermined");

    await user.unhover(scoreButton);
    await user.click(scoreButton);
    expect(within(screen.getByTestId("message-score-details-0-0")).getByText("undetermined")).toBeInTheDocument();
  });

  it("should preserve a long single-score value outside its ellipsized chip", async () => {
    const user = userEvent.setup();
    const longScoreValue = "a".repeat(200);
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Scored response",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-long",
            message_piece_id: "piece-1",
            scorer_type: "UnknownScorer",
            score_type: "unknown",
            score_value: longScoreValue,
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:00:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    const scoreButton = screen.getByRole("button", {
      name: `Score ${longScoreValue} from UnknownScorer, Piece 1 · text`,
    });
    await user.hover(scoreButton);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(longScoreValue);

    await user.unhover(scoreButton);
    await user.click(scoreButton);
    expect(screen.getByTestId("message-score-details-0-0")).toHaveTextContent(longScoreValue);
  });

  it("should show a stacked score control with tabs for multiple scores", async () => {
    const user = userEvent.setup();
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Scored response",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-new",
            message_piece_id: "piece-2",
            scorer_type: "NewScorer",
            score_type: "float_scale",
            score_value: "0.9",
            pieceIndex: 1,
            pieceType: "text",
            sourceLabel: "Piece 2 · text",
            timestamp: "2026-02-15T00:01:00Z",
          },
          {
            id: "score-old",
            message_piece_id: "piece-1",
            scorer_type: "OldScorer",
            score_type: "true_false",
            score_value: "False",
            is_objective_score: true,
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:00:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    const stackedScoreButton = screen.getByRole("button", {
      name: /view 2 scores, displayed score false from oldscorer, objective score/i,
    });
    expect(stackedScoreButton).toBeInTheDocument();

    await user.click(stackedScoreButton);

    expect(screen.getByRole("tablist", { name: "Scores" })).toBeInTheDocument();
    const objectiveTab = screen.getByRole("tab", {
      name: /score false from oldscorer, objective score/i,
    });
    const auxiliaryTab = screen.getByRole("tab", {
      name: /score 0.9 from newscorer/i,
    });
    expect(screen.getAllByRole("tab")).toEqual([objectiveTab, auxiliaryTab]);
    expect(objectiveTab).toHaveTextContent("False");
    expect(objectiveTab).not.toHaveTextContent("Score:");
    expect(objectiveTab).not.toHaveTextContent("OldScorer");
    expect(objectiveTab).not.toHaveTextContent("Objective");
    expect(auxiliaryTab).toHaveTextContent("0.9");
    expect(auxiliaryTab).not.toHaveTextContent("Score:");
    expect(auxiliaryTab).not.toHaveTextContent("NewScorer");
    expect(objectiveTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", objectiveTab.id);
    expect(screen.getByText("Score:")).toBeInTheDocument();
    expect(screen.getByText("true_false")).toBeInTheDocument();
    expect(screen.getByText("OldScorer")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();

    await user.hover(auxiliaryTab);
    expect(
      await screen.findByText("Score 0.9 from NewScorer, Piece 2 · text")
    ).toBeInTheDocument();
    await user.unhover(auxiliaryTab);

    await user.click(auxiliaryTab);

    expect(auxiliaryTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", auxiliaryTab.id);
    expect(screen.getByText("float_scale")).toBeInTheDocument();
    expect(screen.getByText("NewScorer")).toBeInTheDocument();
    expect(screen.getByText("No")).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /view 2 scores, displayed score false from oldscorer, objective score/i,
      })
    ).toBeInTheDocument();
    expect(stackedScoreButton).toHaveTextContent("Score: False");
  });

  it("should preserve a long stacked-score value outside its ellipsized chip", async () => {
    const user = userEvent.setup();
    const longScoreValue = "b".repeat(200);
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Scored response",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-long",
            message_piece_id: "piece-1",
            scorer_type: "UnknownScorer",
            score_type: "unknown",
            score_value: longScoreValue,
            is_objective_score: true,
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:00:00Z",
          },
          {
            id: "score-short",
            message_piece_id: "piece-1",
            scorer_type: "OtherScorer",
            score_type: "true_false",
            score_value: "False",
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:01:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    const stackedScoreButton = screen.getByRole("button", {
      name: `View 2 scores, displayed score ${longScoreValue} from UnknownScorer, objective score, Piece 1 · text`,
    });
    await user.hover(stackedScoreButton);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(longScoreValue);

    await user.unhover(stackedScoreButton);
    await user.click(stackedScoreButton);
    expect(screen.getByRole("tabpanel")).toHaveTextContent(longScoreValue);
  });

  it("should focus the selected score tab when reopening with the keyboard", async () => {
    const user = userEvent.setup();
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Scored response",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-latest",
            message_piece_id: "piece-1",
            scorer_type: "ScaleScorer",
            score_type: "float_scale",
            score_value: "0.91",
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:01:00Z",
          },
          {
            id: "score-true",
            message_piece_id: "piece-1",
            scorer_type: "BooleanScorer",
            score_type: "true_false",
            score_value: "True",
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:00:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    const trigger = screen.getByRole("button", {
      name: /view 2 scores, displayed score 0.91 from scalescorer/i,
    });
    await user.click(trigger);

    const trueTab = screen.getByRole("tab", {
      name: /score true from booleanscorer/i,
    });
    await user.click(trueTab);
    expect(trueTab).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
    await user.keyboard("{Enter}");

    const reopenedTrueTab = screen.getByRole("tab", {
      name: /score true from booleanscorer/i,
    });
    const reopenedLatestTab = screen.getByRole("tab", {
      name: /score 0.91 from scalescorer/i,
    });
    expect(reopenedTrueTab).toHaveAttribute("aria-selected", "true");
    expect(reopenedTrueTab).toHaveFocus();
    expect(reopenedLatestTab).not.toHaveFocus();
  });

  it("should display the latest score when there is no objective score", async () => {
    const user = userEvent.setup();
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Scored response",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-old",
            message_piece_id: "piece-1",
            scorer_type: "OldScorer",
            score_type: "true_false",
            score_value: "False",
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:00:00Z",
          },
          {
            id: "score-new",
            message_piece_id: "piece-1",
            scorer_type: "NewScorer",
            score_type: "float_scale",
            score_value: "0.9",
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:01:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    const stackedScoreButton = screen.getByRole("button", {
      name: /view 2 scores, displayed score 0.9 from newscorer/i,
    });
    expect(stackedScoreButton).toHaveTextContent("Score: 0.9");

    await user.click(stackedScoreButton);
    await user.click(screen.getByRole("tab", {
      name: /score false from oldscorer/i,
    }));

    expect(screen.getByText("OldScorer")).toBeInTheDocument();
    expect(stackedScoreButton).toHaveTextContent("Score: 0.9");
  });

  it("should not show a stacked control when the message has only one score", () => {
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Scored response",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-1",
            message_piece_id: "piece-1",
            scorer_type: "SoleScorer",
            score_type: "true_false",
            score_value: "True",
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:00:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    expect(
      screen.getByRole("button", { name: /score true from solescorer/i })
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("message-score-stack-0")
    ).not.toBeInTheDocument();
  });

  it("should show every score tab when they fit in the available space", async () => {
    const user = userEvent.setup();
    const scores = ["FirstScorer", "ObjectiveScorer", "ThirdScorer", "OverflowScorer"].map(
      (scorerType, index) => ({
        id: `score-${index}`,
        message_piece_id: "piece-1",
        scorer_type: scorerType,
        score_type: "float_scale",
        score_value: `${index}`,
        is_objective_score: index === 1,
        pieceIndex: 0,
        pieceType: "text",
        sourceLabel: "Piece 1 · text",
        timestamp: `2026-02-15T00:0${index}:00Z`,
      })
    );

    render(
      <TestWrapper>
        <MessageList
          messages={[
            {
              role: "assistant",
              content: "Scored response",
              timestamp: new Date().toISOString(),
              scores,
            },
          ]}
        />
      </TestWrapper>
    );

    await user.click(screen.getByRole("button", { name: /view 4 scores/i }));
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    expect(
      screen.getByRole("tab", { name: /overflowscorer/i })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /more scores/i })
    ).not.toBeInTheDocument();
  });

  it("should move only score tabs that do not fit into the More menu", async () => {
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get() {
        return this.hasAttribute("data-score-tab-bar") ? 250 : 0;
      },
    });
    const user = userEvent.setup();
    const scores = ["FirstScorer", "ObjectiveScorer", "ThirdScorer", "OverflowScorer"].map(
      (scorerType, index) => ({
        id: `score-${index}`,
        message_piece_id: "piece-1",
        scorer_type: scorerType,
        score_type: "float_scale",
        score_value: `${index}`,
        is_objective_score: index === 1,
        pieceIndex: 0,
        pieceType: "text",
        sourceLabel: "Piece 1 · text",
        timestamp: `2026-02-15T00:0${index}:00Z`,
      })
    );

    render(
      <TestWrapper>
        <MessageList
          messages={[
            {
              role: "assistant",
              content: "Scored response",
              timestamp: new Date().toISOString(),
              scores,
            },
          ]}
        />
      </TestWrapper>
    );

    await user.click(screen.getByRole("button", { name: /view 4 scores/i }));
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    const objectiveTab = screen.getByRole("tab", {
      name: /score 1 from objectivescorer, objective score/i,
    });
    expect(objectiveTab).toHaveAttribute("aria-selected", "true");
    const moreScoresButton = screen.getByRole("button", { name: "More scores, 2 hidden" });
    expect(moreScoresButton).toHaveTextContent("More scores");
    await user.click(moreScoresButton);
    expect(objectiveTab).toHaveAttribute("aria-selected", "true");

    const overflowScore = screen.getByRole("menuitem", {
      name: /3 · overflowscorer/i,
    });
    expect(overflowScore).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /objectivescorer/i })
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /firstscorer/i })
    ).not.toBeInTheDocument();

    await user.click(overflowScore);

    expect(
      screen.getByRole("tab", { name: /score 3 from overflowscorer/i })
    ).toHaveAttribute("aria-selected", "true");
    expect(
      screen.queryByRole("tab", { name: /score 0 from firstscorer/i })
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "More scores, 2 hidden" }));
    expect(
      screen.getByRole("menuitem", { name: /0 · firstscorer/i })
    ).toBeInTheDocument();
  });

  it("should keep another score tab visible when only one tab fits naturally", async () => {
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get() {
        return this.hasAttribute("data-score-tab-bar") ? 150 : 0;
      },
    });

    const user = userEvent.setup();
    const scores = ["FirstScorer", "ObjectiveScorer", "ThirdScorer"].map(
      (scorerType, index) => ({
        id: `score-${index}`,
        message_piece_id: "piece-1",
        scorer_type: scorerType,
        score_type: "float_scale",
        score_value: `${index}`,
        is_objective_score: index === 1,
        pieceIndex: 0,
        pieceType: "text",
        sourceLabel: "Piece 1 · text",
        timestamp: `2026-02-15T00:0${index}:00Z`,
      })
    );

    render(
      <TestWrapper>
        <MessageList
          messages={[
            {
              role: "assistant",
              content: "Scored response",
              timestamp: new Date().toISOString(),
              scores,
            },
          ]}
        />
      </TestWrapper>
    );

    await user.click(screen.getByRole("button", { name: /view 3 scores/i }));
    expect(screen.getAllByRole("tab")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "More scores, 1 hidden" }));
    await user.click(screen.getByRole("menuitem", { name: /2 · thirdscorer/i }));

    expect(screen.getAllByRole("tab")).toHaveLength(2);
    expect(
      screen.getByRole("tab", { name: /score 2 from thirdscorer/i })
    ).toHaveAttribute("aria-selected", "true");
    expect(
      screen.getByRole("tab", { name: /objectivescorer/i })
    ).toBeInTheDocument();
  });

  it("should disambiguate identical overflow scores with piece, category, and ordinal context", async () => {
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get() {
        return this.hasAttribute("data-score-tab-bar") ? 150 : 0;
      },
    });
    const user = userEvent.setup();
    const scores = [
      {
        id: "score-objective",
        message_piece_id: "piece-1",
        scorer_type: "ObjectiveScorer",
        score_type: "true_false",
        score_value: "True",
        is_objective_score: true,
        pieceIndex: 0,
        pieceType: "text",
        sourceLabel: "Piece 1 · text",
        timestamp: "2026-02-15T00:00:00Z",
      },
      {
        id: "score-visible",
        message_piece_id: "piece-1",
        scorer_type: "VisibleScorer",
        score_type: "float_scale",
        score_value: "0.1",
        pieceIndex: 0,
        pieceType: "text",
        sourceLabel: "Piece 1 · text",
        timestamp: "2026-02-15T00:01:00Z",
      },
      ...["piece-2", "piece-3", "piece-3"].map((pieceId, index) => ({
        id: `score-shared-${index}`,
        message_piece_id: pieceId,
        scorer_type: "SharedScorer",
        score_type: "float_scale",
        score_value: "0.5",
        score_category: index === 0 ? ["alpha"] : ["beta"],
        pieceIndex: index === 0 ? 1 : 2,
        pieceType: "text",
        sourceLabel: index === 0 ? "Piece 2 · text" : "Piece 3 · text",
        timestamp: `2026-02-15T00:0${index + 2}:00Z`,
      })),
    ];

    render(
      <TestWrapper>
        <MessageList
          messages={[{
            role: "assistant",
            content: "Scored response",
            timestamp: new Date().toISOString(),
            scores,
          }]}
        />
      </TestWrapper>
    );

    await user.click(screen.getByRole("button", { name: /view 5 scores/i }));
    await user.click(screen.getByRole("button", { name: "More scores, 3 hidden" }));

    expect(screen.getByRole("menuitem", {
      name: "0.5 · SharedScorer · Piece 2 · text · Categories: alpha",
    })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", {
      name: "0.5 · SharedScorer · Piece 3 · text · Categories: beta · 1 of 2",
    })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", {
      name: "0.5 · SharedScorer · Piece 3 · text · Categories: beta · 2 of 2",
    })).toBeInTheDocument();
  });

  it("should distinguish text and attachment score controls by source label", () => {
    const sharedScores: Array<Omit<BackendScore, "message_piece_id">> = [
      {
        id: "score-objective",
        scorer_type: "SharedScorer",
        score_type: "true_false",
        score_value: "True",
        is_objective_score: true,
        timestamp: "2026-02-15T00:00:00Z",
      },
      {
        id: "score-auxiliary",
        scorer_type: "AuxiliaryScorer",
        score_type: "float_scale",
        score_value: "0.5",
        timestamp: "2026-02-15T00:01:00Z",
      },
    ];
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "Scored text",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "test.png",
            url: "data:image/png;base64,iVBORw0KGgo=",
            mimeType: "image/png",
          },
        ],
        displayPieces: [
          {
            type: "text",
            pieceId: "piece-1",
            pieceIndex: 0,
            content: "Scored text",
            scores: sharedScores.map((score) => ({
              ...score,
              id: `${score.id}-text`,
              message_piece_id: "piece-1",
              pieceIndex: 0,
              pieceType: "text",
              sourceLabel: "Piece 1 · text",
            })),
          },
          {
            type: "media",
            pieceId: "piece-2",
            pieceIndex: 1,
            attachment: {
              type: "image",
              name: "test.png",
              url: "data:image/png;base64,iVBORw0KGgo=",
              mimeType: "image/png",
            },
            scores: sharedScores.map((score) => ({
              ...score,
              id: `${score.id}-image`,
              message_piece_id: "piece-2",
              pieceIndex: 1,
              pieceType: "image_path",
              sourceLabel: "Piece 2 · image_path · test.png",
            })),
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    expect(
      screen.getByRole("button", {
        name: "View 2 scores, displayed score True from SharedScorer, objective score, Piece 1 · text",
      })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "View 2 scores, displayed score True from SharedScorer, objective score, Piece 2 · image_path · test.png",
      })
    ).toBeInTheDocument();
  });

  it("should render ordered display pieces with only their own scores", () => {
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "First text\nSecond text",
        timestamp: new Date().toISOString(),
        displayPieces: [
          {
            type: "text",
            pieceId: "piece-1",
            pieceIndex: 0,
            content: "First text",
            scores: [
              {
                id: "score-1",
                message_piece_id: "piece-1",
                scorer_type: "FirstScorer",
                score_type: "unknown",
                score_value: "first-only",
                pieceIndex: 0,
                pieceType: "text",
                sourceLabel: "Piece 1 · text",
                timestamp: "2026-02-15T00:00:00Z",
              },
            ],
          },
          {
            type: "media",
            pieceId: "piece-2",
            pieceIndex: 1,
            attachment: {
              type: "image",
              name: "test.png",
              url: "data:image/png;base64,iVBORw0KGgo=",
              mimeType: "image/png",
            },
            scores: [
              {
                id: "score-2",
                message_piece_id: "piece-2",
                scorer_type: "ImageScorer",
                score_type: "unknown",
                score_value: "image-only",
                pieceIndex: 1,
                pieceType: "image_path",
                sourceLabel: "Piece 2 · image_path · test.png",
                timestamp: "2026-02-15T00:01:00Z",
              },
            ],
          },
          {
            type: "text",
            pieceId: "piece-3",
            pieceIndex: 2,
            content: "Second text",
            scores: [
              {
                id: "score-3",
                message_piece_id: "piece-3",
                scorer_type: "SecondScorer",
                score_type: "unknown",
                score_value: "second-only",
                pieceIndex: 2,
                pieceType: "text",
                sourceLabel: "Piece 3 · text",
                timestamp: "2026-02-15T00:02:00Z",
              },
            ],
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    const pieces = screen.getAllByTestId(/^message-piece-0-/);
    expect(pieces).toHaveLength(3);
    expect(pieces[0]).toHaveTextContent("First text");
    expect(within(pieces[0]).getByRole("button", { name: /score first-only from firstscorer/i })).toBeInTheDocument();
    expect(within(pieces[0]).queryByRole("button", { name: /second-only/i })).not.toBeInTheDocument();
    expect(within(pieces[1]).getByAltText("test.png")).toBeInTheDocument();
    expect(within(pieces[1]).getByRole("button", { name: /score image-only from imagescorer/i })).toBeInTheDocument();
    expect(pieces[2]).toHaveTextContent("Second text");
    expect(within(pieces[2]).getByRole("button", { name: /score second-only from secondscorer/i })).toBeInTheDocument();
    expect(within(pieces[2]).queryByRole("button", { name: /first-only/i })).not.toBeInTheDocument();
  });

  it("should render a score-only media piece without an actionable attachment", async () => {
    const user = userEvent.setup();
    const onCopyToInput = jest.fn();
    const scoreOnlyMessage: Message = {
      role: "assistant",
      content: "",
      timestamp: new Date().toISOString(),
      displayPieces: [
        {
          type: "media",
          pieceId: "piece-blocked",
          pieceIndex: 0,
          scores: [
            {
              id: "score-blocked",
              message_piece_id: "piece-blocked",
              scorer_type: "ImageScorer",
              score_type: "true_false",
              score_value: "blocked-media",
              pieceIndex: 0,
              pieceType: "image_path",
              sourceLabel: "Piece 1 · image_path",
              timestamp: "2026-02-15T00:00:00Z",
            },
          ],
        },
      ],
    };

    render(
      <TestWrapper>
        <MessageList messages={[scoreOnlyMessage]} onCopyToInput={onCopyToInput} />
      </TestWrapper>
    );

    const piece = screen.getByTestId("message-piece-0-0");
    expect(
      within(piece).getByRole("button", { name: /score blocked-media from imagescorer/i })
    ).toBeInTheDocument();
    expect(within(piece).queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByTestId("download-btn-0-0")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("copy-to-input-btn-0"));
    expect(onCopyToInput).toHaveBeenCalledWith(0);
  });

  it("should not offer a download action for an attachment with no URL", () => {
    render(
      <TestWrapper>
        <MessageList
          messages={[{
            role: "assistant",
            content: "Blocked media",
            timestamp: new Date().toISOString(),
            attachments: [
              { type: "image", name: "blocked.png", url: "", mimeType: "image/png" },
            ],
          }]}
          onCopyToInput={jest.fn()}
        />
      </TestWrapper>
    );

    expect(screen.queryByTestId("download-btn-0-0")).not.toBeInTheDocument();
  });

  it("should preserve the message-level score test ID for a single display piece", () => {    const scores = ["FirstScorer", "SecondScorer"].map((scorerType, index) => ({
      id: `score-${index}`,
      message_piece_id: "piece-1",
      scorer_type: scorerType,
      score_type: "float_scale",
      score_value: `${index}`,
      pieceIndex: 0,
      pieceType: "text",
      sourceLabel: "Piece 1 · text",
      timestamp: `2026-02-15T00:0${index}:00Z`,
    }));

    render(
      <TestWrapper>
        <MessageList
          messages={[{
            role: "assistant",
            content: "Scored response",
            timestamp: new Date().toISOString(),
            displayPieces: [{
              type: "text",
              pieceId: "piece-1",
              pieceIndex: 0,
              content: "Scored response",
              scores,
            }],
          }]}
        />
      </TestWrapper>
    );

    expect(screen.getByTestId("message-score-stack-0")).toBeInTheDocument();
  });

  it("should not show a score chip when the message has no score", () => {
    render(
      <TestWrapper>
        <MessageList messages={mockMessages} />
      </TestWrapper>
    );

    expect(
      screen.queryByRole("button", { name: /^score /i })
    ).not.toBeInTheDocument();
  });

  it("should show a text score when the converted response is empty", () => {
    const scoredMessages: Message[] = [
      {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        scores: [
          {
            id: "score-empty-response",
            message_piece_id: "piece-empty-response",
            scorer_type: "EmptyResponseScorer",
            score_type: "true_false",
            score_value: "True",
            pieceIndex: 0,
            pieceType: "text",
            sourceLabel: "Piece 1 · text",
            timestamp: "2026-02-15T00:00:00Z",
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={scoredMessages} />
      </TestWrapper>
    );

    expect(
      screen.getByRole("button", {
        name: "Score True from EmptyResponseScorer, Piece 1 · text",
      })
    ).toBeInTheDocument();
  });

  it("should show a score chip next to the attachment it was computed on", () => {
    const messagesWithScoredAttachment: Message[] = [
      {
        role: "assistant",
        content: "Here is a caption and a picture",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "test.png",
            url: "data:image/png;base64,iVBORw0KGgo=",
            mimeType: "image/png",
            size: 1024,
          },
        ],
        displayPieces: [
          {
            type: "media",
            pieceId: "piece-image",
            pieceIndex: 0,
            attachment: {
              type: "image",
              name: "test.png",
              url: "data:image/png;base64,iVBORw0KGgo=",
              mimeType: "image/png",
              size: 1024,
            },
            scores: [
              {
                id: "score-image",
                message_piece_id: "piece-image",
                scorer_type: "ImageScorer",
                score_type: "true_false",
                score_value: "True",
                pieceIndex: 0,
                pieceType: "image_path",
                sourceLabel: "Piece 1 · image_path · test.png",
                timestamp: "2026-02-15T00:00:00Z",
              },
            ],
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={messagesWithScoredAttachment} />
      </TestWrapper>
    );

    expect(
      screen.getByRole("button", { name: /score true from imagescorer/i })
    ).toBeInTheDocument();
  });

  describe("structured JSON assistant responses", () => {
    // Targets like PromptShieldTarget return structured JSON instead of
    // natural-language text. Render these as pretty-printed JSON in a <pre>
    // so the user can actually read them.

    it("renders JSON object responses as pretty-printed <pre>", () => {
      const messages: Message[] = [
        {
          role: "assistant",
          content: '{"userPromptAnalysis":{"attackDetected":false},"documentsAnalysis":[]}',
          timestamp: new Date().toISOString(),
        },
      ];
      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );
      const block = screen.getByTestId("message-json-0");
      expect(block.tagName).toBe("PRE");
      // Pretty-printed (2-space indent) and round-trips to the original payload.
      const text = block.textContent ?? "";
      expect(text).toContain('"userPromptAnalysis": {\n');
      expect(text).toContain('"attackDetected": false');
      expect(JSON.parse(text)).toEqual({
        userPromptAnalysis: { attackDetected: false },
        documentsAnalysis: [],
      });
    });

    it("renders JSON array responses as pretty-printed <pre>", () => {
      const messages: Message[] = [
        {
          role: "assistant",
          content: '[{"label":"safe","score":0.97},{"label":"unsafe","score":0.03}]',
          timestamp: new Date().toISOString(),
        },
      ];
      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );
      const block = screen.getByTestId("message-json-0");
      expect(block.tagName).toBe("PRE");
      expect(JSON.parse(block.textContent ?? "")).toEqual([
        { label: "safe", score: 0.97 },
        { label: "unsafe", score: 0.03 },
      ]);
    });

    it("does not reformat plain text assistant content", () => {
      const messages: Message[] = [
        {
          role: "assistant",
          content: "Hello there!",
          timestamp: new Date().toISOString(),
        },
      ];
      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );
      expect(screen.queryByTestId("message-json-0")).not.toBeInTheDocument();
      expect(screen.getByText("Hello there!")).toBeInTheDocument();
    });

    it("does not reformat malformed JSON-shaped content", () => {
      const messages: Message[] = [
        {
          role: "assistant",
          content: "{not really json",
          timestamp: new Date().toISOString(),
        },
      ];
      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );
      expect(screen.queryByTestId("message-json-0")).not.toBeInTheDocument();
      expect(screen.getByText("{not really json")).toBeInTheDocument();
    });

    it("does not reformat user messages even if they are JSON-shaped", () => {
      // A user pasting JSON into the input shouldn't have it silently
      // reformatted in their own bubble.
      const messages: Message[] = [
        {
          role: "user",
          content: '{"prompt":"hello"}',
          timestamp: new Date().toISOString(),
        },
      ];
      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );
      expect(screen.queryByTestId("message-json-0")).not.toBeInTheDocument();
      expect(screen.getByText('{"prompt":"hello"}')).toBeInTheDocument();
    });

    it("does not reformat scalar JSON values", () => {
      // "true", "42", '"hello"' are all valid JSON but rendering them as
      // pretty-printed JSON gains nothing — keep them as plain text.
      for (const scalar of ["true", "42", '"hello"', "null"]) {
        const { unmount } = render(
          <TestWrapper>
            <MessageList
              messages={[
                {
                  role: "assistant",
                  content: scalar,
                  timestamp: new Date().toISOString(),
                },
              ]}
            />
          </TestWrapper>
        );
        expect(screen.queryByTestId("message-json-0")).not.toBeInTheDocument();
        unmount();
      }
    });

    it("does not reformat content while a message is still loading", () => {
      // Streaming responses pass through with isLoading=true; the
      // intermediate text may temporarily look JSON-ish.
      const messages: Message[] = [
        {
          role: "assistant",
          content: '{"partial":',
          isLoading: true,
          timestamp: new Date().toISOString(),
        },
      ];
      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );
      expect(screen.queryByTestId("message-json-0")).not.toBeInTheDocument();
    });
  });

  describe("bubble class composition", () => {
    // Regression guard for an earlier bug where the user-bubble background
    // override silently lost to the assistant-bubble base style. The cause was
    // string-concatenated class names — Griffel's atomic CSS doesn't dedupe
    // conflicting properties unless mergeClasses is used. We assert here that
    // the two bubble containers receive distinguishable className strings, so
    // the override always has a chance to win.
    it("renders user and assistant bubbles with distinct class signatures", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={[
              {
                role: "user",
                content: "u",
                timestamp: new Date().toISOString(),
              },
              {
                role: "assistant",
                content: "a",
                timestamp: new Date().toISOString(),
              },
            ]}
          />
        </TestWrapper>
      );
      const userBubble = screen.getByTestId("message-bubble-0");
      const assistantBubble = screen.getByTestId("message-bubble-1");
      // The two bubble class strings must differ — otherwise the user
      // override silently lost (which was the original bug).
      expect(userBubble.className).not.toBe(assistantBubble.className);
      // Both bubbles must carry at least one style hook (catches a future
      // refactor that drops the className entirely).
      expect(userBubble.className.trim()).not.toBe('');
      expect(assistantBubble.className.trim()).not.toBe('');
      // The user bubble must carry at least one style hook the assistant
      // bubble doesn't have — that's the override actually being applied.
      const userClasses = userBubble.className.split(/\s+/).filter(Boolean);
      const assistantClasses = new Set(assistantBubble.className.split(/\s+/).filter(Boolean));
      const userOnly = userClasses.filter(c => !assistantClasses.has(c));
      expect(userOnly.length).toBeGreaterThan(0);
    });
  });

  it("should handle messages with image attachments", () => {
    const messagesWithAttachments: Message[] = [
      {
        role: "assistant",
        content: "Here is your image",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "test.png",
            url: "data:image/png;base64,iVBORw0KGgo=",
            mimeType: "image/png",
            size: 1024,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={messagesWithAttachments} />
      </TestWrapper>
    );

    expect(screen.getByText("Here is your image")).toBeInTheDocument();
    const img = screen.getByAltText("test.png");
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute(
      "src",
      "data:image/png;base64,iVBORw0KGgo="
    );
  });

  it("should handle messages with audio attachments", () => {
    const messagesWithAudio: Message[] = [
      {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "audio",
            name: "audio.wav",
            url: "data:audio/wav;base64,UklGRg==",
            mimeType: "audio/wav",
            size: 512,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={messagesWithAudio} />
      </TestWrapper>
    );

    const audioElements = document.querySelectorAll("audio");
    expect(audioElements.length).toBeGreaterThan(0);
  });

  it("should handle messages with video attachments", () => {
    const messagesWithVideo: Message[] = [
      {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "video",
            name: "video.mp4",
            url: "data:video/mp4;base64,dmlkZW8=",
            mimeType: "video/mp4",
            size: 2048,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={messagesWithVideo} />
      </TestWrapper>
    );

    const videoElements = document.querySelectorAll("video");
    expect(videoElements.length).toBeGreaterThan(0);
  });

  it("should render error messages", () => {
    const errorMessages: Message[] = [
      {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        error: {
          type: "blocked",
          description: "Content was filtered by safety system",
        },
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={errorMessages} />
      </TestWrapper>
    );

    expect(
      screen.getByText(/Content was filtered by safety system/)
    ).toBeInTheDocument();
  });

  it("should render multiple messages in order", () => {
    render(
      <TestWrapper>
        <MessageList messages={mockMessages} />
      </TestWrapper>
    );

    const messageElements = screen.getAllByText(/Hello|doing well|help/);
    expect(messageElements.length).toBeGreaterThanOrEqual(3);
  });

  it("should render simulated_assistant with distinct avatar", () => {
    const simMessages: Message[] = [
      {
        role: "simulated_assistant",
        content: "Simulated response from another conversation",
        timestamp: new Date().toISOString(),
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={simMessages} />
      </TestWrapper>
    );

    expect(
      screen.getByText("Simulated response from another conversation")
    ).toBeInTheDocument();
    // Avatar should be labelled "Simulated" instead of "Assistant"
    expect(screen.getByText("S")).toBeInTheDocument();
  });

  it("should show 'Copy to input' and 'Download' buttons on assistant media attachments", () => {
    const messagesWithMedia: Message[] = [
      {
        role: "assistant",
        content: "Here is the image",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "output.png",
            url: "data:image/png;base64,iVBORw0KGgo=",
            mimeType: "image/png",
            size: 1024,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={messagesWithMedia} onCopyToInput={jest.fn()} />
      </TestWrapper>
    );

    expect(screen.getByTestId("copy-to-input-btn-0")).toBeInTheDocument();
    expect(screen.getByTestId("download-btn-0-0")).toBeInTheDocument();
  });

  it("should not show action buttons on user messages", () => {
    const userMediaMessages: Message[] = [
      {
        role: "user",
        content: "",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "upload.png",
            url: "data:image/png;base64,abc=",
            mimeType: "image/png",
            size: 512,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={userMediaMessages} onCopyToInput={jest.fn()} />
      </TestWrapper>
    );

    expect(screen.queryByTestId("copy-to-input-btn-0")).not.toBeInTheDocument();
    expect(screen.queryByTestId("download-btn-0-0")).not.toBeInTheDocument();
  });

  it("should call onCopyToInput when 'Copy to input' button is clicked", async () => {
    const user = userEvent.setup();
    const onCopyToInput = jest.fn();

    const messagesWithMedia: Message[] = [
      {
        role: "assistant",
        content: "Here is the result",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "result.png",
            url: "data:image/png;base64,abc=",
            mimeType: "image/png",
            size: 256,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList
          messages={messagesWithMedia}
          onCopyToInput={onCopyToInput}
        />
      </TestWrapper>
    );

    await user.click(screen.getByTestId("copy-to-input-btn-0"));

    expect(onCopyToInput).toHaveBeenCalledWith(0);
  });

  it("should not show reply/download buttons on file-type attachments", () => {
    const fileMessages: Message[] = [
      {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "file",
            name: "report.txt",
            url: "",
            mimeType: "text/plain",
            size: 100,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={fileMessages} onCopyToInput={jest.fn()} />
      </TestWrapper>
    );

    // copy-to-input still shows (it copies text, not just media), but no download
    expect(screen.queryByTestId("download-btn-0-0")).not.toBeInTheDocument();
  });

  it("should show Open link for file attachments with a url", () => {
    const fileMessages: Message[] = [
      {
        role: "user",
        content: "make a pdf please",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "file",
            name: "result.pdf",
            url: "/api/media?path=%2Ftmp%2Fresult.pdf",
            mimeType: "application/pdf",
            size: 0,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={fileMessages} />
      </TestWrapper>
    );

    const openLink = screen.getByTestId("attachment-open-0-0");
    expect(openLink).toHaveAttribute("href", "/api/media?path=%2Ftmp%2Fresult.pdf");
    expect(openLink).toHaveAttribute("target", "_blank");
    expect(openLink).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  // -----------------------------------------------------------------------
  // "Use in new conversation" button
  // -----------------------------------------------------------------------

  it("should show 'Copy to new conversation' button when callback is provided", () => {
    const imageMessages: Message[] = [
      {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "output.png",
            url: "data:image/png;base64,abc",
            mimeType: "image/png",
            size: 100,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList
          messages={imageMessages}
          onCopyToNewConversation={jest.fn()}
        />
      </TestWrapper>
    );

    expect(
      screen.getByTestId("copy-to-new-conv-btn-0")
    ).toBeInTheDocument();
  });

  it("should not show 'Copy to new conversation' button when callback is not provided", () => {
    const imageMessages: Message[] = [
      {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "output.png",
            url: "data:image/png;base64,abc",
            mimeType: "image/png",
            size: 100,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList messages={imageMessages} />
      </TestWrapper>
    );

    expect(
      screen.queryByTestId("copy-to-new-conv-btn-0")
    ).not.toBeInTheDocument();
  });

  it("should call onCopyToNewConversation when button is clicked", async () => {
    const user = userEvent.setup();
    const onCopyToNewConversation = jest.fn();

    const imageMessages: Message[] = [
      {
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        attachments: [
          {
            type: "image",
            name: "output.png",
            url: "data:image/png;base64,abc",
            mimeType: "image/png",
            size: 100,
          },
        ],
      },
    ];

    render(
      <TestWrapper>
        <MessageList
          messages={imageMessages}
          onCopyToNewConversation={onCopyToNewConversation}
        />
      </TestWrapper>
    );

    await user.click(screen.getByTestId("copy-to-new-conv-btn-0"));

    expect(onCopyToNewConversation).toHaveBeenCalledWith(0);
  });

  describe("reasoning summary rendering", () => {
    it("should render reasoning summary in a sub-box", () => {
      const messagesWithReasoning: Message[] = [
        {
          role: "assistant",
          content: "The capital of France is Paris.",
          timestamp: new Date().toISOString(),
          reasoningSummaries: ["The user asked about geography."],
        },
      ];

      render(
        <TestWrapper>
          <MessageList messages={messagesWithReasoning} />
        </TestWrapper>
      );

      expect(screen.getByTestId("reasoning-summary")).toBeInTheDocument();
      expect(screen.getByText("Reasoning")).toBeInTheDocument();
      expect(
        screen.getByText("The user asked about geography.")
      ).toBeInTheDocument();
      expect(
        screen.getByText("The capital of France is Paris.")
      ).toBeInTheDocument();
    });

    it("should render multiple reasoning summaries", () => {
      const messagesWithReasoning: Message[] = [
        {
          role: "assistant",
          content: "Answer text.",
          timestamp: new Date().toISOString(),
          reasoningSummaries: ["First thought.", "Second thought."],
        },
      ];

      render(
        <TestWrapper>
          <MessageList messages={messagesWithReasoning} />
        </TestWrapper>
      );

      expect(screen.getByText("First thought.")).toBeInTheDocument();
      expect(screen.getByText("Second thought.")).toBeInTheDocument();
    });

    it("should not render reasoning sub-box when no reasoning summaries", () => {
      render(
        <TestWrapper>
          <MessageList messages={mockMessages} />
        </TestWrapper>
      );

      expect(screen.queryByTestId("reasoning-summary")).not.toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Branch button
  // -----------------------------------------------------------------------

  describe("branch button", () => {
    it("should show branch-attack button on assistant messages when onBranchAttack is provided", () => {
      const onBranchAttack = jest.fn();
      render(
        <TestWrapper>
          <MessageList messages={mockMessages} onBranchAttack={onBranchAttack} />
        </TestWrapper>
      );

      // Branch button should appear on assistant message (index 1) but not user messages
      expect(screen.getByTestId("branch-attack-btn-1")).toBeInTheDocument();
      expect(screen.queryByTestId("branch-attack-btn-0")).not.toBeInTheDocument();
      expect(screen.queryByTestId("branch-attack-btn-2")).not.toBeInTheDocument();
    });

    it("should not show branch-attack button when onBranchAttack is not provided", () => {
      render(
        <TestWrapper>
          <MessageList messages={mockMessages} />
        </TestWrapper>
      );

      expect(screen.queryByTestId("branch-attack-btn-1")).not.toBeInTheDocument();
    });

    it("should call onBranchAttack with correct index when clicked", async () => {
      const user = userEvent.setup();
      const onBranchAttack = jest.fn();
      render(
        <TestWrapper>
          <MessageList messages={mockMessages} onBranchAttack={onBranchAttack} />
        </TestWrapper>
      );

      await user.click(screen.getByTestId("branch-attack-btn-1"));
      expect(onBranchAttack).toHaveBeenCalledWith(1);
    });

    it("should not show branch-attack button on loading messages", () => {
      const loadingMessages: Message[] = [
        {
          role: "user",
          content: "Hello",
          timestamp: new Date().toISOString(),
        },
        {
          role: "assistant",
          content: "Thinking...",
          timestamp: new Date().toISOString(),
          isLoading: true,
        },
      ];
      const onBranchAttack = jest.fn();
      render(
        <TestWrapper>
          <MessageList messages={loadingMessages} onBranchAttack={onBranchAttack} />
        </TestWrapper>
      );

      expect(screen.queryByTestId("branch-attack-btn-1")).not.toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Branch into new conversation button
  // -----------------------------------------------------------------------

  describe("branch-conversation button", () => {
    it("should show branch-conv button on assistant messages when onBranchConversation is provided", () => {
      const onBranchConversation = jest.fn();
      render(
        <TestWrapper>
          <MessageList
            messages={mockMessages}
            onBranchConversation={onBranchConversation}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("branch-conv-btn-1")).toBeInTheDocument();
      expect(screen.queryByTestId("branch-conv-btn-0")).not.toBeInTheDocument();
    });

    it("should call onBranchConversation with correct index when clicked", async () => {
      const user = userEvent.setup();
      const onBranchConversation = jest.fn();
      render(
        <TestWrapper>
          <MessageList
            messages={mockMessages}
            onBranchConversation={onBranchConversation}
          />
        </TestWrapper>
      );

      await user.click(screen.getByTestId("branch-conv-btn-1"));
      expect(onBranchConversation).toHaveBeenCalledWith(1);
    });

    it("should disable branch-conv button when isOperatorLocked", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={mockMessages}
            onBranchConversation={jest.fn()}
            isOperatorLocked={true}
          />
        </TestWrapper>
      );

      const btn = screen.getByTestId("branch-conv-btn-1");
      expect(btn).toBeDisabled();
    });
  });

  // -----------------------------------------------------------------------
  // Disabled-state interactions
  // -----------------------------------------------------------------------

  describe("disabled states", () => {
    const assistantMessage: Message[] = [
      {
        role: "assistant",
        content: "Hello from assistant",
        timestamp: new Date().toISOString(),
      },
    ];

    it("should disable copy-to-input when isSingleTurn is true", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onCopyToInput={jest.fn()}
            isSingleTurn={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("copy-to-input-btn-0")).toBeDisabled();
    });

    it("should disable copy-to-input when isOperatorLocked is true", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onCopyToInput={jest.fn()}
            isOperatorLocked={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("copy-to-input-btn-0")).toBeDisabled();
    });

    it("should disable copy-to-input when isCrossTarget is true", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onCopyToInput={jest.fn()}
            isCrossTarget={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("copy-to-input-btn-0")).toBeDisabled();
    });

    it("should disable copy-to-new-conv when isOperatorLocked is true", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onCopyToNewConversation={jest.fn()}
            isOperatorLocked={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("copy-to-new-conv-btn-0")).toBeDisabled();
    });

    it("should disable copy-to-new-conv when isCrossTarget is true", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onCopyToNewConversation={jest.fn()}
            isCrossTarget={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("copy-to-new-conv-btn-0")).toBeDisabled();
    });

    it("should disable branch-attack button when isSingleTurn is true", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onBranchAttack={jest.fn()}
            isSingleTurn={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("branch-attack-btn-0")).toBeDisabled();
    });

    it("should disable branch-conv button when isSingleTurn is true", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onBranchConversation={jest.fn()}
            isSingleTurn={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("branch-conv-btn-0")).toBeDisabled();
    });

    it("should not disable branch-attack button when isOperatorLocked or isCrossTarget", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onBranchAttack={jest.fn()}
            isOperatorLocked={true}
            isCrossTarget={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("branch-attack-btn-0")).not.toBeDisabled();
    });

    it("should show copy-to-input on text-only assistant messages (no media required)", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onCopyToInput={jest.fn()}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("copy-to-input-btn-0")).toBeInTheDocument();
    });

    it("should disable all action buttons when noTargetSelected is true", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onCopyToInput={jest.fn()}
            onCopyToNewConversation={jest.fn()}
            onBranchConversation={jest.fn()}
            noTargetSelected={true}
          />
        </TestWrapper>
      );

      expect(screen.getByTestId("copy-to-input-btn-0")).toBeDisabled();
      expect(screen.getByTestId("copy-to-new-conv-btn-0")).toBeDisabled();
      expect(screen.getByTestId("branch-conv-btn-0")).toBeDisabled();
    });

    it("should show disabled branch-attack button when noTargetSelected and no onBranchAttack", () => {
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            noTargetSelected={true}
          />
        </TestWrapper>
      );

      const btn = screen.getByTestId("branch-attack-btn-0");
      expect(btn).toBeInTheDocument();
      expect(btn).toBeDisabled();
    });

    it("should give the four disabled action buttons distinct accessible names", () => {
      // Regression guard: previously several disabled-state tooltips collapsed
      // to identical strings (e.g. both branch buttons read
      // "Cannot branch — target is single-turn"), so a screen reader could
      // not tell them apart. Each disabled action's accessible name must be
      // unique.
      render(
        <TestWrapper>
          <MessageList
            messages={assistantMessage}
            onCopyToInput={jest.fn()}
            onCopyToNewConversation={jest.fn()}
            onBranchConversation={jest.fn()}
            onBranchAttack={jest.fn()}
            isSingleTurn={true}
          />
        </TestWrapper>
      );

      const btns = [
        screen.getByTestId("copy-to-input-btn-0"),
        screen.getByTestId("copy-to-new-conv-btn-0"),
        screen.getByTestId("branch-conv-btn-0"),
        screen.getByTestId("branch-attack-btn-0"),
      ];
      const names = btns.map(b => b.getAttribute("aria-label") ?? "");
      // None empty
      for (const name of names) {
        expect(name).not.toBe("");
      }
      // All distinct
      expect(new Set(names).size).toBe(names.length);
    });
  });

  describe("original vs converted display", () => {
    it("should show original section and converted label when originalContent differs", () => {
      const messages: Message[] = [
        {
          role: "user",
          content: "VGVsbCBtZSBhIGpva2U=",
          originalContent: "Tell me a joke",
          timestamp: new Date().toISOString(),
        },
      ];
      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );

      expect(screen.getByTestId("original-section")).toBeInTheDocument();
      expect(screen.getByText("Tell me a joke")).toBeInTheDocument();
      expect(screen.getByTestId("converted-label")).toBeInTheDocument();
      expect(screen.getByText("VGVsbCBtZSBhIGpva2U=")).toBeInTheDocument();
    });

    it("should not show original section when originalContent is not set", () => {
      const messages: Message[] = [
        {
          role: "user",
          content: "Hello",
          timestamp: new Date().toISOString(),
        },
      ];
      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );

      expect(screen.queryByTestId("original-section")).not.toBeInTheDocument();
      expect(screen.queryByTestId("converted-label")).not.toBeInTheDocument();
      expect(screen.getByText("Hello")).toBeInTheDocument();
    });
  });

  describe("MediaWithFallback", () => {
    it("should show video error state on load failure", () => {
      const messages: Message[] = [
        {
          role: "assistant",
          content: "",
          timestamp: new Date().toISOString(),
          attachments: [
            {
              type: "video",
              name: "broken.mp4",
              url: "http://example.com/broken.mp4",
              mimeType: "video/mp4",
              size: 1024,
            },
          ],
        },
      ];

      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );

      const video = screen.getByTestId("video-player");
      fireEvent.error(video);

      expect(screen.getByTestId("video-error")).toBeInTheDocument();
      expect(screen.getByText("Video failed to load")).toBeInTheDocument();
    });

    it("should show audio error state on load failure", () => {
      const messages: Message[] = [
        {
          role: "assistant",
          content: "",
          timestamp: new Date().toISOString(),
          attachments: [
            {
              type: "audio",
              name: "broken.wav",
              url: "http://example.com/broken.wav",
              mimeType: "audio/wav",
              size: 512,
            },
          ],
        },
      ];

      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );

      const audio = screen.getByTestId("audio-player");
      fireEvent.error(audio);

      expect(screen.getByTestId("audio-error")).toBeInTheDocument();
      expect(screen.getByText("Audio failed to load")).toBeInTheDocument();
    });
  });

  describe("original attachments with media", () => {
    it("should render original video and audio attachments", () => {
      const messages: Message[] = [
        {
          role: "user",
          content: "converted text",
          originalContent: "original text",
          originalAttachments: [
            {
              type: "video",
              name: "orig.mp4",
              url: "http://example.com/orig.mp4",
              mimeType: "video/mp4",
              size: 1024,
            },
            {
              type: "audio",
              name: "orig.wav",
              url: "http://example.com/orig.wav",
              mimeType: "audio/wav",
              size: 512,
            },
          ],
          timestamp: new Date().toISOString(),
        },
      ];

      render(
        <TestWrapper>
          <MessageList messages={messages} />
        </TestWrapper>
      );

      expect(screen.getByTestId("original-section")).toBeInTheDocument();
      expect(screen.getByTestId("video-player")).toBeInTheDocument();
      expect(screen.getByTestId("audio-player")).toBeInTheDocument();
    });
  });

  describe("download handler", () => {
    it("should trigger download on click", async () => {
      const user = userEvent.setup();

      // Mock fetch + blob
      const mockBlob = new Blob(["test"], { type: "image/png" });
      const mockObjectUrl = "blob:http://localhost/mock-uuid";
      global.fetch = jest.fn().mockResolvedValue({ blob: () => Promise.resolve(mockBlob) });
      global.URL.createObjectURL = jest.fn().mockReturnValue(mockObjectUrl);
      global.URL.revokeObjectURL = jest.fn();

      const clickSpy = jest.fn();
      const origCreateElement = document.createElement.bind(document);
      jest.spyOn(document, "createElement").mockImplementation((tag: string) => {
        const el = origCreateElement(tag);
        if (tag === "a") {
          jest.spyOn(el, "click").mockImplementation(clickSpy);
        }
        return el;
      });

      const messages: Message[] = [
        {
          role: "assistant",
          content: "Here is the image",
          timestamp: new Date().toISOString(),
          attachments: [
            {
              type: "image",
              name: "download.png",
              url: "data:image/png;base64,abc=",
              mimeType: "image/png",
              size: 1024,
            },
          ],
        },
      ];

      render(
        <TestWrapper>
          <MessageList messages={messages} onCopyToInput={jest.fn()} />
        </TestWrapper>
      );

      await user.click(screen.getByTestId("download-btn-0-0"));

      expect(global.fetch).toHaveBeenCalledWith("data:image/png;base64,abc=");
      expect(clickSpy).toHaveBeenCalled();
      expect(global.URL.revokeObjectURL).toHaveBeenCalledWith(mockObjectUrl);

      jest.restoreAllMocks();
    });
  });

  describe("markdown rendering", () => {
    const markdownMessages: Message[] = [
      {
        role: "user",
        content: "Say **alpha**",
        timestamp: new Date().toISOString(),
      },
      {
        role: "assistant",
        content: "Reply **bravo**",
        timestamp: new Date().toISOString(),
      },
    ];

    it("renders text literally when markdown is off (default)", () => {
      render(
        <TestWrapper>
          <MessageList messages={markdownMessages} />
        </TestWrapper>
      );

      expect(screen.getByText("Say **alpha**")).toBeInTheDocument();
      expect(document.querySelector("strong")).toBeNull();
    });

    it("renders every message as markdown when globalMarkdown is true", () => {
      render(
        <TestWrapper>
          <MessageList messages={markdownMessages} globalMarkdown />
        </TestWrapper>
      );

      expect(screen.getByText("alpha").tagName).toBe("STRONG");
      expect(screen.getByText("bravo").tagName).toBe("STRONG");
      expect(screen.queryByText("Say **alpha**")).not.toBeInTheDocument();
    });
  });
});

import {
  exportConversation,
  conversationToMarkdown,
  conversationToJson,
  conversationToHtml,
  buildExportFilename,
  downloadTextFile,
  EXPORT_MIME_TYPES,
  MAX_TOTAL_INLINE_CHARACTERS,
} from "./conversationExport";
import type { Message, MessageAttachment, BackendMessage } from "../types";
import { backendMessageToFrontend } from "./messageMapper";

const FIXED_NOW = new Date("2026-07-22T02:34:01.059Z");

function textPiece(id: string, value: string) {
  return {
    id,
    original_value_data_type: "text",
    converted_value_data_type: "text",
    original_value: value,
    converted_value: value,
    scores: [],
    response_error: "none",
  };
}

function mediaPiece(id: string, value: string, filename: string) {
  return {
    id,
    original_value_data_type: "image_path",
    converted_value_data_type: "image_path",
    original_value: value,
    converted_value: value,
    converted_value_mime_type: "image/png",
    converted_filename: filename,
    scores: [],
    response_error: "none",
  };
}

function message(overrides: Partial<Message> = {}): Message {
  return {
    role: "assistant",
    content: "Hello there",
    timestamp: "2026-07-22T02:30:07.000Z",
    ...overrides,
  };
}

function attachment(overrides: Partial<MessageAttachment> = {}): MessageAttachment {
  return {
    type: "image",
    name: "result.png",
    url: "/api/media?path=/home/op/dbdata/result.png",
    mimeType: "image/png",
    ...overrides,
  };
}

function mockFetchOnce(body: string, { ok = true, type = "image/png" } = {}): jest.Mock {
  const fetchMock = jest.fn().mockResolvedValue({
    ok,
    blob: () => Promise.resolve(new Blob([body], { type })),
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function blobToText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

function installAnchorSpy(): { clickSpy: jest.Mock; getAnchor: () => HTMLAnchorElement } {
  const clickSpy = jest.fn();
  let anchor: HTMLAnchorElement | null = null;
  const origCreateElement = document.createElement.bind(document);
  jest.spyOn(document, "createElement").mockImplementation((tag: string) => {
    const el = origCreateElement(tag);
    if (tag === "a") {
      anchor = el as HTMLAnchorElement;
      jest.spyOn(el, "click").mockImplementation(clickSpy);
    }
    return el;
  });
  return { clickSpy, getAnchor: () => anchor as HTMLAnchorElement };
}

describe("conversationExport", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  describe("conversationToMarkdown", () => {
    it("renders a header with the conversation id, exported time, and message count", () => {
      const md = conversationToMarkdown([message()], "conv-1", FIXED_NOW);
      expect(md).toContain("# CoPyRIT conversation export");
      expect(md).toContain("- Conversation: conv-1");
      expect(md).toContain("- Exported: 2026-07-22T02:34:01.059Z");
      expect(md).toContain("- Messages: 1");
    });

    it("renders one section per message with a role label and timestamp", () => {
      const md = conversationToMarkdown(
        [
          message({ role: "user", content: "hi", timestamp: "2026-07-22T02:30:05.000Z" }),
          message({ role: "assistant", content: "hello", timestamp: "2026-07-22T02:30:07.000Z" }),
        ],
        "conv-1",
        FIXED_NOW
      );
      expect(md).toContain("## User — 2026-07-22T02:30:05.000Z");
      expect(md).toContain("## Assistant — 2026-07-22T02:30:07.000Z");
    });

    it("includes the system message (hidden in the chat view)", () => {
      const md = conversationToMarkdown(
        [message({ role: "system", content: "You are helpful" })],
        "conv-1",
        FIXED_NOW
      );
      expect(md).toContain("## System — ");
      expect(md).toContain("You are helpful");
    });

    it("skips the loading placeholder", () => {
      const md = conversationToMarkdown(
        [message({ content: "real" }), message({ role: "assistant", content: "", isLoading: true })],
        "conv-1",
        FIXED_NOW
      );
      expect(md).toContain("- Messages: 1");
      expect(md).toContain("real");
    });

    it("wraps content in a code fence and grows the fence when content contains backticks", () => {
      const md = conversationToMarkdown([message({ content: "plain" })], "conv-1", FIXED_NOW);
      expect(md).toContain("```\nplain\n```");

      const withFence = conversationToMarkdown(
        [message({ content: "```js\ncode\n```" })],
        "conv-1",
        FIXED_NOW
      );
      // Longest run in content is 3 backticks, so the wrapper must use 4.
      expect(withFence).toContain("````\n```js\ncode\n```\n````");
    });

    it("does not overflow the stack when content has a huge number of separate backtick runs", () => {
      // Adversarial content: many isolated backticks produce one regex match per
      // run. A spread-based Math.max(...runs) would throw RangeError here.
      const content = Array(200000).fill("`").join(" ");
      let md = "";
      expect(() => {
        md = conversationToMarkdown([message({ content })], "conv-1", FIXED_NOW);
      }).not.toThrow();
      // The longest run is a single backtick, so the fence stays at three.
      expect(md).toContain("```\n" + content + "\n```");
    });

    it("includes the Original block only when the original content differs", () => {
      const differs = conversationToMarkdown(
        [message({ content: "converted", originalContent: "original" })],
        "conv-1",
        FIXED_NOW
      );
      expect(differs).toContain("**Original (before conversion):**");
      expect(differs).toContain("original");

      const same = conversationToMarkdown(
        [message({ content: "converted", originalContent: "converted" })],
        "conv-1",
        FIXED_NOW
      );
      expect(same).not.toContain("**Original (before conversion):**");
    });

    it("includes a Reasoning block when reasoning summaries are present", () => {
      const md = conversationToMarkdown(
        [message({ reasoningSummaries: ["thought one", "thought two"] })],
        "conv-1",
        FIXED_NOW
      );
      expect(md).toContain("**Reasoning:**");
      expect(md).toContain("thought one");
      expect(md).toContain("thought two");
    });

    it("includes an error line with the type, and the description when present", () => {
      const withDescription = conversationToMarkdown(
        [message({ error: { type: "blocked", description: "content was filtered" } })],
        "conv-1",
        FIXED_NOW
      );
      expect(withDescription).toContain("**Error (blocked)**: content was filtered");

      const typeOnly = conversationToMarkdown(
        [message({ error: { type: "processing" } })],
        "conv-1",
        FIXED_NOW
      );
      expect(typeOnly).toContain("**Error (processing)**");
      expect(typeOnly).not.toContain("**Error (processing)**:");
    });

    it("lists attachments by type, name, and mime type without inlining data", () => {
      const md = conversationToMarkdown(
        [
          message({
            attachments: [
              { type: "image", name: "result.png", url: "data:image/png;base64,AAAA", mimeType: "image/png" },
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW
      );
      expect(md).toContain("**Attachments:**");
      expect(md).toContain("- image: result.png (image/png)");
      expect(md).not.toContain("base64,AAAA");
    });

    it("lists original attachments shown in the UI before conversion", () => {
      const md = conversationToMarkdown(
        [
          message({
            content: "converted.png",
            originalAttachments: [
              { type: "image", name: "original.png", url: "blob:orig", mimeType: "image/png" },
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW
      );
      expect(md).toContain("**Original attachments (before conversion):**");
      expect(md).toContain("- image: original.png (image/png)");
    });

    it("collapses newlines in attachment names and error text so they cannot inject structure", () => {
      const md = conversationToMarkdown(
        [
          message({
            error: { type: "blocked", description: "filtered\n## Injected heading" },
            attachments: [
              { type: "file", name: "safe.txt\n## Injected heading", url: "u", mimeType: "text/plain" },
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW
      );
      // No line may begin a new markdown heading from untrusted inline text.
      expect(md).not.toContain("\n## Injected heading");
    });

    it("neutralizes newlines in system-provided header fields (conversation id, timestamp)", () => {
      const md = conversationToMarkdown(
        [message({ timestamp: "2026-07-22T02:30:07.000Z\n## Injected heading" })],
        "conv-1\n## Injected heading",
        FIXED_NOW
      );
      expect(md).not.toContain("\n## Injected heading");
    });

    it("handles an empty conversation with a header and zero messages", () => {
      const md = conversationToMarkdown([], "conv-1", FIXED_NOW);
      expect(md).toContain("- Messages: 0");
      expect(md).not.toContain("## ");
    });

    it("labels an unsaved conversation when the id is null", () => {
      const md = conversationToMarkdown([message()], null, FIXED_NOW);
      expect(md).toContain("- Conversation: (unsaved)");
    });

    it("defaults the exported time to now when omitted", () => {
      const md = conversationToMarkdown([message()], "conv-1");
      expect(md).toContain("# CoPyRIT conversation export");
      expect(md).toContain("- Exported: ");
    });
  });

  describe("conversationToJson", () => {
    it("returns pretty-printed JSON with a conversation_id and messages envelope", () => {
      const json = conversationToJson([message({ content: "hi" })], "conv-1");
      const parsed = JSON.parse(json);
      expect(parsed.conversation_id).toBe("conv-1");
      expect(parsed.messages).toHaveLength(1);
      expect(parsed.messages[0].content).toBe("hi");
      expect(json).toContain("\n  "); // two-space indentation
    });

    it("records the export timestamp in the envelope", () => {
      const json = conversationToJson([message({ content: "hi" })], "conv-1", FIXED_NOW);
      expect(JSON.parse(json).exported_at).toBe(FIXED_NOW.toISOString());
    });

    it("preserves score-only media metadata without exporting an attachment", () => {
      const json = conversationToJson(
        [
          message({
            content: "",
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
                    score_value: "True",
                    pieceIndex: 0,
                    pieceType: "image_path",
                    sourceLabel: "Piece 1 · image_path",
                    timestamp: "2026-02-15T00:00:00Z",
                  },
                ],
              },
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW
      );

      const exportedMessage = JSON.parse(json).messages[0];
      expect(exportedMessage).not.toHaveProperty("attachments");
      expect(exportedMessage.displayPieces[0]).not.toHaveProperty("attachment");
      expect(exportedMessage.displayPieces[0].scores[0].id).toBe("score-blocked");
    });

    it("defaults the export timestamp to a valid ISO string when omitted", () => {
      const exportedAt = JSON.parse(conversationToJson([message()], "conv-1")).exported_at;
      expect(Number.isNaN(Date.parse(exportedAt))).toBe(false);
    });

    it("drops the loading placeholder", () => {
      const json = conversationToJson(
        [message({ content: "real" }), message({ role: "assistant", content: "", isLoading: true })],
        "conv-1"
      );
      expect(JSON.parse(json).messages).toHaveLength(1);
    });

    it("omits the in-memory File handle but keeps the other attachment fields", () => {
      const file = new File(["x"], "local.png", { type: "image/png" });
      const json = conversationToJson(
        [
          message({
            attachments: [
              {
                type: "image",
                name: "local.png",
                url: "blob:local",
                mimeType: "image/png",
                pieceId: "piece-9",
                file,
              },
            ],
          }),
        ],
        "conv-1"
      );
      const attachment = JSON.parse(json).messages[0].attachments[0];
      expect(attachment.file).toBeUndefined();
      expect(attachment.name).toBe("local.png");
      expect(attachment.pieceId).toBe("piece-9");
    });

    it("drops the signed storage url so a shared file carries no credentials", () => {
      const json = conversationToJson(
        [
          message({
            attachments: [
              {
                type: "image",
                name: "result.png",
                url: "https://acct.blob.core.windows.net/c/result.png?sv=2024&sig=SECRETSIG",
                mimeType: "image/png",
              },
            ],
          }),
        ],
        "conv-1"
      );
      expect(json).not.toContain("SECRETSIG");
      expect(json).not.toContain("blob.core.windows.net");
      expect(JSON.parse(json).messages[0].attachments[0].url).toBe("");
      expect(JSON.parse(json).messages[0].attachments[0].name).toBe("result.png");
    });

    it("drops the signed storage url from the display piece it was rendered from", () => {
      const signed = {
        type: "image" as const,
        name: "result.png",
        url: "https://acct.blob.core.windows.net/c/result.png?sv=2024&sig=SECRETSIG",
        mimeType: "image/png",
      };
      const json = conversationToJson(
        [
          message({
            attachments: [signed],
            displayPieces: [
              { type: "media", pieceId: "piece-1", pieceIndex: 0, attachment: signed },
              { type: "text", pieceId: "piece-2", pieceIndex: 1, content: "some text" },
            ],
          }),
        ],
        "conv-1"
      );
      expect(json).not.toContain("SECRETSIG");
      expect(json).not.toContain("blob.core.windows.net");
      const parsed = JSON.parse(json).messages[0];
      expect(parsed.displayPieces[0].attachment.url).toBe("");
      expect(parsed.displayPieces[0].attachment.name).toBe("result.png");
      expect(parsed.displayPieces[1].content).toBe("some text");
    });

    it("drops the signed storage url from a display piece even with no flat attachment list", () => {
      const json = conversationToJson(
        [
          message({
            displayPieces: [
              {
                type: "media",
                pieceId: "piece-1",
                pieceIndex: 0,
                attachment: {
                  type: "image",
                  name: "result.png",
                  url: "https://acct.blob.core.windows.net/c/result.png?sv=2024&sig=SECRETSIG",
                  mimeType: "image/png",
                },
              },
            ],
          }),
        ],
        "conv-1"
      );
      expect(json).not.toContain("SECRETSIG");
      expect(json).not.toContain("blob.core.windows.net");
      expect(JSON.parse(json).messages[0].displayPieces[0].attachment.url).toBe("");
    });

    it("drops the local media path so a shared file does not expose the operator's disk", () => {
      const json = conversationToJson(
        [
          message({
            attachments: [
              {
                type: "image",
                name: "result.png",
                url: "/api/media?path=/home/op/dbdata/prompt-memory-entries/images/1.png",
                mimeType: "image/png",
              },
            ],
          }),
        ],
        "conv-1"
      );
      expect(json).not.toContain("/home/op/dbdata");
      expect(json).not.toContain("/api/media");
    });

    it("leaves the live conversation untouched while stripping the exported copy", () => {
      const attachments: MessageAttachment[] = [
        {
          type: "image",
          name: "result.png",
          url: "/api/media?path=/home/op/dbdata/result.png",
          mimeType: "image/png",
        },
      ];
      const messages = [message({ attachments })];
      conversationToJson(messages, "conv-1");
      // The chat still needs the url to render the image on screen.
      expect(attachments[0].url).toBe("/api/media?path=/home/op/dbdata/result.png");
      expect(messages[0].attachments).toBe(attachments);
    });

    it("keeps an inline data uri, which is the payload rather than a pointer to it", () => {
      const json = conversationToJson(
        [
          message({
            attachments: [
              {
                type: "image",
                name: "result.png",
                url: "data:image/png;base64,AAAA",
                mimeType: "image/png",
              },
            ],
          }),
        ],
        "conv-1"
      );
      expect(JSON.parse(json).messages[0].attachments[0].url).toBe("data:image/png;base64,AAAA");
    });

    it("keeps a serializable metadata field named 'file' (only the attachment File handle is stripped)", () => {
      const file = new File(["x"], "local.png", { type: "image/png" });
      const json = conversationToJson(
        [
          message({
            attachments: [
              {
                type: "image",
                name: "local.png",
                url: "blob:local",
                mimeType: "image/png",
                metadata: { file: "source/path.txt", video_id: "v1" },
                file,
              },
            ],
          }),
        ],
        "conv-1"
      );
      const attachment = JSON.parse(json).messages[0].attachments[0];
      expect(attachment.file).toBeUndefined();
      expect(attachment.metadata.file).toBe("source/path.txt");
      expect(attachment.metadata.video_id).toBe("v1");
    });

    it("strips the File handle from original attachments too", () => {
      const file = new File(["x"], "orig.png", { type: "image/png" });
      const json = conversationToJson(
        [
          message({
            originalAttachments: [
              { type: "image", name: "orig.png", url: "blob:orig", mimeType: "image/png", file },
            ],
          }),
        ],
        "conv-1"
      );
      const attachment = JSON.parse(json).messages[0].originalAttachments[0];
      expect(attachment.file).toBeUndefined();
      expect(attachment.name).toBe("orig.png");
    });

    it("passes through a null conversation id", () => {
      const json = conversationToJson([message()], null);
      expect(JSON.parse(json).conversation_id).toBeNull();
    });

    it("does not mutate the input messages", () => {
      const input = [message({ content: "hi", isLoading: false })];
      const snapshot = JSON.stringify(input);
      conversationToJson(input, "conv-1");
      expect(JSON.stringify(input)).toBe(snapshot);
    });
  });

  describe("buildExportFilename", () => {
    it("builds a markdown filename with a sanitized id and timestamp", () => {
      expect(buildExportFilename("3fa85f64-b3fc", "markdown", FIXED_NOW)).toBe(
        "copyrit-conversation-3fa85f64-b3fc-2026-07-22T02-34-01-059.md"
      );
    });

    it("builds a json filename", () => {
      expect(buildExportFilename("conv-1", "json", FIXED_NOW)).toBe(
        "copyrit-conversation-conv-1-2026-07-22T02-34-01-059.json"
      );
    });

    it("sanitizes unsafe characters in the conversation id", () => {
      expect(buildExportFilename("a/b c:d", "markdown", FIXED_NOW)).toBe(
        "copyrit-conversation-a_b_c_d-2026-07-22T02-34-01-059.md"
      );
    });

    it("omits the id segment when the conversation id is null", () => {
      expect(buildExportFilename(null, "json", FIXED_NOW)).toBe(
        "copyrit-conversation-2026-07-22T02-34-01-059.json"
      );
    });

    it("includes millisecond precision so exports in the same second do not collide", () => {
      const a = buildExportFilename("conv-1", "json", new Date("2026-07-22T02:34:01.001Z"));
      const b = buildExportFilename("conv-1", "json", new Date("2026-07-22T02:34:01.002Z"));
      expect(a).not.toBe(b);
    });

    it("produces a filesystem-safe name with no colons", () => {
      expect(buildExportFilename("conv-1", "markdown", FIXED_NOW)).not.toContain(":");
    });

    it("defaults to the current time when now is omitted", () => {
      expect(buildExportFilename("conv-1", "markdown")).toMatch(/^copyrit-conversation-conv-1-.*\.md$/);
    });
  });

  describe("downloadTextFile", () => {
    it("creates a blob download, sets the filename, clicks the anchor, and revokes the url", () => {
      const { clickSpy, getAnchor } = installAnchorSpy();
      downloadTextFile("body", "file.md", "text/markdown;charset=utf-8");

      const createObjectUrl = URL.createObjectURL as jest.Mock;
      const blob = createObjectUrl.mock.calls[0][0] as Blob;
      expect(blob.type).toBe("text/markdown;charset=utf-8");
      expect(getAnchor().download).toBe("file.md");
      expect(clickSpy).toHaveBeenCalledTimes(1);
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    });

    it("removes the anchor and revokes the object url even when the click throws", () => {
      let anchor: HTMLAnchorElement | null = null;
      const origCreateElement = document.createElement.bind(document);
      jest.spyOn(document, "createElement").mockImplementation((tag: string) => {
        const el = origCreateElement(tag);
        if (tag === "a") {
          anchor = el as HTMLAnchorElement;
          jest.spyOn(el, "click").mockImplementation(() => {
            throw new Error("click failed");
          });
        }
        return el;
      });

      expect(() => downloadTextFile("body", "file.md", "text/markdown")).toThrow("click failed");
      expect(anchor).not.toBeNull();
      expect(document.body.contains(anchor)).toBe(false);
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
    });
  });

  describe("conversationToHtml", () => {
    it("renders a header with the conversation id, exported time, and message count", async () => {
      const html = await conversationToHtml([message()], "conv-1", FIXED_NOW);
      expect(html).toContain("<h1>CoPyRIT conversation export</h1>");
      expect(html).toContain("Conversation: conv-1");
      expect(html).toContain("Exported: 2026-07-22T02:34:01.059Z");
      expect(html).toContain("Messages: 1");
    });

    it("includes print styles so the file can be saved as PDF as-is", async () => {
      const html = await conversationToHtml([message()], "conv-1", FIXED_NOW);
      expect(html).toContain("@media print");
      expect(html).toContain("page-break-inside: avoid");
    });

    it("includes the system message that the chat view hides", async () => {
      const html = await conversationToHtml(
        [message({ role: "system", content: "You are a helpful assistant." })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("System");
      expect(html).toContain("You are a helpful assistant.");
    });

    it("drops loading placeholders", async () => {
      const html = await conversationToHtml(
        [message({ content: "kept" }), message({ content: "pending", isLoading: true })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("kept");
      expect(html).not.toContain("pending");
      expect(html).toContain("Messages: 1");
    });

    it("escapes markup so model output cannot execute when the file is opened", async () => {
      const html = await conversationToHtml(
        [message({ content: "<script>alert(1)</script> & \"quoted\"" })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
      expect(html).not.toContain("<script>alert(1)</script>");
    });

    it("escapes a hostile url so it cannot break out of the src attribute", async () => {
      const html = await conversationToHtml(
        [
          message({
            content: "",
            attachments: [attachment({ url: 'data:image/png;base64,AAAA" onerror="alert(1)' })],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      const img = new DOMParser().parseFromString(html, "text/html").querySelector("img");
      expect(img).not.toBeNull();
      expect(img?.getAttribute("onerror")).toBeNull();
      expect(img?.getAttributeNames().sort()).toEqual(["alt", "src"]);
    });

    it("escapes a hostile filename so it cannot break out of the alt attribute", async () => {
      const html = await conversationToHtml(
        [
          message({
            content: "",
            attachments: [
              attachment({ url: "data:image/png;base64,AAAA", name: 'x" onerror="alert(1)' }),
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      const img = new DOMParser().parseFromString(html, "text/html").querySelector("img");
      expect(img?.getAttribute("onerror")).toBeNull();
      expect(img?.getAttribute("alt")).toBe('x" onerror="alert(1)');
    });

    it("escapes a hostile mime type instead of letting it break out of the src attribute", async () => {
      const html = await conversationToHtml(
        [
          message({
            content: "",
            attachments: [attachment({ url: 'data:image/png;base64,AAAA', mimeType: 'image/png" onerror="alert(1)' })],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).not.toContain('onerror="alert(1)"');
      expect(html).toContain("&quot;");
    });

    it("escapes a hostile mime type on media it fetched, which is the only path that writes one", async () => {
      // The fetched path builds the data uri itself, so the mime type reaches
      // the src attribute here and nowhere else.
      mockFetchOnce("hi");
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ mimeType: 'image/png" onerror="alert(1)' })] })],
        "conv-1",
        FIXED_NOW,
      );
      const src = html.match(/<img src="([^"]*)"/)?.[1] ?? "";
      expect(src).toContain("&quot;");
      expect(src).not.toContain('" onerror=');
      expect(html).not.toContain('onerror="alert(1)"');
    });

    it("omits the text block for a media-only message rather than rendering an empty one", async () => {
      const html = await conversationToHtml(
        [message({ content: "", attachments: [attachment({ url: "data:image/png;base64,AAAA" })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).not.toContain("<pre></pre>");
      expect(html).toContain("<img src=\"data:image/png;base64,AAAA\"");
    });

    it("reuses a data URI without fetching it", async () => {
      const fetchMock = mockFetchOnce("bytes");
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url: "data:image/png;base64,AAAA" })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).not.toHaveBeenCalled();
      expect(html).toContain("data:image/png;base64,AAAA");
    });

    it("reads a pending upload from its local file without fetching", async () => {
      const fetchMock = mockFetchOnce("bytes");
      const file = new File(["hello"], "pending.png", { type: "image/png" });
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url: "blob:http://localhost/abc", file })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).not.toHaveBeenCalled();
      expect(html).toContain("data:image/png;base64,aGVsbG8=");
    });

    it("embeds same-origin media fetched from the media endpoint", async () => {
      const fetchMock = mockFetchOnce("hello");
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).toHaveBeenCalledWith("/api/media?path=/home/op/dbdata/result.png");
      expect(html).toContain("data:image/png;base64,aGVsbG8=");
    });

    it("names but does not embed blob-hosted media, and never writes its signed url", async () => {
      const fetchMock = mockFetchOnce("hello");
      const url = "https://acct.blob.core.windows.net/c/result.png?sv=2024&sig=SECRETSIG";
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).not.toHaveBeenCalled();
      expect(html).not.toContain("SECRETSIG");
      expect(html).not.toContain("blob.core.windows.net");
      expect(html).toContain("[Image: result.png (image/png) — kept in remote storage]");
      expect(html).not.toContain("could not be read");
    });

    it("keeps the read failure wording for a same-origin url it will not fetch", async () => {
      const fetchMock = mockFetchOnce("hello");
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url: "/not-the-media-endpoint?path=x.png" })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).not.toHaveBeenCalled();
      expect(html).toContain("[Image: result.png (image/png) — could not be read]");
      expect(html).not.toContain("kept in remote storage");
    });

    it("names but does not embed a blob url with no local file", async () => {
      const fetchMock = mockFetchOnce("hello");
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url: "blob:http://localhost/abc" })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).not.toHaveBeenCalled();
      expect(html).toContain("[Image: result.png (image/png) — could not be read]");
      expect(html).not.toContain("kept in remote storage");
    });

    it("falls back to a placeholder when the media endpoint refuses the file", async () => {
      mockFetchOnce(JSON.stringify({ detail: "Access denied" }), { ok: false, type: "application/json" });
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: result.png (image/png) — could not be read]");
      expect(html).not.toContain("Access denied");
      expect(html).not.toContain("/api/media");
    });

    it("falls back to a placeholder when the fetch throws", async () => {
      global.fetch = jest.fn().mockRejectedValue(new Error("network down")) as unknown as typeof fetch;
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: result.png (image/png) — could not be read]");
    });

    it("names but does not embed an attachment over the inline size cap", async () => {
      mockFetchOnce("x".repeat(10 * 1024 * 1024 + 1));
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: result.png (image/png) — too large to embed]");
      expect(html).not.toContain("base64");
    });

    it("names but does not embed an inline data uri over the size cap", async () => {
      const oversized = `data:image/png;base64,${"A".repeat(14 * 1024 * 1024)}`;
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url: oversized })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: result.png (image/png) — too large to embed]");
      expect(html).not.toContain("base64,AAAA");
      expect(html.length).toBeLessThan(10000);
    });

    it("names but does not embed an empty inline data uri", async () => {
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url: "data:image/png;base64," })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: result.png (image/png) — could not be read]");
      expect(html).not.toContain("<img");
    });

    it("does not fetch a same-origin url outside the media endpoint", async () => {
      // Any other same-origin path is answered by the single-page app, whose
      // HTML would otherwise be embedded as if it were the image.
      const fetchMock = mockFetchOnce("<!doctype html><html>app shell</html>", { type: "text/html" });
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url: "/attacks/conv-1" })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).not.toHaveBeenCalled();
      expect(html).toContain("[Image: result.png (image/png) — could not be read]");
      expect(html).not.toContain("app shell");
    });

    it("prefers the attachment mime type over the one the server reports", async () => {
      mockFetchOnce("hello", { type: "text/plain" });
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ mimeType: "image/png" })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("data:image/png;base64,aGVsbG8=");
    });

    it("names file attachments rather than embedding them as links", async () => {
      const fetchMock = mockFetchOnce("hello");
      const html = await conversationToHtml(
        [
          message({
            content: "",
            attachments: [
              attachment({ type: "file", name: "evil.html", mimeType: "text/html", url: "data:text/html;base64,AAAA" }),
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).not.toHaveBeenCalled();
      expect(html).toContain("[File: evil.html (text/html) — not a media file]");
      expect(html).not.toContain("<a ");
      expect(html).not.toContain("data:text/html");
    });

    it("names but does not embed a zero-byte attachment", async () => {
      mockFetchOnce("");
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: result.png (image/png) — could not be read]");
    });

    it("falls back to a generic mime type when the attachment has none", async () => {
      mockFetchOnce("hello", { type: "" });
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ mimeType: "" })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("data:application/octet-stream;base64,aGVsbG8=");
    });

    it("renders players for audio and video and names other files", async () => {
      const html = await conversationToHtml(
        [
          message({
            content: "",
            attachments: [
              attachment({ type: "audio", name: "a.mp3", mimeType: "audio/mpeg", url: "data:audio/mpeg;base64,AAAA" }),
              attachment({ type: "video", name: "v.mp4", mimeType: "video/mp4", url: "data:video/mp4;base64,AAAA" }),
              attachment({ type: "file", name: "f.txt", mimeType: "text/plain", url: "data:text/plain;base64,AAAA" }),
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("<audio controls");
      expect(html).toContain("<video controls");
      expect(html).toContain("[File: f.txt (text/plain) — not a media file]");
    });

    it("renders original content, original attachments, reasoning, and errors like Markdown does", async () => {
      const html = await conversationToHtml(
        [
          message({
            content: "converted",
            originalContent: "original",
            originalAttachments: [attachment({ name: "before.png", url: "data:image/png;base64,AAAA" })],
            reasoningSummaries: ["step one", "step two"],
            error: { type: "rate_limit", description: "slow down" },
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("Original (before conversion):");
      expect(html).toContain("original");
      expect(html).toContain("Original attachments (before conversion):");
      expect(html).toContain("step one");
      expect(html).toContain("Reasoning:");
      expect(html).toContain("Error (rate_limit): slow down");
    });

    it("preserves non-ASCII content", async () => {
      const html = await conversationToHtml(
        [message({ content: "emoji 🎉 عربى 中文" })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain('<meta charset="utf-8" />');
      expect(html).toContain("emoji 🎉 عربى 中文");
    });

    it("keeps media aligned with its message when a loading placeholder is dropped", async () => {
      // Resolution and rendering both run over the settled messages, so an
      // attachment after a dropped placeholder must still land in its own
      // message rather than shifting onto another one.
      const html = await conversationToHtml(
        [
          message({ content: "first" }),
          message({ content: "typing", isLoading: true }),
          message({
            content: "third",
            attachments: [attachment({ url: "data:image/png;base64,AAAA" })],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain('<img src="data:image/png;base64,AAAA"');
      expect(html).toContain("Attachments: 1 of 1 embedded");
      expect(html).not.toContain("could not be read");
    });

    it("renders an empty conversation without messages", async () => {
      const html = await conversationToHtml([], null, FIXED_NOW);
      expect(html).toContain("Conversation: (unsaved)");
      expect(html).toContain("Messages: 0");
      expect(html).not.toContain("<article");
    });

    it("reports that every attachment made it into the file", async () => {
      mockFetchOnce("hello");
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("Attachments: 1 of 1 embedded");
      expect(html).not.toContain("not embedded (");
    });

    it("says how many attachments were left out and why", async () => {
      mockFetchOnce("hello");
      const html = await conversationToHtml(
        [
          message({
            attachments: [
              attachment({ name: "a.png", url: "https://acct.blob.core.windows.net/c/a.png?sig=S" }),
              attachment({ name: "b.png", url: `data:image/png;base64,${"A".repeat(16 * 1024 * 1024)}` }),
              attachment({ type: "file", name: "c.txt", mimeType: "text/plain" }),
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("Attachments: 0 of 3 embedded");
      expect(html).toContain(
        "3 not embedded (1 kept in remote storage; 1 too large to embed; 1 not a media file)",
      );
    });

    it("says there are no attachments when the conversation has none", async () => {
      const html = await conversationToHtml([message()], "conv-1", FIXED_NOW);
      expect(html).toContain("Attachments: none");
    });

    it("measures a percent-encoded data uri instead of assuming it is small", async () => {
      const url = `data:image/svg+xml,${"%20".repeat(11 * 1024 * 1024)}`;
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: result.png (image/png) — too large to embed]");
      expect(html).not.toContain("data:image/svg+xml");
    });

    it("measures an uppercase base64 marker like a lowercase one", async () => {
      // Sized so the two readings disagree: decoded it is 9MB and embeds, but
      // read as raw text it would be 12MB and would be dropped.
      const url = `data:image/png;BASE64,${"A".repeat(12_000_000)}`;
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain('<img src="data:image/png;BASE64,');
      expect(html).not.toContain("too large to embed");
    });

    it("counts the data uri prefix, so a huge mime type cannot slip past the budget", async () => {
      // The mime type is untrusted metadata and is written into the document
      // ahead of the payload, so it has to be paid for like the payload is.
      const hugeMime = `image/${"x".repeat(60 * 1024 * 1024)}`;
      const url = `data:${hugeMime};base64,AAAA`;
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url, mimeType: hugeMime })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("no room left in this file");
      expect(html).not.toContain("base64,AAAA");
    });

    it("counts every place an attachment appears, even the same one twice", async () => {
      // The summary has to match what the reader sees, not how many distinct
      // objects produced it.
      const shared = attachment({ url: "data:image/png;base64,AAAA" });
      const html = await conversationToHtml(
        [message({ attachments: [shared, shared] })],
        "conv-1",
        FIXED_NOW,
      );
      expect((html.match(/<img /g) ?? []).length).toBe(2);
      expect(html).toContain("Attachments: 2 of 2 embedded");
    });

    it("keeps the summary honest when one object is used more times than the budget allows", async () => {
      // Rendering reads each place in the document, not each distinct object,
      // so the count and the page cannot drift apart.
      const shared = attachment({ url: `data:image/png;base64,${"A".repeat(8 * 1024 * 1024)}` });
      const messages = Array.from({ length: 7 }, () => message({ attachments: [shared] }));
      const html = await conversationToHtml(messages, "conv-1", FIXED_NOW);
      const embedded = (html.match(/<img /g) ?? []).length;
      expect(embedded).toBeLessThan(messages.length);
      expect(html).toContain(`Attachments: ${embedded} of ${messages.length} embedded`);
      expect(html.length).toBeLessThanOrEqual(MAX_TOTAL_INLINE_CHARACTERS);
    });

    it("charges escaped media text, so escaping cannot push the file past the budget", async () => {
      // Sized so the two accountings disagree: as raw text all six fit inside
      // the budget, but each ampersand becomes five characters once escaped,
      // so only the first one can actually be afforded.
      const svg = `data:image/svg+xml,${"&".repeat(8_000_000)}`;
      const messages = Array.from({ length: 6 }, (_, index) =>
        message({ attachments: [attachment({ name: `s${index}.svg`, mimeType: "image/svg+xml", url: `${svg}${index}` })] }),
      );
      expect(messages.length * svg.length).toBeLessThan(MAX_TOTAL_INLINE_CHARACTERS);

      const html = await conversationToHtml(messages, "conv-1", FIXED_NOW);
      expect((html.match(/<img /g) ?? []).length).toBeLessThan(messages.length);
      expect(html).toContain("no room left in this file");
      expect(html.length).toBeLessThanOrEqual(MAX_TOTAL_INLINE_CHARACTERS);
    });

    it("counts a repeated attachment against the budget every time it appears", async () => {
      // Reading it once saves the fetch, not the space: each copy is written
      // into the document, so each copy has to be paid for.
      const payload = `data:image/png;base64,${"A".repeat(8 * 1024 * 1024)}`;
      const messages = Array.from({ length: 10 }, () =>
        message({ attachments: [attachment({ name: "same.png", url: payload })] }),
      );
      const html = await conversationToHtml(messages, "conv-1", FIXED_NOW);
      const embedded = (html.match(/<img /g) ?? []).length;
      expect(embedded).toBeLessThan(messages.length);
      expect(html.length).toBeLessThanOrEqual(MAX_TOTAL_INLINE_CHARACTERS);
      expect(html).toContain("no room left in this file");
    });

    it("spends the budget on the converted attachment before the original it came from", async () => {
      // Three fit, so the pair in the last message decides the point: the
      // converted result the operator is sharing is charged before the original
      // it came from, and the original is the one left without room.
      const payload = `data:image/png;base64,${"A".repeat(Math.floor((10 * 1024 * 1024 * 4) / 3) - 64)}`;
      const html = await conversationToHtml(
        [
          message({ attachments: [attachment({ name: "earlier.png", url: `${payload}1` })] }),
          message({ attachments: [attachment({ name: "also-earlier.png", url: `${payload}2` })] }),
          message({
            content: "converted",
            originalContent: "original",
            originalAttachments: [attachment({ name: "before.png", url: `${payload}3` })],
            attachments: [attachment({ name: "after.png", url: `${payload}4` })],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: before.png (image/png) — no room left in this file]");
      expect(html).not.toContain("[Image: after.png");
    });

    it("keeps filling after an attachment does not fit, so a smaller one later still lands", async () => {
      // The walk does not stop at the first refusal. The docs and the constant
      // both say so, and a reader sees the omission above embedded media.
      const big = `data:image/png;base64,${"A".repeat(Math.floor((10 * 1024 * 1024 * 4) / 3) - 64)}`;
      const html = await conversationToHtml(
        [
          message({
            attachments: [
              attachment({ name: "one.png", url: `${big}1` }),
              attachment({ name: "two.png", url: `${big}2` }),
              attachment({ name: "three.png", url: `${big}3` }),
              attachment({ name: "does-not-fit.png", url: `${big}4` }),
              attachment({ name: "still-fits.png", url: "data:image/png;base64,AAAA" }),
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("[Image: does-not-fit.png (image/png) — no room left in this file]");
      expect(html.indexOf("does-not-fit.png")).toBeLessThan(html.lastIndexOf("<img "));
      expect(html).toContain("Attachments: 4 of 5 embedded");
    });

    it("stops embedding once the whole document has used its budget", async () => {
      // Each attachment clears the per-attachment cap; together they do not.
      const payload = `data:image/png;base64,${"A".repeat(8 * 1024 * 1024)}`;
      const attachments = Array.from({ length: 12 }, (_, index) =>
        attachment({ name: `img-${index}.png`, url: payload.replace("AAAA", `AAA${index}`) }),
      );
      const html = await conversationToHtml(
        [message({ attachments })],
        "conv-1",
        FIXED_NOW,
      );
      const embedded = (html.match(/<img /g) ?? []).length;
      expect(embedded).toBeGreaterThan(0);
      expect(embedded).toBeLessThan(attachments.length);
      expect(html).toContain("no room left in this file");
      // The budget bounds the document, so what it holds must stay under it.
      expect(embedded * payload.length).toBeLessThanOrEqual(MAX_TOTAL_INLINE_CHARACTERS);
    });

    it("reads a repeated attachment once and embeds it in both places", async () => {
      const fetchMock = mockFetchOnce("hello");
      const html = await conversationToHtml(
        [
          message({ attachments: [attachment()] }),
          message({ attachments: [attachment()] }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect((html.match(/data:image\/png;base64,aGVsbG8=/g) ?? []).length).toBe(2);
      expect(html).toContain("Attachments: 2 of 2 embedded");
    });

    it("cancels the body of a response that declares it is too large", async () => {
      const blob = jest.fn();
      const cancel = jest.fn().mockResolvedValue(undefined);
      global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => String(11 * 1024 * 1024) },
        body: { cancel },
        blob,
      }) as unknown as typeof fetch;
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(blob).not.toHaveBeenCalled();
      expect(cancel).toHaveBeenCalledTimes(1);
      expect(html).toContain("[Image: result.png (image/png) — too large to embed]");
    });

    it("keeps the size reason when cancelling the body fails", async () => {
      const cancel = jest.fn().mockRejectedValue(new Error("already locked"));
      global.fetch = jest.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => String(11 * 1024 * 1024) },
        body: { cancel },
        blob: jest.fn(),
      }) as unknown as typeof fetch;
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(cancel).toHaveBeenCalledTimes(1);
      expect(html).toContain("[Image: result.png (image/png) — too large to embed]");
      expect(html).not.toContain("could not be read");
    });

    it("cancels the body of a failed response", async () => {
      const cancel = jest.fn().mockResolvedValue(undefined);
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        headers: { get: () => null },
        body: { cancel },
        blob: jest.fn(),
      }) as unknown as typeof fetch;
      const html = await conversationToHtml(
        [message({ attachments: [attachment()] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(cancel).toHaveBeenCalledTimes(1);
      expect(html).toContain("[Image: result.png (image/png) — could not be read]");
    });

    it("gives audio and video players an accessible name", async () => {
      const html = await conversationToHtml(
        [
          message({
            attachments: [
              attachment({ type: "audio", name: "clip.wav", mimeType: "audio/wav", url: "data:audio/wav;base64,AAAA" }),
              attachment({ type: "video", name: "clip.mp4", mimeType: "video/mp4", url: "data:video/mp4;base64,AAAA" }),
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain('aria-label="clip.wav"');
      expect(html).toContain('aria-label="clip.mp4"');
    });

    it("scales to the width of the screen it is opened on", async () => {
      const html = await conversationToHtml([message()], "conv-1", FIXED_NOW);
      expect(html).toContain('<meta name="viewport" content="width=device-width, initial-scale=1" />');
    });

    it("keeps text and media in the order the chat showed them", async () => {
      const img = attachment({ name: "middle.png", url: "data:image/png;base64,AAAA" });
      const html = await conversationToHtml(
        [
          message({
            content: "AAA_BEFORE\nZZZ_AFTER",
            attachments: [img],
            displayPieces: [
              { type: "text", pieceId: "p1", pieceIndex: 0, content: "AAA_BEFORE" },
              { type: "media", pieceId: "p2", pieceIndex: 1, attachment: img },
              { type: "text", pieceId: "p3", pieceIndex: 2, content: "ZZZ_AFTER" },
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html.indexOf("AAA_BEFORE")).toBeLessThan(html.indexOf("<img "));
      expect(html.indexOf("<img ")).toBeLessThan(html.indexOf("ZZZ_AFTER"));
      // The flattened pair would put both texts in one block ahead of the image.
      expect(html).not.toContain("AAA_BEFORE\nZZZ_AFTER");
    });

    it("keeps consecutive text pieces in a single block", async () => {
      const html = await conversationToHtml(
        [
          message({
            content: "FLATTENED_PAIR",
            displayPieces: [
              { type: "text", pieceId: "p1", pieceIndex: 0, content: "first" },
              { type: "text", pieceId: "p2", pieceIndex: 1, content: "second" },
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("<pre>first\nsecond</pre>");
      // The flat field is deliberately different here, so rendering it instead
      // of the pieces shows up rather than passing by coincidence.
      expect(html).not.toContain("FLATTENED_PAIR");
    });

    it("renders a message that has no display pieces from its flat fields", async () => {
      const html = await conversationToHtml(
        [message({ content: "optimistic", attachments: [attachment({ url: "data:image/png;base64,AAAA" })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("<pre>optimistic</pre>");
      // The per-message label, not the "Attachments: 1 of 1 embedded" summary.
      expect(html).toContain('<p class="label">Attachments:</p>');
      expect(html).toContain("<img ");
    });

    it("pairs each media piece with its own resolution", async () => {
      const inline = attachment({ name: "inline.png", url: "data:image/png;base64,AAAA" });
      const remote = attachment({
        name: "remote.png",
        url: "https://acct.blob.core.windows.net/c/remote.png?sv=2024&sig=SECRETSIG",
      });
      const html = await conversationToHtml(
        [
          message({
            content: "between",
            attachments: [inline, remote],
            displayPieces: [
              { type: "media", pieceId: "p1", pieceIndex: 0, attachment: inline },
              { type: "text", pieceId: "p2", pieceIndex: 1, content: "between" },
              { type: "media", pieceId: "p3", pieceIndex: 2, attachment: remote },
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      // Pairing the pieces with the wrong resolutions would embed the remote
      // one and put the placeholder on the one that was actually read.
      expect(html).toContain('<img src="data:image/png;base64,AAAA" alt="inline.png" />');
      expect(html).toContain("[Image: remote.png (image/png) — kept in remote storage]");
      expect(html).not.toContain("SECRETSIG");
    });

    it("keeps a backend message in piece order end to end", async () => {
      // The export pairs pieces with resolutions by position, which only holds
      // because the mapper fills both lists in the same pass. Pin that here so
      // a change to either side has to fail a test.
      const backend = {
        turn_number: 1,
        role: "assistant",
        created_at: "2026-07-22T02:30:07.000Z",
        message_pieces: [
          textPiece("p1", "AAA_BEFORE"),
          mediaPiece("p2", "data:image/png;base64,iVBORw0KGgo=", "embedded.png"),
          textPiece("p3", "ZZZ_AFTER"),
          mediaPiece("p4", "https://acct.blob.core.windows.net/c/late.png?sig=SECRETSIG", "late.png"),
        ],
      } as unknown as BackendMessage;

      const html = await conversationToHtml([backendMessageToFrontend(backend)], "conv-1", FIXED_NOW);

      expect(html.indexOf("AAA_BEFORE")).toBeLessThan(html.indexOf("<img "));
      expect(html.indexOf("<img ")).toBeLessThan(html.indexOf("ZZZ_AFTER"));
      expect(html.indexOf("ZZZ_AFTER")).toBeLessThan(html.indexOf("late.png (image/png)"));
      expect(html).toContain("iVBORw0KGgo=");
      expect(html).not.toContain("SECRETSIG");
      expect(html).toContain("Attachments: 1 of 2 embedded");
    });

    it("skips a display piece that carries scores but no media", async () => {
      const img = attachment({ name: "only.png", url: "data:image/png;base64,AAAA" });
      const html = await conversationToHtml(
        [
          message({
            content: "text",
            attachments: [img],
            displayPieces: [
              { type: "media", pieceId: "p1", pieceIndex: 0 },
              { type: "text", pieceId: "p2", pieceIndex: 1, content: "text" },
              { type: "media", pieceId: "p3", pieceIndex: 2, attachment: img },
            ],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      // The scores-only piece must not consume the resolution meant for the real one.
      expect((html.match(/<img /g) ?? []).length).toBe(1);
      expect(html).toContain("only.png");
      expect(html).toContain("Attachments: 1 of 1 embedded");
    });

    it("falls back to the flat list when the pieces do not account for every attachment", async () => {
      const shown = attachment({ name: "shown.png", url: "data:image/png;base64,AAAA" });
      const missing = attachment({ name: "missing.png", url: "data:image/png;base64,BBBB" });
      const html = await conversationToHtml(
        [
          message({
            content: "text",
            attachments: [shown, missing],
            displayPieces: [{ type: "media", pieceId: "p1", pieceIndex: 0, attachment: shown }],
          }),
        ],
        "conv-1",
        FIXED_NOW,
      );
      // Both were billed against the budget, so both have to appear.
      expect(html).toContain("shown.png");
      expect(html).toContain("missing.png");
      expect(html).toContain("Attachments: 2 of 2 embedded");
    });

    it("charges a percent-encoded payload the bytes it decodes to, not the characters it occupies", async () => {
      const url = `data:image/svg+xml,${"%41".repeat(4 * 1024 * 1024)}`;
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url })] })],
        "conv-1",
        FIXED_NOW,
      );
      // Twelve mebibytes of text carrying a four mebibyte image. The limit is
      // on the image, and the text is charged to the document budget instead.
      expect(html).not.toContain("too large to embed");
      expect(html).toContain("Attachments: 1 of 1 embedded");
    });

    it("measures a non-ascii payload in utf-8 bytes rather than characters", async () => {
      const url = `data:image/svg+xml,${"é".repeat(6 * 1024 * 1024)}`;
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url })] })],
        "conv-1",
        FIXED_NOW,
      );
      // 6M characters, but 12 MiB once encoded — counting characters would
      // have embedded it under a documented 10 MiB limit.
      expect(html).toContain("too large to embed");
    });

    it("ignores the line breaks in a wrapped base64 payload", async () => {
      // 12.8M base64 characters (9.6 MB) wrapped every 4th character. Counting
      // the newlines as data would measure 12 MB and refuse a payload that
      // fits well inside the 10 MiB limit.
      const wrapped = `data:image/png;base64,${"AAAA\n".repeat(3_200_000)}`;
      const html = await conversationToHtml(
        [message({ attachments: [attachment({ url: wrapped })] })],
        "conv-1",
        FIXED_NOW,
      );
      expect(html).toContain("Attachments: 1 of 1 embedded");
      expect(html).not.toContain("too large to embed");
    });
  });

  describe("exportConversation", () => {
    it("exports Markdown with the markdown mime type and rendered transcript", async () => {
      const { getAnchor } = installAnchorSpy();
      exportConversation({ messages: [message({ content: "hi" })], conversationId: "conv-1", format: "markdown", now: FIXED_NOW });

      const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
      expect(blob.type).toBe(EXPORT_MIME_TYPES.markdown);
      expect(getAnchor().download).toMatch(/^copyrit-conversation-conv-1-.*\.md$/);
      expect(await blobToText(blob)).toContain("# CoPyRIT conversation export");
    });

    it("exports JSON with the json mime type and a parseable envelope", async () => {
      const { getAnchor } = installAnchorSpy();
      exportConversation({ messages: [message({ content: "hi" })], conversationId: "conv-1", format: "json", now: FIXED_NOW });

      const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
      expect(blob.type).toBe(EXPORT_MIME_TYPES.json);
      expect(getAnchor().download).toMatch(/^copyrit-conversation-conv-1-.*\.json$/);
      expect(JSON.parse(await blobToText(blob)).conversation_id).toBe("conv-1");
    });

    it("defaults the timestamp when now is omitted", () => {
      const { getAnchor } = installAnchorSpy();
      exportConversation({ messages: [message()], conversationId: "conv-1", format: "markdown" });
      expect(getAnchor().download).toMatch(/^copyrit-conversation-conv-1-.*\.md$/);
    });

    it("uses one timestamp for both the JSON body and the filename", async () => {
      const { getAnchor } = installAnchorSpy();
      exportConversation({ messages: [message({ content: "hi" })], conversationId: "conv-1", format: "json", now: FIXED_NOW });

      const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
      expect(JSON.parse(await blobToText(blob)).exported_at).toBe(FIXED_NOW.toISOString());
      expect(getAnchor().download).toContain("2026-07-22T02-34-01-059");
    });

    // Markdown and JSON must not await, so they keep downloading in the same
    // tick. Adding an await on those branches would silently break callers
    // that do not expect a pending promise.
    it("still downloads Markdown synchronously", () => {
      installAnchorSpy();
      void exportConversation({ messages: [message({ content: "hi" })], conversationId: "conv-1", format: "markdown", now: FIXED_NOW });
      expect(URL.createObjectURL as jest.Mock).toHaveBeenCalledTimes(1);
    });

    it("exports HTML with the html mime type and an embedded transcript", async () => {
      const { getAnchor } = installAnchorSpy();
      await exportConversation({ messages: [message({ content: "hi" })], conversationId: "conv-1", format: "html", now: FIXED_NOW });

      const blob = (URL.createObjectURL as jest.Mock).mock.calls[0][0] as Blob;
      expect(blob.type).toBe(EXPORT_MIME_TYPES.html);
      expect(getAnchor().download).toMatch(/^copyrit-conversation-conv-1-.*\.html$/);
      expect(await blobToText(blob)).toContain("<h1>CoPyRIT conversation export</h1>");
    });
  });
});

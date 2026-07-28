import {
  exportConversation,
  conversationToMarkdown,
  conversationToJson,
  buildExportFilename,
  downloadTextFile,
  EXPORT_MIME_TYPES,
} from "./conversationExport";
import type { Message } from "../types";

const FIXED_NOW = new Date("2026-07-22T02:34:01.059Z");

function message(overrides: Partial<Message> = {}): Message {
  return {
    role: "assistant",
    content: "Hello there",
    timestamp: "2026-07-22T02:30:07.000Z",
    ...overrides,
  };
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
  });
});

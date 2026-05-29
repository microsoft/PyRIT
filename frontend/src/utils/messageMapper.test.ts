import {
  fileToBase64,
  mimeTypeToDataType,
  dataTypeToAttachmentType,
  buildDataUri,
  backendMessageToFrontend,
  backendMessagesToFrontend,
  attachmentToMessagePieceRequest,
  buildMessagePieces,
} from "./messageMapper";
import type { BackendMessage, MessageAttachment } from "../types";

describe("messageMapper", () => {
  describe("fileToBase64", () => {
    it("should convert a file to base64 string", async () => {
      const file = new File(["hello world"], "test.txt", {
        type: "text/plain",
      });
      const result = await fileToBase64(file);
      // base64 of "hello world"
      expect(result).toBe("aGVsbG8gd29ybGQ=");
    });

    it("should convert an image file to base64", async () => {
      const content = new Uint8Array([137, 80, 78, 71]); // PNG magic bytes
      const file = new File([content], "test.png", { type: "image/png" });
      const result = await fileToBase64(file);
      expect(typeof result).toBe("string");
      expect(result.length).toBeGreaterThan(0);
    });

    it("should handle empty file", async () => {
      const file = new File([""], "empty.txt", { type: "text/plain" });
      const result = await fileToBase64(file);
      expect(result).toBe("");
    });
  });

  describe("mimeTypeToDataType", () => {
    it("should map image MIME types to image_path", () => {
      expect(mimeTypeToDataType("image/png")).toBe("image_path");
      expect(mimeTypeToDataType("image/jpeg")).toBe("image_path");
      expect(mimeTypeToDataType("image/gif")).toBe("image_path");
    });

    it("should map audio MIME types to audio_path", () => {
      expect(mimeTypeToDataType("audio/wav")).toBe("audio_path");
      expect(mimeTypeToDataType("audio/mpeg")).toBe("audio_path");
      expect(mimeTypeToDataType("audio/ogg")).toBe("audio_path");
    });

    it("should map video MIME types to video_path", () => {
      expect(mimeTypeToDataType("video/mp4")).toBe("video_path");
      expect(mimeTypeToDataType("video/webm")).toBe("video_path");
    });

    it("should map other MIME types to binary_path", () => {
      expect(mimeTypeToDataType("application/pdf")).toBe("binary_path");
      expect(mimeTypeToDataType("text/plain")).toBe("binary_path");
    });
  });

  describe("dataTypeToAttachmentType", () => {
    it("should map image data types to image", () => {
      expect(dataTypeToAttachmentType("image_path")).toBe("image");
      expect(dataTypeToAttachmentType("image")).toBe("image");
    });

    it("should map audio data types to audio", () => {
      expect(dataTypeToAttachmentType("audio_path")).toBe("audio");
      expect(dataTypeToAttachmentType("audio")).toBe("audio");
    });

    it("should map video data types to video", () => {
      expect(dataTypeToAttachmentType("video_path")).toBe("video");
      expect(dataTypeToAttachmentType("video")).toBe("video");
    });

    it("should map other data types to file", () => {
      expect(dataTypeToAttachmentType("text")).toBe("file");
      expect(dataTypeToAttachmentType("binary_path")).toBe("file");
    });
  });

  describe("buildDataUri", () => {
    it("should build a data URI from base64 and MIME type", () => {
      const result = buildDataUri("aGVsbG8=", "image/png");
      expect(result).toBe("data:image/png;base64,aGVsbG8=");
    });

    it("should build audio data URI", () => {
      const result = buildDataUri("YXVkaW8=", "audio/wav");
      expect(result).toBe("data:audio/wav;base64,YXVkaW8=");
    });
  });

  describe("backendMessageToFrontend", () => {
    it("should convert a text message", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: "Hello there",
            converted_value: "Hello there",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.role).toBe("assistant");
      expect(result.content).toBe("Hello there");
      expect(result.attachments).toBeUndefined();
      expect(result.error).toBeUndefined();
    });

    it("should convert an image response", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "image_path",
            original_value: "generate an image",
            converted_value: "/api/media?path=output%2Fimage.png",
            converted_value_mime_type: "image/png",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.attachments).toHaveLength(1);
      expect(result.attachments![0].type).toBe("image");
      expect(result.attachments![0].url).toBe(
        "/api/media?path=output%2Fimage.png"
      );
    });

    it("should convert an audio response", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "audio_path",
            original_value: "speak this",
            converted_value: "/api/media?path=output%2Fspeech.wav",
            converted_value_mime_type: "audio/wav",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.attachments).toHaveLength(1);
      expect(result.attachments![0].type).toBe("audio");
      expect(result.attachments![0].url).toBe("/api/media?path=output%2Fspeech.wav");
    });

    it("should convert a video response", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "video_path",
            original_value: "generate video",
            converted_value: "/api/media?path=output%2Fclip.mp4",
            converted_value_mime_type: "video/mp4",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.attachments).toHaveLength(1);
      expect(result.attachments![0].type).toBe("video");
      expect(result.attachments![0].url).toBe("/api/media?path=output%2Fclip.mp4");
    });

    it("should preserve prompt_metadata on video attachment for remix", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "vid-1",
            original_value_data_type: "text",
            converted_value_data_type: "video_path",
            original_value: "generate video",
            converted_value: "/api/media?path=output%2Fclip.mp4",
            converted_value_mime_type: "video/mp4",
            prompt_metadata: { video_id: "sora-vid-789" },
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.attachments).toHaveLength(1);
      expect(result.attachments![0].metadata).toEqual({ video_id: "sora-vid-789" });
    });

    it("should render binary_path pieces as file attachments, not text", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "binary_path",
            original_value: "convert this",
            converted_value: "/api/media?path=output%2Fdoc.pdf",
            converted_value_mime_type: "application/pdf",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      // Should NOT appear as text content
      expect(result.content).toBe("");
      // Should appear as a file attachment
      expect(result.attachments).toHaveLength(1);
      expect(result.attachments![0].type).toBe("file");
      expect(result.attachments![0].url).toBe("/api/media?path=output%2Fdoc.pdf");
    });

    it("should handle error response", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "",
            scores: [],
            response_error: "blocked",
            response_error_description: "Content was filtered",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.error).toBeDefined();
      expect(result.error!.type).toBe("blocked");
      expect(result.error!.description).toBe("Content was filtered");
    });

    it("should handle multi-piece message with text + image", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "Here is the image:",
            scores: [],
            response_error: "none",
          },
          {
            piece_id: "p2",
            original_value_data_type: "text",
            converted_value_data_type: "image_path",
            converted_value: "aW1hZ2U=",
            converted_value_mime_type: "image/jpeg",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.content).toBe("Here is the image:");
      expect(result.attachments).toHaveLength(1);
      expect(result.attachments![0].type).toBe("image");
    });

    it("should map user role correctly", () => {
      const msg: BackendMessage = {
        turn_number: 0,
        role: "user",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "test",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      expect(backendMessageToFrontend(msg).role).toBe("user");
    });

    it("should map system role correctly", () => {
      const msg: BackendMessage = {
        turn_number: 0,
        role: "system",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "You are helpful",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      expect(backendMessageToFrontend(msg).role).toBe("system");
    });

    it("should preserve simulated_assistant role", () => {
      const msg: BackendMessage = {
        turn_number: 0,
        role: "simulated_assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "injected",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      expect(backendMessageToFrontend(msg).role).toBe("simulated_assistant");
    });

    it("should use default MIME type when none provided for image", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "image_path",
            converted_value: "/api/media?path=output%2Fimg.png",
            // No converted_value_mime_type
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.attachments![0].url).toBe("/api/media?path=output%2Fimg.png");
      expect(result.attachments![0].mimeType).toBe("image/png");
    });
  });

  describe("backendMessagesToFrontend", () => {
    it("should convert multiple messages", () => {
      const messages: BackendMessage[] = [
        {
          turn_number: 0,
          role: "user",
          pieces: [
            {
              piece_id: "p1",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              converted_value: "Hello",
              scores: [],
              response_error: "none",
            },
          ],
          created_at: "2026-02-15T00:00:00Z",
        },
        {
          turn_number: 1,
          role: "assistant",
          pieces: [
            {
              piece_id: "p2",
              original_value_data_type: "text",
              converted_value_data_type: "text",
              converted_value: "Hi there!",
              scores: [],
              response_error: "none",
            },
          ],
          created_at: "2026-02-15T00:00:01Z",
        },
      ];

      const result = backendMessagesToFrontend(messages);
      expect(result).toHaveLength(2);
      expect(result[0].role).toBe("user");
      expect(result[1].role).toBe("assistant");
    });

    it("should handle empty message list", () => {
      expect(backendMessagesToFrontend([])).toEqual([]);
    });
  });

  describe("attachmentToMessagePieceRequest", () => {
    it("should convert image attachment with File", async () => {
      const file = new File(["imagedata"], "test.png", { type: "image/png" });
      const att: MessageAttachment = {
        type: "image",
        name: "test.png",
        url: "blob:http://localhost/abc",
        mimeType: "image/png",
        size: 9,
        file,
      };

      const result = await attachmentToMessagePieceRequest(att);

      expect(result.data_type).toBe("image_path");
      expect(result.mime_type).toBe("image/png");
      expect(typeof result.original_value).toBe("string");
      expect(result.original_value.length).toBeGreaterThan(0);
    });

    it("should convert audio attachment with File", async () => {
      const file = new File(["audiodata"], "sound.wav", { type: "audio/wav" });
      const att: MessageAttachment = {
        type: "audio",
        name: "sound.wav",
        url: "blob:http://localhost/def",
        mimeType: "audio/wav",
        size: 9,
        file,
      };

      const result = await attachmentToMessagePieceRequest(att);

      expect(result.data_type).toBe("audio_path");
      expect(result.mime_type).toBe("audio/wav");
    });

    it("should convert video attachment with File", async () => {
      const file = new File(["videodata"], "clip.mp4", { type: "video/mp4" });
      const att: MessageAttachment = {
        type: "video",
        name: "clip.mp4",
        url: "blob:http://localhost/ghi",
        mimeType: "video/mp4",
        size: 9,
        file,
      };

      const result = await attachmentToMessagePieceRequest(att);

      expect(result.data_type).toBe("video_path");
      expect(result.mime_type).toBe("video/mp4");
    });

    it("should handle attachment with data URI url (no File)", async () => {
      const att: MessageAttachment = {
        type: "image",
        name: "embedded.png",
        url: "data:image/png;base64,iVBORw0KGgo=",
        mimeType: "image/png",
        size: 100,
      };

      const result = await attachmentToMessagePieceRequest(att);

      expect(result.original_value).toBe("iVBORw0KGgo=");
      expect(result.data_type).toBe("image_path");
    });

    it("should convert PDF attachment to binary_path", async () => {
      const file = new File(["pdfcontent"], "doc.pdf", {
        type: "application/pdf",
      });
      const att: MessageAttachment = {
        type: "file",
        name: "doc.pdf",
        url: "blob:http://localhost/jkl",
        mimeType: "application/pdf",
        size: 10,
        file,
      };

      const result = await attachmentToMessagePieceRequest(att);

      expect(result.data_type).toBe("binary_path");
      expect(result.mime_type).toBe("application/pdf");
    });
  });

  describe("buildMessagePieces", () => {
    it("should build text-only pieces", async () => {
      const pieces = await buildMessagePieces("Hello world", []);
      expect(pieces).toHaveLength(1);
      expect(pieces[0].data_type).toBe("text");
      expect(pieces[0].original_value).toBe("Hello world");
    });

    it("should build text + attachment pieces", async () => {
      const file = new File(["img"], "photo.png", { type: "image/png" });
      const attachments: MessageAttachment[] = [
        {
          type: "image",
          name: "photo.png",
          url: "blob:test",
          mimeType: "image/png",
          size: 3,
          file,
        },
      ];

      const pieces = await buildMessagePieces("Describe this", attachments);

      expect(pieces).toHaveLength(2);
      expect(pieces[0].data_type).toBe("text");
      expect(pieces[0].original_value).toBe("Describe this");
      expect(pieces[1].data_type).toBe("image_path");
      expect(pieces[1].mime_type).toBe("image/png");
    });

    it("should build attachment-only pieces (no text)", async () => {
      const file = new File(["audio"], "audio.wav", { type: "audio/wav" });
      const attachments: MessageAttachment[] = [
        {
          type: "audio",
          name: "audio.wav",
          url: "blob:test",
          mimeType: "audio/wav",
          size: 5,
          file,
        },
      ];

      const pieces = await buildMessagePieces("", attachments);

      expect(pieces).toHaveLength(1);
      expect(pieces[0].data_type).toBe("audio_path");
    });

    it("should skip text piece when only whitespace", async () => {
      const pieces = await buildMessagePieces("   ", []);
      expect(pieces).toHaveLength(0);
    });

    it("should handle multiple attachments", async () => {
      const img = new File(["img"], "photo.png", { type: "image/png" });
      const audio = new File(["aud"], "sound.mp3", { type: "audio/mpeg" });
      const attachments: MessageAttachment[] = [
        {
          type: "image",
          name: "photo.png",
          url: "blob:a",
          mimeType: "image/png",
          size: 3,
          file: img,
        },
        {
          type: "audio",
          name: "sound.mp3",
          url: "blob:b",
          mimeType: "audio/mpeg",
          size: 3,
          file: audio,
        },
      ];

      const pieces = await buildMessagePieces("test", attachments);

      expect(pieces).toHaveLength(3);
      expect(pieces[0].data_type).toBe("text");
      expect(pieces[1].data_type).toBe("image_path");
      expect(pieces[2].data_type).toBe("audio_path");
    });

    it("should set video_id on text piece when video attachment has metadata", async () => {
      const attachments: MessageAttachment[] = [
        {
          type: "video",
          name: "clip.mp4",
          url: "data:video/mp4;base64,AAAA",
          mimeType: "video/mp4",
          size: 4,
          pieceId: "vid-piece-1",
          metadata: { video_id: "sora-vid-123" },
        },
      ];

      const pieces = await buildMessagePieces("remix this", attachments);

      expect(pieces).toHaveLength(2);
      expect(pieces[0].data_type).toBe("text");
      expect(pieces[0].prompt_metadata).toEqual({ video_id: "sora-vid-123" });
      expect(pieces[1].data_type).toBe("video_path");
      expect(pieces[1].prompt_metadata).toEqual({ video_id: "sora-vid-123" });
    });

    it("should not set prompt_metadata on text piece when no video attachment", async () => {
      const pieces = await buildMessagePieces("just text", []);

      expect(pieces).toHaveLength(1);
      expect(pieces[0].prompt_metadata).toBeUndefined();
    });

    it("should carry metadata through attachmentToMessagePieceRequest", async () => {
      const att: MessageAttachment = {
        type: "video",
        name: "clip.mp4",
        url: "data:video/mp4;base64,AAAA",
        mimeType: "video/mp4",
        size: 4,
        pieceId: "vid-piece-1",
        metadata: { video_id: "sora-vid-456" },
      };

      const result = await attachmentToMessagePieceRequest(att);

      expect(result.prompt_metadata).toEqual({ video_id: "sora-vid-456" });
    });
  });

  describe("reasoning summary extraction", () => {
    it("should extract reasoning summaries from reasoning piece", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "r1",
            original_value_data_type: "reasoning",
            converted_value_data_type: "reasoning",
            converted_value: JSON.stringify({
              type: "reasoning",
              summary: [{ type: "summary_text", text: "The model thought about this." }],
            }),
            scores: [],
            response_error: "none",
          },
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "Here is the answer.",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.content).toBe("Here is the answer.");
      expect(result.reasoningSummaries).toEqual(["The model thought about this."]);
    });

    it("should handle multiple reasoning summaries", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "r1",
            original_value_data_type: "reasoning",
            converted_value_data_type: "reasoning",
            converted_value: JSON.stringify({
              type: "reasoning",
              summary: [
                { type: "summary_text", text: "First thought." },
                { type: "summary_text", text: "Second thought." },
              ],
            }),
            scores: [],
            response_error: "none",
          },
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "Answer.",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.reasoningSummaries).toEqual(["First thought.", "Second thought."]);
    });

    it("should omit reasoningSummaries when empty", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "r1",
            original_value_data_type: "reasoning",
            converted_value_data_type: "reasoning",
            converted_value: JSON.stringify({
              type: "reasoning",
              summary: [],
            }),
            scores: [],
            response_error: "none",
          },
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "Just text.",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      expect(result.content).toBe("Just text.");
      expect(result.reasoningSummaries).toEqual([
        "(Reasoning was performed but details are not available)",
      ]);
    });

    it("should not include reasoning value as text content", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "r1",
            original_value_data_type: "reasoning",
            converted_value_data_type: "reasoning",
            converted_value: JSON.stringify({
              type: "reasoning",
              summary: [{ type: "summary_text", text: "Hidden reasoning." }],
            }),
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      // The raw JSON should NOT appear in content
      expect(result.content).toBe("");
      expect(result.reasoningSummaries).toEqual(["Hidden reasoning."]);
    });

    it("should handle non-JSON reasoning value gracefully", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "r1",
            original_value_data_type: "reasoning",
            converted_value_data_type: "reasoning",
            converted_value: "plain text reasoning",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);

      // Falls back to using raw value
      expect(result.reasoningSummaries).toEqual(["plain text reasoning"]);
    });
  });

  describe("original vs converted value propagation", () => {
    it("should set originalContent when original differs from converted", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "user",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: "Tell me a joke",
            converted_value: "VGVsbCBtZSBhIGpva2U=",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.content).toBe("VGVsbCBtZSBhIGpva2U=");
      expect(result.originalContent).toBe("Tell me a joke");
    });

    it("should not set originalContent when original equals converted", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "user",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: "Hello",
            converted_value: "Hello",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.content).toBe("Hello");
      expect(result.originalContent).toBeUndefined();
    });

    it("should detect even whitespace differences", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "user",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: "Hello",
            converted_value: " Hello",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.originalContent).toBe("Hello");
    });

    it("should detect capitalization differences", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "user",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: "Hello World",
            converted_value: "hello world",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.originalContent).toBe("Hello World");
      expect(result.content).toBe("hello world");
    });

    it("should not set originalContent when original_value is null", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            original_value: null,
            converted_value: "Some response",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.content).toBe("Some response");
      expect(result.originalContent).toBeUndefined();
    });

    it("should not set originalContent when original_value is undefined", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "text",
            converted_value_data_type: "text",
            converted_value: "Some response",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.content).toBe("Some response");
      expect(result.originalContent).toBeUndefined();
    });

    it("should include original media attachments when they differ from converted", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "image_path",
            converted_value_data_type: "image_path",
            original_value: "originalImageData",
            converted_value: "convertedImageData",
            converted_value_mime_type: "image/png",
            original_value_mime_type: "image/jpeg",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.attachments).toBeDefined();
      expect(result.originalAttachments).toBeDefined();
    });

    it("should not include originalAttachments when media is identical", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p1",
            original_value_data_type: "image_path",
            converted_value_data_type: "image_path",
            original_value: "sameData",
            converted_value: "sameData",
            converted_value_mime_type: "image/png",
            original_value_mime_type: "image/png",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.attachments).toBeDefined();
      expect(result.originalAttachments).toBeUndefined();
    });
  });

  describe("defaultMimeForDataType edge cases", () => {
    it("should use audio default mime when no mime provided for audio_path", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p-audio",
            original_value_data_type: "text",
            converted_value_data_type: "audio_path",
            converted_value: "audioBase64Data",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.attachments).toBeDefined();
      expect(result.attachments![0].mimeType).toBe("audio/wav");
    });

    it("should use video default mime when no mime provided for video_path", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "p-video",
            original_value_data_type: "text",
            converted_value_data_type: "video_path",
            converted_value: "videoBase64Data",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-25T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.attachments).toBeDefined();
      expect(result.attachments![0].mimeType).toBe("video/mp4");
    });
  });

  describe("reasoning content fallback", () => {
    it("should return content field when present but no summaries", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "r1",
            original_value_data_type: "reasoning",
            converted_value_data_type: "reasoning",
            converted_value: JSON.stringify({
              type: "reasoning",
              content: "Some readable content here",
              summary: [],
            }),
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      expect(result.reasoningSummaries).toEqual(["Some readable content here"]);
    });

    it("should return empty array when reasoning value is empty", () => {
      const msg: BackendMessage = {
        turn_number: 1,
        role: "assistant",
        pieces: [
          {
            piece_id: "r1",
            original_value_data_type: "reasoning",
            converted_value_data_type: "reasoning",
            converted_value: "",
            scores: [],
            response_error: "none",
          },
        ],
        created_at: "2026-02-15T00:00:00Z",
      };

      const result = backendMessageToFrontend(msg);
      // Empty value → no summaries
      expect(result.reasoningSummaries).toBeUndefined();
    });
  });

  describe("attachment URL handling", () => {
    it("should use raw URL when attachment has no file and no data URI", async () => {
      const att: MessageAttachment = {
        type: "image",
        name: "remote.png",
        url: "https://example.com/image.png",
        mimeType: "image/png",
        size: 1000,
      };

      const result = await attachmentToMessagePieceRequest(att);
      expect(result.original_value).toBe("https://example.com/image.png");
    });
  });
});

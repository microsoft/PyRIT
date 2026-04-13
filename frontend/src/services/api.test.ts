// Mock axios before importing the module
jest.mock("axios", () => ({
  create: jest.fn(() => ({
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
    interceptors: {
      request: { use: jest.fn() },
      response: { use: jest.fn() },
    },
  })),
}));

import {
  apiClient,
  healthApi,
  versionApi,
  targetsApi,
  attacksApi,
} from "./api";

describe("api service", () => {
  // Interceptor functions are registered at module-load time.
  // Capture them before beforeEach's clearAllMocks wipes the call records.
  const requestInterceptor = (apiClient.interceptors.request.use as jest.Mock).mock.calls[0]?.[0];
  const [responseOnSuccess, responseOnError] =
    (apiClient.interceptors.response.use as jest.Mock).mock.calls[0] ?? [];

  beforeEach(() => {
    jest.clearAllMocks();
  });

  describe("apiClient", () => {
    it("should be defined", () => {
      expect(apiClient).toBeDefined();
    });

    it("should have correct methods", () => {
      expect(apiClient.get).toBeDefined();
      expect(apiClient.post).toBeDefined();
    });
  });

  describe("interceptors", () => {
    it("should register a request interceptor", () => {
      expect(requestInterceptor).toBeDefined();
      expect(typeof requestInterceptor).toBe("function");
    });

    it("request interceptor adds X-Request-ID header", async () => {
      const headers: Record<string, string> & { set: (k: string, v: string) => void } = Object.assign(
        {} as Record<string, string>,
        { set(k: string, v: string) { this[k] = v; } }
      );
      const config = { headers };
      const result = await requestInterceptor(config);
      expect(result.headers["X-Request-ID"]).toBeDefined();
      expect(typeof result.headers["X-Request-ID"]).toBe("string");
      expect(result.headers["X-Request-ID"].length).toBeGreaterThan(0);
    });

    it("request interceptor generates UUID-like format", async () => {
      const headers: Record<string, string> & { set: (k: string, v: string) => void } = Object.assign(
        {} as Record<string, string>,
        { set(k: string, v: string) { this[k] = v; } }
      );
      const config = { headers };
      const result = await requestInterceptor(config);
      // UUID v4 pattern: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
      expect(result.headers["X-Request-ID"]).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
      );
    });

    it("should register a response interceptor", () => {
      expect(responseOnSuccess).toBeDefined();
      expect(responseOnError).toBeDefined();
      expect(typeof responseOnSuccess).toBe("function");
      expect(typeof responseOnError).toBe("function");
    });

    it("response interceptor passes through successful responses", () => {
      const response = { status: 200, data: { ok: true } };
      expect(responseOnSuccess(response)).toBe(response);
    });

    it("response interceptor logs and re-rejects on error", async () => {
      const consoleSpy = jest.spyOn(console, "error").mockImplementation(() => {});

      const error = {
        isAxiosError: true,
        config: { method: "post", url: "/attacks", headers: { "X-Request-ID": "test-id" } },
        response: { status: 500, data: { detail: "Internal error" } },
      };

      await expect(responseOnError(error)).rejects.toBe(error);
      expect(consoleSpy).toHaveBeenCalledWith(
        expect.stringContaining("POST /attacks failed")
      );
      expect(consoleSpy).toHaveBeenCalledWith(
        expect.stringContaining("requestId=test-id")
      );

      consoleSpy.mockRestore();
    });
  });

  describe("healthApi", () => {
    it("should have checkHealth method", () => {
      expect(healthApi.checkHealth).toBeDefined();
      expect(typeof healthApi.checkHealth).toBe("function");
    });

    it("should call correct endpoint", async () => {
      const mockResponse = { data: { status: "healthy" } };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await healthApi.checkHealth();

      expect(apiClient.get).toHaveBeenCalledWith("/health");
      expect(result).toEqual({ status: "healthy" });
    });

    it("should handle errors", async () => {
      const error = new Error("Network error");
      (apiClient.get as jest.Mock).mockRejectedValueOnce(error);

      await expect(healthApi.checkHealth()).rejects.toThrow("Network error");
    });
  });

  describe("versionApi", () => {
    it("should have getVersion method", () => {
      expect(versionApi.getVersion).toBeDefined();
      expect(typeof versionApi.getVersion).toBe("function");
    });

    it("should call correct endpoint", async () => {
      const mockResponse = { data: { version: "0.10.1" } };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await versionApi.getVersion();

      expect(apiClient.get).toHaveBeenCalledWith("/version");
      expect(result).toEqual({ version: "0.10.1" });
    });
  });

  describe("targetsApi", () => {
    it("should list targets with default params", async () => {
      const mockResponse = {
        data: {
          items: [
            {
              target_registry_name: "test-target",
              target_type: "OpenAIChatTarget",
            },
          ],
          pagination: { limit: 50, has_more: false },
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await targetsApi.listTargets();

      expect(apiClient.get).toHaveBeenCalledWith("/targets", {
        params: { limit: 50 },
      });
      expect(result.items).toHaveLength(1);
      expect(result.items[0].target_type).toBe("OpenAIChatTarget");
    });

    it("should list targets with custom limit and cursor", async () => {
      const mockResponse = {
        data: { items: [], pagination: { limit: 10, has_more: false } },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      await targetsApi.listTargets(10, "cursor-abc");

      expect(apiClient.get).toHaveBeenCalledWith("/targets", {
        params: { limit: 10, cursor: "cursor-abc" },
      });
    });

    it("should get a specific target", async () => {
      const mockResponse = {
        data: {
          target_registry_name: "my-target",
          target_type: "OpenAIImageTarget",
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await targetsApi.getTarget("my-target");

      expect(apiClient.get).toHaveBeenCalledWith("/targets/my-target");
      expect(result.target_type).toBe("OpenAIImageTarget");
    });

    it("should create a target", async () => {
      const mockResponse = {
        data: {
          target_registry_name: "new-target",
          target_type: "OpenAIChatTarget",
        },
      };
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await targetsApi.createTarget({
        type: "OpenAIChatTarget",
        params: { endpoint: "https://test.openai.azure.com/" },
      });

      expect(apiClient.post).toHaveBeenCalledWith("/targets", {
        type: "OpenAIChatTarget",
        params: { endpoint: "https://test.openai.azure.com/" },
      });
      expect(result.target_registry_name).toBe("new-target");
    });

    it("should handle list targets error", async () => {
      const error = new Error("Server error");
      (apiClient.get as jest.Mock).mockRejectedValueOnce(error);

      await expect(targetsApi.listTargets()).rejects.toThrow("Server error");
    });
  });

  describe("attacksApi", () => {
    it("should create an attack", async () => {
      const mockResponse = {
        data: {
          attack_result_id: "ar-123",
          conversation_id: "conv-123",
          created_at: "2026-02-15T00:00:00Z",
        },
      };
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await attacksApi.createAttack({
        target_registry_name: "test-target",
      });

      expect(apiClient.post).toHaveBeenCalledWith("/attacks", {
        target_registry_name: "test-target",
      });
      expect(result.attack_result_id).toBe("ar-123");
      expect(result.conversation_id).toBe("conv-123");
    });

    it("should get an attack by attack result id", async () => {
      const mockResponse = {
        data: {
          conversation_id: "conv-123",
          attack_type: "ManualAttack",
          message_count: 2,
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await attacksApi.getAttack("ar-conv-123");

      expect(apiClient.get).toHaveBeenCalledWith("/attacks/ar-conv-123");
      expect(result.attack_type).toBe("ManualAttack");
    });

    it("should get attack messages", async () => {
      const mockResponse = {
        data: {
          conversation_id: "conv-123",
          messages: [
            {
              turn_number: 1,
              role: "user",
              pieces: [
                {
                  piece_id: "p1",
                  converted_value: "Hello",
                  converted_value_data_type: "text",
                },
              ],
              created_at: "2026-02-15T00:00:00Z",
            },
          ],
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await attacksApi.getMessages("ar-conv-123", "conv-123");

      expect(apiClient.get).toHaveBeenCalledWith(
        "/attacks/ar-conv-123/messages",
        { params: { conversation_id: "conv-123" } }
      );
      expect(result.messages).toHaveLength(1);
    });

    it("should add a text message to an attack", async () => {
      const mockResponse = {
        data: {
          attack: { conversation_id: "conv-123", message_count: 2 },
          messages: { conversation_id: "conv-123", messages: [] },
        },
      };
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await attacksApi.addMessage("ar-conv-123", {
        role: "user",
        pieces: [{ data_type: "text", original_value: "Hello" }],
        send: true,
        target_conversation_id: "conv-123",
        target_registry_name: "test-target",
      });

      expect(apiClient.post).toHaveBeenCalledWith(
        "/attacks/ar-conv-123/messages",
        {
          role: "user",
          pieces: [{ data_type: "text", original_value: "Hello" }],
          send: true,
          target_conversation_id: "conv-123",
          target_registry_name: "test-target",
        }
      );
      expect(result.attack.conversation_id).toBe("conv-123");
    });

    it("should add a message with image attachment", async () => {
      const mockResponse = {
        data: {
          attack: { conversation_id: "conv-123", message_count: 2 },
          messages: { conversation_id: "conv-123", messages: [] },
        },
      };
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);

      await attacksApi.addMessage("ar-conv-123", {
        role: "user",
        pieces: [
          { data_type: "text", original_value: "What is in this image?" },
          {
            data_type: "image_path",
            original_value: "base64encodeddata",
            mime_type: "image/png",
          },
        ],
        send: true,
        target_conversation_id: "conv-123",
        target_registry_name: "test-target",
      });

      expect(apiClient.post).toHaveBeenCalledWith(
        "/attacks/ar-conv-123/messages",
        expect.objectContaining({
          pieces: expect.arrayContaining([
            expect.objectContaining({ data_type: "image_path" }),
          ]),
        })
      );
    });

    it("should add a message with audio attachment", async () => {
      const mockResponse = {
        data: {
          attack: { conversation_id: "conv-123", message_count: 2 },
          messages: { conversation_id: "conv-123", messages: [] },
        },
      };
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);

      await attacksApi.addMessage("ar-conv-123", {
        role: "user",
        pieces: [
          {
            data_type: "audio_path",
            original_value: "base64audiodata",
            mime_type: "audio/wav",
          },
        ],
        send: true,
        target_conversation_id: "conv-123",
        target_registry_name: "test-target",
      });

      expect(apiClient.post).toHaveBeenCalledWith(
        "/attacks/ar-conv-123/messages",
        expect.objectContaining({
          pieces: [
            expect.objectContaining({
              data_type: "audio_path",
              mime_type: "audio/wav",
            }),
          ],
        })
      );
    });

    it("should add a message with video attachment", async () => {
      const mockResponse = {
        data: {
          attack: { conversation_id: "conv-123", message_count: 2 },
          messages: { conversation_id: "conv-123", messages: [] },
        },
      };
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);

      await attacksApi.addMessage("ar-conv-123", {
        role: "user",
        pieces: [
          {
            data_type: "video_path",
            original_value: "base64videodata",
            mime_type: "video/mp4",
          },
        ],
        send: true,
        target_conversation_id: "conv-123",
        target_registry_name: "test-target",
      });

      expect(apiClient.post).toHaveBeenCalledWith(
        "/attacks/ar-conv-123/messages",
        expect.objectContaining({
          pieces: [
            expect.objectContaining({
              data_type: "video_path",
              mime_type: "video/mp4",
            }),
          ],
        })
      );
    });

    it("should list attacks with filters", async () => {
      const mockResponse = {
        data: {
          items: [],
          pagination: { limit: 20, has_more: false },
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      await attacksApi.listAttacks({ limit: 10, outcome: "success" });

      expect(apiClient.get).toHaveBeenCalledWith("/attacks", {
        params: { limit: 10, outcome: "success" },
        paramsSerializer: {
          indexes: null,
        },
      });
    });

    it("should handle add message error", async () => {
      const error = new Error("Target not found");
      (apiClient.post as jest.Mock).mockRejectedValueOnce(error);

      await expect(
        attacksApi.addMessage("conv-123", {
          role: "user",
          pieces: [{ data_type: "text", original_value: "test" }],
          send: true,
          target_conversation_id: "conv-456",
          target_registry_name: "test-target",
        })
      ).rejects.toThrow("Target not found");
    });
  });
});

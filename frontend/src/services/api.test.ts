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
  authApi,
  healthApi,
  initializersApi,
  versionApi,
  configurationApi,
  targetsApi,
  attacksApi,
  scenariosApi,
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

  describe("authApi", () => {
    it("should read current administrator access", async () => {
      const response = { data: { isAdmin: true } };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(response);

      await expect(authApi.getAccess()).resolves.toEqual(response.data);
      expect(apiClient.get).toHaveBeenCalledWith("/auth/access");
    });
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

  describe("initializersApi", () => {
    it("should list stored custom initializers", async () => {
      const response = { data: { source: "C:/custom", items: [] } };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(response);

      await expect(initializersApi.listCustom()).resolves.toEqual(response.data);
      expect(apiClient.get).toHaveBeenCalledWith("/initializers/custom");
    });

    it("should register and unregister through existing endpoints", async () => {
      const request = { name: "custom", script_content: "class Custom: pass\n" };
      (apiClient.post as jest.Mock).mockResolvedValueOnce({ status: 201 });
      (apiClient.delete as jest.Mock).mockResolvedValueOnce({ status: 204 });

      await initializersApi.register(request);
      await initializersApi.unregister("custom/name");

      expect(apiClient.post).toHaveBeenCalledWith("/initializers", request);
      expect(apiClient.delete).toHaveBeenCalledWith("/initializers/custom%2Fname");
    });
  });

  describe("configurationApi", () => {
    it("should read configuration content", async () => {
      const response = {
        data: {
          content: "operator: alice\n",
          source: "C:/Users/test/.pyrit/config.yaml",
          version: "config-v1",
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(response);

      await expect(configurationApi.getContent()).resolves.toEqual(response.data);
      expect(apiClient.get).toHaveBeenCalledWith("/config");
    });

    it("should update configuration content", async () => {
      const request = { content: "operator: bob\n", version: "config-v1" };
      const response = {
        data: { ...request, source: "https://account.blob.core.windows.net/config/config.yaml" },
      };
      (apiClient.put as jest.Mock).mockResolvedValueOnce(response);

      await expect(configurationApi.updateContent(request)).resolves.toEqual(response.data);
      expect(apiClient.put).toHaveBeenCalledWith("/config", request);
    });

    it("should list environment files", async () => {
      const response = { data: { items: [] } };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(response);

      await expect(configurationApi.listEnvironmentFiles()).resolves.toEqual(response.data);
      expect(apiClient.get).toHaveBeenCalledWith("/config/env-files");
    });

    it("should get an environment file", async () => {
      const response = { data: { id: "akv:0", content: "KEY=value\n" } };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(response);

      await expect(configurationApi.getEnvironmentFile("akv:0")).resolves.toEqual(response.data);
      expect(apiClient.get).toHaveBeenCalledWith("/config/env-files/akv%3A0");
    });

    it("should update an environment file", async () => {
      const request = { content: "KEY=value\n", version: "version-1" };
      const response = {
        data: {
          id: "akv:0",
          name: "AKV: bootstrap",
          path: "https://vault.vault.azure.net/secrets/bootstrap",
          exists: true,
          ...request,
        },
      };
      (apiClient.put as jest.Mock).mockResolvedValueOnce(response);

      await expect(configurationApi.updateEnvironmentFile("akv:0", request)).resolves.toEqual(response.data);
      expect(apiClient.put).toHaveBeenCalledWith("/config/env-files/akv%3A0", request);
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
              message_pieces: [
                {
                  id: "p1",
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

  describe("scenariosApi", () => {
    it("lists the scenario catalog with default params", async () => {
      const mockResponse = {
        data: {
          items: [],
          pagination: { limit: 50, has_more: false },
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      await scenariosApi.listCatalog();

      expect(apiClient.get).toHaveBeenCalledWith("/scenarios/catalog", {
        params: { limit: 50 },
      });
    });

    it("lists the scenario catalog with a custom limit and cursor", async () => {
      const mockResponse = {
        data: { items: [], pagination: { limit: 10, has_more: true, next_cursor: "next" } },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      await scenariosApi.listCatalog(10, "cursor-abc");

      expect(apiClient.get).toHaveBeenCalledWith("/scenarios/catalog", {
        params: { limit: 10, cursor: "cursor-abc" },
      });
    });

    it("can request scenario metadata without estimates", async () => {
      const mockResponse = {
        data: { items: [], pagination: { limit: 50, has_more: false } },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      await scenariosApi.listCatalog(50, undefined, false);

      expect(apiClient.get).toHaveBeenCalledWith("/scenarios/catalog", {
        params: { limit: 50, include_estimates: false },
      });
    });

    it("encodes a dotted scenario registry name as a single path segment", async () => {
      const mockResponse = {
        data: {
          scenario_name: "foundry.red_team_agent",
          scenario_type: "RedTeamAgentScenario",
          description: "desc",
          default_technique: "prompt_injection",
          aggregate_techniques: [],
          all_techniques: ["prompt_injection"],
          technique_summaries: [],
          default_datasets: [],
          baseline_policy: "enabled",
          include_baseline_by_default: true,
          supported_parameters: [],
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await scenariosApi.getScenario("foundry.red_team_agent");

      expect(apiClient.get).toHaveBeenCalledWith(
        "/scenarios/catalog/foundry.red_team_agent"
      );
      expect(result.scenario_name).toBe("foundry.red_team_agent");
    });

    it("encodes a slash-bearing scenario registry name as a single %2F-escaped segment", async () => {
      const mockResponse = { data: { scenario_name: "foundry/red_team_agent" } };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      await scenariosApi.getScenario("foundry/red_team_agent");

      expect(apiClient.get).toHaveBeenCalledWith(
        "/scenarios/catalog/foundry%2Fred_team_agent"
      );
    });

    it("posts the exact estimate request and forwards cancellation", async () => {
      const mockResponse = {
        data: {
          estimated_attack_count: 8,
          components: [],
          datasets: [],
          note: null,
        },
      };
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);
      const controller = new AbortController();
      const request = {
        target_name: "my-target",
        techniques: ["prompt_sending"],
        dataset_names: ["harmbench"],
        max_dataset_size: 4,
        dataset_filters: { harm_categories: ["violence"] },
        include_baseline: false,
        scenario_params: { num_jailbreaks: 2, num_attempts_per_template: 1 },
      };

      const result = await scenariosApi.estimateRun(
        "airt.jailbreak",
        request,
        controller.signal
      );

      expect(apiClient.post).toHaveBeenCalledWith(
        "/scenarios/catalog/airt.jailbreak/estimate",
        request,
        { signal: controller.signal }
      );
      expect(result.estimated_attack_count).toBe(8);
    });

    it("posts the exact RunScenarioRequest payload to start a run", async () => {
      const mockResponse = {
        data: {
          scenario_result_id: "sr-1",
          scenario_name: "foundry.red_team_agent",
          scenario_version: 0,
          status: "CREATED",
          created_at: "2026-02-15T00:00:00Z",
          updated_at: "2026-02-15T00:00:00Z",
          techniques_used: [],
          total_attacks: 0,
          completed_attacks: 0,
          objective_achieved_rate: 0,
          failed_attacks: [],
          attack_retries: [],
          total_retries: 0,
          labels: {},
        },
      };
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);

      const request = {
        scenario_name: "foundry.red_team_agent",
        target_name: "my-target",
        techniques: ["prompt_injection"],
        max_concurrency: 10,
        max_retries: 0,
        include_baseline: true,
        labels: { operator: "roakey" },
      };
      const result = await scenariosApi.startRun(request);

      expect(apiClient.post).toHaveBeenCalledWith("/scenarios/runs", request);
      expect(result.scenario_result_id).toBe("sr-1");
    });

    it("gets a scenario run by id", async () => {
      const mockResponse = {
        data: {
          scenario_result_id: "sr-1",
          scenario_name: "foundry.red_team_agent",
          scenario_version: 0,
          status: "IN_PROGRESS",
          created_at: "2026-02-15T00:00:00Z",
          updated_at: "2026-02-15T00:00:00Z",
          techniques_used: [],
          total_attacks: 0,
          completed_attacks: 0,
          objective_achieved_rate: 0,
          failed_attacks: [],
          attack_retries: [],
          total_retries: 0,
          labels: {},
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await scenariosApi.getRun("sr-1");

      expect(apiClient.get).toHaveBeenCalledWith("/scenarios/runs/sr-1");
      expect(result.status).toBe("IN_PROGRESS");
    });

    it("lists scenario history with repeated array query parameters", async () => {
      const mockResponse = {
        data: { items: [], pagination: { limit: 10, has_more: false } },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      await scenariosApi.listRuns({
        limit: 10,
        cursor: "history-cursor",
        scenario_names: ["first.scenario", "second.scenario"],
        run_statuses: ["IN_PROGRESS", "FAILED"],
        label: ["operator:alice", "operator:bob", "team:safety"],
      });

      expect(apiClient.get).toHaveBeenCalledWith("/scenarios/runs", {
        params: {
          limit: 10,
          cursor: "history-cursor",
          scenario_names: ["first.scenario", "second.scenario"],
          run_statuses: ["IN_PROGRESS", "FAILED"],
          label: ["operator:alice", "operator:bob", "team:safety"],
        },
        paramsSerializer: { indexes: null },
      });
    });

    it("gets scenario run progress with since/limit query params", async () => {
      const mockResponse = {
        data: {
          run: {
            scenario_result_id: "sr-1",
            scenario_name: "foundry.red_team_agent",
            scenario_version: 0,
            status: "IN_PROGRESS",
            created_at: "2026-02-15T00:00:00Z",
          },
          results: [],
          has_more: false,
          plan_complete: false,
        },
      };
      (apiClient.get as jest.Mock).mockResolvedValueOnce(mockResponse);

      const controller = new AbortController();
      await scenariosApi.getRunProgress(
        "sr-1",
        { since: "cursor-1", limit: 50 },
        controller.signal,
      );

      expect(apiClient.get).toHaveBeenCalledWith("/scenarios/runs/sr-1/progress", {
        params: { since: "cursor-1", limit: 50 },
        signal: controller.signal,
      });
    });

    it("cancels a scenario run by id", async () => {
      const mockResponse = {
        data: {
          scenario_result_id: "sr-1",
          status: "CANCELLED",
        },
      };
      const controller = new AbortController();
      (apiClient.post as jest.Mock).mockResolvedValueOnce(mockResponse);

      const result = await scenariosApi.cancelRun("sr/1", controller.signal);

      expect(apiClient.post).toHaveBeenCalledWith(
        "/scenarios/runs/sr%2F1/cancel",
        undefined,
        { signal: controller.signal },
      );
      expect(result.status).toBe("CANCELLED");
    });
  });
});

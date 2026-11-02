import {
  attributeErrorToField,
  getParameterLabel,
  normalizeForMatch,
  type AttributionCandidate,
} from "./fieldErrorAttribution";

const HTTP_REQUEST: AttributionCandidate = {
  key: "http_request",
  names: ["http_request", "Http Request"],
};
const USE_TLS: AttributionCandidate = {
  key: "use_tls",
  names: ["use_tls", "Use Tls"],
};

describe("normalizeForMatch", () => {
  it("lowercases and folds underscores, hyphens and whitespace runs into one space", () => {
    expect(normalizeForMatch("HTTP_Request")).toBe("http request");
    expect(normalizeForMatch("use-tls")).toBe("use tls");
    expect(normalizeForMatch("a__b--c \t\n d")).toBe("a b c d");
    expect(normalizeForMatch("_-mixed_ -Separators-_")).toBe("mixed separators");
  });

  it("trims leading and trailing separators", () => {
    expect(normalizeForMatch("   http request  ")).toBe("http request");
  });

  it("applies NFKC normalization before matching", () => {
    // Fullwidth Latin letters fold to their ASCII equivalents under NFKC.
    expect(normalizeForMatch("ＨＴＴＰ＿Ｒｅｑｕｅｓｔ")).toBe("http request");
    // The "ﬁ" ligature decomposes to "fi".
    expect(normalizeForMatch("ﬁeld")).toBe("field");
  });

  it("returns an empty string for an empty or separator-only input", () => {
    expect(normalizeForMatch("")).toBe("");
    expect(normalizeForMatch(" _- ")).toBe("");
  });
});

describe("attributeErrorToField", () => {
  it("attributes the issue's canonical backend error to the Http Request field", () => {
    expect(attributeErrorToField("Invalid HTTP request line", [HTTP_REQUEST, USE_TLS])).toBe(
      "http_request",
    );
  });

  it("matches the raw parameter name, the label, and differently separated spellings", () => {
    expect(attributeErrorToField("http_request: field required", [HTTP_REQUEST])).toBe("http_request");
    expect(attributeErrorToField("Http Request is malformed", [HTTP_REQUEST])).toBe("http_request");
    expect(attributeErrorToField("HTTP-REQUEST could not be parsed", [HTTP_REQUEST])).toBe("http_request");
    expect(attributeErrorToField("'http_request' is invalid.", [HTTP_REQUEST])).toBe("http_request");
  });

  it("matches NFKC-equivalent text in the message", () => {
    expect(attributeErrorToField("Ｉｎｖａｌｉｄ ＨＴＴＰ ｒｅｑｕｅｓｔ line", [HTTP_REQUEST])).toBe(
      "http_request",
    );
  });

  it("returns the key once when several names of the same candidate match", () => {
    expect(
      attributeErrorToField("http_request (Http Request) is invalid", [HTTP_REQUEST]),
    ).toBe("http_request");
  });

  it("returns the key when two candidates share it", () => {
    expect(
      attributeErrorToField("Invalid HTTP request line", [
        { key: "http_request", names: ["http_request"] },
        { key: "http_request", names: ["Http Request"] },
      ]),
    ).toBe("http_request");
  });

  it("refuses to guess when two different fields are named", () => {
    expect(
      attributeErrorToField("http_request and use_tls are incompatible", [HTTP_REQUEST, USE_TLS]),
    ).toBeNull();
    expect(
      attributeErrorToField("Http Request conflicts with Use Tls", [HTTP_REQUEST, USE_TLS]),
    ).toBeNull();
  });

  it("returns null when a blocker matches, even alongside a single field match", () => {
    const blocker: AttributionCandidate = { key: null, names: ["substring"] };
    expect(attributeErrorToField("substring is required", [HTTP_REQUEST, blocker])).toBeNull();
    expect(
      attributeErrorToField("Invalid HTTP request line: substring is required", [HTTP_REQUEST, blocker]),
    ).toBeNull();
  });

  it("ignores a blocker that does not match", () => {
    const blocker: AttributionCandidate = { key: null, names: ["substring"] };
    expect(attributeErrorToField("Invalid HTTP request line", [HTTP_REQUEST, blocker])).toBe(
      "http_request",
    );
  });

  it("requires a word boundary on both sides of the name", () => {
    const url: AttributionCandidate = { key: "url", names: ["url"] };
    expect(attributeErrorToField("Unexpected curly brace", [url])).toBeNull();
    expect(attributeErrorToField("urls are not allowed", [url])).toBeNull();
    expect(attributeErrorToField("Too many http requests", [HTTP_REQUEST])).toBeNull();
    expect(attributeErrorToField("url is required", [url])).toBe("url");
    expect(attributeErrorToField("Bad url.", [url])).toBe("url");
  });

  it("treats non-ASCII letters as word characters when finding boundaries", () => {
    // An ASCII-only \b would see a boundary between "caf" and "é".
    const caf: AttributionCandidate = { key: "caf", names: ["caf"] };
    expect(attributeErrorToField("café is closed", [caf])).toBeNull();

    const groesse: AttributionCandidate = { key: "groesse", names: ["größe"] };
    expect(attributeErrorToField("größe must be positive", [groesse])).toBe("groesse");
    expect(attributeErrorToField("übergrößen must be positive", [groesse])).toBeNull();
  });

  it("matches names literally rather than as regular expressions", () => {
    const dotted: AttributionCandidate = { key: "dotted", names: ["x.y"] };
    expect(attributeErrorToField("xzy failed", [dotted])).toBeNull();
    expect(attributeErrorToField("x.y failed", [dotted])).toBe("dotted");

    const modelName: AttributionCandidate = {
      key: "modelName",
      names: ["model_name", "Model / Deployment Name"],
    };
    expect(attributeErrorToField("Model / Deployment Name is not available", [modelName])).toBe(
      "modelName",
    );
  });

  it("ignores names shorter than three characters after normalization", () => {
    const id: AttributionCandidate = { key: "id", names: ["id", "_i_"] };
    expect(attributeErrorToField("id is invalid", [id])).toBeNull();
    // A short name that is ignored does not make a real match ambiguous.
    expect(attributeErrorToField("id for HTTP request is invalid", [id, HTTP_REQUEST])).toBe(
      "http_request",
    );
  });

  describe("raw names do not match inside longer snake_case names (loop 2, N3)", () => {
    const ENDPOINT: AttributionCandidate = { key: "endpoint", names: ["endpoint", "Endpoint"] };
    const MODEL_NAME: AttributionCandidate = {
      key: "modelName",
      names: ["model_name", "Model / Deployment Name"],
    };
    const UNDERLYING_MODEL: AttributionCandidate = {
      key: "underlyingModel",
      names: ["underlying_model_name", "Underlying Model"],
    };

    it("does not match endpoint inside azure_endpoint", () => {
      expect(attributeErrorToField("Unknown parameter 'azure_endpoint'", [ENDPOINT])).toBeNull();
    });

    it("does not match model_name inside underlying_model_name", () => {
      expect(attributeErrorToField("underlying_model_name must be set", [MODEL_NAME])).toBeNull();
    });

    it("attributes underlying_model_name to its own field, not also to model_name", () => {
      expect(
        attributeErrorToField("underlying_model_name must be set", [MODEL_NAME, UNDERLYING_MODEL]),
      ).toBe("underlyingModel");
    });

    it("still matches a raw name standing on its own", () => {
      expect(attributeErrorToField("http_request must not be empty", [HTTP_REQUEST, USE_TLS])).toBe(
        "http_request",
      );
      expect(attributeErrorToField("Invalid HTTP request line", [HTTP_REQUEST, USE_TLS])).toBe(
        "http_request",
      );
      expect(attributeErrorToField("endpoint: invalid URL", [ENDPOINT])).toBe("endpoint");
    });
  });

  it("returns null for an empty message or no candidates", () => {
    expect(attributeErrorToField("", [HTTP_REQUEST])).toBeNull();
    expect(attributeErrorToField("   ", [HTTP_REQUEST])).toBeNull();
    expect(attributeErrorToField("Invalid HTTP request line", [])).toBeNull();
  });

  it("returns null when nothing matches or a candidate has no names", () => {
    expect(attributeErrorToField("Server exploded", [HTTP_REQUEST, USE_TLS])).toBeNull();
    expect(attributeErrorToField("Invalid HTTP request line", [{ key: "empty", names: [] }])).toBeNull();
  });
});

describe("getParameterLabel", () => {
  it("splits on underscores and capitalises each word", () => {
    expect(getParameterLabel("http_request")).toBe("Http Request");
    expect(getParameterLabel("use_tls")).toBe("Use Tls");
    expect(getParameterLabel("extra_body_parameters")).toBe("Extra Body Parameters");
  });

  it("upper-cases single-letter words and single-word names", () => {
    expect(getParameterLabel("top_p")).toBe("Top P");
    expect(getParameterLabel("x")).toBe("X");
    expect(getParameterLabel("endpoint")).toBe("Endpoint");
  });

  it("leaves the rest of each word's casing unchanged", () => {
    expect(getParameterLabel("api_URL")).toBe("Api URL");
    expect(getParameterLabel("httpx_client_kwargs")).toBe("Httpx Client Kwargs");
  });
});

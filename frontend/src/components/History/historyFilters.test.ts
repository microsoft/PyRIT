/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import {
  DEFAULT_HISTORY_FILTERS,
  filtersFromSearchParams,
  filtersToSearchParams,
} from "./historyFilters";
import type { HistoryFilters } from "./historyFilters";

describe("historyFilters URL encoding", () => {
  it("decodes empty params to the default filters", () => {
    expect(filtersFromSearchParams(new URLSearchParams())).toEqual(DEFAULT_HISTORY_FILTERS);
  });

  it("encodes default filters to an empty query string", () => {
    expect(filtersToSearchParams(DEFAULT_HISTORY_FILTERS).toString()).toBe("");
  });

  it("round-trips a fully populated filter set", () => {
    const filters: HistoryFilters = {
      outcome: "success",
      includeScenarioAttacks: true,
      operator: ["roakey"],
      operationId: ["6f1b2c3d-0000-4000-8000-000000000001"],
      otherLabels: ["env:prod", "team:red"],
      labelSearchText: "prod",
    };
    const decoded = filtersFromSearchParams(filtersToSearchParams(filters));
    expect(decoded).toEqual(filters);
  });

  it.each([
    "attackType=PromptSendingAttack&attackType=CrescendoAttack",
    "converter=Base64Converter&converter=ROT13Converter",
    "converterMatch=all",
    "hasConverters=false",
  ])("ignores removed filters in older URLs: %s", (legacyQuery: string) => {
    const filters = filtersFromSearchParams(new URLSearchParams(`outcome=success&${legacyQuery}`));

    expect(filters).toEqual({ ...DEFAULT_HISTORY_FILTERS, outcome: "success" });
    expect(filtersToSearchParams(filters).toString()).toBe("outcome=success");
  });

  it("only stores explicit scanner attack exclusion", () => {
    const excluded = filtersToSearchParams({
      ...DEFAULT_HISTORY_FILTERS,
      includeScenarioAttacks: false,
    });

    expect(excluded.get("includeScannerAttacks")).toBe("false");
    expect(filtersFromSearchParams(excluded).includeScenarioAttacks).toBe(false);
    expect(filtersToSearchParams(DEFAULT_HISTORY_FILTERS).has("includeScannerAttacks")).toBe(false);
  });

  it("repeats multi-value keys for list filters", () => {
    const params = filtersToSearchParams({
      ...DEFAULT_HISTORY_FILTERS,
      operator: ["alice", "bob"],
    });
    expect(params.getAll("operator")).toEqual(["alice", "bob"]);
  });
});

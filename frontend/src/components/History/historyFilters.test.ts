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
      attackTypes: ["PromptSendingAttack", "CrescendoAttack"],
      outcome: "success",
      converter: ["Base64Converter", "ROT13Converter"],
      converterMatchMode: "all",
      hasConverters: true,
      operator: ["roakey"],
      operation: ["op_trash_panda"],
      otherLabels: ["env:prod", "team:red"],
      labelSearchText: "prod",
    };
    const decoded = filtersFromSearchParams(filtersToSearchParams(filters));
    expect(decoded).toEqual(filters);
  });

  it("omits the converter match mode when it is the default 'any'", () => {
    const params = filtersToSearchParams({
      ...DEFAULT_HISTORY_FILTERS,
      converter: ["Base64Converter"],
      converterMatchMode: "any",
    });
    expect(params.has("converterMatch")).toBe(false);
  });

  it("preserves hasConverters=false distinctly from undefined", () => {
    const explicitFalse = filtersToSearchParams({
      ...DEFAULT_HISTORY_FILTERS,
      hasConverters: false,
    });
    expect(explicitFalse.get("hasConverters")).toBe("false");
    expect(filtersFromSearchParams(explicitFalse).hasConverters).toBe(false);

    const unset = filtersToSearchParams(DEFAULT_HISTORY_FILTERS);
    expect(unset.has("hasConverters")).toBe(false);
    expect(filtersFromSearchParams(unset).hasConverters).toBeUndefined();
  });

  it("repeats multi-value keys for list filters", () => {
    const params = filtersToSearchParams({
      ...DEFAULT_HISTORY_FILTERS,
      attackTypes: ["A", "B"],
    });
    expect(params.getAll("attackType")).toEqual(["A", "B"]);
  });
});

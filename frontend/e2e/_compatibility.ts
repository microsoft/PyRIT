import { readFileSync } from "node:fs";

import { isCompatibilityId } from "../src/utils/compatibilityId";

function readStamp() {
  const stamp = JSON.parse(
    readFileSync(new URL("../../pyrit/_compatibility.json", import.meta.url), "utf8"),
  );
  if (
    !stamp ||
    !isCompatibilityId(stamp.compatibility_id) ||
    typeof stamp.version !== "string" ||
    stamp.compatibility_id !== `${stamp.version}+g${stamp.commit}` ||
    typeof stamp.dirty !== "boolean"
  ) {
    throw new Error("E2E tests require a valid packaged pyrit/_compatibility.json stamp.");
  }
  return stamp;
}

export function getCompatibilityId(): string {
  return readStamp().compatibility_id;
}

export function compatibilityHeaders(): Record<string, string> {
  return { "PyRIT-Compatibility-ID": getCompatibilityId() };
}

export function mockVersion(overrides: Record<string, unknown> = {}) {
  const stamp = readStamp();
  return {
    display: stamp.version,
    ...overrides,
    version: stamp.version,
    compatibility_id: stamp.compatibility_id,
  };
}

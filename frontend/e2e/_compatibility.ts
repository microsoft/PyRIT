import { readFileSync } from "node:fs";

export function isValidCompatibilityId(value: unknown): value is string {
  return typeof value === "string" && value.length <= 256 && value.trim() === value &&
    /^[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?\+g[0-9a-f]{40}$/.test(value);
}

function readStamp() {
  const stamp = JSON.parse(
    readFileSync(new URL("../../pyrit/_compatibility.json", import.meta.url), "utf8"),
  );
  if (
    !stamp ||
    !isValidCompatibilityId(stamp.compatibility_id) ||
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

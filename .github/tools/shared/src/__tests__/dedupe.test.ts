import { describe, it, expect } from "vitest";
import {
  fingerprint,
  fingerprintId,
  fingerprintComment,
  fingerprintSearchToken,
  extractFingerprint,
} from "../dedupe.js";

describe("fingerprint", () => {
  it("returns a 12-char hex string", () => {
    const fp = fingerprint(["ci-failure", "pr-validation"]);
    expect(fp).toMatch(/^[0-9a-f]{12}$/);
  });

  it("is deterministic", () => {
    expect(fingerprint(["a", "b"])).toBe(fingerprint(["a", "b"]));
  });

  it("differs for different inputs", () => {
    expect(fingerprint(["a"])).not.toBe(fingerprint(["b"]));
  });
});

describe("fingerprintComment / extractFingerprint", () => {
  it("round-trips", () => {
    const comment = fingerprintComment("ci-failure-pr-validation");
    expect(extractFingerprint(comment)).toBe("ci-failure-pr-validation");
  });

  it("extracts from a larger string", () => {
    const text = `Some issue body\n\n<!-- fingerprint:alert-123 -->\n\nMore text`;
    expect(extractFingerprint(text)).toBe("alert-123");
  });

  it("returns null when absent", () => {
    expect(extractFingerprint("no fingerprint here")).toBeNull();
  });
});

describe("fingerprintId / fingerprintSearchToken", () => {
  it("builds a stable prefixed fingerprint id", () => {
    const id = fingerprintId("cluster", ["<DEV_NAMESPACE>", "deployment/rental-app", "CrashLoopBackOff"]);
    expect(id).toMatch(/^cluster-[0-9a-f]{12}$/);
    expect(id).toBe(fingerprintId("cluster", ["<DEV_NAMESPACE>", "deployment/rental-app", "CrashLoopBackOff"]));
  });

  it("builds a search token for issue body scans", () => {
    expect(fingerprintSearchToken("cluster-a1b2c3d4e5f6")).toBe("fingerprint:cluster-a1b2c3d4e5f6");
  });
});

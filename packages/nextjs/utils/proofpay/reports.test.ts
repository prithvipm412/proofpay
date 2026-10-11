import { Check, Report, currentReport, duplicateOf, jobMessage, parseReports } from "./reports";
import { describe, expect, it } from "vitest";

const deployment = "10143:0x123";
const proof = "0x" + "ab".repeat(32);
const report: Report = {
  deploymentId: deployment,
  taskId: 2,
  attempt: 1,
  proofHash: proof,
  jobState: "confirmed",
  attemptResult: "approved",
  evaluation: null,
  settlement: { state: "confirmed", txHashes: [], error: null, conflict: false },
};
describe("F-16 report evidence binding", () => {
  it("requires both the current attempt and committed photo", () => {
    expect(currentReport([report], 1, proof)).toBe(report);
    expect(currentReport([report], 2, proof)).toBeUndefined();
    expect(currentReport([report], 1, "0x" + "cd".repeat(32))).toBeUndefined();
  });
  it("retains earlier attempts and orders them", () => {
    expect(parseReports([{ ...report, attempt: 2 }, report], 2n, deployment).map(r => r.attempt)).toEqual([1, 2]);
  });
  it.each([
    { taskId: 3 },
    { deploymentId: "143:0x123" },
    { attempt: 4 },
    { proofHash: "wrong" },
    { evaluation: {} },
    { jobState: "made_up" },
  ])("rejects malformed or mismatched reports: %o", change => {
    expect(() => parseReports([{ ...report, ...change }], 2n, deployment)).toThrow();
  });
  it("rejects duplicate attempts", () => expect(() => parseReports([report, report], 2n, deployment)).toThrow());
  it("accepts restored chain evidence without a local job or evaluation", () => {
    expect(
      parseReports(
        [{ ...report, jobState: null, settlement: { ...report.settlement, state: "none" } }],
        2n,
        deployment,
      )[0].attemptResult,
    ).toBe("approved");
  });
  it("F-15 covers every worker state without claiming payment", () => {
    const states = [
      undefined,
      "queued",
      "awaiting_files",
      "evaluating",
      "evaluated",
      "signed",
      "broadcast",
      "confirmed",
      "model_retry",
      "blocked_config",
      "blocked_nonce",
      "superseded",
      "reverted",
      "model_failed",
      "expired",
      "not_admitted",
    ];
    for (const state of states) {
      expect(jobMessage(state, 400n)).not.toMatch(/unknown|payment released|transaction|gas|hash/i);
    }
  });
});

describe("Demo step 8 duplicate evidence (V-C2, V-C3)", () => {
  const withChecks = (checks: Check[]): Report =>
    ({
      ...report,
      attemptResult: "rejected",
      evaluation: { pass: false, score: 0, reason: "x", model: "m", configVersion: "0x1", checks },
    }) as Report;
  it("names the task of an exact match", () => {
    const r = withChecks([
      { id: "V-C1", name: "Integrity", result: "pass", detail: "" },
      { id: "V-C2", name: "Exact reuse", result: "fail", detail: "Same file as the accepted proof of task #12" },
    ]);
    expect(duplicateOf(r)).toEqual({ kind: "exact", taskId: 12 });
  });
  it("names the task of a near match", () => {
    const r = withChecks([
      { id: "V-C2", name: "Exact reuse", result: "pass", detail: "" },
      { id: "V-C3", name: "Near reuse", result: "fail", detail: "distance 2 to the accepted proof of task #3" },
    ]);
    expect(duplicateOf(r)).toEqual({ kind: "near", taskId: 3 });
  });
  it("ignores warnings, soft checks and missing evaluations", () => {
    expect(duplicateOf(report)).toBeNull();
    expect(
      duplicateOf(
        withChecks([
          { id: "V-C3", name: "Near reuse", result: "warn", detail: "distance 8 to the accepted proof of task #3" },
          { id: "V-C4", name: "Seen elsewhere", result: "warn", detail: "Same file as the before photo of task #4" },
        ]),
      ),
    ).toBeNull();
    expect(
      duplicateOf(
        withChecks([{ id: "V-C3", name: "Near reuse", result: "fail", detail: "Near reuse check could not complete" }]),
      ),
    ).toBeNull();
  });
});

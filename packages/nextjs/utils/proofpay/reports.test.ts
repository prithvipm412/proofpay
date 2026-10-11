import { Report, currentReport, jobMessage, parseReports } from "./reports";
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

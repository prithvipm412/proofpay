import { TaskStatus as S } from "./escrow";
import type { Report } from "./reports";
import { isFinal, pollDelay, refundNote, reportPollDelay, taskActions } from "./taskActions";
import { describe, expect, it } from "vitest";
import type { Task } from "~~/hooks/proofpay/useTasks";

const poster = "0x1111111111111111111111111111111111111111";
const worker = "0x2222222222222222222222222222222222222222";
const other = "0x3333333333333333333333333333333333333333";
const task = (status: S, attempts = 1): Task => ({
  poster,
  worker,
  status,
  attempts,
  submitBy: 100n,
  reviewBy: 400n,
  disputeUntil: 500n,
  amount: 1000000000000000n,
  beforeHash: "0x11",
  proofHash: "0x22",
  createdAt: 0n,
  score: 0,
  title: "Clean the bench",
  description: "Remove litter",
});

describe("F-14 role and deadline boundaries", () => {
  it("Open: only others can accept, before submitBy; poster can always refund", () => {
    expect(taskActions(task(S.Open), poster, 0)).toEqual(["refund"]);
    expect(taskActions(task(S.Open), other, 99)).toEqual(["acceptTask"]);
    expect(taskActions(task(S.Open), other, 100)).toEqual([]);
    expect(taskActions(task(S.Open), poster, 100)).toEqual(["refund"]);
    expect(taskActions(task(S.Open), undefined, 0)).toEqual([]);
  });
  it("Accepted: worker proof and poster refund boundaries never overlap", () => {
    expect(taskActions(task(S.Accepted), worker, 99)).toEqual(["submitProof"]);
    expect(taskActions(task(S.Accepted), poster, 99)).toEqual([]);
    expect(taskActions(task(S.Accepted), worker, 100)).toEqual([]);
    expect(taskActions(task(S.Accepted), poster, 100)).toEqual(["refund"]);
    expect(taskActions(task(S.Accepted, 3), worker, 99)).toEqual([]);
    expect(taskActions(task(S.Accepted, 3), poster, 99)).toEqual(["refund"]);
    expect(taskActions(task(S.Accepted), other, 99)).toEqual([]);
  });
  it("Submitted: protects the review period, including third attempt", () => {
    expect(taskActions(task(S.Submitted, 3), poster, 399)).toEqual([]);
    expect(taskActions(task(S.Submitted), poster, 400)).toEqual(["refund"]);
    expect(taskActions(task(S.Submitted), worker, 400)).toEqual([]);
  });
  it("Approved: poster dispute closes exactly when release opens for everyone", () => {
    expect(taskActions(task(S.Approved), poster, 499)).toEqual(["dispute"]);
    expect(taskActions(task(S.Approved), worker, 499)).toEqual([]);
    for (const who of [poster, worker, other]) expect(taskActions(task(S.Approved), who, 500)).toEqual(["release"]);
  });
  it("Disputed: requires a known timeout and opens to everyone at the boundary", () => {
    expect(taskActions(task(S.Disputed), other, 9999)).toEqual([]);
    expect(taskActions(task(S.Disputed), other, 4099, 3600n)).toEqual([]);
    for (const who of [poster, worker, other])
      expect(taskActions(task(S.Disputed), who, 4100, 3600n)).toEqual(["expireDispute"]);
  });
  it("Final states have no task actions; unknown chain time hides actions", () => {
    for (const state of [S.Paid, S.Refunded]) {
      expect(isFinal(state)).toBe(true);
      expect(taskActions(task(state), poster, 10000)).toEqual([]);
    }
    expect(taskActions(task(S.Open), other, NaN)).toEqual([]);
  });
  it("F-17 backs off 3/6/12/24 seconds, resets after success, and stops at final", () => {
    expect([0, 1, 2, 3, 4, 5].map(n => pollDelay(n))).toEqual([3000, 3000, 6000, 12000, 24000, 24000]);
    expect(pollDelay(0, true)).toBe(false);
  });
});

describe("Demo step 9 refund note (C-21)", () => {
  it("explains an expired Open or Accepted task", () => {
    expect(refundNote(task(S.Accepted), 99, true)).toBeNull();
    expect(refundNote(task(S.Accepted), 100, true)).toBe(
      "The time to send proof has ended. You can take your MON back with Refund.",
    );
    expect(refundNote(task(S.Open, 0), 100, false)).toBe(
      "The time to send proof has ended. The poster can take the MON back.",
    );
  });
  it("explains three used attempts before submitBy", () => {
    expect(refundNote(task(S.Accepted, 3), 0, true)).toBe(
      "All 3 attempts were used without an approval. You can take your MON back with Refund.",
    );
  });
  it("explains a Submitted task only at or after reviewBy", () => {
    expect(refundNote(task(S.Submitted), 399, true)).toBeNull();
    expect(refundNote(task(S.Submitted), 400, true)).toContain("review time has ended");
  });
  it("gives no note when no refund rule applies", () => {
    for (const s of [S.Approved, S.Disputed, S.Paid, S.Refunded]) expect(refundNote(task(s), 10_000, true)).toBeNull();
    expect(refundNote(task(S.Accepted), NaN, true)).toBeNull();
  });
  it("matches the refund button rule for the poster", () => {
    for (const s of [S.Open, S.Accepted, S.Submitted])
      for (const now of [0, 99, 100, 399, 400])
        for (const attempts of [0, 1, 3]) {
          const t = task(s, attempts);
          const button = taskActions(t, poster, now).includes("refund");
          // An Open task can always be cancelled; the note only explains expired or exhausted tasks.
          if (s !== S.Open) expect(refundNote(t, now, true) !== null).toBe(button);
        }
  });
});

describe("Report polling with the ngrok request quota (F-17, M8)", () => {
  const report = (jobState: string | null, attemptResult: Report["attemptResult"] = "none"): Report[] => [
    {
      deploymentId: "10143:0x1",
      taskId: 1,
      attempt: 1,
      proofHash: "0x22",
      jobState,
      attemptResult,
      evaluation: null,
      settlement: { state: "x", txHashes: [], error: null, conflict: false },
    },
  ];
  it("polls every 3 s while Submitted, with backoff, and slowly after reviewBy", () => {
    expect(reportPollDelay(task(S.Submitted), undefined, 0, 0)).toBe(3_000);
    expect(reportPollDelay(task(S.Submitted), undefined, 3, 0)).toBe(12_000);
    expect(reportPollDelay(task(S.Submitted), undefined, 0, 400)).toBe(30_000);
  });
  it("stops once the current attempt's report is settled", () => {
    expect(reportPollDelay(task(S.Approved), report("confirmed", "approved"), 0, 0)).toBe(false);
    expect(reportPollDelay(task(S.Accepted), report("confirmed", "rejected"), 0, 0)).toBe(false);
    expect(reportPollDelay(task(S.Accepted), report("model_failed"), 0, 0)).toBe(false);
  });
  it("keeps a slow poll while the result is still being saved or no report matches", () => {
    expect(reportPollDelay(task(S.Approved), report("broadcast"), 0, 0)).toBe(15_000);
    expect(reportPollDelay(task(S.Disputed), undefined, 0, 0)).toBe(15_000);
    expect(reportPollDelay(task(S.Approved), [{ ...report("confirmed", "approved")[0], attempt: 2 }], 0, 0)).toBe(
      15_000,
    );
  });
  it("never polls without an attempt or in a final status", () => {
    expect(reportPollDelay(undefined, undefined, 0, 0)).toBe(false);
    expect(reportPollDelay(task(S.Open, 0), undefined, 0, 0)).toBe(false);
    expect(reportPollDelay(task(S.Paid), undefined, 0, 0)).toBe(false);
    expect(reportPollDelay(task(S.Refunded), report("broadcast"), 0, 0)).toBe(false);
  });
});

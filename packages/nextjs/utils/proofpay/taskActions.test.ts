import { TaskStatus as S } from "./escrow";
import { isFinal, pollDelay, taskActions } from "./taskActions";
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

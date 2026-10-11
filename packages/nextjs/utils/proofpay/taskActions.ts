import { TaskStatus } from "./escrow";
import type { Task } from "~~/hooks/proofpay/useTasks";

export type TaskAction = "acceptTask" | "submitProof" | "refund" | "dispute" | "release" | "expireDispute";
export const ACTION_LABEL: Record<TaskAction, string> = {
  acceptTask: "Accept task",
  submitProof: "Submit proof",
  refund: "Refund",
  dispute: "Dispute",
  release: "Release payment",
  expireDispute: "End dispute",
};
export const isFinal = (status: number) => status === TaskStatus.Paid || status === TaskStatus.Refunded;

/** F-14: exact contract boundaries. The contract rechecks these when the call executes. */
export function taskActions(
  task: Task,
  address: string | undefined,
  now: number,
  arbitrationTimeout?: bigint,
): TaskAction[] {
  if (!address || !Number.isFinite(now)) return [];
  const poster = address.toLowerCase() === task.poster.toLowerCase();
  const worker = address.toLowerCase() === task.worker.toLowerCase();
  switch (task.status) {
    case TaskStatus.Open:
      return poster ? ["refund"] : now < Number(task.submitBy) ? ["acceptTask"] : [];
    case TaskStatus.Accepted:
      if (poster && (now >= Number(task.submitBy) || task.attempts === 3)) return ["refund"];
      return worker && now < Number(task.submitBy) && task.attempts < 3 ? ["submitProof"] : [];
    case TaskStatus.Submitted:
      return poster && now >= Number(task.reviewBy) ? ["refund"] : [];
    case TaskStatus.Approved:
      return now >= Number(task.disputeUntil) ? ["release"] : poster ? ["dispute"] : [];
    case TaskStatus.Disputed:
      return arbitrationTimeout !== undefined && now >= Number(task.disputeUntil + arbitrationTimeout)
        ? ["expireDispute"]
        : [];
    default:
      return [];
  }
}

/** F-17: repeated failures back off; successful reads reset the streak. */
export function pollDelay(failures: number, final = false): number | false {
  return final ? false : Math.min(24_000, 3_000 * 2 ** Math.max(0, failures - 1));
}

/** Demo step 9 (C-21): why a refund is available now, in plain words. Null when no refund is open. */
export function refundNote(task: Task, now: number, isPoster: boolean): string | null {
  if (!Number.isFinite(now)) return null;
  const reason =
    task.status === TaskStatus.Accepted && task.attempts === 3
      ? "All 3 attempts were used without an approval."
      : (task.status === TaskStatus.Open || task.status === TaskStatus.Accepted) && now >= Number(task.submitBy)
        ? "The time to send proof has ended."
        : task.status === TaskStatus.Submitted && now >= Number(task.reviewBy)
          ? "The review time has ended without a recorded result."
          : null;
  if (!reason) return null;
  return isPoster ? `${reason} You can take your MON back with Refund.` : `${reason} The poster can take the MON back.`;
}

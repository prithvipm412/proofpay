import { TaskStatus } from "./escrow";
import { Report, SETTLED_JOB_STATES, currentReport } from "./reports";
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

/**
 * F-17 with the ngrok monthly request quota (M8): reports change only while a check can still run.
 * Submitted: every 3 s (backoff on errors), every 30 s after reviewBy. Other open statuses: every 15 s
 * until the current attempt's report is settled, then no more reads. Final statuses: none.
 * The caller also refetches when the task status or attempt changes.
 */
export function reportPollDelay(
  task: Pick<Task, "status" | "attempts" | "proofHash" | "reviewBy"> | undefined,
  reports: Report[] | undefined,
  failures: number,
  now: number,
): number | false {
  if (!task || task.attempts === 0 || isFinal(task.status)) return false;
  const delay = pollDelay(failures) as number;
  if (task.status === TaskStatus.Submitted) return now >= Number(task.reviewBy) ? Math.max(30_000, delay) : delay;
  const current = reports && currentReport(reports, task.attempts, task.proofHash);
  if (current && (current.attemptResult !== "none" || SETTLED_JOB_STATES.includes(current.jobState ?? "")))
    return false;
  return Math.max(15_000, delay);
}

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

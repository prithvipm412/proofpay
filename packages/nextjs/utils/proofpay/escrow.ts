import { parseEther } from "viem";
import deployedContracts from "~~/contracts/deployedContracts";

/** ABI of ProofPayEscrow, written by the template deploy flow (section 9.7). */
export const escrowAbi = deployedContracts[10143].ProofPayEscrow.abi;

/** Same order as `enum Status` in the contract (section 9.2). */
export enum TaskStatus {
  Open = 0,
  Accepted = 1,
  Submitted = 2,
  Approved = 3,
  Disputed = 4,
  Paid = 5,
  Refunded = 6,
}

export const STATUS_LABEL: Record<TaskStatus, string> = {
  [TaskStatus.Open]: "Open",
  [TaskStatus.Accepted]: "Accepted",
  [TaskStatus.Submitted]: "Proof sent",
  [TaskStatus.Approved]: "Approved",
  [TaskStatus.Disputed]: "Disputed",
  [TaskStatus.Paid]: "Paid",
  [TaskStatus.Refunded]: "Refunded",
};

/** C-11 constants. */
export const MIN_AMOUNT_WEI = parseEther("0.001");
export const MAX_TITLE_BYTES = 80;
export const MAX_DESCRIPTION_BYTES = 500;
export const MIN_SUBMIT_DELAY_S = 60;
export const MAX_SUBMIT_DELAY_S = 30 * 24 * 3600;

/** IDs from newest to oldest: count, count - 1, ... (at most `window` IDs, never 0). */
export function newestIds(count: bigint, window: bigint): bigint[] {
  const ids: bigint[] = [];
  for (let id = count; id >= 1n && id > count - window; id--) ids.push(id);
  return ids;
}

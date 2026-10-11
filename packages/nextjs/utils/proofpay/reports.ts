import { settings } from "./settings";
import { verifierFetch } from "./verifier";

export type Check = { id: string; name: string; result: "pass" | "warn" | "fail" | "skipped"; detail: string };
export type Report = {
  deploymentId: string;
  taskId: number;
  attempt: number;
  proofHash: string;
  jobState: string | null;
  attemptResult: "approved" | "rejected" | "none";
  evaluation: null | {
    pass: boolean;
    score: number;
    reason: string;
    model: string;
    configVersion: string;
    checks: Check[];
  };
  settlement: { state: string; txHashes: string[]; error: string | null; conflict: boolean };
};
/** Job states that do not change any more (V-J terminal states). */
export const SETTLED_JOB_STATES = ["confirmed", "reverted", "superseded", "expired", "model_failed", "not_admitted"];
const states = [
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
const hash = (value: unknown): value is string => typeof value === "string" && /^0x[0-9a-f]{64}$/i.test(value);
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object";

/** V-22/F-16: reject malformed/cross-deployment reports instead of presenting them as evidence. */
export function parseReports(value: unknown, taskId: bigint, deploymentId: string): Report[] {
  if (!Array.isArray(value)) throw new Error("The checking service returned an unreadable report.");
  const seen = new Set<number>();
  for (const r of value) {
    if (
      !object(r) ||
      r.deploymentId !== deploymentId ||
      !Number.isSafeInteger(r.taskId) ||
      BigInt(r.taskId as number) !== taskId ||
      !Number.isInteger(r.attempt) ||
      (r.attempt as number) < 1 ||
      (r.attempt as number) > 3 ||
      seen.has(r.attempt as number) ||
      !hash(r.proofHash) ||
      (r.jobState !== null && !states.includes(r.jobState as string)) ||
      !["approved", "rejected", "none"].includes(r.attemptResult as string) ||
      !object(r.settlement) ||
      typeof r.settlement.state !== "string" ||
      typeof r.settlement.conflict !== "boolean" ||
      !Array.isArray(r.settlement.txHashes) ||
      !r.settlement.txHashes.every(hash)
    ) {
      throw new Error("The checking service returned a report for different or invalid evidence.");
    }
    if (r.evaluation !== null) {
      const e = r.evaluation;
      if (
        !object(e) ||
        typeof e.pass !== "boolean" ||
        !Number.isInteger(e.score) ||
        (e.score as number) < 0 ||
        (e.score as number) > 100 ||
        typeof e.reason !== "string" ||
        typeof e.model !== "string" ||
        typeof e.configVersion !== "string" ||
        !Array.isArray(e.checks) ||
        !e.checks.every(
          c =>
            object(c) &&
            typeof c.id === "string" &&
            typeof c.name === "string" &&
            typeof c.detail === "string" &&
            ["pass", "warn", "fail", "skipped"].includes(c.result as string),
        )
      ) {
        throw new Error("The checking service returned an unreadable evaluation.");
      }
    }
    seen.add(r.attempt as number);
  }
  return (value as Report[]).sort((a, b) => a.attempt - b.attempt);
}

export function currentReport(reports: Report[], attempt: number, proofHash: string) {
  return reports.find(r => r.attempt === attempt && r.proofHash.toLowerCase() === proofHash.toLowerCase());
}

export function jobMessage(state: string | null | undefined, reviewBy: bigint): string {
  const deadline = new Date(Number(reviewBy) * 1000).toLocaleString();
  switch (state) {
    case null:
    case undefined:
      return "Waiting for the check to start";
    case "queued":
      return "In line for checking";
    case "awaiting_files":
      return "Waiting for the photo file";
    case "evaluating":
      return "Checking the photos";
    case "evaluated":
    case "signed":
    case "broadcast":
      return "Result ready, saving it";
    case "confirmed":
      return "Result saved";
    case "model_retry":
      return "Delayed — trying again";
    case "blocked_config":
    case "blocked_nonce":
      return "Checking is paused by the operator";
    case "superseded":
      return "This check is out of date — see the newer attempt";
    case "reverted":
      return "The result could not be saved";
    case "model_failed":
    case "expired":
      return `Could not check — the poster can refund after ${deadline}`;
    case "not_admitted":
      return `Not checked: demo capacity reached — the poster can refund after ${deadline}`;
    default:
      return "The checking service returned an unknown state";
  }
}

export async function fetchReports(id: bigint, signal?: AbortSignal): Promise<Report[]> {
  const response = await verifierFetch(`${settings.verifierUrl}/tasks/${id}/attempts`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Reports are temporarily unavailable. Checking can continue in the background.");
  return parseReports(await response.json(), id, `${settings.chainId}:${settings.escrowAddress.toLowerCase()}`);
}

export async function requestVerification(id: bigint): Promise<void> {
  const response = await verifierFetch(`${settings.verifierUrl}/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ taskId: Number(id) }),
  });
  if (!response.ok)
    throw new Error("The check could not be nudged. Your proof is saved; checking will start automatically.");
}

export type DuplicateMatch = { kind: "exact" | "near"; taskId: number };

/** Demo step 8 (V-C2, V-C3, V-C9): the task whose accepted proof matched this photo, IF a reuse check failed. */
export function duplicateOf(report: Report): DuplicateMatch | null {
  for (const check of report.evaluation?.checks ?? []) {
    if (check.result !== "fail" || (check.id !== "V-C2" && check.id !== "V-C3")) continue;
    const match = /accepted proof of task #(\d{1,15})\b/.exec(check.detail);
    if (match) return { kind: check.id === "V-C2" ? "exact" : "near", taskId: Number(match[1]) };
  }
  return null;
}

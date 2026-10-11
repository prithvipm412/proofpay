"use client";

import type { ContractFunctionReturnType } from "viem";
import { useReadContract, useReadContracts } from "wagmi";
import { monadTestnet } from "~~/utils/proofpay/chain";
import { escrowAbi, newestIds } from "~~/utils/proofpay/escrow";
import { settings } from "~~/utils/proofpay/settings";

export type Task = ContractFunctionReturnType<typeof escrowAbi, "view", "getTask">;

/** The board reads the newest tasks only. Older tasks are past their time limit in the demo. */
export const BOARD_WINDOW = 100n;

const escrow = { address: settings.escrowAddress, abi: escrowAbi, chainId: monadTestnet.id } as const;

export function useTaskCount() {
  return useReadContract({ ...escrow, functionName: "taskCount", query: { refetchInterval: 10_000 } });
}

/** Reads the newest tasks in one multicall. */
export function useRecentTasks(window = BOARD_WINDOW) {
  const { data: count, error: countError } = useTaskCount();
  const ids = count ? newestIds(count, window) : [];
  const { data, error, isLoading } = useReadContracts({
    contracts: ids.map(id => ({ ...escrow, functionName: "getTask", args: [id] }) as const),
    query: { enabled: ids.length > 0, refetchInterval: 10_000 },
  });
  const tasks = (data ?? []).flatMap((r, i) =>
    r.status === "success" ? [{ id: ids[i], task: r.result as unknown as Task }] : [],
  );
  const partialFailure = data?.some(r => r.status === "failure");
  return {
    tasks,
    count,
    loading: count === undefined || (ids.length > 0 && isLoading),
    error: countError ?? error ?? (partialFailure ? new Error("Some tasks could not be read.") : null),
  };
}

export function useTask(id: bigint | null, refetchInterval: number | false = false) {
  return useReadContract({
    ...escrow,
    functionName: "getTask",
    args: id ? [id] : undefined,
    query: { enabled: !!id, refetchInterval },
  });
}

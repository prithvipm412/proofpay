"use client";

import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { usePublicClient, useReadContract } from "wagmi";
import { monadTestnet } from "~~/utils/proofpay/chain";
import { escrowAbi } from "~~/utils/proofpay/escrow";
import { fetchReports } from "~~/utils/proofpay/reports";
import { settings } from "~~/utils/proofpay/settings";
import { isFinal, pollDelay, reportPollDelay } from "~~/utils/proofpay/taskActions";

/** F-17: independent reads keep refund/release available if the report service is down. */
export function useTaskDetail(id: bigint | null) {
  const client = usePublicClient({ chainId: monadTestnet.id });
  // Error streaks for the F-17 backoff; a new task ID starts a new streak.
  const streak = useRef({ id, task: 0, reports: 0 });
  if (streak.current.id !== id) streak.current = { id, task: 0, reports: 0 };
  const failures = streak.current;
  const taskQuery = useQuery({
    queryKey: ["proofpay-task", settings.escrowAddress, id?.toString()],
    enabled: !!client && id !== null,
    retry: false,
    queryFn: async () => {
      try {
        if (!client || id === null) throw new Error("Task is unavailable.");
        const block = await client.getBlock();
        const task = await client.readContract({
          address: settings.escrowAddress,
          abi: escrowAbi,
          functionName: "getTask",
          args: [id],
          blockNumber: block.number,
        });
        failures.task = 0;
        return { task, timestamp: Number(block.timestamp), sampledAt: Date.now() };
      } catch (error) {
        failures.task++;
        throw error;
      }
    },
    refetchInterval: query => pollDelay(failures.task, !!query.state.data && isFinal(query.state.data.task.status)),
  });
  const task = taskQuery.data?.task;
  const chainNow = () => {
    const data = taskQuery.data;
    return data ? data.timestamp + Math.floor((Date.now() - data.sampledAt) / 1000) : NaN;
  };
  const reportsQuery = useQuery({
    // A new status or attempt on the chain gives one fresh read, even when polling has stopped.
    queryKey: ["proofpay-reports", settings.escrowAddress, id?.toString(), task?.status, task?.attempts],
    enabled: id !== null && !!task?.attempts,
    placeholderData: keepPreviousData,
    retry: false,
    queryFn: async ({ signal }) => {
      try {
        const reports = await fetchReports(id!, signal);
        failures.reports = 0;
        return reports;
      } catch (error) {
        failures.reports++;
        throw error;
      }
    },
    refetchInterval: query => reportPollDelay(task, query.state.data, failures.reports, chainNow()),
  });
  const { data: arbitrationTimeout } = useReadContract({
    address: settings.escrowAddress,
    abi: escrowAbi,
    chainId: monadTestnet.id,
    functionName: "arbitrationTimeout",
  });
  const [now, setNow] = useState(NaN);
  useEffect(() => {
    const data = taskQuery.data;
    if (!data) return;
    const tick = () => setNow(data.timestamp + Math.floor((Date.now() - data.sampledAt) / 1000));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [taskQuery.data]);
  const refresh = async () => {
    await taskQuery.refetch();
    // Force a report read even if the action just changed the task to a final status.
    if (id !== null) await reportsQuery.refetch();
  };
  return { task, taskQuery, reportsQuery, arbitrationTimeout, now, refresh };
}

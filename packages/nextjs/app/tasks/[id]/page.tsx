"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useAccount } from "wagmi";
import { TaskPhoto } from "~~/components/TaskCard";
import { useChainNow } from "~~/hooks/proofpay/useChainNow";
import { useTask } from "~~/hooks/proofpay/useTasks";
import { STATUS_LABEL, TaskStatus } from "~~/utils/proofpay/escrow";
import { formatMon, formatTimeLeft, shortAddress } from "~~/utils/proofpay/format";

const parseId = (value: unknown): bigint | null =>
  typeof value === "string" && /^[1-9]\d{0,30}$/.test(value) ? BigInt(value) : null;

/**
 * Task detail, first part (M5): what the F-12 create flow opens.
 * M6 adds the status timeline, the after photo, the report card, role buttons and "Details" (F-10).
 */
const TaskDetail = () => {
  const id = parseId(useParams().id);
  const now = useChainNow();
  const { address } = useAccount();
  const { data: task, error, isLoading } = useTask(id, 3_000);

  if (id === null) return <p className="text-muted">This task number is not valid.</p>;
  if (isLoading) return <p className="text-sm text-muted">Loading the task…</p>;
  if (error || !task) {
    return (
      <div className="card p-5 text-sm">
        <p className="text-fail">Task #{id.toString()} was not found.</p>
        <Link href="/tasks" className="link mt-2 inline-block">
          Back to the board
        </Link>
      </div>
    );
  }

  const left = formatTimeLeft(Number(task.submitBy) - now);
  const isPoster = address?.toLowerCase() === task.poster.toLowerCase();

  return (
    <div className="mx-auto w-full max-w-2xl space-y-5">
      <Link href="/tasks" className="text-sm text-muted hover:text-ink">
        ← Open tasks
      </Link>
      <div className="card animate-fade-up overflow-hidden">
        <TaskPhoto hash={task.beforeHash} alt={`Before: ${task.title}`} className="aspect-[4/3] w-full" />
        <div className="space-y-4 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <h1 className="text-xl font-bold">{task.title}</h1>
            <span className="rounded-full bg-line px-3 py-1 text-xs font-semibold">
              {STATUS_LABEL[task.status as TaskStatus]}
            </span>
          </div>
          <p className="whitespace-pre-wrap text-sm text-muted">{task.description}</p>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-muted">Payment</dt>
              <dd className="font-semibold text-pass">{formatMon(task.amount)} MON</dd>
            </div>
            <div>
              <dt className="text-muted">Time to send proof</dt>
              <dd className="font-semibold">{left ?? "Time is over"}</dd>
            </div>
            <div>
              <dt className="text-muted">Posted by</dt>
              <dd className="font-mono">{isPoster ? "You" : shortAddress(task.poster)}</dd>
            </div>
            <div>
              <dt className="text-muted">Task number</dt>
              <dd>#{id.toString()}</dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  );
};

export default TaskDetail;

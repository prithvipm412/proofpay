"use client";

import Link from "next/link";
import { TaskCard } from "~~/components/TaskCard";
import { useChainNow } from "~~/hooks/proofpay/useChainNow";
import { useRecentTasks } from "~~/hooks/proofpay/useTasks";
import { TaskStatus } from "~~/utils/proofpay/escrow";

/** F-09 Task board: Open tasks that can still be accepted (before submitBy). */
const TaskBoard = () => {
  const now = useChainNow();
  const { tasks, loading, error } = useRecentTasks();
  const open = tasks.filter(({ task }) => task.status === TaskStatus.Open && Number(task.submitBy) > now);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Open tasks</h1>
          <p className="text-sm text-muted">Accept a task, do the work and send a photo before the time ends.</p>
        </div>
        <Link href="/tasks/new" className="btn btn-primary">
          Post a task
        </Link>
      </div>

      {error ? (
        <p className="card p-5 text-sm text-fail">
          The task list could not be loaded. It tries again in a few seconds.
        </p>
      ) : loading ? (
        <p className="text-sm text-muted">Loading tasks…</p>
      ) : open.length === 0 ? (
        <p className="card p-5 text-sm text-muted">No open tasks now. Post the first one.</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {open.map(({ id, task }, i) => (
            <TaskCard key={id.toString()} id={id} task={task} now={now} index={i} />
          ))}
        </div>
      )}
    </div>
  );
};

export default TaskBoard;

"use client";

import { useState } from "react";
import Link from "next/link";
import { useAccount } from "wagmi";
import { LoginButton } from "~~/components/LoginButton";
import { TaskPhoto } from "~~/components/TaskCard";
import { BOARD_WINDOW, useRecentTasks } from "~~/hooks/proofpay/useTasks";
import { STATUS_LABEL, TaskStatus } from "~~/utils/proofpay/escrow";
import { formatMon } from "~~/utils/proofpay/format";

export default function MyTasks() {
  const { address } = useAccount();
  const [tab, setTab] = useState<"poster" | "worker">("poster");
  const [window, setWindow] = useState(BOARD_WINDOW);
  const { tasks, count, loading, error } = useRecentTasks(window);
  const mine = tasks.filter(({ task }) => address && task[tab].toLowerCase() === address.toLowerCase());
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">My tasks</h1>
      {!address ? (
        <LoginButton label="Log in to see your tasks" />
      ) : (
        <>
          <div className="flex gap-2" role="tablist" aria-label="My task roles">
            {(["poster", "worker"] as const).map(role => (
              <button
                role="tab"
                aria-selected={tab === role}
                key={role}
                className={`btn ${tab === role ? "btn-primary" : "btn-ghost"}`}
                onClick={() => setTab(role)}
              >
                {role === "poster" ? "Posted" : "Working"}
              </button>
            ))}
          </div>
          {error && (
            <p className="text-sm text-warn">Some tasks could not be loaded. The list will try again shortly.</p>
          )}
          {loading ? (
            <p className="text-muted">Loading tasks…</p>
          ) : (
            <>
              {!mine.length && <p className="card p-5 text-sm text-muted">No matching tasks in the loaded list.</p>}
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {mine.map(({ id, task }) => (
                  <Link href={`/tasks/${id}`} className="card overflow-hidden" key={id.toString()}>
                    <TaskPhoto hash={task.beforeHash} alt={task.title} className="aspect-[4/3] w-full" />
                    <div className="space-y-2 p-4">
                      <h2 className="break-words font-semibold">{task.title}</h2>
                      <div className="flex justify-between gap-2 text-sm">
                        <span className="text-pass">{formatMon(task.amount)} MON</span>
                        <span className="text-muted">{STATUS_LABEL[task.status as TaskStatus]}</span>
                      </div>
                    </div>
                  </Link>
                ))}
              </div>
            </>
          )}
          {count !== undefined && count > window && (
            <button className="btn btn-ghost" disabled={loading} onClick={() => setWindow(n => n + BOARD_WINDOW)}>
              Load older tasks
            </button>
          )}
          <p className="text-xs text-muted">
            Showing your tasks among the newest{" "}
            {count === undefined ? "…" : (count < window ? count : window).toString()} tasks.
          </p>
        </>
      )}
    </div>
  );
}

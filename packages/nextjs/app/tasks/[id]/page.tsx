"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useAccount, useReadContract } from "wagmi";
import { ReportCard } from "~~/components/ReportCard";
import { TaskPhoto } from "~~/components/TaskCard";
import { TaskControls } from "~~/components/TaskControls";
import { useTaskDetail } from "~~/hooks/proofpay/useTaskDetail";
import { monadTestnet } from "~~/utils/proofpay/chain";
import { STATUS_LABEL, TaskStatus, escrowAbi } from "~~/utils/proofpay/escrow";
import { formatMon, formatTimeLeft, shortAddress } from "~~/utils/proofpay/format";
import { currentReport, jobMessage } from "~~/utils/proofpay/reports";
import { settings } from "~~/utils/proofpay/settings";
import { isFinal } from "~~/utils/proofpay/taskActions";

const parseId = (value: unknown): bigint | null =>
  typeof value === "string" && /^[1-9]\d{0,15}$/.test(value) && Number.isSafeInteger(Number(value))
    ? BigInt(value)
    : null;

const TaskDetail = () => {
  const id = parseId(useParams().id);
  const { address } = useAccount();
  const { task, taskQuery, reportsQuery, arbitrationTimeout, now, refresh } = useTaskDetail(id);
  const recipient =
    task?.status === TaskStatus.Paid ? task.worker : task?.status === TaskStatus.Refunded ? task.poster : undefined;
  // F-16a: withdrawable is an address-wide balance, so never attribute it solely to this task.
  const { data: owed } = useReadContract({
    address: settings.escrowAddress,
    abi: escrowAbi,
    functionName: "withdrawable",
    args: recipient ? [recipient] : undefined,
    chainId: monadTestnet.id,
    query: { enabled: !!recipient, refetchInterval: 15_000 },
  });

  if (id === null) return <p className="text-muted">This task number is not valid.</p>;
  if (!task && taskQuery.isPending) return <p className="text-sm text-muted">Loading the task…</p>;
  if (!task)
    return (
      <div className="card space-y-3 p-5 text-sm">
        <p className="text-fail">
          Task #{id.toString()} could not be loaded. It may not exist, or Monad may be temporarily unavailable.
        </p>
        <button className="btn btn-ghost" onClick={() => refresh()}>
          Try again
        </button>
        <Link href="/tasks" className="link block">
          Back to the board
        </Link>
      </div>
    );

  const reports = reportsQuery.data ?? [];
  const report = currentReport(reports, task.attempts, task.proofHash);
  const earlier = reports.filter(r => r.attempt < task.attempts);
  const left = (deadline: bigint) =>
    Number.isFinite(now) ? (formatTimeLeft(Number(deadline) - now) ?? "0 seconds") : "…";
  const status = task.status as TaskStatus;
  const poster = address?.toLowerCase() === task.poster.toLowerCase();
  const steps = ["Posted", "Accepted", "Proof sent", "Approved", "Paid"];
  const stage = status === TaskStatus.Refunded ? -1 : status === TaskStatus.Disputed ? 3 : status;

  return (
    <div className="mx-auto w-full max-w-3xl space-y-5">
      <Link href="/tasks" className="text-sm text-muted hover:text-ink">
        ← Open tasks
      </Link>
      <section className="card space-y-5 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h1 className="break-words text-xl font-bold">{task.title}</h1>
          <span className="rounded-full bg-line px-3 py-1 text-xs font-semibold">{STATUS_LABEL[status]}</span>
        </div>
        <p className="whitespace-pre-wrap break-words text-sm text-muted">{task.description}</p>
        <ol className="grid grid-cols-5 gap-1 text-center text-[10px] sm:text-xs" aria-label="Task progress">
          {steps.map((step, index) => (
            <li
              key={step}
              aria-current={index === Math.min(stage, 4) ? "step" : undefined}
              className={`border-t-2 pt-2 ${index <= stage ? "border-pass text-ink" : "border-line text-muted"}`}
            >
              {step}
            </li>
          ))}
        </ol>
        {status === TaskStatus.Refunded && (
          <p className="text-sm">Payment entitlement settled: refunded to the poster.</p>
        )}
        {status === TaskStatus.Paid && <p className="text-sm text-pass">Payment entitlement settled for the worker.</p>}
        {recipient && owed !== undefined && owed > 0n && (
          <p className="text-sm text-warn">
            Payment waiting to be withdrawn: this recipient’s wallet has {formatMon(owed)} MON available. The recipient
            can log in and use Withdraw at the top of the page.
          </p>
        )}
        {recipient && owed === undefined && (
          <p className="text-xs text-muted">Checking whether this wallet has a payment waiting to be withdrawn…</p>
        )}
        <div className="grid grid-cols-2 gap-3">
          <figure>
            <TaskPhoto
              key={task.beforeHash}
              hash={task.beforeHash}
              alt={`Before: ${task.title}`}
              className="aspect-[4/3] w-full rounded-card"
            />
            <figcaption className="mt-1 text-xs text-muted">Before</figcaption>
          </figure>
          <figure>
            {task.attempts > 0 ? (
              <TaskPhoto
                key={task.proofHash}
                hash={task.proofHash}
                alt={`After: attempt ${task.attempts}`}
                className="aspect-[4/3] w-full rounded-card"
              />
            ) : (
              <div className="flex aspect-[4/3] items-center justify-center rounded-card bg-bg text-xs text-muted">
                No proof yet
              </div>
            )}
            <figcaption className="mt-1 text-xs text-muted">
              After{task.attempts > 0 ? ` — attempt ${task.attempts}` : ""}
            </figcaption>
          </figure>
        </div>
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-muted">Original payment</dt>
            <dd className="font-semibold text-pass">{formatMon(task.amount)} MON</dd>
          </div>
          <div>
            <dt className="text-muted">Posted by</dt>
            <dd>{poster ? "You" : shortAddress(task.poster)}</dd>
          </div>
          {(status === TaskStatus.Open || status === TaskStatus.Accepted) && (
            <div>
              <dt className="text-muted">Time to send proof</dt>
              <dd>{left(task.submitBy)}</dd>
            </div>
          )}
          {status === TaskStatus.Submitted && (
            <div>
              <dt className="text-muted">Review time left</dt>
              <dd>{left(task.reviewBy)}</dd>
            </div>
          )}
        </dl>
        {status === TaskStatus.Approved && Number(task.disputeUntil) > now && (
          <p className="text-sm text-warn">
            {poster ? "Time left to dispute" : "Payment unlocks in"}: {left(task.disputeUntil)}
          </p>
        )}
        {status === TaskStatus.Approved && Number(task.disputeUntil) <= now && (
          <p className="text-sm text-pass">The payment can now be released.</p>
        )}
        {status === TaskStatus.Disputed && (
          <p className="text-sm text-warn">
            {arbitrationTimeout === undefined
              ? "Waiting for arbiter. Loading the dispute deadline…"
              : Number(task.disputeUntil + arbitrationTimeout) > now
                ? `Waiting for arbiter — ${left(task.disputeUntil + arbitrationTimeout)} remaining`
                : "The arbiter’s time has ended. Anyone can end the dispute to refund the poster."}
          </p>
        )}
      </section>
      <TaskControls
        key={id.toString()}
        id={id}
        task={task}
        now={now}
        arbitrationTimeout={arbitrationTimeout}
        hasReport={!!report?.jobState}
        stale={taskQuery.isError}
        refresh={refresh}
      />
      {reportsQuery.isError && (
        <p className="card p-4 text-sm text-warn">
          Reports are temporarily unavailable. Any report below is the last one loaded. Checking continues
          independently; you can refresh this page later.
        </p>
      )}
      {task.attempts > 0 &&
        (report ? (
          <ReportCard report={report} reviewBy={task.reviewBy} />
        ) : (
          <section className="card p-5">
            <h2 className="font-semibold">Attempt {task.attempts} of 3</h2>
            <p className="text-sm text-muted">
              {isFinal(status)
                ? "No matching report is available. The payment status above comes from Monad."
                : jobMessage(undefined, task.reviewBy)}
            </p>
          </section>
        ))}
      {earlier.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-semibold">Earlier attempts</h2>
          {earlier.map(r => (
            <details key={r.attempt} className="space-y-3">
              <summary className="cursor-pointer text-sm text-muted">
                Attempt {r.attempt}: {r.attemptResult === "none" ? "No recorded result" : r.attemptResult}
              </summary>
              <ReportCard report={r} reviewBy={task.reviewBy} />
            </details>
          ))}
        </section>
      )}
      <details className="card p-5 text-xs text-muted">
        <summary className="cursor-pointer text-sm">Details</summary>
        <dl className="mt-3 space-y-2 break-all">
          <div>
            <dt>Task</dt>
            <dd>#{id.toString()}</dd>
          </div>
          <div>
            <dt>Before hash</dt>
            <dd>{task.beforeHash}</dd>
          </div>
          <div>
            <dt>Current proof hash</dt>
            <dd>{task.proofHash}</dd>
          </div>
          <div>
            <dt>Worker</dt>
            <dd>{task.worker}</dd>
          </div>
          <div>
            <dt>Proof deadline</dt>
            <dd>{new Date(Number(task.submitBy) * 1000).toLocaleString()}</dd>
          </div>
          <div>
            <dt>Review deadline</dt>
            <dd>{new Date(Number(task.reviewBy) * 1000).toLocaleString()}</dd>
          </div>
        </dl>
        <a
          className="link mt-3 block"
          href={`${settings.explorerUrl}/address/${settings.escrowAddress}`}
          target="_blank"
          rel="noreferrer"
        >
          View contract on Monad testnet explorer
        </a>
        <p className="mt-2">
          Withdrawable balances cover all tasks for a wallet. Paid or Refunded settles the entitlement; a failed direct
          payment remains withdrawable.
        </p>
      </details>
    </div>
  );
};

export default TaskDetail;

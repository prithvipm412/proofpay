"use client";

import { useEffect, useRef, useState } from "react";
import { useAccount, usePublicClient } from "wagmi";
import { LoginButton } from "~~/components/LoginButton";
import { TxError } from "~~/components/TxError";
import { useEscrowTx } from "~~/hooks/proofpay/useEscrowTx";
import type { Task } from "~~/hooks/proofpay/useTasks";
import { monadTestnet } from "~~/utils/proofpay/chain";
import { TaskStatus } from "~~/utils/proofpay/escrow";
import { prepareProofPhoto } from "~~/utils/proofpay/proofPhoto";
import { requestVerification } from "~~/utils/proofpay/reports";
import { settings } from "~~/utils/proofpay/settings";
import { ACTION_LABEL, TaskAction, taskActions } from "~~/utils/proofpay/taskActions";
import { uploadPhoto } from "~~/utils/proofpay/verifier";

export const TaskControls = ({
  id,
  task,
  now,
  arbitrationTimeout,
  hasReport,
  stale,
  refresh,
}: {
  id: bigint;
  task: Task;
  now: number;
  arbitrationTimeout?: bigint;
  hasReport: boolean;
  stale: boolean;
  refresh: () => Promise<void>;
}) => {
  const { address } = useAccount();
  const client = usePublicClient({ chainId: monadTestnet.id });
  const { send } = useEscrowTx();
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string>();
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [lastHash, setLastHash] = useState<string>();
  const [uncertain, setUncertain] = useState(false);
  const actions = stale ? [] : taskActions(task, address, now, arbitrationTimeout);
  useEffect(() => {
    if (!photo) {
      setPreview(undefined);
      return;
    }
    const url = URL.createObjectURL(photo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  const act = async (action: TaskAction) => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError(null);
    setMessage("");
    let sent = false;
    try {
      let proofHash: `0x${string}` | undefined;
      if (action === "submitProof") {
        if (!photo) throw new Error("Choose an after photo first.");
        setMessage("Preparing the photo…");
        const prepared = await prepareProofPhoto(photo);
        setMessage(
          prepared.converted
            ? "Converted to JPEG; capture time will be unavailable. Saving the photo…"
            : "Saving the photo…",
        );
        proofHash = await uploadPhoto(prepared.file);
      }
      setMessage("Confirm this action in your wallet…");
      await send(
        action === "submitProof"
          ? { functionName: "submitProof", args: [id, proofHash!] }
          : { functionName: action, args: [id] },
        hash => {
          sent = true;
          setLastHash(hash);
          setUncertain(true);
          setMessage("Waiting for Monad to confirm…");
        },
      );
      setUncertain(false);
      if (action === "submitProof") {
        setPhoto(null);
        setMessage("Proof saved. You can close this page; checking continues in the background.");
        try {
          await requestVerification(id);
        } catch {
          setMessage("Proof saved. The checking service could not be reached; it will find your proof automatically.");
        }
      } else setMessage("The action was confirmed.");
      await refresh();
    } catch (e) {
      setError(e);
      if (sent)
        setMessage(
          "This action may still be confirmed. Refresh the task and inspect the receipt in Details before trying again.",
        );
    } finally {
      running.current = false;
      setBusy(false);
    }
  };
  const nudge = async () => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError(null);
    try {
      await requestVerification(id);
      setMessage("The checking service was asked to look for your proof.");
      await refresh();
    } catch {
      setError(new Error("The checking service is unavailable. Your proof stays saved; try again later."));
    } finally {
      running.current = false;
      setBusy(false);
    }
  };
  // F-14: the worker and other users can nudge; the poster row shows no button.
  const isPoster = address?.toLowerCase() === task.poster.toLowerCase();
  const canNudge = task.status === TaskStatus.Submitted && !hasReport && !isPoster && Number.isSafeInteger(Number(id));
  const checkSavedAction = async () => {
    if (!client || !lastHash || running.current) return;
    running.current = true;
    setBusy(true);
    setError(null);
    try {
      const receipt = await client.getTransactionReceipt({ hash: lastHash as `0x${string}` });
      setUncertain(false);
      setMessage(
        receipt.status === "success"
          ? "The action was confirmed."
          : "The action was refused. Refresh the task before trying again.",
      );
      await refresh();
    } catch {
      setMessage(
        "Confirmation is still unavailable. Your action may still be pending; inspect Details before trying again.",
      );
    } finally {
      running.current = false;
      setBusy(false);
    }
  };
  return (
    <section className="card space-y-4 p-5" aria-label="Task actions">
      {stale && (
        <p className="text-sm text-warn">
          The latest task state could not be loaded. Actions are paused until it refreshes.
        </p>
      )}
      {!address && actions.length === 0 && <LoginButton label="Log in to work with this task" />}
      {actions.includes("submitProof") && (
        <div className="space-y-3">
          <label className="label" htmlFor="after-photo">
            After photo — Attempt {task.attempts + 1} of 3
          </label>
          <input
            id="after-photo"
            className="block w-full min-w-0 text-sm"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            capture="environment"
            disabled={busy || uncertain}
            onChange={e => setPhoto(e.target.files?.[0] ?? null)}
          />
          <p className="text-xs text-muted">
            Show the completed work from a similar view. Some phone formats are converted to JPEG; this removes capture
            time.
          </p>
          {preview && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={preview} alt="Selected after photo" className="max-h-64 w-full rounded-card object-contain" />
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-3">
        {uncertain && (
          <button className="btn btn-ghost" disabled={busy} onClick={checkSavedAction}>
            Check saved action
          </button>
        )}
        {actions.map(action => (
          <button
            key={action}
            className="btn btn-primary"
            disabled={busy || uncertain || (action === "submitProof" && !photo)}
            onClick={() => act(action)}
          >
            {action === "refund" && task.status === TaskStatus.Open ? "Cancel and refund" : ACTION_LABEL[action]}
          </button>
        ))}
        {canNudge && (
          <button className="btn btn-ghost" disabled={busy} onClick={nudge}>
            Continue checking
          </button>
        )}
        <button className="btn btn-ghost" disabled={busy} onClick={() => refresh()}>
          Refresh task
        </button>
      </div>
      {message && (
        <p className="text-sm text-muted" role="status">
          {message}
        </p>
      )}
      <TxError error={error} />
      {lastHash && (
        <details className="text-xs text-muted">
          <summary className="cursor-pointer">Details for your last action</summary>
          <a
            className="link mt-2 block break-all"
            href={`${settings.explorerUrl}/tx/${lastHash}`}
            target="_blank"
            rel="noreferrer"
          >
            View transaction: {lastHash}
          </a>
        </details>
      )}
    </section>
  );
};

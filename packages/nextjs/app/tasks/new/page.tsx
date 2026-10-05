"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { usePrivy } from "@privy-io/react-auth";
import { parseEventLogs } from "viem";
import { usePublicClient } from "wagmi";
import { LoginButton } from "~~/components/LoginButton";
import { TxError } from "~~/components/TxError";
import { useEscrowTx } from "~~/hooks/proofpay/useEscrowTx";
import { monadTestnet } from "~~/utils/proofpay/chain";
import { MAX_DESCRIPTION_BYTES, MAX_TITLE_BYTES, escrowAbi } from "~~/utils/proofpay/escrow";
import {
  DEFAULT_AMOUNT,
  DEFAULT_LIMIT_MINUTES,
  DEMO_LIMIT_MINUTES,
  TaskFormErrors,
  computeSubmitBy,
  parseAmount,
  utf8Length,
  validateTaskForm,
} from "~~/utils/proofpay/taskForm";
import { uploadPhoto } from "~~/utils/proofpay/verifier";

type StepState = "todo" | "active" | "done" | "failed";

// F-12 progress lines. F-19: no technical words on the main path.
const STEP_LABELS = ["Save the photo", "Lock the payment", "Wait for Monad to confirm", "Open the task"];

const StepLine = ({ label, state }: { label: string; state: StepState }) => {
  const dot = {
    todo: "border-line",
    active: "border-action animate-pulse",
    done: "border-pass bg-pass",
    failed: "border-fail bg-fail",
  }[state];
  return (
    <li className={`flex items-center gap-3 text-sm ${state === "todo" ? "text-muted" : "text-ink"}`}>
      <span className={`h-3 w-3 shrink-0 rounded-full border-2 ${dot}`} />
      {label}
    </li>
  );
};

const FieldError = ({ text }: { text?: string }) => (text ? <p className="mt-1 text-sm text-fail">{text}</p> : null);

/** F-08 Create task and the F-12 create flow. */
const CreateTask = () => {
  const router = useRouter();
  const { ready, authenticated } = usePrivy();
  const publicClient = usePublicClient({ chainId: monadTestnet.id });
  const { send, address } = useEscrowTx();

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [amount, setAmount] = useState(DEFAULT_AMOUNT);
  const [hours, setHours] = useState(String(DEFAULT_LIMIT_MINUTES / 60));
  const [demo, setDemo] = useState(false);
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [errors, setErrors] = useState<TaskFormErrors>({});
  const [steps, setSteps] = useState<StepState[] | null>(null);
  const [failedAt, setFailedAt] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!photo) return setPhotoUrl(null);
    const url = URL.createObjectURL(photo);
    setPhotoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  const limitMinutes = demo ? DEMO_LIMIT_MINUTES : Math.round(Number(hours) * 60);
  const running = steps !== null && failedAt === null;

  const create = async () => {
    const form = { title, description, amount, limitMinutes, hasPhoto: photo !== null };
    const found = validateTaskForm(form);
    setErrors(found);
    if (Object.keys(found).length > 0 || !photo || !publicClient) return;

    let stage = 0;
    const show = (current: number) =>
      setSteps(STEP_LABELS.map((_, i) => (i < current ? "done" : i === current ? "active" : "todo")));
    setError(null);
    setFailedAt(null);
    show(0);
    try {
      const beforeHash = await uploadPhoto(photo);
      stage = 1;
      show(1);
      const latest = await publicClient.getBlock();
      const submitBy = computeSubmitBy(latest.timestamp, limitMinutes);
      const receipt = await send(
        {
          functionName: "createTask",
          args: [title.trim(), description.trim(), beforeHash, submitBy],
          value: parseAmount(amount) as bigint,
        },
        () => {
          stage = 2;
          show(2);
        },
      );
      stage = 3;
      show(3);
      const [created] = parseEventLogs({ abi: escrowAbi, logs: receipt.logs, eventName: "TaskCreated" });
      if (!created) throw new Error("The task was made, but its number was not found. Look for it on the board.");
      router.push(`/tasks/${created.args.id}`);
    } catch (e) {
      setFailedAt(stage);
      setSteps(STEP_LABELS.map((_, i) => (i < stage ? "done" : i === stage ? "failed" : "todo")));
      setError(e);
    }
  };

  return (
    <div className="mx-auto w-full max-w-xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Post a task</h1>
        <p className="text-sm text-muted">The payment stays locked in the contract until the work is checked.</p>
      </div>

      <div className="card animate-fade-up space-y-5 p-5">
        <div>
          <label className="label" htmlFor="title">
            Title
          </label>
          <input
            id="title"
            className="field"
            value={title}
            maxLength={MAX_TITLE_BYTES}
            placeholder="Remove the litter around this bench"
            disabled={running}
            onChange={e => setTitle(e.target.value)}
          />
          <FieldError text={errors.title} />
        </div>

        <div>
          <label className="label" htmlFor="description">
            Description
          </label>
          <textarea
            id="description"
            className="field min-h-28"
            value={description}
            placeholder="Pick up all litter within 2 metres of the bench and put it in the bin."
            disabled={running}
            onChange={e => setDescription(e.target.value)}
          />
          <p className="mt-1 text-right text-xs text-muted">
            {utf8Length(description.trim())}/{MAX_DESCRIPTION_BYTES}
          </p>
          <FieldError text={errors.description} />
        </div>

        <div>
          <label className="label" htmlFor="photo">
            Before photo
          </label>
          <input
            id="photo"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            disabled={running}
            className="block w-full text-sm text-muted file:mr-3 file:rounded-card file:border-0 file:bg-line file:px-4 file:py-2.5 file:text-sm file:font-semibold file:text-ink"
            onChange={e => setPhoto(e.target.files?.[0] ?? null)}
          />
          {photoUrl && (
            // A local object URL; next/image does not apply.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={photoUrl} alt="Before photo" className="mt-3 max-h-64 w-full rounded-card object-cover" />
          )}
          <FieldError text={errors.photo} />
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="amount">
              Payment (MON)
            </label>
            <input
              id="amount"
              className="field"
              inputMode="decimal"
              value={amount}
              disabled={running}
              onChange={e => setAmount(e.target.value)}
            />
            <p className="mt-1 text-xs text-muted">Minimum 0.001 MON.</p>
            <FieldError text={errors.amount} />
          </div>

          <div>
            <label className="label" htmlFor="hours">
              Time limit
            </label>
            {demo ? (
              <div className="flex min-h-11 items-center justify-between rounded-card border border-action px-3 text-sm">
                10 minutes (demo)
                <button className="text-muted hover:text-ink" disabled={running} onClick={() => setDemo(false)}>
                  Change
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <input
                  id="hours"
                  className="field"
                  type="number"
                  inputMode="decimal"
                  min={1}
                  step={1}
                  value={hours}
                  disabled={running}
                  onChange={e => setHours(e.target.value)}
                />
                <span className="text-sm text-muted">hours</span>
              </div>
            )}
            {!demo && (
              <button className="link mt-1 text-xs" disabled={running} onClick={() => setDemo(true)}>
                Demo: 10 minutes
              </button>
            )}
            <FieldError text={errors.limitMinutes} />
          </div>
        </div>

        {steps && (
          <ol className="space-y-2 border-t border-line pt-4">
            {STEP_LABELS.map((label, i) => (
              <StepLine key={label} label={label} state={steps[i]} />
            ))}
          </ol>
        )}

        {failedAt !== null && (
          <div className="space-y-1">
            <TxError error={error} />
            {failedAt < 3 && (
              <p className="text-xs text-muted">
                {failedAt < 2
                  ? "No task was made and no MON left your wallet."
                  : "The task can still appear when Monad confirms it. Look on the board before you try again."}
              </p>
            )}
          </div>
        )}

        {!ready ? null : !authenticated ? (
          <LoginButton label="Log in to post a task" />
        ) : (
          <button className="btn btn-primary w-full" disabled={running || !address} onClick={create}>
            {running ? "Working…" : `Lock ${amount.trim() || "0"} MON and post`}
          </button>
        )}
      </div>
    </div>
  );
};

export default CreateTask;

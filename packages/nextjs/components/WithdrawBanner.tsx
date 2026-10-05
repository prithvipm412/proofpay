"use client";

import { useState } from "react";
import { getAddress, isAddress } from "viem";
import { useAccount, useReadContract } from "wagmi";
import { TxError } from "~~/components/TxError";
import { useEscrowTx } from "~~/hooks/proofpay/useEscrowTx";
import { monadTestnet } from "~~/utils/proofpay/chain";
import { escrowAbi } from "~~/utils/proofpay/escrow";
import { formatMon } from "~~/utils/proofpay/format";
import { settings } from "~~/utils/proofpay/settings";

type Step = "closed" | "edit" | "confirm" | "sending";

/**
 * F-06: shows a banner IF a payment to this wallet went to `withdrawable`.
 * Only the entitled account can withdraw (C-22). The user can send it to a different address after a confirmation.
 */
export const WithdrawBanner = () => {
  const { address } = useAccount();
  const { send } = useEscrowTx();
  const { data: owed, refetch } = useReadContract({
    address: settings.escrowAddress,
    abi: escrowAbi,
    functionName: "withdrawable",
    args: address ? [address] : undefined,
    chainId: monadTestnet.id,
    query: { enabled: !!address, refetchInterval: 15_000 },
  });
  const [step, setStep] = useState<Step>("closed");
  const [to, setTo] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState(false);

  if (!address || (!owed && !done)) return null;

  if (done && !owed) {
    return (
      <div className="border-b border-line bg-pass/10 px-4 py-3 text-center text-sm text-pass">
        The payment was sent.{" "}
        <button className="underline" onClick={() => setDone(false)}>
          Close
        </button>
      </div>
    );
  }
  if (!owed) return null;

  const toValid = isAddress(to.trim(), { strict: false });
  const withdraw = async () => {
    setStep("sending");
    setError(null);
    try {
      await send({ functionName: "withdraw", args: [getAddress(to.trim())] });
      setDone(true);
      setStep("closed");
      await refetch();
    } catch (e) {
      setError(e);
      setStep("confirm");
    }
  };

  return (
    <div className="border-b border-line bg-warn/10 px-4 py-3 text-sm">
      <div className="mx-auto max-w-5xl">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-warn">A payment to your wallet did not go through. Withdraw it. ({formatMon(owed)} MON)</p>
          {step === "closed" && (
            <button
              className="btn btn-primary min-h-9"
              onClick={() => {
                setTo(address);
                setStep("edit");
              }}
            >
              Withdraw
            </button>
          )}
        </div>

        {step === "edit" && (
          <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
            <label className="flex-1">
              <span className="label">Send to (your address by default)</span>
              <input className="field font-mono text-sm" value={to} onChange={e => setTo(e.target.value)} />
            </label>
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={!toValid} onClick={() => setStep("confirm")}>
                Next
              </button>
              <button className="btn btn-ghost" onClick={() => setStep("closed")}>
                Cancel
              </button>
            </div>
            {!toValid && to.trim() !== "" && <p className="text-fail">This is not a valid address.</p>}
          </div>
        )}

        {(step === "confirm" || step === "sending") && (
          <div className="mt-3 space-y-2">
            <p>
              Send {formatMon(owed)} MON to <span className="break-all font-mono">{getAddress(to.trim())}</span>?
              {getAddress(to.trim()) !== address && <span className="text-warn"> This is not your own address.</span>}
            </p>
            <div className="flex gap-2">
              <button className="btn btn-primary" disabled={step === "sending"} onClick={withdraw}>
                {step === "sending" ? "Sending…" : "Confirm"}
              </button>
              <button className="btn btn-ghost" disabled={step === "sending"} onClick={() => setStep("edit")}>
                Back
              </button>
            </div>
            <TxError error={error} />
          </div>
        )}
      </div>
    </div>
  );
};

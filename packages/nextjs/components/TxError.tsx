import { FAUCET_URL } from "~~/utils/proofpay/chain";
import { NotEnoughMonError } from "~~/utils/proofpay/fees";
import { describeTxError, isInsufficientFunds } from "~~/utils/proofpay/txErrors";

/** A failed action as a plain sentence. F-05: a missing balance also shows the faucet link. */
export const TxError = ({ error }: { error: unknown }) => {
  if (!error) return null;
  const noMon = error instanceof NotEnoughMonError || isInsufficientFunds(error);
  return (
    <p className="text-sm text-fail" role="alert">
      {describeTxError(error)}{" "}
      {noMon && (
        <a className="link" href={FAUCET_URL} target="_blank" rel="noreferrer">
          Get test MON
        </a>
      )}
    </p>
  );
};

import { NotEnoughMonError } from "./fees";
import { BaseError, ContractFunctionRevertedError, InsufficientFundsError, UserRejectedRequestError } from "viem";

export const isInsufficientFunds = (error: unknown) =>
  error instanceof BaseError && error.walk(e => e instanceof InsufficientFundsError) !== null;

/** Name of the custom contract error (C-25), IF the call reverted with one. */
export function revertErrorName(error: unknown): string | null {
  if (!(error instanceof BaseError)) return null;
  const revert = error.walk(e => e instanceof ContractFunctionRevertedError);
  return revert instanceof ContractFunctionRevertedError ? (revert.data?.errorName ?? null) : null;
}

/**
 * A plain sentence for a failed action (F-19: no technical words on the main path).
 * M6 adds the full sentence map for each C-25 error (F-18).
 */
export function describeTxError(error: unknown): string {
  if (error instanceof NotEnoughMonError || isInsufficientFunds(error)) return new NotEnoughMonError().message;
  if (error instanceof BaseError && error.walk(e => e instanceof UserRejectedRequestError) !== null) {
    return "You cancelled this action.";
  }
  const name = revertErrorName(error);
  if (name) return `The contract refused this action (${name}).`;
  if (error instanceof Error && !(error instanceof BaseError) && error.message) return error.message;
  return "Something went wrong. Try again.";
}

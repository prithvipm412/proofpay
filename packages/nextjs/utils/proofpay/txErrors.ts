import { NotEnoughMonError } from "./fees";
import { BaseError, ContractFunctionRevertedError, InsufficientFundsError, UserRejectedRequestError } from "viem";

export const CONTRACT_ERRORS: Record<string, string> = {
  NotPoster: "Only the person who posted this task can do that.",
  NotWorker: "Only the worker who accepted this task can send proof.",
  NotVerifier: "Only the checking service can save a result.",
  PosterCannotAccept: "You cannot accept your own task.",
  WrongStatus: "The task has changed. Refresh it before trying again.",
  AmountTooLow: "The payment must be at least 0.001 MON.",
  ZeroHash: "Save a photo before continuing.",
  SameAsBefore: "Use an after photo different from the before photo.",
  InvalidDeadline: "Choose a time limit between one minute and 30 days.",
  SubmitClosed: "The time to accept this task or send proof has ended.",
  ReviewClosed: "The time for checking this proof has ended.",
  RefundNotAvailable: "A refund is not available yet. Check the task deadline.",
  TooManyAttempts: "All three proof attempts have been used.",
  StaleVerdict: "This result belongs to an older proof attempt.",
  DisputeWindowOpen: "You can release the payment when the countdown ends.",
  DisputeWindowClosed: "The time to dispute this result has ended.",
  ArbitrationNotExpired: "The arbiter still has time to decide this dispute.",
  ArbitrationClosed: "The arbiter's time has ended. Use End dispute for a refund.",
  ScoreTooHigh: "The check returned an invalid score.",
  TextTooLong: "The text is too long. Please shorten it.",
  EmptyText: "Enter a title and description.",
  TaskNotFound: "This task does not exist.",
  NothingToWithdraw: "There is no payment waiting to be withdrawn.",
  TransferFailed: "The payment could not be sent. Choose another receiving address.",
  ZeroAddress: "Enter a valid receiving address.",
  InvalidConfig: "The contract settings are not valid.",
  RenounceDisabled: "This contract must keep an arbiter.",
  OwnableUnauthorizedAccount: "Only the arbiter can do that.",
  OwnableInvalidOwner: "Choose a valid arbiter address.",
  ReentrancyGuardReentrantCall: "Another payment is already being processed.",
};

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
 * F-18: every C-25 error has a plain sentence.
 */
export function describeTxError(error: unknown): string {
  if (error instanceof NotEnoughMonError || isInsufficientFunds(error)) return new NotEnoughMonError().message;
  if (error instanceof BaseError && error.walk(e => e instanceof UserRejectedRequestError) !== null) {
    return "You cancelled this action.";
  }
  const name = revertErrorName(error);
  if (name) return CONTRACT_ERRORS[name] ?? "The contract refused this action. Refresh the task before trying again.";
  if (error instanceof Error && !(error instanceof BaseError) && error.message) return error.message;
  return "Something went wrong. Try again.";
}

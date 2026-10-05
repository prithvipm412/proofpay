/**
 * Monad charges the gas limit, not the gas used (https://docs.monad.xyz/developer-essentials/gas-pricing).
 * The app sets the limit itself: the estimate plus 25 %, the same rule as the verifier (PROGRESS.md, M4).
 */
export const GAS_BUFFER_PERCENT = 125n;

export function gasLimitFor(estimate: bigint): bigint {
  return (estimate * GAS_BUFFER_PERCENT + 99n) / 100n;
}

/** F-05: the most that the action can take from the balance: the value plus the highest possible fee. */
export function maxCost(value: bigint, gasLimit: bigint, maxFeePerGas: bigint): bigint {
  return value + gasLimit * maxFeePerGas;
}

export class NotEnoughMonError extends Error {
  constructor() {
    super("You need a little test MON to do this.");
    this.name = "NotEnoughMonError";
  }
}

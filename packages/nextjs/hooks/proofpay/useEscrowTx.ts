"use client";

import { useCallback } from "react";
import type { Abi, ContractFunctionArgs, ContractFunctionName, Hash, TransactionReceipt } from "viem";
import { useAccount, usePublicClient, useSwitchChain, useWriteContract } from "wagmi";
import { monadTestnet } from "~~/utils/proofpay/chain";
import { escrowAbi } from "~~/utils/proofpay/escrow";
import { NotEnoughMonError, gasLimitFor, maxCost } from "~~/utils/proofpay/fees";
import { settings } from "~~/utils/proofpay/settings";
import { isInsufficientFunds } from "~~/utils/proofpay/txErrors";

type WriteName = ContractFunctionName<typeof escrowAbi, "nonpayable" | "payable">;

/** One state-changing escrow call, typed from the ABI: function name, its arguments and the MON value. */
export type EscrowCall = {
  [N in WriteName]: {
    functionName: N;
    args: ContractFunctionArgs<typeof escrowAbi, "nonpayable" | "payable", N>;
    value?: bigint;
  };
}[WriteName];

// `EscrowCall` checks the name and arguments. viem cannot infer a union of calls, so the request uses the wide ABI type.
const abi: Abi = escrowAbi;

/**
 * Sends one escrow call from the user's wallet:
 * switch to Monad testnet (F-03), check the balance against value + highest fee (F-05),
 * set the gas limit explicitly (Monad charges the limit), send, and wait for the receipt.
 */
export function useEscrowTx() {
  const { address, chainId } = useAccount();
  const publicClient = usePublicClient({ chainId: monadTestnet.id });
  const { switchChainAsync } = useSwitchChain();
  const { writeContractAsync } = useWriteContract();

  const send = useCallback(
    async (call: EscrowCall, onSent?: (hash: Hash) => void): Promise<TransactionReceipt> => {
      if (!address || !publicClient) throw new Error("Log in first.");
      if (chainId !== monadTestnet.id) await switchChainAsync({ chainId: monadTestnet.id });

      const value = call.value ?? 0n;
      const balance = await publicClient.getBalance({ address });
      if (balance < value) throw new NotEnoughMonError();

      const request = { address: settings.escrowAddress, abi, functionName: call.functionName, args: call.args, value };
      let estimate: bigint;
      try {
        estimate = await publicClient.estimateContractGas({ ...request, account: address });
      } catch (error) {
        if (isInsufficientFunds(error)) throw new NotEnoughMonError();
        throw error;
      }
      const gas = gasLimitFor(estimate);
      const { maxFeePerGas, maxPriorityFeePerGas } = await publicClient.estimateFeesPerGas();
      if (balance < maxCost(value, gas, maxFeePerGas)) throw new NotEnoughMonError();

      const hash = await writeContractAsync({
        ...request,
        gas,
        maxFeePerGas,
        maxPriorityFeePerGas,
        chainId: monadTestnet.id,
      });
      onSent?.(hash);
      const receipt = await publicClient.waitForTransactionReceipt({ hash });
      if (receipt.status !== "success") throw new Error("The contract refused this action.");
      return receipt;
    },
    [address, chainId, publicClient, switchChainAsync, writeContractAsync],
  );

  return { send, address };
}

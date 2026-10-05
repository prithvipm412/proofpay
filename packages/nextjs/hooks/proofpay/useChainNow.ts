"use client";

import { useEffect, useRef, useState } from "react";
import { useBlock } from "wagmi";
import { monadTestnet } from "~~/utils/proofpay/chain";

const clockSeconds = () => Math.floor(Date.now() / 1000);

/**
 * The current chain time in seconds, updated every second.
 * Deadlines in the contract use block.timestamp, so a phone with a wrong clock still shows correct times.
 */
export function useChainNow(): number {
  const { data: block } = useBlock({ chainId: monadTestnet.id, query: { refetchInterval: 60_000 } });
  const offset = useRef(0);
  const [now, setNow] = useState(clockSeconds);

  useEffect(() => {
    if (block) offset.current = Number(block.timestamp) - clockSeconds();
  }, [block]);

  useEffect(() => {
    const timer = setInterval(() => setNow(clockSeconds() + offset.current), 1000);
    return () => clearInterval(timer);
  }, []);

  return now;
}

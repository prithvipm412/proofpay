"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { usePrivy } from "@privy-io/react-auth";
import { useAccount, useBalance } from "wagmi";
import { FAUCET_URL, monadTestnet } from "~~/utils/proofpay/chain";
import { formatMon, shortAddress } from "~~/utils/proofpay/format";

const NAV = [
  { href: "/tasks", label: "Tasks" },
  { href: "/tasks/new", label: "Post a task" },
  { href: "/my-tasks", label: "My tasks" },
];

/** F-04: short address, MON balance and a "Get test MON" link. */
export const Header = () => {
  const pathname = usePathname();
  const { ready, authenticated, login, logout } = usePrivy();
  const { address } = useAccount();
  const [copied, setCopied] = useState(false);
  const { data: balance } = useBalance({
    address,
    chainId: monadTestnet.id,
    query: { enabled: !!address, refetchInterval: 10_000 },
  });

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
        <Link href="/" className="text-lg font-bold tracking-tight">
          Proof<span className="text-action">Pay</span>
        </Link>

        <nav className="order-3 flex w-full gap-1 sm:order-none sm:w-auto">
          {NAV.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              className={`rounded-card px-3 py-2 text-sm font-medium transition-colors duration-200 ${
                pathname === href ? "bg-line text-ink" : "text-muted hover:text-ink"
              }`}
            >
              {label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-3 text-sm">
          {!ready ? null : !authenticated ? (
            <button className="btn btn-primary min-h-9 px-3" onClick={login}>
              Log in
            </button>
          ) : (
            <>
              <div className="text-right leading-tight">
                {address ? (
                  <button
                    className="font-mono text-xs text-muted hover:text-ink"
                    title="Copy your full address"
                    onClick={() =>
                      navigator.clipboard.writeText(address).then(() => {
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1500);
                      })
                    }
                  >
                    {copied ? "Copied" : shortAddress(address)}
                  </button>
                ) : (
                  <div className="text-xs text-muted">Preparing wallet…</div>
                )}
                <div className="font-semibold">{balance ? `${formatMon(balance.value)} MON` : "…"}</div>
              </div>
              <a className="link text-xs" href={FAUCET_URL} target="_blank" rel="noreferrer">
                Get test MON
              </a>
              <button className="text-xs text-muted hover:text-ink" onClick={logout}>
                Log out
              </button>
            </>
          )}
        </div>
      </div>
    </header>
  );
};

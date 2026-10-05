"use client";

import { PrivyProvider } from "@privy-io/react-auth";
import { WagmiProvider } from "@privy-io/wagmi";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { wagmiConfig } from "~~/services/web3/wagmiConfig";
import { monadTestnet } from "~~/utils/proofpay/chain";
import { settings, settingsErrors } from "~~/utils/proofpay/settings";

const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

/** Provider order from the Privy wagmi guide: PrivyProvider > QueryClientProvider > WagmiProvider. */
export const Providers = ({ children }: { children: React.ReactNode }) => {
  if (settingsErrors.length > 0) {
    return (
      <main className="mx-auto max-w-xl px-4 py-12">
        <div className="card p-5">
          <h1 className="text-lg font-semibold text-fail">The app settings are not complete</h1>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-muted">
            {settingsErrors.map(error => (
              <li key={error}>{error}</li>
            ))}
          </ul>
          <p className="mt-3 text-sm text-muted">Fix packages/nextjs/.env.local, then start the app again.</p>
        </div>
      </main>
    );
  }

  return (
    <PrivyProvider
      appId={settings.privyAppId}
      config={{
        // F-01: email is the only login method. Each user gets an embedded wallet at login.
        loginMethods: ["email"],
        embeddedWallets: {
          ethereum: { createOnLogin: "users-without-wallets" },
          // Test MON has no money value, so show amounts in MON only.
          priceDisplay: { primary: "native-token", secondary: null },
        },
        defaultChain: monadTestnet,
        supportedChains: [monadTestnet],
        appearance: {
          theme: "#111C33",
          accentColor: "#3B82F6",
          landingHeader: "Log in to ProofPay",
          walletChainType: "ethereum-only",
        },
      }}
    >
      <QueryClientProvider client={queryClient}>
        <WagmiProvider config={wagmiConfig}>{children}</WagmiProvider>
      </QueryClientProvider>
    </PrivyProvider>
  );
};

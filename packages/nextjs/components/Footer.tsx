import { explorerAddressUrl } from "~~/utils/proofpay/chain";
import { settings, settingsErrors } from "~~/utils/proofpay/settings";

export const Footer = () => (
  <footer className="border-t border-line px-4 py-6 text-center text-xs text-muted">
    <p>ProofPay is a hackathon demo on Monad testnet. Test MON has no money value.</p>
    {settingsErrors.length === 0 && (
      <p className="mt-1">
        <a className="link" href={explorerAddressUrl(settings.escrowAddress)} target="_blank" rel="noreferrer">
          Escrow contract on the explorer
        </a>
      </p>
    )}
  </footer>
);

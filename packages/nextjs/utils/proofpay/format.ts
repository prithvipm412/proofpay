import { formatEther } from "viem";

/** F-04: `0x12ab…9f3c`. */
export function shortAddress(address: string): string {
  if (address.length < 10) return address;
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

/** MON amount with at most `maxDecimals` decimals, cut (not rounded) so a balance is never shown higher. */
export function formatMon(wei: bigint, maxDecimals = 4): string {
  const [whole, fraction = ""] = formatEther(wei).split(".");
  const cut = fraction.slice(0, maxDecimals).replace(/0+$/, "");
  if (cut === "" && whole === "0" && wei > 0n) return `<0.${"0".repeat(Math.max(maxDecimals - 1, 0))}1`;
  return cut ? `${whole}.${cut}` : whole;
}

/** Time left before a deadline, for cards and countdowns. Returns null when the time has ended. */
export function formatTimeLeft(seconds: number): string | null {
  if (seconds <= 0) return null;
  const s = Math.floor(seconds);
  if (s < 60) return `${s} s`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
}

import { settings } from "./settings";

const HASH_RE = /^0x[0-9a-f]{64}$/;

/** SHA-256 of the file bytes, as `0x` + 64 lowercase hex characters (the proof hash format). */
export async function sha256Hex(bytes: ArrayBuffer): Promise<`0x${string}`> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return `0x${Array.from(digest, b => b.toString(16).padStart(2, "0")).join("")}`;
}

/** Plain messages for the V-09 error codes. */
export function uploadErrorMessage(status: number): string {
  switch (status) {
    case 400:
      return "This file is not a photo that the app can use. Use a JPEG, PNG or WebP photo with one frame.";
    case 410:
      return "Uploads are closed for this demo.";
    case 413:
      return "The photo is too large. The limit is 10 MB.";
    case 429:
      return "Too many uploads from your network. Try again in an hour.";
    case 507:
      return "The checking service has no storage space now. Try again later.";
    default:
      return "The checking service could not save the photo. Try again.";
  }
}

/**
 * V-09: uploads the original bytes and returns their SHA-256.
 * The hash from the service must equal the hash of the bytes in the browser,
 * because that hash is the commitment written to the contract.
 */
export async function uploadPhoto(file: Blob, baseUrl = settings.verifierUrl): Promise<`0x${string}`> {
  const bytes = await file.arrayBuffer();
  const localHash = await sha256Hex(bytes);
  const body = new FormData();
  body.append("file", new Blob([bytes], { type: file.type || "application/octet-stream" }), "photo");

  let response: Response;
  try {
    response = await fetch(`${baseUrl}/upload`, { method: "POST", body });
  } catch {
    throw new Error("The checking service is not reachable. Try again in a minute.");
  }
  if (!response.ok) throw new Error(uploadErrorMessage(response.status));

  const data = (await response.json().catch(() => null)) as { sha256?: unknown } | null;
  const remoteHash = typeof data?.sha256 === "string" ? data.sha256.toLowerCase() : "";
  if (!HASH_RE.test(remoteHash) || remoteHash !== localHash) {
    throw new Error("The checking service saved a different file. Try again.");
  }
  return localHash;
}

/** V-10: public preview of a stored photo. */
export const previewUrl = (sha256: string, baseUrl = settings.verifierUrl) => `${baseUrl}/files/${sha256}`;

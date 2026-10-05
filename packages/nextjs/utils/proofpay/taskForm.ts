import { MAX_DESCRIPTION_BYTES, MAX_TITLE_BYTES, MIN_AMOUNT_WEI } from "./escrow";
import { parseEther } from "viem";

/** F-08 defaults and limits. The contract limit is 30 days; 29 days leaves room for clock and mining time. */
export const DEFAULT_AMOUNT = "0.05";
export const DEFAULT_LIMIT_MINUTES = 24 * 60;
export const DEMO_LIMIT_MINUTES = 10;
export const MIN_LIMIT_MINUTES = 10;
export const MAX_LIMIT_MINUTES = 29 * 24 * 60;

export type TaskForm = {
  title: string;
  description: string;
  amount: string;
  limitMinutes: number;
  hasPhoto: boolean;
};

export type TaskFormErrors = Partial<Record<"title" | "description" | "amount" | "limitMinutes" | "photo", string>>;

export const utf8Length = (text: string) => new TextEncoder().encode(text).length;

/** Returns the amount in wei, or null IF the text is not a valid MON amount. */
export function parseAmount(text: string): bigint | null {
  const value = text.trim();
  if (!/^\d+(\.\d{1,18})?$/.test(value) && !/^\.\d{1,18}$/.test(value)) return null;
  return parseEther(value.startsWith(".") ? `0${value}` : value);
}

/** Checks the same limits as `createTask` (C-13), so that a valid form is not refused by the contract. */
export function validateTaskForm(form: TaskForm): TaskFormErrors {
  const errors: TaskFormErrors = {};
  const title = form.title.trim();
  const description = form.description.trim();

  if (!title) errors.title = "Write a title.";
  else if (utf8Length(title) > MAX_TITLE_BYTES)
    errors.title = `The title is too long (${MAX_TITLE_BYTES} bytes or less).`;

  if (!description) errors.description = "Describe the work.";
  else if (utf8Length(description) > MAX_DESCRIPTION_BYTES)
    errors.description = `The description is too long (${MAX_DESCRIPTION_BYTES} bytes or less).`;

  const amount = parseAmount(form.amount);
  if (amount === null) errors.amount = "Write an amount, for example 0.05.";
  else if (amount < MIN_AMOUNT_WEI) errors.amount = "The minimum is 0.001 MON.";

  if (
    !Number.isInteger(form.limitMinutes) ||
    form.limitMinutes < MIN_LIMIT_MINUTES ||
    form.limitMinutes > MAX_LIMIT_MINUTES
  ) {
    errors.limitMinutes = "The time limit must be between 10 minutes and 29 days.";
  }

  if (!form.hasPhoto) errors.photo = "Add a before photo.";
  return errors;
}

/** submitBy from the chain time (latest block timestamp), not from the browser clock. */
export function computeSubmitBy(chainNowSeconds: bigint, limitMinutes: number): bigint {
  return chainNowSeconds + BigInt(limitMinutes) * 60n;
}

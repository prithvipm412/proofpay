"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Task } from "~~/hooks/proofpay/useTasks";
import { formatMon, formatTimeLeft } from "~~/utils/proofpay/format";
import { loadPreview } from "~~/utils/proofpay/verifier";

export const TaskPhoto = ({ hash, alt, className = "" }: { hash: string; alt: string; className?: string }) => {
  const [src, setSrc] = useState<string>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setSrc(undefined);
    setFailed(false);
    loadPreview(hash).then(
      url => live && setSrc(url),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [hash]);
  if (failed) {
    return <div className={`flex items-center justify-center bg-bg text-xs text-muted ${className}`}>No preview</div>;
  }
  if (!src) return <div className={`animate-pulse bg-bg ${className}`} role="img" aria-label={`Loading: ${alt}`} />;
  return (
    // The preview comes from the verifier (V-10) as an object URL, so next/image is not used.
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={alt} className={`object-cover ${className}`} onError={() => setFailed(true)} />
  );
};

/** F-09: preview photo, title, original amount and time left before submitBy. */
export const TaskCard = ({ id, task, now, index }: { id: bigint; task: Task; now: number; index: number }) => {
  const left = formatTimeLeft(Number(task.submitBy) - now);
  return (
    <Link
      href={`/tasks/${id}`}
      className="card group block animate-fade-up overflow-hidden transition-colors duration-200 hover:border-action/60"
      style={{ animationDelay: `${Math.min(index, 8) * 50}ms` }}
    >
      <TaskPhoto hash={task.beforeHash} alt={task.title} className="aspect-[4/3] w-full" />
      <div className="space-y-2 p-4">
        <h2 className="line-clamp-2 font-semibold">{task.title}</h2>
        <div className="flex items-center justify-between text-sm">
          <span className="font-semibold text-pass">{formatMon(task.amount)} MON</span>
          <span className="text-muted">{left ? `${left} left` : "Time is over"}</span>
        </div>
      </div>
    </Link>
  );
};

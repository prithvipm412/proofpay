"use client";

import Link from "next/link";
import { usePrivy } from "@privy-io/react-auth";
import { LoginButton } from "~~/components/LoginButton";

const STEPS = [
  { title: "Lock payment", text: "The poster locks MON in the escrow contract with a before photo." },
  { title: "Do the task", text: "A worker accepts the task, does the work and sends an after photo." },
  { title: "Get paid after the AI check", text: "Automated checks compare the photos. A pass unlocks the payment." },
];

/** F-07 Landing. */
const Landing = () => {
  const { ready, authenticated } = usePrivy();

  return (
    <div className="flex flex-col items-center gap-10 py-6 text-center sm:py-12">
      <div className="max-w-2xl animate-fade-up">
        <h1 className="text-3xl font-bold leading-tight sm:text-5xl">
          Pay for real-world tasks when the photo proof passes.
        </h1>
        <p className="mt-4 text-muted">
          Centralized escrow with automated visual checks and duplicate-evidence detection, on Monad testnet.
        </p>
      </div>

      <ol className="grid w-full gap-3 sm:grid-cols-3">
        {STEPS.map((step, i) => (
          <li
            key={step.title}
            className="card animate-fade-up p-5 text-left"
            style={{ animationDelay: `${(i + 1) * 100}ms` }}
          >
            <div className="flex items-center gap-3">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-action/15 text-sm font-bold text-action">
                {i + 1}
              </span>
              <h2 className="font-semibold">{step.title}</h2>
            </div>
            <p className="mt-3 text-sm text-muted">{step.text}</p>
          </li>
        ))}
      </ol>

      <div className="flex min-h-11 flex-wrap justify-center gap-3">
        {ready &&
          (authenticated ? (
            <>
              <Link href="/tasks" className="btn btn-primary">
                See open tasks
              </Link>
              <Link href="/tasks/new" className="btn btn-ghost">
                Post a task
              </Link>
            </>
          ) : (
            <LoginButton label="Log in with email" />
          ))}
      </div>
    </div>
  );
};

export default Landing;

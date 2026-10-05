"use client";

import { usePrivy } from "@privy-io/react-auth";

export const LoginButton = ({ label = "Log in" }: { label?: string }) => {
  const { ready, login } = usePrivy();
  return (
    <button className="btn btn-primary" disabled={!ready} onClick={login}>
      {label}
    </button>
  );
};

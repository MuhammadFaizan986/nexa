"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/lib/auth";

/** Send people to the chat if they're signed in, otherwise to the login page. */
export default function Home() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading) router.replace(user ? "/chat" : "/login");
  }, [loading, user, router]);

  return <p className="p-8 text-sm text-muted">Loading…</p>;
}

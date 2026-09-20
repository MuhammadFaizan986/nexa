"use client";

/** Sign in to an organisation, or create a new one (which makes you its owner). */

import { useState } from "react";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [organisation, setOrganisation] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(tenantSlug.trim(), email.trim(), password);
      else await register(organisation.trim(), email.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const field = "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm outline-none focus:border-brand";

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4">
      <div className="rounded-2xl border border-line bg-surface p-8 shadow-sm">
        <h1 className="text-2xl font-semibold">NEXA</h1>
        <p className="mt-1 text-sm text-muted">
          Ask questions across your documents. Every answer cites its source.
        </p>

        <div className="mt-6 flex gap-1 rounded-lg bg-canvas p-1 text-sm">
          {(["login", "register"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setMode(option)}
              className={`flex-1 rounded-md px-3 py-1.5 ${
                mode === option ? "bg-surface font-medium shadow-sm" : "text-muted"
              }`}
            >
              {option === "login" ? "Sign in" : "Create organisation"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="mt-6 space-y-4">
          {mode === "login" ? (
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Organisation handle</span>
              <input
                className={field}
                value={tenantSlug}
                onChange={(e) => setTenantSlug(e.target.value)}
                placeholder="harbourview-property-group"
                required
              />
            </label>
          ) : (
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Organisation name</span>
              <input
                className={field}
                value={organisation}
                onChange={(e) => setOrganisation(e.target.value)}
                placeholder="Harbourview Property Group"
                required
              />
            </label>
          )}
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Email</span>
            <input
              type="email"
              className={field}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Password</span>
            <input
              type="password"
              className={field}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={mode === "register" ? 10 : 1}
              required
            />
          </label>

          {error && (
            <p className="rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad" role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create organisation"}
          </button>
        </form>
      </div>
      <p className="mt-4 text-center text-xs text-muted">
        Demo data? Use <code>make seed</code>, then sign in as
        <br />
        <code>owner@harbourview.example.com</code> / <code>nexa-demo-password</code>
      </p>
    </main>
  );
}

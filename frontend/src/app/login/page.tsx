"use client";

/** Sign in to an organisation, or create a new one (which makes you its owner). */

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Logo } from "@/components/brand";
import { ThemeToggle } from "@/components/theme";
import { Button, ErrorNote, Field, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const DEMOS = [
  { slug: "harbourview-property-group", email: "owner@harbourview.example.com", label: "Property" },
  { slug: "kestrel-pay", email: "owner@kestrelpay.example.com", label: "Fintech" },
  { slug: "lumen-labs", email: "owner@lumenlabs.example.com", label: "Company" },
];

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

  function useDemo(demo: (typeof DEMOS)[number]) {
    setMode("login");
    setTenantSlug(demo.slug);
    setEmail(demo.email);
    setPassword("nexa-demo-password");
  }

  return (
    <main className="aurora relative flex min-h-screen items-center justify-center overflow-hidden px-5 py-10">
      <div className="absolute inset-x-0 top-0 flex items-center justify-between px-5 py-4">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-muted transition hover:text-ink"
        >
          <ArrowLeft size={15} />
          Back
        </Link>
        <ThemeToggle compact />
      </div>

      <div className="relative w-full max-w-md animate-fade-up rounded-2xl border border-line bg-surface p-8 shadow-lift">
        <Logo size={34} />
        <h1 className="mt-5 text-xl font-semibold tracking-tight">
          {mode === "login" ? "Welcome back" : "Create your organisation"}
        </h1>
        <p className="mt-1 text-sm text-muted">
          {mode === "login"
            ? "Sign in to ask questions across your documents."
            : "You'll be the owner, and can invite your team afterwards."}
        </p>

        <div className="mt-6 flex gap-1 rounded-lg bg-canvas p-1 text-sm">
          {(["login", "register"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setMode(option)}
              className={`flex-1 rounded-md px-3 py-1.5 transition ${
                mode === option ? "bg-surface font-medium shadow-soft" : "text-muted"
              }`}
            >
              {option === "login" ? "Sign in" : "Create organisation"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="mt-6 space-y-4">
          {mode === "login" ? (
            <Field label="Organisation handle">
              <Input
                className="w-full"
                value={tenantSlug}
                onChange={(e) => setTenantSlug(e.target.value)}
                placeholder="harbourview-property-group"
                required
              />
            </Field>
          ) : (
            <Field label="Organisation name">
              <Input
                className="w-full"
                value={organisation}
                onChange={(e) => setOrganisation(e.target.value)}
                placeholder="Harbourview Property Group"
                required
              />
            </Field>
          )}
          <Field label="Email">
            <Input
              type="email"
              className="w-full"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </Field>
          <Field
            label="Password"
            hint={mode === "register" ? "At least 10 characters" : undefined}
          >
            <Input
              type="password"
              className="w-full"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={mode === "register" ? 10 : 1}
              required
            />
          </Field>

          <ErrorNote>{error}</ErrorNote>

          <Button type="submit" loading={busy} className="w-full">
            {mode === "login" ? "Sign in" : "Create organisation"}
          </Button>
        </form>

        <div className="mt-6 border-t border-line pt-4">
          <p className="text-xs text-muted">Or open a demo workspace:</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {DEMOS.map((demo) => (
              <button
                key={demo.slug}
                type="button"
                onClick={() => useDemo(demo)}
                className="rounded-full border border-line px-3 py-1 text-xs transition hover:border-brand hover:text-brand"
              >
                {demo.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </main>
  );
}

"use client";

/**
 * The shared building blocks. Everything visual goes through here, so the app
 * looks like one product and a change lands everywhere at once.
 *
 * Interaction rules used throughout: 150ms transitions, a 1px lift on hover for
 * anything clickable, a visible focus ring for keyboard users, and a disabled
 * state that actually looks disabled.
 */

import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useState } from "react";

import type { DocumentStatus } from "@/lib/types";

/* ------------------------------------------------------------------ surfaces */

export function Card({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title?: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-line bg-surface shadow-soft transition hover:shadow-lift ${className}`}
    >
      {(title || action) && (
        <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-3.5">
          <div>
            {title && <h2 className="text-sm font-semibold">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
          </div>
          {action}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

/* ------------------------------------------------------------------ controls */

const BUTTON_STYLES = {
  primary: "bg-brand text-brand-ink hover:brightness-110 shadow-soft",
  secondary: "border border-line bg-surface hover:bg-canvas",
  ghost: "text-muted hover:bg-canvas hover:text-ink",
  danger: "border border-line text-bad hover:bg-bad/10",
} as const;

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  icon: Icon,
  className = "",
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof BUTTON_STYLES;
  size?: "sm" | "md";
  loading?: boolean;
  icon?: typeof Check;
}) {
  const sizing = size === "sm" ? "px-2.5 py-1 text-xs gap-1.5" : "px-3.5 py-2 text-sm gap-2";
  return (
    <button
      {...props}
      disabled={props.disabled || loading}
      className={`inline-flex items-center justify-center rounded-lg font-medium transition
        focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand
        active:translate-y-px disabled:pointer-events-none disabled:opacity-50
        ${sizing} ${BUTTON_STYLES[variant]} ${className}`}
    >
      {loading ? (
        <Loader2 size={size === "sm" ? 13 : 15} className="animate-spin" />
      ) : (
        Icon && <Icon size={size === "sm" ? 13 : 15} />
      )}
      {children}
    </button>
  );
}

const FIELD =
  "rounded-lg border border-line bg-surface px-3 py-2 text-sm transition placeholder:text-muted " +
  "focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${FIELD} ${props.className ?? ""}`} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${FIELD} ${props.className ?? ""}`} />;
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-1.5 block font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

/* ------------------------------------------------------------------ feedback */

/** Colour tells the story: green = searchable, amber = working, red = needs you. */
export function StatusBadge({ status }: { status: DocumentStatus }) {
  const config: Record<DocumentStatus, { className: string; label: string; live?: boolean }> = {
    ready: { className: "bg-good/10 text-good", label: "Ready" },
    processing: { className: "bg-warn/10 text-warn", label: "Processing", live: true },
    pending: { className: "bg-warn/10 text-warn", label: "Queued", live: true },
    failed: { className: "bg-bad/10 text-bad", label: "Failed" },
  };
  const { className, label, live } = config[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}
    >
      {live && <span className="size-1.5 rounded-full bg-current animate-pulse-soft" />}
      {label}
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

export function Empty({
  title,
  children,
  icon: Icon,
}: {
  title?: string;
  children: React.ReactNode;
  icon?: typeof Check;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center animate-fade-in">
      {Icon && (
        <span className="grid size-11 place-items-center rounded-full bg-canvas text-muted">
          <Icon size={20} />
        </span>
      )}
      {title && <p className="text-sm font-medium">{title}</p>}
      <p className="max-w-sm text-sm text-muted">{children}</p>
    </div>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return (
    <p
      role="alert"
      className="flex items-start gap-2 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad animate-fade-in"
    >
      <AlertTriangle size={15} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

/* -------------------------------------------------------------------- toasts */

type Toast = { id: number; message: string; tone: "good" | "bad" };
const ToastContext = createContext<((message: string, tone?: "good" | "bad") => void) | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notify = useCallback((message: string, tone: "good" | "bad" = "good") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, message, tone }]);
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 3500);
  }, []);

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="pointer-events-auto flex items-center gap-2 rounded-lg border border-line bg-raised px-3.5 py-2.5 text-sm shadow-lift animate-slide-in"
          >
            {toast.tone === "good" ? (
              <Check size={15} className="text-good" />
            ) : (
              <X size={15} className="text-bad" />
            )}
            {toast.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext) ?? (() => {});
}

/* -------------------------------------------------------------------- format */

export function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export function formatMoney(value: number): string {
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}
